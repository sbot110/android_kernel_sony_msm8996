#!/usr/bin/env python3
"""Check compiled Dora PMIC data against the flattened Sony 4.4 reference.

This validates DT contents, not PMIC probe, measurements or safe charging.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def parse_fdt(path):
    data = path.read_bytes()
    if len(data) < 40:
        raise ValueError("truncated FDT header")
    header = struct.unpack_from(">10I", data)
    magic, total, struct_off, strings_off = header[:4]
    if magic != 0xD00DFEED or total != len(data):
        raise ValueError("invalid FDT magic or total size")
    if struct_off + header[9] > total or strings_off + header[8] > total:
        raise ValueError("FDT blocks exceed total size")
    block = data[struct_off:struct_off + header[9]]
    strings = data[strings_off:strings_off + header[8]]
    nodes, stack, off = {}, [], 0
    while off + 4 <= len(block):
        token = struct.unpack_from(">I", block, off)[0]
        off += 4
        if token == 1:
            end = block.index(0, off)
            name = block[off:end].decode()
            off = (end + 4) & ~3
            parent = stack[-1] if stack else None
            path = parent["path"].rstrip("/") + "/" + name if parent else "/"
            if path in nodes:
                raise ValueError("duplicate node: " + path)
            node = {"path": path, "name": name, "props": {}, "children": []}
            nodes[path] = node
            if parent:
                parent["children"].append(node)
            stack.append(node)
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name_off = struct.unpack_from(">II", block, off)
            off += 8
            if off + length > len(block):
                raise ValueError("truncated property")
            end = strings.index(0, name_off)
            name = strings[name_off:end].decode()
            if name in stack[-1]["props"]:
                raise ValueError("duplicate property: " + name)
            stack[-1]["props"][name] = block[off:off + length]
            off = (off + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack or "/" not in nodes:
                raise ValueError("unbalanced FDT nodes")
            return nodes
        else:
            raise ValueError("invalid FDT token: %d" % token)
    raise ValueError("missing FDT end token")


def find(nodes, suffix):
    matches = [node for path, node in nodes.items() if path.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError("expected one %s, got %d" % (suffix, len(matches)))
    return matches[0]


def power_nodes(nodes, reference=False):
    result = {}
    for group in ("pm8994", "pmi8994"):
        usid = 0 if group == "pm8994" else 2
        parent = "/qcom,%s@%d" % (group, usid) if reference else "/pmic@%d" % usid
        parent = find(nodes, parent)["path"]
        revid = "qcom,revid@100" if reference else "revid@100"
        names = [(revid, "revid@100"), ("vadc@3100", "vadc@3100")]
        if group == "pmi8994":
            names += [("bcl@4200", "bcl@4200"), ("qcom,fg", "qcom,fg")]
        for name, key in names:
            node = find(nodes, parent + "/" + name)
            add_nodes(result, group + "/" + key, node)
    add_nodes(result, "battery", nodes["/qcom,battery-data"])
    return result


def add_nodes(result, key, node):
    result[key] = node
    for child in node["children"]:
        add_nodes(result, key + "/" + child["name"], child)


def digests(nodes, reference=False):
    selected = power_nodes(nodes, reference)
    paths = {node["path"]: key for key, node in selected.items()}
    handles = {struct.unpack(">I", node["props"]["phandle"])[0]: path
               for path, node in nodes.items() if "phandle" in node["props"]}
    result = {}
    for key, node in selected.items():
        props = {}
        for name, value in node["props"].items():
            if name in ("phandle", "linux,phandle"):
                continue
            if name in ("qcom,pmic-revid", "qcom,battery-data"):
                value = paths[handles[struct.unpack(">I", value)[0]]].encode()
            elif reference and name == "reg":
                if key.endswith("/revid@100"):
                    value = struct.pack(">I", 0x100)
                elif key.endswith("/vadc@3100"):
                    value = struct.pack(">I", 0x3100)
                elif key == "pmi8994/bcl@4200":
                    value = struct.pack(">II", 0x4200, 0x88E)
            props[name] = value.hex()
        content = {"properties": props,
                   "children": sorted(child["name"] for child in node["children"])}
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        result[key] = hashlib.sha256(encoded).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    expected = json.loads((root / "Documentation/sony/dora-power-dt.json").read_text())
    try:
        nodes = parse_fdt(args.dtb)
        actual = digests(nodes)
        wanted = expected["node_sha256"]
        bad = sorted(key for key in set(actual) | set(wanted)
                     if actual.get(key) != wanted.get(key))
        if bad:
            raise ValueError("power data differs: " + ", ".join(bad))
        if any(b"qcom,qpnp-smbcharger" in
               node["props"].get("compatible", b"").split(b"\0")
               for node in nodes.values()):
            raise ValueError("charger enabled without completed DPDM provider")
        profile = nodes["/qcom,battery-data/qcom,tone-dora-send"]["props"]["qcom,fg-profile-data"]
        if len(profile) != 128 or hashlib.sha256(profile).hexdigest() != expected["profile_sha256"]:
            raise ValueError("Dora battery profile mismatch")
    except (ValueError, KeyError, IndexError, struct.error) as error:
        print("FAIL:", error)
        return 1
    print("PASS: %d PMIC/profile nodes match Sony reference; Dora profile is exact" % len(actual))
    print("DT data only: F8131/F8132 probe, accuracy and charging remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
