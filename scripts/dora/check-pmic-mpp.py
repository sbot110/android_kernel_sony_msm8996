#!/usr/bin/env python3
"""Exercise the actual native MPP pinconf code with a bounded register model.

This checks that ADC inputs clear stale output state. It is not an electrical
measurement or a substitute for PMIC/charger tests on F8131 and F8132.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / "drivers/pinctrl/qcom/pinctrl-spmi-mpp.c").read_text()


def function(name):
    match = re.search(r"^static int " + name + r"\(", source, re.M)
    if not match:
        raise ValueError("missing native function: " + name)
    brace = source.index("{", match.start())
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint16_t u16;
enum { PIN_CONFIG_BIAS_DISABLE, PIN_CONFIG_BIAS_PULL_UP,
 PIN_CONFIG_BIAS_HIGH_IMPEDANCE, PIN_CONFIG_POWER_SOURCE,
 PIN_CONFIG_INPUT_ENABLE, PIN_CONFIG_OUTPUT_ENABLE, PIN_CONFIG_OUTPUT,
 PIN_CONFIG_DRIVE_STRENGTH, PIN_CONFIG_END = 100 };
static unsigned pinconf_to_config_param(unsigned long c) { return c & 0xffff; }
static unsigned pinconf_to_config_argument(unsigned long c) { return c >> 16; }
static unsigned long pinconf_to_config_packed(unsigned p, unsigned a)
{ return p | ((unsigned long)a << 16); }
'''
harness += "\n".join(re.findall(r"^#define PMIC_MPP_.*$", source, re.M)) + "\n"
start = source.index("struct pmic_mpp_pad {")
harness += source[start:source.index("};", start) + 2] + "\n"
harness += r'''
struct pinctrl_pin_desc { void *drv_data; };
struct pinctrl_desc { struct pinctrl_pin_desc *pins; };
struct pmic_mpp_state { unsigned registers[0x100]; int fail_addr; };
struct pinctrl_dev { struct pinctrl_desc *desc; struct pmic_mpp_state *data; };
static struct pmic_mpp_state *pinctrl_dev_get_drvdata(struct pinctrl_dev *d)
{ return d->data; }
static int pmic_mpp_write(struct pmic_mpp_state *state,
                         struct pmic_mpp_pad *pad, unsigned addr, unsigned val)
{
    (void)pad; assert(addr < 0x100);
    if ((int)addr == state->fail_addr) return -EIO;
    state->registers[addr] = val;
    return 0;
}
'''
for name in ("pmic_mpp_write_mode_ctl", "pmic_mpp_config_get", "pmic_mpp_config_set"):
    harness += function(name)
harness += r'''
int main(void)
{
    struct pmic_mpp_state state = { .fail_addr = -1 };
    struct pmic_mpp_pad pad = { .num_sources = 4, .has_pullup = true,
        .function = PMIC_MPP_ANALOG, .output_enabled = true };
    struct pinctrl_pin_desc pin = { .drv_data = &pad };
    struct pinctrl_desc desc = { .pins = &pin };
    struct pinctrl_dev dev = { .desc = &desc, .data = &state };
    unsigned long config[] = {
        pinconf_to_config_packed(PIN_CONFIG_INPUT_ENABLE, 1),
        pinconf_to_config_packed(PIN_CONFIG_OUTPUT_ENABLE, 0),
        pinconf_to_config_packed(PMIC_MPP_CONF_AMUX_ROUTE, PMIC_MPP_AMUX_ROUTE_CH6)
    };
    unsigned long value;
    assert(pmic_mpp_config_set(&dev, 0, config, 3) == 0);
    assert(pad.input_enabled && !pad.output_enabled && pad.is_enabled);
    assert((state.registers[PMIC_MPP_REG_MODE_CTL] >> PMIC_MPP_REG_MODE_DIR_SHIFT)
           == PMIC_MPP_MODE_ANALOG_INPUT);
    assert(state.registers[PMIC_MPP_REG_AIN_CTL] == PMIC_MPP_AMUX_ROUTE_CH6);
    value = pinconf_to_config_packed(PIN_CONFIG_OUTPUT_ENABLE, 1);
    assert(pmic_mpp_config_get(&dev, 0, &value) == 0);
    assert(pinconf_to_config_argument(value) == 0);

    /* Retain explicit bidirectional mode when a client requests both. */
    config[0] = pinconf_to_config_packed(PIN_CONFIG_OUTPUT_ENABLE, 1);
    assert(pmic_mpp_config_set(&dev, 0, config, 1) == 0);
    assert((state.registers[PMIC_MPP_REG_MODE_CTL] >> PMIC_MPP_REG_MODE_DIR_SHIFT)
           == PMIC_MPP_MODE_ANALOG_BIDIR);
    value = pinconf_to_config_packed(PIN_CONFIG_OUTPUT_ENABLE, 0);
    assert(pmic_mpp_config_get(&dev, 0, &value) == 0);
    assert(pinconf_to_config_argument(value) == 1);

    /* Disabled NC pins stay high impedance rather than driving an output. */
    config[0] = pinconf_to_config_packed(PIN_CONFIG_BIAS_HIGH_IMPEDANCE, 0);
    assert(pmic_mpp_config_set(&dev, 0, config, 1) == 0);
    assert(!pad.is_enabled && !state.registers[PMIC_MPP_REG_EN_CTL]);

    /* MMIO failures propagate; master enable must not be written after them. */
    memset(state.registers, 0, sizeof(state.registers));
    state.fail_addr = PMIC_MPP_REG_MODE_CTL;
    config[0] = pinconf_to_config_packed(PIN_CONFIG_OUTPUT_ENABLE, 0);
    assert(pmic_mpp_config_set(&dev, 0, config, 1) == -EIO);
    assert(!state.registers[PMIC_MPP_REG_EN_CTL]);
    puts("PASS: real MPP pinconf clears stale outputs for ADC input, preserves explicit bidirectionality/high-Z and propagates I/O errors");
    return 0;
}
'''
# The route constants are the actual shared binding values, not model choices.
header = (ROOT / "include/dt-bindings/pinctrl/qcom,pmic-mpp.h").read_text()
defines = "\n".join(re.findall(r"^#define PMIC_MPP_AMUX_ROUTE_.*$", header, re.M))
harness = harness.replace("struct pinctrl_pin_desc {", defines + "\nstruct pinctrl_pin_desc {")
with tempfile.TemporaryDirectory(prefix="dora-mpp-pinconf-") as scratch:
    c_file, binary = Path(scratch) / "mpp.c", Path(scratch) / "mpp"
    c_file.write_text(harness)
    subprocess.run([os.environ.get("HOSTCC", "cc"), "-std=c99", "-Wall", "-Wextra",
                    "-Werror", str(c_file), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("Host register model only; PMIC electrical behavior remains untested")
