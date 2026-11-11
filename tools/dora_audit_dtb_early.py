#!/usr/bin/env python3
"""Read-only early-boot FDT audit: working LOS21 4.4 vs 5.15 no-stages.

Uses known already-verified artifacts and optional unprivileged adb.exe.
Does not write files, modify source, or flash a device.
Run: python3 tools/dora_audit_dtb_early.py
"""
import hashlib
import re
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROM = Path.home() / "android/lineage/out/target/product/dora/lineage-21.0-20261030-UNOFFICIAL-dora.zip"
NEW = Path("/mnt/c/platform-tools/dora515-preflight-no-stages/Image.gz-dtb")
ADB = Path("/mnt/c/platform-tools/adb.exe")
BOOT_HASH = "f923f9b99c468484b713529577b84e3a137d1b9cbc09393e5593258009983820"
NEW_HASH = "c552c694c46ae538e291080e50af0e2a6c0f2f0c75de6812806e008eecd4709d"
FDT_MAGIC = 0xd00dfeed
KEYS = ("qcom,msm-id", "qcom,board-id", "qcom,pmic-id")
INFO = ("model", "compatible", "qcom,msm-id", "qcom,board-id",
        "qcom,pmic-id", "#address-cells", "#size-cells", "bootargs",
        "stdout-path", "linux,initrd-start", "linux,initrd-end")
KEYPATHS = ("/chosen", "/memory", "/reserved-memory", "/cpus", "/psci",
            "/timer", "/interrupt-controller")
SELECT = re.compile(r"(memory|reserved|chosen|psci|timer|interrupt|gic|uart|serial|watchdog|ramoops|splash|debug|rpm|clock|regulator)", re.I)


def aligned4(v):
    return (v + 3) & ~3


def word(b, at):
    return struct.unpack_from(">I", b, at)[0]


