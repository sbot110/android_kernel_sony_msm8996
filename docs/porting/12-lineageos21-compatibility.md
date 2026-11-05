# 12. LineageOS 21 (Android 14) Compatibility & LineageOS 22 Readiness Matrix

## 1. Audited Device / Vendor Repositories

| Repository | Audited Ref | Role |
|---|---|---|
| `sbot110/android_device_sony_dora` | `5e9385a65257a28f9a3974342331644a3bbcdd49` | Dora board config, init scripts, audio/sensor configs |
| `sbot110/android_device_sony_tone-common` | `2701d2aecab52b30c3672ecebbad6ae1910cc212` | Tone common `BoardConfigCommon.mk`, `fstab.qcom`, `ueventd.qcom.rc`, `init.qcom.rc`, USB/Wi-Fi HAL |
| `sbot110/android_vendor_sony` | `bb8c352c8da4942f6189fd08d0c1ec947f25e8ef` | Retail Dora `/vendor` (`/oem`) blobs: `a530_zap.mdt`, `adsp.mdt`, `slpi.mdt`, `modem.mdt`, `venus.mdt`, `fw_bcmdhd*.bin`, `bcmdhd.cal` (`devid=0x4415`) |

---

## 2. Exact Block, USB, Sysfs & Firmware Contract Verification

| LineageOS 21 Userspace Consumer | Expected Kernel Path / Interface | Linux 5.15.220 Stage G Implementation | Status |
|---|---|---|---|
| `fstab.qcom` & `ueventd.qcom.rc` (eMMC 5.1) | `/dev/block/platform/soc/7464900.sdhci/by-name/{system,userdata,cache,oem,boot,modem,dsp}` | `sdhc1: sdhci@7464900` inside `soc` (`HS400`, `GCC_SDCC1_BCR` reset, `0x314` vendor window) | **MATCHED** |
| `fstab.qcom` (MicroSD `vfat`/`exfat`) | `/devices/soc/74a4900.sdhci/mmc_host*` / `74a4900.sdhci` (`CONFIG_EXFAT_FS=y`, `CONFIG_VFAT_FS=y`) | `sdhc2: sdhci@74a4900` + `slot-gpio` single-owner `cd-gpios` (GPIO 40) & `uim2-gpios` (GPIO 29) | **MATCHED** |
| `init.qcom.usb.rc` / `init.qcom.usb.sh` | `6a00000.dwc3` + ConfigFS USB gadget (`ffs.adb`, `mtp`, `PTP`, `rndis`) | `usb3_dwc3: dwc3@6a00000` + `phy-qcom-qusb2` (`vdd` `pm8994_l28`) + ConfigFS gadget built-in | **MATCHED** |
| `init.qcom.rc` & `fingerprint.msm8996.so` | `/dev/fingerprint` + `/sys/devices/platform/soc/fpc1145_device/{irq,wakeup_enable,clk_enable,spi_prepare,hw_reset}` | `&soc { fpc1145_device { ... }; }` + `soc/fpc1145_device` symlink + `spi_prepare` attribute + `0x45` ioctl ABI | **MATCHED** |
| `init.qcom.sh` DSP bring-up | `/sys/kernel/boot_adsp/boot`, `/sys/kernel/boot_slpi/boot` | `somc-dora-platform.c` -> real `rproc_boot()` / `rproc_shutdown()` on `adsp_pil` & `slpi_pil` | **MATCHED** |
| PowerHAL tap-to-wake (`TARGET_TAP_TO_WAKE_NODE`) | `/sys/devices/virtual/input/clearpad/wakeup_gesture` & `post_probe_start` | Bound to real `synaptics@2c` (`rmi4-i2c`) wakeup & driver-attach check; `glove`/`cover` return `-EOPNOTSUPP` on `1` | **MATCHED** |
| Health HAL 2.1 / `healthd` / offline charger | `/sys/class/power_supply/{battery,usb}` | `qcom_pmi8994_fg_chg.c` real SPMI FG SRAM (RIF/IMA) + SMBCHG + `usbin` VADC | **MATCHED** |
| Wi-Fi HAL (`libwifi-hal-bcm`) | `/sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}` + `0x14e4:0x4415` | `brcmfmac` PCI IDs `0x4415..0x4417` + deferred PCIe attach/rebind when `/vendor` mounts or `op_mode` toggles STA/AP | **MATCHED** |
| Sony S1 Bootloader (`BoardConfigCommon.mk`) | `BASE=0x80000000`, `KERNEL_OFFSET=0x80000`, `TAGS_OFFSET=0x01E00000` (`30 MiB`), 4 appended DTBs | `build-dora-5.15.sh` enforces `Image.gz-dtb <= 30 MiB` (`~13.8 MiB` actual) + exact 4-DTB table | **MATCHED** |

---

## 3. Android 14 (LineageOS 21) & Android 15 (LineageOS 22) Kernel Requirements

- **BPF / eBPF & Netd:** `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y`, `CONFIG_BPF_JIT_ALWAYS_ON=y`, `CONFIG_CGROUP_BPF=y`, `CONFIG_NETFILTER_XT_MATCH_BPF=y`. (`CONFIG_DEBUG_INFO_BTF=y` supported via `ENABLE_BTF=1` in `scripts/dora/build-dora-5.15.sh` with `pahole`).
- **Cgroups v1/v2 & PSI:** `CONFIG_CGROUPS=y`, `CONFIG_MEMCG=y`, `CONFIG_CFS_BANDWIDTH=y`, `CONFIG_UCLAMP_TASK=y`, `CONFIG_UCLAMP_TASK_GROUP=y`, `CONFIG_PSI=y`.
- **Binderfs, Mounts & Security:** `CONFIG_ANDROID_BINDER_IPC=y`, `CONFIG_ANDROID_BINDERFS=y`, `CONFIG_SECCOMP=y`, `CONFIG_SECURITY_SELINUX=y` (enforcing preserved), `CONFIG_OVERLAY_FS=y`, `CONFIG_SQUASHFS=y` (`LZ4`/`XZ`/`ZLIB` for `/oem`), `CONFIG_F2FS_FS=y`, `CONFIG_EXFAT_FS=y`, `CONFIG_DM_VERITY=y`, `CONFIG_DM_CRYPT=y`, `CONFIG_FS_ENCRYPTION=y`.
- **Single-SIM (`F8131`) vs Dual-SIM (`F8132`):** Unified DTB image; `init_dora` reads `LTALabel` to set `persist.radio.multisim.config=dsds` on `F8132`, while `mmc/core/slot-gpio.c` owns `TLMM 40` (`cd-gpios`) and `TLMM 29` (`uim2-gpios`) race-free.
