#!/usr/bin/env python3
"""Fault-test actual QUSB2 init/exit/runtime-PM code, not physical USB safety.

Only kernel API boundaries and MMIO storage are modeled. Expected MSM8996
interrupt bits/UTMI offset come from Sony4.4 0c295b15b6d0 phy-msm-qusb.c.
The public Sony init is tested as blocked with no hardware side effects. The
prepared Sony table/error paths are tested by calling the actual private init
helper directly: that fixture does not remove the production ownership gate.
No DPDM provider, cable/charger measurement, or bootloader handoff is simulated
as proof of hardware readiness. --self-test requires dangerous mutations to fail.
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
DRIVER = ROOT / "drivers/phy/qualcomm/phy-qcom-qusb2.c"


def function(source, name):
    match = re.search(r"^static (?:int|void) " + name + r"\([^;]*?\)\s*\{", source, re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define __iomem
#define __maybe_unused
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define EPROBE_DEFER 517
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define dev_err(dev,...) ((void)(dev))
#define dev_dbg(dev,...) ((void)(dev))
#define dev_vdbg(dev,...) ((void)(dev))
#define QUSB2_V2_PREEMPHASIS_WIDTH_HALF_BIT 1
enum phy_mode { PHY_MODE_INVALID, PHY_MODE_USB_HOST_HS, PHY_MODE_USB_HOST_FS,
 PHY_MODE_USB_HOST_LS, PHY_MODE_USB_DEVICE_HS, PHY_MODE_USB_DEVICE_FS,
 PHY_MODE_USB_DEVICE_LS };
struct device { void *data; void *of_node; };
struct phy { struct device dev; void *data; };
struct clk { int votes, id; };
struct reset_control { bool asserted; };
struct regulator_bulk_data { const char *supply; int votes; };
struct regmap { u32 value; };
struct nvmem_cell { u8 trim; };
struct mutex { pthread_mutex_t p; };
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->p, NULL)); }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->p)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->p)); }
static void *phy_get_drvdata(struct phy *p) { return p->data; }
static void *dev_get_drvdata(struct device *d) { return d->data; }
static u32 readl(const void *p);
static u8 readb(const void *p);
static void writel(u32 val, void *p);
static int clk_prepare_enable(struct clk *c);
static void clk_disable_unprepare(struct clk *c);
static int regulator_bulk_enable(int n, struct regulator_bulk_data *v);
static int regulator_bulk_disable(int n, struct regulator_bulk_data *v);
static int reset_control_assert(struct reset_control *r);
static int reset_control_deassert(struct reset_control *r);
static int regmap_read(struct regmap *m, unsigned off, unsigned *v);
static void usleep_range(unsigned a, unsigned b);
static void *nvmem_cell_read(struct nvmem_cell *c, size_t *len);
static void kfree(void *p);
static void *of_find_property(void *np, const char *name, void *len);
static struct regmap *syscon_regmap_lookup_by_phandle(void *np, const char *name);
static void pm_runtime_disable(void *dev);
/* DWC3 register encoders are boundary scaffolding: the gated test must
   return before any DWC3 register access, and makes no register-map claim. */
#define USB_SPEED_HIGH 3
#define DWC3_GCTL_PRTCAP_HOST 1
#define DWC3_DCTL 0
#define DWC3_DCTL_CSFTRST 1
#define pr_err(...) ((void)0)
struct dwc3 { void *usb2_phy,*usb3_phy;struct phy *usb2_generic_phy,*usb3_generic_phy;
 unsigned maximum_speed,current_dr_role;void *regs; };
static void usb_phy_reset(void *p) { assert(!p); }
static int usb_phy_init(void *p) { assert(!p);return 0; }
static int phy_init(struct phy *p);
static int phy_exit(struct phy *p);
static u32 dwc3_readl(void *base,unsigned off) { (void)base;(void)off;assert(!"DWC3 reached unqualified hardware");return 0; }
static void dwc3_writel(void *base,unsigned off,u32 val) { (void)base;(void)off;(void)val;assert(!"DWC3 reached unqualified hardware"); }
static void udelay(unsigned us) { (void)us;assert(0); }
static void msleep(unsigned ms) { (void)ms;assert(0); }
static bool dwc3_is_usb31(struct dwc3 *d) { (void)d;return false; }
'''

HARNESS = r'''
static struct qusb2_phy q;
static struct phy phy;
static struct device device;
static struct clk clocks[3];
static struct reset_control rst;
static struct regmap tcsr;
static struct nvmem_cell cell;
static u32 registers[256];
static unsigned enables, fail_enable, asserts, fail_assert, deasserts, fail_deassert;
static unsigned reads, writes, regulator_enables, regulator_disables, pm_disables;
static unsigned enable_error, disable_error, tcsr_error, trim_error, buffers;
static bool tcsr_present;
static int tcsr_lookup_error;
static unsigned cases;
static void kfree(void *p) { assert(buffers);buffers--;free(p); }
static int phy_init(struct phy *p) { return p?qusb2_phy_init(p):0; }
static int phy_exit(struct phy *p) { return p?qusb2_phy_exit(p):0; }
static int init_prepared(struct phy *p)
{
 /* Boundary fixture only: exercise the prepared Sony path below its public
    ownership gate. No config flag, DT bypass, or production gate is changed. */
 struct qusb2_phy *qphy=phy_get_drvdata(p);
 if(qphy->cfg == &msm8996_dora_phy_cfg) {
  assert(qphy->cfg->needs_charger_handoff);
  return qusb2_phy_init_hw(p);
 }
 return qusb2_phy_init(p);
}
static void check_access(void)
{ assert(clocks[0].votes == 4 && clocks[1].votes == 4); }
static u32 readl(const void *p)
{ const u32 *r=p;check_access();assert(r >= registers && r < registers+256);reads++;return *r; }
static u8 readb(const void *p) { return readl(p); }
static void writel(u32 val, void *p)
{ u32 *r=p;check_access();assert(r >= registers && r < registers+256);writes++;*r=val; }
static int clk_prepare_enable(struct clk *c)
{ assert(c && c->votes == 3);if (++enables == fail_enable) return -EPROBE_DEFER;c->votes++;return 0; }
static void clk_disable_unprepare(struct clk *c)
{ assert(c && c->votes == 4);c->votes--; }
static int regulator_bulk_enable(int n, struct regulator_bulk_data *v)
{ int i;regulator_enables++;for(i=0;i<n;i++) { assert(v[i].votes == 2);v[i].votes++; }
  /* Model even an incompletely reported core rollback: driver must quarantine. */
  if(enable_error) { v[0].votes--;return -(int)enable_error; }return 0; }
static int regulator_bulk_disable(int n, struct regulator_bulk_data *v)
{ int i;regulator_disables++;if(disable_error) { v[0].votes--;return -(int)disable_error; }
  for(i=0;i<n;i++){assert(v[i].votes == 3);v[i].votes--;}return 0; }
static int reset_control_assert(struct reset_control *r)
{ check_access();asserts++;if(asserts == fail_assert)return -ETIMEDOUT;r->asserted=true;return 0; }
static int reset_control_deassert(struct reset_control *r)
{ check_access();deasserts++;if(deasserts == fail_deassert)return -EIO;r->asserted=false;return 0; }
static int regmap_read(struct regmap *m, unsigned off, unsigned *v)
{ assert(!off);if(tcsr_error)return -(int)tcsr_error;*v=m->value;return 0; }
static void usleep_range(unsigned a, unsigned b)
{ assert(a >= 100 && a <= b && b <= 160); }
static void *nvmem_cell_read(struct nvmem_cell *c, size_t *len)
{ u8 *v;if(trim_error)return ERR_PTR(-(int)trim_error);v=malloc(1);assert(v);*v=c->trim;
  if(len) { *len=1; } buffers++;return v; }
static void *of_find_property(void *np, const char *name, void *len)
{ assert(np == &device && !strcmp(name,"qcom,tcsr-syscon") && !len);return tcsr_present?np:NULL; }
static struct regmap *syscon_regmap_lookup_by_phandle(void *np, const char *name)
{ assert(np == &device && !strcmp(name,"qcom,tcsr-syscon"));
  return tcsr_lookup_error?ERR_PTR(tcsr_lookup_error):&tcsr; }
static void pm_runtime_disable(void *dev) { assert(dev == &device);pm_disables++; }

static void setup(const struct qusb2_phy_cfg *cfg, bool differential)
{
 unsigned i;
 if(cases)assert(!pthread_mutex_destroy(&q.lock.p));
 memset(&q,0,sizeof(q));memset(registers,0,sizeof(registers));
 memset(&rst,0,sizeof(rst));memset(&phy,0,sizeof(phy));
 mutex_init(&q.lock);q.phy=&phy;phy.data=&q;device.data=&q;device.of_node=&device;
 q.base=registers;q.cfg=cfg;q.iface_clk=&clocks[0];q.cfg_ahb_clk=&clocks[1];
 q.ref_clk=&clocks[2];q.phy_reset=&rst;
 for(i=0;i<3;i++){clocks[i].votes=3;clocks[i].id=i;}
 for(i=0;i<QUSB2_NUM_VREGS;i++){q.vregs[i].supply=qusb2_phy_vreg_names[i];q.vregs[i].votes=2;}
 registers[cfg->regs[QUSB2PHY_PLL_STATUS]/4]=cfg->mask_core_ready;
 tcsr.value=differential?0:1;q.tcsr=differential?&tcsr:NULL;
 enables=fail_enable=asserts=fail_assert=deasserts=fail_deassert=0;
 reads=writes=regulator_enables=regulator_disables=pm_disables=0;
 enable_error=disable_error=tcsr_error=trim_error=buffers=0;
 tcsr_present=false;tcsr_lookup_error=0;cases++;
}
static void balanced(void)
{ unsigned i;for(i=0;i<3;i++)assert(clocks[i].votes == 3);
  for(i=0;i<QUSB2_NUM_VREGS;i++)assert(q.vregs[i].votes == 2);
  assert(!q.clocks_enabled && !q.ref_clk_enabled && !q.phy_initialized && !buffers); }
static void held(void)
{ assert(clocks[0].votes == 4 && clocks[1].votes == 4 && q.clocks_enabled);
  assert(q.vregs[0].votes == 3 && q.vregs[1].votes == 3); }
static void blocked(void)
{
 unsigned r=reads,w=writes,e=enables,a=asserts,d=regulator_disables;
 assert(q.quarantined);
 assert(init_prepared(&phy)==-EIO && qusb2_phy_exit(&phy)==-EIO);
 assert(qusb2_phy_runtime_suspend(&device)==-EIO && qusb2_phy_runtime_resume(&device)==-EIO);
 assert(qusb2_phy_set_mode(&phy,PHY_MODE_USB_DEVICE_HS)==-EIO);
 assert(reads==r && writes==w && enables==e && asserts==a && regulator_disables==d);
}
static void *mode_worker(void *arg)
{ unsigned i;(void)arg;for(i=0;i<2000;i++)assert(!qusb2_phy_set_mode(&phy,PHY_MODE_USB_DEVICE_HS));return NULL; }
static void *pm_worker(void *arg)
{ unsigned i;(void)arg;for(i=0;i<2000;i++) {assert(!qusb2_phy_runtime_suspend(&device));assert(!qusb2_phy_runtime_resume(&device));}return NULL; }
static void *gate_worker(void *arg)
{ unsigned i;(void)arg;for(i=0;i<2000;i++)assert(qusb2_phy_init(&phy)==-EOPNOTSUPP);return NULL; }
static void gated(void)
{
 assert(qusb2_phy_init(&phy)==-EOPNOTSUPP);
 assert(!regulator_enables && !regulator_disables && !enables && !asserts && !deasserts && !reads && !writes);
 assert(!q.quarantined && !q.suspended && q.mode==PHY_MODE_INVALID);
 balanced();
}
static void run_tests(void)
{
 unsigned i,j,line;
 const struct qusb2_phy_cfg *cfgs[]={&msm8996_dora_phy_cfg,&msm8996_phy_cfg,&sdm845_phy_cfg};
 setup(&msm8996_dora_phy_cfg,false);gated();
 {
  struct dwc3 dwc={.usb2_generic_phy=&phy,.maximum_speed=USB_SPEED_HIGH};
  setup(&msm8996_dora_phy_cfg,false);
  /* Execute DWC3's real generic-PHY error propagation before core reset. */
  assert(dwc3_core_soft_reset(&dwc)==-EOPNOTSUPP);gated();
 }
 {
  pthread_t a,b;setup(&msm8996_dora_phy_cfg,true);
  assert(!pthread_create(&a,NULL,gate_worker,NULL));assert(!pthread_create(&b,NULL,gate_worker,NULL));
  assert(!pthread_join(a,NULL));assert(!pthread_join(b,NULL));gated();
 }
 for(i=0;i<ARRAY_SIZE(cfgs);i++)for(j=0;j<2;j++) {
  setup(cfgs[i],j);assert(!init_prepared(&phy));assert(q.phy_initialized);held();
  assert(!qusb2_phy_set_mode(&phy,PHY_MODE_USB_DEVICE_HS));
  assert(!qusb2_phy_runtime_suspend(&device));assert(q.suspended && !q.clocks_enabled);
  assert(!qusb2_phy_runtime_suspend(&device)); /* no extra clock puts */
  assert(qusb2_phy_exit(&phy)==-EBUSY); /* never touch unclocked MMIO */
  assert(!qusb2_phy_runtime_resume(&device));assert(!q.suspended);
  assert(!qusb2_phy_runtime_resume(&device)); /* no duplicate votes */
  assert(!qusb2_phy_exit(&phy));assert(rst.asserted);balanced();
  assert(!qusb2_phy_runtime_suspend(&device));assert(!qusb2_phy_runtime_resume(&device));
 }
 /* Sony MSM8996 source bits: EN=0x05, DP_HIGH=0x02, DM_HIGH=0x08.
    Exercise all real line levels, independent of speed-mode assumptions. */
 for(i=0;i<2;i++)for(line=0;line<4;line++) {
  u32 expected=0x05 | ((line&1)?0:0x02) | ((line&2)?0:0x08);
  setup(cfgs[i],false);assert(!init_prepared(&phy));registers[0xf4/4]=line;
  q.mode=PHY_MODE_USB_DEVICE_LS;assert(!qusb2_phy_runtime_suspend(&device));
  assert(registers[0xbc/4]==expected && !(registers[0xbc/4]&0x10));
  assert(!qusb2_phy_runtime_resume(&device));assert(!qusb2_phy_exit(&phy));balanced();
 }
 /* Newer hardware retains its existing wake encoding. */
 for(i=0;i<3;i++) {
  static const enum phy_mode modes[]={PHY_MODE_USB_DEVICE_HS,PHY_MODE_USB_DEVICE_LS,PHY_MODE_INVALID};
  static const u32 masks[]={0x13,0x0b,0x1b};
  setup(&sdm845_phy_cfg,false);assert(!init_prepared(&phy));q.mode=modes[i];
  assert(!qusb2_phy_runtime_suspend(&device));assert(registers[0x230/4]==masks[i]);
  assert(!qusb2_phy_runtime_resume(&device));assert(!qusb2_phy_exit(&phy));balanced();
 }
 /* Every clock acquisition fault rolls back only our successful votes. */
 for(i=1;i<=3;i++) {
  setup(&msm8996_dora_phy_cfg,true);fail_enable=i;
  assert(init_prepared(&phy)==-EPROBE_DEFER);assert(!q.quarantined);balanced();
  fail_enable=0;assert(!init_prepared(&phy));assert(!qusb2_phy_exit(&phy));balanced();
 }
 for(i=1;i<=3;i++) {
  setup(&msm8996_dora_phy_cfg,true);assert(!init_prepared(&phy));assert(!qusb2_phy_runtime_suspend(&device));
  fail_enable=enables+i;assert(qusb2_phy_runtime_resume(&device)==-EPROBE_DEFER);
  assert(q.suspended && !q.quarantined && !q.clocks_enabled);
  for(j=0;j<3;j++)assert(clocks[j].votes==3);
  fail_enable=0;assert(!qusb2_phy_runtime_resume(&device));assert(!qusb2_phy_exit(&phy));balanced();
 }
 setup(&msm8996_dora_phy_cfg,false);enable_error=EIO;
 assert(init_prepared(&phy)==-EIO);blocked();
 setup(&msm8996_dora_phy_cfg,false);fail_assert=1;
 assert(init_prepared(&phy)==-ETIMEDOUT);held();blocked();
 setup(&msm8996_dora_phy_cfg,false);fail_deassert=1;
 assert(init_prepared(&phy)==-EIO && !q.quarantined && rst.asserted);balanced();
 setup(&msm8996_dora_phy_cfg,false);fail_deassert=1;fail_assert=2;
 assert(init_prepared(&phy)==-EIO);held();blocked(); /* first errno preserved */
 setup(&msm8996_dora_phy_cfg,true);tcsr_error=ETIMEDOUT;
 assert(init_prepared(&phy)==-ETIMEDOUT && !q.quarantined);balanced();
 setup(&msm8996_dora_phy_cfg,false);registers[0x38/4]=0;
 assert(init_prepared(&phy)==-EBUSY && rst.asserted && !q.quarantined);balanced();
 setup(&msm8996_dora_phy_cfg,false);registers[0x38/4]=0;fail_assert=2;
 assert(init_prepared(&phy)==-EBUSY);held();blocked();
 setup(&msm8996_dora_phy_cfg,false);fail_enable=1;disable_error=EIO;
 assert(init_prepared(&phy)==-EPROBE_DEFER);blocked();
 setup(&msm8996_dora_phy_cfg,false);assert(!init_prepared(&phy));fail_assert=2;
 assert(qusb2_phy_exit(&phy)==-ETIMEDOUT);held();assert(q.phy_initialized);blocked();
 setup(&msm8996_dora_phy_cfg,false);assert(!init_prepared(&phy));disable_error=EIO;
 assert(qusb2_phy_exit(&phy)==-EIO && rst.asserted && q.phy_initialized);blocked();
 setup(&msm8996_dora_phy_cfg,true);assert(!init_prepared(&phy));assert(!qusb2_phy_runtime_suspend(&device));
 registers[0x38/4]=0;assert(qusb2_phy_runtime_resume(&device)==-EBUSY);held();blocked();
 /* Optional syscon absence, declared defer/error, and real resolved provider. */
 setup(&msm8996_dora_phy_cfg,false);assert(!qusb2_phy_get_tcsr(&device,&q) && !q.tcsr);
 tcsr_present=true;tcsr_lookup_error=-EPROBE_DEFER;
 assert(qusb2_phy_get_tcsr(&device,&q)==-EPROBE_DEFER);
 tcsr_lookup_error=-EINVAL;assert(qusb2_phy_get_tcsr(&device,&q)==-EINVAL);
 tcsr_lookup_error=0;assert(!qusb2_phy_get_tcsr(&device,&q) && q.tcsr==&tcsr);
 qusb2_phy_disable_runtime_pm(&device);assert(pm_disables==1);
 /* Source tuning and zero/error fuse fallback remain intact. */
 for(i=0;i<3;i++) {
  setup(&msm8996_dora_phy_cfg,false);q.cell=&cell;cell.trim=i?9:0;trim_error=i==2?EIO:0;
  assert(!init_prepared(&phy));assert(registers[0x80/4]==0xd0 && registers[0x88/4]==0x83 && registers[0x8c/4]==0xc7);
  assert(registers[0x84/4]==(i==1?0x93:0xa3) && !buffers);assert(!qusb2_phy_exit(&phy));balanced();
 }
 /* Actual mutex serializes runtime operations and mode changes. */
 {
  pthread_t a,b;setup(&msm8996_dora_phy_cfg,true);assert(!init_prepared(&phy));
  assert(!pthread_create(&a,NULL,pm_worker,NULL));assert(!pthread_create(&b,NULL,mode_worker,NULL));
  assert(!pthread_join(a,NULL));assert(!pthread_join(b,NULL));assert(!qusb2_phy_exit(&phy));balanced();
 }
}
int main(void)
{ run_tests();printf("PASS: %u actual-QUSB2 cases: public Dora gate, prepared Sony IRQ/line readback, clock ownership, PLL, faults, quarantine, PM/concurrency; hardware handoff still unqualified\n",cases);return 0; }
'''


def compile_run(source, directory, name, expect_success=True):
    production = source[source.index("#define QUSB2PHY_PLL_TEST"):source.index("static const struct phy_ops")]
    production += function(source, "qusb2_phy_get_tcsr")
    production += function(source, "qusb2_phy_disable_runtime_pm")
    production += function((ROOT / "drivers/usb/dwc3/core.c").read_text(), "dwc3_core_soft_reset")
    c, binary = directory / (name + ".c"), directory / name
    c.write_text(PREAMBLE + production + HARNESS)
    flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if os.environ.get("USB_HANDOFF_SANITIZE") else []
    subprocess.run(shlex.split(os.environ.get("HOSTCC", "cc")) +
                   ["-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror", "-pthread", *flags, str(c), "-o", str(binary)], check=True)
    result = subprocess.run([str(binary)], text=True, capture_output=True,
                            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
    if expect_success:
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout, end="")
        return int(re.search(r"PASS: (\d+)", result.stdout)[1])
    assert result.returncode != 0, "unsafe mutation escaped: " + name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    source = DRIVER.read_text()
    # Provider PM is drained before managed resources disappear; callback data
    # must already exist when runtime PM becomes visible to userspace.
    assert source.index("dev_set_drvdata(dev, qphy)") < source.index("pm_runtime_enable(dev)")
    assert "devm_add_action_or_reset(dev, qusb2_phy_disable_runtime_pm, dev)" in source
    assert source.index("devm_phy_create(dev,") < source.index("devm_add_action_or_reset(dev,") < source.index("devm_of_phy_provider_register(dev,")
    assert "regulator_register(" not in source and "power_supply_register(" not in source
    mutations = {
        "remove-dora-ownership-gate": (".needs_charger_handoff = true,", ".needs_charger_handoff = false,"),
        "wrong-msm8996-dm-bit": ("#define MSM8996_DMSE_INTR_EN\t\t\tBIT(2)", "#define MSM8996_DMSE_INTR_EN\t\t\tBIT(1)"),
        "wrong-msm8996-dp-bit": ("#define MSM8996_DPSE_INTR_EN\t\t\tBIT(0)", "#define MSM8996_DPSE_INTR_EN\t\t\tBIT(2)"),
        "wrong-msm8996-dm-polarity": ("#define MSM8996_DMSE_INTR_HIGH_SEL\t\tBIT(3)", "#define MSM8996_DMSE_INTR_HIGH_SEL\t\tBIT(4)"),
        "wrong-msm8996-dp-polarity": ("#define MSM8996_DPSE_INTR_HIGH_SEL\t\tBIT(1)", "#define MSM8996_DPSE_INTR_HIGH_SEL\t\tBIT(3)"),
        "wrong-msm8996-status": ("#define MSM8996_PORT_UTMI_STATUS\t\t\t0xf4", "#define MSM8996_PORT_UTMI_STATUS\t\t\t0xf0"),
        "skip-pll-readback": ("if (!(val & qphy->cfg->mask_core_ready))", "if (!(val | qphy->cfg->mask_core_ready))"),
        "release-after-reset-failure": ("ret = reset_control_assert(qphy->phy_reset);\n\tif (ret) {\n\t\tqphy->quarantined = true;", "ret = reset_control_assert(qphy->phy_reset);\n\tif (0) {\n\t\tqphy->quarantined = true;"),
        "drop-sticky-quarantine": ("if (qphy->quarantined)", "if (false)"),
        "swallow-tcsr-error": ("return PTR_ERR(qphy->tcsr);", "return 0;"),
        "forget-ref-vote": ("qphy->ref_clk_enabled = true;", "qphy->ref_clk_enabled = false;"),
    }
    with tempfile.TemporaryDirectory(prefix="dora-usb-handoff-") as tmp:
        directory = Path(tmp)
        cases = compile_run(source, directory, "production")
        if args.self_test:
            for name, (before, after) in mutations.items():
                assert before in source, name
                compile_run(source.replace(before, after), directory, name, False)
            print(f"PASS: {len(mutations)} unsafe QUSB2 mutations rejected")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"production_cases": cases,
            "negative_mutations": len(mutations) if args.self_test else 0,
            "physical_usb_charging_handoff_verified": False,
            "dpdm_vbus_id_owner_integrated": False,
            "dora_public_usb_init_blocked": True}, indent=2) + "\n")


if __name__ == "__main__":
    main()
