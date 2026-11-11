#!/usr/bin/env python3
"""Create an unflashed 4.4 boot-control image changing ONLY Android v0 id[8].

Purpose: determine whether Sony bootloader accepts an AOSP-computed v0 SHA-1
for a KNOWN-WORKING LineageOS 21 Linux 4.4 boot image. This script NEVER
communicates with a device and NEVER flashes anything.
"""
import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

BASE = Path("/mnt/c/platform-tools")
REF = BASE / "dora-boot-reference.img"
ROM = Path.home() / "android/lineage/out/target/product/dora/lineage-21.0-20261030-UNOFFICIAL-dora.zip"
DEST = BASE / "dora44-id-control"
REF_SHA = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
ZIP_SHA = "f923f9b99c468484b713529577b84e3a137d1b9cbc09393e5593258009983820"
EXPECTED_OLD_ID = bytes.fromhex("26019fa18d9ecd502b29199901c11535675606d9") + b"\0" * 12
EXPECTED_AOSP_ID = bytes.fromhex("889c051b73d4f5cc6c04e1104f41b6155a6ea107") + b"\0" * 12
ID_START, ID_END = 576, 608
PARTITION_BYTES = 64 * 1024 * 1024


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def align(n, a):
    return (n + a - 1) // a * a


def stop(message):
    raise ValueError(message)


def main():
    if DEST.exists():
        stop(f"Output directory exists (will not overwrite): {DEST}")
    original = REF.read_bytes()
    if len(original) != PARTITION_BYTES or digest(original) != REF_SHA:
        stop("Working 4.4 partition backup differs from verified original")
    with zipfile.ZipFile(ROM) as z:
        compact = z.read("boot.img")
    if digest(compact) != ZIP_SHA or original[:len(compact)] != compact:
        stop("LOS21 ZIP boot.img does not match working 4.4 partition prefix")
    if original[:8] != b"ANDROID!":
        stop("Unexpected boot magic")
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags_addr, page, ver, osver = struct.unpack_from("<10I", original, 8)
    if (kernel_size, ramdisk_size, second_size, page, ver, kernel_addr, ramdisk_addr, tags_addr) != (
        12047744, 1224724, 0, 4096, 0, 0x80008000, 0x81000000, 0x80000100):
        stop("Unexpected legacy Sony boot header")
    kernel_offset = page
    ramdisk_offset = page + align(kernel_size, page)
    end = ramdisk_offset + align(ramdisk_size, page)
    if end != len(compact):
        stop("Unexpected boot payload size")
    kernel = original[kernel_offset:kernel_offset + kernel_size]
    ramdisk = original[ramdisk_offset:ramdisk_offset + ramdisk_size]
    sha1 = hashlib.sha1()
    for item in (kernel, ramdisk, b""):
        sha1.update(item)
        sha1.update(struct.pack("<I", len(item)))
    proposed_id = sha1.digest() + b"\0" * 12
    old_id = original[ID_START:ID_END]
    if old_id != EXPECTED_OLD_ID or proposed_id != EXPECTED_AOSP_ID:
        stop("Source IDs or computed AOSP ID do not match previous audit")
    if old_id == proposed_id:
        stop("Original and AOSP ID already match; control test would be invalid")
    out = bytearray(original)
    out[ID_START:ID_END] = proposed_id
    # Assert byte-for-byte identity everywhere other than id[8].
    if bytes(out[:ID_START]) != original[:ID_START] or bytes(out[ID_END:]) != original[ID_END:]:
        stop("Unexpected change outside v0 header id[8]")
    full = DEST / "dora44-aosp-id-full64M.img"
    manifest = DEST / "dora44-aosp-id-control.json"
    DEST.mkdir(parents=True)
    full.write_bytes(out)
    info = {
        "status": "CONTROL IMAGE ONLY - NEVER BOOT VALIDATED",
        "purpose": "Change only 32-byte Android v0 header id of known-working Linux 4.4",
        "original_full64_sha256": digest(original),
        "original_zip_boot_sha256": digest(compact),
        "new_full64_sha256": digest(out),
        "only_changed_byte_range": [ID_START, ID_END],
        "old_id": old_id.hex(),
        "new_id": proposed_id.hex(),
        "all_bytes_outside_id_unchanged": True,
        "boot_image_size": len(compact),
        "partition_size": len(out),
    }
    manifest.write_text(json.dumps(info, indent=2) + "\n")
    print("=== AOSP SHA-1 ID CONTROL FOR WORKING LINUX 4.4 ===")
    print("Original boot partition SHA256:", REF_SHA)
    print("Original header id:", old_id.hex())
    print("AOSP header id:    ", proposed_id.hex())
    print("Changed region:     [0x240, 0x260) (id field ONLY)")
    print("NEW full64 SHA256:  ", digest(out))
    print("Image:", full)
    print("Manifest:", manifest)
    print("PASS: verified 4.4 id-only control image generated; NOT FLASHED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile, struct.error) as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(2)
