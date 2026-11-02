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
export PYTHONDONTWRITEBYTECODE=1
out=${OUT_DIR:-"$root/out/dora"}
mkdir -p -- "$out"
out=$(cd -- "$out" && pwd)
jobs=${JOBS:-4}
for command in "${CROSS_COMPILE}gcc" "${CROSS_COMPILE_COMPAT}gcc" python3 make bison flex bc; do
    command -v "$command" >/dev/null
done
{
    if [[ -e "$root/.git" ]]; then
        git -C "$root" rev-parse HEAD
    else
        printf 'Unversioned source archive\n'
    fi
    "${CROSS_COMPILE}gcc" --version
    "${CROSS_COMPILE_COMPAT}gcc" --version
    "${CROSS_COMPILE}ld" --version
    "${CROSS_COMPILE_COMPAT}ld" --version
    "${HOSTCC:-gcc}" --version
    bison --version
    flex --version
} > "$out/toolchain.txt"
make -C "$root" O="$out" tone_dora_defconfig 2>&1 | tee "$out/config.log"
python3 "$root/scripts/dora/check-config.py" "$out/.config"
python3 "$root/scripts/dora/check-cpu-clocks.py" --config "$out/.config"
python3 "$root/scripts/dora/check-panels.py"
python3 "$root/scripts/dora/check-panel-selection.py"
python3 "$root/scripts/dora/check-ufs-phy.py"
python3 "$root/scripts/dora/check-ufs-host.py"
python3 "$root/scripts/dora/check-ufs-power.py"
python3 "$root/scripts/dora/check-bus-routes.py"
python3 "$root/scripts/dora/check-rpm-bus.py"
python3 "$root/scripts/dora/check-bus-retry.py"
python3 "$root/scripts/dora/check-genpd-regulator.py"
python3 "$root/scripts/dora/check-pmic-mpp.py"
# Preserve the original Sony Dora Makefile ordering. This does not prove
# that a particular Sony bootloader selects an appended DTB correctly.
dtbs=(
    qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb
    qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb
    qcom/msm8996-sony-xperia-tone-dora.dtb
    qcom/msm8996-v3.1-pmi8996-sony-xperia-tone-dora.dtb
)
make -C "$root" O="$out" -j"$jobs" Image.gz "${dtbs[@]}" 2>&1 | tee "$out/build.log"
trees=()
artifact_args=()
for dtb in "${dtbs[@]}"; do
    tree="$out/arch/arm64/boot/dts/$dtb"
    trees+=("$tree")
    artifact_args+=(--dtb "$dtb")
    python3 "$root/scripts/dora/check-dt.py" "$tree"
    python3 "$root/scripts/dora/check-cpu-dt.py" "$tree" --selftest
    python3 "$root/scripts/dora/check-power-dt.py" "$tree"
    python3 "$root/scripts/dora/check-pin-safety.py" "$tree" --self-test
    python3 "$root/scripts/dora/check-bus-dt.py" "$tree" --selftest
    python3 "$root/scripts/dora/check-display-dt.py" "$tree" --config "$out/.config" --require-gated --self-test
done
python3 "$root/scripts/dora/check-variants.py" "${trees[@]}" --require-four --selftest
# An explicit list excludes stale or unrelated DTBs in O=.
image="$out/arch/arm64/boot/Image.gz"
cat -- "$image" "${trees[@]}" > "$out/arch/arm64/boot/Image.gz-dtb"
python3 "$root/scripts/dora/check-artifacts.py" "$out" "${artifact_args[@]}" \
    --report "$out/artifact-validation.json"
sha256sum "$out/.config" "$out/vmlinux" "$out/System.map" "$out/arch/arm64/boot/Image" \
    "$image" "${trees[@]}" "$out/arch/arm64/boot/Image.gz-dtb" \
    > "$out/SHA256SUMS"
printf 'Built safety-gated bring-up artifacts in %s; not boot-test ready.\n' "$out"
