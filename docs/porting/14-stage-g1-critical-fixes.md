# 14. Stage G.1 Critical Fixes: Power Management, Clock Controller & Hardware Safety

## Overview
Stage G.1 resolves post-publication hardware integration blockers identified after Stage G.
All modifications are targeted, verified against Sony 4.4.302 (`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`), and enforced under `-Werror` compliance.

---

## 1. PMI8994 / PMI8996 Fuel Gauge & Charger Fixes (`qcom_pmi8994_fg_chg.c`)

### 1.1 Disentanglement of Battery Charging States
- **The Issue:** `CHGR_RT_CHG_INHIBIT` was erroneously treated as `POWER_SUPPLY_STATUS_FULL`. Charge inhibit occurs when battery voltage is within ~50 mV of float voltage or temperature thresholds restrict charging; it does not indicate termination.
- **The Fix:**
  - Status logic now strictly differentiates:
    1. `TCC_REACHED` or `FULL_SOC`: reports `POWER_SUPPLY_STATUS_FULL`.
    2. `CHGR_RT_CHG_INHIBIT`: reports `POWER_SUPPLY_STATUS_NOT_CHARGING`.
    3. `CHG_HOLD_OFF`: reports `POWER_SUPPLY_STATUS_NOT_CHARGING`.
    4. Active charging (`FAST_CHG` / `TRICKLE` / `PRE_CHG`): reports `POWER_SUPPLY_STATUS_CHARGING`.
    5. Discharging (USB absent or discharge path): reports `POWER_SUPPLY_STATUS_DISCHARGING`.
    6. Battery missing: reports `POWER_SUPPLY_STATUS_UNKNOWN`.

### 1.2 Multi-Word SRAM Read Boundary Crossing
- **The Issue:** Qualcomm Fuel Gauge memory interfaces (RIF in PMI8994, single-mode IMA in PMI8996) access memory in 32-bit (4-byte) aligned words. When a requested register read starts near the end of a word (e.g., `IBAT` at base `0x5cc` offset 3, length 2 bytes), the second byte resides in the next word at `0x5d0` offset 0. A naive linear index read returned corrupted or repeated data from the first word.
- **The Fix:**
  - `pmi8994_fg_sram_read()` now tracks word boundaries:
  - For each byte `i`:
    - `cur_word_offset = (offset + i) % 4`
    - `cur_word_addr = addr + (offset + i) - cur_word_offset`
    - Whenever `cur_word_addr` changes, the hardware address register `lsb_reg` is reprogrammed, and a new 32-bit read transaction is initiated.
  - Applies symmetrically to both RIF and IMA access paths.

### 1.3 Battery Missing Detection & Coulomb Counter Precision
- **The Issue:** When battery was disconnected or missing (`BAT_IF_RT_BATT_MISSING`), properties returned artificial synthetic formulas (e.g. `design_capacity * reg / 255`) instead of failing with proper error codes.
- **The Fix:**
  - `pmi8994_is_battery_present()` verifies both `BAT_IF_RT_BATT_MISSING` and thermistor resistance.
  - If battery is absent, telemetry queries for voltage, current, temperature, and cycle count return `-ENODATA`.
  - `POWER_SUPPLY_PROP_CHARGE_COUNTER` now queries the real 28-bit coulomb counter register (`CC_CHARGE` at `0x570` offset 0, 4 bytes) via `pmi8994_fg_sram_read()`, converting raw Q28 micro-ampere-hours, or returning `-ENODATA` if hardware CC is uninitialized.

### 1.4 Wake IRQ Lifecycle & Power Management Ops
- **The Issue:** Device wake IRQs (`enable_irq_wake()`) lacked symmetric disabling, causing wake reference leaks across driver bind/unbind cycles. Delayed work items were cancelled after IRQ unregistration rather than before.
- **The Fix:**
  - Introduced `struct pmi8994_irq_desc irqs[10]` tracking individual `wake_enabled` state per IRQ line.
  - Added `pmi8994_disable_wake_irqs()` with strict symmetric `disable_irq_wake()`.
  - Implemented explicit `.remove` and `.shutdown` platform driver hooks:
    ```c
    cancel_delayed_work_sync(&chip->notify_work);
    pmi8994_disable_wake_irqs(chip);
    ```
  - Added `SIMPLE_DEV_PM_OPS` with `.suspend` (cancels delayed work) and `.resume` (refreshes SRAM cache and schedules immediate telemetry update).

