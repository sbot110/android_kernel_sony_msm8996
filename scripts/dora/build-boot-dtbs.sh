#!/usr/bin/env bash
# Build four explicit LOS21 SDHC1 trees; never write an image or a device.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ -e "$root/.git" ]]; then
    [[ $(git -C "$root" branch --show-current) == kernel-4.19-701905467803253630 ]] || {
        printf 'Expected the existing Dora work branch.\n' >&2
        exit 1
    }
fi
: "${OUT_DIR:?Set OUT_DIR to a normal Dora build with its .config and four normal DTBs}"
out=$(cd -- "$OUT_DIR" && pwd -P)
[[ -f "$out/.config" ]] || { printf 'Missing normal .config: %s\n' "$out" >&2; exit 1; }
boot_out=${BOOT_OUT_DIR:-"$out/lineage21-boot"}
mkdir -p -- "$boot_out"
boot_out=$(cd -- "$boot_out" && pwd -P)
[[ "$boot_out" != "$out" && "$boot_out" != "$root" ]] || {
    printf 'Boot DT output must be separate from normal/source roots.\n' >&2
    exit 1
}
case "$boot_out/" in "$out/arch/"*)
    printf 'Boot DT output must stay outside the normal arch artifact tree.\n' >&2
    exit 1;;
esac
shopt -s dotglob nullglob
boot_entries=("$boot_out"/*)
shopt -u dotglob nullglob
if (( ${#boot_entries[@]} )); then
    printf 'Boot DT output must be fresh/empty; refusing reused or aliased files.\n' >&2
    exit 1
fi
export ARCH=arm64
export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
export CROSS_COMPILE_COMPAT=${CROSS_COMPILE_COMPAT:-arm-linux-gnueabi-}
export PYTHONDONTWRITEBYTECODE=1
for command in "${CROSS_COMPILE}gcc" python3 make sha256sum cmp; do
    command -v "$command" >/dev/null
done
# Exact appended order in the supplied working Sony/LOS21 boot image.
# The regular build's historical ordering is deliberately untouched.
bases=(
    msm8996-v3.0-sony-xperia-tone-dora
    msm8996-sony-xperia-tone-dora
    msm8996-v3.0-pmi8996-sony-xperia-tone-dora
    msm8996-v3.1-pmi8996-sony-xperia-tone-dora
)
normals=()
targets=()
pairs=()
for base in "${bases[@]}"; do
    normal="$out/arch/arm64/boot/dts/qcom/$base.dtb"
    [[ -f "$normal" ]] || { printf 'Missing normal DTB: %s\n' "$normal" >&2; exit 1; }
    target="qcom/boot/$base-lineage21-boot.dtb"
    normals+=("$normal")
    targets+=("$target")
    pairs+=(--normal "$normal" --boot "$boot_out/arch/arm64/boot/dts/$target")
done
baseline_inputs=("$out/.config" "${normals[@]}")
for artifact in vmlinux System.map arch/arm64/boot/Image arch/arm64/boot/Image.gz \
                arch/arm64/boot/Image.gz-dtb boot.img; do
    if [[ -e "$out/$artifact" ]]; then baseline_inputs+=("$out/$artifact"); fi
done
baseline="$boot_out/normal-inputs.sha256"
sha256sum -- "${baseline_inputs[@]}" > "$baseline"
verify_normal_inputs() {
    build_result=$?
    trap - EXIT
    if ! sha256sum --check --status "$baseline"; then
        printf 'Normal config/DTBs/kernel artifacts changed during boot DT workflow.\n' >&2
        build_result=1
    fi
    exit "$build_result"
}
trap verify_normal_inputs EXIT
cp -- "$out/.config" "$boot_out/.config"
make -C "$root" O="$boot_out" olddefconfig 2>&1 | tee "$boot_out/boot-dtb-config.log"
cmp -- "$out/.config" "$boot_out/.config"
make -C "$root" O="$boot_out" -j"${JOBS:-4}" "${targets[@]}" \
    2>&1 | tee "$boot_out/boot-dtb-build.log"
python3 "$root/scripts/dora/check-first-stage-init.py" "${pairs[@]}" \
    --config "$boot_out/.config" --self-test --report "$boot_out/boot-dtb-validation.json"
python3 - "$boot_out/boot-dtb-validation.json" "$boot_out/boot-dtb-order.json" <<'PY'
import json
from pathlib import Path
import sys
report = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps({
    'order_source': 'supplied working Sony/LOS21 image selection tuples',
    'status': 'SOFTWARE_CHECKED / HARDWARE_UNVERIFIED',
    'boot_test_ready': False,
    'dtbs': report['boot_dtb_order'],
}, indent=2) + '\n')
PY
for artifact in vmlinux System.map arch/arm64/boot/Image arch/arm64/boot/Image.gz \
                arch/arm64/boot/Image.gz-dtb boot.img; do
    [[ ! -e "$boot_out/$artifact" ]] || {
        printf 'Unexpected kernel/boot image in DT-only workflow: %s\n' "$artifact" >&2
        exit 1
    }
done
printf 'Built four isolated LOS21 SDHC1 DTBs in %s; hardware boot remains unverified.\n' "$boot_out"
