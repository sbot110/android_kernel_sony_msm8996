#!/usr/bin/env python3
"""Run CCF reattachment with the real GCC probe and bounded registration APIs.

MMIO/regulator/CCF registration are explicit model boundaries; the CCF lease
and GCC probe bodies are production code. This does not verify a phone PLL.
"""
import argparse
import os
from pathlib import Path
import re
import resource
import runpy
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--self-test", action="store_true")
args = parser.parse_args()
base = runpy.run_path(str(ROOT / "scripts/dora/check-clk-vdd.py"))
common = base["common"].split("int main(void)", 1)[0]
common = common.replace("#include <assert.h>", "#include <assert.h>\n#include <pthread.h>")
common = common.replace("struct mutex { int unused; };", "struct mutex { pthread_mutex_t value; };")
common = common.replace("static struct mutex prepare_lock,vdd_class_list_lock;",
                        "static struct mutex prepare_lock={PTHREAD_RECURSIVE_MUTEX_INITIALIZER_NP},"
                        "vdd_class_list_lock={PTHREAD_MUTEX_INITIALIZER};")
common = common.replace("#define mutex_lock(x) ((void)(x))", "#define mutex_lock(x) assert(!pthread_mutex_lock(&(x)->value))")
common = common.replace("#define mutex_unlock(x) ((void)(x))", "#define mutex_unlock(x) assert(!pthread_mutex_unlock(&(x)->value))")
common = common.replace("static void clk_prepare_lock(void) {}", "static void clk_prepare_lock(void) {mutex_lock(&prepare_lock);}")
common = common.replace("static void clk_prepare_unlock(void) {}", "static void clk_prepare_unlock(void) {mutex_unlock(&prepare_lock);}")
common = common.replace("memset(&vdd,0,sizeof(vdd));", "memset(&vdd,0,sizeof(vdd));assert(!pthread_mutex_init(&vdd.lock.value,NULL));")
gcc = (ROOT / "drivers/clk/qcom/gcc-msm8996.c").read_text()
start = gcc.index("static int gcc_msm8996_probe(")
end = gcc.index("\nstatic struct platform_driver", start)
probe = gcc[start:end]
contracts = re.search(r"gcc_msm8996_voltage_clocks\[\] = \{(.*?)\n\};", gcc, re.S)[1].count(".hw =")
assert contracts == 56
model = r'''
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define IS_BUILTIN(x) 1
#define dev_err(...) ((void)0)
struct device { unsigned tag; };
struct platform_device { struct device dev; };
struct regmap { unsigned tag; };
struct clk_regmap { struct clk_hw hw;struct regmap *regmap; };
struct qcom_cc_desc { struct clk_regmap **clks;unsigned num_clks; };
struct msm8996_voltage { struct clk_vdd_class *class; };
struct msm8996_voltage_clock { unsigned tag; };
static struct msm8996_voltage gcc_msm8996_digital={&vdd};
static struct msm8996_voltage_clock gcc_msm8996_voltage_clocks[56];
static struct clk_hw *gcc_msm8996_hws[5];
static unsigned gcc_msm8996_internal_plls[]={0,1};
static struct clk_regmap *source_clocks[2];
static struct qcom_cc_desc gcc_msm8996_desc={source_clocks,2};
static struct { struct clk_regmap clkr; } gpll0,gpll0_early;
static struct regmap map;
static unsigned boundary,probe_fault,live,allocations,ownership_checks;
static void *copies[8];
static int probe_errno;
static bool bad_pll;
static struct clk_core registered[56];
static int probe_step(void) { return ++boundary==probe_fault?probe_errno:0; }
static bool msm8996_voltage_required(struct device *dev) { (void)dev;return true; }
static int gcc_msm8996_fixed_root(struct device *dev) { (void)dev;return probe_step(); }
/* Typed supply boundary: real adapter is executed by check-clock-voltage.
   Keep one enable and source HIGH across retries, never a new lease. */
static int msm8996_voltage_init(struct device *dev,struct msm8996_voltage *v,const char *dig,const char *ana)
{ int ret;(void)dig;(void)ana;assert(v->class==&vdd);
  if(vdd.handoff_owner && vdd.handoff_owner!=dev)return -EBUSY;
  vdd.handoff_owner=dev;
  ret=clk_vdd_class_reattach(&vdd,dev);if(ret)return ret;
  ret=probe_step();if(ret)return ret;
  return clk_apply_vdd_level(&vdd,2); }
static int msm8996_voltage_attach(struct device *dev,struct msm8996_voltage_clock *c,unsigned n,
                                  struct msm8996_voltage *dig,struct msm8996_voltage *ana)
{ (void)dev;(void)c;(void)n;(void)dig;(void)ana;return probe_step(); }
static void *devm_kmemdup(struct device *dev,const void *p,size_t size,int flags)
{ void *copy;(void)dev;(void)flags;if(probe_step())return NULL;copy=malloc(size);assert(copy);
  memcpy(copy,p,size);assert(allocations<ARRAY_SIZE(copies));copies[allocations++]=copy;return copy; }
static struct regmap *qcom_cc_map(struct platform_device *p,struct qcom_cc_desc *d)
{ int ret=probe_step();(void)p;(void)d;return ret?ERR_PTR(ret):&map; }
static int regmap_update_bits(struct regmap *m,unsigned offset,unsigned mask,unsigned value)
{ (void)m;(void)offset;(void)mask;(void)value;return probe_step(); }
static int devm_clk_hw_register(struct device *dev,struct clk_hw *hw)
{ (void)dev;(void)hw;return probe_step(); }
static int devm_clk_register_regmap(struct device *dev,struct clk_regmap *clkr)
{ (void)dev;(void)clkr;return probe_step(); }
static unsigned long clk_hw_get_rate(struct clk_hw *hw) { (void)hw;return 600000000; }
static unsigned long source_rate(struct clk_hw *hw,unsigned long rate)
{ (void)hw;return bad_pll?0:rate; }
static const struct { unsigned long (*recalc_rate)(struct clk_hw *,unsigned long); } source_ops={source_rate};
static const struct { const __typeof__(source_ops) *ops; } source_init={&source_ops};
/* gcc's real direct root check uses hw.init, added to the harness clk_hw. */
static int qcom_cc_really_probe(struct platform_device *dev,struct qcom_cc_desc *d,struct regmap *m)
{ unsigned i;int ret;(void)dev;(void)d;(void)m;
  for(i=0;i<56;i++) {
   ret=probe_step();if(ret)return ret;
   ret=clk_vdd_class_init(&vdd);if(ret)return ret;
   memset(&registered[live],0,sizeof(registered[live]));
   registered[live].vdd_class=&vdd;registered[live].vdd_class_registered=true;
   INIT_LIST_HEAD(&registered[live].rate_change_node);live++;
  }
  return probe_step(); /* provider publication can fail after all clocks */ }
static void msm8996_voltage_ownership(struct device *dev,struct msm8996_voltage_clock *c,unsigned n,
                                     struct msm8996_voltage *dig,struct msm8996_voltage *ana)
{ (void)dev;(void)c;(void)n;(void)dig;(void)ana;ownership_checks++; }
'''
# Only the probe's read-only GPLL0 check needs this additional CCF field.
common = common.replace("struct clk_hw { struct clk_core *core;",
                        "struct probe_init;\nstruct clk_hw { const struct probe_init *init;struct clk_core *core;")
