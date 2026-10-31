#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Reproducible Linux 5.15.x Build & Sony S1 Image.gz-dtb Packaging Script
# Target: Sony Xperia X Performance (dora: F8131 Single SIM / F8132 Dual SIM)
# OS:     LineageOS 21 (Android 14) / LineageOS 22 (Android 15)
# Host:   WSL2 Ubuntu 22.04 LTS or Linux x86_64
#
# Supports:
#   1) AOSP / LineageOS Clang + LLD (recommended: LLVM=1 LLVM_IAS=1)
#   2) GNU ARM64 Cross Toolchain fallback (CROSS_COMPILE=aarch64-linux-gnu-)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${OUT_DIR:-${KERNEL_DIR}/out}"
DEFCONFIG="${DEFCONFIG:-dora_defconfig}"
JOBS="${JOBS:-$(nproc)}"
ENABLE_BTF="${ENABLE_BTF:-0}"

DORA_DTBS=(
  "qcom/msm8996-sony-xperia-tone-dora.dtb"
  "qcom/msm8996-pmi8996-sony-xperia-tone-dora.dtb"
  "qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb"
  "qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb"
)

MAKE_ARGS=(
  -C "${KERNEL_DIR}"
  O="${OUT_DIR}"
  ARCH=arm64
)

if [[ "${USE_GCC:-0}" != "1" ]] && command -v clang >/dev/null 2>&1 && command -v ld.lld >/dev/null 2>&1; then
  echo "[dora-build] Toolchain: Clang/LLVM ($(clang --version | head -n1))"
  MAKE_ARGS+=(
    LLVM=1
    LLVM_IAS=1
    CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
  )
elif command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
  echo "[dora-build] Toolchain: GNU GCC ($(aarch64-linux-gnu-gcc --version | head -n1))"
  MAKE_ARGS+=(
    CROSS_COMPILE=aarch64-linux-gnu-
  )
else
  echo "ERROR: Neither clang+ld.lld nor aarch64-linux-gnu-gcc found in PATH." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

echo "[dora-build] Step 1/4: Generating .config from ${DEFCONFIG}..."
make "${MAKE_ARGS[@]}" "${DEFCONFIG}"

if [[ "${ENABLE_BTF}" == "1" ]]; then
  if command -v pahole >/dev/null 2>&1; then
    echo "[dora-build] Enabling CONFIG_DEBUG_INFO_BTF=y (pahole detected)..."
    "${KERNEL_DIR}/scripts/config" --file "${OUT_DIR}/.config" \
      -e DEBUG_INFO \
      -e DEBUG_INFO_DWARF4 \
      -e DEBUG_INFO_BTF
    make "${MAKE_ARGS[@]}" olddefconfig
  else
    echo "WARNING: ENABLE_BTF=1 requested, but 'pahole' (dwarves) is not installed." >&2
  fi
fi

echo "[dora-build] Step 2/4: Compiling Image.gz, modules, and Sony Dora DTBs (-j${JOBS})..."
make "${MAKE_ARGS[@]}" -j"${JOBS}" Image.gz modules "${DORA_DTBS[@]}"

BOOT_DIR="${OUT_DIR}/arch/arm64/boot"
IMAGE_GZ="${BOOT_DIR}/Image.gz"
IMAGE_GZ_DTB="${BOOT_DIR}/Image.gz-dtb"

echo "[dora-build] Step 3/4: Assembling Sony S1 multi-variant Image.gz-dtb..."
cp -f "${IMAGE_GZ}" "${IMAGE_GZ_DTB}"
for dtb in "${DORA_DTBS[@]}"; do
  dtb_path="${BOOT_DIR}/dts/${dtb}"
  if [[ ! -f "${dtb_path}" ]]; then
    echo "ERROR: Missing compiled DTB: ${dtb_path}" >&2
    exit 1
  fi
  cat "${dtb_path}" >> "${IMAGE_GZ_DTB}"
done

echo "[dora-build] Step 4/4: Verifying Sony S1 64 MiB boot.img budget..."
IMG_BYTES="$(stat -c '%s' "${IMAGE_GZ_DTB}")"
MAX_BOOT_BUDGET=$((48 * 1024 * 1024)) # Leave >=16 MiB headroom for XZ ramdisk inside 64 MiB boot
if (( IMG_BYTES > MAX_BOOT_BUDGET )); then
  echo "ERROR: Image.gz-dtb (${IMG_BYTES} bytes) exceeds safe 48 MiB budget for 64 MiB boot partition!" >&2
  exit 1
fi

STAGE_MOD_DIR="${OUT_DIR}/modules_staging"
rm -rf "${STAGE_MOD_DIR}"
make "${MAKE_ARGS[@]}" INSTALL_MOD_PATH="${STAGE_MOD_DIR}" INSTALL_MOD_STRIP=1 modules_install

echo ""
echo "================================================================="
echo " Sony Xperia X Performance (F8131/F8132) Linux 5.15 Build Ready"
echo "================================================================="
ls -lh "${IMAGE_GZ}" "${IMAGE_GZ_DTB}" "${BOOT_DIR}/dts/qcom/"*tone-dora*.dtb
sha256sum "${IMAGE_GZ_DTB}" "${BOOT_DIR}/dts/qcom/"*tone-dora*.dtb
