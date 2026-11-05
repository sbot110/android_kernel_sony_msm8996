# 11. Stage G Power, Regulator, Clock, Watchdog & Early-Boot Hardware Audit

## 1. Executive Summary

Stage G audited all remaining early-boot, PMIC regulator, Kryo CPU clock/OPP, reserved-memory, USB2 PHY, and battery/charger/thermal integration paths on **Linux 5.15.220** (`kernel-5.5`) for **Sony Xperia X Performance (`dora`: `F8131` Single-SIM / `F8132` Dual-SIM, Qualcomm MSM8996)**.

All findings below were cross-checked directly against Sony's stock `4.4.302` kernel (`0c295b15b6d0`), `sbot110/android_device_sony_dora`, `sbot110/android_device_sony_tone-common`, and `sbot110/android_vendor_sony`.

---

## 2. Critical P0 Early-Boot & Regulator Fixes

### 2.1 PM8994 vs PMI8994 APCC SAW Regulator Misplacement (`tone.dtsi`)
- **Defect found:** `qcom,saw-reg = <&saw3>` and gang-phased bucks `s9`, `s10`, `s11` (`vdd_apcc`, `470000..1140000 uV`) were declared under `&pmi8994_spmi_regulators` (SPMI USID 3, `qcom,pmi8994-regulators`, which only implements `s1..s3, l1`) instead of `&pm8994_spmi_regulators` (SPMI USID 1, `qcom,pm8994-regulators`, `s9@2c00`, `s10@2f00`, `s11@3200`).
- **Resolution:** Moved `saw3`, `pm8994_s9`, `pm8994_s10`, and `vdd_apcc: pm8994_s11` (`470–1140 mV`, `vdd_s9/s10/s11-supply = <&vph_pwr>`) to `&pm8994_spmi_regulators`, and added `vdd_s2-supply = <&vph_pwr>` on `&pmi8994_spmi_regulators` for `vdd_gfx` (`pmi8994_s2`, `1015 mV`).

### 2.2 Kryo APCC Clock Driver (`drivers/clk/qcom/clk-cpu-8996.c`) & Speedbin 3 OPPs (`msm8996.dtsi`)
- **Defects found:**
  1. In `qcom_cpu_clk_msm8996_acd_init()`, `(0x103 == (hwid | 0x103))` evaluated `true` on CPU0 (`hwid = 0`), causing CPU0 to program `PERFCL_REG_OFFSET + SSSCTL_OFFSET` while cluster 1 L2 was not initialized, and `PWRCL` wrote `SSSCTL_OFFSET` before `L2ACDCR_REG`.
  2. `pwrcl_pll_acd` and `perfcl_pll_acd` were referenced as `pmux` parents without being registered in Common Clock Framework, leaving orphan parent entries.
  3. Primary Kryo HFPLLs used `clk_alpha_pll_huayra_ops` (`l = 60`) instead of `clk_alpha_pll_hwfsm_ops` (`l = 54`, `1036.8 MHz`, v3 `config_ctl_val = 0x200d4828`, `test_ctl_val = 0x1c000000`) and lacked `AUTO_CLK_SEL` (`MUX_OFFSET 0x40` `0x3 << 4`, `CLK_CTL_OFFSET 0x44` `BIT(8)`) and `PSCTL_OFFSET` (`0x164` = `0x00030005`).
  4. `cluster0_opp` and `cluster1_opp` in `msm8996.dtsi` only enabled speedbins `0..2` (`0x1..0x7`), omitting `BIT(3)` (`0x8`) used on retail Snapdragon 820 (MSM8996 v3.0/v3.1) silicon.
- **Resolution:** Fixed MPIDR cluster affinity check (`(read_cpuid_mpidr() & 0xf00) == 0x000` vs `0x100`), programmed `L2ACDCR_REG` (`0x002c5ffd`) prior to `SSSCTL_OFFSET` (`0xf`) with `L2ACDTD_REG == 0x00006a11` idempotence check, registered `pwrcl_pll_acd` and `perfcl_pll_acd`, switched primary HFPLLs to `clk_alpha_pll_hwfsm_ops`, configured `AUTO_CLK_SEL` + `PSCTL`, guarded `DIV_2_THRESHOLD` (`600 MHz`) rate switches via `SMUX_XO_INDEX` / `ALT_INDEX` + `ABORT_RATE_CHANGE` recovery, and enabled `BIT(3)` (`0x8`) across `cluster0_opp` (`307.2 MHz..1.59 GHz`) and `cluster1_opp` (`307.2 MHz..2.15 GHz`).

