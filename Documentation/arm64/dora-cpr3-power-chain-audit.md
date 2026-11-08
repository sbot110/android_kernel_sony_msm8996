# Dora MSM8996 Linux 5.15: CPU Power and Frequency Chain Audit

Status: **SOURCE-READY & BUILDABLE (CPR3 disabled in DT and CPUFreq fail-closed pending on-device verification)**.
Scope: Sony Xperia X Performance (`F8131` single-SIM / `F8132` dual-SIM, branch `kernel-5.15`).

## 1. Reconciled Power & Clock Architecture

1. **SAW3 / SPM Voltage & AVS Backend (`drivers/soc/qcom/spm.c`, `include/soc/qcom/spm.h`, `drivers/regulator/qcom_spmi-regulator.c`)**
   - **Exclusive Single-Writer Ownership:** `qcom-spm` claims exclusive MMIO ownership of `saw3: power-controller@9a10000` (`0x09a10000`, `system-cbf`) via `devm_platform_ioremap_resource()`. The competing `saw_regmap` / `spmi_saw_set_vdd()` fallback writer has been completely removed from `drivers/regulator/qcom_spmi-regulator.c`. Both `pm8994_s11` (`qcom,saw-leader`) and CPR3 route all voltage/AVS operations through `msm_spm_*` under a single mutex and state machine.
   - **Cluster Binding Verification:** `saw3: power-controller@9a10000` specifies `qcom,cpu-vctl-list = <&CPU0 &CPU1 &CPU2 &CPU3>` (matching Sony 4.4 `msm8996-pm.dtsi`), binding both the Silver (`CPU0`, `CPU1`) and Gold (`CPU2`, `CPU3`) Kryo clusters to the shared CBF/APCC SAW3 controller. `qcom_spm_probe()` verifies that every possible CPU is present in `qcom,cpu-vctl-list`, checks `SAW3_VERSION` (`v3.0` / `v2.3`), verifies `SAW3_ID` bit 2 (`SAW3_ID_PMIC_ARB_INTF_MASK`), and enforces `qcom,vctl-port = <0>`.
   - **Verified PMIC FSM & Voltage Confirmation (`0x30000` + `0xFF`):** Matches Sony/Qualcomm 4.4 `msm-spm.c` (`msm_spm_drv_set_vdd`): writes `SAW3_RST = 1`, updates `SAW3_VCTL` and `SAW3_SPM_PMIC_DATA_3`, executes `mb()`, and polls `SAW3_PMIC_STS` (`0x14`) with `udelay(1)` before each read for up to `500` us until both the PMIC FSM state bits `[17:16]` (`0x30000`) equal `0` (`IDLE`) and the acknowledged voltage byte `[7:0]` (`0xFF`) equals `vlevel`.
   - **Fail-Closed AVS Handling:** If any register write or `SAW3_PMIC_STS` acknowledgement fails after clearing `SAW3_AVS_CTL_TGGL_MASK`, the driver does **not** re-enable AVS with unconfirmed rail state or stale limits; it keeps AVS disabled, latches `state_uncertain = true`, and rejects all subsequent votes with `-EIO`.

2. **Array Power Mux (`drivers/soc/qcom/apm.c`, `include/linux/power/qcom/apm.h`)**
   - Ported the MSM8996 / MSM8996Pro APM controller driver to Linux 5.15 (`CONFIG_MSM_APM=y`, `arch_initcall`).
   - Preserves the Sony 4.4 SMCCC secure clock source override (`0xC4000020`), HMSS v1.0/v1.1 GPLL0 clock muxing and SPM event sequencing, and APCC/MX rail switching with hardware status verification.
   - Wired `apc_apm: apm@99e0000` in `arch/arm64/boot/dts/qcom/msm8996.dtsi`.

3. **Memory Accelerator Regulators (`drivers/regulator/mem-acc-regulator.c`)**
   - Ported the Qualcomm MEM-ACC regulator driver to the Linux 5.15 regulator framework (`CONFIG_REGULATOR_MEM_ACC=y`, `postcore_initcall`).
   - Wired `apc0_pwrcl_mem_acc_vreg`, `apc1_perfcl_mem_acc_vreg`, and `apcc_l3_mem_acc_vreg` at `0x099e00c0` in `msm8996.dtsi`.

4. **Kryo Cluster LDO/BHS Regulators (`drivers/regulator/kryo-regulator.c`, `include/linux/regulator/msm-ldo-regulator.h`)**
   - Ported the Kryo cluster LDO/BHS and retention regulator driver (`CONFIG_REGULATOR_KRYO=y`, `arch_initcall`).
   - Replaced the legacy `BUG_ON(1)` on LPM entry/exit counter mismatch in `kryo_regulator_lpm_resume()` with spinlock-protected counter resynchronization and `NOTIFY_BAD` error return.
   - Wired `kryo0_vreg` (`0x099a2000`) and `kryo1_vreg` (`0x099d2000`) along with `kryo0_retention_vreg` and `kryo1_retention_vreg` in `msm8996.dtsi`.

