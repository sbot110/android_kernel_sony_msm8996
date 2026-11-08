# Dora Linux 5.15: first-boot blockers and validation gates

Target: Sony Xperia X Performance F8131/F8132 (MSM8996), LineageOS 21.
Known-good baseline: branch Lineageos-21, Linux 4.4.
Experimental branch: kernel-5.15 (Linux 5.15.220).

## P0: CPU voltage and CPR

The 5.15 msm8996.dtsi CPU nodes use qcom,kryo OPP tables and APCC clocks.
The checked OPP entries do not declare opp-microvolt and the CPU nodes do
not declare cpu-supply. This alone does not prove that voltage regulation is
missing: determine whether firmware, RPM, or another driver owns it before
making changes.

The known-good 4.4 tone_dora_defconfig enables:
CONFIG_REGULATOR_CPR3_HMSS, CONFIG_REGULATOR_SPM, CONFIG_REGULATOR_KRYO,
CONFIG_REGULATOR_CPR3_MMSS.

Required before a boot test:
1. Trace cpufreq driver -> OPP selection -> clock rate change -> voltage owner.
2. Verify CPU and L2 voltage corners against actual Sony 4.4 DTS and driver
   behavior, including speed bins, efuses, RPM requests and CPR calibration.
3. Confirm that voltage is raised before a frequency increase and lowered
   only after a frequency decrease, if software controls voltage.
4. Audit ACD/HWFSM PLL notifier paths for failure and rollback.
5. Do not synthesize opp-microvolt values from frequency alone.

## P0: eMMC and early boot

Dora boots from SDHC1 eMMC, not UFS. HS400 is deliberately disabled for
initial bring-up; HS200 is still advertised. Before flashing:
1. Build dora_defconfig from a clean tree and inspect warnings/errors.
2. Confirm SDHC1 registers, GCC reset, RPM XO, regulator constraints and
   pinctrl against the working 4.4 DTS.
3. Confirm boot.img kernel compression, DTB concatenation/selection,
   ramdisk format, cmdline, boot header and partition limits against the
   bootable LOS21 4.4 image.
4. Confirm ramoops reserved-memory does not overlap other allocations.
5. Preserve all four Dora DTB variants and panel autodetection.
6. Preserve F8131 and F8132 microSD/UIM2 differences.

## First boot decision

NOT CLEARED for flashing. No successful build, voltage-control validation,
boot-image comparison or hardware test has been recorded for this branch.

Never overwrite the known-good Lineageos-21 branch or its bootable images.
