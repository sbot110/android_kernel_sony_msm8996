# Stage 57 — Dora 4.19: single DTB, pstore, USB experiment and SDHC1 diagnostics

Status: source/build candidate only. No hardware boot qualification, no flashing.

## Exact target DTB

The LOS22.2 test image now appends **one** boot DTB:
`qcom/boot/msm8996-v3.1-pmi8996-sony-xperia-tone-dora-lineage21-boot`.
Its expected selection tuple is MSM8996 `[246, 0x30001]`, PMIC
`[0x20009, 0x10013, 0, 0]`, board `[8, 0]`, matching the previously captured
working F8132 runtime IDs (DTB index 3 in the former four-DTB order). The other
Dora DTS source variants remain in the repository but are not appended to this
single-device test image. This does not claim that the image supports every
F8131/F8132 hardware revision.

The selected wrapper inherits the existing SDHC1 diagnostic overlay and the
Lineage first-stage boot overlay. The normal storage DTS is kept disabled;
SDHC1 is enabled only by the selected boot DTB. No `qcom,force-sdhc1-probe`
property is added.

## Memory and pstore

- `ramoops@a7f00000` retains/adds `no-map`.
- DFPS reserves `[0x83400000, 0x83401000)` without `no-map`, matching the
  existing Sony 4.4 reservation semantics.
- Two `no-map` gaps are reserved: `[0x90402000, 0x90500000)` and
  `[0x91400000, 0x91500000)`. Static interval checks reject overlaps with the
  adjacent splash, zap shader, SLPI, Venus and MBA ranges.
- Soft-lockup, hung-task and oops panic diagnostics are enabled with
  `CONFIG_PANIC_TIMEOUT=5`. This requests a reboot after panic; it does not
  guarantee that Sony S1 preserves ramoops across its reset path.

## USB: intentionally experimental

The Dora-specific DWC3 parent and QUSB2 PHY early-return guards are removed as
requested. The existing tree already enables the USB3 parent and QUSB2 PHY in
fixed peripheral mode; the DWC3 child is now explicitly `status = "okay"`.
The USB mode remains `dr_mode = "peripheral"`, `maximum-speed = "high-speed"`,
with extcon/OTG ID handling removed for Dora.

**Important:** removing the gates does not implement Sony's missing shared
charger/DPDM/VBUS ownership handoff. The code emits warnings when Dora USB is
initialized. Charging detection, OTG, host mode and simultaneous charger/PHY
ownership are not qualified. Treat USB as an isolated bring-up experiment, not
as daily-use-ready functionality.

## SDHC1 / RPM / GCC barriers

The physical RPM ACK and GCC supplier barriers are preserved; no fake provider,
forced SDHC1 probe, voltage override or ACK bypass is introduced. The SDHCI
consumer now logs the exact clock supplier that is absent/unbound, regulator
readiness failures, physical ACK sync failures and bootdevice mismatch. These
messages are diagnostic only and do not change the gating decisions.

The existing `sony,require-rpm-physical-acks`, `qcom_rpm_physical_check_ready()`,
`qcom_rpm_physical_sync()` and `sdhci_msm_link_clock_providers()` checks remain.
A static DT phandle is not evidence of a runtime RPM ACK or successful GCC
probe. SDHC1 runtime probe remains unverified until a hardware log is available.

## Android boot header addresses

No address change is made to the active LOS22.2 ROM build path: the installed
boot-partition dump and the Stage38 ROM validator already use
`kernel_addr=0x80008000`, `ramdisk_addr=0x82000000`, `tags_addr=0x81e00000`.
Those are Android boot-header fields, not proof of Sony S1's expanded Image or
runtime initrd placement. The separate LK2nd source-layout profile is not used
by Stage38 and is deliberately not rewritten to conflate those meanings. The
ROM build must continue to pass the existing header check.

## Build and publication contract

Stage57 first builds a fresh out-of-tree Linux 4.19 `Image.gz-dtb` from this
working source tree and verifies exactly one appended FDT with the expected
F8132 selection IDs. Only that newly built payload is passed to the LOS22.2
Stage38 ROM builder. The script does not reuse the old Stage27 prebuilt payload.

Existing source modifications in the approved kernel source directories are
preserved and included in the same commit after a successful build so GitHub
matches the source used for the image. Local backup snapshots (`*.before-*`,
`*.bak`, logs and similar) are preserved locally and excluded from publication.
The push is a normal non-force push to the existing branch.

`BOOT_TEST_READY=NO`, `FLASH_ALLOWED=NO`. No fastboot, dd, partition write,
reboot or phone access is performed by this script.