5. **CPR3 Core & HMSS Regulator (`drivers/regulator/cpr3-regulator.c`, `drivers/regulator/cpr3-util.c`, `drivers/regulator/cpr3-hmss-regulator.c`)**
   - Adapted CPR3 regulator operations, CPU hotplug state machine (`cpuhp_setup_state_multi`), and panic notifier headers for Linux 5.15 (`CONFIG_REGULATOR_CPR3=y`, `CONFIG_REGULATOR_CPR3_HMSS=y`, `subsys_initcall`).
   - Removed the obsolete `CONFIG_REGULATOR_QCOM_CPR3_HMSS_PORT` (`depends on BROKEN`) target from `drivers/regulator/Kconfig` and `drivers/regulator/Makefile`.
   - Removed `BUG_ON(1)` and `BUG()` in `cpr3-util.c` when hardware fuse combo or speed bin exceeds DT limits, returning `-EINVAL` cleanly.
   - Populated the complete `apcc_cpr: cpr3-ctrl@99e8000` Device Tree graph in `msm8996.dtsi` and `msm8996-sony-xperia-tone.dtsi` while keeping `status = "disabled"` and preserving the `qcom-cpufreq-nvmem.c` fail-closed guard (`-ENODEV`) until on-device telemetry confirms fuse corners on `F8131` and `F8132`.

## 2. Deterministic Initialization Order (`initcall` Sequence)

| Level | Macro | Driver | Role |
| --- | --- | --- | --- |
| 2 | `postcore_initcall` | `qcom-spm` (`drivers/soc/qcom/spm.c`) | Claims `0x09a10000` SAW3 MMIO & binds `CPU0..CPU3` |
| 2 | `postcore_initcall` | `mem-acc-regulator` (`drivers/regulator/mem-acc-regulator.c`) | Registers `apc0`, `apc1`, and `apcc_l3` MEM-ACC corner regulators |
| 3 | `arch_initcall` | `msm-apm` (`drivers/soc/qcom/apm.c`) | Registers `apc_apm` (`0x099e0000`) controller |
| 3 | `arch_initcall` | `kryo-regulator` (`drivers/regulator/kryo-regulator.c`) | Registers Silver/Gold Kryo LDO/BHS & retention regulators |
| 4 | `subsys_initcall` | `cpr3-hmss-regulator` (`drivers/regulator/cpr3-hmss-regulator.c`) | Registers HMSS CPR3 controller driver (defers until `pm8994_s11` is ready) |
| 6 | `device_initcall` | `qcom-spmi-regulator` (`drivers/regulator/qcom_spmi-regulator.c`) | Registers `pm8994_s11` (`vdd_apcc`) delegating SAW3 writes to `qcom-spm` |
| 6 | `device_initcall` | `qcom-cpufreq-nvmem` (`drivers/cpufreq/qcom-cpufreq-nvmem.c`) | Intentionally fail-closed (`-ENODEV`) until CPR3 hardware verification |

## 3. On-Device Hardware Verification Gates (Before Enabling CPR3 & CPUFreq)

1. **Stage 1 (`BOOT-TEST` with `apcc_cpr` disabled):**
   - Boot with `dora_defconfig` + `dora_diag.config` (`pstore`/`ramoops` at `0xa7f00000`).
   - Verify `qcom-spm: Qualcomm SAW3 v1.0 voltage backend probed for 0-3 (timeout 500 us)` in `dmesg`.
   - Confirm `vdd_apcc` (`pm8994_s11`) readback through `msm_spm_get_vdd(0)` matches bootloader boot voltage.
2. **Stage 2 (`CPR3-OPEN-LOOP` / `CPR3-CLOSED-LOOP` validation):**
   - Enable `apcc_cpr` (`status = "okay"`) in a test DTB with `qcom-cpufreq-nvmem` still fail-closed.
   - Inspect `/sys/kernel/debug/cpr3-regulator/` to verify decoded QFPROM fuse corners, speed bin, open-loop voltages, and quotients against Sony 4.4 `dmesg` from the same physical `F8131`/`F8132` unit.
3. **Stage 3 (`CPUFREQ-READY`):**
   - Only after Stage 2 passes on hardware, lift the `if (1)` guard in `drivers/cpufreq/qcom-cpufreq-nvmem.c` and test stepwise OPP transitions across both Silver and Gold clusters.
