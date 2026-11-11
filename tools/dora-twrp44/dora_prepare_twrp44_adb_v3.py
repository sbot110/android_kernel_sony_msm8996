#!/usr/bin/env python3
"""Prepare (never flash) a Linux 4.4 Dora TWRP ramdisk with ConfigFS ADB.

The proven kernel and every CPIO entry except init.recovery.usb.rc remain
identical. Refuses unknown inputs, unexpected header, or pre-existing outputs.
"""
import argparse
import gzip
import hashlib
import json
import struct
from pathlib import Path

SOURCE_SHA = "a66002ea0f77764b37a6e3c86c5bc8a4072812179f8d0d122ded459223c5e97c"
KERNEL_SHA = "2780f84885f686aba71bfb2157317e4281834e2af9ff7e61b06d5be10af53cad"
RAMDISK_SHA = "ad479734b77f5a0d402a1636f9002c6f27eed236bf70744f72a049810f5fd92c"
TARGET = "init.recovery.usb.rc"
REPLACEMENT = r"""# Linux 4.4 Sony Dora recovery: ConfigFS + FunctionFS ADB-only test v3
# UDC 6a00000.dwc3 verified on F8132. The stock init.rc tries to mount
# FunctionFS on fs, before ffs.adb exists. Retry after ConfigFS creation.

on boot
    mkdir /config 0770 root root
    mount configfs none /config
    mkdir /config/usb_gadget/g1 0770 shell shell
    mkdir /config/usb_gadget/g1/strings/0x409 0770 shell shell
    mkdir /config/usb_gadget/g1/functions/ffs.adb 0770 shell shell
    mkdir /config/usb_gadget/g1/configs/b.1 0770 shell shell
    mkdir /config/usb_gadget/g1/configs/b.1/strings/0x409 0770 shell shell
    mkdir /dev/usb-ffs 0770 shell shell
    mkdir /dev/usb-ffs/adb 0770 shell shell
    # Boot happens after fs: function is now registered in ConfigFS.
    # A duplicate mount can fail EBUSY; init continues and existing mount is used.
    mount functionfs adb /dev/usb-ffs/adb uid=2000,gid=2000
    write /config/usb_gadget/g1/idVendor 0x0fce
    write /config/usb_gadget/g1/idProduct 0x51e1
    write /config/usb_gadget/g1/bcdUSB 0x0200
    write /config/usb_gadget/g1/strings/0x409/serialnumber ${ro.serialno}
    write /config/usb_gadget/g1/strings/0x409/manufacturer Sony
    write /config/usb_gadget/g1/strings/0x409/product Dora-TWRP44
    write /config/usb_gadget/g1/configs/b.1/strings/0x409/configuration adb
    setprop sys.usb.configfs 1
    setprop sys.usb.config adb
    # Previous adbd may have started on fs without usable FunctionFS.
    # Deliberately restart ONLY adbd after the second mount attempt.
    restart adbd

on property:sys.usb.config=adb
    start adbd

on property:sys.usb.ffs.ready=1 && property:sys.usb.config=adb
    symlink /config/usb_gadget/g1/functions/ffs.adb /config/usb_gadget/g1/configs/b.1/f1
    write /config/usb_gadget/g1/UDC 6a00000.dwc3
    setprop sys.usb.state adb

# For the diagnostic image, force ADB-only instead of MTP.
on property:sys.usb.config=mtp,adb
    setprop sys.usb.config adb

on property:sys.usb.config=mtp
    setprop sys.usb.config adb
""".encode("ascii")


