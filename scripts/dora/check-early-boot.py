#!/usr/bin/env python3
"""Audit compiled early-boot dependencies and optionally inspect supplied artifacts.

Read-only: never constructs a boot image, extracts files, accesses a device or
qualifies hardware. Missing phone artifacts remain explicit readiness blockers.
"""
import argparse
import copy
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import struct
import zlib

ROOT = Path(__file__).resolve().parents[2]
PARSE = runpy.run_path(str(ROOT / "scripts/dora/check-power-dt.py"))["parse_fdt"]
VARIANTS = runpy.run_path(str(ROOT / "scripts/dora/check-sdhc1-diagnostic.py"))["PAIRS"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cells(value):
    require(len(value) % 4 == 0, "unaligned DT cells")
    return struct.unpack(">%dI" % (len(value) // 4), value)


def available(nodes, path):
    while path:
        if nodes[path]["props"].get("status", b"okay\0") not in (b"ok\0", b"okay\0"):
            return False
        path = path.rsplit("/", 1)[0]
    return True


def validate(nodes, config):
    for symbol in ("BLK_DEV_INITRD", "RD_GZIP", "RD_XZ", "EFI_PARTITION", "DEVTMPFS",
                   "MMC", "MMC_BLOCK", "MMC_SDHCI_MSM", "PSTORE", "PSTORE_RAM",
                   "PSTORE_CONSOLE", "QCOM_SMD_RPM", "QCOM_QFPROM", "MSM_GCC_8996"):
        require(f"CONFIG_{symbol}=y\n" in config, "missing built-in " + symbol)
    require('CONFIG_CMDLINE=""\n' in config, "unexpected compiled cmdline override")
    for symbol in ("CPU_FREQ", "CPU_IDLE"):
        require(f"# CONFIG_{symbol} is not set\n" in config, "unsafe CPU activation")
    tokens = nodes["/chosen"]["props"]["bootargs"].rstrip(b"\0").split()
    require([x for x in tokens if x.startswith(b"maxcpus=")] == [b"maxcpus=2"], "CPU limit changed")
    require(not any(x in tokens for x in (b"selinux=0", b"enforcing=0", b"androidboot.selinux=permissive")),
            "security bypass in DT cmdline")
    aliases = nodes["/aliases"]["props"]
    for name, path in (("mmc0", "/soc/sdhci@7464900"), ("sdhc1", "/soc/sdhci@7464900"),
                       ("mmc1", "/soc/sdhci@74a4900"), ("sdhc2", "/soc/sdhci@74a4900")):
        require(aliases.get(name) == path.encode() + b"\0", "unstable bootdevice alias: " + name)
    pstore = "/reserved-memory/ramoops@a7f00000"
    props = nodes[pstore]["props"]
    require(available(nodes, pstore) and props.get("compatible") == b"ramoops\0", "ramoops unavailable")
    require(cells(props.get("reg", b"")) == (0, 0xa7f00000, 0, 0x100000), "ramoops reservation changed")
    require(cells(props.get("record-size", b"")) == (0x20000,) and
            cells(props.get("console-size", b"")) == (0x40000,), "ramoops record/console changed")
    for name, dev, fs in (("system", "system", "ext4"), ("vendor", "oem", "squashfs")):
        props = nodes["/firmware/android/fstab/" + name]["props"]
        require(props.get("dev") == ("/dev/block/platform/soc/7464900.sdhci/by-name/" + dev).encode() + b"\0",
                "incorrect early mount device: " + name)
        require(props.get("type") == fs.encode() + b"\0", "incorrect early filesystem: " + name)
    diagnostic = "sony,sdhc1-diagnostic" in nodes["/"]["props"]
    require(available(nodes, "/soc/sdhci@7464900") == diagnostic, "unexpected SDHC1 activation policy")
    for path in ("/soc/sdhci@74a4900", "/soc/ufshc@624000"):
        require(not available(nodes, path), "unqualified storage enabled: " + path)
    watchdogs, serial = [], []
    for path, node in nodes.items():
        compatible = node["props"].get("compatible", b"").split(b"\0")
        if available(nodes, path):
            if any(x in compatible for x in (b"qcom,msm-watchdog", b"qcom,kpss-wdt")):
                watchdogs.append(path)
            if any(x in compatible for x in (b"qcom,msm-uartdm", b"qcom,msm-uartdm-v1.4")):
                serial.append(path)
    return dict(sdhc1_policy="experimental" if diagnostic else "disabled",
                pstore="compiled/reserved; persistence unverified", watchdog_nodes=watchdogs,
                watchdog_driver=("CONFIG_QCOM_WATCHDOG_V2=y\n" in config or "CONFIG_QCOM_WDT=y\n" in config),
                serial_nodes=serial, stdout_path=nodes["/chosen"]["props"].get("stdout-path", b"").rstrip(b"\0").decode(),
                actual_console="UNVERIFIED", actual_rpm_ack="UNVERIFIED", emmc_gpt="UNVERIFIED")


def boot_header(data, partition_size=None):
    require(len(data) >= 1632 and data[:8] == b"ANDROID!", "not a complete Android legacy boot header")
    ks, ka, rs, ra, ss, sa, ta, page, version, os_version = struct.unpack_from("<10I", data, 8)
    require(version == 0, "unsupported header version or legacy dt_size; inspect the original format first")
    require(2048 <= page <= 65536 and not page & (page - 1), "invalid boot page size")
    require(ks and rs, "missing kernel or ramdisk")
    offset, sections = page, {}
    for name, size in (("kernel", ks), ("ramdisk", rs), ("second", ss)):
        require(offset + size <= len(data), "truncated " + name)
        sections[name] = dict(offset=offset, size=size)
        offset += (size + page - 1) // page * page
    if partition_size is not None:
        require(partition_size > 0 and len(data) <= partition_size and offset <= partition_size,
                "boot payload exceeds supplied partition size")
    cmdline = (data[64:576].split(b"\0", 1)[0] + data[608:1632].split(b"\0", 1)[0]).decode("ascii")
    return dict(format="Android v0 (Sony wrapper/loader acceptance unverified)", page_size=page,
                kernel_address=ka, ramdisk_address=ra, second_address=sa, tags_address=ta,
                os_version=os_version, cmdline=cmdline, sections=sections,
                trailing_bytes=max(0, len(data) - offset),
                partition_size_verified=partition_size is not None)


def ramdisk_info(data, config):
    limit = 128 * 1024 * 1024
    if data.startswith(b"\x1f\x8b"):
        compression, symbol = "gzip", "RD_GZIP"
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        payload = decoder.decompress(data, limit + 1)
    elif data.startswith(b"\xfd7zXZ\0"):
        compression, symbol = "xz", "RD_XZ"
        decoder = lzma.LZMADecompressor(memlimit=limit)
        payload = decoder.decompress(data, max_length=limit + 1)
    else:
        compression, symbol, decoder, payload = "raw", None, None, data
    require(len(payload) <= limit, "ramdisk exceeds inspection bound")
    if decoder:
        require(decoder.eof and not decoder.unused_data, "truncated or concatenated ramdisk stream")
        require(f"CONFIG_{symbol}=y\n" in config, "unsupported ramdisk compression")
    offset, names, init = 0, set(), None
    while True:
        require(offset + 110 <= len(payload) and payload[offset:offset + 6] in (b"070701", b"070702"),
                "ramdisk is not a complete newc CPIO")
        magic = payload[offset:offset + 6]
        fields = [int(payload[offset + 6 + 8 * i:offset + 14 + 8 * i], 16) for i in range(13)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        offset += 110
        require(namesize > 0 and offset + namesize <= len(payload), "truncated CPIO name")
        name = payload[offset:offset + namesize]
        require(name[-1:] == b"\0", "unterminated CPIO name")
        name = name[:-1].decode()
        offset = (offset + namesize + 3) & ~3
        require(offset + size <= len(payload), "truncated CPIO content")
        if magic == b"070702":
            require(sum(payload[offset:offset + size]) & 0xffffffff == fields[12], "CPIO checksum mismatch")
        if name == "TRAILER!!!":
            require(size == 0, "CPIO trailer has content")
            break
        if name.removeprefix("./") == "init":
            init = dict(mode=oct(mode), bytes=size,
                        symlink=payload[offset:offset + size].decode() if mode & 0o170000 == 0o120000 else None)
        require(name not in names, "duplicate CPIO entry needs manual review")
        names.add(name)
        offset = (offset + size + 3) & ~3
    require(init is not None, "ramdisk has no init entry")
    return dict(compression=compression, entries=len(names), init=init,
                fstab_entries=sorted(x for x in names if "fstab" in x),
                userspace_compatibility="UNVERIFIED")


def selftest(nodes, config):
    for path, key, value in (("/aliases", "mmc0", b"/soc/ufshc@624000\0"),
                             ("/chosen", "bootargs", b"maxcpus=4\0"),
                             ("/chosen", "bootargs", b"maxcpus=2 enforcing=0\0"),
                             ("/reserved-memory/ramoops@a7f00000", "reg", b""),
                             ("/soc/sdhci@74a4900", "status", b"okay\0"),
                             ("/firmware/android/fstab/vendor", "dev", b"/dev/block/sda\0")):
        bad = copy.deepcopy(nodes)
        bad[path]["props"][key] = value
        try:
            validate(bad, config)
        except ValueError:
            continue
        raise ValueError("unsafe early-boot fixture escaped: " + key)
    # Header-parser fixtures stay in memory. No synthetic boot image is written.
    header = bytearray(4096 * 3)
    header[:8] = b"ANDROID!"
    struct.pack_into("<10I", header, 8, 1, 1, 1, 2, 0, 0, 3, 4096, 0, 0)
    boot_header(header, len(header))
    for offset, value in ((36, 0), (36, 4095), (40, 4), (8, len(header)), (16, len(header))):
        bad = bytearray(header)
        struct.pack_into("<I", bad, offset, value)
        try:
            boot_header(bad, len(header))
        except ValueError:
            continue
        raise ValueError("invalid header accepted")
    for bad, size in ((header[:100], None), (header, 4096)):
        try:
            boot_header(bad, size)
        except ValueError:
            continue
        raise ValueError("truncated/oversized header accepted")
    def entry(name, content):
        # Parser fixture only: never execute or write the dummy init bytes.
        fields = [0, 0o100755, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name) + 1, 0]
        record = b"070701" + b"".join(f"{x:08x}".encode() for x in fields) + name.encode() + b"\0"
        record += b"\0" * (-len(record) % 4)
        record += content
        return record + b"\0" * (-len(content) % 4)
    archive = entry("init", b"parser fixture") + entry("TRAILER!!!", b"")
    for data in (archive, lzma.compress(archive)):
        require(ramdisk_info(data, config)["init"]["bytes"] == 14, "ramdisk parser changed")
    for data in (archive[:100], entry("TRAILER!!!", b""), lzma.compress(archive)[:-4]):
        try:
            ramdisk_info(data, config)
        except ValueError:
            continue
        raise ValueError("invalid/missing-init ramdisk accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--diagnostic-out", type=Path)
    parser.add_argument("--boot-image", type=Path, help="existing reference image, read only")
    parser.add_argument("--partition-size", type=lambda x: int(x, 0), help="actual observed byte size")
    parser.add_argument("--ramdisk", type=Path, help="existing LOS21 ramdisk, read only")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    config = (args.out / ".config").read_text()
    report = dict(status="PASS (software checks only)", boot_test_ready=False, trees=[],
                  blockers=["writable CPU adapter/CPR/SPM ownership incomplete",
                            "watchdog handoff unqualified", "serial/USB transport unverified",
                            "bootloader DTB selection and merged cmdline unverified",
                            "firmware charging/DPDM ownership unqualified", "phone RPM/eMMC/GPT logs missing"])
    for base, _ in VARIANTS:
        paths = [args.out / "arch/arm64/boot/dts/qcom" / (base + ".dtb")]
        if args.diagnostic_out:
            paths.append(args.diagnostic_out / "arch/arm64/boot/dts/qcom/diagnostic" / (base + "-sdhc1-diagnostic.dtb"))
        for path in paths:
            nodes = PARSE(path)
            finding = validate(nodes, config)
            if args.self_test:
                selftest(nodes, config)
            report["trees"].append(dict(path=str(path), **finding))
    if args.boot_image:
        data = args.boot_image.read_bytes()
        report["reference_boot"] = dict(sha256=hashlib.sha256(data).hexdigest(), **boot_header(data, args.partition_size))
    else:
        report["blockers"].append("actual working boot.img unavailable")
    if args.ramdisk:
        data = args.ramdisk.read_bytes()
        report["los21_ramdisk"] = dict(sha256=hashlib.sha256(data).hexdigest(), **ramdisk_info(data, config))
    else:
        report["blockers"].append("actual LOS21 ramdisk unavailable")
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: early software dependencies/DT metadata; inspected %d trees; BOOT TEST READY: NO" % len(report["trees"]))
    print("Missing/unverified: " + "; ".join(report["blockers"]))


if __name__ == "__main__":
    main()
