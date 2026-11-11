#!/usr/bin/env python3
"""Exercise production SDHCI physical-RPM integration with typed API faults.

Reuse the frozen Stage4 regulator-core/cache model, enabling the new production
branches. Transport and provider lifecycle are host models; no device is used.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
legacy = ROOT / 'scripts/dora/check-mmc-power.py'
prefix = legacy.read_text().split("harness += r'''\nstatic void reset(void)")[0]
namespace = {'__file__': str(legacy)}
exec(compile(prefix, str(legacy), 'exec'), namespace)
source = namespace['SOURCE']
function = namespace['function']
base = namespace['harness']
# The real typed backend refuses generic writes after an uncertain ACK too.
# Keep the frozen core/cache model, replacing only its provider boundary.
base = base.replace('static int provider(struct regulator *r,char op,struct fault *fault)',
                    'static int typed_provider_error(struct regulator *r);\n'
                    'static int provider(struct regulator *r,char op,struct fault *fault)')
base = base.replace('return fault?fault->error:0;',
                    'return fault?fault->error:typed_provider_error(r);', 1)
split = base.index('static int sdhci_msm_vreg_fault(')
base = '#define CONFIG_REGULATOR_QCOM_RPM_PHYSICAL 1\n' + base[:split] + r'''
static bool typed[2], retired[2], protocol_quarantine[2];
static int ready_error[2];
static int typed_provider_error(struct regulator *r)
{
    if (!typed[r->id]) return 0;
    if (retired[r->id]) return -ENODEV;
    return protocol_quarantine[r->id] ? -EUCLEAN : 0;
}
static int qcom_rpm_physical_check_ready(struct regulator *r)
{
    assert(r && !IS_ERR(r) && r->handles);
    request(r, 'R', 0, 0);
    if (!typed[r->id]) return -EOPNOTSUPP;
    if (retired[r->id]) return -ENODEV;
    if (protocol_quarantine[r->id]) return -EUCLEAN;
    return ready_error[r->id];
}
static int qcom_rpm_physical_sync(struct regulator *r, bool enabled)
{
    struct fault *fault;
    int ret = qcom_rpm_physical_check_ready(r);
    if (ret) return ret;
    assert(enabled && data[r->id].is_enabled && r->refs);
    fault = request(r, 'S', enabled, 0);
    ret = provider(r, 'S', fault);
    if (!ret || (fault && fault->applied)) {
        r->physical_load = r->requested_load;
        r->physical_min_uV = r->min_uV;
        r->physical_max_uV = r->max_uV;
        r->physical_enabled = true;
    }
    if (ret) protocol_quarantine[r->id] = true;
    return ret;
}
''' + base[split:]
base += function('sdhci_msm_power_sync')

TESTS = r'''
static void reset(void)
{
    unsigned i;
    memset(rails, 0, sizeof(rails)); memset(data, 0, sizeof(data));
    memset(faults, 0, sizeof(faults)); memset(events, 0, sizeof(events));
    memset(retired, 0, sizeof(retired)); memset(ready_error, 0, sizeof(ready_error));
    memset(protocol_quarantine, 0, sizeof(protocol_quarantine));
    nr_faults = nr_events = 0;
    for (i = 0; i < 2; i++) {
        typed[i] = true; rails[i].id = i; rails[i].voltage_count = 1;
        data[i].name = i ? "vdd-io" : "vdd";
        data[i].low_vol_level = data[i].high_vol_level = i ? 1800000 : 2950000;
        data[i].hpm_uA = i ? 325000 : 570000; data[i].lpm_uA = i ? 110 : 200;
        data[i].require_physical_ack = true;
    }
    slot.vdd_data = &data[0]; slot.vdd_io_data = &data[1]; pdata.vreg_data = &slot;
}
static void initialize(void) { assert(!sdhci_msm_vreg_init(&dev, &pdata, true)); }
static void sticky(unsigned rail, int error)
{
    unsigned before = nr_events;
    assert(data[rail].power_error == error);
    assert(sdhci_msm_power_sync(&pdata) == error);
    assert(sdhci_msm_vreg_enable(&data[rail]) == error);
    assert(sdhci_msm_setup_vreg(&pdata, true, false) == error);
    assert(sdhci_msm_vreg_init(&dev, &pdata, true) == error);
    assert(nr_events == before);
}
int main(void)
{
    unsigned before, generic;
    const int errors[] = {-EOPNOTSUPP, -EPROBE_DEFER, -ENODEV, -EBUSY, -EUCLEAN};
    unsigned i;
    /* Readiness rejects a wrong, unpublished, retired, suspended or uncertain
     * provider without sending load, voltage, enable, or forced requests. */
    for (i = 0; i < ARRAY_SIZE(errors); i++) {
        reset(); ready_error[0] = errors[i];
        assert(sdhci_msm_vreg_init(&dev, &pdata, true) == errors[i]);
        assert(!data[0].reg && !rails[0].handles && !data[0].power_error);
        assert(!rails[0].count['L'] && !rails[0].count['V'] && !rails[0].count['E']);
        assert(!rails[0].count['S']);
        ready_error[0] = 0; initialize();
    }
    reset(); typed[0] = false;
    assert(sdhci_msm_vreg_init(&dev, &pdata, true) == -EOPNOTSUPP);
    assert(!rails[0].handles && !data[0].power_error);
    reset(); ready_error[1] = -EPROBE_DEFER;
    assert(sdhci_msm_vreg_init(&dev, &pdata, true) == -EPROBE_DEFER);
    assert(!rails[0].handles && !rails[1].handles && !data[0].reg && !data[1].reg);

    /* Initial disabled bootstrap has no owned ACK baseline. */
    reset(); initialize(); assert(!sdhci_msm_power_sync(&pdata));
    assert(!rails[0].count['S'] && !rails[1].count['S']);
    assert(!sdhci_msm_vreg_enable(&data[0]));
    assert(data[0].is_enabled && rails[0].refs == 1 && rails[0].count['S'] == 1);
    assert(events[nr_events - 1].op == 'S');
    assert(rails[0].physical_enabled && rails[0].physical_load == 570000);
    before = rails[0].count['S']; generic = rails[0].count['E'];
    data[0].is_always_on = true; data[0].lpm_sup = true;
    assert(!sdhci_msm_vreg_enable(&data[0]));
    assert(rails[0].refs == 1 && rails[0].count['E'] == generic);
    assert(rails[0].count['S'] == before + 1 && data[0].load_uA == 570000);
    assert(!sdhci_msm_vreg_disable(&data[0]));
    assert(data[0].is_enabled && rails[0].refs == 1 && data[0].load_uA == 200);
    assert(!sdhci_msm_vreg_enable(&data[0]) && data[0].load_uA == 570000);

    /* Identical generic voltage cache hits still force physical ACKs. */
    before = rails[0].count['S']; generic = rails[0].provider_count['V'];
    assert(!sdhci_msm_vreg_set_voltage(&data[0], 2950000, 2950000));
    assert(rails[0].provider_count['V'] == generic && rails[0].count['S'] == before + 1);
    before = rails[0].count['S']; assert(!sdhci_msm_power_sync(&pdata));
    assert(rails[0].count['S'] == before + 1);

    /* A successful generic enable owns its reference before forced ACK fails.
     * Explicit protocol recovery can permit cleanup, never host reuse. */
    reset(); initialize(); fail_next(0, 'S', -ETIMEDOUT, true);
    assert(sdhci_msm_vreg_enable(&data[0]) == -ETIMEDOUT);
    assert(data[0].is_enabled && rails[0].refs == 1 && protocol_quarantine[0]);
    assert(rails[0].physical_enabled); sticky(0, -ETIMEDOUT);
    protocol_quarantine[0] = false; nr_faults = 0; /* Explicit backend recovery model. */
    sticky(0, -ETIMEDOUT);
    assert(sdhci_msm_vreg_deinit_reg(&data[0]) == -ETIMEDOUT);
    assert(!data[0].reg && !rails[0].handles && !rails[0].refs);
    sticky(0, -ETIMEDOUT);

    /* A lost post-cache ACK during voltage/load changes permanently blocks
     * later clock barriers, even when the generic operation was successful. */
    reset(); initialize(); assert(!sdhci_msm_vreg_enable(&data[0]));
    fail_next(0, 'S', -EREMOTEIO, true);
    assert(sdhci_msm_vreg_set_voltage(&data[0], 2950000, 2950000) == -EREMOTEIO);
    assert(data[0].is_enabled && rails[0].refs == 1); sticky(0, -EREMOTEIO);
    reset(); initialize(); assert(!sdhci_msm_vreg_enable(&data[0]));
    fail_next(0, 'S', -EIO, true);
    assert(sdhci_msm_vreg_set_optimum_mode(&data[0], 200) == -EIO);
    assert(data[0].load_uA == 200 && rails[0].refs == 1); sticky(0, -EIO);

    /* Provider retirement after publication preserves an owned reference. */
    reset(); initialize(); assert(!sdhci_msm_vreg_enable(&data[0]));
    retired[0] = true;
    assert(sdhci_msm_vreg_init_reg(&dev, &data[0]) == -ENODEV);
    assert(data[0].reg && rails[0].handles == 1 && rails[0].refs == 1);
    sticky(0, -ENODEV);
    reset(); initialize(); retired[1] = true;
    assert(sdhci_msm_power_sync(&pdata) == -ENODEV); sticky(1, -ENODEV);

    /* A quarantined second rail refuses rollback. Keep its live reference
     * and constraints, while the healthy first rail unwinds normally. */
    reset(); initialize(); fail_next(1, 'S', -EIO, true);
    assert(sdhci_msm_setup_vreg(&pdata, true, false) == -EIO);
    assert(!data[0].is_enabled && !rails[0].refs);
    assert(data[1].is_enabled && rails[1].refs == 1);
    assert(rails[1].physical_enabled && protocol_quarantine[1]);
    assert(rails[1].min_uV == 1800000 && rails[1].requested_load == 325000);
    sticky(1, -EIO);
    puts("PASS: production MMC typed readiness, cache-bypass ACKs, ownership, retirement and permanent host quarantine");
    return 0;
}
'''


def run(code, expect_failure=False):
    with tempfile.TemporaryDirectory(prefix='dora-mmc-physical-') as scratch:
        c, binary = Path(scratch) / 'mmc.c', Path(scratch) / 'mmc'
        c.write_text(code + TESTS)
        flags = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wno-unused-parameter', '-Wno-unused-function', '-Wno-sign-compare',
                 '-fsanitize=' + os.environ.get('DORA_SANITIZERS', 'undefined'),
                 '-fno-sanitize-recover=all']
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + flags +
                       shlex.split(os.environ.get('HOSTCFLAGS', '')) +
                       [str(c), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], check=False, capture_output=expect_failure,
                                text=True, timeout=15)
        assert bool(result.returncode) == expect_failure, result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    probe = function('sdhci_msm_probe')
    assert probe.index('sdhci_msm_vreg_init') < probe.index('msm_bus_scale_client_init')
    assert probe.index('sdhci_msm_vreg_init') < probe.index('clk_set_rate')
    for name in ('sdhci_msm_prepare_clocks', 'sdhci_msm_enable_controller_clock'):
        body = function(name)
        assert body.index('sdhci_msm_power_sync') < body.index('sdhci_msm_bus_voting')
    assert 'sony,require-rpm-physical-acks' in function('sdhci_msm_parse_regulator_info')
    assert 'sony,require-rpm-physical-acks' in function('sdhci_msm_dt_parse_vreg_info')
    run(base)
    if args.self_test:
        for before, after in (
            ('ret = qcom_rpm_physical_check_ready(vreg->reg);', 'ret = 0;'),
            ('ret = qcom_rpm_physical_sync(vreg->reg, true);', 'ret = 0;'),
            ('vreg->is_enabled = true;', 'vreg->is_enabled = false;'),
        ):
            assert before in base
            run(base.replace(before, after), expect_failure=True)
        print('PASS: rejected readiness, forced-ACK and enable-ownership mutations')
    print('Host API/cache model only; actual firmware, clock/IRQ scheduling and SDHC I/O remain untested.')


if __name__ == '__main__':
    main()
