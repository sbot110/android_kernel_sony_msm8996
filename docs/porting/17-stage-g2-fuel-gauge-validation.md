# Stage G.2 Fuel Gauge & Charger Driver Validation

## 1. Overview
The PMI8994 Fuel Gauge and Charger driver (`drivers/power/supply/qcom_pmi8994_fg_chg.c`) manages battery fuel gauging, autonomous hardware charge control, and thermal protection on the Sony Xperia X Performance (Tone Dora, MSM8996).

During the Stage G.2 independent audit, critical defects were identified and resolved in:
1. Charge state priority resolution
2. Battery presence error propagation
3. Uncalibrated Coulomb Counter reporting
4. SRAM multi-byte boundary reads
5. Worker lifecycle unwinding

## 2. Charge State Priority Resolution
### Problem
In earlier iterations, `pmi8994_get_batt_status()` evaluated charger state flags without strictly respecting the hardware inhibition state (`CHGR_RT_CHG_INHIBIT`). When a battery reached the float voltage or Soft JEITA threshold, the hardware charger stopped charging, yet the status register still retained previous charge type bits (`CHG_TYPE_FAST`), leading the driver to falsely report `POWER_SUPPLY_STATUS_CHARGING`.

### Resolution
Strict priority hierarchy was established in `pmi8994_get_batt_status()`:
1. **Priority 1: Critical Hardware Safety Trips:** Thermal shutdown (`MISC_RT_TEMP_SHUTDOWN`), Over-Temperature (`BAT_IF_RT_HOT`), Under-Temperature (`BAT_IF_RT_COLD`), or Battery Overvoltage (`BAT_IF_RT_BATT_OV`). Returns `POWER_SUPPLY_STATUS_NOT_CHARGING`.
2. **Priority 2: Full Charge / Termination:** TCC reached (`CHGR_RT_BAT_TCC_REACHED`) or SOC 100% (`FG_SOC_RT_FULL_SOC`). Returns `POWER_SUPPLY_STATUS_FULL`.
3. **Priority 3: Hardware Inhibit:** Charge inhibit active (`CHGR_RT_CHG_INHIBIT`). Returns `POWER_SUPPLY_STATUS_NOT_CHARGING`.
4. **Priority 4: Active Charging Phase:** Evaluated ONLY if not inhibited:
   - `CHG_TYPE_PRE`, `CHG_TYPE_FAST`, `CHG_TYPE_TAPER` -> `POWER_SUPPLY_STATUS_CHARGING`
   - `CHG_TYPE_NONE` -> `POWER_SUPPLY_STATUS_NOT_CHARGING`

## 3. Battery Presence & Safe Data Semantics
`pmi8994_get_battery_presence(chip, &present)` returns negative error codes if SPMI bus communication fails. All power supply properties (`VOLTAGE_NOW`, `VOLTAGE_OCV`, `CURRENT_NOW`, `TEMP`, `CAPACITY`, `CYCLE_COUNT`) return `-ENODATA` when the battery is absent, preventing corrupted reads or uninitialized zero values from reaching userspace.

## 4. Hardware Coulomb Counter (CC_CHARGE) Audit
The PMI8994 Fuel Gauge hardware integrates charge in SRAM register `0x570` as raw Q28 values. In the absence of calibrated factory Rsense trim data and board-level thermal coefficient models, synthesizing microampere-hour figures yields mathematically false readings that can trigger erratic Android Health HAL capacity jumps. `POWER_SUPPLY_PROP_CHARGE_COUNTER` therefore intentionally returns `-ENODATA`, prompting userspace to rely on monotonic SOC (`FG_SOC_MONOTONIC_SOC`), which is hardware-filtered.

## 5. Worker Lifecycle & SPMI Safety
Delayed notification work is queued during probe and interrupt handling. To prevent use-after-free or race conditions on driver unbind, `devm_add_action_or_reset(dev, pmi8994_cancel_work, chip)` cancels all delayed workers before SPMI bus de-registration.