model = model.replace("static const struct { unsigned long (*recalc_rate)", "static const struct probe_ops { unsigned long (*recalc_rate)")
model = model.replace("static const struct { const __typeof__(source_ops) *ops; }", "static const struct probe_init { const struct probe_ops *ops; }")
tests = r'''
static void unwind(void)
{ while(live)clk_detach_vdd_core(&registered[--live]);while(allocations)free(copies[--allocations]); }
static void setup(void)
{ reset();vdd.skip_handoff=false;vdd.require_handoff=true;boundary=probe_fault=live=0;
  ownership_checks=0;probe_errno=-EPROBE_DEFER;bad_pll=false;gpll0.clkr.hw.init=&source_init; }
static void *retry_thread(void *owner)
{ unsigned i;for(i=0;i<1000;i++)assert(!clk_vdd_class_reattach(&vdd,owner));return NULL; }
static void *consumer_thread(void *unused)
{ unsigned i;(void)unused;for(i=0;i<1000;i++){
  assert(!clk_vote_vdd_level(&vdd,1));assert(!clk_unvote_vdd_level(&vdd,1));}return NULL; }
int main(void)
{
 struct platform_device p={0},other={0};unsigned total,i,n;pthread_t threads[8];int ret;
 (void)combined_ops;
 setup();assert(!gcc_msm8996_probe(&p));total=boundary;assert(live==56 && ownership_checks==1);
 unwind();assert(vdd.orphaned && vdd.orphaned_handoff && votes[2]==1);
 assert(gcc_msm8996_probe(&other)==-EBUSY && votes[2]==1);
 assert(clk_vdd_class_reattach(&vdd,&other.dev)==-EPERM && vdd.orphaned);
 boundary=0;assert(!gcc_msm8996_probe(&p) && votes[2]==1 && rail.enables==1);unwind();
 /* Every GCC probe boundary, partial publication, deferred and hard errors. */
 for(n=0;n<3;n++)for(i=1;i<=total;i++) {
  setup();probe_fault=i;probe_errno=n==0?-EPROBE_DEFER:n==1?-EIO:-ETIMEDOUT;
  ret=gcc_msm8996_probe(&p);assert(ret<0);unwind();assert(!vdd.num_clocks && votes[2]<=1);
  probe_fault=boundary=0;assert(!gcc_msm8996_probe(&p));
  assert(votes[2]==1 && rail.enables==1 && live==56);unwind();
 }
 setup();bad_pll=true;assert(gcc_msm8996_probe(&p)==-EINVAL);unwind();
 bad_pll=false;assert(!gcc_msm8996_probe(&p));unwind();
 /* Reattach itself never releases HIGH, including a provider after late init. */
 vdd_class_handoff_completed=true;
 for(i=0;i<8;i++)assert(!pthread_create(&threads[i],NULL,retry_thread,&p.dev));
 for(i=0;i<8;i++)assert(!pthread_join(threads[i],NULL));
 assert(!vdd.orphaned && votes[2]==1 && rail.enables==1 && !vdd.num_clocks);
 assert(!clk_vdd_class_init(&vdd));
 for(i=0;i<8;i++)assert(!pthread_create(&threads[i],NULL,consumer_thread,NULL));
 for(i=0;i<8;i++)assert(!pthread_join(threads[i],NULL));
 assert(votes[2]==1 && !votes[1] && rail.enables==1);clk_vdd_class_deinit(&vdd);
 /* Lost ACK is sticky: later good transport cannot clear the quarantine. */
 ack_error=-ETIMEDOUT;assert(clk_vdd_class_reattach(&vdd,&p.dev)==-ETIMEDOUT);
 ack_error=0;assert(clk_vdd_class_reattach(&vdd,&p.dev)==-ESHUTDOWN && votes[2]==1);
 setup();assert(!gcc_msm8996_probe(&p));assert(!clk_vote_vdd_level(&vdd,1));
 registered[live-1].vdd_class_vote_held=true;registered[live-1].vdd_class_vote=1;
 unwind();assert(!vdd.orphaned_handoff && clk_vdd_class_reattach(&vdd,&p.dev)==-ESHUTDOWN);
 puts("PASS: real GCC probe control flow at every boundary + CCF partial unwind/reattach, wrong owner, bad PLL, lost ACK, late probe and 8-thread consumers");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="dora-clock-reattach-") as tmp:
    c, binary = Path(tmp) / "reattach.c", Path(tmp) / "reattach"
    flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.environ.get("CCF_TEST_SANITIZE") else []
    command = shlex.split(os.environ.get("HOSTCC", "cc")) + [
                    "-std=gnu11", "-D_GNU_SOURCE", "-O1", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-unused-value",
                    "-Wno-sign-compare", "-pthread", *flags, str(c), "-o", str(binary)]
    c.write_text(common + model + probe + tests)
    subprocess.run(command, check=True)
    subprocess.run([str(binary)], check=True)
    if args.self_test:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        mutations = {
            "foreign owner": ("vdd->handoff_owner != owner", "false"),
            "fresh ACK": ("ret = vdd->sync_vdd(vdd, vdd->num_levels - 1);", "ret = 0;"),
            "sticky quarantine": ("!vdd->orphaned_handoff || vdd->num_clocks", "vdd->num_clocks"),
            "duplicate HIGH": ("vdd->orphaned_handoff = false;\n\tvdd->orphaned = false;",
                               "vdd->level_votes[vdd->num_levels - 1]++;\n\tvdd->orphaned = false;"),
        }
        for name, (old, new) in mutations.items():
            assert old in common, name
            c.write_text(common.replace(old, new, 1) + model + probe + tests)
            subprocess.run(command, check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True)
            if result.returncode == 0:
                raise AssertionError("unsafe mutation escaped: " + name)
            print("PASS: rejected " + name + " mutation")
