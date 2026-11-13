#!/usr/bin/env python3
"""Check Dora identity, active provider references and CPU observation links.

The separate variant checker verifies actual Sony hardware differences and
mandatory activation guards. Neither checker proves device/bootloader support.
"""
import argparse
import copy
import importlib.util
import json
import re
import struct
from pathlib import Path


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
spec = importlib.util.spec_from_file_location("dora_power_dt", SCRIPT.with_name("check-power-dt.py"))
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)
spec = importlib.util.spec_from_file_location("dora_cpu_dt", SCRIPT.with_name("check-cpu-dt.py"))
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)

APCC = "/soc/clock-controller@6400000"
# These are observational links to the one disabled HMSS/SPM gang and its
# distinct MX AO digital supply. They are not active regulator dependencies.
CPU_OBSERVATIONS = {
    "vdd-pwrcl-supply": ("/soc/cpr3-ctrl@99e8000/thread@0/regulator-pwrcl", b"apc0_pwrcl_corner\0"),
    "vdd-perfcl-supply": ("/soc/cpr3-ctrl@99e8000/thread@1/regulator", b"apc1_corner\0"),
    "vdd-cbf-supply": ("/soc/cpr3-ctrl@99e8000/thread@0/regulator-cbf", b"apc0_cbf_corner\0"),
    "vdd-dig-supply": ("/rpm-glink/rpm_requests/rpm-regulator-smpa2/regulator-s2-corner-ao", b"pm8994_s2_corner_ao\0"),
}


def observation_sources(provider, owner):
    """Fail closed if the live source can turn metadata into a power vote."""
    def code(source):
        return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)

    provider, owner = code(provider), code(owner)
    tables = re.findall(r"static\s+const\s+struct\s+clk_ops\s+(\w+)\s*=\s*\{(.*?)\};",
                        provider, re.S)
    if len(tables) != 1 or tables[0][0] != "msm8996_boot_ops":
        raise ValueError("CPU observation requires the audited read-only clock operations")
    fields = re.findall(r"\.(\w+)\s*=", tables[0][1])
    if (fields != ["recalc_rate"] or
            not re.fullmatch(r"\s*\.recalc_rate\s*=\s*msm8996_boot_recalc\s*,?\s*", tables[0][1])):
        raise ValueError("CPU observation cannot bypass disabled providers for writable clock operations")
    registrations = re.findall(r"\binit\.ops\s*=\s*&\s*(\w+)\s*;", provider)
    if registrations != ["msm8996_boot_ops"]:
        raise ValueError("CPU observer operations registration changed")
    if re.search(r"\bclk_(?:prepare_enable|prepare|enable|disable_unprepare|unprepare|disable|set_rate|set_parent)\s*\(", provider):
        raise ValueError("CPU observer unexpectedly controls clocks")
    if re.search(r"\b(?:regulator_\w+|devm_regulator_\w+|"
                 r"qcom_rpm_corner_sync|msm8996_cpu_native_\w+|msm8996_cpu_transaction_\w+|"
                 r"write[blwq]\w*|__raw_write\w*|iowrite\w*|write_sysreg\w*|"
                 r"regmap_(?:write\w*|update\w*))\s*\(", provider + owner):
        raise ValueError("CPU observational metadata gained a regulator, transaction or register write path")
    if ("qcom_cpr3_cpu_get_state_by_node(" not in owner or
            "qcom_rpm_corner_get_state_by_node(" not in owner):
        raise ValueError("CPU observer requires typed read-only provider snapshots")


