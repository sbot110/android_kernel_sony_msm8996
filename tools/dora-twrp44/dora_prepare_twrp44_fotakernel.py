#!/usr/bin/env python3
"""Build, but NEVER flash, a Linux 4.4 + existing Sony Dora TWRP ramdisk experiment.

Inputs: verified 64 MiB recovery/FOTAKernel backup with SHA256 sidecar,
verified stable LOS21 boot partition backup. Keeps TWRP ramdisk, cmdline,
other header fields, and trailing full-partition bytes; replaces only the
compressed kernel, kernel size, and Android v0 SHA1 ID. DOES NOT CALL ADB.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

PARTITION = 64 * 1024 * 1024
STABLE_SHA256 = "07200b77a75f989533a477da355544dd25f1be99ee8c763df7ca75e5108d3178"
ID_START, ID_END = 576, 608
KEYS = ("kernel_size", "kernel_addr", "ramdisk_size", "ramdisk_addr",
        "second_size", "second_addr", "tags_addr", "page_size",
        "unused", "os_version")


def fail(reason):
    raise SystemExit("STOP: " + reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n, page):
    return (n + page - 1) // page * page


def read_full(path):
    data = path.read_bytes()
    if len(data) != PARTITION:
        fail("expected exact 64 MiB partition backup: " + str(path))
    return data


def parse_boot(data, desc):
    if data[:8] != b"ANDROID!":
        fail(f"{desc} uses a non-Android-boot-v0 format; magic={data[:16].hex()}")
    fields = dict(zip(KEYS, struct.unpack_from("<10I", data, 8)))
    if fields["page_size"] != 4096 or fields["second_size"] or fields["unused"]:
        fail(f"{desc}: unexpected legacy header fields (inspect before repacking): {fields}")
    if fields["kernel_size"] == 0 or fields["ramdisk_size"] == 0:
        fail(f"{desc}: kernel/ramdisk size zero")
    page = fields["page_size"]
    kernel_off = page
    ramdisk_off = page + align(fields["kernel_size"], page)
    end = ramdisk_off + align(fields["ramdisk_size"], page)
    if end > len(data):
        fail(f"{desc}: payload exceeds file")
    fields["kernel"] = data[kernel_off:kernel_off + fields["kernel_size"]]
    fields["ramdisk"] = data[ramdisk_off:ramdisk_off + fields["ramdisk_size"]]
    fields["compact_size"] = end
    fields["ramdisk_offset"] = ramdisk_off
    fields["cmdline"] = (data[64:576].split(b"\0", 1)[0] +
                          data[608:1632].split(b"\0", 1)[0]).decode("utf-8", "replace")
    return fields


def boot_id(kernel, ramdisk):
    h = hashlib.sha1()
    for payload in (kernel, ramdisk, b""):
        h.update(payload)
        h.update(struct.pack("<I", len(payload)))
    return h.digest() + bytes(12)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--twrp", type=Path,
                   default=Path("/mnt/d/dora-FOTAKernel-twrp318-original.img"))
    p.add_argument("--stable-boot", type=Path,
                   default=Path("/mnt/c/platform-tools/dora-boot-stable-current.img"))
    p.add_argument("--outdir", type=Path,
                   default=Path("/mnt/d/dora-twrp44-unflashed"))
    a = p.parse_args()

    if a.outdir.exists():
        fail(f"output directory exists; will not overwrite: {a.outdir}")
    full_fota = read_full(a.twrp)
    actual_fota_sha = sha(full_fota)
    sidecar = Path(str(a.twrp) + ".sha256")
    if not sidecar.exists():
        fail("missing verified on-device sha256 sidecar: " + str(sidecar))
    first = sidecar.read_text(encoding="ascii").strip().split()
    if not first or not re.fullmatch(r"[0-9a-fA-F]{64}", first[0]):
        fail("malformed TWRP SHA256 sidecar")
    if actual_fota_sha != first[0].lower():
        fail("TWRP SHA256 differs from on-device verification sidecar")
    full_stable = read_full(a.stable_boot)
    if sha(full_stable) != STABLE_SHA256:
        fail("stable 4.4 backup SHA256 mismatch")

    twrp = parse_boot(full_fota, "FOTAKernel TWRP")
    stable = parse_boot(full_stable, "working LOS21 boot")
    print("=== SOURCE CHECK ===")
    print("TWRP partition SHA256:", actual_fota_sha)
    print("Stable boot SHA256:", STABLE_SHA256)
    for title, item in (("TWRP", twrp), ("Stable", stable)):
        print(f"{title}: kernel={item['kernel_size']} ramdisk={item['ramdisk_size']} "
              f"kernel_addr=0x{item['kernel_addr']:x} "
              f"ramdisk_addr=0x{item['ramdisk_addr']:x} "
              f"tags_addr=0x{item['tags_addr']:x}")
        print(f"{title} cmdline: {item['cmdline']}")

    for field in ("kernel_addr", "ramdisk_addr", "tags_addr", "page_size"):
        if twrp[field] != stable[field]:
            fail(f"TWRP and stable boot differ at {field}; "
                 "do not mix headers/ramdisks without manual layout audit")

    new_kernel, twrp_ramdisk = stable["kernel"], twrp["ramdisk"]
    if not new_kernel.startswith(b"\x1f\x8b"):
        fail("stable kernel payload does not start with gzip")
    if twrp["kernel_addr"] + len(new_kernel) > twrp["ramdisk_addr"]:
        fail("compressed 4.4 kernel overlaps TWRP ramdisk physical load address")
    if twrp["ramdisk_addr"] + len(twrp_ramdisk) > (1 << 32):
        fail("TWRP ramdisk physical end exceeds 32-bit boot address space")

    page = twrp["page_size"]
    header = bytearray(full_fota[:page])
    struct.pack_into("<I", header, 8, len(new_kernel))
    header[ID_START:ID_END] = boot_id(new_kernel, twrp_ramdisk)
    compact = (bytes(header) + new_kernel + bytes(align(len(new_kernel), page) - len(new_kernel))
               + twrp_ramdisk + bytes(align(len(twrp_ramdisk), page) - len(twrp_ramdisk)))
    if len(compact) > PARTITION:
        fail("hybrid does not fit FOTAKernel partition")
    full = bytearray(full_fota)
    full[:len(compact)] = compact
    full = bytes(full)
    result = parse_boot(compact, "generated compact TWRP 4.4")
    if result["kernel"] != new_kernel or result["ramdisk"] != twrp_ramdisk:
        fail("post-pack payload verification failed")
    for field in KEYS:
        if field != "kernel_size" and result[field] != twrp[field]:
            fail(f"TWRP header field {field} changed unexpectedly")
    if bytes(header[:8]) != full_fota[:8]:
        fail("Android header magic changed")
    expected_header = bytearray(full_fota[:page])
    expected_header[8:12] = header[8:12]
    expected_header[ID_START:ID_END] = header[ID_START:ID_END]
    if bytes(expected_header) != bytes(header):
        fail("header has unexpected changes")
    if full[len(compact):] != full_fota[len(compact):]:
        fail("recovery partition tail unexpectedly changed")

    manifest = {
        "status": "UNFLASHED; BOOTABILITY UNKNOWN",
        "description": "Existing TWRP ramdisk with verified working LineageOS 21 Linux 4.4 kernel",
        "twrp_partition_sha256": actual_fota_sha,
        "working_boot_sha256": STABLE_SHA256,
        "kernel_sha256": sha(new_kernel),
        "twrp_ramdisk_sha256": sha(twrp_ramdisk),
        "twrp_original_cmdline": twrp["cmdline"],
        "compact_size": len(compact),
        "compact_sha256": sha(compact),
        "full_partition_size": len(full),
        "full_partition_sha256": sha(full),
        "twrp_old_kernel_size": twrp["kernel_size"],
        "new_kernel_size": len(new_kernel),
        "original_twrp_ramdisk_size": len(twrp_ramdisk),
        "header_mismatch_stopped": False,
    }
    a.outdir.mkdir(parents=True, exist_ok=False)
    (a.outdir / "dora-twrp44-unflashed.img").write_bytes(compact)
    (a.outdir / "dora-twrp44-full64M-unflashed.img").write_bytes(full)
    (a.outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("=== NEW UNFLASHED RECOVERY IMAGE ===")
    print(json.dumps(manifest, indent=2))
    print("Output:", a.outdir)
    print("PASS: images packaged; NOTHING FLASHED; first-boot safety not established")


if __name__ == "__main__":
    main()
