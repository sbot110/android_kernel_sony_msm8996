#!/usr/bin/env python3
"""Execute real APM ownership/readback/retirement and CPR crossover/PM code.

Only kernel API, MMIO/SMC and regulator boundaries are modeled. No firmware
handoff, gang exclusion, measured voltage or live CPU power backend is proven.
"""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(text, name):
    match = re.search(r'^(?:static )?(?:inline )?(?:int|void|struct msm_(?:apm_ctrl_dev|spm_device) \*)\s*' +
                      re.escape(name) + r'\([^;]*?\)\s*\{', text, re.M | re.S)
    assert match, name
    pos, depth = match.end(), 1
    while depth:
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    return text[match.start():pos] + '\n'


PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
#define __iomem
#define EPROBE_DEFER 517
#define GFP_KERNEL 0
#define THIS_MODULE NULL
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define IS_ERR_OR_NULL(p) (!(p)||(uintptr_t)(p)>=(uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define pr_err(...) ((void)0)
#define cpr3_err(...) ((void)0)
#define cpr3_debug(...) ((void)0)
#define mb() ((void)0)
#define rmb() ((void)0)
static void udelay(unsigned x) { (void)x; }
static void ndelay(unsigned x) { (void)x; }
struct mutex { pthread_mutex_t p; };
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->p,NULL)); }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->p)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->p)); }
typedef struct mutex spinlock_t;
#define spin_lock_init(m) mutex_init(m)
#define spin_lock_irqsave(m,f) do { (f)=0;mutex_lock(m); } while(0)
#define spin_unlock_irqrestore(m,f) do { (void)(f);mutex_unlock(m); } while(0)
struct list_head { struct list_head *next,*prev; };
#define LIST_HEAD(n) struct list_head n={&n,&n}
#define INIT_LIST_HEAD(h) ((h)->next=(h)->prev=(h))
static void list_add_tail(struct list_head *n,struct list_head *h)
{ n->next=h;n->prev=h->prev;h->prev->next=n;h->prev=n; }
#define entry(n,t,m) ((t *)((char *)(n)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(p=entry((h)->next,__typeof__(*p),m); &p->m!=(h);p=entry(p->m.next,__typeof__(*p),m))
struct resource { unsigned long start,end; };
#define IORESOURCE_MEM 1
#define resource_size(r) ((r)->end-(r)->start+1)
struct device_node { int id; };
struct device { struct device_node *of_node; struct resource res; void *data; unsigned refs; };
struct platform_device { struct device dev; };
struct of_device_id { const void *data; };
struct dentry { int dummy; };
enum msm_apm_supply { MSM_APM_SUPPLY_APCC,MSM_APM_SUPPLY_MX };
static struct of_device_id msm_apm_match_table[1];
static unsigned allocations,module_refs,writes,reads,smcs,ldo_calls,voltage_calls,loop_writes;
static bool allocation_fail,missing_resource,missing_match,unmapped,drop_write,unstable;
static int map_error,smc_error,voltage_error,ldo_error,disable_error,update_error,clear_error;
static u32 registers[0x1000/4],csr[0x1000/4],spm_events[6],pll_ctl[2];
static struct device_node *requested_node;
static unsigned status_reads;
static void *kzalloc(size_t n,int flag) { (void)flag;if(allocation_fail)return NULL;allocations++;return calloc(1,n); }
static void kfree(void *p) { assert(p && allocations);allocations--;free(p); }
static void get_device(struct device *d) { d->refs++; }
static void __module_get(void *m) { (void)m;module_refs++; }
static void platform_set_drvdata(struct platform_device *p,void *d) { p->dev.data=d; }
static void *platform_get_drvdata(struct platform_device *p) { return p->dev.data; }
static const struct of_device_id *of_match_device(const struct of_device_id *m,struct device *d)
{ (void)d;return missing_match?NULL:m; }
static struct resource *platform_get_resource_byname(struct platform_device *p,int type,const char *name)
{ assert(type==IORESOURCE_MEM && !strcmp(name,"pm-apcc-glb"));return missing_resource?NULL:&p->dev.res; }
static struct device_node *of_parse_phandle(struct device_node *n,const char *key,unsigned index)
{ assert(n && !strcmp(key,"qcom,apm-ctrl") && !index);return requested_node; }
static void of_node_put(struct device_node *n) { (void)n; }
static u32 readl_relaxed(const void *p)
{ assert(!unmapped);reads++;if(p==&registers[0xa8/4] && unstable)return (++status_reads)&1;return *(const u32 *)p; }
static void writel_relaxed(u32 v,void *p)
{ assert(!unmapped);writes++;*(u32 *)p=v;
  if(p==&registers[0x98/4] && !drop_write)registers[0xa8/4]=v==0x10?3:0; }
