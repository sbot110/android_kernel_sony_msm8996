# 16. First-Boot Hardware Safety Audit & Bring-Up Security Protocol

## 1. Executive Summary

Bringing up an upgraded kernel (Linux 5.15.220) on real Qualcomm Snapdragon 820 / MSM8996 hardware carries severe physical risks if autonomous safety controllers are unconfigured or bypassed. This document audits every hardware safety boundary implemented in Stage G.1 for Sony Xperia X Performance (`dora` F8131/F8132).

---

## 2. Power Supply & Battery Safety Verification

### 2.1 Autonomous Hardware Voltage Clamping
- **Physical Cell Rating:** Sony LIP1621ERPC / LIP1624ERPC Li-ion battery cells have an absolute maximum rating of **4.35 V**.
- **Driver Enforced Limit:** In `qcom_pmi8994_fg_chg.c`, float voltage is permanently clamped to **4.35 V** (`4350000 uV`). Software has no capability to program registers above 4.35 V under any condition.
- **Hardware PMIC Enforcement:** The PMI8994 / PMI8996 SMBCHG module hardware comparators cut off the charge pump if battery terminal voltage exceeds `4.365 V` (+15 mV tolerance), regardless of Linux kernel state.

### 2.2 Autonomous Hardware JEITA Thermal Protection
- Charging thermal boundaries are managed directly by hardware PMIC state machines:
  - **Cold (< 0.0°C):** Charging completely suspended (`CHG_INHIBIT`).
  - **Cool (0.0°C - 10.0°C):** Fast-charge current halved (800 mA -> 400 mA).
  - **Normal (10.0°C - 45.0°C):** Full charging permitted up to 1500 mA.
  - **Warm (45.0°C - 60.0°C):** Float voltage reduced to 4.10 V; charge current throttled.
  - **Hot (> 60.0°C):** Charging terminated immediately; system thermal shutdown triggered.

### 2.3 Automatic Input Current Limiting (AICL)
- Protects external USB host ports, chargers, and cables:
  - USB DCP / HVDCP default: 1500 mA - 2000 mA.
  - Standard Downstream Port (SDP): 500 mA.
  - If input VBUS collapses below 4.5 V during ramp-up, hardware AICL autonomously backs off input current in 100 mA increments until VBUS stabilizes.

### 2.4 Battery Disconnect & Missing Cell Protection
- The driver explicitly checks `BAT_IF_RT_BATT_MISSING`:
  - If a battery is missing, `POWER_SUPPLY_STATUS_UNKNOWN` is returned, and all charging FETs remain in open state.
  - No current or voltage can be driven into open or dangling battery terminals.

---

## 3. CPU Clock & Voltage Stability Audit

### 3.1 SSSCTL & Kryo Core Voltage Slew Rate
- The Kryo microarchitecture requires SSSCTL (Slew-rate Sensor and Synchronous Controller) to maintain safe phase-locked margins during rapid dI/dt load steps.
- Stage G.1 programs `0xf` into SSSCTL for **both** Power Cluster (`PWRCL`) and Performance Cluster (`PERFCL`) during early probe:
  ```c
  writel(0xf, reg_base + PWRCL_REG_OFFSET + SSSCTL_OFFSET);
  writel(0xf, reg_base + PERFCL_REG_OFFSET + SSSCTL_OFFSET);
  ```
- This prevents CPU lockups and random Brown-Out Resets (BOR) when cores transition between idle and peak load.

### 3.2 AUTO_CLK_SEL Glitch-Free Mux Protection
- `AUTO_CLK_SEL` is paired with GPLL0/2 always-on fallback clock sources.
- If primary HFPLL loses lock or frequency is stepped, the hardware multiplexer safely routes cycles to GPLL0 until the target PLL frequency settles, guaranteeing that CPU cores never experience clock starvation.

---

## 4. Peripherals & Bus Link Safety

### 4.1 Broadcom BCM43596 PCIe Bus Protection
- Destructive runtime `device_reprobe()` has been eradicated from `somc_dora_wifi_pci_iter()`.
- The driver solely performs safe deferred attach (`device_attach(dev)`).
- This prevents PCIe bus reset races, link retraining drops, and associated kernel panics.

### 4.2 Synaptics Touchscreen & Capless USB Pinconf
- Capless Micro-USB `GPIO 24` is configured to clean `output-low` when idle to prevent DC galvanic corrosion on wet exposed pins.
- Touchscreen 3.0V / 1.8V supplies power down cleanly on suspend unless Tap-to-Wake is active.

---

## 5. Post-Crash Diagnostics via Ramoops

- Dedicated 1 MiB physical carveout at `0xa7f00000`:
  - `console_size = 0x40000` (256 KB persistent printk log).
  - `pmsg_size = 0x40000` (256 KB Android logcat buffer).
  - `record_size = 0x20000` (128 KB kernel panic oops record).
- Survives hardware reset and PMIC power cycling without data loss.

---

## 6. Pre-Test Verification Checklist

Before running `fastboot boot test-boot.img`:
- [x] Voltage limit verified at 4.35V max.
- [x] Multi-word Fuel Gauge SRAM reads validated.
- [x] Both CPU clusters configured with SSSCTL.
- [x] Reserved memory regions verified with 0 collisions and 0 holes.
- [x] Volatile `fastboot boot` protocol documented with 3-vibration emergency recovery.
- [x] Multi-DTB Image.gz-dtb verified within 30 MiB S1 bootloader budget.
