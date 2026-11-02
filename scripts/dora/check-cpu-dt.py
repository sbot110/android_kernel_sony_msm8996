#!/usr/bin/env python3
"""Validate observational CPU clocks and disabled Sony voltage dependencies.

The golden data was extracted from the FINAL flattened working Sony 4.4 DT,
not from intermediate .dtsi files. This is a structure/data check, not DVFS or
four-CPU runtime validation.
"""
import argparse
import copy
import json
from pathlib import Path
import runpy
import struct

ROOT = Path(__file__).resolve().parents[2]
PARSE = runpy.run_path(str(ROOT / "scripts/dora/check-power-dt.py"))["parse_fdt"]
GOLDEN = json.loads((ROOT / "Documentation/sony/dora-cpu-dt.json").read_text())


def cells(value):
    if len(value) % 4:
        raise ValueError("unaligned cell array")
    return struct.unpack(">%dI" % (len(value) // 4), value)


def target(path):
    return path.replace("/qcom,pm8994@1/", "/pmic@1/")


def validate(nodes):
    phandles = {cells(node["props"]["phandle"])[0]: path
                for path, node in nodes.items() if "phandle" in node["props"]}
    original_ids = GOLDEN["reference_phandles"]
    for data in GOLDEN["nodes"].values():
        path = target(data["reference_path"])
        if path not in nodes:
            raise ValueError("missing CPU dependency: " + path)
        props = nodes[path]["props"]
        # Every independently probed voltage controller must remain disabled.
        if path.count("/") == 2 or path.endswith("spm-regulator@3200"):
            if props.get("status") != b"disabled\0":
                raise ValueError("unsafe CPU voltage controller activation: " + path)
        for key, expected_hex in data["properties"].items():
            actual = props.get(key)
            expected = bytes.fromhex(expected_hex)
            if actual is None:
                raise ValueError("missing CPU property: " + path + ":" + key)
            if key.endswith("-supply") or key == "qcom,apm-ctrl":
                old_id = str(cells(expected)[0])
                wanted = target(original_ids[old_id])
                if phandles.get(cells(actual)[0]) != wanted:
                    raise ValueError("incorrect CPU provider: " + path + ":" + key)
            elif key == "clocks":
                # qcom,gcc-msm8996 GCC_HMSS_RBCPR_CLK, replacing Sony hashed ID.
                values = cells(actual)
                if len(values) != 2 or values[1] != 172 or phandles.get(values[0]) != "/soc/clock-controller@300000":
                    raise ValueError("incorrect HMSS CPR clock")
            elif key == "qcom,cpu-vctl-list":
                if [phandles.get(i) for i in cells(actual)] != ["/cpus/cpu@0", "/cpus/cpu@1", "/cpus/cpu@100", "/cpus/cpu@101"]:
                    raise ValueError("incorrect SAW CPU mapping")
            elif key == "reg" and path.endswith("spm-regulator@3200"):
                if cells(actual) != (cells(expected)[0],):
                    raise ValueError("incorrect native SPMI register cell conversion")
            elif actual != expected:
                raise ValueError("Sony CPU data changed: " + path + ":" + key)
    apcc_path = "/soc/clock-controller@6400000"
    apcc = nodes[apcc_path]["props"]
    if apcc.get("clock-names") != b"xo\0":
        raise ValueError("missing APCC reference clock")
    for key, expected in GOLDEN["frequency_plans"].items():
        if cells(apcc.get(key, b"")) != tuple(expected):
            raise ValueError("final Sony bin plan mismatch: " + key)
    for suffix, index in (("0", 0), ("1", 0), ("100", 1), ("101", 1)):
        values = cells(nodes["/cpus/cpu@" + suffix]["props"].get("clocks", b""))
        if len(values) != 2 or phandles.get(values[0]) != apcc_path or values[1] != index:
            raise ValueError("incorrect CPU/APCC mapping: " + suffix)
    cbf = nodes["/soc/clock-controller@9a11000"]["props"]
    if cbf.get("compatible") != b"qcom,msm8996-cbf\0" or cells(cbf.get("reg", b"")) != (0x9a11000, 0x10000):
        raise ValueError("incorrect CBF register topology")
    if b"maxcpus=2" not in nodes["/chosen"]["props"].get("bootargs", b""):
        raise ValueError("two-CPU diagnostic limit was removed")
    # All four phases form one SPM-controlled APCC rail in the final Sony DT.
    # Native RPM must not register independent clients while that owner is gated.
    for name in ("s8", "s9", "s10", "s11"):
        path = "/rpm-glink/rpm_requests/pm8994-regulators/" + name
        if nodes[path]["props"].get("status") != b"disabled\0":
            raise ValueError("competing native RPM APCC phase owner: " + name)


def selftest(nodes):
    cases = (
        ("/soc/cpr3-ctrl@99e8000", "vdd-limit-supply", None),
        ("/soc/cpr3-ctrl@99e8000", "status", b"okay\0"),
        ("/soc/cpr3-ctrl@99e8000", "vdd-supply", struct.pack(">I", 0xffffffff)),
        ("/soc/clock-controller@6400000", "qcom,pwrcl-speedbin1-v0", b"\0" * 8),
        ("/chosen", "bootargs", b"\0"),
        ("/rpm-glink/rpm_requests/pm8994-regulators/s11", "status", b"okay\0"),
    )
    for path, key, replacement in cases:
        bad = copy.deepcopy(nodes)
        if replacement is None:
            del bad[path]["props"][key]
        else:
            bad[path]["props"][key] = replacement
        try:
            validate(bad)
        except (ValueError, KeyError):
            continue
        raise ValueError("CPU checker accepted invalid fixture: " + key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    nodes = PARSE(args.dtb)
    validate(nodes)
    if args.selftest:
        selftest(nodes)
    print("CPU DT: Sony bin/fuse/corner data, provider links and disabled-voltage guards PASS; DVFS unvalidated")


if __name__ == "__main__":
    main()