struct arm_smccc_res { long a0; };
static void arm_smccc_smc(unsigned fid,unsigned mask,unsigned command,unsigned a,unsigned b,unsigned c,unsigned d,unsigned e,struct arm_smccc_res *r)
{ assert(fid==0xC4000020 && mask==3 && command<=1 && !(a|b|c|d|e));smcs++;r->a0=smc_error; }
static DEFINE_MUTEX(scm_lmh_lock);
'''

BOUNDARIES = r'''
static DEFINE_MUTEX(apm_ctrl_list_mutex);
static LIST_HEAD(apm_ctrl_list);
static void *event_addresses[6];
static int msm_apm_ctrl_devm_ioremap(struct platform_device *p,struct msm_apm_ctrl_dev *c)
{ unsigned i;(void)p;if(map_error)return map_error;c->reg_base=registers;c->apcs_csr_base=csr;
  for(i=0;i<6;i++) { event_addresses[i]=&spm_events[i]; }
  c->apcs_spm_events_addr=event_addresses;
  c->apc0_pll_ctl_addr=&pll_ctl[0];c->apc1_pll_ctl_addr=&pll_ctl[1];
  c->version=HMSS_VERSION_1P2;c->clk_src_override=true;return 0; }
static int msm8953_apm_ctrl_init(struct platform_device *p,struct msm_apm_ctrl_dev *c)
{ (void)p;(void)c;return -EOPNOTSUPP; }
static int msm8953_apm_switch_to_mx(struct msm_apm_ctrl_dev *c) { (void)c;return -EOPNOTSUPP; }
static int msm8953_apm_switch_to_apcc(struct msm_apm_ctrl_dev *c) { (void)c;return -EOPNOTSUPP; }
static void apm_debugfs_init(struct msm_apm_ctrl_dev *c) { (void)c; }
static void apm_debugfs_deinit(struct msm_apm_ctrl_dev *c) { (void)c; }
'''

CPR_BOUNDARIES = r'''
struct regulator { int voltage; };
struct cpr4_sdelta { bool allow_boost; };
struct cpr3_corner { struct cpr4_sdelta *sdelta; };
#define CPR_CTRL_TYPE_CPR3 0
#define CPR_CTRL_TYPE_CPR4 1
#define CPR_CTRL_TYPE_CPRH 2
#define CPR3_REG_CPR_CTL 4
#define CPR3_CPR_CTL_LOOP_EN_MASK 1
#define CPR3_CPR_CTL_LOOP_ENABLE 1
#define CPR3_CPR_CTL_LOOP_DISABLE 0
struct cpr3_controller { struct mutex lock;struct regulator *vdd_regulator;
 struct msm_apm_ctrl_dev *apm;int apm_threshold_volt,power_error,ctrl_type;
 enum msm_apm_supply apm_high_supply,apm_low_supply;
 bool cpr_enabled,cpr_suspended;struct cpr3_corner aggr_corner; };
