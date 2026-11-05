#!/usr/bin/env python3
"""Check compiled Dora identity and enabled provider references.

The separate variant checker verifies actual Sony hardware differences and
mandatory activation guards. Neither checker proves device/bootloader support.
"""
import argparse
import importlib.util
import json
import struct
from pathlib import Path


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
spec = importlib.util.spec_from_file_location("dora_power_dt", SCRIPT.with_name("check-power-dt.py"))
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)


def cells(value):
    if len(value) % 4:
        raise ValueError("property length is not a multiple of four")
    return struct.unpack(">%dI" % (len(value) // 4), value)


def strings(value):
    if not value or value[-1]:
        raise ValueError("unterminated string property")
    return value[:-1].decode().split("\0")


def validate(nodes):
    handles = {}
    for node in nodes.values():
        if "phandle" in node["props"]:
            handle, = cells(node["props"]["phandle"])
            if not handle or handle in handles:
                raise ValueError("invalid or duplicate phandle")
            handles[handle] = node

    def available(node):
        path = node["path"]
        while True:
            status = nodes[path]["props"].get("status", b"okay\0")
            if status not in (b"okay\0", b"ok\0"):
                return False
            if path == "/":
                return True
            path = path.rsplit("/", 1)[0] or "/"

    def provider(handle):
        if handle not in handles:
            raise ValueError("unresolved phandle %#x" % handle)
        result = handles[handle]
        if not available(result):
            raise ValueError("disabled provider: " + result["path"])
        return result

    root = nodes["/"]["props"]
    manifest = json.loads((ROOT / "Documentation/sony/dora-original-dtb-ids.json").read_text())
    keys = ("qcom,msm-id", "qcom,board-id", "qcom,pmic-id")
    identity = tuple(cells(root[key]) for key in keys)
    known = {tuple(tuple(int(item, 0) for item in row[key].split())
                   for key in keys) for row in manifest}
    if identity not in known:
        raise ValueError("unknown Sony stepping/board/PMIC tuple")
    if "somc,dora-row" not in strings(root["compatible"]):
        raise ValueError("missing Sony Dora compatible")
    bootargs = strings(nodes["/chosen"]["props"]["bootargs"])[0].split()
    if [arg for arg in bootargs if arg.startswith("maxcpus=")] != ["maxcpus=2"]:
        raise ValueError("CPU safety limit removed before bring-up validation")
    if any(arg.startswith(("lcdid_adc=", "panel_name=")) for arg in bootargs):
        raise ValueError("panel selection must not be forced")
    cpus = [node for node in nodes.values() if node["props"].get("device_type") == b"cpu\0"]
    if sorted(cells(node["props"]["reg"]) for node in cpus) != [(0, 0), (0, 1), (0, 256), (0, 257)]:
        raise ValueError("expected all four Kryo CPU hardware nodes")

    argument_lists = {
        "clocks": ("#clock-cells", "clock-names"),
        "resets": ("#reset-cells", "reset-names"),
        "phys": ("#phy-cells", "phy-names"),
        "power-domains": ("#power-domain-cells", "power-domain-names"),
        "iommus": ("#iommu-cells", None),
        "mboxes": ("#mbox-cells", "mbox-names"),
        "hwlocks": ("#hwlock-cells", "hwlock-names"),
        "interrupts-extended": ("#interrupt-cells", "interrupt-names"),
    }
    checked = 0
    for node in nodes.values():
        if not available(node):
            continue
        props = node["props"]
        for name, value in props.items():
            try:
                if name in argument_lists or name == "gpio" or name == "gpios" or name.endswith(("-gpios", "-gpio")):
                    cell_name, names = argument_lists.get(name, ("#gpio-cells", None))
                    words, offset, count = cells(value), 0, 0
                    while offset < len(words):
                        target = provider(words[offset])
                        count_cells, = cells(target["props"][cell_name])
                        offset += 1 + count_cells
                        if offset > len(words):
                            raise ValueError("truncated provider arguments")
                        count += 1
                        checked += 1
                    if names in props and len(strings(props[names])) != count:
                        raise ValueError("provider/name count mismatch")
                elif name.endswith("-supply") or name in ("interrupt-parent", "memory-region") or name.startswith("pinctrl-") and name[8:].isdigit():
                    words = cells(value)
                    if name.endswith("-supply") and len(words) != 1:
                        raise ValueError("supply must reference one regulator")
                    for handle in words:
                        provider(handle)
                        checked += 1
                elif name == "interrupts":
                    parent = node
                    while True:
                        if "interrupt-parent" in parent["props"]:
                            handle, = cells(parent["props"]["interrupt-parent"])
                            parent = provider(handle)
                        else:
                            path = parent["path"].rsplit("/", 1)[0] or "/"
                            if path == parent["path"]:
                                raise ValueError("missing interrupt parent")
                            parent = nodes[path]
                        if "#interrupt-cells" in parent["props"]:
                            break
                    count_cells, = cells(parent["props"]["#interrupt-cells"])
                    words = cells(value)
                    if not count_cells or len(words) % count_cells:
                        raise ValueError("invalid interrupt specifier size")
                    count = len(words) // count_cells
                    if "interrupt-names" in props and len(strings(props["interrupt-names"])) != count:
                        raise ValueError("interrupt/name count mismatch")
                    checked += count
            except (KeyError, ValueError) as exc:
                raise ValueError("%s: %s: %s" % (node["path"], name, exc)) from exc

    if any(node["props"].get("compatible") == b"linux,extcon-usb-gpio\0" and available(node)
           for node in nodes.values()):
        raise ValueError("Sony ADC/pullup USB ID must not be replaced by GPIO extcon")
    usb = power.find(nodes, "/usb@6a00000/dwc3@6a00000")["props"]
    if usb.get("dr_mode") != b"peripheral\0" or usb.get("maximum-speed") != b"high-speed\0":
        raise ValueError("unvalidated OTG/SuperSpeed mode enabled")
    if "extcon" in usb:
        raise ValueError("unported USB ID/VBUS extcon provider")
    for node in nodes.values():
        if b"qcom,qpnp-smbcharger\0" in node["props"].get("compatible", b""):
            raise ValueError("charger needs the real DPDM/USB provider before DT activation")
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    args = parser.parse_args()
    try:
        count = validate(power.parse_fdt(args.dtb))
    except (KeyError, ValueError, OSError, struct.error) as exc:
        parser.exit(1, "Dora DT check failed: %s\n" % exc)
    print("Dora DT: original SoC/PMIC tuple, four CPU nodes/maxcpus=2, %d provider references checked" % count)
    print("LIMIT: provider references only; run variant/CPU/display/bus checks; hardware activation remains gated")


if __name__ == "__main__":
    main()
