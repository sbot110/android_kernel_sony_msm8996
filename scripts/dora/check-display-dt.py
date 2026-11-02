#!/usr/bin/env python3
"""Validate the active Sony MDP/DSI/WLED chain against final 4.4 hardware DTs.

Checks structural dependencies and retained electrical/command data. Neither
successful probe nor panel, secure-display or bootloader behavior is implied.
"""
import argparse
import copy
import importlib.util
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dora_power", Path(__file__).with_name("check-power-dt.py"))
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)


def cells(data):
    if len(data) % 4:
        raise ValueError("unaligned cells")
    return struct.unpack(">%dI" % (len(data) // 4), data)


def strings(data):
    if not data or data[-1]:
        raise ValueError("unterminated strings")
    return data[:-1].decode().split("\0")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(nodes, config=None, require_gated=False):
    manifest = json.loads((ROOT / "Documentation/sony/dora-display-dt.json").read_text())
    suffixes = {
        "mdp": "qcom,mdss_mdp@900000", "dsi": "qcom,mdss_dsi",
        "dsi0": "qcom,mdss_dsi_ctrl0@994000", "labibb": "qpnp-labibb-regulator",
        "wled": "qcom,leds@d800", "hybrid": "dsi_panel_pwr_supply_hybrid_incell",
        "full": "dsi_panel_pwr_supply_full_incell", "mdp_smmu": "arm,smmu-mdp@d00000",
        "rot_smmu": "arm,smmu-rot@d20000",
    }
    panel_names = ("auo_synaptics_cmd_4", "jdi_synaptics_cmd_6", "lgd_synaptics_cmd_8",
                   "sharp_synaptics_cmd_3", "sharp_synaptics_cmd_9")
    suffixes.update({name: "somc," + name + "_panel" for name in panel_names})
    selected = {key: power.find(nodes, "/" + suffix) for key, suffix in suffixes.items()}
    handles = {cells(node["props"]["phandle"])[0]: node for node in nodes.values()
               if "phandle" in node["props"]}

    gates = {selected[key]["path"] for key in ("mdp", "dsi", "mdp_smmu", "rot_smmu", "labibb", "wled")}
    gates.update(power.find(nodes, "/qcom,mdss_dsi_pll@%x" % address)["path"]
                 for address in (0x994400, 0x996400))
    if require_gated:
        for path in gates:
            require(nodes[path]["props"].get("status") == b"disabled\0",
                    "display requires clock/corner safety gate: " + path)

    def available(node):
        path = node["path"]
        while True:
            if not (require_gated and path in gates):
                require(nodes[path]["props"].get("status", b"okay\0") in (b"okay\0", b"ok\0"),
                        "disabled display dependency: " + path)
            if path == "/":
                return
            path = path.rsplit("/", 1)[0] or "/"

    def target(node, prop):
        ids = cells(node["props"].get(prop, b""))
        require(len(ids) == 1 and ids[0] in handles, node["path"] + ": missing " + prop)
        result = handles[ids[0]]
        available(result)
        return result

    def same_target(node, prop, expected):
        require(target(node, prop)["path"] == expected["path"], node["path"] + ": wrong " + prop)

    def clock_list(node):
        words = cells(node["props"].get("clocks", b""))
        result = []
        while words:
            require(words[0] in handles, "unresolved display clock")
            provider = handles[words[0]]
            available(provider)
            count, = cells(provider["props"]["#clock-cells"])
            require(count == 1 and len(words) >= 2, "expected real one-cell display clock")
            result.append((provider["path"], words[1]))
            words = words[2:]
        names = strings(node["props"]["clock-names"])
        require(len(names) == len(set(names)) == len(result), "duplicate/missing display clock name")
        return dict(zip(names, result))

    for node in selected.values():
        available(node)
    for key, rows in manifest["sections"].items():
        base = selected[key]["path"]
        for suffix, expected in rows.items():
            path = base + suffix
            require(path in nodes, "missing original display node: " + path)
            actual = nodes[path]["props"]
            for name, value in expected.items():
                if name.endswith("-gpio"):
                    # Phandle numbering changes; compare controller/pin below.
                    continue
                expected_data = bytes.fromhex(value)
                if key == "mdp" and name == "interrupts":
                    # Old IRQ_TYPE_NONE becomes the real level-high GIC line.
                    require(cells(expected_data) == (0, 83, 0), "unexpected reference MDP IRQ")
                    expected_data = struct.pack(">3I", 0, 83, 4)
                require(actual.get(name) == expected_data, path + ": differs from final Sony DT: " + name)

    mdp, dsi, ctrl = (selected[key] for key in ("mdp", "dsi", "dsi0"))
    require(cells(mdp["props"]["reg"]) == (0x900000, 0x90000, 0x9b0000, 0x1040, 0x9b8000, 0x1040), "wrong MDP/VBIF ranges")
    require(cells(ctrl["props"]["reg"]) == (0x994000, 0x400, 0x994400, 0x588, 0x828000, 0x108), "wrong DSI0/PHY ranges")
    same_target(ctrl, "qcom,mdss-mdp", mdp)
    anchor = target(ctrl, "qcom,dsi-pref-prim-pan")
    require("somc,lcd-id-adc" not in anchor["props"], "default panel must not be an ADC wildcard")
    require(anchor["path"].rsplit("/", 1)[0] == mdp["path"], "panel enumeration has wrong parent")
    bus = power.find(nodes, "/ad-hoc-bus@520000")
    for key in ("mdp", "dsi", "mdp_smmu", "rot_smmu"):
        same_target(selected[key], "qcom,msm-bus-provider", bus)
    for node in (mdp, dsi):
        prop = "vdd-supply" if node is mdp else "gdsc-supply"
        gdsc = target(node, prop)
        require(gdsc["props"].get("compatible") == b"qcom,genpd-regulator\0", "display GDSC must use native genpd")
        require(len(cells(gdsc["props"].get("power-domains", b""))) == 2, "missing real GDSC domain")
        require(not any(p in gdsc["props"] for p in ("reg", "regulator-min-microvolt", "regulator-max-microvolt")), "duplicate/fake GDSC owner")
    for owner, prop, rail, voltage in ((dsi, "vdda-supply", "l2", 1250000), (dsi, "vcca-supply", "l28", 925000),
                                      (ctrl, "vddio-supply", "s4", 1800000), (ctrl, "touch-avdd-supply", "l22", 3000000)):
        regulator = power.find(nodes, "/pm8994-regulators/" + rail)
        same_target(owner, prop, regulator)
        require(cells(regulator["props"]["regulator-min-microvolt"]) == (voltage,) and
                cells(regulator["props"]["regulator-max-microvolt"]) == (voltage,), "wrong Sony display regulator voltage: " + rail)
    tlmm = power.find(nodes, "/pinctrl@1010000")
    for name, pin in (("te", 10), ("reset", 8), ("vddio", 51), ("touch-vddio", 50), ("touch-reset", 89), ("touch-int", 125)):
        handle, gpio, flags = cells(ctrl["props"]["qcom,platform-" + name + "-gpio"])
        require(handles[handle] is tlmm and (gpio, flags) == (pin, 0), "wrong Sony panel/touch GPIO")
    same_target(ctrl, "lab-supply", power.find(nodes, "/qcom,lab@de00"))
    same_target(ctrl, "ibb-supply", power.find(nodes, "/qcom,ibb@dc00"))
    require(cells(power.find(nodes, "/qcom,ibb@dc00")["props"]["qcom,qpnp-ibb-discharge-resistor"]) == (32,), "final Sony IBB discharge must be 32")
    require(cells(selected["wled"]["props"]["reg"]) == (0xd800, 0xd900), "WLED register format must match native SPMI parent")
    require(strings(selected["wled"]["props"]["linux,default-trigger"]) == ["bkl-trigger"], "missing Sony backlight trigger")

    # Each original interval must identify exactly one real panel, including
    # boundaries. Unknown IDs must leave the display unselected.
    panels = [child for child in mdp["children"] if "somc,lcd-id-adc" in child["props"]]
    require({node["path"] for node in panels} == {selected[key]["path"] for key in panel_names}, "unexpected panel selector candidates")
    intervals = []
    for key in panel_names:
        node = selected[key]
        same_target(node, "qcom,mdss-dsi-panel-controller", ctrl)
        supply = "hybrid" if key.startswith("jdi_") else "full"
        same_target(node, "qcom,panel-supply-entries", selected[supply])
        low, high = cells(node["props"]["somc,lcd-id-adc"])
        require(low <= high, "reversed panel ADC interval")
        intervals.append((low, high, node["path"]))
        timing = power.find(nodes, node["path"] + "/qcom,mdss-dsi-display-timings")
        require([child["name"] for child in timing["children"]] == ["1080p60"], "only 1080p60 may be active")
        props = timing["children"][0]["props"]
        require(tuple(cells(props[name])[0] for name in ("qcom,mdss-dsi-panel-width", "qcom,mdss-dsi-panel-height", "qcom,mdss-dsi-panel-framerate")) == (1080, 1920, 60), "unsafe display operating mode")
        for path, child in nodes.items():
            if path == node["path"] or path.startswith(node["path"] + "/"):
                require(not any(flag in child["props"] for flag in ("somc,change-fps-enable", "qcom,dynamic-mode-switch-enabled", "qcom,mdss-dsi-pan-enable-dynamic-fps", "qcom,dsi-dynamic-clk-enable")), "dynamic display switching enabled")
    for low, high, path in intervals:
        for adc in (low, (low + high) // 2, high):
            require([p for lo, hi, p in intervals if lo <= adc <= hi] == [path], "ambiguous panel LCD ID")
    for adc in (0x7fffffff, 0xffffffff):
        require(not any(lo <= adc <= hi for lo, hi, _ in intervals), "unknown LCD ID accepted")

    # Native CCF checks exact parent identity. The exported MMCC aliases are
    # distinct from the underlying vendor PLL mux objects.
    mmcc = power.find(nodes, "/clock-controller@8c0000")
    header = (ROOT / "include/dt-bindings/clock/qcom,mmcc-msm8996.h").read_text()
    ids = {name: int(value) for name, value in re.findall(r"^#define\s+(\w+)\s+(\d+)\s*$", header, re.M)}
    expected_clocks = {
        "mdp": {"iface_clk": "MDSS_AHB_CLK", "bus_clk": "MDSS_AXI_CLK", "core_clk_src": "MDP_CLK_SRC", "core_clk": "MDSS_MDP_CLK", "vsync_clk": "MDSS_VSYNC_CLK"},
        "dsi": {"mdp_core_clk": "MDSS_MDP_CLK", "iface_clk": "MDSS_AHB_CLK", "core_mmss_clk": "MMSS_MISC_AHB_CLK", "bus_clk": "MDSS_AXI_CLK"},
        "dsi0": {"byte_clk": "MDSS_BYTE0_CLK", "pixel_clk": "MDSS_PCLK0_CLK", "core_clk": "MDSS_ESC0_CLK", "byte_clk_rcg": "BYTE0_CLK_SRC", "pixel_clk_rcg": "PCLK0_CLK_SRC"},
        "mdp_smmu": {"mmagic_ahb_clk": "MMSS_MMAGIC_AHB_CLK", "mmagic_cfg_ahb_clk": "MMSS_MMAGIC_CFG_AHB_CLK", "mdp_ahb_clk": "SMMU_MDP_AHB_CLK", "mdp_axi_clk": "SMMU_MDP_AXI_CLK", "mmagic_mdss_axi_clk": "MMAGIC_MDSS_AXI_CLK"},
        "rot_smmu": {"mmagic_ahb_clk": "MMSS_MMAGIC_AHB_CLK", "mmagic_cfg_ahb_clk": "MMSS_MMAGIC_CFG_AHB_CLK", "rot_ahb_clk": "SMMU_ROT_AHB_CLK", "rot_axi_clk": "SMMU_ROT_AXI_CLK", "mmagic_mdss_axi_clk": "MMAGIC_MDSS_AXI_CLK"},
    }
    for key, expected in expected_clocks.items():
        actual = clock_list(selected[key])
        for name, clock in expected.items():
            require(actual.get(name) == (mmcc["path"], ids[clock]), key + ": wrong native MMCC clock " + name)
    for i in (0, 1):
        pll = power.find(nodes, "/qcom,mdss_dsi_pll@%x" % (0x994400 + i * 0x2000))
        require(cells(pll["props"]["reg"]) == (0x994400 + i * 0x2000, 0x588, 0x8c2300, 8, 0x994200 + i * 0x2000, 0x98), "wrong DSI PLL range")
        require(pll["props"]["compatible"] == b"qcom,mdss_dsi_pll_8996_v2\0", "wrong MSM8996 PLL implementation")
        require("memory-region" not in pll["props"], "dynamic PLL handoff must remain disabled")
        actual = clock_list(dsi)
        require(actual["ext_byte%d_clk" % i] == (pll["path"], 24 + 2 * i), "DSI byte parent must be MMCC alias")
        require(actual["ext_pixel%d_clk" % i] == (pll["path"], 25 + 2 * i), "DSI pixel parent must be MMCC alias")
    for name, provider, stream in (("mdp_unsec", "mdp_smmu", 0), ("rot_unsec", "rot_smmu", 0), ("mdp_sec", "mdp_smmu", 1), ("rot_sec", "rot_smmu", 1)):
        cb = next(n for n in mdp["children"] if n["props"].get("compatible") == ("qcom,smmu_" + name).encode() + b"\0")
        handle, sid = cells(cb["props"]["iommus"])
        require(handles[handle] is selected[provider] and sid == stream, "wrong MDP/ROT stream mapping")
        require(strings(selected[provider]["props"]["qcom,regulator-names"]) == ["vdd"], "SMMU must acquire its vdd supply")
        gdsc = target(selected[provider], "vdd-supply")
        same_target(cb, "gdsc-mmagic-mdss-supply", gdsc)
        clock_list(cb)
    for node, domain_id in ((target(mdp, "vdd-supply"), ids["MDSS_GDSC"]),
                            (target(selected["mdp_smmu"], "vdd-supply"), ids["MMAGIC_MDSS_GDSC"])):
        handle, index = cells(node["props"]["power-domains"])
        require(handles[handle] is mmcc and index == domain_id, "wrong native display GDSC")
    if config:
        content = Path(config).read_text()
        for symbol in ("REGULATOR_QCOM_GENPD", "LEDS_QPNP_WLED", "LEDS_TRIGGERS", "REGULATOR_QPNP_LABIBB", "REGULATOR_QPNP_LABIBB_SOMC", "FB_MSM_MDSS", "FB_MSM_MDSS_SPECIFIC_PANEL", "MDSS_PLL", "ARM_SMMU"):
            require("CONFIG_" + symbol + "=y\n" in content, "missing display config: " + symbol)
        require("CONFIG_BACKLIGHT_QCOM_SPMI_WLED=y" not in content, "modern incompatible WLED implementation selected")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--require-gated", action="store_true", help="validate retained graph and mandatory MMCC corner safety gates")
    args = parser.parse_args()
    nodes = power.parse_fdt(args.dtb)
    selected = validate(nodes, args.config, args.require_gated)
    if args.self_test:
        cases = [("mdp", "qcom,msm-bus-provider", None), ("dsi0", "clocks", b""),
                 ("dsi0", "lab-supply", None), ("auo_synaptics_cmd_4", "somc,change-fps-enable", b"")]
        for key, prop, value in cases:
            bad = copy.deepcopy(nodes)
            props = bad[selected[key]["path"]]["props"]
            if value is None:
                props.pop(prop)
            else:
                props[prop] = value
            try:
                validate(bad, args.config, args.require_gated)
            except (ValueError, KeyError):
                pass
            else:
                raise ValueError("self-test accepted missing/unsafe " + prop)
        if args.require_gated:
            bad = copy.deepcopy(nodes)
            bad[selected["mdp"]["path"]]["props"]["status"] = b"okay\0"
            try:
                validate(bad, args.config, True)
            except ValueError:
                pass
            else:
                raise ValueError("self-test accepted unsafe display activation")
        print("PASS: missing bus/clock/rail, unsafe FPS and safety gate mutations rejected")
    print("PASS: original final Sony hardware data, five LCD-ID panels, 1080p60 only")
    print("PASS: native MMCC/PLL parents, GDSC, SMMU, bandwidth, LAB/IBB and WLED chain")
    if args.require_gated:
        print("PASS: hardware activation gated pending real MMCC frequency/RPM-corner coupling")
    print("Structural validation only; on-device display/secure buffers remain untested.")


if __name__ == "__main__":
    main()
