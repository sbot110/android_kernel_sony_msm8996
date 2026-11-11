#!/usr/bin/env python3
"""Compare the appended DTBs in the *working* LOS21 4.4 boot.img and 5.15.

Read-only inspection. No flashing, file mutation, or device access.
Use from existing test worktree:
    python3 tools/dora_compare_boot_dtbs.py
"""
import hashlib
import struct
import sys
import zipfile
import zlib
from pathlib import Path

ROM = Path.home() / "android/lineage/out/target/product/dora/lineage-21.0-20261030-UNOFFICIAL-dora.zip"
NEW = Path("/mnt/c/platform-tools/dora515-preflight-no-stages/Image.gz-dtb")
EXPECTED_BOOT_SHA256 = "f923f9b99c468484b713529577b84e3a137d1b9cbc09393e5593258009983820"
EXPECTED_NEW_SHA256 = "c552c694c46ae538e291080e50af0e2a6c0f2f0c75de6812806e008eecd4709d"
FDT_MAGIC = 0xd00dfeed
INTERESTING = ("model", "compatible", "qcom,msm-id", "qcom,board-id", "qcom,pmic-id")


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def align(n):
    return (n + 3) & ~3


def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def fdt_info(data):
    if len(data) < 40 or u32(data, 0) != FDT_MAGIC:
        raise ValueError("Not an FDT DTB")
    total, off_struct, off_strings, off_resv, version, last_compatible, boot_cpu, size_strings, size_struct = struct.unpack_from(">9I", data, 4)
    if total < 40 or total > len(data):
        raise ValueError("Invalid FDT length")
    if (off_struct + size_struct > total or
            off_strings + size_strings > total or
            off_resv >= total):
        raise ValueError("Invalid FDT offsets")
    if version < 16:
        raise ValueError("Unexpectedly old FDT version")
    st = data[off_struct:off_struct + size_struct]
    strings = data[off_strings:off_strings + size_strings]
    pos = 0
    depth = 0
    values = {}
    found_end = False
    while pos + 4 <= len(st):
        token = u32(st, pos)
        pos += 4
        if token == 1:
            end = st.find(b"\0", pos)
            if end < 0:
                raise ValueError("Unterminated FDT node")
            pos = align(end + 1)
            depth += 1
        elif token == 2:
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced FDT end-node")
        elif token == 3:
            if pos + 8 > len(st):
                raise ValueError("Truncated FDT property")
            length = u32(st, pos)
            nameoff = u32(st, pos + 4)
            pos += 8
            if nameoff >= len(strings) or pos + length > len(st):
                raise ValueError("Invalid FDT property range")
            name_end = strings.find(b"\0", nameoff)
            if name_end < 0:
                raise ValueError("Invalid FDT property name")
            name = strings[nameoff:name_end].decode("ascii", "replace")
            if depth == 1 and name in INTERESTING:
                values[name] = st[pos:pos + length]
            pos = align(pos + length)
        elif token == 4:
            continue
        elif token == 9:
            found_end = True
            break
        else:
            raise ValueError(f"Invalid FDT token {token} at {pos - 4:#x}")
    if not found_end:
        raise ValueError("FDT lacks END token")
    return total, values


def decode_value(name, raw):
    if name in ("model", "compatible"):
        return raw.rstrip(b"\0").decode("utf-8", "replace").replace("\0", " | ")
    if len(raw) % 4:
        return f"{len(raw)} bytes: {raw.hex()}"
    values = struct.unpack(">" + "I" * (len(raw) // 4), raw)
    return " ".join(f"{x:#x}" for x in values)


def dtbs_after_gzip(packed, label):
    if not packed.startswith(b"\x1f\x8b"):
        raise ValueError(f"{label}: payload does not begin with gzip")
    dec = zlib.decompressobj(31)
    image = dec.decompress(packed) + dec.flush()
    if not dec.eof:
        raise ValueError(f"{label}: incomplete gzip")
    footer = dec.unused_data
    print(f"{label}: packed={len(packed):,}, uncompressed Image={len(image):,}, gzip tail={len(footer):,}")
    if len(image) < 64 or image[56:60] != b"ARM\x64":
        raise ValueError(f"{label}: ARM64 Image magic missing")
    text_offset, image_size, flags = struct.unpack_from("<QQQ", image, 8)
    print(f"  text_offset=0x{text_offset:x}, image_size=0x{image_size:x}, flags=0x{flags:x}")
    result = []
    offset = 0
    if not footer:
        print("  no appended DTB bytes found")
    while offset < len(footer):
        if offset + 40 > len(footer):
            print(f"  remainder [{offset:#x}..] {len(footer) - offset} bytes, prefix {footer[offset:offset+32].hex()}")
            break
        if u32(footer, offset) != FDT_MAGIC:
            print(f"  non-FDT remainder [{offset:#x}..], {len(footer) - offset} bytes, prefix {footer[offset:offset+32].hex()}")
            break
        length, values = fdt_info(footer[offset:])
        result.append(values)
        print(f"  DTB {len(result)}: offset=0x{offset:x}, size={length:,}")
        for name in INTERESTING:
            raw = values.get(name)
            print(f"    {name}: {decode_value(name, raw) if raw is not None else '<ABSENT>'}")
        offset += length
    if offset == len(footer):
        print(f"  exact DTB tail consumed: {len(result)} DTBs")
    return result


def main():
    with zipfile.ZipFile(ROM) as archive:
        original = archive.read("boot.img")
    if sha(original) != EXPECTED_BOOT_SHA256:
        raise ValueError("Original LOS21 ZIP boot.img hash differs from installed reference")
    if original[:8] != b"ANDROID!":
        raise ValueError("Original boot image magic mismatch")
    size, = struct.unpack_from("<I", original, 8)
    page, = struct.unpack_from("<I", original, 36)
    if page != 4096 or size != 12047744:
        raise ValueError("Original Sony boot header layout unexpected")
    old = original[page:page + size]

    new = NEW.read_bytes()
    if sha(new) != EXPECTED_NEW_SHA256:
        raise ValueError("New 5.15 Image.gz-dtb hash differs from the third experiment")
    print("=== WORKING LOS21 / LINUX 4.4 ===")
    old_dtbs = dtbs_after_gzip(old, "4.4")
    print("\n=== EXPERIMENTAL LINUX 5.15 (NO STAGES) ===")
    new_dtbs = dtbs_after_gzip(new, "5.15")
    print("\n=== COMPARISON ===")
    print("Working 4.4 appended DTBs:", len(old_dtbs))
    print("Experimental 5.15 appended DTBs:", len(new_dtbs))
    print("Important: legacy qcom IDs and DTB order determine Sony bootloader selection.")
    print("Read-only comparison complete; nothing has been flashed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, struct.error, zipfile.BadZipFile, zlib.error) as e:
        print("STOP:", e, file=sys.stderr)
        sys.exit(2)