static void cpr3_masked_write(struct cpr3_controller *c,unsigned off,unsigned mask,unsigned value)
{ (void)c;assert(off==4 && mask==1 && value<=1);loop_writes++; }
static int regulator_set_voltage(struct regulator *r,int lo,int hi)
{ assert(lo<=hi);voltage_calls++;r->voltage=lo;return voltage_error; }
static int cpr3_regulator_ldo_apm_prepare(struct cpr3_controller *c,int n,int l,struct cpr3_corner *a)
{ (void)a;assert(n>0 && l==c->apm_threshold_volt);ldo_calls++;return ldo_error; }
static int cpr3_ctrl_clear_cpr4_config(struct cpr3_controller *c) { (void)c;return clear_error; }
static int cpr3_closed_loop_disable(struct cpr3_controller *c) { if(!disable_error)c->cpr_enabled=false;return disable_error; }
static int cpr3_regulator_update_ctrl_state(struct cpr3_controller *c) { (void)c;return update_error; }
'''

TESTS = r'''
static struct platform_device platform;
static struct device_node node={1},alias={2};
static struct msm_apm_ctrl_dev *handle;
static struct cpr3_controller cpr;
static struct regulator vdd;
static void cleanup(void)
{
 if(handle) { assert(allocations==1 && module_refs==1 && platform.dev.refs==1);
  pthread_mutex_destroy(&handle->lock.p);pthread_mutex_destroy(&handle->transaction_lock.p);
  kfree(handle);handle=NULL;module_refs=0;platform.dev.refs=0; }
 assert(!allocations);INIT_LIST_HEAD(&apm_ctrl_list);
}
static void setup(void)
{
 cleanup();memset(&platform,0,sizeof(platform));platform.dev.of_node=&node;
 platform.dev.res=(struct resource){0x9820000,0x9820fff};
 requested_node=&node;allocation_fail=missing_resource=missing_match=unmapped=drop_write=unstable=false;
 map_error=smc_error=voltage_error=ldo_error=disable_error=update_error=clear_error=0;
 memset(registers,0,sizeof(registers));registers[0x98/4]=0x10;registers[0xa8/4]=3;
 msm_apm_match_table[0].data=(void *)(uintptr_t)MSM8996_ID;
 writes=reads=smcs=ldo_calls=voltage_calls=loop_writes=status_reads=0;
}
static void probe(void)
{ assert(!msm_apm_probe(&platform));handle=platform.dev.data;assert(handle && !writes && !smcs); }
static void cpr_init(void)
{
 memset(&cpr,0,sizeof(cpr));mutex_init(&cpr.lock);cpr.apm=handle;cpr.vdd_regulator=&vdd;
 cpr.apm_threshold_volt=850000;cpr.apm_high_supply=MSM_APM_SUPPLY_APCC;cpr.apm_low_supply=MSM_APM_SUPPLY_MX;
 cpr.cpr_enabled=true;vdd.voltage=950000;
}
static void cpr_end(void) { assert(!pthread_mutex_destroy(&cpr.lock.p)); }
static void *consumer(void *data)
{ unsigned i;(void)data;for(i=0;i<500;i++){
 assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_MX));
 assert(msm_apm_get_supply(handle)>=0);assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_APCC));}return NULL; }