def fmt_value(name, raw):
    if name in ("model", "compatible", "bootargs", "stdout-path", "device_type"):
        return raw.rstrip(b"\x00").decode("utf-8", "replace").replace("\x00", " | ")[:200]
    if len(raw) in (4, 8) and name.startswith("linux,initrd"):
        return f"0x{int.from_bytes(raw, 'big'):x}"
    if not raw:
        return "true"
    if len(raw) > 40 or len(raw) % 4:
        return f"{len(raw)} bytes"
    v = struct.unpack(">" + "I" * (len(raw)//4), raw)
    return " ".join(hex(x) for x in v)


def parse_fdt(blob):
    if len(blob) < 40 or word(blob, 0) != FDT_MAGIC:
        raise ValueError("Invalid FDT magic")
    total, ofs_st, ofs_strings, ofs_mem, ver, last, cpuid, size_str, size_st = struct.unpack_from(">9I", blob, 4)
    if total < 40 or total > len(blob) or ver < 16:
        raise ValueError("Invalid FDT header")
    if ofs_st + size_st > total or ofs_strings + size_str > total or ofs_mem >= total:
        raise ValueError("FDT header section outside blob")
    reserve = []
    at = ofs_mem
    while at+16 <= ofs_st:
        a, n = struct.unpack_from(">QQ", blob, at)
        at += 16
        if a == n == 0:
            break
        reserve.append((a,n))
        if len(reserve) > 1000:
            raise ValueError("Too many FDT reserved regions")
    strings = blob[ofs_strings:ofs_strings+size_str]
    st = blob[ofs_st:ofs_st+size_st]
    nodes = {}
    stack = []
    pos = 0
    properties = 0
    ended = False
    while pos + 4 <= len(st):
        op = word(st, pos)
        pos += 4
        if op == 1:
            end = st.find(b"\x00", pos)
            if end < 0:
                raise ValueError("Unterminated node name")
            name = st[pos:end].decode("utf-8", "replace")
            pos = aligned4(end+1)
            path = (stack[-1].rstrip("/") + "/" + name) if stack else "/"
            if path in nodes:
                raise ValueError(f"Duplicate path: {path}")
            stack.append(path)
            nodes[path] = {}
        elif op == 2:
            if not stack:
                raise ValueError("Unbalanced end node")
            stack.pop()
        elif op == 3:
            if not stack or pos+8 > len(st):
                raise ValueError("Truncated FDT property")
            length, nameoff = struct.unpack_from(">II", st, pos)
            pos += 8
            if pos + length > len(st) or nameoff >= len(strings):
                raise ValueError("Property outside FDT bounds")
            end = strings.find(b"\x00", nameoff)
            if end < 0:
                raise ValueError("Unterminated property name")
            name = strings[nameoff:end].decode("utf-8", "replace")
            nodes[stack[-1]][name] = st[pos:pos+length]
            pos = aligned4(pos+length)
            properties += 1
        elif op == 4:
            continue
        elif op == 9:
            ended = True
            break
        else:
            raise ValueError(f"Unexpected FDT token {op} at {pos-4:#x}")
    if not ended or stack:
        raise ValueError("Invalid FDT node nesting")
    return total, nodes, reserve, properties


def get_dtbs(packed):
    dec = zlib.decompressobj(31)
    uncompressed = dec.decompress(packed) + dec.flush()
    if not dec.eof or uncompressed[56:60] != b"ARM\x64":
        raise ValueError("Not a valid gzip-compressed ARM64 Image")
    rest = dec.unused_data
    result = []
    pos = 0
    while pos < len(rest):
        if len(rest) - pos < 40 or word(rest,pos) != FDT_MAGIC:
            raise ValueError(f"Non-DTB data in appended footer at {pos:#x}")
        length, nodes, reserved, nprops = parse_fdt(rest[pos:])
        result.append((nodes, reserved, nprops, length))
        pos += length
    return result


def key(root):
    return tuple(root.get(k, b"") for k in KEYS)


def keystr(node):
    return " / ".join(f"{k}: {fmt_value(k, node.get(k,b''))}" for k in KEYS)


def summarize(label, variant):
    nodes, reserved, nprops, length = variant
    root = nodes.get("/", {})
    print(f"\n=== {label} ===")
    print("DTB size:", length, "nodes:", len(nodes), "properties:", nprops, "memreserve entries:", len(reserved))
    print("IDs:", keystr(root))
    for name in ("model", "compatible"):
        if name in root:
            print(f"root {name}: {fmt_value(name,root[name])}")
    for path in KEYPATHS:
        if path in nodes:
            print(f"{path} present: properties={len(nodes[path])}")
        else:
            matches = [p for p in nodes if p.startswith(path+"@") or
                       (path in ("/memory", "/interrupt-controller") and p.rsplit("/",1)[-1].startswith(path[1:]+"@"))]
            print(f"{path}: {' '.join(matches[:4]) if matches else '<ABSENT>'}")
    print("Memory/reserved root children:")
    for p in list(nodes):
        if p.count("/") == 1 and (p.startswith("/memory") or p == "/reserved-memory"):
            print(" ", p, "reg:", fmt_value("reg", nodes[p]["reg"]) if "reg" in nodes[p] else "<no reg>")
    print("FDT memreserve ranges (up to 12):")
    for a,n in reserved[:12]:
        print(f"  0x{a:x}-0x{a+n:x} ({n:,} bytes)")
    important = [p for p in nodes if SELECT.search(p.split("/")[-1])]
    print("Interesting nodes:", len(important), "sample:", ", ".join(important[:20]))


def active_properties():
    if not ADB.is_file():
        print("\nADB not installed at expected Windows path; skipping active DT inspection")
        return
    print("\n=== ACTIVE PHONE DT ROOT (UNPRIVILEGED ADB) ===")
    try:
        dev = subprocess.run([str(ADB), "get-state"], capture_output=True, timeout=8)
        if dev.returncode != 0 or b"device" not in dev.stdout:
            print("Phone not in Android adb-device state; skipping")
            return
        for name in ("model", "compatible", *KEYS):
            cp = subprocess.run([str(ADB), "exec-out", "cat", f"/proc/device-tree/{name}"],
                                capture_output=True, timeout=10)
            if cp.returncode:
                print(f"{name}: unavailable")
            else:
                print(f"{name}: {fmt_value(name,cp.stdout)}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        print("ADB reading unavailable:", exc)


def main():
    with zipfile.ZipFile(ROM) as z:
        original = z.read("boot.img")
    if hashlib.sha256(original).hexdigest() != BOOT_HASH:
        raise ValueError("Working LOS21 boot.img unexpected")
    page = struct.unpack_from("<I", original, 36)[0]
    old_size = struct.unpack_from("<I", original, 8)[0]
    if page != 4096 or old_size != 12047744:
        raise ValueError("Original Sony legacy header differs from validated")
    old = original[page:page+old_size]
    new = NEW.read_bytes()
    if hashlib.sha256(new).hexdigest() != NEW_HASH:
        raise ValueError("No-stages Image.gz-dtb SHA256 unexpected")

    old_dtbs = get_dtbs(old)
    new_dtbs = get_dtbs(new)
    print("Working 4.4 DTBs:", len(old_dtbs), "experimental 5.15 DTBs:", len(new_dtbs))
    if len(old_dtbs) != 4 or len(new_dtbs) != 4:
        raise ValueError("Unexpected count of appended DTBs")

    old_by_key = {key(x[0].get("/", {})): x for x in old_dtbs}
    new_by_key = {key(x[0].get("/", {})): x for x in new_dtbs}
    if len(old_by_key) != 4 or len(new_by_key) != 4:
        raise ValueError("Duplicate/ambiguous hardware IDs in DTBs")
    if set(old_by_key) != set(new_by_key):
        print("WARNING: mismatch between DTB hardware ID sets")
    else:
        print("PASS: exact same set of 4 Qualcomm hardware ID combinations")

    for i, k in enumerate(old_by_key, 1):
        print(f"\n===== MATCHED VARIANT {i} =====")
        summarize("Sony 4.4", old_by_key[k])
        if k in new_by_key:
            summarize("Linux 5.15", new_by_key[k])
            a,b = old_by_key[k], new_by_key[k]
            print("Node count difference:", len(b[0])-len(a[0]))
            print("Missing exact node paths (first 20):", ", ".join(sorted(set(a[0])-set(b[0]))[:20]))
            print("NOTE: differences are diagnostic, not proof of failure; DTS schemas differ")
    active_properties()
    print("\nPASS: FDT early-boot audit completed; no files modified or flashed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, struct.error, zipfile.BadZipFile, zlib.error) as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(2)
