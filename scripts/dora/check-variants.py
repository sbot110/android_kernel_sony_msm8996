#!/usr/bin/env python3
"""Check the four final Sony SoC/PMIC datasets and explicit power guards.

This verifies compiled DT data and links, not runtime support or Sony bootloader
selection. RPM logical corners must not be supplied by a microvolt regulator.
"""
import argparse
import copy
import importlib.util
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dora_power", ROOT / "scripts/dora/check-power-dt.py")
POWER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POWER)
SPEC = importlib.util.spec_from_file_location("dora_voltage_dt", ROOT / "scripts/dora/check-clock-voltage-dt.py")
VOLTAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VOLTAGE)
MANIFEST = ROOT / "Documentation/sony/dora-variant-dt.json"
PATHS = {
    "gfx-cpr": "/soc/cpr3-ctrl@838000",
    "gfx-mem-acc": "/soc/regulator@7af004",
    "gfx-buck": "/soc/qcom,spmi@400f000/pmic@3/regulator@1700",
    "flash": "/soc/qcom,spmi@400f000/pmic@3/qcom,leds@d300",
    "cx-resource": "/rpm-glink/rpm_requests/rpm-regulator-smpa1",
    "mx-resource": "/rpm-glink/rpm_requests/rpm-regulator-smpa2",
    "gfx-clock": "/soc/clock-controller@8c0000/gfx-clock-controller",
}
REFERENCE_PATHS = dict(PATHS, **{
    "gfx-mem-acc": "/soc/regulator@007af004",
    "gfx-buck": "/soc/qcom,spmi@400f000/qcom,pmi8994@3/regulator@1700",
    "flash": "/soc/qcom,spmi@400f000/qcom,pmi8994@3/qcom,leds@d300",
    "cx-resource": "/soc/qcom,rpm-smd/rpm-regulator-smpa1",
    "mx-resource": "/soc/qcom,rpm-smd/rpm-regulator-smpa2",
    "gfx-clock": "/soc/qcom,gpucc@8c0000",
})