def cells(value):
    if len(value) % 4:
        raise ValueError("property length is not a multiple of four")
    return struct.unpack(">%dI" % (len(value) // 4), value)


def strings(value):
    if not value or value[-1]:
        raise ValueError("unterminated string property")
    return value[:-1].decode().split("\0")


def validate(nodes, provider_source=None, owner_source=None):
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

    # The complete CPU check also pins source-derived fuse/CPR/SPM/APM data,
    # disabled controller ancestors, MX S2 ACTIVE-only metadata and all four
    # independently disabled RPM gang phases. Never exempt arbitrary supplies.
    cpu.validate(nodes)
    qcom = ROOT / "drivers/clk/qcom"
    observation_sources(
        provider_source if provider_source is not None else (qcom / "clk-cpu-8996.c").read_text(),
        owner_source if owner_source is not None else (qcom / "clk-msm8996-cpu-power.c").read_text())
    if nodes[APCC]["props"].get("compatible") != b"qcom,apcc-msm8996\0":
        raise ValueError("CPU observation exception requires the audited APCC provider")

    def observational_supply(node, name, handle):
        if node["path"] != APCC or name not in CPU_OBSERVATIONS:
            return False
        path, regulator_name = CPU_OBSERVATIONS[name]
        target = handles.get(handle)
        if (not target or target["path"] != path or
                target["props"].get("regulator-name") != regulator_name or available(target)):
            raise ValueError("incorrect or prematurely active CPU observational supply: " + name)
        return True

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
                        if not observational_supply(node, name, handle):
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


def selftest(nodes):
    provider_source = (ROOT / "drivers/clk/qcom/clk-cpu-8996.c").read_text()
    owner_source = (ROOT / "drivers/clk/qcom/clk-msm8996-cpu-power.c").read_text()
    props = nodes[APCC]["props"]
    mx_path = CPU_OBSERVATIONS["vdd-dig-supply"][0]
    changes = [
        (APCC, "vdd-pwrcl-supply", props["vdd-perfcl-supply"]),
        (APCC, "vdd-dig-supply", props["vdd-cbf-supply"]),
        (APCC, "unexpected-supply", props["vdd-pwrcl-supply"]),
        ("/soc/cpr3-ctrl@99e8000", "status", b"okay\0"),
        (mx_path.rsplit("/", 1)[0], "status", b"okay\0"),
        (mx_path, "qcom,set", struct.pack(">I", 3)),
        ("/rpm-glink/rpm_requests/pm8994-regulators/s8", "status", b"okay\0"),
        ("/rpm-glink/rpm_requests/pm8994-regulators/s11", "status", b"okay\0"),
    ]
    cases = []
    for path, key, replacement in changes:
        bad = copy.deepcopy(nodes)
        bad[path]["props"][key] = replacement
        cases.append((path + ":" + key, bad, provider_source, owner_source))
    bad = copy.deepcopy(nodes)
    path = "/soc/active-provider-regression"
    bad[path] = {"path": path, "props": {"vdd-supply": props["vdd-pwrcl-supply"]}}
    cases.append(("active consumer of disabled regulator", bad, provider_source, owner_source))
    cases.extend([
        ("writable clock operation", nodes,
         provider_source.replace(".recalc_rate = msm8996_boot_recalc,",
                                 ".recalc_rate = msm8996_boot_recalc,\n.enable = unsafe_enable,"), owner_source),
        ("pointer-valued writable clock operation", nodes,
         provider_source.replace(".recalc_rate = msm8996_boot_recalc,",
                                 ".recalc_rate = msm8996_boot_recalc,\n.enable = &unsafe_enable,"), owner_source),
        ("CPU clock mutation", nodes, provider_source + "\nvoid unsafe(void) { clk_set_rate(0, 1); }\n", owner_source),
        ("regulator consumer acquisition", nodes, provider_source,
         owner_source + "\nvoid unsafe(void) { regulator_get_optional(0, 0); }\n"),
        ("CPU voltage vote", nodes, provider_source,
         owner_source + "\nvoid unsafe(void) { regulator_set_voltage(0, 1, 2); }\n"),
        ("native callback binding", nodes, provider_source,
         owner_source + "\nvoid unsafe(void) { msm8996_cpu_native_bind(0, 0, 0, 0); }\n"),
        ("raw MMIO write", nodes, provider_source,
         owner_source + "\nvoid unsafe(void) { __raw_writel(1, 0); }\n"),
        ("I/O MMIO write", nodes, provider_source,
         owner_source + "\nvoid unsafe(void) { iowrite32(1, 0); }\n"),
    ])
    for name, bad, provider, owner in cases:
        try:
            validate(bad, provider, owner)
        except (KeyError, ValueError):
            continue
        raise ValueError("provider checker accepted unsafe fixture: " + name)
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        nodes = power.parse_fdt(args.dtb)
        count = validate(nodes)
        negatives = selftest(nodes) if args.self_test else 0
    except (KeyError, ValueError, OSError, struct.error) as exc:
        parser.exit(1, "Dora DT check failed: %s\n" % exc)
    print("Dora DT: original SoC/PMIC tuple, four CPU nodes/maxcpus=2, %d provider references checked" % count)
    if args.self_test:
        print("Dora DT: %d unsafe dependency/source mutations rejected" % negatives)
    print("LIMIT: provider references only; run variant/CPU/display/bus checks; hardware activation remains gated")


if __name__ == "__main__":
    main()
