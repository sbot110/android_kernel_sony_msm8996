#!/usr/bin/env python3
"""Decode Sony dora kernel-5.15 4 KiB early-boot breadcrumb RAM page.

Usage:
  python3 scripts/dora_decode_early_markers.py dora-stage.bin --image-size 0x2160000

The mirrored table starts at page offset 0xF00 and is preferred if the
primary table has been overwritten by TWRP 3.18's pmsg initialization.
Raw RAM must be acquired using a recovery/kernel that permits physical reads.
"""
import argparse
import struct
from pathlib import Path

BASE_MAGIC = 0x3053475453353135
CHECK_XOR = 0x3C3C3C3C3C3C3C3C
STAGES = {
    1: ("FDT pointer at kernel entry", "Image physical address (head.S)"),
    2: ("Image misalignment/KASLR offset", "init_pg_dir physical address"),
    3: ("FDT physical pointer", "boot CPU MPIDR"),
    4: ("DRAM physical start", "DRAM physical end"),
    5: ("Kernel _text physical", "Kernel _end physical"),
    6: ("initrd physical start", "initrd physical end"),
    7: ("FDT physical start", "FDT physical end"),
}


def decode_table(data: bytes, start: int):
    found = {}
    for stage in STAGES:
        at = start + (stage - 1) * 32
        if at + 32 > len(data):
            continue
        magic, arg0, arg1, checksum = struct.unpack_from("<QQQQ", data, at)
        expected_magic = BASE_MAGIC | (stage << 56)
        expected_checksum = expected_magic ^ arg0 ^ arg1 ^ CHECK_XOR
        if magic == expected_magic and checksum == expected_checksum:
            found[stage] = (arg0, arg1)
    return found


def overlap(a, b):
    return max(a[0], b[0]) < min(a[1], b[1])


def fmt_range(name, r):
    print(f"  {name:<12} [0x{r[0]:x}, 0x{r[1]:x})", end="")
    print(f"  ({(r[1] - r[0]) / (1024 * 1024):.3f} MiB)")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("memory_page", type=Path, help="4 KiB raw physical RAM dump from 0xa7fff000")
    p.add_argument("--image-size", type=lambda x: int(x, 0), help="ARM64 header image_size (e.g. 0x2160000)")
    args = p.parse_args()
    data = args.memory_page.read_bytes()
    if len(data) != 4096:
        p.error(f"expected exactly 4096 bytes; got {len(data)}")

    primary = decode_table(data, 0)
    mirror = decode_table(data, 0xf00)
    print(f"Primary table: {len(primary)} valid stage(s); mirror: {len(mirror)}")
    stages = {}
    for stage in STAGES:
        m = mirror.get(stage)
        q = primary.get(stage)
        if m and q and m != q:
            print(f"WARNING: Stage {stage}: primary/mirror disagree; choosing mirror")
        selected = m or q
        if selected:
            stages[stage] = selected
            a, b = selected
            print(f"Stage {stage}: VALID ({'mirror' if m else 'primary'})")
            print(f"  {STAGES[stage][0]}: 0x{a:x}")
            print(f"  {STAGES[stage][1]}: 0x{b:x}")
        else:
            print(f"Stage {stage}: absent or invalid")

    if not stages:
        print("No valid markers. This does NOT prove the kernel failed to enter:")
        print("the bootloader/recovery may have overwritten RAM, or the dump may be inaccessible.")
        return

    r = {}
    if 5 in stages:
        start, end = stages[5]
        if end > start:
            r["Kernel _end"] = (start, end)
        if args.image_size:
            r["Image_size"] = (start, start + args.image_size)
    if 6 in stages and stages[6][1] > stages[6][0]:
        r["initrd"] = stages[6]
    if 7 in stages and stages[7][1] > stages[7][0]:
        r["FDT"] = stages[7]

    if r:
        print("\nPHYSICAL RANGES")
        for name, rg in r.items():
            fmt_range(name, rg)
        image = r.get("Image_size") or r.get("Kernel _end")
        for name in ("initrd", "FDT"):
            if image and name in r:
                print(f"{name} overlaps {'Image_size' if 'Image_size' in r else 'Kernel _end'}: "
                      f"{'YES' if overlap(image, r[name]) else 'NO'}")

    print("\nMarker validity does not prove that later boot stages succeeded.")


if __name__ == "__main__":
    main()
