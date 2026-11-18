#!/usr/bin/env python3
"""Execute QFPROM/NVMEM selection and actual CPU plan code, without rail writes.

This is a software boundary test, not hardware qualification of CPR/SPM or DVFS.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
QCOM = ROOT / "drivers/clk/qcom"


def body(source, name):
    match = re.search(r"(?:static )?(?:int|void) " + name + r"\([^;]*?\)\s*\{", source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


plans = json.loads((ROOT / "Documentation/sony/dora-cpu-dt.json").read_text())["frequency_plans"]
arrays = ""
entries = []
for index, (name, values) in enumerate(plans.items()):
    assert len(values) % 2 == 0
    arrays += f"static u32 table{index}[] = {{" + ",".join(map(str, values)) + "};\n"
    entries.append(f'{{"{name}",table{index},ARRAY_SIZE(table{index})}}')

preamble = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define EPROBE_DEFER 517
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define BITS_PER_BYTE 8
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define GENMASK(h,l) ((~0U<<(l)) & (~0U>>(31-(h))))
#define GFP_KERNEL 0
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
struct device { void *of_node; };
struct nvmem_cell { int bytes,bit_offset,nbits; };
struct msm8996_cpu_plan { unsigned long rate;unsigned int level; };
struct msm8996_cpu_hw_plans { unsigned speed_bin;struct msm8996_cpu_plan *domain[3];unsigned count[3]; };
static struct nvmem_cell cell={.bytes=1,.bit_offset=5,.nbits=3};
static unsigned gets,cell_puts,allocs,buffers,steps,fail_at,alloc_step,alloc_fail;
static unsigned raw_byte;
static int get_error,read_error,bad_cells,length=1;
static const char *bad_name;
static int step(void) { return ++steps==fail_at?-EIO:0; }
static void nvmem_shift_read_buffer_in_place(struct nvmem_cell *,void *);
static struct nvmem_cell *nvmem_cell_get(struct device *dev,const char *name)
{ assert(dev && !strcmp(name,"speedbin"));gets++;return get_error?ERR_PTR(get_error):&cell; }
static void nvmem_cell_put(struct nvmem_cell *c) { assert(c==&cell);cell_puts++; }
static void *nvmem_cell_read(struct nvmem_cell *c,size_t *len)
{ u8 *value;if(read_error)return ERR_PTR(read_error);value=calloc(1,8);assert(value);
  value[0]=raw_byte;nvmem_shift_read_buffer_in_place(c,value);*len=length;buffers++;return value; }
static void kfree(void *p) { assert(buffers);buffers--;free(p); }
static void *devm_kcalloc(struct device *dev,size_t count,size_t size,int flags)
{ void *p;(void)dev;(void)flags;if(++alloc_step==alloc_fail)return NULL;
  p=calloc(count,size);assert(p);allocs++;return p; }
static void devm_kfree(struct device *dev,void *p)
{ (void)dev;assert(allocs);allocs--;free(p); }
struct entry { const char *name;u32 *values;unsigned count; };
'''
preamble += arrays + "static struct entry entries[]={" + ",".join(entries) + "};\n"
preamble += r'''
static struct entry *find(const char *name)
{ unsigned i;for(i=0;i<ARRAY_SIZE(entries);i++)if(!strcmp(entries[i].name,name))return &entries[i];
  assert(!"unknown bin/domain or fallback");return NULL; }
static int of_property_count_u32_elems(void *node,const char *name)
{ (void)node;if(bad_name && !strcmp(name,bad_name))return bad_cells;return find(name)->count; }
static int of_property_read_u32_index(void *node,const char *name,unsigned index,u32 *value)
{ struct entry *e=find(name);int ret=step();(void)node;assert(index<e->count);if(!ret)*value=e->values[index];return ret; }
'''
production = re.sub(r"^#include[^\n]*\n", "", (QCOM / "clk-msm8996-cpu-hw.c").read_text(), flags=re.M)
production = (body((ROOT / "drivers/nvmem/core.c").read_text(), "nvmem_shift_read_buffer_in_place") +
              body((QCOM / "clk-msm8996-cpu-transaction.c").read_text(), "msm8996_cpu_plan_lookup") + production)
tests = r'''
static struct device device;
static void release(struct msm8996_cpu_hw_plans *p)
{ unsigned i;for(i=0;i<3;i++)devm_kfree(&device,p->domain[i]); }
static void reset(void)
{ assert(!allocs && !buffers);gets=cell_puts=steps=fail_at=alloc_step=alloc_fail=0;
  get_error=read_error=bad_cells=0;length=1;raw_byte=0;bad_name=NULL; }
static int run(struct msm8996_cpu_hw_plans *p)
{ int ret=msm8996_cpu_hw_read_plans(&device,p);assert(!buffers);
  assert(cell_puts==gets-(get_error!=0));if(ret)assert(!allocs);return ret; }
int main(void)
{
 struct msm8996_cpu_hw_plans p;unsigned byte,i,n,field;int ret;
 /* Execute Linux4.19's byte-relative NVMEM shifter for every fuse byte,
    with all unrelated lower bits and all eight 3-bit bins. */
 for(byte=0;byte<256;byte++) {
  reset();raw_byte=byte;memset(&p,0xa5,sizeof(p));ret=run(&p);
  if((byte>>5)>3){assert(ret==-EINVAL && p.speed_bin==0xa5a5a5a5U);continue;}
  assert(!ret && p.speed_bin==(byte>>5));
  for(i=0;i<3;i++)assert(p.count[i]>1 && p.domain[i][1].level==1);
  release(&p);
 }
 reset();get_error=-EPROBE_DEFER;assert(run(&p)==-EPROBE_DEFER);
 reset();get_error=-EIO;assert(run(&p)==-EIO);
 reset();read_error=-ETIMEDOUT;assert(run(&p)==-ETIMEDOUT);
 for(i=0;i<5;i++)if(i!=1){reset();length=i;assert(run(&p)==-EINVAL);}
 for(i=1;i<=3;i++){reset();alloc_fail=i;assert(run(&p)==-ENOMEM);}
 reset();assert(!run(&p));n=steps;release(&p);
 for(i=1;i<=n;i++){reset();fail_at=i;assert(run(&p)==-EIO);}
 for(i=0;i<ARRAY_SIZE(entries);i++) {
  const int invalid[]={-ENOENT,0,1,2,3,5,65,128};
  for(n=0;n<ARRAY_SIZE(invalid);n++) {
   reset();
   raw_byte=(unsigned)(strstr(entries[i].name,"speedbin")[8]-'0')<<5;
   bad_name=entries[i].name;bad_cells=invalid[n];
   assert(run(&p)==(invalid[n]<0?invalid[n]:-EINVAL));
  }
  for(field=0;field<2;field++) {
   u32 saved=entries[i].values[entries[i].count-2+field];
   reset();raw_byte=(unsigned)(strstr(entries[i].name,"speedbin")[8]-'0')<<5;
   entries[i].values[entries[i].count-2+field]=0;
   assert(run(&p)==-EINVAL);
   entries[i].values[entries[i].count-2+field]=saved;
  }
 }
 reset();assert(msm8996_cpu_hw_read_plans(NULL,&p)==-EINVAL);
 assert(msm8996_cpu_hw_read_plans(&device,NULL)==-EINVAL);
 puts("PASS: actual NVMEM shift + CPU fuse/12-plan adapter; bins4..7, all read/allocation faults, malformed plans, cleanup and no fallback");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="dora-cpu-hardware-") as tmp:
    c, binary = Path(tmp) / "hw.c", Path(tmp) / "hw"
    c.write_text(preamble + production + tests)
    flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.environ.get("CPU_HW_SANITIZE") else []
    subprocess.run(shlex.split(os.environ.get("HOSTCC", "cc")) +
                   ["-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror", *flags, str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