static void *read_until_retired(void *data)
{ unsigned i;int ret;(void)data;for(i=0;i<4000;i++) {ret=msm_apm_get_supply(handle);assert(ret>=0 || ret==-ESHUTDOWN);}return NULL; }
int main(void)
{
 unsigned before,observed;int last;pthread_t threads[4];struct cpr3_corner corner={0};
 setup();allocation_fail=true;assert(msm_apm_probe(&platform)==-ENOMEM && !allocations && !writes);
 setup();missing_resource=true;assert(msm_apm_probe(&platform)==-EINVAL && !allocations);
 setup();platform.dev.res.end=platform.dev.res.start+0xa7;assert(msm_apm_probe(&platform)==-EINVAL && !allocations);
 setup();map_error=-EPROBE_DEFER;assert(msm_apm_probe(&platform)==-EPROBE_DEFER && !allocations && !platform.dev.data);
 setup();registers[0xa8/4]=1;assert(msm_apm_probe(&platform)==-EIO && !allocations && !writes);
 setup();probe();assert(msm_apm_get_supply(handle)==MSM_APM_SUPPLY_APCC);
 assert(msm_apm_probe(&platform)==-EBUSY && allocations==1);
 platform.dev.of_node=&alias;assert(msm_apm_probe(&platform)==-EBUSY);platform.dev.of_node=&node;
 assert(msm_apm_ctrl_dev_get(&platform.dev)==handle);
 assert(msm_apm_get_supply(NULL)==-EINVAL && msm_apm_set_supply(NULL,0)==-EINVAL);
 assert(msm_apm_set_supply(handle,42)==-EPERM && !writes);
 assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_APCC) && !writes && !smcs);
 assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_MX) && writes==1 && smcs==2);
 assert(msm_apm_get_supply(handle)==MSM_APM_SUPPLY_MX);
 assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_APCC));
 handle->version=0;assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_MX));
 assert(pll_ctl[0]==0x30 && pll_ctl[1]==0x30 && spm_events[0]==1);
 assert(!msm_apm_set_supply(handle,MSM_APM_SUPPLY_APCC));
 /* Never trust a cached APCC when physical register samples say MX. */
 registers[0x98/4]=0;registers[0xa8/4]=0;assert(msm_apm_get_supply(handle)==MSM_APM_SUPPLY_MX);
 setup();probe();unstable=true;assert(msm_apm_get_supply(handle)==-EAGAIN);unstable=false;
 assert(msm_apm_get_supply(handle)==-EAGAIN);before=writes;assert(msm_apm_set_supply(handle,1)==-EAGAIN && writes==before);
 setup();probe();drop_write=true;assert(msm_apm_set_supply(handle,MSM_APM_SUPPLY_MX)==-ETIMEDOUT);
 before=writes;drop_write=false;registers[0x98/4]=0;registers[0xa8/4]=0;
 assert(msm_apm_get_supply(handle)==-ETIMEDOUT && msm_apm_set_supply(handle,0)==-ETIMEDOUT && writes==before);
 setup();probe();smc_error=-EACCES;assert(msm_apm_set_supply(handle,1)==-EACCES && !writes);
 smc_error=0;assert(msm_apm_set_supply(handle,1)==-EACCES && !writes);
 setup();probe();for(before=0;before<4;before++)assert(!pthread_create(&threads[before],NULL,consumer,NULL));
 for(before=0;before<4;before++)assert(!pthread_join(threads[before],NULL));
 /* Remove serializes with active operations; raw handles survive as tombstones. */
 for(before=0;before<4;before++)assert(!pthread_create(&threads[before],NULL,read_until_retired,NULL));
 assert(!msm_apm_remove(&platform));
 for(before=0;before<4;before++)assert(!pthread_join(threads[before],NULL));
 unmapped=true;observed=reads;before=writes;
 assert(msm_apm_get_supply(handle)==-ESHUTDOWN && msm_apm_set_supply(handle,1)==-ESHUTDOWN);
 assert(reads==observed && writes==before && !platform.dev.data);
 assert(PTR_ERR(msm_apm_ctrl_dev_get(&platform.dev))==-ESHUTDOWN);
 assert(msm_apm_probe(&platform)==-ESHUTDOWN && allocations==1);
 setup();probe();cpr_init();last=950000;
 assert(!cpr3_regulator_switch_apm_mode(&cpr,800000,&last,&corner));
 assert(last==850000 && vdd.voltage==850000 && voltage_calls==1 && ldo_calls==1);
 assert(msm_apm_get_supply(handle)==MSM_APM_SUPPLY_MX);cpr_end();
 setup();probe();cpr_init();last=950000;drop_write=true;
 assert(cpr3_regulator_switch_apm_mode(&cpr,800000,&last,&corner)==-ETIMEDOUT);
 assert(last==850000 && vdd.voltage==850000 && voltage_calls==1 && cpr.power_error==-ETIMEDOUT);
 before=writes;drop_write=false;
 assert(cpr3_regulator_switch_apm_mode(&cpr,800000,&last,&corner)==-ETIMEDOUT && voltage_calls==1 && writes==before);
 before=loop_writes;cpr3_ctrl_loop_enable(&cpr);assert(loop_writes==before);
 assert(cpr3_regulator_resume(&cpr)==-ETIMEDOUT && cpr3_regulator_suspend(&cpr)==-ETIMEDOUT);cpr_end();
 setup();probe();cpr_init();last=950000;ldo_error=-EIO;
 assert(cpr3_regulator_switch_apm_mode(&cpr,800000,&last,&corner)==-EIO && !writes && last==850000);cpr_end();
 setup();probe();cpr_init();last=950000;voltage_error=-EIO;
 assert(cpr3_regulator_switch_apm_mode(&cpr,800000,&last,&corner)==-EIO && !writes && !ldo_calls && cpr.power_error);
 voltage_error=0;assert(cpr3_regulator_switch_apm_mode(&cpr,800000,&last,&corner)==-EIO && voltage_calls==1);cpr_end();
 setup();probe();cpr_init();disable_error=-EIO;
 assert(cpr3_regulator_suspend(&cpr)==-EIO && cpr.cpr_suspended);cpr_end();
 setup();probe();cpr_init();assert(!cpr3_regulator_suspend(&cpr) && cpr.cpr_suspended);
 update_error=-EPROBE_DEFER;assert(cpr3_regulator_resume(&cpr)==-EPROBE_DEFER && cpr.cpr_suspended && cpr.power_error);
 update_error=0;assert(cpr3_regulator_resume(&cpr)==-EPROBE_DEFER);cpr_end();
 setup();probe();cpr_init();cpr.ctrl_type=CPR_CTRL_TYPE_CPR4;clear_error=-EIO;
 assert(cpr3_regulator_suspend(&cpr)==-EIO);cpr_end();
 setup();probe();cpr_init();assert(!cpr3_regulator_suspend(&cpr) && !cpr3_regulator_resume(&cpr) && !cpr.cpr_suspended);cpr_end();
 cleanup();puts("PASS: production APM readback/SMC/timeout/quarantine/owner/retirement and CPR crossover/PM; concurrent consumers");
 puts("No physical CPU shared-gang guard or firmware handoff is qualified by these boundary models.");return 0;
}
'''

SPM_PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define MSM_SPM_REG_SAW_SEQ_ENTRY 0
#define mb() ((void)0)
static unsigned warnings,writes;
#define __WARN() (warnings++)
static void __raw_writel(uint32_t value,void *address) { *(uint32_t *)address=value;writes++; }
struct msm_spm_driver_data { uint32_t *reg_seq_entry_shadow;uint32_t seq_entries;
 void *reg_base_addr;uint32_t *reg_offsets; };
'''
SPM_TESTS = r'''
int main(void)
{
 uint32_t shadow[2]={0xaaaaaaaa,0xbbbbbbbb},hardware[2]={0},offsets[1]={0};
 struct msm_spm_driver_data dev={shadow,2,hardware,offsets};
 uint8_t good[]={0x11,0x22,0x0f},bad[]={0x11,0x22};uint32_t offset=0;
 assert(msm_spm_drv_write_seq_data(NULL,good,3,&offset)==-EINVAL);
 assert(msm_spm_drv_write_seq_data(&dev,NULL,3,&offset)==-EINVAL);
 assert(msm_spm_drv_write_seq_data(&dev,good,3,NULL)==-EINVAL);
 assert(msm_spm_drv_write_seq_data(&dev,good,0,&offset)==-EINVAL);
 assert(msm_spm_drv_write_seq_data(&dev,bad,2,&offset)==-EINVAL && offset==0 && shadow[0]==0xaaaaaaaa);
 offset=7;assert(msm_spm_drv_write_seq_data(&dev,good,3,&offset)==-E2BIG && offset==7 && shadow[1]==0xbbbbbbbb);
 offset=UINT32_MAX;assert(msm_spm_drv_write_seq_data(&dev,good,3,&offset)==-E2BIG && offset==UINT32_MAX);
 offset=3;assert(!msm_spm_drv_write_seq_data(&dev,good,3,&offset) && offset==6);
 assert(shadow[0]==0x11aaaaaa && shadow[1]==0xbbbb0f22 && !writes);
 msm_spm_drv_flush_seq_entry(NULL);assert(warnings==1 && !writes);
 msm_spm_drv_flush_seq_entry(&dev);assert(writes==2 && !memcmp(hardware,shadow,sizeof(shadow)));
 offset=8;assert(msm_spm_drv_write_seq_data(&dev,good,3,&offset)==-E2BIG && offset==8);
 puts("PASS: production SAW bounded sequence builder/flush: bad length, terminator, capacity, offsets and byte replacement");
 return 0;
}
'''


