# Dora MSM8996 Linux 5.15 — Stage 65 experimental checkpoints

**Status: SOURCE/BUILD EXPERIMENT — NOT BOOT-VERIFIED — NOT FLASH-APPROVED.**

Captured 2026-11-18 for Sony Xperia X Performance **dora** (F8131/F8132) and LineageOS 21 / Android 14. The working phone remains on Linux **4.4.302+**. A Linux 5.15 boot test previously stalled at the Sony logo; its recovery pstore was empty.

## Git vs local WSL

At capture, published `kernel-5.15` was `0f955fbca80d3ccc7834f5524b1a5dc25a7ad6c9`, while the latest *locally observed* WSL HEAD was `7c054647d39abd40824df7d784e5b3644aa7f214` (not then available on GitHub). Do not assume this GitHub branch contains all local changes until the WSL history is pushed.

The local checkout has an **uncommitted** `arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-dora-common.dtsi` (Stage 65N), SHA-256 `9ab5ca627db0ad6c6364d053bb60a78b50e325f1fa655b15ad1d21ef18dd7b5a`. It was incorporated into the *isolated* build, but is NOT added by this checkpoint commit.

## Tested preview changes (not applied by this commit)

Patches under `patches/` are the exact **PREVIEW** changes validated *together* in an isolated Stage 65AD build:
- Stage 65Y: defer APCC register programming until SAW3 `msm_spm_probe_done()` succeeds, without claiming S11 voltage or PLL lock safety. SHA-256 `2c4e164571e5dd1d82712240558ac6a01c92793f80d872bf07521cf73dab42d4`.
- Stage 65AB: fix use of possibly uninitialized `vdd_volt_uv` in Kryo LPM. SHA-256 `e7c113b3306cf0a9f27aa35ab36f864245cc3ffa93ec973947c8e933201f6e0c`.
- Stage 65AC: propagate a failed SAW3 probe instead of masking it with `-EPROBE_DEFER`. SHA-256 `646aa829992227769b9057bc07af7fb2d0c8749b653a950b71491a154559c138`.

Isolated Stage 65AD: `PASS_ISOLATED_VMLINUX_THREE_PATCHES_DORA_DTBS_ONLY`.
- `vmlinux` 45,972,760 bytes, SHA-256 `3900fa450077adee07ede9f925a1e0e573f1bdd90e49336718558e2bf7d8abe8`.
- Preserved `.config` SHA-256 `a8cd58323213599606ac9f86de292fd99c3b5bcb20948c5970a0601f098c2fe4`.
- Selected Stage 65N DTB `msm8996-pmi8996-sony-xperia-tone-dora.dtb` SHA-256 `efa7739a16e962cd485a4375a49b0332e46d51400d91a00e0cde1a8d78605ac5`. Four variants built.

The isolated build produced **no flashable boot.img**. Stage 65AE source and ELF SHA checks passed, but its automatic disassembly command was incompatible with GNU objdump; a later analysis confirmed the built-in SAW3 gate in ELF. Stage 65AF remains an unverified local read-only audit script.

## Open P0 blockers

- Actual Sony S1 legacy-boot Image/ramdisk/FDT placement and overlap remain unverified.
- Both Kryo cluster PLL/ALT PLL lock and voltage/handoff safety are not verified on hardware.
- SAW3 runtime probe and actual PMIC rail state are unverified.
- **Do not apply unsafe speculative Gemini suggestions** (out-of-range APCC AUX MMIO, live PLL_MODE zeroing, unverified self-relocation code).
- **Never** overwrite the stable Linux 4.4 backup or force-push over local WSL commits.

Next: reconcile the local Git history and the Stage 65N source, then optionally apply the three independently reviewed preview patches into kernel sources via a separate commit. Publication/compilation alone is not boot qualification.
