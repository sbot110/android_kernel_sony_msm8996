#!/usr/bin/env python3
"""Check compiled MSM8996 clock/corner ownership with an explicit gate policy.

This checks provider relationships and resolved Kconfig, not RPM acknowledgements
on a phone. Neither mode establishes DVFS, display or diagnostic boot readiness.
"""
import argparse
import copy
import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/dora" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POWER = load("voltage_power", "check-power-dt.py")
CONFIG = load("voltage_config", "check-config.py")
PHYSICAL = load("voltage_physical", "check-rpm-physical-dt.py")
RPM = "/rpm-glink/rpm_requests"
CX = RPM + "/rpm-regulator-smpa1"
MX = RPM + "/rpm-regulator-smpa2"
NATIVE = RPM + "/pm8994-regulators"
GCC = "/soc/clock-controller@300000"
MMCC = "/soc/clock-controller@8c0000"
TYPED = b"qcom,msm8996-rpm-corner-resource\0"
LEGACY = b"qcom,rpm-smd-regulator-resource\0"
COUPLING = "sony,msm8996-voltage-coupling"


def cells(value):
    if len(value) % 4:
        raise ValueError("incomplete u32 property")
    return struct.unpack(">%dI" % (len(value) // 4), value)


def handles(nodes):
    result = {}
    for path, node in nodes.items():
        if "phandle" not in node["props"]:
            continue
        values = cells(node["props"]["phandle"])
        if len(values) != 1 or values[0] in (0, 0xffffffff) or values[0] in result:
            raise ValueError("invalid/duplicate phandle: " + path)
        result[values[0]] = path
    return result


def available(nodes, path):
    while path:
        if nodes[path]["props"].get("status", b"okay\0") not in (b"okay\0", b"ok\0"):
            return False
        path = path.rsplit("/", 1)[0] if path != "/" else ""
        if not path and "/" in nodes:
            return nodes["/"]["props"].get("status", b"okay\0") in (b"okay\0", b"ok\0")
    return True


def target(nodes, path, prop, refs):
    values = cells(nodes[path]["props"][prop])
    if len(values) != 1 or values[0] not in refs:
        raise ValueError("missing/invalid provider: %s:%s" % (path, prop))
    return refs[values[0]]


def disabled(nodes, path):
    if nodes[path]["props"].get("status") != b"disabled\0":
        raise ValueError("power owner must remain explicitly disabled: " + path)


def verify_cpu_limit(nodes):
    arguments = nodes["/chosen"]["props"]["bootargs"].rstrip(b"\0").split()
    limit = [arg for arg in arguments if arg.split(b"=", 1)[0] == b"maxcpus"]
    if limit != [b"maxcpus=2"]:
        raise ValueError("exactly one maxcpus=2 argument is required")
    if any(arg.split(b"=", 1)[0] in (b"nr_cpus", b"boot_cpus", b"nosmp")
           for arg in arguments):
        raise ValueError("additional CPU bring-up overrides require separate validation")


def verify_corner_endpoint(nodes, path):
    """Validate the adaptation before variant-golden projection may omit it."""
    refs = handles(nodes)
    if nodes[path]["props"].get("compatible") != TYPED or path not in (CX, MX):
        raise ValueError("corner resource is not the typed MSM8996 frontend: " + path)
    if target(nodes, path, "qcom,rpm", refs) != RPM or path.rsplit("/", 1)[0] != RPM:
        raise ValueError("corner resource must use its real native RPM parent")
    if (nodes[RPM]["props"].get("compatible") != b"qcom,rpm-msm8996\0" or
            nodes[RPM]["props"].get("qcom,glink-channels") != b"rpm_requests\0" or
            nodes["/rpm-glink"]["props"].get("compatible") != b"qcom,glink-rpm\0" or
            not available(nodes, RPM)):
        raise ValueError("native MSM8996 RPM/GLINK endpoint is missing or disabled")
    glink = nodes["/rpm-glink"]["props"]
    ram = target(nodes, "/rpm-glink", "qcom,rpm-msg-ram", refs)
    if (ram != "/soc/memory@68000" or not available(nodes, ram) or
            nodes[ram]["props"].get("compatible") != b"qcom,rpm-msg-ram\0" or
            cells(nodes[ram]["props"]["reg"]) != (0x68000, 0x6000)):
        raise ValueError("RPM message RAM is not the real MSM8996 provider")
    mailbox = cells(glink["mboxes"])
    if (len(mailbox) != 2 or mailbox[1] != 0 or
            refs.get(mailbox[0]) != "/soc/mailbox@9820000"):
        raise ValueError("RPM GLINK mailbox is not native APCS channel 0")
    apcs = nodes[refs[mailbox[0]]]["props"]
    if (not available(nodes, refs[mailbox[0]]) or
            apcs.get("compatible") != b"qcom,msm8996-apcs-hmss-global\0" or
            cells(apcs["reg"]) != (0x9820000, 0x1000) or
            cells(apcs["#mbox-cells"]) != (1,)):
        raise ValueError("APCS mailbox provider does not match MSM8996")


def validate(nodes, config, mode):
    PHYSICAL.validate(nodes, config)
    if mode not in ("gated", "active"):
        raise ValueError("explicit gated/active policy is required")
    required = """QCOM_SMD_RPM RPMSG_QCOM_GLINK_RPM QCOM_APCS_IPC
        REGULATOR REGULATOR_QCOM_RPM_CORNER REGULATOR_QCOM_SMD_RPM
        COMMON_CLK COMMON_CLK_QCOM MSM_GCC_8996 MSM_MMCC_8996""".split()
    for key in required:
        if config.get(key) != "y":
            raise ValueError("compiled config requires CONFIG_%s=y" % key)
    for key in ("CPU_FREQ", "CPU_IDLE"):
        if config.get(key) != "n":
            raise ValueError("unvalidated CPU power management enabled: " + key)
    verify_cpu_limit(nodes)
    if config.get("CMDLINE") != '\"\"':
        raise ValueError("built-in command line requires separate CPU safety validation")
    refs = handles(nodes)
    for resource, number in ((CX, 1), (MX, 2)):
        verify_corner_endpoint(nodes, resource)
        props = nodes[resource]["props"]
        if (props["qcom,resource-name"] != b"smpa\0" or
                cells(props["qcom,resource-id"]) != (number,) or
                cells(props["qcom,regulator-type"]) != (1,)):
            raise ValueError("wrong original RPM resource tuple: " + resource)
        disabled(nodes, resource + "/regulator-s%d" % number)
        clients = [("corner", 3, "qcom,use-voltage-corner"),
                   ("corner-ao", 1, "qcom,use-voltage-corner")]
        if number == 1:
            clients.append(("floor-corner", 3, "qcom,use-voltage-floor-corner"))
        names = {"regulator-s%d" % number}
        names.update("regulator-s%d-%s" % (number, suffix) for suffix, _, _ in clients)
        if {node["name"] for node in nodes[resource]["children"]} != names:
            raise ValueError("unexpected regulator clients on original corner resource")
        for suffix, mask, key in clients:
            path = resource + "/regulator-s%d-" % number + suffix
            child = nodes[path]["props"]
            if (not child.get("compatible") == b"qcom,rpm-smd-regulator\0" or
                    child.get("status", b"okay\0") not in (b"okay\0", b"ok\0") or
                    child.get("regulator-name") != ("pm8994_s%d_%s\0" %
                        (number, suffix.replace("-", "_"))).encode() or
                    cells(child["qcom,set"]) != (mask,) or
                    cells(child["regulator-min-microvolt"]) != (1,) or
                    cells(child["regulator-max-microvolt"]) != (7,) or child.get(key) != b"" or
                    ("qcom,use-voltage-floor-corner" in child) != (suffix == "floor-corner") or
                    ("qcom,use-voltage-corner" in child) != (suffix != "floor-corner") or
                    ("qcom,always-send-voltage" in child) != (suffix == "floor-corner")):
                raise ValueError("logical corn/vfc/set semantics differ from Sony: " + path)
    if mode == "gated":
        disabled(nodes, CX)
    elif not available(nodes, CX):
        raise ValueError("active mode requires a live typed CX provider")
    disabled(nodes, MX)
    for path in ("/soc/cpr3-ctrl@838000", "/soc/regulator@7af004",
                 "/soc/qcom,spmi@400f000/pmic@3/regulator@1700",
                 MMCC + "/gfx-clock-controller"):
        disabled(nodes, path)
    for number in (1, 2, 8, 9, 10, 11):
        disabled(nodes, NATIVE + "/s%d" % number)
    if (nodes[NATIVE]["props"].get("compatible") != b"qcom,rpm-pm8994-regulators\0" or
            not available(nodes, NATIVE)):
        raise ValueError("physical PM8994 RPM regulator provider is unavailable")
    for path, node in nodes.items():
        compatible = node["props"].get("compatible", b"").split(b"\0")
        if b"qcom,pm8994-regulators" in compatible and available(nodes, path):
            raise ValueError("direct SPMI provider would create a second CX/MX owner: " + path)
        if (b"qcom,rpm-pm8994-regulators" in compatible and path != NATIVE and
                available(nodes, path)):
            raise ValueError("additional native PM8994 RPM resource owner: " + path)
        if (path not in (CX, MX) and available(nodes, path) and
                node["props"].get("qcom,resource-name") == b"smpa\0" and
                cells(node["props"].get("qcom,resource-id", b"")) in ((1,), (2,))):
            raise ValueError("additional active CX/MX resource owner: " + path)
    analog = NATIVE + "/l12"
    if (not available(nodes, analog) or
            cells(nodes[analog]["props"]["regulator-min-microvolt"]) != (1800000,) or
            cells(nodes[analog]["props"]["regulator-max-microvolt"]) != (1800000,)):
        raise ValueError("MMPLL4 analog supply must be real PM8994 L12 at 1.8 V")
    for path, compatible in ((GCC, b"qcom,gcc-msm8996\0"), (MMCC, b"qcom,mmcc-msm8996\0")):
        props = nodes[path]["props"]
        if not available(nodes, path) or props.get("compatible") != compatible or COUPLING not in props:
            raise ValueError("native clock provider lacks mandatory corner coupling: " + path)
        if target(nodes, path, "vdd-dig-supply", refs) != CX + "/regulator-s1-corner":
            raise ValueError("native clock digital supply is not BOTH-set CX: " + path)
    if (target(nodes, MMCC, "mmpll4-dig-supply", refs) != CX + "/regulator-s1-corner" or
            target(nodes, MMCC, "mmpll4-analog-supply", refs) != analog):
        raise ValueError("MMPLL4 supplies do not match Sony CX and physical L12")


def selftest(nodes, config, mode):
    # The other policy is tested only on a copied in-memory fixture; this never
    # edits a DTB or activates hardware in the board source.
    other = "active" if mode == "gated" else "gated"
    fixture = copy.deepcopy(nodes)
    fixture[CX]["props"]["status"] = b"okay\0" if other == "active" else b"disabled\0"
    validate(fixture, config, other)
    cases = [
        (CX, "compatible", LEGACY),
        (CX, "qcom,rpm", nodes[GCC]["props"]["phandle"]),
        (RPM, "compatible", b"qcom,rpm-sdm845\0"),
        ("/rpm-glink", "status", b"disabled\0"),
        (CX, "status", b"okay\0" if mode == "gated" else b"disabled\0"),
        (MX, "status", b"okay\0"),
        ("/chosen", "bootargs", b"maxcpus=2 maxcpus=4\0"),
        ("/chosen", "bootargs", b"maxcpus=2 maxcpus=2\0"),
        ("/chosen", "bootargs", b"maxcpus=2 boot_cpus=2-3\0"),
        (CX + "/regulator-s1-corner", "qcom,set", struct.pack(">I", 1)),
        (CX + "/regulator-s1-corner-ao", "qcom,set", struct.pack(">I", 3)),
        (CX + "/regulator-s1-corner", "regulator-max-microvolt", struct.pack(">I", 1000000)),
        (CX + "/regulator-s1-floor-corner", "qcom,use-voltage-corner", b""),
        (NATIVE + "/s1", "status", b"okay\0"),
        (NATIVE + "/s2", "status", b"okay\0"),
        (NATIVE + "/s11", "status", b"okay\0"),
        (NATIVE + "/l12", "regulator-max-microvolt", struct.pack(">I", 1900000)),
        (GCC, COUPLING, None),
        (MMCC, "vdd-dig-supply", None),
        (MMCC, "mmpll4-analog-supply", nodes[CX + "/regulator-s1-corner"]["props"]["phandle"]),
        (MMCC + "/gfx-clock-controller", "status", b"okay\0"),
    ]
    for path, prop, replacement in cases:
        bad = copy.deepcopy(nodes)
        if replacement is None:
            bad[path]["props"].pop(prop, None)
        else:
            bad[path]["props"][prop] = replacement
        try:
            validate(bad, config, mode)
        except (ValueError, KeyError, IndexError, struct.error):
            continue
        raise ValueError("accepted unsafe fixture: %s:%s" % (path, prop))
    for compatible in (b"qcom,pm8994-regulators\0qcom,spare-fallback\0",
                       b"qcom,rpm-pm8994-regulators\0",
                       b"qcom,rpm-smd-regulator-resource\0"):
        bad = copy.deepcopy(nodes)
        path = "/soc/shadow-regulators"
        bad[path] = {"path": path, "name": "shadow-regulators", "children": [],
                     "props": {"compatible": compatible,
                               "qcom,resource-name": b"smpa\0",
                               "qcom,resource-id": struct.pack(">I", 1)}}
        try:
            validate(bad, config, mode)
        except ValueError:
            continue
        raise ValueError("accepted an additional CX/MX hardware owner")
    for key in ("REGULATOR_QCOM_RPM_CORNER", "MSM_MMCC_8996", "CPU_FREQ"):
        bad = dict(config, **{key: "n" if config[key] == "y" else "y"})
        try:
            validate(nodes, bad, mode)
        except ValueError:
            continue
        raise ValueError("accepted missing/unsafe config: " + key)
    try:
        validate(nodes, dict(config, CMDLINE='\"maxcpus=4\"'), mode)
    except ValueError:
        pass
    else:
        raise ValueError("accepted an unvalidated built-in command line")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path, nargs="+")
    parser.add_argument("--config", type=Path, required=True, help="resolved O=/.config")
    policy = parser.add_mutually_exclusive_group(required=True)
    policy.add_argument("--require-gated", action="store_true")
    policy.add_argument("--require-active", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    mode = "active" if args.require_active else "gated"
    try:
        config = CONFIG.assignments(args.config)
        for dtb in args.dtb:
            nodes = POWER.parse_fdt(dtb)
            validate(nodes, config, mode)
            if args.selftest:
                selftest(nodes, config, mode)
            print("PASS: %s typed CX/%s clock voltage ownership; MX/GPU/CPU remain gated" %
                  (dtb.name, mode))
    except (ValueError, KeyError, IndexError, struct.error) as error:
        print("FAIL:", error)
        return 1
    print("DT/config data only: physical voltage, RPM ACK and clock transitions are unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