def harness(apm, cpr):
    constants = apm[apm.index('#define APCS_GFMUXA_SEL_VAL'):apm.index('#if defined(CONFIG_DEBUG_FS)')]
    code = PREAMBLE + constants + '\n#define MSM8953_APCC_APM_CTL_STS 0x2b0\n' + BOUNDARIES
    for name in ['msm_apm_secure_clock_source_override', 'msm8996_apm_wait_for_switch',
                 'msm8996_apm_switch_to_mx', 'msm8996_apm_switch_to_apcc',
                 'msm8996pro_apm_switch_to_mx', 'msm8996pro_apm_switch_to_apcc',
                 'msm_apm_switch_to_mx', 'msm_apm_switch_to_apcc',
                 'msm_apm_read_supply', 'msm_apm_get_supply', 'msm_apm_set_supply',
                 'msm_apm_ctrl_dev_get', 'msm_apm_probe', 'msm_apm_remove']:
        code += function(apm, name)
    code += CPR_BOUNDARIES
    for name in ['cpr3_ctrl_loop_enable', 'cpr3_ctrl_loop_disable', 'cpr3_power_fault',
                 'cpr3_apm_ready', 'cpr3_regulator_switch_apm_mode',
                 'cpr3_regulator_suspend', 'cpr3_regulator_resume']:
        code += function(cpr, name)
    return code + TESTS


