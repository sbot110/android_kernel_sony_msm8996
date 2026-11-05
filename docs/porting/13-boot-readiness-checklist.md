# 13. Safe First Hardware Test Procedure & Boot-Readiness Checklist (F8131 / F8132)

> **CRITICAL SAFETY NOTICE:**
> - **NEVER** flash `aboot` / S1 bootloader, `tz`, `hyp`, `rpm`, `pmic`, `xbl`, `ta` (Trim Area), or partition tables (`GPT`).
> - **ALWAYS** test via volatile RAM boot (`fastboot boot test-boot.img`) first so a simple power-off/reboot restores the untouched flashed `boot` partition!
> - **DO NOT USE** `fastboot flash boot` for initial bring-up testing!

---

## 1. Baseline Assumptions & Hardware Context

1. **Working Baseline Distinction:**
   - **Known Physical Baseline:** Linux 4.4.302 (`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`) on **LineageOS 20 (Android 13)** is the confirmed, physically working baseline for Sony Xperia X Performance (`dora` F8131 / F8132).
   - **Target OS:** LineageOS 21 (Android 14) has been prepared at the source tree level, but has **not yet been verified on physical hardware**.
   - **Strategy:** Initial boot tests must be executed with volatile `fastboot boot` against a device with an existing, functional LineageOS 20 or 21 installation and an intact TWRP / Lineage Recovery partition.

2. **Pre-Flight Device State:**
   - Device is **Sony Xperia X Performance `F8131` (Single SIM) or `F8132` (Dual SIM)** with an unlocked bootloader.
   - Battery state: Charge level must be between **40% and 80%** (do not perform initial bring-up below 20% or above 90%).
   - Clean Micro-USB port (capless waterproof port dry and free of debris).
   - Back up current working `boot` partition before anything else:
     ```bash
     # From TWRP or root adb shell:
     dd if=/dev/block/bootdevice/by-name/boot of=/sdcard/stock-working-boot.img
     ```

---

## 2. Boot Image Repacking Parameters

Sony MSM8996 S1 bootloader requires header version 0 with strict memory offsets:

| Parameter | Value | Description |
|:---|:---|:---|
| `--base` | `0x80000000` | Qualcomm MSM8996 physical RAM base |
| `--kernel_offset` | `0x00080000` | Kernel load address `0x80080000` |
| `--ramdisk_offset` | `0x02000000` | Ramdisk load address `0x82000000` |
| `--second_offset` | `0x00f00000` | Second stage bootloader offset |
| `--tags_offset` | `0x01e00000` | ATAGS / Devicetree load address `0x81e00000` |
| `--pagesize` | `4096` | Flash block page size |
| `--header_version` | `0` | Standard Android v0 boot header |

### Kernel Command Line (`--cmdline`)
```text
console=ttyMSM0,115200,n8 androidboot.console=ttyMSM0 androidboot.hardware=qcom user_debug=31 msm_rtb.filter=0x237 ehci-hcd.park=3 lpm_levels.sleep_disabled=1 service_locator.enable=1 androidboot.bootdevice=7464900.sdhci ramoops.mem_address=0xa7f00000 ramoops.mem_size=0x100000 ramoops.console_size=0x40000 ramoops.pmsg_size=0x40000 ramoops.record_size=0x20000 ramoops.dump_oops=1
```
*(Notice: retail units lack Sony TA earlycon UART clocks; earlycon is omitted to prevent early MMIO SError before GCC probe).*

### OS-Specific Parameters:
- **LineageOS 20 (Android 13 Baseline):**
  - `--os_version`: `13.0.0`
  - `--os_patch_level`: `2023-08-01`
- **LineageOS 21 (Android 14 Target):**
  - `--os_version`: `14.0.0`
  - `--os_patch_level`: `2024-03-01`

### Repacking with `mkbootimg` or `magiskboot`:
```bash
# Using mkbootimg:
mkbootimg \
  --kernel out/arch/arm64/boot/Image.gz-dtb \
  --ramdisk ramdisk.cpio.gz \
  --cmdline "console=ttyMSM0,115200,n8 androidboot.console=ttyMSM0 androidboot.hardware=qcom user_debug=31 msm_rtb.filter=0x237 ehci-hcd.park=3 lpm_levels.sleep_disabled=1 service_locator.enable=1 androidboot.bootdevice=7464900.sdhci ramoops.mem_address=0xa7f00000 ramoops.mem_size=0x100000 ramoops.console_size=0x40000 ramoops.pmsg_size=0x40000 ramoops.record_size=0x20000 ramoops.dump_oops=1" \
  --base 0x80000000 \
  --kernel_offset 0x00080000 \
  --ramdisk_offset 0x02000000 \
  --second_offset 0x00f00000 \
  --tags_offset 0x01e00000 \
  --pagesize 4096 \
  --header_version 0 \
  --os_version 14.0.0 \
  --os_patch_level 2024-03-01 \
  -o test-boot.img

# Or using magiskboot:
magiskboot unpack stock-working-boot.img
cp out/arch/arm64/boot/Image.gz-dtb kernel
magiskboot repack stock-working-boot.img test-boot.img
```

