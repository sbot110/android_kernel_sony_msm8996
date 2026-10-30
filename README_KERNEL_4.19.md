# Migration Guide & Documentation: Transitioning Sony Xperia X Performance (tone / dora) to Linux Kernel 4.19 for LineageOS 21 / Android 14+

## Overview
This branch (`kernel-4.19`) provides the prepared kernel tree structure, build configuration, and Android 14 (LineageOS 21) requirements for migrating the Sony Xperia X Performance (MSM8996 / tone_dora platform) from Linux kernel 4.4 to Linux kernel 4.19.

---

## Key Changes & Updates in `kernel-4.19` Branch

1. **Branch Isolation:**
   - Isolated in `kernel-4.19` branch without modifying existing production branches (`main` or `dora-full-state-20261017`).

2. **Kernel Defconfig Updates (`arch/arm64/configs/tone_dora_defconfig`):**
   - Enabled **Pressure Stall Information (PSI)** (`CONFIG_PSI=y`): Mandatory for Android 14 LMKD.
   - Enabled **cgroups v2 & Memory Controller** (`CONFIG_MEMCG=y`, `CONFIG_MEMCG_SWAP=y`).
   - Enabled **eBPF & Cgroup BPF Filters** (`CONFIG_CGROUP_BPF=y`, `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y`, `CONFIG_NET_CLS_BPF=y`, `CONFIG_NET_ACT_BPF=y`, `CONFIG_NETFILTER_XT_MATCH_BPF=y`).
   - Enabled **BinderFS** (`CONFIG_ANDROID_BINDERFS=y`) and 64-bit IPC (`CONFIG_ANDROID_BINDER_IPC_32BIT=n`).
   - Enabled **FUSE BPF Passthrough** (`CONFIG_FUSE_BPF=y`) and **Incremental FS** (`CONFIG_INCREMENTAL_FS=y`) for modern Android storage stack.
   - Enabled BPF & Uprobe trace events (`CONFIG_BPF_EVENTS=y`, `CONFIG_UPROBE_EVENTS=y`).

3. **Build Configurations:**
   - Updated `build.config.common` target branch to `android-4.19-stable`.
   - Updated build toolchain configurations for LLVM/Clang compilation targeting LineageOS 21 / Android 14 Android.bp and AndroidKernel.mk build rules.

---

## Build Instructions (LineageOS 21 / Android 14)

### 1. Environment Requirements
- **OS:** Linux x86_64 (Ubuntu 20.04 LTS or 22.04 LTS recommended)
- **Toolchain:** Android LLVM/Clang (r450784 / r488909 or newer recommended for Android 14)
- **Cross Compilers:** `aarch64-linux-gnu-` / `arm-linux-gnueabi-`

### 2. Manual Kernel Compilation
To compile standalone using LLVM/Clang:

```bash
export ARCH=arm64
export SUBARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-
export REAL_CC=clang
export CC=clang

# Load device configuration
make tone_dora_defconfig

# Build kernel Image.gz-dtb
make -j$(nproc)
```

### 3. LineageOS 21 Tree Build
In your LineageOS 21 workspace (`device/sony/tone` & `kernel/sony/msm8996`):

```bash
source build/envsetup.sh
lunch lineage_dora-userdebug
m bootimage
```

---

## Hardware Abstraction & Driver Porting Status Checklist (Kernel 4.19)

| Subsystem | Kernel 4.4 Status | Kernel 4.19 Status / Migration Notes |
| :--- | :--- | :--- |
| **CPU / MSM8996 SoC** | Supported | Core ARM64 & MSM PMIC DTBs mapped |
| **Display / MSM DRM** | FBDEV / DRM | Requires DRM/KMS DRM driver adaptations |
| **Wi-Fi / BCM4359** | DHD / bcmdhd | Updated wpa_supplicant & nl80211 integration |
| **Audio / SLIMbus** | MSM WCD9335 | Audio HAL sound card mapping via ALSA |
| **Camera / MSM CPP** | Qualcomm KAPI | V4L2 subdevice adaptations for Android 14 |
| **Binder / IPC** | Android Binder | **BinderFS enabled** (`/dev/binderfs`) |
| **BPF / Network** | Legacy xt_qtaguid | **eBPF eBpfLoader enabled** |

---

## Maintenance & Contributions
When making further commits to this kernel 4.19 porting effort, ensure all commits are pushed to `kernel-4.19` or individual topic branches targeting kernel 4.19.