def run(code, path, name, expected=True):
    c, binary = path / (name + '.c'), path / name
    c.write_text(code)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('SHARED_POWER_SANITIZE') else []
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
                    *flags, str(c), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert (result.returncode == 0) == expected, (name, result.stdout, result.stderr)
    if expected:
        print(result.stdout, end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    apm = (ROOT / 'drivers/soc/qcom/msm-apm.c').read_text()
    cpr = (ROOT / 'drivers/regulator/cpr3-regulator.c').read_text()
    spm = (ROOT / 'drivers/soc/qcom/msm-spm.c').read_text()
    devices = (ROOT / 'drivers/soc/qcom/spm_devices.c').read_text()
    probe = function(devices, 'msm_spm_dev_probe')
    assert probe.index('goto fail;', probe.index('ret = msm_spm_dev_init(')) < probe.index('list_add(')
    assert 'list_add(' not in function(devices, 'msm_spm_get_device')
    for name in ['_cpr3_regulator_update_ctrl_state', 'cpr3_regulator_scale_vdd_voltage']:
        body = function(cpr, name)
        assert body.index('cpr3_apm_ready(ctrl)') < body.index('if (')
    mutations = [
        ('cached-supply', 'apm', 'ret = msm_apm_read_supply(ctrl_dev);', 'ret = ctrl_dev->supply;'),
        ('forget-quarantine', 'apm', 'ret = ctrl_dev->error;', 'ret = 0;'),
        ('fake-lock', 'apm', 'return -ETIMEDOUT;', 'return 0;'),
        ('drop-retirement', 'apm', 'ctrl_dev->error = -ESHUTDOWN;', 'ctrl_dev->error = 0;'),
        ('erase-tombstone', 'apm', 'old->retired ? -ESHUTDOWN : -EBUSY', '-EBUSY'),
        ('swallow-smc', 'apm', 'ret = (int)res.a0;', 'ret = 0;'),
        ('unsafe-rollback', 'cpr', 'return cpr3_power_fault(ctrl, rc);\n\t}\n\treturn 0;\n}',
         'regulator_set_voltage(vdd, 950000, 950000);\n\t\treturn cpr3_power_fault(ctrl, rc);\n\t}\n\treturn 0;\n}'),
        ('clear-power-fault', 'cpr', 'if (ctrl->power_error)\n\t\treturn ctrl->power_error;', 'ctrl->power_error = 0;'),
        ('restart-loop-after-fault', 'cpr', '!ctrl->power_error && ctrl->cpr_enabled', 'ctrl->cpr_enabled'),
        ('swallow-pm', 'cpr', 'return rc;\n}\n', 'return 0;\n}\n'),
    ]
    with tempfile.TemporaryDirectory(prefix='dora-shared-power-') as tmp:
        path = Path(tmp)
        run(harness(apm, cpr), path, 'production')
        spm_code = SPM_PREAMBLE + function(spm, 'msm_spm_drv_write_seq_data') + function(spm, 'msm_spm_drv_flush_seq_entry') + SPM_TESTS
        run(spm_code, path, 'spm-sequences')
        if args.self_test:
            for name, target, before, after in mutations:
                source = apm if target == 'apm' else cpr
                assert before in source, name
                changed = source.replace(before, after)
                run(harness(changed if target == 'apm' else apm,
                            changed if target == 'cpr' else cpr), path, name, False)
            print('PASS: %d unsafe shared-power mutations rejected' % len(mutations))
    if args.report:
        args.report.write_text(json.dumps(dict(production_functions=23,
            unsafe_mutations_rejected=len(mutations) if args.self_test else 0,
            hardware_verified=False, live_cpu_backend=False), indent=2) + '\n')


if __name__ == '__main__':
    main()
