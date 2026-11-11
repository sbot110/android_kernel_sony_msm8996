#!/usr/bin/env python3
"""Inspect supplied boot artifacts read-only; never build or qualify a boot image.

Supports unwrapped Android v0, ARM64 Image, gzip/XZ, newc initramfs and FDT v17.
Unknown wrappers/versions/layouts require separate analysis. Parser fixtures stay
in memory. Header addresses and DT tuples do not prove bootloader behaviour.
"""
import argparse
import gzip
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import zlib

MAX_INPUT = 512 * 1024 * 1024
MAX_KERNEL = 256 * 1024 * 1024
MAX_RAMDISK = 128 * 1024 * 1024
MAX_FDT = 8 * 1024 * 1024
MAX_DTBS = 32
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def align(value, boundary):
    return (value + boundary - 1) & -boundary


def read_artifact(path, limit=MAX_INPUT):
    # Bound the read itself, including files that grow after stat(). No extraction,
    # device access, or writes to an input path are performed.
    require(path.is_file(), "artifact is not a regular file: " + str(path))
    require(path.stat().st_size <= limit, "artifact exceeds input bound")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    require(len(data) <= limit, "artifact exceeds input bound")
    return data


def cstring(data, label):
    require(b"\0" in data, "unterminated " + label)
    value, padding = data.split(b"\0", 1)
    require(not any(padding), "nonzero bytes after " + label)
    return value


def cmdline_info(value):
    text = value.decode("ascii")
    require(all(32 <= ord(x) <= 126 or x == "\t" for x in text),
            "control character in cmdline")
    tokens = text.split()
    keyed = {}
    for token in tokens:
        key, _, item = token.partition("=")
        keyed.setdefault(key, []).append(item)
    unsafe = [token for token in tokens if token in
              ("selinux=0", "enforcing=0", "androidboot.selinux=permissive")]
    return dict(text=text, duplicate_keys={k: v for k, v in keyed.items() if len(v) > 1},
                security_bypasses=unsafe, maxcpus=keyed.get("maxcpus", []),
                console=keyed.get("console", []), earlycon=keyed.get("earlycon", []),
                bootdevice=keyed.get("androidboot.bootdevice", []),
                effective_bootloader_cmdline="UNVERIFIED")


def boot_header(data, partition_size=None):
    require(len(data) >= 1632 and data[:8] == b"ANDROID!",
            "unsupported wrapper or truncated Android header (ELF/Sony wrappers are not inferred)")
    ks, ka, rs, ra, ss, sa, ta, page, version, os_version = struct.unpack_from("<10I", data, 8)
    require(version == 0, "unsupported header version/legacy dt_size: %d" % version)
    require(2048 <= page <= 65536 and not page & (page - 1), "invalid boot page size")
    require(ks and rs, "Android v0 inspection requires kernel and ramdisk")
    require(len(data) >= page, "truncated header page")
    sections, offset = {}, page
    for name, size, address in (("kernel", ks, ka), ("ramdisk", rs, ra), ("second", ss, sa)):
        end = offset + size
        padded_end = offset + align(size, page)
        require(end <= len(data) and padded_end <= len(data), "truncated/padding-truncated " + name)
        sections[name] = dict(offset=offset, size=size, padded_end=padded_end, address=address)
        offset = padded_end
    if partition_size is not None:
        require(partition_size > 0, "invalid supplied partition size")
        require(len(data) <= partition_size and offset <= partition_size,
                "image exceeds supplied partition size")
    first, extra = data[64:576], data[608:1632]
    # The first 512-byte slot may be full when extra_cmdline is used.
    first_text = cstring(first, "cmdline slot") if b"\0" in first else first
    extra_text = cstring(extra, "extra cmdline slot")
    name = data[48:64]
    name = cstring(name, "board name") if b"\0" in name else name
    return dict(format="Android v0", header_version=version, page_size=page,
                os_version=os_version, name=name.decode("ascii"),
                cmdline=cmdline_info(first_text + extra_text), tags_address=ta,
                sections=sections, payload_padded_end=offset, trailing_bytes=len(data) - offset,
                trailing_sha256=hashlib.sha256(data[offset:]).hexdigest() if len(data) > offset else None,
                partition_size=dict(supplied_bytes=partition_size,
                                    checked=partition_size is not None,
                                    provenance="caller must supply observed GPT size; no BoardConfig default"),
                loader_acceptance="UNVERIFIED", relocation_and_entry="UNVERIFIED")