### 2.3 Non-Overlapping `/reserved-memory` & Adreno 530 `a530_zap.mdt`
- **Defects found:** `msm8996.dtsi` had `zap_shader_region` (`0x90b00000`, `0xa00000`) 100% overlapping `slpi_region` (`0x90b00000`, `0xa00000`) and a unit-address warning on `rmtfs@86700000`. In `dora-common.dtsi`, `&gpu` requested `"qcom/msm8996/a530_zap.mdt"` while Sony's `/oem` (`/vendor/firmware/`) stores `"a530_zap.mdt"` directly at root (verified ELF32 span `0x1324 < 0x2000`).
- **Resolution:** Fixed `msm8996.dtsi` Carveouts so every region is strictly disjoint; verified Dora's final DTB `/reserved-memory` layout (`cont_splash_mem` `0x83401000`, `0x85800000`, `smem` `0x86000000`, `hyp` `0x86200000`, `mpss` `0x88800000`, `adsp` `0x8ea00000`, `zap_shader` `0x90400000` `8 KiB`, `slpi` `0x90500000` `10 MiB`, `venus` `0x90f00000` `5 MiB`, `mba` `0x91500000` `2 MiB`, `ramoops` `0xa7f00000` `1 MiB`), and updated `zap-shader` `firmware-name = "a530_zap.mdt"`.

### 2.4 Hardware APSS Watchdog (`watchdog@9830000`) & QUSB2 Core `vdd` Supply
- Added `watchdog@9830000` (`"qcom,apss-wdt-msm8996", "qcom,kpss-wdt"`, `0x09830000`, `GIC_SPI 28`, `<&sleep_clk>`) and enabled `CONFIG_QCOM_WDT=y` so hard hangs reset cleanly instead of wedging with an unlogged black screen, allowing `ramoops@a7f00000` (`pstore`) capture on next boot.
- Added optional `vdd` core supply (`pm8994_l28`, `0.925 V`) support to `drivers/phy/qualcomm/phy-qcom-qusb2.c` and wired `vdd-supply = <&pm8994_l28>` on `&hsusb_phy1` so ADB/USB2 enumeration never loses its digital core LDO when unused regulators are disabled in late init.

---

## 3. Real PMI8994 / PMI8996 Fuel Gauge, Charger & Thermal Safety Architecture

- Implemented `drivers/power/supply/qcom_pmi8994_fg_chg.c` (`CONFIG_CHARGER_QCOM_PMI8994=y`) bound to real SPMI USID 2 peripherals (`chgr@1000`, `bat-if@1200`, `usb-chgpth@1300`, `chgr-misc@1600`, `fg-soc@4000`, `fg-batt@4100`, `fg-memif@4400`) and PMI8994 SPMI VADC (`adc@3100`, `VADC_USBIN` `1:20` prescaling).
- Runtime PMIC REVID subtype check (`0x0105`: `10` = `PMI8994` RIF arbiter vs `19` = `PMI8996` IMA arbiter) selects the exact hardware SRAM access handshake (`{0x40,0x41,0x42,0x4c}` vs `{0x50,0x51,0x61,0x67}`).
- Reads real `MONOTONIC_SOC` (`0x4009`), `VBAT` (`0x5cc+1`), `IBAT` (`0x5cc+3` burst), `BATT_TEMP` (`0x550+2`), `OCV` (`0x588+3`), `CYCLE_COUNT` (`0x5e8+0`), `APSD_DG_STS` (`SDP`/`CDP`/`DCP`), `ICL_STS_1`, and `VBUS` (`usbin` IIO channel).
- **Strict Hardware Safety Preservation:** The driver is **100% non-destructive / read-only** with respect to charging thresholds. It never raises or overwrites `FV_CFG` (`4.35 V` max for Sony Dora `LIP1621ERPC`), `FCC_CFG`, AICL limits, hardware JEITA cold/cool/warm/hot comparators (`bat-if@1200`), or `TEMP_SHUTDOWN` (`chgr-misc@1600`). Hardware/S1 OTP autonomous protection remains authoritative at all times.
