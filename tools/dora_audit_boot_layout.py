#!/usr/bin/env python3
"""Read-only comparison of dora LOS21 working 4.4 vs experimental 5.15 boot memory layout.

On the WSL build host, with Sony working boot partition backup, experimental
memory-fix image, and optionally a device in TWRP, run:
  python3 tools/dora_audit_boot_layout.py

No build, source modification, flash or reboot operations.
"""
import hashlib
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROM = Path.home() / "android/lineage/out/target/product/dora/lineage-21.0-20261030-UNOFFICIAL-dora.zip"
BACKUP = Path("/mnt/c/platform-tools/dora-boot-reference.img")
NEW = Path("/mnt/c/platform-tools/dora515-preflight-memory-fix/dora-515-diagnostic-boot-full64M.img")
ADB = Path("/mnt/c/platform-tools/adb.exe")
BOOT_SHA = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
NEW_SHA = "94a9d751d4f2f160863b81df11e49310835534d7c4163dd20b6665f9ffed1dba"
ORIGINAL_ZIP_SHA = "f923f9b99c468484b713529577b84e3a137d1b9cbc09393e5593258009983820"
# Observed previously in running LOS21 4.4: /proc/device-tree/chosen/linux,initrd-start
OBSERVED_44_INITRD_START = 0x82200000
OBSERVED_44_INITRD_END = 0x8232b014


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n, step):
    return (n + step - 1) // step * step


def unpack_boot(data):
    if len(data) != 67108864 or data[:8] != b"ANDROID!":
        raise ValueError("Expected full 64MiB Android legacy boot partition")
    ksize, kaddr, rsize, raddr, second, _, tags, pagesize, ver, _ = struct.unpack_from("<10I", data, 8)
    if pagesize != 4096 or ksize <= 0 or rsize <= 0 or second != 0 or ver != 0:
        raise ValueError("Unexpected Sony legacy v0 boot header")
    off = pagesize
    kernel = data[off:off+ksize]
    roff = pagesize + align(ksize, pagesize)
    ramdisk = data[roff:roff+rsize]
    if len(kernel) != ksize or len(ramdisk) != rsize:
        raise ValueError("Truncated payload")
    dec = zlib.decompressobj(31)
    img = dec.decompress(kernel) + dec.flush()
    if not dec.eof or img[56:60] != b"ARM\x64":
        raise ValueError("Bad ARM64 gzip Image")
    text_off, size, flags = struct.unpack_from("<QQQ", img, 8)
    if not text_off or not size:
        raise ValueError("Unexpected ARM64 Image header")
    # Physical base inference assumes the image is placed at RAM base + text_offset.
    base = 0x80000000
    image_start = base + text_off
    image_end = image_start + size
    return {"kernel_size": ksize, "kernel_addr": kaddr, "ramdisk_size": rsize,
            "ramdisk_addr": raddr, "ramdisk_off": roff, "tags_addr": tags,
            "text_off": text_off, "image_size": size, "image_file_size":len(img),
            "flags": flags, "start": image_start, "end": image_end,
            "compressed_end": kaddr + ksize, "dtb_bytes": len(dec.unused_data),
            "ramdisk_sha": sha(ramdisk), "kernel_sha": sha(kernel)}


def boot_summary(label, b):
    print(f"\n=== {label} ===")
    for x in ("kernel_size", "kernel_addr", "ramdisk_size", "ramdisk_addr",
              "ramdisk_off", "tags_addr", "text_off", "image_file_size",
              "image_size", "flags", "compressed_end", "dtb_bytes"):
        v = b[x]
        print(f"{x:18}: {v:,} ({v:#x})")
    print("Assumed Image physical range:", f"0x{b['start']:x}–0x{b['end']:x} (end exclusive)")
    print("Header ramdisk_addr inside assumed Image range:",
          b["start"] <= b["ramdisk_addr"] < b["end"])
    print("Compressed kernel end below header ramdisk_addr:",
          b["compressed_end"] <= b["ramdisk_addr"])