---

## 2. Kryo CPU Clock Controller Fixes (`clk-cpu-8996.c`)

### 2.1 Return Code Validation
- Verified return codes for all `regmap_write()` and `regmap_update_bits()` calls:
  - `PWRCL_REG_OFFSET + MUX_OFFSET` / `PERFCL_REG_OFFSET + MUX_OFFSET` (AUTO_CLK_SEL GPLL0 fallback).
  - `PWRCL_REG_OFFSET + PSCTL_OFFSET` / `PERFCL_REG_OFFSET + PSCTL_OFFSET`.
  - `PWRCL_REG_OFFSET + CLK_CTL_OFFSET` / `PERFCL_REG_OFFSET + CLK_CTL_OFFSET`.
- Verified return codes for secondary mux parent switches (`clk_cpu_8996_mux_set_parent`).

### 2.2 SSSCTL & L2 Indirect Register Initialisation
- **The Issue:** `qcom_cpu_clk_msm8996_acd_init()` only wrote `writel(0xf, ...)` to the cluster corresponding to the currently executing CPU core (`read_cpuid_mpidr()`). During driver probe (executed exclusively on CPU0 in PWRCL), PERFCL SSSCTL was never written. Additionally, repeated calls from rate change notifiers re-wrote L2 indirect registers unnecessarily.
- **The Fix:**
  - SSSCTL (`writel(0xf, reg_base + ... + SSSCTL_OFFSET)`) is now unconditionally configured for **both** `PWRCL` and `PERFCL`.
  - L2 indirect registers (`L2ACDTD_REG`, `L2ACDDVMRC_REG`, `L2ACDSSCR_REG`, `L2ACDCR_REG`) are guarded by `if (l2acdtd != L2ACDTD_INIT_VAL)` to eliminate repeated indirect writes during CPU frequency scaling.

### 2.3 Rate Change Error Rollback
- Notifier callback `cpu_clk_notifier_cb()` validates intermediate parent switch returns during `PRE_RATE_CHANGE` and `POST_RATE_CHANGE`. If switching secondary muxes to `SMUX_XO_INDEX` fails, the rate transition is aborted cleanly, preventing orphaned intermediate PLL states.

---

## 3. Broadcom Wi-Fi BCM43596 Stability (`somc-dora-platform.c`)

### 3.1 Removal of Destructive `device_reprobe()`
- **The Issue:** In `somc_dora_wifi_pci_iter()`, when force rebind was triggered, `device_reprobe(dev)` was invoked directly from a worker context while the PCIe device was already bound to `brcmfmac`. This caused race conditions and kernel panics if the PCIe link state or firmware was in transition.
- **The Fix:**
  - Removed `device_reprobe(dev)` entirely.
  - Retained solely the safe deferred attach path (`if (!dev->driver) device_attach(dev);`).
  - Cleared `bcmdhd_force_rebind` safely in worker callback without destructive re-probing.

---

## 4. Reserved Memory Carveout Verification

Full verification against Sony 4.4 (`origin/main:arch/arm/boot/dts/qcom/msm8996.dtsi`):
- Sony 4.4 defines single contiguous block:
  ```dts
  peripheral_region@8ea00000 {
      reg = <0 0x8ea00000 0 0x2d00000>; /* 45 MiB total */
  };
  ```
- Linux 5.15 divides this into explicit sub-regions:
  - `adsp_region@8ea00000`: `0x8ea00000` - `0x90400000` (26 MiB)
  - `slpi_region@90400000`: `0x90400000` - `0x90f00000` (11 MiB)
  - `zap_shader_region@90f00000`: `0x90f00000` - `0x91000000` (1 MiB)
  - `venus_region@91000000`: `0x91000000` - `0x91500000` (5 MiB)
  - `mba_region@91500000`: `0x91500000` - `0x91700000` (2 MiB)
- **Sum:** 26 + 11 + 1 + 5 + 2 = 45 MiB (`0x2d00000`).
- **Conclusion:** 0 overlaps, 0 gaps, exactly matches Sony 4.4 physical hardware memory map.
