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
BUILD_LOG="${BUILD_LOG:-${OUT_DIR}/build.log}"

# Sony S1 bootloader multi-DTB concatenation order matching Tone/Dora board_id/pmic_id
DORA_DTBS=(
  "qcom/msm8996-sony-xperia-tone-dora.dtb"
  "qcom/msm8996-pmi8996-sony-xperia-tone-dora.dtb"
  "qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb"
  "qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb"
)

mkdir -p "${OUT_DIR}"
exec > >(tee -a "${BUILD_LOG}") 2>&1

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

if [[ ! -f "${OUT_DIR}/.config" || "${FORCE_DEFCONFIG:-0}" == "1" ]]; then
  echo "[dora-build] Step 1/5: Generating clean .config from ${DEFCONFIG}..."
  make "${MAKE_ARGS[@]}" "${DEFCONFIG}"
else
  echo "[dora-build] Step 1/5: Verifying existing .config via olddefconfig..."
  make "${MAKE_ARGS[@]}" olddefconfig
fi

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

echo "[dora-build] Step 2/5: Compiling vmlinux, Image, Image.gz, modules, and Sony Dora DTBs (-j${JOBS})..."
make "${MAKE_ARGS[@]}" -j"${JOBS}" vmlinux Image Image.gz modules "${DORA_DTBS[@]}"

BOOT_DIR="${OUT_DIR}/arch/arm64/boot"
VMLINUX="${OUT_DIR}/vmlinux"
SYSTEM_MAP="${OUT_DIR}/System.map"
IMAGE_RAW="${BOOT_DIR}/Image"
IMAGE_GZ="${BOOT_DIR}/Image.gz"
IMAGE_GZ_DTB="${BOOT_DIR}/Image.gz-dtb"

echo "[dora-build] Step 3/5: Verifying ELF vmlinux, System.map, and ARM64 Image header..."
for req_file in "${VMLINUX}" "${SYSTEM_MAP}" "${IMAGE_RAW}" "${IMAGE_GZ}"; do
  if [[ ! -s "${req_file}" ]]; then
    echo "ERROR: Required build artifact missing or empty: ${req_file}" >&2
    exit 1
  fi
done

ARM64_MAGIC="$(dd if="${IMAGE_RAW}" bs=1 skip=56 count=4 2>/dev/null | od -An -tx1 | tr -d ' \n')"
if [[ "${ARM64_MAGIC}" != "41524d64" ]]; then
  echo "ERROR: Invalid ARM64 Image header magic at 0x38 (got ${ARM64_MAGIC}, expected 41524d64 'ARM\\x64')!" >&2
  exit 1
fi

echo "[dora-build] Step 4/5: Assembling Sony S1 multi-variant Image.gz-dtb..."
cp -f "${IMAGE_GZ}" "${IMAGE_GZ_DTB}"
for dtb in "${DORA_DTBS[@]}"; do
  dtb_path="${BOOT_DIR}/dts/${dtb}"
  if [[ ! -s "${dtb_path}" ]]; then
    echo "ERROR: Missing compiled DTB: ${dtb_path}" >&2
    exit 1
  fi
  dtb_magic="$(dd if="${dtb_path}" bs=1 count=4 2>/dev/null | od -An -tx1 | tr -d ' \n')"
  if [[ "${dtb_magic}" != "d00dfeed" ]]; then
    echo "ERROR: Invalid FDT magic in ${dtb_path} (got ${dtb_magic}, expected d00dfeed)!" >&2
    exit 1
  fi
  cat "${dtb_path}" >> "${IMAGE_GZ_DTB}"
done

echo "[dora-build] Step 5/5: Verifying Sony S1 64 MiB boot.img budget and staging modules..."
IMG_BYTES="$(stat -c '%s' "${IMAGE_GZ_DTB}")"
# BoardConfigCommon.mk sets BOARD_KERNEL_BASE=0x80000000,KERNEL_OFFSET=0x80000,TAGS_OFFSET=0x01E00000 (30 MiB)
MAX_BOOT_BUDGET=$((30 * 1024 * 1024))
if (( IMG_BYTES > MAX_BOOT_BUDGET )); then
  echo "ERROR: Image.gz-dtb (${IMG_BYTES} bytes) exceeds Sony S1 30 MiB tags offset (0x01E00000)!" >&2
  exit 1
fi

STAGE_MOD_DIR="${OUT_DIR}/modules_staging"
rm -rf "${STAGE_MOD_DIR}"
make "${MAKE_ARGS[@]}" INSTALL_MOD_PATH="${STAGE_MOD_DIR}" INSTALL_MOD_STRIP=1 modules_install

sha256sum \
  "${VMLINUX}" \
  "${SYSTEM_MAP}" \
  "${IMAGE_RAW}" \
  "${IMAGE_GZ}" \
  "${IMAGE_GZ_DTB}" \
  "${BOOT_DIR}/dts/qcom/"*tone-dora*.dtb | tee "${OUT_DIR}/SHA256SUMS.txt"

echo ""
echo "================================================================="
echo " Sony Xperia X Performance (F8131/F8132) Linux 5.15 Build Ready"
echo "================================================================="
ls -lh "${VMLINUX}" "${SYSTEM_MAP}" "${IMAGE_RAW}" "${IMAGE_GZ}" "${IMAGE_GZ_DTB}" "${BOOT_DIR}/dts/qcom/"*tone-dora*.dtb
