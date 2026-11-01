#!/usr/bin/env python3
"""Validate ARM64 build products and the exact ordered appended-DTB payload.

The reported Qualcomm ID tuples are source metadata, not proof of Sony
bootloader selection, firmware compatibility or a successful device boot.
"""
import argparse
import hashlib
import json
import runpy
import struct
import zlib
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(out, dtbs):
    root = Path(__file__).resolve().parents[2]
    parse_fdt = runpy.run_path(str(root / "scripts/dora/check-power-dt.py"))["parse_fdt"]
    boot = out / "arch/arm64/boot"
    image = (boot / "Image").read_bytes()
    require(len(image) >= 64, "truncated ARM64 Image header")
    require(image[56:60] == b"ARM\x64", "invalid ARM64 Image magic")
    text_offset, image_size, flags = struct.unpack_from("<QQQ", image, 8)
    require(image_size >= 64, "missing ARM64 image_size")
    require(flags & 1 == 0, "expected little-endian ARM64 Image")

    elf = (out / "vmlinux").read_bytes()
    require(len(elf) >= 64 and elf[:6] == b"\x7fELF\x02\x01",
            "vmlinux is not ELF64 little-endian")
    require(struct.unpack_from("<HH", elf, 16) == (2, 183),
            "vmlinux is not an AArch64 executable")

    compressed = (boot / "Image.gz").read_bytes()
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    unpacked = inflater.decompress(compressed) + inflater.flush()
    require(inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail,
            "Image.gz has a truncated stream or trailing data")
    require(unpacked == image, "Image.gz does not decompress to Image")

    reference = json.loads((root / "Documentation/sony/dora-original-dtb-ids.json").read_text())
    keys = ("qcom,msm-id", "qcom,board-id", "qcom,pmic-id")
    known = {tuple(tuple(int(v, 0) for v in entry[key].split()) for key in keys)
             for entry in reference}
    seen, entries = set(), []
    combined = (boot / "Image.gz-dtb").read_bytes()
    require(combined[:len(compressed)] == compressed, "appended image gzip prefix differs")
    offset = len(compressed)
    paths = [out / "vmlinux", out / ".config", boot / "Image", boot / "Image.gz"]
    for name in dtbs:
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts
                and relative.suffix == ".dtb", "invalid DTB relative path: " + name)
        path = boot / "dts" / relative
        nodes = parse_fdt(path)
        props = nodes["/"]["props"]
        identity = []
        for key, count in zip(keys, (2, 2, 4)):
            value = props.get(key, b"")
            require(len(value) == count * 4, "expected one %s tuple in %s" % (key, name))
            identity.append(struct.unpack(">%dI" % count, value))
        identity = tuple(identity)
        require(identity in known, "DTB IDs differ from the original Dora tuples: " + name)
        require(identity not in seen, "duplicate appended hardware tuple: " + name)
        seen.add(identity)
        data = path.read_bytes()
        require(combined[offset:offset + len(data)] == data,
                "appended DTB missing, changed or out of order: " + name)
        entries.append(dict(path=name, offset=offset, size=len(data),
                            **{key: list(value) for key, value in zip(keys, identity)}))
        offset += len(data)
        paths.append(path)
    require(offset == len(combined), "unexpected trailing bytes or stale appended DTBs")
    paths.append(boot / "Image.gz-dtb")
    artifacts = []
    for path in paths:
        data = path.read_bytes()
        artifacts.append(dict(path=str(path.relative_to(out)), size=len(data),
                              sha256=hashlib.sha256(data).hexdigest()))
    return dict(status="PASS", scope="build formats and exact DTB payload only",
                bootloader_selection="UNVERIFIED", device_boot="UNTESTED",
                image_header=dict(text_offset=text_offset, image_size=image_size, flags=flags),
                appended_dtbs=entries, artifacts=artifacts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="kernel O= output directory")
    parser.add_argument("--dtb", action="append", required=True,
                        help="exact ordered path relative to arch/arm64/boot/dts (repeatable)")
    parser.add_argument("--report", type=Path, help="write JSON validation evidence")
    args = parser.parse_args()
    try:
        result = validate(args.out, args.dtb)
    except (OSError, ValueError, KeyError, IndexError, struct.error, zlib.error) as error:
        print("FAIL:", error)
        return 1
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    print("PASS: AArch64 vmlinux, ARM64 Image, gzip round-trip and %d ordered DTB(s)" % len(args.dtb))
    print("Sony bootloader selection and device boot remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
