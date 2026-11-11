#!/usr/bin/env bash
# Explicit experimental DT-only build. Normal build.sh and image lists stay intact.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ -e "$root/.git" ]]; then
    [[ $(git -C "$root" branch --show-current) == kernel-4.19-701905467803253630 ]] || {
        printf 'Expected the existing Dora work branch.\n' >&2
        exit 1
    }
fi
: "${OUT_DIR:?Set OUT_DIR to an existing normal Dora build output with its four normal DTBs}"
out=$(cd -- "$OUT_DIR" && pwd -P)
[[ -f "$out/.config" ]] || { printf 'Missing normal .config: %s\n' "$out" >&2; exit 1; }
diag_out=${DIAGNOSTIC_OUT_DIR:-"$out/diagnostic-sdhc1"}
mkdir -p -- "$diag_out"
diag_out=$(cd -- "$diag_out" && pwd -P)
[[ "$diag_out" != "$out" && "$diag_out" != "$root" ]] || {
    printf 'Diagnostic O= must be separate from the normal output and source.\n' >&2
    exit 1
}
case "$diag_out/" in "$out/arch/"*)
    printf 'Diagnostic O= must stay outside the normal arch artifact tree.\n' >&2
    exit 1;;
esac
shopt -s dotglob nullglob
diag_entries=("$diag_out"/*)
shopt -u dotglob nullglob
if (( ${#diag_entries[@]} )); then
    printf 'Diagnostic O= must be fresh/empty; refusing reused files or descendant symlinks.\n' >&2
    exit 1
fi
export ARCH=arm64
export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
export CROSS_COMPILE_COMPAT=${CROSS_COMPILE_COMPAT:-arm-linux-gnueabi-}
export PYTHONDONTWRITEBYTECODE=1
for command in "${CROSS_COMPILE}gcc" python3 make sha256sum cmp; do
    command -v "$command" >/dev/null
done
bases=(
    msm8996-v3.0-sony-xperia-tone-dora
    msm8996-v3.0-pmi8996-sony-xperia-tone-dora
    msm8996-sony-xperia-tone-dora
    msm8996-v3.1-pmi8996-sony-xperia-tone-dora
)
normals=()
targets=()
pairs=()
for base in "${bases[@]}"; do
    normal="$out/arch/arm64/boot/dts/qcom/$base.dtb"
    [[ -f "$normal" ]] || { printf 'Missing normal DTB: %s\n' "$normal" >&2; exit 1; }
    target="qcom/diagnostic/$base-sdhc1-diagnostic.dtb"
    normals+=("$normal")
    targets+=("$target")
    pairs+=(--normal "$normal" --diagnostic "$diag_out/arch/arm64/boot/dts/$target")
done
doc="$root/Documentation/sony/dora-sdhc1-diagnostic.txt"
printf 'EXPERIMENTAL SDHC1-only DT build; source/hardware gates are not overridden.\nQualification record: %s\n' "$doc"
python3 - "$doc" <<'PY'
from pathlib import Path
import sys
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith('Source-qualification:'):
        print(line)
PY
python3 "$root/scripts/dora/check-sdhc1-diagnostic.py" --default-only "${normals[@]}" \
    --config "$out/.config" --selftest
baseline_inputs=("$out/.config" "${normals[@]}")
for artifact in vmlinux System.map arch/arm64/boot/Image arch/arm64/boot/Image.gz \
                arch/arm64/boot/Image.gz-dtb boot.img; do
    if [[ -e "$out/$artifact" ]]; then baseline_inputs+=("$out/$artifact"); fi
    [[ ! -e "$diag_out/$artifact" ]] || {
        printf 'Diagnostic output must not contain an existing kernel/boot artifact: %s\n' "$artifact" >&2
        exit 1
    }
done
baseline="$diag_out/normal-inputs.sha256"
sha256sum -- "${baseline_inputs[@]}" > "$baseline"
verify_normal_inputs() {
    build_result=$?
    trap - EXIT
    if ! sha256sum --check --status "$baseline"; then
        printf 'Normal config, DTBs or kernel/boot artifact changed during diagnostic build.\n' >&2
        build_result=1
    fi
    exit "$build_result"
}
trap verify_normal_inputs EXIT
cp -- "$out/.config" "$diag_out/.config"
make -C "$root" O="$diag_out" olddefconfig 2>&1 | tee "$diag_out/sdhc1-diagnostic-config.log"
cmp -- "$out/.config" "$diag_out/.config"
make -C "$root" O="$diag_out" -j"${JOBS:-4}" "${targets[@]}" \
    2>&1 | tee "$diag_out/sdhc1-diagnostic-build.log"
python3 "$root/scripts/dora/check-sdhc1-diagnostic.py" "${pairs[@]}" \
    --config "$diag_out/.config" --selftest --report "$diag_out/sdhc1-diagnostic-validation.json"
for artifact in vmlinux System.map arch/arm64/boot/Image arch/arm64/boot/Image.gz \
                arch/arm64/boot/Image.gz-dtb boot.img; do
    [[ ! -e "$diag_out/$artifact" ]] || { printf 'Unexpected diagnostic kernel/boot artifact: %s\n' "$artifact" >&2; exit 1; }
done
printf 'Built four isolated experimental DTBs in %s; no kernel/boot image was produced.\n' "$diag_out"
