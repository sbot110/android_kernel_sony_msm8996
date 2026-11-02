#!/usr/bin/env python3
"""Compile and run the actual MSM8996 PLL decoder against hardware encodings."""
import argparse
import os
import re
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "drivers/clk/qcom/clk-msm8996-boot.h"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    source_text = (HEADER.parent / "clk-cpu-8996.c").read_text()
    source_text = re.sub(r"/\*.*?\*/|//[^\n]*", "", source_text, flags=re.S)
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
        source.write_text(HARNESS)
        command = shlex.split(os.environ.get("HOSTCC", "cc"))
        subprocess.run(command + ["-std=gnu99", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(HEADER.parent), str(source),
                                 "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("CPU boot-clock decoder: original-rate, signed alpha, M/N errors and divider checks PASS")


if __name__ == "__main__":
    main()
