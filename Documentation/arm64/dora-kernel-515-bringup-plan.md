# MSM8996 Dora Linux 5.15 bring-up: blocking work and validation gates

Scope: Sony Xperia X Performance F8131 and F8132; branch `kernel-5.15`.
This is a source-level plan, **not** a claim of successful compilation or boot.
Do not enable CPU frequency scaling, CPR3 or experimental CPU rail writers
until their dependencies and hardware ownership are proven.

## Gate 0: establish a reproducible baseline

- Record branch HEAD, compiler version, defconfig, DTB targets, and all
  out-of-tree patches. Preserve the known-good Linux 4.4 boot image.
- Build baseline `Image`, `dtbs`, and modules with the target toolchain.
- Capture compiler/linker diagnostics and identify the first actual failure.
- Ensure the build has no stale output from previous kernel versions.

## Gate 1 (P0): CPU power and clock ownership

- Inventory APCC clock, SAW3/SPM, PM8994 S11, RPM regulator, APM,
  MEM-ACC, CPR3, and CPUFreq writers. One voltage rail must have one
  authoritative writer, with an explicit handoff protocol if needed.
- The staged `reference/spm.c` implements CPU idle/collapse flows, **not**
  the complete `msm_spm_set_vdd/get_vdd/avs_*` backend required by
  `reference/spm-regulator.c`. Do not wire the latter to stub returns.
- Port the missing SPM voltage/AVS register operations from an
  MSM8996-matched implementation, validating offsets, locking, ordering,
  CPU/cluster association, and failure semantics against hardware docs.
- Replace legacy SCM/PSCI calls with correct Linux 5.15 interfaces;
  validate firmware call IDs and return values rather than assuming
  generic `qcom_scm_io_*` covers secure fuse services.
- Validate MEM-ACC fuse access, mapping bounds, and transition order.
- Validate APM secure override lock ownership and error unwinding.
- Port CPR3 fuse interpretation, regulator aggregation, corner/quotient
  calculation, and IRQ handling; compare fuse-derived values to Sony 4.4.
- Keep `REGULATOR_QCOM_CPR3_HMSS_PORT` gated and `apcc_cpr` disabled
  until this entire gate is compile-tested and runtime-validated.
- Do not remove the MSM8996 CPUFreq fail-closed guard before voltage
  and clock transitions are demonstrated safe.

## Gate 2 (P0): early boot hardware

- Check eMMC/SDHCI, RPM/RPMCC, GCC/MMCC, PMIC SPMI, pinctrl, regulators,
  reset, reserved-memory, IOMMU, DMA, interconnect and interrupt routing.
- Audit bootloader DTB selection, memory map, early console and pstore.
- Build and validate all F8131/F8132 DTBs; retain runtime panel
  identification instead of hard-coding one F8132 display variant.

## Gate 3 (P1): Android userspace prerequisites

- Verify Binder/binderfs, SELinux, namespaces, cgroups, PSI, ZRAM,
  filesystem features, dm-verity, USB gadget and Android boot parameters.
- Check initramfs/boot image layout and compatibility with the target
  LineageOS device tree and vendor blobs.

## Gate 4: clean build acceptance

- Clean build `Image`, `dtbs`, and required modules using the actual
  LineageOS kernel toolchain; no missing symbols or unresolved dependencies.
- Run `dtbs_check` for supported bindings and `W=1` targeted builds.
- Review `nm`/`readelf` outputs for unresolved or unexpected symbols.
- Produce an artifact manifest with SHA256, compiler revision, config,
  DTB names and commit SHA. No untested claim of BUILDABLE.

## Gate 5: controlled first device boot

- Keep a tested recovery path and verified known-good boot image.
- Initially use conservative CPU frequency and voltage settings; do not
  activate unverified CPR3/AVS or deep idle modes.
- Collect early console, ramoops/pstore, kernel dmesg and Android logs.
- Check boot progression: bootloader -> kernel -> init -> mount ->
  Android services -> display. Fix observed faults, then repeat.
- Verify both F8131 and F8132, including panel variants, Wi-Fi, modem,
  audio, thermal management, suspend/resume and repeated cold boots.

## Definition of done

**Ready to build:** source dependencies resolved and clean build reproducible.
**Ready for first boot:** artifact and recovery procedure verified; no known
P0 boot blockers or uncontrolled CPU voltage writes.
**Boot validated:** real-device logs demonstrate stable boot and repeated
operation. Source review alone cannot establish this final condition.
