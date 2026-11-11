#!/usr/bin/env python3
"""Prepare an UNFLASHED Sony dora Linux 4.4 image_size control image.

Change only ARM64 Image.image_size in the *uncompressed* kernel, from
0x1cea000 to 0x2160000 (the experimental Linux 5.15 value). Recompress
gzip Image, append identical 4 original Sony DTBs, and repack Android v0.
The Android kernel_size and canonical SHA1 ID also necessarily change.
No files outside the NEW output directory are modified. NO DEVICE ACCESS.
"""
import gzip
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

BASE = Path("/mnt/c/platform-tools")
SOURCE = BASE / "dora44-id-control/dora44-aosp-id-full64M.img"
BACKUP = BASE / "dora-boot-reference.img"
DEST = BASE / "dora44-image-size-control"
SOURCE_SHA = "94de94c399ef0bee51c3fe5ce83dbb575d46487f05a988e7d1f3f5826c7d9aa6"
BACKUP_SHA = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
OLD_IMAGE_SIZE = 0x1cea000
NEW_IMAGE_SIZE = 0x2160000
DTB_SIZES = (470605, 471117, 473675, 474187)
PARTITION_SIZE = 67108864
PAGE = 4096


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n):
    return (n + PAGE-1) // PAGE * PAGE


def boot_id(kernel, ramdisk):
    h = hashlib.sha1()
    for part in (kernel, ramdisk, b""):
        h.update(part)
        h.update(struct.pack("<I", len(part)))
    return h.digest() + b"\0" * 12


def gzip_and_dtbs(payload):
    dec = zlib.decompressobj(31)
    raw = dec.decompress(payload) + dec.flush()
    if not dec.eof or len(raw) < 64 or raw[56:60] != b"ARM\x64":
        raise ValueError("Invalid original compressed ARM64 Image")
    rest = dec.unused_data
    at, lengths = 0, []
    while at < len(rest):
        if at+8 > len(rest):
            raise ValueError("Truncated DTB")
        magic, size = struct.unpack_from(">II", rest, at)
        if magic != 0xd00dfeed or size < 40 or at + size > len(rest):
            raise ValueError(f"Malformed Sony DTB at {at:#x}")
        lengths.append(size)
        at += size
    if tuple(lengths) != DTB_SIZES:
        raise ValueError(f"Original four Sony DTBs unexpected: {lengths}")
    return raw, rest


