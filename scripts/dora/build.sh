#!/usr/bin/env bash
# Standalone dora bring-up build; never switches branches or flashes a device.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
branch=kernel-4.19-701905467803253630
if [[ -e "$root/.git" ]]; then
    actual_branch=$(git -C "$root" branch --show-current)
    [[ "$actual_branch" == "$branch" ]] || {
        printf 'Expected existing branch %s, found %s\n' "$branch" "$actual_branch" >&2
        exit 1
    }
fi
export ARCH=arm64
export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
export CROSS_COMPILE_COMPAT=${CROSS_COMPILE_COMPAT:-arm-linux-gnueabi-}
out=${OUT_DIR:-"$root/out/dora"}
mkdir -p -- "$out"
out=$(cd -- "$out" && pwd)
jobs=${JOBS:-4}
for command in "${CROSS_COMPILE}gcc" "${CROSS_COMPILE_COMPAT}gcc" python3 make; do
    command -v "$command" >/dev/null
done
{
    git -C "$root" rev-parse HEAD 2>/dev/null || true
    "${CROSS_COMPILE}gcc" --version
    "${CROSS_COMPILE_COMPAT}gcc" --version
    "${CROSS_COMPILE}ld" --version
} > "$out/toolchain.txt"
make -C "$root" O="$out" tone_dora_defconfig 2>&1 | tee "$out/config.log"
python3 "$root/scripts/dora/check-config.py" "$out/.config"
dtb=qcom/msm8996-sony-xperia-tone-dora.dtb
make -C "$root" O="$out" -j"$jobs" Image.gz "$dtb" 2>&1 | tee "$out/build.log"
# Explicit list avoids accidentally appending unrelated/stale DTBs from O=.
image="$out/arch/arm64/boot/Image.gz"
tree="$out/arch/arm64/boot/dts/$dtb"
cat -- "$image" "$tree" > "$out/arch/arm64/boot/Image.gz-dtb"
sha256sum "$image" "$tree" "$out/arch/arm64/boot/Image.gz-dtb" \
    > "$out/SHA256SUMS"
printf 'Built bring-up artifacts in %s; hardware readiness is not established.\n' "$out"