---

## 3. Step-by-Step Volatile `fastboot boot` Execution

### Why `fastboot boot` vs `fastboot flash boot`?
- **`fastboot boot test-boot.img`:** Loads the kernel directly into volatile DRAM. The eMMC flash partition table and `/dev/block/bootdevice/by-name/boot` remain 100% untouched. If anything goes wrong (kernel panic, hang, thermal trip), powering off immediately restores the pristine system.
- **`fastboot flash boot test-boot.img`:** Overwrites the persistent boot partition on eMMC. If the kernel fails to initialize the display or USB ADB, recovery requires manual fastboot intervention. **NEVER USE `flash boot` FOR INITIAL TESTS.**

### Execution Procedure:
1. Turn off the device completely.
2. Hold **Volume Up** and plug in the USB cable connected to the host PC until the notification LED turns **solid Blue** (Fastboot mode).
3. Verify connection:
   ```bash
   fastboot devices
   ```
4. Perform the volatile boot:
   ```bash
   fastboot boot test-boot.img
   ```
5. Monitor host USB bus:
   ```bash
   # On Linux host terminal:
   dmesg -w | grep -iE "usb|qcom|sony|0fce"
   ```

---

## 4. Emergency Abort & Instant Rollback Procedure

If the screen goes black without backlight, bootloops, or fails to enumerate ADB within 60 seconds:

1. **Immediate Hard Power-Off:**
   - Press and hold **Power + Volume Up** simultaneously for **10 full seconds**.
   - The device will vibrate **three times consecutively**. This confirms a hardware-level power cut and PMIC reset.
   - Release the buttons immediately.
2. **Reboot to Pristine System:**
   - Turn on the phone normally by holding **Power** for 2 seconds.
   - The S1 bootloader boots from the untouched `/boot` partition, immediately returning you to your working LineageOS baseline.
3. **Accidental `flash boot` Recovery:**
   - If `fastboot flash boot` was executed previously and the device does not boot:
     - Hold **Volume Up** while connecting USB cable (Blue LED).
     - Restore your backed-up stock kernel:
       ```bash
       fastboot flash boot stock-working-boot.img
       fastboot reboot
       ```
4. **Post-Crash Diagnostics Extraction:**
   - Boot into TWRP / Recovery or existing OS.
   - Inspect Ramoops persistent console buffer:
     ```bash
     adb shell "cat /sys/fs/pstore/console-ramoops-0" > crash-console.txt
     adb shell "cat /sys/fs/pstore/dmesg-ramoops-0" > crash-dmesg.txt
     ```
   - All kernel panic messages and early printks are captured at `ramoops@a7f00000`.

---

## 5. Post-Boot Hardware Safety Telemetry Audit

As soon as ADB connects (`adb wait-for-device`):

```bash
# 1. Verify kernel version and build identity:
adb shell "uname -a"
adb shell "cat /proc/version"

# 2. Verify Fuel Gauge and Charger safety limits:
adb shell "cat /sys/class/power_supply/battery/uevent"
adb shell "cat /sys/class/power_supply/usb/uevent"

# Check critical safety properties:
# POWER_SUPPLY_PROP_VOLTAGE_NOW <= 4350000 uV (4.35V max float)
# POWER_SUPPLY_PROP_TEMP between 150 and 450 (15.0°C - 45.0°C)
# POWER_SUPPLY_PROP_STATUS != UNKNOWN when plugged in

# 3. Verify CPU frequencies and ACD governor:
adb shell "cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq"
adb shell "cat /sys/devices/system/cpu/cpufreq/policy2/scaling_cur_freq"
adb shell "cat /sys/devices/system/cpu/cpufreq/policy*/scaling_available_frequencies"

# 4. Check thermals across all PMIC and SoC zones:
adb shell "cat /sys/class/thermal/thermal_zone*/temp"

# 5. Extract full dmesg:
adb shell "dmesg" > dmesg-stage-g1.txt
```
