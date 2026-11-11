#!/usr/bin/env bash
# Read-only Sony Dora FOTAKernel backup through TWRP 3.18 ADB.
# ADB exec-out can merge remote dd stderr with stdout: redirect remote stderr.
set -euo pipefail

ADB=/mnt/c/platform-tools/adb.exe
PART=/dev/block/bootdevice/by-name/FOTAKernel
DEST=/mnt/d/dora-FOTAKernel-twrp318-original.img

if [[ ! -x "$ADB" ]]; then
  echo "STOP: adb.exe missing" >&2
  exit 1
fi
if [[ -e "$DEST" || -e "$DEST.sha256" ]]; then
  echo "STOP: destination or sidecar already exists; no overwrites: $DEST" >&2
  exit 1
fi

echo '=== VERIFY TWRP ==='
RELEASE=$("$ADB" shell uname -r | tr -d '\r\n')
echo "$RELEASE"
[[ "$RELEASE" == 3.18.* ]] || { echo 'STOP: not running TWRP 3.18'; exit 1; }

TARGET=$("$ADB" shell "readlink -f $PART" | tr -d '\r\n')
echo "FOTAKernel target: $TARGET"
[[ "$TARGET" == /dev/block/mmcblk0p45 ]] || {
  echo 'STOP: unexpected FOTAKernel target; do not read guessed partition'
  exit 1
}

SECTORS=$("$ADB" shell 'cat /sys/class/block/mmcblk0p45/size' | tr -dc '0-9')
[[ "$SECTORS" =~ ^[0-9]+$ ]] || { echo 'STOP: missing partition size'; exit 1; }
EXPECTED=$((SECTORS * 512))
[[ "$EXPECTED" -eq 67108864 ]] || { echo "STOP: unexpected partition size $EXPECTED"; exit 1; }

TMP=$(mktemp /mnt/d/.dora-fota-XXXXXXXX.img)
trap 'rm -f "$TMP"' EXIT

echo '=== READ FULL PARTITION (REMOTE dd STATUS SUPPRESSED) ==='
"$ADB" exec-out "dd if=$PART bs=4096 2>/dev/null" > "$TMP"
ACTUAL=$(stat -c%s "$TMP")
echo "Bytes read: $ACTUAL of $EXPECTED"
[[ "$ACTUAL" -eq "$EXPECTED" ]] || {
  echo 'STOP: binary ADB stream size mismatch; backup not saved'
  exit 1
}

echo '=== CHECK LOCAL VS DEVICE SHA-256 ==='
LOCAL=$(sha256sum "$TMP" | awk '{print $1}')
REMOTE=$("$ADB" shell "sha256sum $PART" | tr -d '\r' | awk '{print $1}')
echo "Local:  $LOCAL"
echo "Device: $REMOTE"
[[ "$LOCAL" =~ ^[0-9a-f]{64}$ && "$LOCAL" == "$REMOTE" ]] || {
  echo 'STOP: checksum mismatch; backup not saved'
  exit 1
}

mv "$TMP" "$DEST"
printf '%s  %s\n' "$REMOTE" "$DEST" > "$DEST.sha256"
echo "PASS: read-only FOTAKernel backup verified: $DEST"
echo "PASS: checksum sidecar: $DEST.sha256"
echo 'No partition was written; recovery is unchanged.'
