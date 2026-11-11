#!/usr/bin/env python3
"""Read-only audit of USB init scripts inside the actual Sony Dora hybrid TWRP ramdisk.

Parses Android boot v0 + gzip-compressed newc CRC/ASCII CPIO; no extraction,
no filesystem writes, no ADB commands, and no flashing.
"""
import argparse
import gzip
import hashlib
import struct
from pathlib import Path

EXPECTED_SHA256 = "a66002ea0f77764b37a6e3c86c5bc8a4072812179f8d0d122ded459223c5e97c"


def stop(reason):
    raise SystemExit("STOP: " + reason)


def aligned(n, step=4):
    return ((n + step - 1) // step) * step


def read_newc(data):
    pos = 0
    files = {}
    while pos + 110 <= len(data):
        head = data[pos:pos + 110]
        if head[:6] not in (b"070701", b"070702"):
            stop(f"expected newc CPIO header at offset 0x{pos:x}; magic={head[:8]!r}")
        try:
            fields = [int(head[6+i*8:6+(i+1)*8], 16) for i in range(13)]
        except ValueError:
            stop(f"invalid CPIO header fields at 0x{pos:x}")
        size = fields[6]
        name_size = fields[11]
        if not 1 <= name_size <= 4096:
            stop("invalid CPIO pathname size")
        name_start = pos + 110
        name_end = name_start + name_size
        if name_end > len(data) or data[name_end - 1] != 0:
            stop("truncated CPIO pathname")
        name = data[name_start:name_end - 1].decode("utf-8", "replace")
        payload_start = aligned(name_end)
        payload_end = payload_start + size
        if payload_end > len(data):
            stop(f"truncated CPIO member {name}")
        if name == "TRAILER!!!":
            return files
        if name in files:
            stop(f"duplicate CPIO filename {name}")
        files[name] = data[payload_start:payload_end]
        pos = aligned(payload_end)
    stop("missing CPIO TRAILER!!!")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", type=Path,
                   default=Path("/mnt/d/dora-twrp44-unflashed/dora-twrp44-unflashed.img"))
    args = p.parse_args()
    data = args.image.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    print("Image:", args.image)
    print("Size:", len(data), "SHA256:", digest)
    if digest != EXPECTED_SHA256:
        stop("unexpected image SHA256; refuse to audit unknown artifact")
    if len(data) < 4096 or data[:8] != b"ANDROID!":
        stop("not legacy Android boot image")
    ksize, kaddr, rsize, raddr, second, _, tags, page, unused, _ = struct.unpack_from("<10I", data, 8)
    if page != 4096 or second or unused:
        stop("unexpected boot header layout")
    roff = page + aligned(ksize, page)
    if roff + rsize > len(data):
        stop("truncated ramdisk")
    raw = data[roff:roff + rsize]
    if not raw.startswith(b"\x1f\x8b"):
        stop("TWRP ramdisk does not start with gzip")
    cpio = gzip.decompress(raw)
    print("Kernel addr:", hex(kaddr), "Ramdisk addr:", hex(raddr), "Tags addr:", hex(tags))
    print("Decompressed ramdisk bytes:", len(cpio))
    files = read_newc(cpio)
    print("CPIO entries:", len(files))
    keywords = ("usb", "adbd", "android_usb", "usb_gadget", "functionfs", "configfs")
    print("\n=== USB-RELATED RECOVERY CONFIGURATION ===")
    found = False
    for name, body in sorted(files.items()):
        if not (name.endswith(".rc") or name.endswith(".prop") or name == "init.rc"):
            continue
        try:
            txt = body.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relevant = [(i, ln) for i, ln in enumerate(txt.splitlines(), 1)
                    if any(key in ln.lower() for key in keywords)]
        if relevant:
            found = True
            print(f"\n--- {name} ({len(relevant)} matching lines) ---")
            for line, statement in relevant[:100]:
                print(f"{line:4}: {statement}")
            if len(relevant) > 100:
                print(f"... plus {len(relevant) - 100} matching lines")
    if not found:
        print("No matching init/property lines found")
    print("\n=== ROOT INIT FILES ===")
    for name in sorted(files):
        if "/" not in name and (name.startswith("init") or name.endswith(".prop")):
            print(name, len(files[name]))
    print("\nPASS: actual TWRP ramdisk audited, without extraction or writes")


if __name__ == "__main__":
    main()
