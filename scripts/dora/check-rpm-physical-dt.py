#!/usr/bin/env python3
"""Check the five final Sony physical RPM contracts in compiled Dora DTBs.

No request is sent. A passing check does not measure a rail, validate RPM
firmware dependencies, or authorize activation of MMC/display hardware.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RPM = "/rpm-glink/rpm_requests"
BANK = RPM + "/pm8994-regulators"
RAILS = {"s4": ("smpa", 4, 1800000, 1800000),
         "l12": ("ldoa", 12, 1800000, 1800000),
         "l13": ("ldoa", 13, 1800000, 2950000),
         "l20": ("ldoa", 20, 2950000, 2950000),
         "l21": ("ldoa", 21, 2950000, 2950000)}
MANIFEST = ROOT / "Documentation/sony/dora-rpm-physical-dt.json"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/dora" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POWER = load("physical_dt_power", "check-power-dt.py")
CONFIG = load("physical_dt_config", "check-config.py")


def cells(raw):
    if len(raw) % 4:
        raise ValueError("incomplete physical regulator cell array")
    return struct.unpack(">%dI" % (len(raw) // 4), raw)


def verify_manifest(reference_dir=None, reference_commit=None):
    manifest = json.loads(MANIFEST.read_text())
    if manifest["source_commit"] != "0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1":
        raise ValueError("physical RPM reference commit changed")
    if len(manifest["fixtures"]) != 4:
        raise ValueError("all four final Sony DT variants are required")
    if reference_commit:
        if reference_commit != manifest["source_commit"]:
            raise ValueError("reference driver must be the pinned final Sony commit")
        source = subprocess.check_output(
            ["git", "show", reference_commit + ":" + manifest["source_driver"]], cwd=ROOT)
        if hashlib.sha256(source).hexdigest() != manifest["source_driver_sha256"]:
            raise ValueError("Sony RPM driver checksum differs")
        text = source.decode()
        if ("supply_name" in text or
                'if (of_get_property(node, "parent-supply", NULL))\n'
                '\t\tinit_data->supply_regulator = "parent";' not in text):
            raise ValueError("Sony firmware-managed input proof changed")
    for name, fixture in manifest["fixtures"].items():
        if set(fixture["rails"]) != set(RAILS):
            raise ValueError("incomplete final Sony physical rail dataset")
        nodes = None
        if reference_dir:
            path = reference_dir / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != fixture["sha256"]:
                raise ValueError("Sony reference DTB checksum differs: " + name)
            nodes = POWER.parse_fdt(path)
        for rail, (kind, number, low, high) in RAILS.items():
            row = fixture["rails"][rail]
            if ((row["resource_name"], row["resource_id"], row["set"],
                 row["min_uV"], row["max_uV"], row["init_uV"]) !=
                    (kind, number, 3, low, high, high) or
                    row["electrical_supply_properties"] or
                    row["provider_boot_on"] or row["provider_always_on"] or
                    row["proxy_enable"] != (rail == "l12") or
                    row["proxy_uA"] != (10000 if rail == "l12" else None)):
                raise ValueError("final Sony rail contract differs: " + name + ":" + rail)
            if nodes is None:
                continue
            props = nodes[row["path"]]["props"]
            for key, value in (("qcom,set", 3), ("regulator-min-microvolt", low),
                               ("regulator-max-microvolt", high),
                               ("qcom,init-voltage", high)):
                if cells(props[key]) != (value,):
                    raise ValueError("final Sony property differs: " + key)
            if ("qcom,proxy-consumer-enable" in props) != (rail == "l12"):
                raise ValueError("final Sony temporary L12 proxy differs")
            if rail == "l12":
                if cells(props["qcom,proxy-consumer-current"]) != (10000,):
                    raise ValueError("final Sony L12 bootstrap load differs")
                if props["proxy-supply"] != props["phandle"]:
                    raise ValueError("Sony proxy must refer to its own L12 provider")
            current = row["path"]
            while current:
                p = nodes[current]["props"]
                if any(k.endswith("-supply") and k != "proxy-supply" for k in p):
                    raise ValueError("Sony RPM physical ancestor has an electrical parent")
                if any(k in p for k in ("regulator-boot-on", "regulator-always-on")):
                    raise ValueError("Sony physical provider acquires an automatic enable")
                current = (current.rsplit("/", 1)[0] or "/") if current != "/" else ""


def validate(nodes, config=None):
    if config is not None:
        for name in ("REGULATOR", "REGULATOR_QCOM_SMD_RPM",
                     "REGULATOR_QCOM_RPM_PHYSICAL", "QCOM_SMD_RPM"):
            if config.get(name) != "y":
                raise ValueError("physical RPM mode requires built-in CONFIG_" + name)
    refs = {cells(n["props"]["phandle"])[0]: p for p, n in nodes.items()
            if "phandle" in n["props"]}
    current = RPM
    while current:
        if nodes[current]["props"].get("status", b"okay\0") not in (b"okay\0", b"ok\0"):
            raise ValueError("physical RPM endpoint or ancestor is unavailable")
        current = (current.rsplit("/", 1)[0] or "/") if current != "/" else ""
    if nodes[RPM]["props"].get("compatible") != b"qcom,rpm-msm8996\0":
        raise ValueError("physical RPM transport does not match MSM8996")
    bank = nodes[BANK]["props"]
    if (bank.get("compatible") != b"qcom,rpm-pm8994-regulators\0" or
            bank.get("status", b"okay\0") not in (b"okay\0", b"ok\0") or
            cells(bank["qcom,rpm"]) not in [(h,) for h, p in refs.items() if p == RPM]):
        raise ValueError("physical bank must use the live native MSM8996 RPM endpoint")
    if any(key in bank for key in ("vdd_s4-supply", "vdd_l20_l21-supply")):
        raise ValueError("unused upstream input graph remains on opted-in resources")
    expected = {BANK + "/" + rail for rail in RAILS}
    actual = {p for p, n in nodes.items() if "qcom,active-sleep-votes" in n["props"]}
    if actual != expected:
        raise ValueError("physical opt-in must select exactly the five audited resources")
    for rail, (_, _, low, high) in RAILS.items():
        path = BANK + "/" + rail
        props = nodes[path]["props"]
        if (props.get("status", b"okay\0") not in (b"okay\0", b"ok\0") or
                props.get("regulator-name") != ("pm8994_" + rail + "\0").encode() or
                props.get("qcom,active-sleep-votes") != b"" or
                props.get("qcom,rpm-managed-input") != b"" or
                props.get("regulator-allow-set-load") != b"" or
                cells(props["qcom,set"]) != (3,) or
                cells(props["regulator-min-microvolt"]) != (low,) or
                cells(props["regulator-max-microvolt"]) != (high,)):
            raise ValueError("incomplete or wrong physical BOTH-set rail contract: " + path)
        if any(k.endswith("-supply") or "proxy" in k or
               k in ("regulator-boot-on", "regulator-always-on", "regulator-system-load",
                     "qcom,init-voltage", "qcom,use-voltage-corner",
                     "qcom,use-voltage-floor-corner") for k in props):
            raise ValueError("physical provider must not invent parent/default requests: " + path)


def selftest(nodes, config=None):
    cases = [(BANK, "qcom,rpm", None), (RPM, "status", b"disabled\0"),
             (BANK, "vdd_l20_l21-supply", nodes[BANK + "/s5"]["props"]["phandle"])]
    for rail in RAILS:
        path = BANK + "/" + rail
        cases += [(path, "qcom,active-sleep-votes", None),
                  (path, "qcom,rpm-managed-input", None),
                  (path, "regulator-allow-set-load", None),
                  (path, "qcom,set", struct.pack(">I", 1)),
                  (path, "regulator-min-microvolt", struct.pack(">I", 1)),
                  (path, "regulator-max-microvolt", struct.pack(">I", 1)),
                  (path, "regulator-always-on", b""),
                  (path, "regulator-boot-on", b""),
                  (path, "regulator-system-load", struct.pack(">I", 10000)),
                  (path, "parent-supply", nodes[BANK + "/s5"]["props"]["phandle"]),
                  (path, "qcom,proxy-consumer-enable", b"")]
    for path, prop, value in cases:
        bad = copy.deepcopy(nodes)
        if value is None:
            bad[path]["props"].pop(prop, None)
        else:
            bad[path]["props"][prop] = value
        try:
            validate(bad, config)
        except (ValueError, KeyError, struct.error):
            continue
        raise ValueError("accepted invalid physical RPM fixture: " + path + ":" + prop)
    if config is not None:
        for name in ("REGULATOR_QCOM_RPM_PHYSICAL", "REGULATOR_QCOM_SMD_RPM"):
            try:
                validate(nodes, dict(config, **{name: "m"}))
            except ValueError:
                continue
            raise ValueError("accepted modular physical bank dependency: " + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path, nargs="+")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--reference-commit")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    try:
        verify_manifest(args.reference_dir, args.reference_commit)
        config = CONFIG.assignments(args.config)
        for dtb in args.dtb:
            nodes = POWER.parse_fdt(dtb)
            validate(nodes, config)
            if args.selftest:
                selftest(nodes, config)
            print("PASS: %s physical S4/L12/L13/L20/L21 BOTH contracts" % dtb.name)
    except (ValueError, KeyError, OSError, struct.error, subprocess.CalledProcessError) as error:
        print("FAIL:", error)
        return 1
    print("DT contracts only: no measured voltage, RPM runtime ACK or MMC activation proof.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