def fail(message):
    raise SystemExit("STOP: " + message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(number, step=4):
    return (number + step - 1) // step * step


def patch_newc(cpio):
    pos, count, out = 0, 0, bytearray()
    while pos + 110 <= len(cpio):
        hdr = cpio[pos:pos + 110]
        if hdr[:6] not in (b"070701", b"070702"):
            fail(f"unrecognized CPIO member at {pos:#x}")
        try:
            vals = [int(hdr[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        except ValueError:
            fail("bad CPIO header")
        length, namesize = vals[6], vals[11]
        if not 1 <= namesize <= 4096:
            fail("invalid CPIO pathname length")
        name_at = pos + 110
        name_end = name_at + namesize
        if name_end > len(cpio) or cpio[name_end - 1] != 0:
            fail("invalid CPIO pathname")
        name = cpio[name_at:name_end - 1].decode("utf-8")
        body_at = align(name_end)
        body_end = body_at + length
        next_at = align(body_end)
        if next_at > len(cpio):
            fail("truncated CPIO entry")
        if name == "TRAILER!!!":
            out += cpio[pos:]
            break
        if name != TARGET:
            out += cpio[pos:next_at]
        else:
            count += 1
            original = cpio[body_at:body_end]
            print("Original USB RC:", len(original), "bytes SHA256", sha(original))
            if not (b"/sys/class/android_usb/android0" in original
                    and b"mount functionfs adb" in original
                    and b"on property:sys.usb.config=adb" in original):
                fail("unexpected original USB RC; refuse to overwrite")
            new_hdr = bytearray(hdr)
            new_hdr[54:62] = f"{len(REPLACEMENT):08x}".encode("ascii")
            if hdr[:6] == b"070702":
                new_hdr[102:110] = f"{sum(REPLACEMENT) & 0xffffffff:08x}".encode("ascii")
            out += new_hdr
            out += cpio[name_at:body_at]
            out += REPLACEMENT
            out += bytes(align(len(REPLACEMENT)) - len(REPLACEMENT))
        pos = next_at
    else:
        fail("missing CPIO trailer")
    if count != 1:
        fail(f"expected one USB RC entry, got {count}")
    return bytes(out)


def boot_id(kernel, ramdisk):
    h = hashlib.sha1()
    for blob in (kernel, ramdisk, b""):
        h.update(blob)
        h.update(struct.pack("<I", len(blob)))
    return h.digest() + bytes(12)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/mnt/d/dora-twrp44-unflashed/dora-twrp44-unflashed.img"))
    parser.add_argument("--output-dir", type=Path, default=Path("/mnt/d/dora-twrp44-adb-v3"))
    args = parser.parse_args()
    if args.output_dir.exists():
        fail("output directory already exists; refuse overwrite: " + str(args.output_dir))
    data = args.input.read_bytes()
    if sha(data) != SOURCE_SHA or data[:8] != b"ANDROID!":
        fail("input hybrid image does not match verified source")
    ksize, kaddr, rsize, raddr, second, _, tags, pages, unused, _ = struct.unpack_from("<10I", data, 8)
    if (kaddr, raddr, tags, pages, second, unused) != (0x8000, 0x02000000, 0x01e00000, 4096, 0, 0):
        fail("unexpected Sony TWRP boot header")
    r_offset = pages + align(ksize, pages)
    if r_offset + rsize > len(data):
        fail("truncated image")
    kernel = data[pages:pages + ksize]
    old_ramdisk = data[r_offset:r_offset + rsize]
    if sha(kernel) != KERNEL_SHA or sha(old_ramdisk) != RAMDISK_SHA:
        fail("original kernel or ramdisk hash mismatch")
    if not old_ramdisk.startswith(b"\x1f\x8b"):
        fail("ramdisk is not gzip")
    cpio = gzip.decompress(old_ramdisk)
    new_cpio = patch_newc(cpio)
    new_ramdisk = gzip.compress(new_cpio, compresslevel=6, mtime=0)
    if gzip.decompress(new_ramdisk) != new_cpio:
        fail("recompressed ramdisk failed round-trip")
    header = bytearray(data[:pages])
    struct.pack_into("<I", header, 16, len(new_ramdisk))
    header[576:608] = boot_id(kernel, new_ramdisk)
    image = bytes(header) + kernel + bytes(align(len(kernel), pages) - len(kernel))
    image += new_ramdisk + bytes(align(len(new_ramdisk), pages) - len(new_ramdisk))
    if len(image) > 67108864:
        fail("image exceeds FOTAKernel size")
    manifest = {
        "status": "UNFLASHED; FIRST BOOT NOT TESTED",
        "source_sha256": sha(data),
        "new_image_sha256": sha(image),
        "new_image_size": len(image),
        "kernel_sha256_unchanged": sha(kernel),
        "ramdisk_sha256_original": sha(old_ramdisk),
        "ramdisk_sha256_new": sha(new_ramdisk),
        "modified_files": [TARGET],
        "variant": "v3: mount FunctionFS after creating ffs.adb; restart adbd",
        "USB": "ConfigFS+FunctionFS, ADB only, late FunctionFS mount retry + adbd restart",
        "UDC": "6a00000.dwc3",
        "original_twrp_header_addresses_preserved": True,
        "flash_performed": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "dora-twrp44-adb-v3.img").write_bytes(image)
    (args.output_dir / "init.recovery.usb.rc").write_bytes(REPLACEMENT)
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    print("PASS: ADB-only test image built; no partitions written")


if __name__ == "__main__":
    main()
