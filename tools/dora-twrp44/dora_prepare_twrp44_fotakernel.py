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
import zlib
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

    # FOTAKernel/TeamWin header intentionally uses BASE=0 whereas the
    # working Android boot uses BASE=0x80000000. Sony recovery's own boot
    # path has been observed to boot with the original header; retain it.
    # Do NOT copy the stable header's addresses onto the TWRP image.
    for field, expected in (("kernel_addr", 0x8000),
                            ("ramdisk_addr", 0x02000000),
                            ("tags_addr", 0x01e00000), ("page_size", 4096)):
        if twrp[field] != expected:
            fail(f"unexpected TWRP FOTAKernel {field} = {twrp[field]:#x}")
    for field, expected in (("kernel_addr", 0x80008000),
                            ("ramdisk_addr", 0x81000000),
                            ("tags_addr", 0x80000100), ("page_size", 4096)):
        if stable[field] != expected:
            fail(f"unexpected working Linux 4.4 boot {field} = {stable[field]:#x}")
    print("NOTE: FOTAKernel uses legacy base-0 addresses; retaining ALL original "
          "TWRP header addresses. This is a bootloader-specific hypothesis.")

    new_kernel, twrp_ramdisk = stable["kernel"], twrp["ramdisk"]
    if not new_kernel.startswith(b"\x1f\x8b"):
        fail("stable kernel payload does not start with gzip")
    # The Sony bootloader's actual physical placement can differ from the
    # legacy header. Assume 0x80000000 + FOTAKernel offsets ONLY for an
    # initial overlap check, not as proof of runtime loader behaviour.
    base = 0x80000000
    compressed_start = base + twrp["kernel_addr"]
    compressed_end = compressed_start + len(new_kernel)
    header_ramdisk_start = base + twrp["ramdisk_addr"]
    if compressed_end > header_ramdisk_start:
        fail("compressed 4.4 kernel could overlap TWRP ramdisk under FOTA base hypothesis")

    dec = zlib.decompressobj(31)
    try:
        image = dec.decompress(new_kernel) + dec.flush()
    except zlib.error as e:
        fail(f"stable kernel gzip decompression failed: {e}")
    if not dec.eof or len(image) < 64 or image[56:60] != b"ARM\x64":
        fail("working 4.4 kernel payload does not contain a complete ARM64 Image")
    text_offset, image_size, image_flags = struct.unpack_from("<QQQ", image, 8)
    if text_offset != 0x80000 or not image_size or image_size < len(image):
        fail("unexpected ARM64 Image header size/offset; inspect before packaging")
    image_start = base + text_offset
    image_end = image_start + image_size

    # Verified on working 4.4 and original TWRP boots in this project:
    # /chosen/linux,initrd-start == 0x82200000, despite different header
    # ramdisk_addr fields. New image placement is still UNPROVEN.
    observed_initrd_start = 0x82200000
    observed_initrd_end = observed_initrd_start + len(twrp_ramdisk)
    splash_start = 0x83401000
    if image_end > observed_initrd_start:
        fail("working 4.4 ARM64 Image reserved range can overlap observed initrd start")
    if observed_initrd_end > splash_start:
        fail("TWRP ramdisk can collide with preserved contiguous splash region")
    print("=== INFERRED PHYSICAL LAYOUT (NOT VERIFIED FOR HYBRID) ===")
    print(f"4.4 Image reserved: [0x{image_start:x}, 0x{image_end:x})")
    print(f"FOTA base-mapped compressed kernel: [0x{compressed_start:x}, 0x{compressed_end:x})")
    print(f"FOTA header ramdisk (inferred): 0x{header_ramdisk_start:x}")
    print(f"Observed bootloader initrd start (4.4 and TWRP): 0x{observed_initrd_start:x}")
    print(f"TWRP initrd if retained at observed start: "
          f"[0x{observed_initrd_start:x}, 0x{observed_initrd_end:x})")
    print(f"Gap to splash carveout: {splash_start - observed_initrd_end:,} bytes")
    print(f"4.4 Image header flags: 0x{image_flags:x}, appended DTBs: {len(dec.unused_data):,} bytes")
    if twrp_ramdisk.startswith(b"\x1f\x8b"):
        ramdisk_format = "gzip"
    elif twrp_ramdisk.startswith(b"\xfd7zXZ\x00"):
        ramdisk_format = "xz"
    else:
        fail(f"unrecognized TWRP ramdisk compressor (first 12 bytes: {twrp_ramdisk[:12].hex()})")
    print("TWRP ramdisk compressor:", ramdisk_format)
    print("CAUTION: matching addresses do not prove FOTAKernel will load this hybrid.")

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
        "fota_header_addresses_preserved": True,
        "stable_4_4_kernel_reused": True,
        "fota_legacy_address_base_hypothesis": "0x80000000",
        "assumed_4_4_image_physical_start": hex(image_start),
        "assumed_4_4_image_physical_end": hex(image_end),
        "observed_original_4_4_and_twrp_initrd_start": hex(observed_initrd_start),
        "hypothetical_hybrid_twrp_initrd_end": hex(observed_initrd_end),
        "hypothetical_gap_to_splash_bytes": splash_start - observed_initrd_end,
        "ramdisk_format": ramdisk_format,
        "limitations": [
            "TWRP FOTAKernel base-0 address interpretation not independently verified",
            "Actual ramdisk placement after bootloader handoff is not guaranteed",
            "TWRP 3.18 userspace compatibility with Linux 4.4 remains untested",
            "No flashing is performed by this script",
        ],
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
