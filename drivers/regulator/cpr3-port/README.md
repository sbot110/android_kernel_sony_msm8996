# MSM8996 HMSS CPR3 port staging area

Source: the user's working Sony Linux 4.4 tree, branch `Lineageos-21`
of `sbot110/android_kernel_sony_msm8996`. The files here preserve
the source verbatim as a baseline for porting, not as working 5.15 drivers.

## Imported source

- `cpr3-regulator.h`: controller, thread, regulator and fuse interfaces.
- `cpr3-util.c`: fuse-row extraction, interpolation, corner handling.
- `cpr3-regulator.c`: CPR3 controller and dual-thread aggregation.
- `cpr3-hmss-regulator.c`: MSM8996 HMSS fuse/corner/platform implementation.
- `reference/msm8996-regulator.dtsi`: original voltage and CPR node bindings.
- `reference/apm.c`, `reference/apm.h`: memory-array power mux dependency.
- `reference/msm-ldo-regulator.h`, `reference/spm.h`: legacy interfaces.

## Porting blockers (not yet resolved)

1. The original framework uses legacy regulator and OPP APIs; audit and
   adapt each call to Linux 5.15 without changing the electrical semantics.
2. Implement the original APM, MEM-ACC, SPM and CPU LDO dependencies using
   verified 5.15 interfaces; do not introduce stub implementations.
3. Translate the Sony 4.4 CPR DT bindings, resource addresses, fuse rows,
   thread mapping and regulator supplies into the existing 5.15 dora DTS.
   Do not blindly include the legacy reference DTS in the live DT.
4. Verify primary/redundant fuse layouts and speedbin/revision interpretation
   for F8131 and F8132, including MSM8996 variants.
5. Test regulator arbitration, voltage ordering, memory retention, suspend,
   failure unwinding and both Kryo clusters.
6. Build kernel and all four Dora DTBs, then validate on a device.

**Safety:** Nothing in this directory is wired into Kconfig/Makefile or
enabled in the live Device Tree. The MSM8996 CPUFreq fail-closed guard
must remain enabled until the complete hardware path is integrated and
validated. No claim of buildability or hardware readiness is made.
