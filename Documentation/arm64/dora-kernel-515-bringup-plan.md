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

## Gate 1A (P0): implement the missing SPM voltage/AVS backend

This is a mandatory implementation milestone, not a documentation-only audit.

1. Compare Sony 4.4 SPM and SAW3 register definitions, CPU affinity,
   VCTL/PMIC_DATA/AVS_CTL/AVS_LIMIT register layout and interrupt flow
   against the existing Linux 5.15 `drivers/regulator/qcom_spmi-regulator.c`.
2. Review upstream SAW/SPM regulator work (2024 SAW2 regulator series)
   and 2026 SAW control/locking fixes as reference designs; **do not**
   transplant SAW2 register offsets into SAW3 without verification.
3. Implement a single MSM8996-matched backend for
   `msm_spm_probe_done`, `msm_spm_set_vdd`, `msm_spm_get_vdd`,
   `msm_spm_avs_enable/disable/set_limit` and the AVS IRQ functions
   actually used by CPR3. Resolve backend selection and symbol linkage.
4. Verify PMIC acknowledgement, timeouts, readback and CPU-affine
   execution; propagate failures without switching to a second writer.
5. Audit atomic vs sleeping contexts: no sleeping regmap locks in an
   IPI or spinlocked SAW critical section. Preserve unrelated SAW
   register bits and serialize VCTL/AVS operations.
6. Add targeted build and failure-path tests, then review the backend
   against Sony 4.4 and MSM8996-specific DT resources.

### Existing SAW3 code discovered in this branch

`drivers/regulator/qcom_spmi-regulator.c` already contains SAW3 register
definitions, `spmi_saw_set_vdd()`, and `spmi_regulator_saw_set_voltage()`
with serialized PMIC status polling. The earlier CPU0 IPI callback
was removed because syscon regmap access may sleep. SAW-leader nodes
now receive per-regulator ops copies; SAW-slave nodes are skipped.
Failure after any SAW3 write latches an uncertain state, and AVS limit
calculation rejects selectors that do not fit its six-bit field.

**Still P0:** establish whether MSM8996 requires CPU-local SAW register
access, verify both cluster register windows and their ownership,
implement the actual `msm_spm_*` voltage/AVS API needed by staged CPR3,
and ensure there is only one writer for each CPU supply. Source changes
above have not been compile-tested or validated on hardware. Treat this
as a potentially reusable backend, not a validated one.

## Gate 1B (P0): complete remaining power-chain components

- **APM:** correct LMH/SCM lock coordination, secure override protocol,
  switch sequencing and rollback, voltage supply ownership.
- **MEM-ACC:** secure fuse service implementation or verified nonsecure
  alternative, bounded QFPROM mapping, corner state and rollback.
- **SPM regulator:** adapt regulator API, SPMI access, AVS enable/limits,
  voltage stepping, recalibration, suspend and error recovery.
- **CPR3 HMSS:** port all required source and headers, fuse corners,
  quotient/RO tables, IRQ, thread and regulator aggregation logic.
- **Kryo/APCC:** validate PLL/clock transitions, OPP voltages, speedbins,
  MEM-ACC/APM sequencing and cpufreq safety handoff.
- **RPM/PMIC:** verify S11 rail ownership, votes and parent supplies;
  reject duplicate regulator writers.
- **Device Tree:** integrate verified supply phandles, SAW3/APM/MEM-ACC/
  CPR3 nodes and per-device differences, keeping experimental nodes
  disabled until dependencies are proven.
- **Kconfig/Makefile:** introduce explicit, testable dependencies; remove
  the `BROKEN` gate only after all required objects compile and their
  voltage paths have been reviewed, not merely to silence configuration.
- **Verification:** compile each driver, check symbol resolution, DT
  bindings, error unwinding and a conservative first-boot configuration.

Reference material: Linux SAW2 regulator patch series (Jan 2024),
`https://lists.openwall.net/linux-kernel/2024/01/02/74`; Qualcomm
SAW control/locking fix series (Oct 2026),
`https://lkml.iu.edu/2610.2/07335.html`. These are design references,
not proof of MSM8996 compatibility or of inclusion in Linux 5.15.

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
