#!/usr/bin/env python3
"""Check retained Sony panel data and ADC selection, with compiled graph checked separately."""
import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[2]
manifest = json.loads((root / "Documentation/sony/dora-panels.json").read_text())
base = root / "arch/arm64/boot/dts/qcom/sony/dora"
panels = []
for name, expected in manifest["reference_files_sha256"].items():
    data = (base / name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == expected, name + ": reference data changed"
for entry in manifest["panels"]:
    source = (base / entry["file"]).read_text()
    match = re.search(r"somc,lcd-id-adc\s*=\s*<([^>]+)>", source)
    low, high = (int(value, 0) for value in match[1].split())
    name = re.search(r'qcom,mdss-dsi-panel-name\s*=\s*"([^"]+)"', source)[1]
    assert (low, high, name) == (entry["adc_min"], entry["adc_max"], entry["panel_name"])
    assert "qcom,mdss-dsi-on-command" in source, entry["file"]
    assert "qcom,mdss-dsi-off-command" in source, entry["file"]
    panels.append((low, high, name))
assert {name for _, _, name in panels} == {"3", "4", "6", "8", "9"}
for i, (low, high, _) in enumerate(panels):
    assert low <= high
    for other_low, other_high, _ in panels[i + 1:]:
        assert high < other_low or other_high < low, "ambiguous panel ADC ranges"


def detect(adc):
    return [name for low, high, name in panels if low <= adc <= high]


for low, high, name in panels:
    assert detect(low) == [name] and detect(high) == [name]
    assert detect((low + high) // 2) == [name]
control = manifest["f8132_reported_los20"]
assert int(control["lcdid_adc"], 0) == control["physical"] == 1179626
assert detect(control["physical"]) == ["4"]
assert control["bitclk"] == control["byteclk"] * 8 == control["pclk"] * 6
assert (control["width"], control["height"], control["fps"], control["escclk"]) == (
    1080, 1920, 60, 19200000)
assert "somc,auo_synaptics_cmd_4_panel" == control["node"]
print("PASS: all five Sony panel datasets and init/off command bytes retained")
print("PASS: inclusive ADC boundaries for AUO/JDI/LGD/Sharp ID3/Sharp ID9")
print("PASS: reported F8132 0x11FFEA selects AUO Panel Name=4; clock ratios agree")
print("Source/data checks only: run check-display-dt.py for compiled graph and safety gates.")
