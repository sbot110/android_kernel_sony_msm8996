#!/usr/bin/env python3
"""Check compiled Sony PMIC boot pins and the intentionally inactive charger.

The manifest comes from all four final Sony4.4 DTBs. Passing verifies the
selected native pinconf data, not electrical behavior or charger safety.
"""
import argparse
import copy
import json
import runpy
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POWER = runpy.run_path(str(ROOT / "scripts/dora/check-power-dt.py"))
CONTROLLERS = {
    "pm8994-gpio": "/pmic@0/gpios@c000",
    "pmi8994-gpio": "/pmic@2/gpios@c000",
    "pm8994-mpp": "/pmic@0/mpps@a000",
}


def cells(data):
    if len(data) % 4:
        raise ValueError("misaligned cell property")
    return struct.unpack(">%dI" % (len(data) // 4), data)


def strings(data):
    if not data or data[-1] != 0:
        raise ValueError("unterminated string property")
    return data[:-1].decode().split("\0")


def native_properties(group, pin, row):
    """Translate only explicit legacy fields; unspecified fields stay untouched."""
    props = row["properties"]
    if row["status"] == "disabled" or not props:
        return None
    prefix = "mpp" if group.endswith("-mpp") else "gpio"
    result = {"pins": (prefix + pin).encode() + b"\0"}
    if props.get("qcom,master-en") == 0:
        result["bias-high-impedance"] = b""
        return result
    mode = props.get("qcom,mode")
    if prefix == "gpio":
        functions = ("normal", "paired", "func1", "func2", "dtest1",
                     "dtest2", "dtest3", "dtest4")
        if "qcom,src-sel" in props:
            result["function"] = functions[props["qcom,src-sel"]].encode() + b"\0"
        if "qcom,output-type" in props:
            result[("drive-push-pull", "drive-open-drain", "drive-open-source")[props["qcom,output-type"]]] = b""
        if mode == 0:
            result.update({"input-enable": b"", "output-disable": b""})
        elif mode == 1:
            result.update({"input-disable": b"", "output-enable": b""})
        elif mode == 2:
            result.update({"input-enable": b"", "output-enable": b""})
        elif mode is not None:
            raise ValueError("unsupported legacy GPIO mode")
        if "qcom,invert" in props:
            result["output-high" if props["qcom,invert"] else "output-low"] = b""
        if "qcom,out-strength" in props:
            result["qcom,drive-strength"] = struct.pack(">I", props["qcom,out-strength"])
        if "qcom,pull" in props:
            pull = props["qcom,pull"]
            if pull <= 3:
                result["qcom,pull-up-strength"] = struct.pack(">I", pull)
            elif pull in (4, 5):
                result["bias-pull-down" if pull == 4 else "bias-disable"] = b""
            else:
                raise ValueError("unsupported legacy GPIO pull")
    else:
        if mode != 4:
            raise ValueError("unsupported active legacy MPP mode")
        result.update({"function": b"analog\0", "input-enable": b"",
                       "output-disable": b""})
        if "qcom,ain-route" in props:
            result["qcom,amux-route"] = struct.pack(">I", props["qcom,ain-route"])
    if "qcom,vin-sel" in props:
        result["power-source"] = struct.pack(">I", props["qcom,vin-sel"])
    return result


def validate(nodes):
    manifest = json.loads((ROOT / "Documentation/sony/dora-pin-safety.json").read_text())
    handles = {cells(n["props"]["phandle"])[0]: n for n in nodes.values()
               if "phandle" in n["props"]}

    def active(node):
        path = node["path"]
        while True:
            if nodes[path]["props"].get("status", b"okay\0") not in (b"okay\0", b"ok\0"):
                return False
            if path == "/":
                return True
            path = path.rsplit("/", 1)[0] or "/"

    selected, checked = {}, 0
    for group, suffix in CONTROLLERS.items():
        controller = POWER["find"](nodes, suffix)
        if not active(controller):
            raise ValueError("disabled PMIC pin provider " + suffix)
        props = controller["props"]
        names = strings(props["pinctrl-names"])
        index = names.index("default")
        refs = cells(props["pinctrl-%d" % index])
        actual = {}
        for handle in refs:
            state = handles[handle]
            for path, node in nodes.items():
                if path != state["path"] and not path.startswith(state["path"] + "/"):
                    continue
                pinprops = node["props"]
                if not active(node) or "pins" not in pinprops:
                    continue
                for name in strings(pinprops["pins"]):
                    if name in actual:
                        raise ValueError("duplicate boot programming for %s %s" % (group, name))
                    actual[name] = node
        expected_names = set()
        for pin, row in manifest["groups"][group].items():
            expected = native_properties(group, pin, row)
            if expected is None:
                continue
            name = strings(expected["pins"])[0]
            expected_names.add(name)
            if name not in actual:
                raise ValueError("missing final Sony boot pin %s %s" % (group, name))
            node = actual[name]
            got = {k: v for k, v in node["props"].items()
                   if k not in ("phandle", "linux,phandle", "status")}
            if got != expected:
                differences = sorted(k for k in set(got) | set(expected)
                                     if got.get(k) != expected.get(k))
                raise ValueError("%s %s differs from final Sony: %s" %
                                 (group, name, ", ".join(differences)))
            selected[(group, name)] = node["path"]
            checked += 1
        if set(actual) != expected_names:
            raise ValueError("unproven boot pin programming in %s: %s" %
                             (group, sorted(set(actual) - expected_names)))

    fg = POWER["find"](nodes, "/pmic@2/qcom,fg")
    for name, expected in manifest["fuel_gauge_safety"].items():
        if cells(fg["props"][name]) != tuple(expected):
            raise ValueError("FG safety property changed: " + name)
    phy_owners = []
    for node in nodes.values():
        if not active(node):
            continue
        props = node["props"]
        comp = props.get("compatible", b"").split(b"\0")
        if b"qcom,qpnp-smbcharger" in comp:
            raise ValueError("charger must remain inactive until real DPDM/ID/VBUS integration")
        if any(b"qusb" in item for item in comp):
            if "reg" in props and cells(props["reg"])[0] == 0x7411000:
                phy_owners.append(node)
        if b"regulator-fixed" in comp and any(b"dpdm" in value.lower()
                                              for key, value in props.items()
                                              if key in ("regulator-name", "label")):
            raise ValueError("fixed regulator cannot implement DPDM")
    if len(phy_owners) != 1:
        raise ValueError("QUSB2 hardware must have exactly one active owner")
    if b"sony,dora-qusb2-phy" not in phy_owners[0]["props"]["compatible"].split(b"\0"):
        raise ValueError("native Sony PHY owner changed without complete DPDM integration")
    return checked, selected


def self_test(nodes, selected):
    def reject(label, mutate):
        changed = copy.deepcopy(nodes)
        mutate(changed)
        try:
            validate(changed)
        except (ValueError, KeyError):
            return
        raise AssertionError("accepted unsafe mutation: " + label)
    ear = selected[("pm8994-gpio", "gpio13")]
    def high_ear(changed):
        props = changed[ear]["props"]
        props.pop("output-low")
        props["output-high"] = b""
    reject("EAR_EN high", high_ear)
    pin7 = selected[("pmi8994-gpio", "gpio7")]
    reject("wrong PMI GPIO7 rail", lambda n: n[pin7]["props"].__setitem__("power-source", struct.pack(">I", 2)))
    mpp2 = selected[("pm8994-mpp", "mpp2")]
    reject("stale MPP output enable", lambda n: n[mpp2]["props"].pop("output-disable"))
    fg = POWER["find"](nodes, "/pmic@2/qcom,fg")["path"]
    reject("changed hot cutoff", lambda n: n[fg]["props"].__setitem__("qcom,hot-bat-decidegc", struct.pack(">I", 1000)))
    def charger(changed):
        changed["/unsafe-charger"] = {"path": "/unsafe-charger", "props": {"compatible": b"qcom,qpnp-smbcharger\0"}, "children": []}
    reject("incomplete charger activation", charger)
    return 5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        nodes = POWER["parse_fdt"](args.dtb)
        count, selected = validate(nodes)
        if args.self_test:
            print("PASS: %d unsafe pin/charger mutations rejected" % self_test(nodes, selected))
    except (OSError, KeyError, ValueError, IndexError, struct.error) as error:
        parser.exit(1, "FAIL PMIC boot safety data: %s\n" % error)
    print("PASS: %d selected PMIC boot pins match final Sony; FG cutoffs retained; one QUSB2 owner" % count)
    print("LIMIT: no electrical/charging/boot safety claim; DPDM/ID/VBUS remain incomplete")


if __name__ == "__main__":
    main()