def cells(value):
    if len(value) % 4:
        raise ValueError("property does not contain complete u32 cells")
    return struct.unpack(">%dI" % (len(value) // 4), value)


def handles(nodes):
    return {cells(node["props"]["phandle"])[0]: path
            for path, node in nodes.items() if "phandle" in node["props"]}


def selected(nodes, reference=False, mode="gated"):
    if not reference:
        for key in ("cx-resource", "mx-resource"):
            VOLTAGE.verify_corner_endpoint(nodes, PATHS[key])
        if mode not in ("gated", "active"):
            raise ValueError("explicit gated/active projection policy is required")
        if mode == "gated":
            VOLTAGE.disabled(nodes, PATHS["cx-resource"])
        elif not VOLTAGE.available(nodes, PATHS["cx-resource"]):
            raise ValueError("active policy requires an available typed CX resource")
        VOLTAGE.disabled(nodes, PATHS["mx-resource"])
    paths = REFERENCE_PATHS if reference else PATHS
    aliases = {}
    for key, prefix in paths.items():
        if key == "gfx-clock":
            continue
        if prefix not in nodes:
            raise ValueError("missing source hardware node: " + prefix)
        for path in nodes:
            if path == prefix or path.startswith(prefix + "/"):
                aliases[path] = key + path[len(prefix):]
    refs = handles(nodes)
    result = {}
    for path, key in aliases.items():
        node = nodes[path]
        props = {}
        for name, value in node["props"].items():
            if name in ("phandle", "linux,phandle", "clocks"):
                continue
            # These two properties adapt the frontend and transport only. The
            # exact typed match/real RPM endpoint were checked before projection;
            # all original corner payload remains subject to the Sony golden.
            if not reference and key in ("cx-resource", "mx-resource"):
                if name == "qcom,rpm":
                    continue
                if name == "compatible":
                    props[name] = VOLTAGE.LEGACY.hex()
                    continue
                if name == "status":
                    props[name] = b"disabled\0".hex()
                    continue
            if name.endswith("-supply") or name == "qcom,pmic-revid":
                if len(value) != 4:
                    raise ValueError("malformed supply/revid phandle: " + key)
                target = refs[cells(value)[0]]
                if target in ("/soc/qcom,spmi@400f000/pmic@2/revid@100",
                              "/soc/qcom,spmi@400f000/qcom,pmi8994@2/qcom,revid@100"):
                    props[name] = "@pmi-revid"
                else:
                    props[name] = "@" + aliases[target]
            elif reference and name == "reg" and key in ("gfx-buck", "flash"):
                props[name] = value[:4].hex()
            elif reference and name == "status" and key in paths:
                props[name] = b"disabled\0".hex()
            else:
                props[name] = value.hex()
        if reference and key in paths:
            props["status"] = b"disabled\0".hex()
        result[key] = {"properties": props,
                       "children": sorted(child["name"] for child in node["children"])}
    props = nodes[paths["gfx-clock"]]["props"]
    result["gfx-clock"] = {
        "properties": {key: value.hex() for key, value in props.items()
                       if key.startswith("qcom,gfxfreq-")}, "children": []}
    return result


def check_reference(directory):
    expected = json.loads(MANIFEST.read_text())
    for variant, data in expected["variants"].items():
        nodes = POWER.parse_fdt(directory / data["source_dtb"])
        if selected(nodes, reference=True) != data["nodes"]:
            raise ValueError("fixture is not the final Sony reference: " + variant)
        for prop, key in (("qcom,msm-id", "msm_id"),
                          ("qcom,pmic-id", "pmic_id"),
                          ("qcom,board-id", "board_id")):
            if nodes["/"]["props"][prop].hex() != data[key]:
                raise ValueError("reference ID mismatch: " + variant)
        bcl = nodes["/soc/qcom,spmi@400f000/qcom,pmi8994@2/bcl@4200"]["props"]
        ibb = [node for path, node in nodes.items() if path.endswith("/qcom,ibb@dc00")]
        if len(ibb) != 1 or bcl["qcom,inhibit-derating-ua"].hex() != data["bcl_derating"]:
            raise ValueError("reference BCL/IBB mismatch: " + variant)
        if ibb[0]["props"]["qcom,qpnp-ibb-discharge-resistor"].hex() != data["ibb_discharge"]:
            raise ValueError("reference final IBB discharge mismatch: " + variant)
    print("PASS: all four fixtures match the flattened Sony 4.4 reference data")


def validate(nodes, expected=None, mode="gated"):
    if expected is None:
        expected = json.loads(MANIFEST.read_text())
    root = nodes["/"]["props"]
    matches = [(name, data) for name, data in expected["variants"].items()
               if data["msm_id"] == root["qcom,msm-id"].hex()
               and data["pmic_id"] == root["qcom,pmic-id"].hex()]
    if len(matches) != 1:
        raise ValueError("unexpected or ambiguous SoC/PMIC tuple")
    variant, wanted = matches[0]
    if root["qcom,board-id"].hex() != wanted["board_id"]:
        raise ValueError("board ID is not Sony Tone")
    actual = selected(nodes, mode=mode)
    bad = []
    for key in sorted(set(actual) | set(wanted["nodes"])):
        a = actual.get(key, {})
        b = wanted["nodes"].get(key, {})
        if a.get("children") != b.get("children"):
            bad.append(key + ":children")
        ap, bp = a.get("properties", {}), b.get("properties", {})
        bad += [key + ":" + prop for prop in sorted(set(ap) | set(bp))
                if ap.get(prop) != bp.get(prop)]
    if bad:
        raise ValueError("final Sony hardware data differs: " + ", ".join(bad))
    refs = handles(nodes)
    for key, path in PATHS.items():
        if key == "cx-resource" and mode == "active":
            continue
        if nodes[path]["props"].get("status") != b"disabled\0":
            raise ValueError("incomplete power chain enabled: " + key)
    cpr = nodes[PATHS["gfx-cpr"]]["props"]
    clock_data = cells(cpr["clocks"])
    if len(clock_data) != 6 or list(clock_data[1::2]) != [108, 109, 63]:
        raise ValueError("GFX CPR requires native RBCPR/RBCPR_AHB/MMAGIC_AHB clocks")
    if any(refs[ref] != "/soc/clock-controller@8c0000" for ref in clock_data[::2]):
        raise ValueError("GFX CPR clock supplier is not native MSM8996 MMCC")
    clock = nodes[PATHS["gfx-clock"]]["props"]
    rev, pmic = variant.split("-")
    if clock["compatible"] != ("qcom,msm8996-" + rev + "-gfxcc\0").encode():
        raise ValueError("GPU clock revision does not match SoC")
    if "reg" in clock:
        raise ValueError("GPU child must share parent MMCC regmap without a second owner")
    for prop, target in (("vdd-gfx-supply", PATHS["gfx-cpr"] + "/thread@0/regulator"),
                         ("vdd-mx-supply", PATHS["mx-resource"] + "/regulator-s2-corner"),
                         ("qcom,rpm", "/rpm-glink/rpm_requests"),
                         ("nvmem-cells", "/soc/qfprom@74000/cpu-speedbin@133")):
        if refs[cells(clock[prop])[0]] != target:
            raise ValueError("GPU child provider mismatch: " + prop)
    if cells(clock["#clock-cells"]) != (1,) or clock["nvmem-cell-names"] != b"speed-bin\0":
        raise ValueError("GPU child clock/NVMEM binding mismatch")
    fuse = nodes["/soc/qfprom@74000/cpu-speedbin@133"]["props"]
    if cells(fuse["reg"]) != (0x133, 1) or cells(fuse["bits"]) != (5, 3):
        raise ValueError("GPU speed-bin fuse location does not match Sony clock driver")
    for usid in (2, 3):
        compat = nodes["/soc/qcom,spmi@400f000/pmic@%d" % usid]["props"]["compatible"]
        if compat != ("qcom," + pmic + "\0qcom,spmi-pmic\0").encode():
            raise ValueError("SPMI PMIC compatible does not match variant")
    bcl = nodes["/soc/qcom,spmi@400f000/pmic@2/bcl@4200"]["props"]
    if bcl["qcom,inhibit-derating-ua"].hex() != wanted["bcl_derating"]:
        raise ValueError("PMIC-specific BCL derating differs")
    ibb = nodes["/soc/qcom,spmi@400f000/pmic@3/qpnp-labibb-regulator/qcom,ibb@dc00"]["props"]
    if ibb["qcom,qpnp-ibb-discharge-resistor"].hex() != wanted["ibb_discharge"]:
        raise ValueError("final IBB discharge differs (Sony dora requires 32)")
    # This stage deliberately retains maxcpus=2 independently of model or PMIC.
    VOLTAGE.verify_cpu_limit(nodes)
    return variant


def selftest(nodes, mode="gated"):
    fixture = copy.deepcopy(nodes)
    other = "active" if mode == "gated" else "gated"
    fixture[PATHS["cx-resource"]]["props"]["status"] = (
        b"okay\0" if other == "active" else b"disabled\0")
    if validate(fixture, mode=other) != validate(nodes, mode=mode):
        raise ValueError("explicit CX policy changed the original variant identity")
    cases = [
        (PATHS["gfx-cpr"] + "/thread@0/regulator", "qcom,cpr-corners", struct.pack(">I", 99)),
        (PATHS["flash"], "qcom,ramp_up_step", b"BAD\0"),
        (PATHS["gfx-clock"], "status", b"okay\0"),
        (PATHS["cx-resource"], "status", b"okay\0" if mode == "gated" else b"disabled\0"),
        (PATHS["cx-resource"], "compatible", VOLTAGE.LEGACY),
        (PATHS["mx-resource"], "compatible", b"qcom,unverified-corner-resource\0"),
        (PATHS["cx-resource"], "qcom,rpm", nodes["/soc/clock-controller@300000"]["props"]["phandle"]),
        ("/rpm-glink/rpm_requests", "status", b"disabled\0"),
        ("/chosen", "bootargs", b"maxcpus=2 maxcpus=4\0"),
        ("/chosen", "bootargs", b"maxcpus=2 boot_cpus=2-3\0"),
        (PATHS["gfx-cpr"], "clocks", b"\0" * 24),
        ("/soc/qfprom@74000/cpu-speedbin@133", "bits", struct.pack(">II", 0, 3)),
        ("/soc/qfprom@74000/cpu-speedbin@133", "bits", struct.pack(">II", 29, 3)),
        ("/soc/qfprom@74000/cpu-speedbin@133", "reg", struct.pack(">II", 0x130, 4)),
        ("/soc/qcom,spmi@400f000/pmic@3/qpnp-labibb-regulator/qcom,ibb@dc00",
         "qcom,qpnp-ibb-discharge-resistor", struct.pack(">I", 300)),
        ("/", "qcom,pmic-id", struct.pack(">IIII", 0, 0, 0, 0)),
    ]
    for path, prop, replacement in cases:
        bad = copy.deepcopy(nodes)
        bad[path]["props"][prop] = replacement
        try:
            validate(bad, mode=mode)
        except (ValueError, KeyError, IndexError, struct.error):
            continue
        raise ValueError("checker accepted corrupt/unsafe fixture: " + prop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path, nargs="+")
    parser.add_argument("--require-four", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--require-gated", action="store_true",
                        help="require all incomplete power chains gated (the default)")
    policy.add_argument("--require-active", action="store_true",
                        help="allow only the explicitly available typed CX resource")
    parser.add_argument("--reference-dir", type=Path,
                        help="also verify fixtures against all four original Sony DTBs")
    args = parser.parse_args()
    mode = "active" if args.require_active else "gated"
    seen = []
    try:
        if args.reference_dir:
            check_reference(args.reference_dir)
        for path in args.dtb:
            nodes = POWER.parse_fdt(path)
            variant = validate(nodes, mode=mode)
            if variant in seen:
                raise ValueError("duplicate variant: " + variant)
            seen.append(variant)
            if args.selftest:
                selftest(nodes, mode=mode)
            print("PASS: %s final Sony GPU/PMIC data, native links and %s CX policy" % (variant, mode))
        if args.require_four and set(seen) != set(json.loads(MANIFEST.read_text())["variants"]):
            raise ValueError("all four real SoC/PMIC variants are required")
    except (ValueError, KeyError, IndexError, struct.error) as error:
        print("FAIL:", error)
        return 1
    print("Source data only: F8131/F8132 runtime and Sony bootloader DTB selection are unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
