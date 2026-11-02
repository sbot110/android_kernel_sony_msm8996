#!/usr/bin/env python3
"""Exercise real UFS common power-on and regulator helpers with fault injection.

No physical PHY, RPM or regulator hardware is accessed by this host model.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / "drivers/phy/qualcomm/phy-qcom-ufs.c").read_text()


def function(name):
    match = re.search(r"^(?:static\s+)?int\s+" + name + r"\(", source, re.M)
    if not match:
        raise ValueError("missing UFS helper " + name)
    brace = source.index("{", match.start())
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define dev_err(...) ((void)++error_logs)
struct device { int unused; };
struct ufs_qcom_phy_vreg {
    const char *name;
    struct ufs_qcom_phy_vreg *reg;
    bool enabled, is_always_on, configured, hardware_on;
    int id, enable_calls;
};
struct ufs_qcom_phy_rpmh_rsc { bool enabled; };
struct ufs_qcom_phy;
struct specific_ops { void (*power_control)(struct ufs_qcom_phy *, bool); };
struct ufs_qcom_phy {
    struct device *dev;
    struct ufs_qcom_phy_vreg vdda_phy, vdda_pll, vddp_ref_clk;
    struct ufs_qcom_phy_rpmh_rsc rpmh_rsc;
    struct specific_ops *phy_spec_ops;
    bool is_powered_on, is_ref_clk_enabled, is_iface_clk_enabled;
};
struct phy { struct ufs_qcom_phy *common; };
enum { FAIL_NONE, FAIL_RPMH, FAIL_PHY_CFG, FAIL_PHY_ENABLE, FAIL_PLL_CFG,
    FAIL_PLL_ENABLE, FAIL_IFACE, FAIL_REF, FAIL_PAD_CFG, FAIL_PAD_ENABLE };
static int fail_at, fail_cleanup_id, error_logs, power_changes, rpmh_changes;
static bool analog_on, fail_rpmh_drop;
static struct ufs_qcom_phy *get_ufs_qcom_phy(struct phy *p) { return p->common; }
static int ufs_qcom_phy_cfg_vreg(struct device *d, struct ufs_qcom_phy_vreg *v, bool on)
{
    int stage = v->id == 1 ? FAIL_PHY_CFG : v->id == 2 ? FAIL_PLL_CFG : FAIL_PAD_CFG;
    (void)d; v->configured = on;
    return on && fail_at == stage ? -EIO : 0;
}
static int regulator_enable(struct ufs_qcom_phy_vreg *v)
{
    int stage = v->id == 1 ? FAIL_PHY_ENABLE : v->id == 2 ? FAIL_PLL_ENABLE : FAIL_PAD_ENABLE;
    v->enable_calls++;
    if (fail_at == stage) return -EIO;
    v->hardware_on = true; return 0;
}
static int regulator_disable(struct ufs_qcom_phy_vreg *v)
{
    if (v->id != 3) assert(!analog_on); /* reset before analog rail collapse */
    if (v->id == fail_cleanup_id) return -EBUSY;
    v->hardware_on = false; return 0;
}
static int regulator_set_load(struct ufs_qcom_phy_vreg *v, int load)
{ v->configured = load != 0; return 0; }
static int ufs_qcom_phy_setup_rpmh_rsc(struct device *d, struct ufs_qcom_phy_rpmh_rsc *r, bool on)
{
    (void)d; rpmh_changes++;
    if ((on && fail_at == FAIL_RPMH) || (!on && fail_rpmh_drop)) return -EBUSY;
    r->enabled = on; return 0;
}
static void power_control(struct ufs_qcom_phy *p, bool on)
{ assert(p->vdda_phy.hardware_on); analog_on = on; power_changes++; }
static int ufs_qcom_phy_enable_iface_clk(struct ufs_qcom_phy *p)
{ if (fail_at == FAIL_IFACE) return -EIO; p->is_iface_clk_enabled = true; return 0; }
static int ufs_qcom_phy_enable_ref_clk(struct ufs_qcom_phy *p)
{ if (fail_at == FAIL_REF) return -EIO; p->is_ref_clk_enabled = true; return 0; }
static void ufs_qcom_phy_disable_iface_clk(struct ufs_qcom_phy *p)
{ assert(!analog_on); p->is_iface_clk_enabled = false; }
static void ufs_qcom_phy_disable_ref_clk(struct ufs_qcom_phy *p)
{ assert(!analog_on); p->is_ref_clk_enabled = false; }
'''
for name in ("ufs_qcom_phy_enable_vreg", "ufs_qcom_phy_disable_vreg", "ufs_qcom_phy_power_on"):
    harness += function(name)
harness += r'''
static struct device dev;
static struct specific_ops ops = { .power_control = power_control };
static void init(struct ufs_qcom_phy *p)
{
    memset(p, 0, sizeof(*p)); p->dev = &dev; p->phy_spec_ops = &ops;
    p->vdda_phy.name = "vdda-phy"; p->vdda_phy.id = 1; p->vdda_phy.reg = &p->vdda_phy;
    p->vdda_pll.name = "vdda-pll"; p->vdda_pll.id = 2; p->vdda_pll.reg = &p->vdda_pll;
    p->vddp_ref_clk.name = "vddp-ref-clk"; p->vddp_ref_clk.id = 3; p->vddp_ref_clk.reg = &p->vddp_ref_clk;
    fail_at = FAIL_NONE; fail_cleanup_id = 0; fail_rpmh_drop = false;
    error_logs = power_changes = rpmh_changes = 0; analog_on = false;
}
int main(void)
{
    struct ufs_qcom_phy p;
    struct phy phy = { .common = &p };
    int stage, ret;
    for (stage = FAIL_RPMH; stage <= FAIL_PAD_ENABLE; stage++) {
        init(&p); fail_at = stage;
        ret = ufs_qcom_phy_power_on(&phy);
        assert(ret == (stage == FAIL_RPMH ? -EBUSY : -EIO));
        assert(!p.is_powered_on && !analog_on && !p.rpmh_rsc.enabled);
        assert(!p.vdda_phy.enabled && !p.vdda_pll.enabled && !p.vddp_ref_clk.enabled);
        assert(!p.vdda_phy.configured && !p.vdda_pll.configured && !p.vddp_ref_clk.configured);
        assert(!p.is_ref_clk_enabled && !p.is_iface_clk_enabled);
        assert(power_changes == (stage >= FAIL_PLL_CFG ? 2 : 0));
    }
    init(&p); assert(ufs_qcom_phy_power_on(&phy) == 0);
    assert(p.is_powered_on && analog_on && p.rpmh_rsc.enabled);
    stage = power_changes; ret = rpmh_changes;
    assert(ufs_qcom_phy_power_on(&phy) == 0);
    assert(power_changes == stage && rpmh_changes == ret); /* no double votes */

    /* Always-on L25 must restore active load without a second enable reference. */
    init(&p); p.vddp_ref_clk.is_always_on = true;
    assert(ufs_qcom_phy_enable_vreg(&dev, &p.vddp_ref_clk) == 0);
    assert(ufs_qcom_phy_disable_vreg(&dev, &p.vddp_ref_clk) == 0);
    assert(p.vddp_ref_clk.enabled && p.vddp_ref_clk.hardware_on && !p.vddp_ref_clk.configured);
    assert(ufs_qcom_phy_enable_vreg(&dev, &p.vddp_ref_clk) == 0);
    assert(p.vddp_ref_clk.configured && p.vddp_ref_clk.enable_calls == 1);
    fail_at = FAIL_PAD_CFG;
    assert(ufs_qcom_phy_enable_vreg(&dev, &p.vddp_ref_clk) == -EIO);
    assert(!p.vddp_ref_clk.configured && p.vddp_ref_clk.enabled);

    /* A cleanup failure must not replace the original provider failure. */
    init(&p); fail_at = FAIL_PLL_ENABLE; fail_rpmh_drop = true;
    assert(ufs_qcom_phy_power_on(&phy) == -EIO);
    assert(!p.is_powered_on && !analog_on && p.rpmh_rsc.enabled && error_logs);
    init(&p); fail_at = FAIL_PLL_ENABLE; fail_cleanup_id = 1;
    assert(ufs_qcom_phy_power_on(&phy) == -EIO);
    assert(!p.is_powered_on && !analog_on && p.vdda_phy.enabled && error_logs);
    puts("PASS: actual UFS power-on/helpers, nine injected failures, reset-before-collapse, RPMH balance and always-on load restore");
    puts("PASS: cleanup errors preserve original failure and truthful residual resource flags");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="dora-ufs-power-") as scratch:
    c_file, binary = Path(scratch) / "power.c", Path(scratch) / "power"
    c_file.write_text(harness)
    subprocess.run([os.environ.get("HOSTCC", "cc"), "-std=c99", "-Wall", "-Wextra",
                    "-Werror", str(c_file), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("Host model only; regulator-failure recovery and physical suspend/PHY behavior remain unverified")