def read_adb_blob(name):
    try:
        r = subprocess.run([str(ADB), "exec-out", "cat", name], capture_output=True, timeout=10)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def device():
    if not ADB.is_file():
        print("adb.exe not found; skipping connected device data")
        return
    uname = read_adb_blob("/proc/version")
    if uname is None:
        print("adb device inaccessible; skipping")
        return
    print("\n=== CURRENT DEVICE (read-only) ===")
    print("Kernel:", uname.decode("utf-8","replace").splitlines()[0][:240])
    for name in ("linux,initrd-start", "linux,initrd-end"):
        val = read_adb_blob("/proc/device-tree/chosen/" + name)
        if val is None or len(val) not in (4,8):
            print(name, "<unavailable>")
        else:
            print(name, f"0x{int.from_bytes(val, 'big'):x}", f"({len(val)} bytes)")
    for path in ("/proc/device-tree/memory/reg", "/proc/device-tree/memory@80000000/reg"):
        val = read_adb_blob(path)
        if val is None:
            print(path, "<unavailable>")
        elif len(val) % 4 == 0:
            parts = struct.unpack(">" + "I"*(len(val)//4), val)
            print(path, " ".join(f"0x{i:x}" for i in parts))
        else:
            print(path, len(val), "raw bytes")
    try:
        out = subprocess.run([str(ADB), "shell", "cat /proc/iomem"], capture_output=True, timeout=10)
        if out.returncode == 0:
            lines = out.stdout.decode("utf-8", "replace").splitlines()
            print("\n/proc/iomem (first 30 relevant lines):")
            for line in [x for x in lines if any(w in x.lower() for w in ("system ram","kernel code","kernel data","reserved"))][:30]:
                print("  " + line)
        else:
            print("/proc/iomem not readable from current device")
    except (OSError, subprocess.TimeoutExpired):
        print("/proc/iomem query failed")


def main():
    oldblob = BACKUP.read_bytes()
    newblob = NEW.read_bytes()
    if sha(oldblob) != BOOT_SHA or sha(newblob) != NEW_SHA:
        raise ValueError("Boot partition artifacts differ from established verified SHA256")
    with zipfile.ZipFile(ROM) as z:
        original_zip = z.read("boot.img")
    if sha(original_zip) != ORIGINAL_ZIP_SHA or oldblob[:len(original_zip)] != original_zip:
        raise ValueError("Working backup differs from LineageOS ROM ZIP")
    old = unpack_boot(oldblob)
    new = unpack_boot(newblob)
    if old["ramdisk_sha"] != new["ramdisk_sha"]:
        raise ValueError("Experimental boot does not contain original LineageOS ramdisk")
    boot_summary("WORKING SONY LINUX 4.4", old)
    boot_summary("EXPERIMENTAL LINUX 5.15 /memory FIX", new)
    print("\n=== PHYSICAL LAYOUT HYPOTHESIS ===")
    print("Observed 4.4 /chosen initrd:", f"0x{OBSERVED_44_INITRD_START:x}..0x{OBSERVED_44_INITRD_END:x}")
    for name, info in (("4.4",old),("5.15",new)):
        print(f"{name} gap: {OBSERVED_44_INITRD_START-info['end']:,} bytes "
              f"(0x{OBSERVED_44_INITRD_START-info['end']:x}) "
              f"from assumed Image reserved end to 4.4 initrd start")
    print("CAUTION: the location of the decompressed 5.15 Image, FDT,")
    print("and 5.15 initrd is UNKNOWN; these are conditional calculations.")
    print("The current recovery DTB/initrd may use different addresses from Android.")
    device()
    print("\nPASS: read-only boot memory audit finished; NO FLASH")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, struct.error, zlib.error, zipfile.BadZipFile) as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(2)
