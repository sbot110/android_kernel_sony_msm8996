#!/usr/bin/env python3
"""Prepare an UNFLASHED Android v0 test boot.img for Sony Xperia X Performance.

The reference must be a verified full 64 MiB boot-partition backup.
Preserve original ramdisk, boot command line, addresses, and header bytes.
Replace only kernel_size, kernel payload, and 32-byte header SHA-1 id.
Refuse unexpected layout, nonzero trailing bytes, wrong reference SHA-256.
This script NEVER communicates with the phone or flashes anything.
"""
import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

REFERENCE_SHA256 = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
PARTITION_BYTES = 64 * 1024 * 1024
ID_START = 576
ID_END = 608


def stop(reason):
    raise ValueError(reason)


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def aligned(n, page):
    return (n + page - 1) // page * page


def boot_id(kernel, ramdisk, second=b""):
    """Android legacy v0 boot image ID: SHA1 of each payload followed by its LE32 size."""
    hasher = hashlib.sha1()
    for item in (kernel, ramdisk, second):
        hasher.update(item)
        hasher.update(struct.pack("<I", len(item)))
    return hasher.digest() + b"\x00" * 12


def parse_boot(raw):
    if len(raw) < 4096 or raw[:8] != b"ANDROID!":
        stop("Not a legacy Android boot image")
    keys = (
        "kernel_size", "kernel_addr", "ramdisk_size", "ramdisk_addr",
        "second_size", "second_addr", "tags_addr", "page_size",
        "unused", "os_version",
    )
    b = dict(zip(keys, struct.unpack_from("<10I", raw, 8)))
    page = b["page_size"]
    if page != 4096 or b["unused"] != 0 or b["second_size"]:
        stop("Unexpected boot header: expected page_size 4096, unused=0, no second-stage")
    addresses = (b["kernel_addr"], b["ramdisk_addr"], b["tags_addr"])
    if addresses != (0x80008000, 0x81000000, 0x80000100):
        stop("Unexpected Sony boot addresses; refusing to change an unknown reference")
    if not b["kernel_size"] or not b["ramdisk_size"]:
        stop("Missing kernel or ramdisk")
    koff = page
    roff = koff + aligned(b["kernel_size"], page)
    end = roff + aligned(b["ramdisk_size"], page)
    if end > len(raw):
        stop("Original payload goes beyond reference file")
    b["kernel"] = raw[koff:koff + b["kernel_size"]]
    b["ramdisk"] = raw[roff:roff + b["ramdisk_size"]]
    b["ramdisk_offset"] = roff
    b["end_offset"] = end
    b["header"] = raw[:page]
    b["id_matches"] = raw[ID_START:ID_END] == boot_id(b["kernel"], b["ramdisk"])
    b["tail_is_zero"] = not any(raw[end:])
    b["cmdline"] = (
        raw[64:576].split(b"\x00", 1)[0] +
        raw[608:1632].split(b"\x00", 1)[0]
    ).decode("utf-8", "replace")
    return b


