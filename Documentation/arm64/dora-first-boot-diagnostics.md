# Dora (F8131 / F8132) Linux 5.15 First-Boot Diagnostic & Recovery Procedure

Status: **SOURCE-READY & BUILDABLE (NOT YET BOOT-TEST-READY until boot.img packaging and hardware voltage telemetry are verified on device)**.

## 1. Preserving and Restoring Working LineageOS 21 `boot.img`

Before testing any Linux 5.15 kernel image on Sony Xperia X Performance (`dora`, models `F8131` and `F8132`):

1. **Backup the known-good LineageOS 21 (Linux 4.4) boot partition:**
   From an active ADB root shell or recovery shell on the device:
   ```sh
   dd if=/dev/block/bootdevice/by-name/boot of=/sdcard/boot-los21-working.img bs=4096
   ```
   Pull the backup to the host workstation and verify its SHA256 checksum:
   ```sh
   adb pull /sdcard/boot-los21-working.img .
   sha256sum boot-los21-working.img > boot-los21-working.img.sha256
   ```

2. **Non-destructive transient boot via Fastboot (preferred):**
   Boot the test Linux 5.15 image into RAM without overwriting the flash partition:
   ```sh
   fastboot boot boot-dora-5.15-diag.img
   ```
   A single power-cycle or automatic panic reboot (`CONFIG_PANIC_TIMEOUT=5`) will automatically boot back into the untouched LineageOS 21 `boot` partition and preserve `ramoops` in physical RAM (`0xa7f00000`).

3. **Full partition recovery (if `boot` was flashed):**
   Hold **Volume Up** while connecting USB to enter Sony Fastboot mode (blue LED), then restore the known-good image:
   ```sh
   fastboot flash boot boot-los21-working.img
   fastboot reboot
   ```

## 2. Building the Diagnostic Kernel Configuration

Merge `dora_defconfig` with `dora_diag.config`:
```sh
ARCH=arm64 ./scripts/kconfig/merge_config.sh -m \
  arch/arm64/configs/dora_defconfig \
  arch/arm64/configs/dora_diag.config
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image.gz dtbs
```

## 3. Collecting `pstore` / `ramoops` Logs After First Boot

The Device Tree (`msm8996-sony-xperia-tone.dtsi`) reserves 1 MiB at `0xa7f00000` with `no-map`:
- `reg = <0 0xa7f00000 0 0x100000>`
- `record-size = <0x20000>` (128 KiB panic/oops records)
- `console-size = <0x40000>` (256 KiB persistent kernel console log)
- `ftrace-size = <0x20000>` (128 KiB function trace buffer)
- `pmsg-size = <0x20000>` (128 KiB userspace `init` log)
- `ecc-size = <16>`

After the test kernel boots (or panics and warm-reboots after 5 seconds into the working LineageOS 21 kernel or recovery), retrieve the captured logs:
```sh
adb shell "ls -la /sys/fs/pstore/"
adb shell "cat /sys/fs/pstore/console-ramoops-0" > dora-5.15-console-ramoops.log
adb shell "cat /sys/fs/pstore/dmesg-ramoops-0" > dora-5.15-dmesg-ramoops.log
```
