# Dora MSM8996 Linux 5.15: CPU power and frequency chain audit

Status: **BLOCKED; no safe CPR3 voltage/frequency transitions enabled**.
Scope: F8131/F8132, branch `kernel-5.15`. This audit is based on
source inspection; it is **not** a build or device validation report.

## Actual source chain

1. QFPROM at `0x74000` supplies the CPU speedbin NVMEM cell
   `speedbin@133` (bits 5..7) to `qcom-cpufreq-nvmem`.
   This cell does not represent the HMSS CPR3 per-corner calibration.
2. Kryo cluster OPP tables in `msm8996.dtsi` select frequencies by
   `opp-supported-hw`; their voltage calibration is not integrated.
3. APCC clock driver `clk-cpu-8996.c` provides CPU cluster clocks.
   CPUFreq is intentionally fail-closed for MSM8996/APQ8096.
4. PM8994 S11 is the shared `vdd_apcc` rail with Sony SAW3 linkage;
   this alone does not provide HMSS CPR3 corner aggregation.
5. Original CPR3 HMSS sources are preserved under
   `drivers/regulator/cpr3-port/`. The core, utility, and HMSS sources
   are declared as a disabled Kconfig build target (`depends on BROKEN`).
6. The `apcc_cpr` node has verified original register/IRQ resource
   addresses but is `status = "disabled"` and has no live voltage,
   APM, MEM-ACC, LDO, fuse-corner, or thread regulator graph.

## Concrete API blockers

| Dependency | Legacy implementation | Linux 5.15 integration status |
| --- | --- | --- |
| CPR3 core and HMSS | cpr3-regulator.c, cpr3-util.c, cpr3-hmss-regulator.c | Staged; not compiled; regulator and OPP APIs not reconciled |
| APM | drivers/power/qcom/apm.c, linux/power/qcom/apm.h | Staged as reference; header absent from live include tree |
| MEM-ACC | mem-acc-regulator.c | Staged; legacy SCM `scm_call` / `scm_call2` not ported |
| SPM | drivers/soc/qcom/spm.c, spm-regulator.c | Staged; `soc/qcom/spm.h` absent from live include tree |
| Kryo LDO | kryo-regulator.c, msm-ldo-regulator.h | Staged; regulator header absent from live include tree |
| SAW3 | qcom_spmi-regulator.c | Present; not yet reconciled with CPR3 ownership |
| CPR3 fuse calibration | legacy fuse-row read and per-corner decode | Staged; no active HMSS fuse provider |
| CPU OPP/clock transitions | cpufreq-nvmem, APCC clock | Intentionally blocked pending voltage sequencing |

## Electrical ordering requirements

On frequency increase: resolve both CPR thread corner votes, prepare
MEM-ACC/APM and associated retention/LDO requirements, raise the
aggregated shared rail to the required voltage, wait for regulator
acknowledgement, then increase the CPU clock. On decrease: lower the
clock first, then relax shared-rail and memory votes only after both
threads permit it. This is an invariant to validate against the Sony
driver, **not** a claim that the sequence currently executes.

Failures in any vote or acknowledgement must leave the CPU clock at
a safe frequency and must not silently lower the rail. Verify
suspend/resume, hotplug, aging calibration, speedbin variations,
partial binning, and fallback paths before enabling the controller.

## Release gates

- Port the original APM, MEM-ACC, SPM and Kryo LDO code to real
  5.15 APIs (no stubs), including secure monitor calls and bindings.
- Reconcile CPR3 regulator registration, corner transitions, and
  shared SAW3 ownership; implement real two-thread aggregation.
- Translate the original regulator DT graph with validated phandles,
  register sizes, QFPROM layouts, interrupt and clock dependencies.
- Compile all enabled drivers, link the kernel, and build every Dora
  DTB; run dtc/dt-schema checks.
- Validate measured voltage/clock order and both CPU clusters on
  physical F8131/F8132 hardware.

**Do not** remove `depends on BROKEN`, enable `apcc_cpr`, add
unverified `opp-microvolt` values, or lift the CPUFreq guard before
all gates pass.

## Linux 5.15 API equivalence investigation

The live kernel exports `qcom_scm_io_readl(phys_addr_t, unsigned int *)`
and `qcom_scm_io_writel(phys_addr_t, unsigned int)` from
`drivers/firmware/qcom_scm.c`. These are **not** drop-in equivalents
for the Sony MEM-ACC driver's `SCM_SVC_FUSE/SCM_FUSE_READ` command:
the original request takes a fuse-row address and address type and
returns two data words plus a secure status. The modern IO service
uses a different command and single 32-bit result. Replacing it with
two IO reads would change the TrustZone protocol and is not justified.

The original MEM-ACC function `mem_acc_read_efuse_row()` returns
`u64`, even on secure-read failure, so it cannot propagate an errno
to its callers without changing the function signature and call sites.
Port this interface to an `int` status plus `u64 *` result, audit all
callers, and reject invalid or unavailable secure fuse reads before
registering a voltage regulator. Never interpret a failed read as a
valid zero fuse.

The Sony APM source has real MSM8996 switch sequences. Its staged
copy now rejects unsupported SoCs and NULL controller pointers instead
of claiming a successful switch. This is not yet a buildable port.
The modern SPM/SAW3 interface and regulator ownership still need
explicit reconciliation: do not issue concurrent voltage votes through
both legacy SPM and the modern PM8994 regulator without verified
arbitration.

No live DT supply links, CPR3 activation, CPUFreq unlock or voltage
values were changed in this investigation.
