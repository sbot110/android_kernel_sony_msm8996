# 13. Safe First Hardware Test Procedure & Boot-Readiness Checklist (F8131 / F8132)

> **CRITICAL SAFETY NOTICE:**
> - **NEVER** flash `aboot` / S1 bootloader, `tz`, `hyp`, `rpm`, `pmic`, `xbl`, `ta` (Trim Area), or partition tables (`GPT`).
> - **ALWAYS** test via volatile RAM boot (`fastboot boot test-boot.img`) first so a simple power-off/reboot restores the untouched flashed `boot` partition!

---

## 1. Pre-Flight Prerequisites

1. Device is **Sony Xperia X Performance `F8131` (Single SIM) or `F8132` (Dual SIM)** with an **unlocked bootloader** and working **LineageOS 21** (`4.4`/`4.19`) installation.
2. Battery charge level is between **40% and 80%** (do not perform initial kernel bring-up on a deeply depleted `<15%` battery or while hot).
3. Back up current working `boot.img` from TWRP / Lineage Recovery (`dd if=/dev/block/bootdevice/by-name/boot of=/sdcard/stock-los21-boot.img`).
4. Repack `test-boot.img` by replacing **only** `kernel` (`Image.gz-dtb` produced by `scripts/dora/build-dora-5.15.sh`) inside your working LineageOS 21 `boot.img` using `magiskboot` or `unpack_bootimg` / `mkbootimg` (preserving exact `cmdline`, `ramdisk`, `base=0x80000000`, `kernel_offset=0x00080000`, `tags_offset=0x01e00000`, `pagesize=4096`).
5. Verify `test-boot.img` size is `< 64 MiB` and `Image.gz-dtb` is `< 30 MiB` (`0x01E00000`).

---

## 2. Step-by-Step Volatile `fastboot boot` Test

1. Power off phone; hold **Volume Up** while connecting USB cable to PC until LED turns **Blue** (Fastboot mode):
   ```bash
   fastboot devices
   fastboot boot test-boot.img
   ```
2. Observe early boot behavior (0–30 seconds):
   - Sony logo -> backlight WLED transition (`pmi8994-wled`) -> USB enumeration on host (`lsusb` / `dmesg -w` on Linux PC for `0fce:` Sony VID).
3. As soon as ADB enumerates (`adb wait-for-device`):
   ```bash
   adb shell dmesg > dmesg-first-boot.txt
   adb logcat -b all -d > logcat-first-boot.txt
   ```
4. Inspect mandatory hardware health & safety telemetry immediately:
   ```bash
   adb shell "cat /proc/version"
   adb shell "cat /sys/class/power_supply/battery/uevent"
   adb shell "cat /sys/class/power_supply/usb/uevent"
   adb shell "cat /sys/class/thermal/thermal_zone*/temp"
   adb shell "cat /sys/devices/system/cpu/cpufreq/policy*/scaling_available_frequencies"
   ```

---

## 3. Immediate Abort Criteria (Unplug & Hard Power-Off: Power + Volume Up 10s)

Immediately abort the test and reboot back to the untouched flashed `boot` partition if ANY of the following occur:
- Battery `TEMP` reads outside `100..450` (`10.0°C..45.0°C` indoors) or phone chassis feels unusually warm while idle.
- `VOLTAGE_NOW` exceeds `4365000` (`4.365 V`) during USB charging.
- Continuous crash-loop before ADB comes up: wait for 1 reboot into existing LineageOS 21 / TWRP and pull `/sys/fs/pstore/console-ramoops-0` and `/sys/fs/pstore/dmesg-ramoops-0` (`ramoops@a7f00000`).
