# Dora (F8131 / F8132) Linux 5.15 First-Boot Diagnostic & Recovery Procedure

Status: **SOURCE-READY & BUILDABLE (NOT YET BOOT-TEST-READY until boot.img packaging and hardware voltage telemetry are verified on device)**.

## 1. Verification of `ramoops@a7f00000` & `reserved-memory` Layout

The physical address `0xa7f00000` (size `0x100000`, 1 MiB) matches Sony reference kernel 4.4 (`arch/arm/boot/dts/qcom/msm8996-tone-common.dtsi`, node `pstore_reserve_mem_region@a7f00000`) and has been audited against all `reserved-memory` regions in `msm8996.dtsi` and `msm8996-sony-xperia-tone.dtsi`:

| Region Node | Start Address | End Address (excl.) | Size | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `cont_splash_mem` | `0x83401000` | `0x85800000` | `~36 MiB` | Bootloader continuous splash |
| `memory@85800000` | `0x85800000` | `0x86000000` | `8 MiB` | TZ / hypervisor reserved |
| `smem_mem` | `0x86000000` | `0x86200000` | `2 MiB` | Qualcomm SMEM |
| `memory@86200000` | `0x86200000` | `0x88800000` | `38 MiB` | Subsystem reserved |
| `mpss_region` | `0x88800000` | `0x8ea00000` | `98 MiB` | Modem (MPSS) PIL |
| `adsp_region` | `0x8ea00000` | `0x90400000` | `26 MiB` | ADSP PIL |
| `zap_shader_region` | `0x90400000` | `0x90402000` | `8 KiB` | Adreno 530 GPU zap shader |
| `slpi_region` | `0x90500000` | `0x90f00000` | `10 MiB` | Sensors (SLPI) PIL |
| `venus_region` | `0x90f00000` | `0x91400000` | `5 MiB` | Venus video PIL |
| `mba_region` | `0x91500000` | `0x91700000` | `2 MiB` | Modem boot authenticator |
| `rmtfs_mem` | `0xa0000000` | `0xa2000000` | `2 MiB` (range) | Remote filesystem buffer |
| `ramoops@a7f00000` | `0xa7f00000` | `0xa8000000` | `1 MiB` | `pstore` / `ramoops` (`no-map`) |

No address overlaps exist. Sub-allocations (`record-size = 128 KiB`, `console-size = 256 KiB`, `ftrace-size = 128 KiB`, `pmsg-size = 128 KiB`) total `640 KiB <= 1024 KiB`.

## 2. Sony S1 Bootloader (`Tone`) Constraints: `fastboot flash boot` vs `fastboot boot`

On Sony Xperia X Performance (`F8131` / `F8132`, `Tone` S1 Bootloader):
- **Why `fastboot boot` is unreliable on Sony S1:** Certain Sony S1 Bootloader revisions on MSM8996 either reject RAM-booted `boot.img` payloads, fail to populate `/chosen` board/panel command-line tags when booting from RAM, or—if the test kernel panics and warm-reboots (`CONFIG_PANIC_TIMEOUT=5`)—immediately boot the flashed `boot` partition, which can re-initialize the `pstore` ring buffer before recovery is entered.
- **Recommended Safe Procedure:**
  1. Ensure a working recovery (TWRP / Lineage Recovery) is installed in the independent `fotakernel` (`recovery`) partition.
  2. Back up the working LineageOS 21 `boot` partition (`boot-los21-working.img`) both on `/sdcard/` and on the host PC.
  3. Flash the diagnostic 5.15 image via `fastboot flash boot boot-dora-5.15-diag.img`.
  4. Boot the device; if a panic or early hang occurs, hold **Volume Down** during the warm reboot (after the 5-second `panic_timeout`) to enter `fotakernel` recovery immediately and read `/sys/fs/pstore/*` before any normal Android boot overwrites the console ring.
  5. Only use `fastboot boot boot-dora-5.15-diag.img` if your specific S1 Bootloader revision has been verified to pass `/chosen/bootargs` (`mdss_mdp.panel=...`, `oemandroidboot.babe09a9=...`) cleanly in RAM-boot mode.

## 3. Preserving and Restoring Working LineageOS 21 `boot.img`

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

2. **Full partition recovery:**
   Hold **Volume Up** while connecting USB to enter Sony Fastboot mode (blue LED), or restore directly from TWRP recovery:
   ```sh
   fastboot flash boot boot-los21-working.img
   fastboot reboot
   ```

## 4. Building the Diagnostic Kernel Configuration

Merge `dora_defconfig` with `dora_diag.config`:

```sh
ARCH=arm64 ./scripts/kconfig/merge_config.sh -m \
  arch/arm64/configs/dora_defconfig \
  arch/arm64/configs/dora_diag.config
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc) Image.gz-dtb dtbs
```

## 5. Collecting `pstore` / `ramoops` Logs After First Boot

After booting into `fotakernel` recovery (or ADB if the test kernel reaches `adbd`), retrieve the captured logs:

```sh
adb shell "ls -la /sys/fs/pstore/"
adb shell "cat /sys/fs/pstore/console-ramoops-0" > dora-5.15-console-ramoops.log
adb shell "cat /sys/fs/pstore/dmesg-ramoops-0" > dora-5.15-dmesg-ramoops.log
```