def main():
    if DEST.exists():
        raise ValueError(f"Refusing to overwrite output: {DEST}")
    source = SOURCE.read_bytes()
    backup = BACKUP.read_bytes()
    if (len(source) != PARTITION_SIZE or sha(source) != SOURCE_SHA or
        len(backup) != PARTITION_SIZE or sha(backup) != BACKUP_SHA):
        raise ValueError("Missing or mismatched known-working boot source/backup")
    if source[:576] != backup[:576] or source[608:] != backup[608:]:
        raise ValueError("Input differs from verified original outside known 4.4 header id")
    if source[:8] != b"ANDROID!":
        raise ValueError("Android legacy header magic mismatch")

    ksize,kaddr,rsize,raddr,ssize,saddr,tags,page,unused,osver = struct.unpack_from("<10I",source,8)
    if (ksize,kaddr,rsize,raddr,ssize,tags,page,unused) != (
        12047744,0x80008000,1224724,0x81000000,0,0x80000100,PAGE,0):
        raise ValueError("Unexpected 4.4 header fields")
    roff = PAGE + align(ksize)
    kernel, ramdisk = source[PAGE:PAGE+ksize],source[roff:roff+rsize]
    if source[576:608] != boot_id(kernel,ramdisk):
        raise ValueError("Source 4.4 canonical boot ID mismatched")
    raw, dtbs = gzip_and_dtbs(kernel)
    text_offset, image_size, flags = struct.unpack_from("<QQQ",raw,8)
    if (text_offset,image_size,flags) != (0x80000,OLD_IMAGE_SIZE,0xa):
        raise ValueError("Unexpected original ARM64 Image header")
    if len(raw) > OLD_IMAGE_SIZE or len(raw) > NEW_IMAGE_SIZE:
        raise ValueError("Image file length is greater than reserved Image size")

    patched = bytearray(raw)
    struct.pack_into("<Q",patched,0x10,NEW_IMAGE_SIZE)
    patched = bytes(patched)
    if patched[:0x10] != raw[:0x10] or patched[0x18:] != raw[0x18:]:
        raise ValueError("Unexpected Image change outside image_size")
    new_kernel = gzip.compress(patched,compresslevel=9,mtime=0) + dtbs
    check_raw,check_dtbs = gzip_and_dtbs(new_kernel)
    if check_raw != patched or check_dtbs != dtbs:
        raise ValueError("Repacked raw Image or appended DTBs differ")
    if kaddr+len(new_kernel) >= raddr:
        raise ValueError("Compressed kernel overlaps boot header ramdisk address")

    header = bytearray(source[:PAGE])
    struct.pack_into("<I",header,8,len(new_kernel))
    header[576:608] = boot_id(new_kernel,ramdisk)
    if header[12:576] != source[12:576] or header[608:] != source[608:PAGE]:
        raise ValueError("Unexpected Android header mutation")
    compact = bytes(header) + new_kernel + b"\0"*(align(len(new_kernel))-len(new_kernel)) + ramdisk + b"\0"*(align(rsize)-rsize)
    if len(compact) > PARTITION_SIZE:
        raise ValueError("Image exceeds boot partition")
    full = compact + backup[len(compact):]
    if len(full) != PARTITION_SIZE or full[len(compact):] != backup[len(compact):]:
        raise ValueError("Boot partition tail not preserved")
    new_roff = PAGE + align(len(new_kernel))
    if full[new_roff:new_roff+rsize] != ramdisk or full[576:608] != boot_id(new_kernel,ramdisk):
        raise ValueError("Boot image payload invalid")

    DEST.mkdir(parents=True)
    dest = DEST/"dora44-image-size-2160000-full64M.img"
    dest.write_bytes(full)
    manifest = {
        "status":"UNFLASHED BOOT CONTROL: BOOTABILITY NOT PROVEN",
        "known_booted_4_4_source_sha256":SOURCE_SHA,
        "original_reference_sha256":BACKUP_SHA,
        "old_image_size":hex(OLD_IMAGE_SIZE),
        "new_image_size":hex(NEW_IMAGE_SIZE),
        "raw_kernel_bytes_only_changed_at_offset":"[0x10,0x18)",
        "original_four_dtb_sizes":list(DTB_SIZES),
        "dtb_bytes_identical":True,
        "ramdisk_bytes_identical":True,
        "ramdisk_addr_unchanged":hex(raddr),
        "gzip_recompressed_and_boot_header_kernel_size_and_id_recalculated":True,
        "kernel_size_before":ksize,
        "kernel_size_after":len(new_kernel),
        "new_full64_sha256":sha(full),
        "output":str(dest),
    }
    (DEST/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print("=== SONY LINUX 4.4 ARM64 IMAGE_SIZE CONTROL ===")
    print("Image.image_size:",hex(OLD_IMAGE_SIZE),"->",hex(NEW_IMAGE_SIZE))
    print("Decompressed kernel differs ONLY at [0x10,0x18)")
    print("Four original DTBs and LOS21 ramdisk are byte-identical")
    print("Original header ramdisk_addr:",hex(raddr))
    print("New full64 SHA256:",sha(full))
    print("Image:",dest)
    print("Manifest:",DEST/"manifest.json")
    print("PASS: inflated 4.4 image_size control generated; NOT FLASHED")


if __name__ == "__main__":
    try:
        main()
    except (OSError,ValueError,struct.error,zlib.error) as exc:
        print("STOP:",exc,file=sys.stderr)
        sys.exit(2)