def verify_kernel(packed, image):
    if not packed.startswith(b"\x1f\x8b"):
        stop("5.15 kernel payload must begin with gzip")
    dec = zlib.decompressobj(31)
    uncompressed = dec.decompress(packed) + dec.flush()
    if not dec.eof or uncompressed != image:
        stop("Image.gz does not fully decompress to the provided Image")
    if len(image) < 64:
        stop("ARM64 Image too short")
    offset, reserved, flags = struct.unpack_from("<QQQ", image, 8)
    magic, = struct.unpack_from("<I", image, 0x38)
    if magic != 0x644d5241 or offset != 0x80000 or reserved < len(image):
        stop("ARM64 Image header invalid")
    dtb_bytes = dec.unused_data
    sizes = []
    pos = 0
    while pos + 8 <= len(dtb_bytes):
        magic, length = struct.unpack_from(">II", dtb_bytes, pos)
        if magic != 0xd00dfeed or length < 40 or pos + length > len(dtb_bytes):
            stop("Malformed DTB footer at offset %d" % pos)
        sizes.append(length)
        pos += length
    if pos != len(dtb_bytes) or len(sizes) != 4:
        stop("Expected precisely four appended DTBs, found %d" % len(sizes))
    return dict(text_offset=offset, image_size=reserved, file_size=len(image),
                flags=flags, appended_dtb_sizes=sizes)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", required=True, type=Path)
    p.add_argument("--kernel", required=True, type=Path)
    p.add_argument("--image", required=True, type=Path)
    p.add_argument("--outdir", required=True, type=Path)
    a = p.parse_args()

    original = a.reference.read_bytes()
    source_sha = sha256(original)
    if len(original) != PARTITION_BYTES:
        stop("Reference must be exactly 67108864 bytes (full boot partition)")
    if source_sha != REFERENCE_SHA256:
        stop("Reference SHA256 differs from the backup verified on the device")

    b = parse_boot(original)
    kernel = a.kernel.read_bytes()
    image = a.image.read_bytes()
    metadata = verify_kernel(kernel, image)

    print("=== ORIGINAL BOOT PARTITION ===")
    print("size:", len(original), "sha256:", source_sha)
    for key in ("kernel_size", "kernel_addr", "ramdisk_size", "ramdisk_addr",
                "tags_addr", "page_size", "unused", "os_version"):
        print("%s: %s" % (key, hex(b[key]) if key.endswith("addr") else b[key]))
    print("cmdline:", b["cmdline"])
    print("original boot ID matches canonical SHA-1:", b["id_matches"])
    print("bytes after original ramdisk all zero:", b["tail_is_zero"])

    if not b["id_matches"]:
        stop("Original image ID is not canonical v0 SHA-1; need to investigate before repacking")
    if not b["tail_is_zero"]:
        stop("Nonzero data after original ramdisk, possibly footer or Sony metadata; refusing to discard")

    print("=== NEW KERNEL ===")
    print("compressed kernel + DTBs:", len(kernel), "bytes")
    print("raw Image:", len(image), "bytes")
    print("ARM64 image_size:", hex(metadata["image_size"]))
    print("DTBs:", metadata["appended_dtb_sizes"])

    if len(kernel) >= 0x100000000:
        stop("New kernel too large for 32-bit kernel_size field")
    compressed_end = b["kernel_addr"] + len(kernel)
    if compressed_end > b["ramdisk_addr"]:
        stop("Compressed kernel would overlap original ramdisk load address")
    if b["ramdisk_addr"] + b["ramdisk_size"] > 0x100000000:
        stop("Ramdisk end overflows 32-bit physical address space")

    page = b["page_size"]
    header = bytearray(b["header"])
    struct.pack_into("<I", header, 8, len(kernel))
    header[ID_START:ID_END] = boot_id(kernel, b["ramdisk"])
    compact = (bytes(header) + kernel +
               b"\x00" * (aligned(len(kernel), page) - len(kernel)) +
               b["ramdisk"] +
               b"\x00" * (aligned(len(b["ramdisk"]), page) - len(b["ramdisk"])))
    if len(compact) > PARTITION_BYTES:
        stop("New boot image exceeds boot partition")
    check = parse_boot(compact)
    if not check["id_matches"] or check["ramdisk"] != b["ramdisk"]:
        stop("Repacked image did not preserve ramdisk / correct ID")
    if header[12:ID_START] != b["header"][12:ID_START] or header[ID_END:] != b["header"][ID_END:]:
        stop("Unexpected header mutation")
    full = compact + b"\x00" * (PARTITION_BYTES - len(compact))

    print("=== LAYOUT ===")
    print("old flash ramdisk offset:", hex(b["ramdisk_offset"]))
    print("new flash ramdisk offset:", hex(check["ramdisk_offset"]))
    print("compressed kernel load end:", hex(compressed_end))
    print("ramdisk load address:", hex(b["ramdisk_addr"]))
    print("compressed load gap:", b["ramdisk_addr"] - compressed_end, "bytes")
    print("IMPORTANT: actual post-decompression RAM placement remains UNVERIFIED")
    print("trimmed boot size:", len(compact))
    print("full partition size:", len(full))

    a.outdir.mkdir(parents=True, exist_ok=True)
    dest = (
        a.outdir / "dora-515-diagnostic-boot.img",
        a.outdir / "dora-515-diagnostic-boot-full64M.img",
        a.outdir / "dora-515-diagnostic-manifest.json",
    )
    if any(f.exists() for f in dest):
        stop("Output exists: refusing to overwrite any prior test artifacts")
    manifest = {
        "status": "PREFLIGHT ONLY - NOT BOOT VALIDATED",
        "reference_full64_sha256": source_sha,
        "original_ramdisk_sha256": sha256(b["ramdisk"]),
        "new_kernel_sha256": sha256(kernel),
        "image_sha256": sha256(image),
        "new_boot_trimmed_size": len(compact),
        "new_boot_trimmed_sha256": sha256(compact),
        "new_boot_full64_sha256": sha256(full),
        "new_ramdisk_flash_offset": check["ramdisk_offset"],
        "new_ramdisk_sha256": sha256(check["ramdisk"]),
        "boot_header": {key: b[key] for key in (
            "kernel_addr", "ramdisk_addr", "tags_addr", "page_size", "os_version")},
        "image_info": metadata,
    }
    dest[0].write_bytes(compact)
    dest[1].write_bytes(full)
    dest[2].write_text(json.dumps(manifest, indent=2) + "\n")
    print("=== OUTPUTS (NOT FLASHED) ===")
    for f in dest:
        print(f)
    print("SHA256 compact:", manifest["new_boot_trimmed_sha256"])
    print("SHA256 full64M:", manifest["new_boot_full64_sha256"])
    print("PASS: image packaged and reference/ramdisk/header validated; bootability NOT proven")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, struct.error, zlib.error) as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(2)