def decompress(data, limit, kind):
    require(data, "empty " + kind)
    if data.startswith(b"\x1f\x8b"):
        compression = "gzip"
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = decoder.decompress(data, limit + 1)
    elif data.startswith(b"\xfd7zXZ\0"):
        compression = "xz"
        decoder = lzma.LZMADecompressor(memlimit=MAX_KERNEL)
        output = decoder.decompress(data, max_length=limit + 1)
    else:
        require(len(data) <= limit, kind + " exceeds inspection bound")
        return "raw", data, b"", len(data)
    require(len(output) <= limit, kind + " exceeds expanded inspection bound")
    require(decoder.eof, "truncated or expansion-limited " + kind + " stream")
    tail = decoder.unused_data
    return compression, output, tail, len(data) - len(tail)


def be_cells(data):
    require(len(data) % 4 == 0, "unaligned FDT cells")
    return list(struct.unpack(">%dI" % (len(data) // 4), data))


def fdt_info(data):
    require(40 <= len(data) <= MAX_FDT, "truncated/oversized FDT header/blob")
    magic, total, so, sto, ro, version, compat, cpu, strs, stsz = struct.unpack_from(">10I", data)
    require(magic == 0xd00dfeed and 40 <= total == len(data), "invalid FDT total size/magic")
    require(version == 17 and compat <= 17, "unsupported FDT version")
    require(so % 4 == 0 and ro % 8 == 0, "unaligned FDT block")
    require(so >= 40 and sto >= 40 and ro >= 40 and so + stsz <= total and sto + strs <= total,
            "FDT block outside blob")
    require(stsz >= 4 and not (so < sto + strs and sto < so + stsz), "overlapping FDT blocks")
    reserved, rpos = [], ro
    while True:
        require(rpos + 16 <= total, "unterminated FDT reserve map")
        require(not (rpos < so + stsz and so < rpos + 16) and
                not (rpos < sto + strs and sto < rpos + 16), "FDT reserve map overlaps block")
        start, size = struct.unpack_from(">QQ", data, rpos)
        rpos += 16
        if start == size == 0:
            break
        require(size and start + size <= 1 << 64, "invalid FDT reserve range")
        reserved.append(dict(source="FDT memreserve", start=start, size=size))
    block, strings = data[so:so + stsz], data[sto:sto + strs]
    nodes, stack, offset, done, properties = {}, [], 0, False, 0
    while offset + 4 <= len(block):
        token = struct.unpack_from(">I", block, offset)[0]
        offset += 4
        if token == 1:
            end = block.find(b"\0", offset)
            require(end >= 0, "unterminated FDT node")
            name = block[offset:end].decode("ascii")
            require("/" not in name and len(name) <= 1024, "invalid/oversized FDT node name")
            offset = align(end + 1, 4)
            require(offset <= len(block), "truncated FDT node padding")
            if not stack:
                require(not nodes and name == "", "invalid/duplicate FDT root")
                path = "/"
            else:
                path = stack[-1].rstrip("/") + "/" + name
            require(path not in nodes, "duplicate FDT node")
            require(len(nodes) < 16384 and len(stack) < 64 and len(path) <= 4096,
                    "FDT node/depth/path bound exceeded")
            nodes[path] = {}
            stack.append(path)
        elif token == 2:
            require(stack, "FDT node stack underflow")
            stack.pop()
        elif token == 3:
            require(stack and offset + 8 <= len(block), "FDT property outside node/truncated")
            length, nameoff = struct.unpack_from(">II", block, offset)
            offset += 8
            require(nameoff < len(strings), "FDT property name outside strings")
            end = strings.find(b"\0", nameoff)
            require(end >= 0, "unterminated FDT property name")
            name = strings[nameoff:end].decode("ascii")
            require(name and len(name) <= 1024 and name not in nodes[stack[-1]], "empty/duplicate/oversized FDT property")
            properties += 1
            require(properties <= 65536, "FDT property count bound exceeded")
            require(offset + length <= len(block) and align(offset + length, 4) <= len(block),
                    "truncated FDT property/padding")
            nodes[stack[-1]][name] = block[offset:offset + length]
            offset = align(offset + length, 4)
        elif token == 4:
            continue
        elif token == 9:
            require(not stack and "/" in nodes, "unbalanced FDT nodes")
            require(not any(block[offset:]), "nonzero structure bytes after FDT end")
            done = True
            break
        else:
            raise ValueError("invalid FDT token: %d" % token)
    require(done, "missing FDT end")
    root, chosen = nodes["/"], nodes.get("/chosen", {})
    metadata = {}
    for key in ("qcom,msm-id", "qcom,board-id", "qcom,pmic-id"):
        if key in root:
            require(len(root[key]) <= 4096, "oversized DTB selection metadata")
            metadata[key] = be_cells(root[key])
    dyn = []
    parent = nodes.get("/reserved-memory", {})
    for path, props in nodes.items():
        if not path.startswith("/reserved-memory/") or path.count("/") != 2:
            continue
        if props.get("status", b"okay\0") not in (b"okay\0", b"ok\0"):
            continue
        if "reg" not in props:
            dyn.append(path)
            continue
        ac = be_cells(parent.get("#address-cells", root.get("#address-cells", struct.pack(">I", 2))))
        sc = be_cells(parent.get("#size-cells", root.get("#size-cells", struct.pack(">I", 1))))
        require(len(ac) == len(sc) == 1 and ac[0] in (1, 2) and sc[0] in (1, 2),
                "unsupported reserved-memory cell counts")
        require(not parent.get("ranges", b""), "translated reserved-memory ranges need separate analysis")
        values = be_cells(props["reg"])
        width = ac[0] + sc[0]
        require(values and len(values) % width == 0, "invalid reserved-memory reg")
        for pos in range(0, len(values), width):
            start = int.from_bytes(props["reg"][pos * 4:(pos + ac[0]) * 4], "big")
            size = int.from_bytes(props["reg"][(pos + ac[0]) * 4:(pos + width) * 4], "big")
            require(size and start + size <= 1 << 64, "invalid reserved-memory range")
            reserved.append(dict(source=path, start=start, size=size))
    def textprop(props, key):
        return cstring(props[key], key).decode("ascii") if key in props else None
    aliases = {key: textprop(nodes.get("/aliases", {}), key) for key in ("mmc0", "sdhc1", "mmc1", "sdhc2")}
    initrd = {}
    for key in ("linux,initrd-start", "linux,initrd-end"):
        if key in chosen:
            require(len(chosen[key]) in (4, 8), "unsupported DT initrd address width")
            initrd[key] = int.from_bytes(chosen[key], "big")
    return dict(bytes=total, boot_cpuid_phys=cpu, selection_tuples=metadata,
                selected_by_bootloader="UNVERIFIED", reserved_ranges=reserved,
                dynamic_reserved_nodes=dyn, dt_bootargs=textprop(chosen, "bootargs"),
                stdout_path=textprop(chosen, "stdout-path"), mmc_aliases=aliases,
                declared_initrd=initrd,
                sha256=hashlib.sha256(data).hexdigest())


def appended_dtbs(tail):
    trees, offset = [], 0
    while offset < len(tail):
        if not any(tail[offset:]):
            return trees, len(tail) - offset
        require(tail[offset:offset + 4] == FDT_MAGIC and offset + 40 <= len(tail),
                "unknown kernel stream trailer (concatenated compression/wrapper is unsupported)")
        size = struct.unpack_from(">I", tail, offset + 4)[0]
        require(size >= 40 and offset + size <= len(tail), "truncated appended FDT")
        require(len(trees) < MAX_DTBS, "appended FDT count bound exceeded")
        trees.append(dict(trailer_offset=offset, **fdt_info(tail[offset:offset + size])))
        offset += size
    return trees, 0


def kernel_info(data):
    compression, image, tail, consumed = decompress(data, MAX_KERNEL, "kernel")
    require(len(image) >= 64 and image[56:60] == b"ARM\x64", "kernel is not an ARM64 Image")
    text_offset, image_size, flags = struct.unpack_from("<QQQ", image, 8)
    require(image_size == 0 or 64 <= image_size <= MAX_KERNEL, "invalid/oversized ARM64 image_size")
    # ARM64 image_size includes run-time/BSS memory and can exceed file bytes.
    # A zero value is a legacy unknown extent; never substitute a guessed size.
    if compression == "raw":
        trees, padding, layout = [], 0, "raw Image trailing DTB boundary is not inferred"
    else:
        trees, padding = appended_dtbs(tail)
        layout = "single %s stream followed by zero or more complete FDTs" % compression
    return dict(compression=compression, compressed_stream_bytes=consumed,
                decompressed_file_bytes=len(image), text_offset=text_offset,
                image_size=image_size, runtime_extent=max(image_size, len(image)) if image_size else None,
                flags=flags, little_endian=not bool(flags & 1), layout=layout,
                appended_dtbs=trees, trailing_zero_padding=padding,
                entry_and_relocation="UNVERIFIED", sha256=hashlib.sha256(data).hexdigest())


def ramdisk_info(data, config=None):
    compression, payload, tail, _ = decompress(data, MAX_RAMDISK, "ramdisk")
    require(not tail, "concatenated/trailing compressed ramdisk streams need separate analysis")
    symbol = {"gzip": "RD_GZIP", "xz": "RD_XZ"}.get(compression)
    if config is not None and symbol:
        require("CONFIG_%s=y\n" % symbol in config, "ramdisk compression is not built in")
    names, offset, init = set(), 0, None
    while True:
        require(offset + 110 <= len(payload) and payload[offset:offset + 6] in (b"070701", b"070702"),
                "ramdisk is not a complete newc CPIO")
        magic = payload[offset:offset + 6]
        encoded = payload[offset + 6:offset + 110]
        require(all(x in b"0123456789abcdefABCDEF" for x in encoded), "invalid CPIO hexadecimal field")
        fields = [int(encoded[i:i + 8], 16) for i in range(0, 104, 8)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        offset += 110
        require(1 < namesize <= 4096 and offset + namesize <= len(payload), "invalid/truncated CPIO name")
        raw_name = payload[offset:offset + namesize]
        require(raw_name[-1:] == b"\0" and b"\0" not in raw_name[:-1], "invalid CPIO name terminator")
        name = raw_name[:-1].decode("utf-8")
        trailer = name == "TRAILER!!!"
        require(not name.startswith("/") and ".." not in name.split("/"), "unsafe CPIO path")
        name = str(PurePosixPath(name))
        offset = align(offset + namesize, 4)
        require(offset + size <= len(payload) and align(offset + size, 4) <= len(payload), "truncated CPIO content/padding")
        content = payload[offset:offset + size]
        require((sum(content) & 0xffffffff == fields[12]) if magic == b"070702" else fields[12] == 0,
                "invalid CPIO checksum")
        offset = align(offset + size, 4)
        if trailer:
            require(size == 0, "CPIO trailer has content")
            require(not any(payload[offset:]), "nonzero data/second archive after CPIO trailer needs separate analysis")
            break
        require(name not in names, "duplicate/canonical-duplicate CPIO entry")
        names.add(name)
        require(len(names) <= 100000, "CPIO entry count exceeds bound")
        if name == "init":
            require(stat.S_ISLNK(mode) or stat.S_ISREG(mode), "init is not a regular file or symlink")
            require(stat.S_ISLNK(mode) or mode & 0o111, "init is not executable")
            init = dict(mode=oct(mode), bytes=size, hardlink_count=fields[4],
                        symlink=content.decode("utf-8") if stat.S_ISLNK(mode) else None)
    require(init is not None, "ramdisk has no init entry")
    return dict(compression=compression, expanded_bytes=len(payload), entries=len(names), init=init,
                fstab_entries=sorted(x for x in names if "fstab" in x),
                sha256=hashlib.sha256(data).hexdigest(),
                config_compression_checked=config is not None,
                interpreter_linker_policy_and_mount_writes="UNVERIFIED",
                userspace_compatibility="UNVERIFIED")


def overlap(a, b):
    return a["start"] < b["start"] + b["size"] and b["start"] < a["start"] + a["size"]


def declared_layout(header, kernel, supplied_ranges):
    sections = header["sections"]
    ranges = [dict(source="header ramdisk", start=sections["ramdisk"]["address"], size=sections["ramdisk"]["size"])]
    if sections["second"]["size"]:
        ranges.append(dict(source="header second", start=sections["second"]["address"], size=sections["second"]["size"]))
    if kernel["runtime_extent"]:
        ranges.append(dict(source="header kernel expanded extent", start=sections["kernel"]["address"], size=kernel["runtime_extent"]))
    findings = []
    for i, left in enumerate(ranges):
        for right in ranges[i + 1:]:
            if overlap(left, right):
                findings.append([left["source"], right["source"]])
    candidates = []
    # Test each candidate independently. tags_addr is only a header field; it
    # does not prove which tree is loaded there or any bootloader relocation.
    for index, tree in enumerate(kernel["appended_dtbs"]):
        dt_range = dict(source="header tags candidate", start=header["tags_address"], size=tree["bytes"])
        hazards = []
        for loaded in ranges + [dt_range]:
            for reserved in tree["reserved_ranges"] + supplied_ranges:
                if overlap(loaded, reserved):
                    hazards.append([loaded["source"], reserved["source"]])
        for loaded in ranges:
            if overlap(loaded, dt_range):
                hazards.append([loaded["source"], dt_range["source"]])
        candidates.append(dict(index=index, overlaps=hazards))
    supplied_hazards = [[loaded["source"], reserved["source"]]
                        for loaded in ranges for reserved in supplied_ranges if overlap(loaded, reserved)]
    return dict(address_basis="declared header fields only; physical relocation/ownership UNVERIFIED",
                ranges=ranges, load_overlaps=findings, supplied_range_overlaps=supplied_hazards,
                dtb_candidates=candidates, selected_dtb="UNVERIFIED",
                expanded_kernel_extent_known=kernel["runtime_extent"] is not None)


def inspect_boot(data, partition_size=None, config=None, reserved=None):
    header = boot_header(data, partition_size)
    def section(name):
        item = header["sections"][name]
        return data[item["offset"]:item["offset"] + item["size"]]
    kernel = kernel_info(section("kernel"))
    ramdisk = ramdisk_info(section("ramdisk"), config)
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), header=header,
                kernel=kernel, ramdisk=ramdisk, layout=declared_layout(header, kernel, reserved or []))


def compare_boot(left, right):
    fields = {}
    for key in ("bytes",):
        fields[key] = [left[key], right[key]]
    for key in ("format", "page_size", "os_version", "tags_address", "sections", "cmdline", "trailing_bytes"):
        fields["header." + key] = [left["header"][key], right["header"][key]]
    for key in ("compression", "compressed_stream_bytes", "decompressed_file_bytes", "text_offset", "image_size", "appended_dtbs"):
        fields["kernel." + key] = [left["kernel"][key], right["kernel"][key]]
    for key in ("compression", "expanded_bytes", "entries", "init", "fstab_entries", "sha256"):
        fields["ramdisk." + key] = [left["ramdisk"][key], right["ramdisk"][key]]
    return dict(differences={key: value for key, value in fields.items() if value[0] != value[1]},
                bootloader_and_userspace_equivalence="UNVERIFIED")


def selftest():
    # All synthetic headers/CPIO/FDT payloads remain in memory and are never
    # written or executed. Their addresses are arbitrary parser fixtures.
    checks = 0
    def rejects(fn, *args):
        nonlocal checks
        try:
            fn(*args)
        except (ValueError, struct.error, zlib.error, lzma.LZMAError):
            checks += 1
            return
        raise AssertionError("unsafe parser fixture accepted: " + fn.__name__)
    def entry(name, content=b"", mode=0o100755):
        fields = [0, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name.encode()) + 1, 0]
        result = b"070701" + b"".join(("%08x" % x).encode() for x in fields) + name.encode() + b"\0"
        result += b"\0" * (-len(result) % 4)
        result += content
        return result + b"\0" * (-len(content) % 4)
    archive = entry("init", b"not executable fixture bytes") + entry("TRAILER!!!")
    for payload in (archive, gzip.compress(archive), lzma.compress(archive)):
        require(ramdisk_info(payload)["entries"] == 1, "ramdisk positive fixture")
        checks += 1
    for payload in (archive[:100], archive[:-1], archive + b"X", archive + archive,
                    entry("init") + entry("./init") + entry("TRAILER!!!"),
                    entry("../init") + entry("TRAILER!!!"), entry("/init") + entry("TRAILER!!!"),
                    entry("init", mode=0o100644) + entry("TRAILER!!!"), entry("TRAILER!!!"),
                    gzip.compress(archive)[:-4], gzip.compress(archive) + gzip.compress(archive),
                    lzma.compress(archive)[:-4], lzma.compress(archive) + b"X"):
        rejects(ramdisk_info, payload)
    bad = bytearray(archive)
    bad[6] = ord("-")
    rejects(ramdisk_info, bad)
    bad = bytearray(archive)
    bad[102:110] = b"00000001"  # nonzero checksum in newc without CRC
    rejects(ramdisk_info, bad)
    bad = bytearray(archive)
    bad[0:6] = b"070702"  # CRC variant with an incorrect checksum
    rejects(ramdisk_info, bad)
    rejects(ramdisk_info, entry("init", mode=0o040755) + entry("TRAILER!!!"))
    rejects(ramdisk_info, entry("init") + entry("TRAILER!!!", b"X"))
    rejects(ramdisk_info, gzip.compress(archive), "# CONFIG_RD_GZIP is not set\n")
    for payload in (gzip.compress(b"x" * 4096), lzma.compress(b"x" * 4096)):
        rejects(decompress, payload, 32, "fixture")
    image = bytearray(128)
    image[56:60] = b"ARM\x64"
    struct.pack_into("<QQQ", image, 8, 64, 4096, 0)
    # A complete minimal v17 FDT with a real in-memory reservation and tuple.
    strings = b"qcom,msm-id\0"
    block = struct.pack(">I", 1) + b"\0" * 4
    block += struct.pack(">III", 3, 8, 0) + struct.pack(">II", 246, 0)
    block += struct.pack(">II", 2, 9)
    reserve = struct.pack(">QQQQ", 0x1800, 0x100, 0, 0)
    fdt = struct.pack(">10I", 0xd00dfeed, 40 + len(reserve) + len(block) + len(strings),
                      40 + len(reserve), 40 + len(reserve) + len(block), 40, 17, 16, 0, len(strings), len(block))
    fdt += reserve + block + strings
    for compressed in (gzip.compress(image), lzma.compress(image)):
        info = kernel_info(compressed + fdt + fdt)
        require(len(info["appended_dtbs"]) == 2 and info["runtime_extent"] == 4096, "kernel/FDT positive fixture")
        checks += 1
        rejects(kernel_info, compressed + fdt[:-1])
        rejects(kernel_info, compressed + compressed)
        rejects(kernel_info, compressed[:-2])
    require(fdt_info(fdt)["reserved_ranges"][0]["start"] == 0x1800, "reservation fixture")
    checks += 1
    for offset, value in ((4, 0xffffffff), (8, 4), (12, 40), (16, 72), (20, 18), (32, 0xffffffff), (36, 0xffffffff)):
        bad = bytearray(fdt)
        struct.pack_into(">I", bad, offset, value)
        rejects(fdt_info, bad)
    bad = bytearray(fdt)
    struct.pack_into(">I", bad, 72, 2)  # node stack underflow
    rejects(fdt_info, bad)
    bad = bytearray(fdt)
    struct.pack_into(">QQ", bad, 40, (1 << 64) - 1, 2)
    rejects(fdt_info, bad)
    bad = bytearray(fdt)
    struct.pack_into(">I", bad, 88, 0xffffffff)  # property string offset
    rejects(fdt_info, bad)
    bad = bytearray(fdt)
    struct.pack_into(">I", bad, 84, len(fdt))  # property size beyond structure
    rejects(fdt_info, bad)
    rejects(appended_dtbs, fdt * (MAX_DTBS + 1))
    bad = bytearray(image)
    struct.pack_into("<Q", bad, 16, MAX_KERNEL + 1)
    rejects(kernel_info, gzip.compress(bad))
    rejects(kernel_info, gzip.compress(b"wrong ARM64 header"))
    unknown_extent = bytearray(image)
    struct.pack_into("<Q", unknown_extent, 16, 0)
    require(kernel_info(gzip.compress(unknown_extent))["runtime_extent"] is None,
            "legacy zero image_size was replaced by a guessed extent")
    checks += 1
    kernel = gzip.compress(image) + fdt
    ramdisk = gzip.compress(archive)
    header = bytearray(2048)
    header[:8] = b"ANDROID!"
    struct.pack_into("<10I", header, 8, len(kernel), 0x1000, len(ramdisk), 0x8000, 0, 0, 0x9000, 2048, 0, 0)
    blob = bytes(header) + kernel + b"\0" * (-len(kernel) % 2048)
    blob += ramdisk + b"\0" * (-len(ramdisk) % 2048)
    info = inspect_boot(blob, len(blob))
    require(info["layout"]["dtb_candidates"][0]["overlaps"], "expanded BSS reservation overlap missed")
    require(not compare_boot(info, info)["differences"], "comparison fixture")
    checks += 2
    for offset, value in ((36, 0), (36, 4095), (40, 1), (40, 2), (40, 4), (8, len(blob)), (16, 0)):
        bad = bytearray(blob)
        struct.pack_into("<I", bad, offset, value)
        rejects(boot_header, bad)
    rejects(boot_header, blob[:-1])  # old parser accepted missing final page padding
    rejects(boot_header, blob, len(blob) - 1)
    rejects(boot_header, b"\x7fELF" + blob[4:])
    bad = bytearray(blob)
    bad[64:70] = b"x\0junk"
    rejects(boot_header, bad)
    bad = bytearray(blob)
    bad[64:576] = b"x" * 512
    bad[608:1632] = b"x" * 1024
    rejects(boot_header, bad)
    require(cmdline_info(b"maxcpus=2 enforcing=0 maxcpus=4")["security_bypasses"] == ["enforcing=0"], "security reporting fixture")
    require("maxcpus" in cmdline_info(b"maxcpus=2 maxcpus=4")["duplicate_keys"], "cmdline duplicate fixture")
    checks += 2
    rejects(cmdline_info, b"maxcpus=2\nselinux=0")
    require(overlap(dict(start=1, size=2), dict(start=2, size=1)) and
            not overlap(dict(start=1, size=1), dict(start=2, size=1)), "half-open range fixture")
    checks += 1
    return checks


def supplied_range(value):
    try:
        start, size = (int(x, 0) for x in value.split(":"))
        require(start >= 0 and size > 0 and start + size <= 1 << 64, "invalid reserved range")
        return dict(source="caller supplied observed range " + value, start=start, size=size)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected observed START:SIZE: " + str(error)) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-image", type=Path, help="existing working reference image; read only")
    parser.add_argument("--compare", type=Path, help="second existing image (for example LOS21); read only")
    parser.add_argument("--partition-size", type=lambda x: int(x, 0), help="actually observed first boot GPT size in bytes")
    parser.add_argument("--compare-partition-size", type=lambda x: int(x, 0), help="actually observed second boot GPT size")
    parser.add_argument("--kernel", type=Path, help="existing Image.gz-dtb/Image; format inspection only")
    parser.add_argument("--ramdisk", type=Path, help="existing supplied ramdisk; read only")
    parser.add_argument("--dtb", type=Path, action="append", default=[], help="existing DTB; metadata inspection only")
    parser.add_argument("--config", type=Path, help="actual final kernel .config for compression support")
    parser.add_argument("--reserved-range", type=supplied_range, action="append", default=[], help="observed START:SIZE; no guessed defaults")
    parser.add_argument("--bootloader-cmdline", type=Path, help="existing text captured from actual bootloader/kernel log")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path, help="JSON report path; never an input artifact")
    args = parser.parse_args()
    parser.error("--compare requires --boot-image") if args.compare and not args.boot_image else None
    parser.error("--partition-size requires --boot-image") if args.partition_size is not None and not args.boot_image else None
    parser.error("--compare-partition-size requires --compare") if args.compare_partition_size is not None and not args.compare else None
    inputs = [x for x in (args.boot_image, args.compare, args.kernel, args.ramdisk, args.config, args.bootloader_cmdline) if x] + args.dtb
    if args.report:
        require(all(args.report.resolve() != path.resolve() for path in inputs), "report must not overwrite an input")
        if args.report.exists():
            require(all(not args.report.samefile(path) for path in inputs), "report aliases an input inode")
    config = read_artifact(args.config, 1024 * 1024).decode("utf-8") if args.config else None
    report = dict(boot_test_ready=False, runtime_verified=False, status="software inspection only",
                  selftest_checks=selftest() if args.self_test else 0, supplied_reserved_ranges=args.reserved_range)
    if args.boot_image:
        report["reference_boot"] = dict(path=str(args.boot_image), **inspect_boot(read_artifact(args.boot_image), args.partition_size, config, args.reserved_range))
    if args.compare:
        report["comparison_boot"] = dict(path=str(args.compare), **inspect_boot(read_artifact(args.compare), args.compare_partition_size, config, args.reserved_range))
        report["comparison"] = compare_boot(report["reference_boot"], report["comparison_boot"])
    if args.kernel:
        report["kernel_artifact"] = dict(path=str(args.kernel), **kernel_info(read_artifact(args.kernel)))
    if args.ramdisk:
        report["ramdisk_artifact"] = dict(path=str(args.ramdisk), **ramdisk_info(read_artifact(args.ramdisk), config))
    report["dtb_artifacts"] = [dict(path=str(path), **fdt_info(read_artifact(path))) for path in args.dtb]
    if args.bootloader_cmdline:
        value = read_artifact(args.bootloader_cmdline, 65536).rstrip(b"\r\n")
        report["supplied_observed_cmdline"] = dict(path=str(args.bootloader_cmdline), **cmdline_info(value))
    report["missing_evidence"] = ["phone kernel-entry/RPM/eMMC/GPT logs", "actual DTB selection and relocation logs",
                                  "watchdog/CPU/charging handoff qualification", "userspace init/SELinux/APEX runtime validation"]
    if not args.boot_image:
        report["missing_evidence"].append("working original boot.img")
    if not args.ramdisk and not args.compare:
        report["missing_evidence"].append("identified LOS21 ramdisk/image")
    if args.partition_size is None:
        report["missing_evidence"].append("observed boot partition size")
    if not args.bootloader_cmdline:
        report["missing_evidence"].append("observed effective cmdline")
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: boot-artifact parser (%d self-test checks); BOOT TEST READY: NO" % report["selftest_checks"])
    print("Inspected: " + ", ".join(str(path) for path in inputs if path != args.config) if inputs else "Inspected: no supplied artifacts")
    print("Missing/unverified: " + "; ".join(report["missing_evidence"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, UnicodeError, struct.error, zlib.error, lzma.LZMAError) as error:
        print("FAIL:", error, file=sys.stderr)
        raise SystemExit(1)
