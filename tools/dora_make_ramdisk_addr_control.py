#!/usr/bin/env python3
"""Create a controlled 4.4 Sony dora boot image modifying ONLY ramdisk_addr.

The baseline is the already-boot-tested Sony Linux 4.4 boot image with
canonical AOSP id from the preceding experiment. We change the legacy v0
header ramdisk_addr from 0x81000000 to 0x82800000 to test whether Sony S1
uses that address, or instead fixes up initrd at 0x82200000 regardless.

NO FLASH, NO REBOOT, NO DEVICE ACCESS. Use only as a controlled TWRP test.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

BASE = Path("/mnt/c/platform-tools")
WORKING = BASE / "dora-boot-reference.img"
SOURCE = BASE / "dora44-id-control/dora44-aosp-id-full64M.img"
DEST = BASE / "dora44-ramdisk-addr-control"
SOURCE_SHA = "94de94c399ef0bee51c3fe5ce83dbb575d46487f05a988e7d1f3f5826c7d9aa6"
WORKING_SHA = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
EXPECTED_CANONICAL_ID = bytes.fromhex("889c051b73d4f5cc6c04e1104f41b6155a6ea107") + b"\0"*12
EXPECTED_RAMDISK_SIZE = 0x12b014
NEW_RAMDISK_ADDR = 0x82800000
PAGE_SIZE = 4096
PARTITION_BYTES = 64*1024*1024
ADDR_OFF = 20  # Android v0: ramdisk_addr, u32 LE at offset 0x14


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def stop(message):
    raise RuntimeError(message)


def main():
    if DEST.exists():
        stop(f"Refusing to overwrite existing output directory: {DEST}")
    original = WORKING.read_bytes()
    source = SOURCE.read_bytes()
    if len(original) != PARTITION_BYTES or digest(original) != WORKING_SHA:
        stop("Verified original working 4.4 boot backup is missing or corrupt")
    if len(source) != PARTITION_BYTES or digest(source) != SOURCE_SHA:
        stop("Previously boot-validated 4.4 ID-control source missing or corrupt")
    if source[:8] != b"ANDROID!" or original[:8] != b"ANDROID!":
        stop("Android legacy boot magic missing")
    if source[576:608] != EXPECTED_CANONICAL_ID:
        stop("4.4 AOSP canonical boot id differs from boot-validated control")
    if original[:576] != source[:576] or original[608:] != source[608:]:
        stop("Unexpected source delta outside the previously validated id field")

    ksize, kaddr, rsize, raddr, ssize, saddr, tags, page, unused, osver = struct.unpack_from("<10I", source, 8)
    if (ksize, kaddr, rsize, raddr, ssize, tags, page, unused) != (
            12047744, 0x80008000, EXPECTED_RAMDISK_SIZE,
            0x81000000, 0, 0x80000100, PAGE_SIZE, 0):
        stop("Unexpected boot header fields: refuse to patch")

    # 0x82800000..0x8292b014 remains below the known Sony splash region
    # at 0x83401000. The running device's actual map still takes priority.
    if NEW_RAMDISK_ADDR % (2*1024*1024):
        stop("New ramdisk address is not 2MiB aligned")
    if NEW_RAMDISK_ADDR + rsize > 0x83401000:
        stop("Proposed ramdisk range intersects known splash reservation")
    output = bytearray(source)
    struct.pack_into("<I", output, ADDR_OFF, NEW_RAMDISK_ADDR)
    if output[:ADDR_OFF] != source[:ADDR_OFF] or output[ADDR_OFF+4:] != source[ADDR_OFF+4:]:
        stop("Unexpected differences outside ramdisk_addr field")
    if digest(output) == SOURCE_SHA:
        stop("Control image identical to source")

    DEST.mkdir(parents=True)
    result = DEST / "dora44-ramdisk-addr-82800000-full64M.img"
    result.write_bytes(output)
    manifest = {
        "status": "CONTROL IMAGE ONLY; NOT FLASHED; BOOTABILITY UNTESTED",
        "purpose": "Test whether Sony S1 honors ramdisk_addr by using known bootable 4.4",
        "source_boot_sha256": SOURCE_SHA,
        "original_reference_boot_sha256": WORKING_SHA,
        "modified_offset": "0x14",
        "modified_length": 4,
        "old_ramdisk_addr": hex(raddr),
        "new_ramdisk_addr": hex(NEW_RAMDISK_ADDR),
        "expected_original_android_initrd_start": "0x82200000",
        "new_ramdisk_end_if_header_honored": hex(NEW_RAMDISK_ADDR + rsize),
        "only_modified_four_bytes": True,
        "new_full64_sha256": digest(output),
        "result_file": str(result),
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("=== DORA WORKING 4.4 RAMDISK ADDRESS CONTROL ===")
    print("Source 4.4 (boot-tested) SHA256:", SOURCE_SHA)
    print("Changed Android v0 header bytes: [0x14, 0x18) ONLY")
    print("ramdisk_addr from 0x81000000 to 0x82800000")
    print("The kernel, ramdisk bytes, DTBs, boot id, cmdline and partition tail are unchanged.")
    print("New full64 SHA256:", digest(output))
    print("Image:", result)
    print("Manifest:", DEST / "manifest.json")
    print("PASS: ramdisk address control generated; NOT FLASHED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, struct.error) as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(2)
