# Stage G.2 Complete Build Verification Report

## 1. Build Environment
- **Host Architecture:** x86_64 Linux container
- **Host Toolchain:** GCC 12.3.0, GNU Make 4.3
- **Cross-Compiler:** `aarch64-linux-gnu-gcc (Ubuntu 12.3.0-1ubuntu1~22.04) 12.3.0`
- **Architecture:** ARM64 (`ARCH=arm64`)
- **Compilation Flags:** `KCFLAGS="-Werror"`
- **Target Kernel Tree:** Linux 5.15.220 (`sbot110/android_kernel_sony_msm8996`, branch `kernel-5.5`)
- **Target Platform:** Sony Xperia X Performance (`dora`: F8131 Single SIM / F8132 Dual SIM)

## 2. Configuration & Defconfig
- Defconfig: `arch/arm64/configs/dora_defconfig`
- All required Android 14 / LineageOS 21 kernel configs active:
  - `CONFIG_PSI=y`, `CONFIG_MEMCG=y`
  - `CONFIG_BPF_SYSCALL=y`, `CONFIG_NETFILTER_XT_MATCH_BPF=y`
  - `CONFIG_SECURITY_SELINUX=y`, `CONFIG_DM_VERITY=y`, `CONFIG_F2FS_FS=y`
  - `CONFIG_QCOM_PMI8994_FG_CHG=y`, `CONFIG_MSM_GCC_8996=y`, `CONFIG_ARM_QCOM_CPUFREQ_KRYO=y`

## 3. Reserved Memory Carveout Verification
Decompiled Device Tree inspection confirmed 11 contiguous, non-overlapping carveouts across all 4 target DTBs:
- `0x83401000..0x85800000` (36.00 MB): Secure OS / TrustZone
- `0x85800000..0x86000000` (8.00 MB): Camera / QSEECOM
- `0x86000000..0x86200000` (2.00 MB): Shared Memory (SMEM)
- `0x86200000..0x88800000` (38.00 MB): Display framebuffer / CMA
- `0x88800000..0x8ea00000` (98.00 MB): MPSS (Modem subsystem)
- `0x8ea00000..0x90400000` (26.00 MB): ADSP (Audio subsystem)
- `0x90400000..0x90402000` (8 KB): Zap Shader
- `0x90500000..0x90f00000` (10.00 MB): SLPI (Sensors)
- `0x90f00000..0x91400000` (5.00 MB): Venus (Video codec)
- `0x91500000..0x91700000` (2.00 MB): MBA (Modem Boot Authenticator)
- `0xa7f00000..0xa8000000` (1.00 MB): Ramoops / pstore

Result: **ZERO overlapping addresses detected.**

## 4. Multi-DTB Concatenation
The Sony S1 bootloader accepts an `Image.gz-dtb` payload where the gzipped kernel is followed by appended device tree blobs:
1. `msm8996-sony-xperia-tone-dora.dtb` (F8131 v1.x PM8996)
2. `msm8996-pmi8996-sony-xperia-tone-dora.dtb` (F8131 v1.x PMI8996)
3. `msm8996-v3.0-sony-xperia-tone-dora.dtb` (F8131/F8132 v3.0 PM8996)
4. `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` (F8131/F8132 v3.0 PMI8996)

Size Constraint: Total boot partition budget is 30 MiB (31,457,280 bytes).
- Actual `Image.gz-dtb` size: **13,338,380 bytes (~12.72 MiB)**
- Sony S1 Budget Utilization: **42.4%** (well within the 30 MiB safety threshold).

## 5. Artifact Hashes & Sizes

| Artifact | Size (bytes) | SHA-256 Checksum |
| :--- | :--- | :--- |
| `vmlinux` | 43,330,248 | `51c0656927fa0fee2b8df53f71fa4cf64cf15ccece780fddd0450af8ed75124b` |
| `System.map` | 6,799,737 | `f54dcb4dde7a5cee3dfc628286dda514674d46cf14a12c706c454ec4ad4f433f` |
| `Image` | 34,613,760 | `b4f5c12efc42aba80acdad8ab8e0f370bdedba57cb7df89ac687d20bb69089c8` |
| `Image.gz` | 13,043,312 | `e5f5b4660d2d79fa84cd368be5e8e539e40b58e412f54a1ad8abe355807067f5` |
| `Image.gz-dtb` | 13,338,380 | `5082998f4c7163a700ee092f03d8a00d8fe9c5cac8a45cf6bd172d3d8a907ad7` |
| `msm8996-sony-xperia-tone-dora.dtb` | 73,505 | `51edc31ebcafd9951c4a5edd0ab0b99b6306ee8f87dc5cab4b22215ce815fb03` |
| `msm8996-pmi8996-sony-xperia-tone-dora.dtb` | 73,537 | `206e37678f6f10a220665e913dfbf3945eb50165ed655c9143ea478f8ef9cfad` |
| `msm8996-v3.0-sony-xperia-tone-dora.dtb` | 73,997 | `c48ef86902a2adbacbd05348e5f73c7a2a41e6b09789e6ed452ffcb9c2bdb7e0` |
| `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` | 74,029 | `344600c25eb391147bd6242e4dc484e5c502230340c8bc16fcc3ade58467cdf0` |
