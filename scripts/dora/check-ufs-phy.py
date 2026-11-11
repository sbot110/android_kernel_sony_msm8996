#!/usr/bin/env python3
"""Check original MSM8996 UFS data and execute its real calibration selector.

This host MMIO model verifies revision/rate selection, not storage hardware.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
directory = root / 'drivers/phy/qualcomm'
header = (directory / 'phy-qcom-ufs-qmp-14nm.h').read_bytes()
assert hashlib.sha256(header).hexdigest() == '3829c8be1322b07099eee7ae313c68af28557388b51ff2c71c57650248435e02', 'Sony calibration data changed'


def function(source, name):
    start = source.index(name + '(')
    start = source.rfind('static', 0, start) if name.startswith('ufs_qcom_phy_qmp') else source.rfind('\n', 0, start) + 1
    brace = source.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


common = (directory / 'phy-qcom-ufs.c').read_text()
driver = (directory / 'phy-qcom-ufs-qmp-14nm.c').read_text()
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define BIT(n) (1U << (n))
#define UFS_QCOM_PHY_QUIRK_HIBERN8_EXIT_AFTER_PHY_PWR_COLLAPSE BIT(0)
#define UFS_QCOM_PHY_QUIRK_SVS_MODE BIT(1)
#define UFS_QCOM_PHY_QUIRK_VCO_MANUAL_TUNING BIT(2)
#define dev_err(...) ((void)0)
#define mb() ((void)0)
struct ufs_qcom_phy { void *dev; unsigned char *mmio; unsigned quirks;
    u8 host_ctrl_rev_major; u16 host_ctrl_rev_minor, host_ctrl_rev_step;
    u32 vco_tune1_mode1; };
struct ufs_qcom_phy_calibration { u32 reg_offset, cfg_value; };
#define UFS_QCOM_PHY_CAL_ENTRY(reg, val) { .reg_offset = reg, .cfg_value = val }
static u32 registers[0xda8 / 4], expected[0xda8 / 4];
static unsigned writes;
static void writel_relaxed(u32 value, void *address)
{
    ptrdiff_t offset = (unsigned char *)address - (unsigned char *)registers;
    assert(offset >= 0 && offset % 4 == 0 && (size_t)offset < sizeof(registers));
    *(u32 *)address = value; writes++;
}
'''
harness += header.decode().replace('#include "phy-qcom-ufs-i.h"', '')
for name in ('ufs_qcom_phy_write_tbl', 'ufs_qcom_phy_calibrate'):
    harness += function(common, name)
for name in ('ufs_qcom_phy_qmp_14nm_phy_calibrate', 'ufs_qcom_phy_qmp_14nm_advertise_quirks'):
    harness += function(driver, name)
harness += r'''
static void expect_table(struct ufs_qcom_phy_calibration *table, unsigned count)
{
    unsigned i;
    for (i = 0; i < count; i++) expected[table[i].reg_offset / 4] = table[i].cfg_value;
}
int main(void)
{
    unsigned minor, rate;
    struct ufs_qcom_phy phy = { .mmio = (unsigned char *)registers,
        .host_ctrl_rev_major = 2, .vco_tune1_mode1 = 0x5a };
    assert(ARRAY_SIZE(phy_svs_mode_config_2_0_0) == 2);
    for (minor = 0; minor <= 2; minor++) for (rate = 0; rate <= 1; rate++) {
        memset(registers, 0xaa, sizeof(registers));
        memcpy(expected, registers, sizeof(expected)); writes = 0;
        phy.host_ctrl_rev_minor = minor; phy.quirks = 0;
        ufs_qcom_phy_qmp_14nm_advertise_quirks(&phy);
        assert((phy.quirks != 0) == (minor == 0));
        switch (minor) {
        case 0: expect_table(phy_cal_table_rate_A_2_0_0, ARRAY_SIZE(phy_cal_table_rate_A_2_0_0)); break;
        case 1: expect_table(phy_cal_table_rate_A_2_1_0, ARRAY_SIZE(phy_cal_table_rate_A_2_1_0)); break;
        case 2: expect_table(phy_cal_table_rate_A_2_2_0, ARRAY_SIZE(phy_cal_table_rate_A_2_2_0)); break;
        }
        if (rate) {
            if (minor == 2) expect_table(phy_cal_table_rate_B_2_2_0, ARRAY_SIZE(phy_cal_table_rate_B_2_2_0));
            else expect_table(phy_cal_table_rate_B, ARRAY_SIZE(phy_cal_table_rate_B));
        }
        if (minor == 0) expected[QSERDES_COM_VCO_TUNE1_MODE1 / 4] = phy.vco_tune1_mode1;
        assert(ufs_qcom_phy_qmp_14nm_phy_calibrate(&phy, rate, false) == 0);
        assert(writes && !memcmp(expected, registers, sizeof(expected)));
    }
    writes = 0;
    assert(ufs_qcom_phy_qmp_14nm_phy_calibrate(&phy, true, true) == -EOPNOTSUPP);
    assert(!writes);
    phy.host_ctrl_rev_minor = 3;
    assert(ufs_qcom_phy_qmp_14nm_phy_calibrate(&phy, false, false) == -ENODEV);
    assert(!writes);
    phy.host_ctrl_rev_minor = 1; phy.host_ctrl_rev_step = 1;
    assert(ufs_qcom_phy_qmp_14nm_phy_calibrate(&phy, false, false) == -ENODEV);
    assert(!writes);
    puts("PASS: real UFS14nm selector, six revision/rate combinations, VCO quirk, G4/unknown rejection");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-ufs-phy-') as scratch:
    source, binary = Path(scratch) / 'ufs.c', Path(scratch) / 'ufs'
    source.write_text(harness)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu99', '-Wall', '-Wextra',
                    '-Werror', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('PASS: unchanged Sony14nm header SHA256; physical UFS/ICE/data integrity untested')
