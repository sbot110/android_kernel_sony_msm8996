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
| `ramoops@a7f00000` | `0xa7f00000` | `0xa7fff000` | `1020 KiB` | `pstore` / `ramoops` (`no-map`) |
| `memory@a7fff000` | `0xa7fff000` | `0xa8000000` | `4 KiB` | Experimental early-boot stage breadcrumbs (`no-map`) |

No address overlaps exist. Sub-allocations (`record-size = 128 KiB`, `console-size = 256 KiB`, `ftrace-size = 128 KiB`, `pmsg-size = 128 KiB`) total `640 KiB <= 1020 KiB`.

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

## 5. Collecting `pstore` / `ramoops` Logs and Experimental Early-Boot Stage Breadcrumbs

1. **Standard `pstore` / `ramoops` logs (once `ramoops_probe()` has run):**
   ```sh
   adb shell "ls -la /sys/fs/pstore/"
   adb shell "cat /sys/fs/pstore/console-ramoops-0" > dora-5.15-console-ramoops.log
   adb shell "cat /sys/fs/pstore/dmesg-ramoops-0" > dora-5.15-dmesg-ramoops.log
   ```

2. **Experimental Early-Boot Breadcrumbs at `0xa7fff000` (if boot stops before `ramoops` initializes):**
   - **Status:** **EXPERIMENTAL** — relies on DDR retention across warm reset (`PON_WARM_RESET`) and on `/dev/mem` availability in recovery or the working 4.4 kernel.
   - **Memory Layout (`0xa7fff000..0xa7fff0bf`, 6 non-overlapping 32-byte slots):**
     Each 32-byte slot contains `[+0x00] magic`, `[+0x08] arg0`, `[+0x10] arg1`, `[+0x18] checksum = magic ^ arg0 ^ arg1 ^ 0x3c3c3c3c3c3c3c3c`, cleaned and invalidated to PoC via `dsb sy` + `dc civac` + `dsb sy`:
     - **Slot 0 (`0xa7fff000`, Stage 1 — `head.S:preserve_boot_args`):** `magic = "515STGS1"`, `arg0 = FDT PA (x21)`, `arg1 = _text PA`. Also zeroes Slots 1..5 on entry.
     - **Slot 1 (`0xa7fff020`, Stage 2 — `head.S:__primary_switch` before `__enable_mmu`):** `magic = "515STGS2"`, `arg0 = phys_misalign (x23)`, `arg1 = init_pg_dir PA`.
     - **Slot 2 (`0xa7fff040`, Stage 3 — `setup.c:setup_arch` after `early_ioremap_init`):** `magic = "515STGS3"`, `arg0 = __fdt_pointer`, `arg1 = read_cpuid_mpidr()`.
     - **Slot 3 (`0xa7fff060`, Stage 4 — `setup.c:setup_arch` after `arm64_memblock_init`):** `magic = "515STGS4"`, `arg0 = memblock_start_of_DRAM()`, `arg1 = memblock_end_of_DRAM()`.
     - **Slot 4 (`0xa7fff080`, Stage 5 — `setup.c:setup_arch` after `paging_init`):** `magic = "515STGS5"`, `arg0 = (u64)_text`, `arg1 = (u64)_end`.
     - **Slot 5 (`0xa7fff0a0`, Stage 6 — `setup.c:setup_arch` before `early_ioremap_reset`):** `magic = "515STGS6"`, `arg0 = initrd_start`, `arg1 = initrd_end`.
   - **Checking `/dev/mem` and reading `0xa7fff000` in TWRP or Linux 4.4:**
     ```sh
     adb shell "ls -l /dev/mem && grep -iE 'a7f00000|ramoops' /proc/iomem"
     adb shell "dd if=/dev/mem bs=4096 skip=688127 count=1 2>/dev/null | hexdump -C | head -n 16"
     ```

## 6. Sony S1 Appended DTB (`Image.gz-dtb`) & LineageOS 21 `boot.img` Geometry

Per binary inspection of the working Linux 4.4.302 `boot.img`:

- **Appended DTB Format (`Image.gz-dtb`):** Sony S1 Bootloader selects from 4 concatenated Flattened Device Tree (`.dtb`, magic `0xd00dfeed`) blobs matching the exact 4 SoC/PMIC combinations from the working 4.4 `boot.img`:
  1. `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` (`qcom,msm-id = <0xf6 0x30000>`, `qcom,pmic-id = <0x20009 0x10013 0x0 0x0>`, `qcom,board-id = <0x8 0x0>`)
  2. `msm8996-pmi8996-sony-xperia-tone-dora.dtb` (`qcom,msm-id = <0xf6 0x30001>`, `qcom,pmic-id = <0x20009 0x10013 0x0 0x0>`, `qcom,board-id = <0x8 0x0>`)
  3. `msm8996-v3.0-sony-xperia-tone-dora.dtb` (`qcom,msm-id = <0xf6 0x30000>`, `qcom,pmic-id = <0x20009 0x2000a 0x0 0x0>`, `qcom,board-id = <0x8 0x0>`)
  4. `msm8996-sony-xperia-tone-dora.dtb` (`qcom,msm-id = <0xf6 0x30001>`, `qcom,pmic-id = <0x20009 0x2000a 0x0 0x0>`, `qcom,board-id = <0x8 0x0>`)
- **Exact `mkbootimg` Offsets (`BoardConfigCommon.mk` / working 4.4 `boot.img`):**
  - `BOARD_KERNEL_BASE := 0x80000000`
  - `BOARD_KERNEL_PAGESIZE := 4096`
  - `BOARD_KERNEL_OFFSET := 0x00008000` (header `kernel_addr = 0x80008000`)
  - `BOARD_KERNEL_TAGS_OFFSET := 0x01E00000` (header `tags_addr = 0x81E00000`, +30 MiB)
  - `BOARD_RAMDISK_OFFSET := 0x02200000` (confirmed `/chosen/linux,initrd-start = 0x82200000`, +34 MiB in working 4.4 `boot.img`)
  - `BOARD_BOOTIMAGE_PARTITION_SIZE := 67108864` (64 MiB)
- **Diagnostic `BOARD_KERNEL_CMDLINE` (without `earlycon=msm_serial_dm,0x075b0000` on retail units unless TA UART is enabled):**
  ```
  androidboot.hardware=qcom user_debug=31 msm_rtb.filter=0x237 ehci-hcd.park=3 lpm_levels.sleep_disabled=1 cma=64M@0-0xffffffff coherent_pool=2M zram.backend=z3fold loop.max_part=16 androidboot.selinux=permissive console=pstore0 loglevel=8 ignore_loglevel printk.time=1 panic=5 oops=panic softlockup_panic=1 hung_task_panic=1
  ```
