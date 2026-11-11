#!/usr/bin/env bash
# Read-only host preflight for the optional dora_stage_reader.ko.
# Sony Xperia X Performance / TWRP 3.18; no reboot, insmod, dd, or flashing.
set -euo pipefail

ADB=/mnt/c/platform-tools/adb.exe
BASE=/mnt/c/platform-tools
if [[ ! -f "$ADB" ]]; then
  echo "STOP: adb.exe missing: $ADB" >&2
  exit 2
fi
if [[ ! -d "$BASE" ]]; then
  echo "STOP: output parent missing: $BASE" >&2
  exit 2
fi

echo "=== CONNECTED DEVICE ==="
"$ADB" get-state
RECOVERY="$("$ADB" shell uname -r | tr -d '\r\n')"
echo "Kernel: $RECOVERY"
if [[ "$RECOVERY" != 3.18.* ]]; then
  echo "STOP: requires TWRP 3.18, not Android/other recovery."
  echo "Phone was not modified. Boot into TWRP separately if desired."
  exit 2
fi

OUT="$(mktemp -d "$BASE/dora-stage-reader-preflight-XXXXXXXX")"
echo "Output: $OUT"

echo "=== RECOVERY BUILD INFORMATION ==="
"$ADB" shell 'uname -a; cat /proc/version' |
  tr -d '\r' | tee "$OUT/recovery-version.txt"

echo "=== PHYSICAL READER PREREQUISITES (READ-ONLY) ==="
"$ADB" shell '
  echo "Paths:"
  for p in /dev/mem /dev/pmsg0 /proc/config.gz /proc/kallsyms /sys/fs/pstore; do
    if [ -e "$p" ]; then
      ls -ld "$p"
    else
      echo "NOT_PRESENT: $p"
    fi
  done
  echo "Module state:"
  if [ -r /proc/modules ]; then cat /proc/modules; else echo "proc/modules unreadable"; fi
  echo "Pstore entries:"
  if [ -d /sys/fs/pstore ]; then ls -la /sys/fs/pstore; fi
' | tr -d '\r' > "$OUT/recovery-features.txt"
cat "$OUT/recovery-features.txt"

echo "=== RAM LAYOUT (RECOVERY) ==="
if "$ADB" shell 'cat /proc/iomem' > "$OUT/recovery-iomem.txt"; then
  tr -d '\r' < "$OUT/recovery-iomem.txt" |
    grep -Ei 'System RAM|reserved|ramoops|Kernel code|Kernel data' || true
else
  echo "INFO: could not read /proc/iomem"
fi

echo "=== RUNNING RECOVERY CONFIG ==="
if "$ADB" shell 'test -r /proc/config.gz'; then
  if "$ADB" exec-out cat /proc/config.gz > "$OUT/recovery-config.gz" &&
     gzip -t "$OUT/recovery-config.gz"; then
    gzip -dc "$OUT/recovery-config.gz" > "$OUT/recovery-config.txt"
    grep -E '^(CONFIG_(MODULES|MODVERSIONS|MODULE_SIG|MODULE_SIG_FORCE|IKCONFIG|STRICT_DEVMEM|DEVMEM|PROC_FS|KALLSYMS)=|# CONFIG_(MODULES|MODVERSIONS|MODULE_SIG|MODULE_SIG_FORCE|IKCONFIG|STRICT_DEVMEM|DEVMEM|PROC_FS|KALLSYMS) is not set)' \
      "$OUT/recovery-config.txt" || true
  else
    echo "WARNING: /proc/config.gz could not be transferred as valid gzip"
    rm -f "$OUT/recovery-config.gz"
  fi
else
  echo "INFO: /proc/config.gz not readable. Exact TWRP configuration still required."
fi

echo "=== HOST CROSS-COMPILERS ==="
for exe in aarch64-linux-gnu-gcc aarch64-linux-gnu-ld clang gcc make; do
  command -v "$exe" || echo "NOT_FOUND: $exe"
done

echo "=== ASSESSMENT ==="
if [[ -f "$OUT/recovery-config.txt" ]] &&
   grep -qx 'CONFIG_MODULES=y' "$OUT/recovery-config.txt"; then
  echo "Module support enabled; loading remains unverified."
else
  echo "Module support NOT verified; do not build/load dora_stage_reader yet."
fi
echo "A compatible complete TWRP 3.18 build and Module.symvers may be needed."
echo "No physical RAM was read. No kernel module was inserted."
echo "No partitions, firmware, boot images, or phone settings were modified."
echo "PASS: recovery preflight complete"
