#!/usr/bin/env python3
"""Execute the production MSM8996 PLL decoder and boot MMIO observation path."""
import argparse
import os
import re
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "drivers/clk/qcom/clk-msm8996-boot.h"
REFERENCE = "0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1"


def reference_topology(cpu, pll):
    """Independently parse the original operational mux/PLL register contract."""
    offsets = {"MUX_OFFSET": 0x40, "CBF_MUX_OFFSET": 0x18}
    for cluster in ("C0", "C1"):
        for field, offset in (("MODE", 0), ("L_VAL", 4), ("ALPHA", 8), ("USER_CTL", 0x10)):
            offsets[cluster + "_PLL_" + field] = offset
            offsets[cluster + "_PLLA_" + field] = offset + 0x100
    offsets.update({"CBF_PLL_MODE": 0, "CBF_PLL_L_VAL": 8,
                    "CBF_PLL_ALPHA": 0x10, "CBF_PLL_USER_CTL": 0x18})
    for name, expected in offsets.items():
        match = re.search(r"#define\s+" + name + r"\s+(0x[0-9A-Fa-f]+|\d+)\b", cpu)
        if not match or int(match[1], 0) != expected:
            raise ValueError("Sony CPU register offset changed: " + name)

    def block(name):
        match = re.search(r"static struct \w+ " + name + r" = \{", cpu)
        if not match:
            raise ValueError("missing Sony CPU object: " + name)
        end, depth = match.end(), 1
        while depth:
            depth += (cpu[end] == "{") - (cpu[end] == "}")
            end += 1
        return cpu[match.end():end - 1]

    expected = {
        "pwrcl_lf_mux": (2, [("pwrcl_pll_main", 1), ("sys_apcsaux_clk", 3)]),
        "perfcl_lf_mux": (2, [("perfcl_pll_main", 1), ("sys_apcsaux_clk", 3)]),
        "pwrcl_hf_mux": (0, [("pwrcl_pll", 1), ("pwrcl_lf_mux", 0)]),
        "perfcl_hf_mux": (0, [("perfcl_pll", 1), ("perfcl_lf_mux", 0)]),
        "cbf_hf_mux": (0, [("cbf_pll", 1), ("cbf_pll_main", 2),
                          ("sys_apcsaux_clk", 3)]),
    }
    for name, (shift, parents) in expected.items():
        body = block(name)
        actual = [(parent, int(selector)) for parent, selector in
                  re.findall(r"\{\s*&([\w]+)\.c,\s*(\d+)\s*\}", body)]
        if actual != parents or not re.search(r"\.shift\s*=\s*" + str(shift) + r"\b", body):
            raise ValueError("Sony CPU mux topology changed: " + name)
    for name in ("pwrcl_pll", "perfcl_pll", "cbf_pll"):
        body = block(name)
        for field, value in (("lock_mask", r"BIT\(31\)"),
                             ("main_output_mask", r"BIT\(0\)"),
                             ("early_output_mask", r"BIT\(3\)")):
            if not re.search(r"\." + field + r"\s*=\s*" + value, body):
                raise ValueError("Sony CPU PLL output/lock contract changed")
    alternate = block("alt_pll_masks")
    if (not re.search(r"\.lock_mask\s*=\s*BIT\(31\)", alternate) or
            not re.search(r"\.output_mask\s*=\s*0xf", alternate)):
        raise ValueError("Sony alternate PLL lock/output masks changed")
    for name in ("pwrcl_alt_pll", "perfcl_alt_pll"):
        if not re.search(r"\.enable_config\s*=\s*0x9\b", block(name)):
            raise ValueError("Sony alternate PLL enabled outputs changed")
    for cluster in ("pwrcl", "perfcl"):
        if not re.search(cluster + r"_hf_mux\.parents\[i\]\.sel\s*=\s*2;", cpu):
            raise ValueError("Sony ACD selector changed")
    if not re.search(r"DEFINE_FIXED_SLAVE_DIV_CLK\(sys_apcsaux_clk,\s*2,\s*"
                     r"&sys_apcsaux_clk_gcc\.c\)", cpu):
        raise ValueError("Sony auxiliary divider changed")
    if not re.search(r"u32 mask = PLL_BYPASSNL \| PLL_RESET_N \| PLL_OUTCTRL;", pll):
        raise ValueError("Sony enabled-PLL handoff contract changed")
    for name, bit in (("PLL_OUTCTRL", 0), ("PLL_BYPASSNL", 1), ("PLL_RESET_N", 2)):
        if not re.search(r"#define\s+" + name + r"\s+BIT\(" + str(bit) + r"\)", pll):
            raise ValueError("Sony PLL mode bits changed")


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef int64_t s64;
typedef int32_t s32;
typedef int16_t s16;
typedef int8_t s8;
#include "clk-msm8996-boot.h"
int main(void)
{
    u64 rate = 0;
    const u32 alpha_enable = 1U << 24, mn_mode = 1U << 25;
    /* Original Sony 19.2 MHz reference and 883.2 MHz early CPU rate. */
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0, 0, &rate));
    assert(rate == 883200000);
    /* Signed 16-bit Huayra fraction: +1/4 and -1/2. */
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0x4000,
                                    alpha_enable, &rate));
    assert(rate == 888000000);
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0x8000,
                                    alpha_enable, &rate));
    assert(rate == 873600000);
    /* A negative fractional remainder must round down, as native CCF does. */
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0xffff,
                                    alpha_enable, &rate));
    assert(rate == 883199707);
    /* M/N mode: signed numerator, unsigned denominator; reject N=0. */
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0x0104,
                                    alpha_enable | mn_mode, &rate));
    assert(rate == 888000000);
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0xff04,
                                    alpha_enable | mn_mode, &rate));
    assert(rate == 878400000);
    assert(msm8996_boot_huayra_rate(19200000, 46, 0x0100,
                                   alpha_enable | mn_mode, &rate) == -EINVAL);
    /* Disabled alpha must ignore stale M/N values, including zero N. */
    assert(!msm8996_boot_huayra_rate(19200000, 46, 0x0100,
                                    mn_mode, &rate));
    assert(rate == 883200000);
    assert(msm8996_boot_huayra_rate(19200000, 0, 0, 0, &rate) == -EINVAL);
    assert(msm8996_boot_huayra_rate(19200000, 46, 0, 1U << 12, &rate) == -EINVAL);
    assert(msm8996_boot_main_div(0) == 1);
    assert(msm8996_boot_main_div(0x100) == 2);
    assert(msm8996_boot_main_div(0x300) == 4);
    assert(msm8996_boot_main_div(0x200) == -EINVAL);
    return 0;
}
'''

OBSERVER_PREAMBLE = r'''
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#define __iomem
#define BIT(n) (1U << (n))
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))
struct clk_init_data;
struct clk_hw { struct clk_init_data *init; };
struct clk { unsigned long rate; };
static u32 registers[0x90000 / 4];
static unsigned reads, change_at, change_offset, barriers, aux_reads;
static u32 change_mask;
static bool changing_aux;
static u32 readl_relaxed(const void *address)
{
    size_t offset = (const char *)address - (const char *)registers;
    assert(offset < sizeof(registers) && !(offset & 3));
    reads++;
    if (change_at && reads == change_at)
        registers[change_offset / 4] ^= change_mask;
    return registers[offset / 4];
}
static void rmb(void) { barriers++; }
static unsigned long clk_get_rate(struct clk *clock)
{
    aux_reads++;
    return clock->rate + (changing_aux && aux_reads > 1 ? 1 : 0);
}
/* Probe API model: actual probe code must resolve AUX before publishing. */
#define EPROBE_DEFER 517
#define IORESOURCE_MEM 0
#define GFP_KERNEL 0
#define CLK_GET_RATE_NOCACHE 1
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define dev_info(...) ((void)0)
#define of_clk_hw_onecell_get NULL
struct device { void *of_node; };
struct platform_device { struct device dev; };
struct resource { unsigned long size; };
struct clk_ops { unsigned long (*recalc_rate)(struct clk_hw *, unsigned long); };
struct clk_init_data {
    const char *name;
    const struct clk_ops *ops;
    unsigned flags;
    const char **parent_names;
    unsigned num_parents;
};
struct clk_hw_onecell_data { unsigned num; struct clk_hw *hws[]; };
static struct clk probe_xo = { .rate = 19200000 }, probe_aux = { .rate = 600000000 };
static bool probe_cbf;
static int aux_error;
static unsigned registrations, providers;
static struct resource resource = { .size = sizeof(registers) };
static void *allocations[8];
static unsigned allocation_count;
static bool of_device_is_compatible(void *node, const char *compatible)
{ (void)node; (void)compatible; return probe_cbf; }
static struct resource *platform_get_resource(struct platform_device *pdev, int type, unsigned index)
{ (void)pdev; (void)type; (void)index; return &resource; }
static unsigned long resource_size(struct resource *res) { return res->size; }
static void *devm_ioremap_resource(struct device *dev, struct resource *res)
{ (void)dev; (void)res; return registers; }
static struct clk *devm_clk_get(struct device *dev, const char *name)
{
    (void)dev;
    if (!strcmp(name, "xo")) return &probe_xo;
    assert(!strcmp(name, "aux"));
    assert(!registrations && !providers);
    return aux_error ? (struct clk *)(intptr_t)aux_error : &probe_aux;
}
static const char *__clk_get_name(struct clk *clk)
{ assert(clk == &probe_xo); return "xo_board"; }
static void *devm_kzalloc(struct device *dev, size_t size, unsigned flags)
{
    (void)dev; (void)flags; assert(allocation_count < 8);
    allocations[allocation_count] = calloc(1, size);
    assert(allocations[allocation_count]); return allocations[allocation_count++];
}
static void *devm_kcalloc(struct device *dev, size_t count, size_t size, unsigned flags)
{ return devm_kzalloc(dev, count * size, flags); }
static int devm_clk_hw_register(struct device *dev, struct clk_hw *hw)
{ (void)dev; assert(hw->init && hw->init->ops); registrations++; return 0; }
static unsigned long clk_hw_get_rate(struct clk_hw *hw);
static int devm_of_clk_add_hw_provider(struct device *dev, void *get,
                                     struct clk_hw_onecell_data *data)
{ (void)dev; (void)get; assert(data->num == registrations); providers++; return 0; }
'''

OBSERVER_HARNESS = r'''
static struct clk auxiliary = { .rate = 600000000 };
static struct msm8996_boot_clk clock = { .base = registers, .aux = &auxiliary };
static unsigned long clk_hw_get_rate(struct clk_hw *hw)
{ return hw->init->ops->recalc_rate(hw, probe_xo.rate); }
static void reset(bool cbf, u32 mux)
{
    memset(registers, 0, sizeof(registers));
    reads = change_at = barriers = aux_reads = 0;
    changing_aux = false;
    auxiliary.rate = 600000000;
    clock.cbf = cbf;
    clock.base = registers;
    registers[(cbf ? 0x18 : 0x40) / 4] = mux;
}
static void pll(unsigned offset, bool cbf, u32 l, u32 user)
{
    registers[offset / 4] = BIT(31) | 7;
    registers[(offset + (cbf ? 8 : 4)) / 4] = l;
    registers[(offset + (cbf ? 0x18 : 0x10)) / 4] = user;
}
static unsigned long measured(void)
{
    unsigned long rate = msm8996_boot_recalc(&clock.hw, 19200000);
    assert(barriers >= 1);
    return rate;
}
int main(void)
{
    unsigned i, j, boundary, total;
    const struct { bool cbf; u32 mux, base, user; } sources[] = {
        {false, 1, 0, BIT(3)}, {false, 4, 0, BIT(0) | 0x100},
        {false, 3, 0x100, BIT(3)}, {true, 1, 0xf000, BIT(3)},
        {true, 2, 0xf000, BIT(0) | 0x100},
    };
    struct platform_device pdev = { };
    assert(!decoder_test());
    /* XO and source-proven AUX bypass an unconfigured, disabled PLL. */
    reset(false, 0); assert(measured() == 19200000);
    reset(true, 0); assert(measured() == 19200000);
    reset(false, 0x3c); assert(measured() == 300000000);
    assert(reads == 4 && aux_reads == 2);
    reset(true, 3); assert(measured() == 300000000);
    assert(reads == 4 && aux_reads == 2);
    reset(false, 0x3c); auxiliary.rate = 0; assert(!measured());
    reset(false, 0x3c); changing_aux = true; assert(!measured());
    reset(true, 3); changing_aux = true; assert(!measured());
    /* Reserved SMUX2 has no invented rate; primary early and ACD are 1:1. */
    reset(false, 8); pll(0, false, 46, BIT(0) | BIT(3)); assert(!measured());
    reset(false, 1); pll(0, false, 46, BIT(3)); assert(measured() == 883200000);
    reset(false, 2); pll(0, false, 46, BIT(3)); assert(measured() == 883200000);
    reset(false, 4); pll(0, false, 46, BIT(0) | 0x100); assert(measured() == 441600000);
    reset(false, 4); pll(0, false, 46, BIT(0) | 0x300); assert(measured() == 220800000);
    reset(false, 4); pll(0, false, 46, BIT(0) | 0x200); assert(!measured());
    /* Use the alternate register bank; stale primary registers remain zero. */
    reset(false, 3); pll(0x100, false, 29, BIT(3)); assert(measured() == 556800000);
    reset(false, 3); pll(0x100, false, 29, BIT(3) | BIT(24));
    registers[0x10c / 4] = 0x80; assert(measured() == 566400000);
    reset(true, 1); pll(0xf000, true, 46, BIT(3)); assert(measured() == 883200000);
    reset(true, 2); pll(0xf000, true, 46, BIT(0) | 0x100); assert(measured() == 441600000);
    reset(true, 1); pll(0xf000, true, 46, BIT(3) | BIT(24));
    registers[0xf014 / 4] = 0x80; assert(measured() == 892800000);
    /* Lock, all three original handoff mode bits and selected output matter. */
    for (i = 0; i < 4; i++) {
        reset(false, 1); pll(0, false, 46, BIT(3));
        registers[0] &= ~(i == 3 ? BIT(31) : BIT(i)); assert(!measured());
        reset(true, 1); pll(0xf000, true, 46, BIT(3));
        registers[0xf000 / 4] &= ~(i == 3 ? BIT(31) : BIT(i)); assert(!measured());
    }
    reset(false, 1); pll(0, false, 46, BIT(0)); assert(!measured());
    reset(false, 4); pll(0, false, 46, BIT(3)); assert(!measured());
    reset(false, 3); pll(0x100, false, 29, BIT(0)); assert(!measured());
    reset(true, 1); pll(0xf000, true, 46, BIT(0)); assert(!measured());
    reset(true, 2); pll(0xf000, true, 46, BIT(3)); assert(!measured());
    reset(false, 1); pll(0, false, 46, BIT(3) | BIT(12)); assert(!measured());
    reset(false, 3); pll(0x100, false, 29, BIT(3) | BIT(12)); assert(!measured());
    reset(true, 1); pll(0xf000, true, 46, BIT(3) | BIT(12)); assert(!measured());
    reset(true, 2); pll(0xf000, true, 46, BIT(0) | 0x200); assert(!measured());
    reset(false, 1); pll(0, false, 46, BIT(3) | BIT(24) | BIT(25));
    registers[8 / 4] = 0x100; assert(!measured());
    /* Perturb mux during each sample and PLL state between/during samples. */
    reset(false, 1); pll(0, false, 46, BIT(3)); assert(measured() == 883200000);
    boundary = reads / 2;
    total = reads;
    for (i = 2; i <= total; i++) {
        reset(false, 1); pll(0, false, 46, BIT(3));
        change_at = i; change_offset = 0x40; change_mask = 3;
        assert(!measured());
    }
    for (i = 4; i <= boundary + 3; i++) {
        reset(false, 1); pll(0, false, 46, BIT(3));
        change_at = i; change_offset = 4; change_mask = 1;
        assert(!measured());
    }
    reset(false, 1); pll(0, false, 46, BIT(3));
    change_at = boundary + 1; change_offset = 0x10; change_mask = BIT(3);
    assert(!measured());
    /* Every captured PLL field changing between samples must fail closed,
       for each selected bank and both main/early outputs. */
    for (i = 0; i < sizeof(sources) / sizeof(sources[0]); i++) {
        const u32 apcc_fields[] = {0, 4, 8, 0x10};
        const u32 alt_fields[] = {0, 4, 8, 0x0c, 0x10};
        const u32 cbf_fields[] = {0, 8, 0x10, 0x14, 0x18};
        const u32 *fields = sources[i].cbf ? cbf_fields :
                            sources[i].base ? alt_fields : apcc_fields;
        unsigned count = fields == apcc_fields ? 4 : 5;
        for (j = 0; j < count; j++) {
            reset(sources[i].cbf, sources[i].mux);
            pll(sources[i].base, sources[i].cbf, 46, sources[i].user);
            assert(measured()); boundary = reads / 2;
            reads = barriers = 0;
            change_at = boundary + 1;
            change_offset = sources[i].base + fields[j]; change_mask = 1;
            assert(!measured());
        }
    }
    reset(false, 1); pll(0, false, 46, BIT(3));
    assert(!msm8996_boot_recalc(&clock.hw, 0));
    /* The performance domain uses its separate +0x80000 hardware bank. */
    reset(false, 0);
    clock.base = (char *)registers + 0x80000;
    registers[0x80040 / 4] = 1;
    pll(0x80000, false, 46, BIT(3)); assert(measured() == 883200000);
    for (i = 0; i < 2; i++) {
        reset(i, 0); probe_cbf = i; registrations = providers = 0;
        aux_error = -EPROBE_DEFER;
        assert(msm8996_boot_probe(&pdev) == -EPROBE_DEFER);
        assert(!registrations && !providers && !allocation_count);
        aux_error = -EIO;
        assert(msm8996_boot_probe(&pdev) == -EIO);
        assert(!registrations && !providers && !allocation_count);
        aux_error = 0;
        assert(!msm8996_boot_probe(&pdev));
        assert(registrations == (i ? 1 : 2) && providers == 1);
        while (allocation_count) free(allocations[--allocation_count]);
    }
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--reference-commit", action="store_true",
                        help="also parse mux/PLL provenance from the saved Sony Git object")
    args = parser.parse_args()
    if args.reference_commit:
        def original(name):
            return subprocess.check_output(["git", "show", REFERENCE +
                                            ":drivers/clk/msm/" + name],
                                           cwd=ROOT, text=True)
        cpu, pll = original("clock-cpu-8996.c"), original("clock-pll.c")
        reference_topology(cpu, pll)
        changed = cpu.replace("{ &sys_apcsaux_clk.c, 3 }", "{ &sys_apcsaux_clk.c, 2 }", 1)
        try:
            reference_topology(changed, pll)
        except ValueError:
            pass
        else:
            raise ValueError("Sony topology checker accepted a changed AUX selector")
        print("CPU source provenance: original Sony mux/PLL/handoff/AUX topology PASS; mutation rejected")
    production = (HEADER.parent / "clk-cpu-8996.c").read_text()
    source_text = re.sub(r"/\*.*?\*/|//[^\n]*", "", production, flags=re.S)
    forbidden = (r"\b(?:writel(?:_relaxed)?|writeq(?:_relaxed)?|regmap_write|"
                 r"regmap_update_bits|clk_set_rate|clk_set_parent|regulator_set_voltage)\s*\("
                 r"|\.(?:set_rate|set_parent|set_rate_and_parent)\s*=")
    if re.search(forbidden, source_text):
        raise ValueError("observational APCC/CBF driver has a hardware mutation path")
    if args.config:
        config = args.config.read_text()
        for symbol in ("CPU_FREQ", "CPU_IDLE"):
            if f"# CONFIG_{symbol} is not set\n" not in config:
                raise ValueError(f"CONFIG_{symbol} must remain disabled")
        if "CONFIG_QCOM_MSM8996_BOOT_CLOCK=y\n" not in config:
            raise ValueError("observational clock provider is not built-in")
    with tempfile.TemporaryDirectory(prefix="dora-cpu-clocks-") as directory:
        source = Path(directory) / "test.c"
        binary = Path(directory) / "test"
        observer = production.split("static const struct of_device_id msm8996_boot_match", 1)[0]
        observer = re.sub(r"^#include[^\n]*\n", "", observer, flags=re.M)
        source.write_text(HARNESS.replace("int main(void)", "static int decoder_test(void)") +
                          OBSERVER_PREAMBLE + observer + OBSERVER_HARNESS)
        command = shlex.split(os.environ.get("HOSTCC", "cc"))
        subprocess.run(command + ["-std=gnu99", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(HEADER.parent), str(source),
                                 "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("CPU boot clocks: production MMIO read path, AUX/PLL sources, lock/output checks, "
          "unstable snapshots, signed alpha/MN/dividers, perf bank and probe defer checks PASS")


if __name__ == "__main__":
    main()
