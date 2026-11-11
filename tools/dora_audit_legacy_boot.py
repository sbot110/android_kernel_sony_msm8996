#!/usr/bin/env python3
"""Read-only forensic inspection of a full Sony dora Android boot partition.

Requires no tools besides Python 3 and NEVER writes or flashes any data.
Use before repacking an image whose Android v0 ID is not canonical or whose
trailing partition bytes are not all zero.
"""
import argparse
import hashlib
import struct
from pathlib import Path

EXPECTED_SHA = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
PART_BYTES = 64 * 1024 * 1024
PAGE = 4096


def roundup(v, a):
    return (v + a - 1) // a * a


def fmt(n):
    return f"0x{n:08x} ({n:,})"


def id_standard(kernel, ramdisk, second=b""):
    sha = hashlib.sha1()
    for payload in (kernel, ramdisk, second):
        sha.update(payload)
        sha.update(struct.pack("<I", len(payload)))
    return sha.hexdigest()


def id_nosize(kernel, ramdisk):
    return hashlib.sha1(kernel + ramdisk).hexdigest()


def id_nonzero_segments(kernel, ramdisk):
    sha = hashlib.sha1()
    for payload in (kernel, ramdisk):
        sha.update(payload)
        sha.update(struct.pack("<I", len(payload)))
    return sha.hexdigest()


def page_runs(data, start):
    """Return nonzero-page intervals; do not print every page."""
    begin = None
    runs = []
    n_pages = 0
    for ofs in range(start, len(data), PAGE):
        chunk = data[ofs:ofs + PAGE]
        nonzero = any(chunk)
        if nonzero:
            n_pages += 1
            if begin is None:
                begin = ofs
        elif begin is not None:
            runs.append((begin, ofs))
            begin = None
    if begin is not None:
        runs.append((begin, len(data)))
    return runs, n_pages


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("reference", type=Path)
    args = ap.parse_args()
    data = args.reference.read_bytes()
    if len(data) != PART_BYTES or hashlib.sha256(data).hexdigest() != EXPECTED_SHA:
        ap.error("the file is not the 64 MiB boot backup already verified with TWRP")
    if data[:8] != b"ANDROID!":
        ap.error("legacy boot magic not found")

    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags_addr, page_size, unused, osver = struct.unpack_from("<10I", data, 8)
    if page_size != PAGE:
        ap.error(f"unexpected page size: {page_size}")
    ko = PAGE
    kr = (ko, ko + kernel_size)
    ro = roundup(kr[1], PAGE)
    rr = (ro, ro + ramdisk_size)
    so = roundup(rr[1], PAGE)
    sr = (so, so + second_size)
    tail = roundup(sr[1], PAGE)
    kernel = data[kr[0]:kr[1]]
    ramdisk = data[rr[0]:rr[1]]
    second = data[sr[0]:sr[1]]
    stored_id = data[576:608]

    print("=== HEADER / OFFSETS ===")
    print("reference sha256: ", EXPECTED_SHA)
    print("kernel_size:      ", fmt(kernel_size))
    print("ramdisk_size:     ", fmt(ramdisk_size))
    print("second_size:     ", fmt(second_size))
    print("kernel_addr:     ", hex(kernel_addr))
    print("ramdisk_addr:    ", hex(ramdisk_addr))
    print("tags_addr:       ", hex(tags_addr))
    print("header extra:    ", hex(unused), "os_version:", hex(osver))
    for label, i, j in (("kernel payload", *kr), ("ramdisk payload", *rr),
                        ("second payload", *sr)):
        print(f"{label:17} [{fmt(i)}, {fmt(j)})")
    print("nominal compact end:", fmt(tail))
    print("page padding kernel nonzero:", sum(x != 0 for x in data[kr[1]:ro]))
    print("page padding ramdisk nonzero:", sum(x != 0 for x in data[rr[1]:so]))

    print("\n=== HEADER ID (offset 576, 32 bytes) ===")
    print("stored id:", stored_id.hex())
    print("stored sha1 (first 20):", stored_id[:20].hex())
    tests = [
        ("standard AOSP v0 with second size=0", id_standard(kernel, ramdisk, second)),
        ("payloads only, no size fields", id_nosize(kernel, ramdisk)),
        ("kernel & ramdisk with sizes, no second_size=0", id_nonzero_segments(kernel, ramdisk)),
    ]
    for name, digest in tests:
        print(f"{name}: {digest}  {'MATCH' if stored_id[:20].hex() == digest else 'no'}")
    print("stored last 12 id bytes all zero:", not any(stored_id[20:]))

    print("\n=== PARTITION TAIL AFTER NOMINAL BOOT IMAGE ===")
    rest = data[tail:]
    print("tail starts:", fmt(tail))
    print("tail size:", len(rest))
    print("tail nonzero byte count:", len(rest) - rest.count(0))
    runs, count = page_runs(data, tail)
    print("nonzero pages:", count, "of", len(rest) // PAGE)
    print("nonzero page ranges:", len(runs))
    for x, y in runs[:12]:
        print("  nonzero page range:", hex(x), "-", hex(y), "size", y - x)
    if len(runs) > 12:
        print("  ... remaining ranges:", len(runs) - 12)
        for x, y in runs[-3:]:
            print("  last range:", hex(x), "-", hex(y), "size", y - x)
    first = next((tail + i for i, b in enumerate(rest) if b), None)
    last = (tail + len(rest) - 1 - next((i for i, b in enumerate(reversed(rest)) if b), len(rest))) if any(rest) else None
    if first is not None:
        print("first nonzero byte at:", hex(first))
        print("first nonzero sample:", data[first:first+48].hex(" "))
        print("last nonzero byte at:", hex(last))
        left = max(tail, last - 31)
        print("last nonzero sample:", data[left:last+1].hex(" "))

    print("\n=== RECOGNIZABLE MARKERS IN TAIL ===")
    for marker in (b"ANDROID!", b"AVBf", b"AVB0", b"SEANDROIDENFORCE",
                   b"BOOTLDR!", b"BLOB", b"CERT", b"S1_BOOT"):
        offsets = []
        x = tail
        while len(offsets) < 8:
            x = data.find(marker, x)
            if x < 0:
                break
            offsets.append(hex(x))
            x += 1
        if offsets:
            print(marker.decode("ascii"), ":", ", ".join(offsets))
    print("\nREAD-ONLY AUDIT COMPLETE. Do NOT flash or relax packer checks yet.")


if __name__ == "__main__":
    main()
