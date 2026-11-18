# SDHC1 source activation qualification

Source audit complete, 2026-11-04, against the shared Stage5 tree on
`kernel-4.19-701905467803253630`; Sony reference is final commit
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`, including all four final Dora DTBs.
Validated kernel/config/check code: `85850877ef5d1481a89df71ac9af3b74c7d65e40`.
The audit covers the MMC fault closure, RPM PM phases and explicit clock
supplier links. Device/vendor repositories and physical devices were not modified.

**Current decision: source-qualified for a separate experimental SDHC1-only
DT artifact, with clean-build and exact compiled-DT results recorded in
[dora-build-validation.txt](dora-build-validation.txt).** The confirmed clock/IRQ error-chain and PM ordering
gaps below are repaired and their focused tests pass. Absence of a hardware
test is not a source blocker. No new regulator input, GDSC, reset or PHY is
inferred. The experimental artifact must remain separate from the four
default DTBs and keep SDHC2/UFS disabled.

## Conditions checked

| Condition | Source evidence and current result |
| --- | --- |
| Final DT identity/phandles | `msm8996-sony-xperia-tone-storage.dtsi:25`: SDHC1 `/soc/sdhci@7464900`, aliases `sdhc1`/`mmc0`, 8-bit nonremovable; SDHC2 is separate `74a4900.sdhci`/`mmc1`. The four final Sony DTBs and `dora-storage-dt.json` agree. Fresh four-variant storage/clock/physical/golden checks exited 0 in `stage5-dt-out`; the final clean-build evidence is recorded separately. No board/model-to-PMIC guess is introduced. |
| Registers/IRQ identity | HC `0x7464900/0x500`, core `0x7464000/0x800`, optional CQ `0x7464e00/0x19c`; SPI141 HC and SPI134 power, names `hc_irq`,`pwr_irq`, equal final Sony data. `sdhci_pltfm_init()` maps resource0 and propagates HC IRQ errors. `sdhci_msm_get_irqs()` resolves the mandatory power IRQ and propagates declared supplier/defer failures. Generic-compatible legacy queue operation does not require CQ/ICE-crypto phandles to enumerate the eMMC. |
| Physical regulator ownership | VDD=L20 2.95V, HPM570000uA/LPM200uA; IO=S4 1.8V, HPM325000uA/LPM110uA, client always-on/no LPM support. `tone-rpm-physical.dtsi` preserves real phandles, uses BOTH opt-in and explicit RPM-managed input, and removes the incompatible inherited S5 input edge. Final Sony DT plus its regulator driver use no Linux input edge for these resources; firmware RPM owns that dependency. Native/physical/corner users share tuple claims. The dedicated SDHC flag requires both typed providers. |
| ACK integration | `sdhci_msm_vreg_init_reg()` checks typed readiness before bus/clock operations. Enable records the consumer reference before forced `sync(true)`, so lost ACK retains ownership. Enabled voltage/load changes force aggregate replay; `power_sync()` checks disabled readiness and freshly ACKs enabled rails before clock/NoC entry, including controller-clock already-on paths. Host quarantine survives external provider recovery. Runtime-resume ungate-skip also checks power_sync. `check-mmc-physical.py` verifies wrong/retired/suspended/quarantined providers and preserves a live second-rail reference when its lost ACK causes rollback rejection; normal/selftest and ASan/UBSan exit0. |
| Clock IDs/rates | DT names iface/core/ICE resolve to native GCC SDCC1 AHB/APPS/ICE branches. `gcc-msm8996.c:408-451` retains the Sony plan (apps includes400k/20/25/50/96/192/384MHz; ICE19.2/150/300MHz). `1460-1501` branches use SET_RATE_PARENT. No400MHz card rate is invented:400MHz is a CX ceiling,384MHz remains the DDR root target. |
| CX/GPLL4 | Final Sony `clock-gcc-8996.c:188-201,1373-1418,3644` gives fixed GPLL4=384MHz/NOMINAL and final v3 SDCC1 LOW ceiling50MHz. Current GCC `228-253,3493,3540-3541` matches. `clk-msm8996-voltage.h:69-169` ACKs typed CX and preserves safe rollback/quarantine; `199-252,316-353` acquires HIGH for inherited unknown boot state. GCC probe `3595-3619,3645-3669` bootstraps that lease before publication and hides raw PLL bypass IDs. CCF `clk.c:2486-2542,2717-2787,2897-2959` votes before hardware and retains a safe lease after hardware failure; boot handoff adoption/drop is at4666-4695/1790-1825/972-999. The added explicit MMC→GCC PM link closes the previously missing branch dependency; see the exact ordering proof below. |
| Reset/power domains/PHY | Both final Sony DT and restored node have no required reset or power-domains property. Native GCC exposes SDCC1 BCR at0x13000, but driver reset is optional and propagates errors; adding a reset phandle would change the known sequence without source evidence. GCC GDSC table contains no SDCC GDSC. DLL/tuning/HS400 calibration is inside the SDHCI driver, so no missing generic MMC PHY is inferred. |
| Pinctrl | Final active/sleep drive+bias values are kept for sdc1_clk/cmd/data/rclk. Native TLMM groups at `pinctrl-msm8996.c:1890-1896` map the same control block0x12c000. `sdhci_msm_parse_pinctrl_info()` requires active/sleep; optional drive-rate states may be absent as in the source board. Declared pinctrl errors and EPROBE_DEFER propagate instead of falling back silently. Runtime pin failure now preserves the first IRQ error, masks the power IRQ, suppresses pad/cache publication and blocks requests. |
| NoC/bus | Nine Sony vectors, master78→EBI512, preserved in the SDHC1 node. `sdhci_msm_bus_register()` validates vector count/order and submits initial index0 instead of trusting a cache. `msm_bus_scale_client_init()` resolves the explicit ad_hoc provider, adds the device link and defers until graph callbacks publish. `msm_bus_device_probe()` publishes the complete graph only after RPM/clock/QoS initialization succeeds. Bus request failures already quarantine the host; no fake clock/no-op bus stub replaces the route. |
| Probe/defer/unwind | `sdhci_msm_probe()` rejects wrong bootdevice/disabled slot before touching shared clocks/rails; exact-token bootdevice parsing matches `7464900.sdhci`. It resolves all declared clocks, IRQs, typed rails and bus provider before clock enable/reset MMIO. Initial apps rate is set to400k before enable, matching Sony4.4 line4611, under the GCC HIGH handoff lease. Optional declared suppliers preserve real error/defer codes. IRQ/work cleanup precedes clock/host free. No additional supplier/probe source blocker found. |
| Runtime/system IRQ reentry | Runtime suspend disables host/power IRQ before controller clock gating. Resume checks sticky faults/typed ACKs before IRQ reenable, including no-ungate path. Clear/pin/wait/restore/DLL failures now feed the sticky fault; an uncleared PWRCTL is masked and no IRQ reenable follows failed restore. Native physical and CX corner banks now replay in resume_early, with actual parent/supplier dependencies ahead of SDHC. Both source closures are covered by production extraction tests below. |
| Shutdown/reboot | `mmc_bus_shutdown()` (`core/bus.c:130`) suspends block queues through `mmc_blk_shutdown()` (`block.c:3106`) then invokes MMC bus shutdown. `mmc_shutdown()` (`mmc.c:2793`) resumes if required, stops scaling and sends PON via `mmc_send_pon()`. Sony4.4 has the same PON path (`mmc.c:3009`) and no extra SDHCI platform shutdown callback. Failed power/clock requests now block request/CQE dispatch. Faulted set_ios can stop SDCLK while its controller is accessible, then returns before timing/reset MMIO and retains uncertain rails. Repeated cleanup after runtime gating avoids all gated-controller MMIO, including the auxiliary-clocks-only state. Hardware flush/PON/reboot success remains unmeasured. |

## Confirmed source gaps closed

1. **Void clock callback hid rate/CX failure.** Current
   `sdhci_msm_set_clock()` previously returned on failed `clk_set_rate()` after vendor
   timing/DLL writes; generic `sdhci_set_ios()` then unconditionally published
   `host->clock=ios->clock`. The host's existing error source covered bus/rails,
   not this rate failure. Root assigned the MMC agent a sticky host clock fault,
   stopped card clock, request rejection and cache-publication guard, with an
   actual-production fault test. The final code confirms fallible clock/NoC/pin
   prerequisites before timing/DLL publication, latches rate/DLL/SDCLK failures,
   and requires both non-ES DLL lock bits. Generic request/send/CQE paths reject
   the sticky fault. All three set_ios void-clock calls check it before later
   publication/MMIO. Clock stop precedes source gating and the already-gated
   path performs no controller MMIO. The new clock/core test passes19 negative
   mutations plus ASan/UBSan; the provider test retains its unwind cases and
   adds sticky-retry rejection plus auxiliary-only clock cleanup.
   This is pre-existing source behavior, not a claim that the working Sony
   happy path failed on hardware.

2. **IRQ/pad failures could escape that guard.** `sdhci_msm_pwr_irq()` ignored
   `sdhci_msm_clear_pwrctl_status()` errors; pinctrl failure only set BUS_FAIL.
   `sdhci_msm_check_power_status()` timed out with WARN/log then returned void.
   `sdhci_msm_registers_restore()` also ignored a failed status clear. None of
   these alone necessarily set the bus/rail latch, so a request guard could
   still see success. Final code checks the clear result, immediately exits on
   the first BUS/IO failure, drops all accumulated SUCCESS bits, publishes no
   power/IO cache and performs no pad write after a host fault. Timeout, restore
   clear, DLL-init and DLL-phase failures all latch the same fault. It stops
   SDCLK and masks PWRCTL; restore only reinstates its mask after all checks,
   and runtime_resume returns before GIC IRQ enable on failure. The probe's
   stale reset-event drain now uses the verified retry helper before ACK/GIC.
   `check-mmc-irq-faults.py --self-test` executes these actual functions and
   rejects8 first-error/clear/pad/cache/mask/DLL/timeout mutations (exit0).

3. **Physical-bank normal resume is later than host resume_early.** SDHC uses
   SET_LATE_SYSTEM_SLEEP_PM_OPS (`sdhci-msm.c`, late suspend/early resume), while
   native regulator bank previously used SET_SYSTEM_SLEEP_PM_OPS; the CX corner
   bank had the same mismatch.
   The bank marks every physical rail suspended on successful suspend;
   `rpm_physical_ready()` returns -EBUSY until bank resume. A host that was not
   runtime-suspended enters resume_early, calls power_sync, receives -EBUSY and
   would permanently quarantine before bank normal resume ran. Device links order
   devices inside a PM phase, not the global early-vs-normal phases. Required:
   bank replay/readiness before host resume_early, with supplier ordering.
   Both banks now use SET_LATE_SYSTEM_SLEEP_PM_OPS; no sleeping ACK operation
   was moved into NOIRQ. The physical backend commits all resources' suspended
   flags only after the entire bank replay succeeds; a failed replay leaves
   them frozen and does not clear quarantine. The corner callback likewise
   clears suspended only after successful replay.
   `include/linux/pm.h:317-337` and `drivers/base/power/main.c:903-961,1208-1217`
   show the phase distinction. The dependency closure is explicit below.

## PM supplier ordering proof

Clock lookup in `drivers/clk/clkdev.c:31-46,194-212` does not add device links.
DT/probe order therefore was insufficient to order SDHC against GCC/CX.
`sdhci_msm_link_clock_providers()` now runs before any clock acquisition or
hardware activation, gated by `sony,require-rpm-physical-acks`. It parses every
declared clocks entry; all three SDHC1 entries in these four DTBs refer to the
native GCC platform device. Missing/unbound provider gives EPROBE_DEFER,
malformed phandles/empty clocks fail, and allocation/link failure fails probe.
OF/device references are balanced under the supplier device lock. Managed
AUTOREMOVE_CONSUMER links clean up with a failed probe or consumer removal;
repeated phandles reuse the same device link.

| Consumer→supplier edge | Actual source |
| --- | --- |
| SDHC→physical rdev | `regulator_get()`, `drivers/regulator/core.c:1866`, STATELESS; these links participate in PM waits. |
| physical rdev→native bank | `regulator_register()`, `core.c:4894`, rdev parent. |
| SDHC→GCC | new helper `sdhci-msm.c:5844`, AUTO_REMOVE_CONSUMER, invoked at6080 before get_clocks. |
| GCC→CX rdev | real `regulator_get(dev,digital)` in `clk-msm8996-voltage.h:212`, same regulator-core STATELESS link. |
| CX rdev→corner bank | real regulator-core rdev parent assignment. |
| native/corner bank→RPM transport | `qcom_smd_rpm_get()`, `drivers/soc/qcom/smd-rpm.c:215`, managed consumer link. |

`device_link_add()` calls actual `device_reorder_to_tail()`
(`drivers/base/core.c:196,461`), recursively placing consumers/children after
suppliers. `device_resume_early()` invokes `dpm_wait_for_superior()`
(`drivers/base/power/main.c:869`); it waits for the parent and then all
non-dormant supplier links (`255-302`), including STATELESS links with state
NONE. Thus the matching early phase has both chains:
RPM→native bank→physical rdev→SDHC and
RPM→corner bank→CX rdev→GCC→SDHC. Reverse device order suspends consumers first.
The regulator class has only normal suspend/resume hooks, no competing early
hook; native/corner ops have no regulator-core per-rdev resume hook. The NoC
and RPM transport providers introduce no separate software suspended flag or
later normal-phase replay callback in these paths.

`check-rpm-pm-order.py` compiles the real PM macros/tables, core event
selectors/waits/reordering, native wrappers, corner PM callbacks and MMC
provider helper. With modeled object/OF/transport plumbing it executes suspend,
freeze/thaw, poweroff/restore and recover events. Reverting either bank to
normal PM reproduces the host's early busy fault and fails the test. It also
checks missing/unbound providers, malformed/partial lookup, failed links and
reference balance. All7 phase/link/wait/ref mutations are rejected; normal and
ASan/UBSan selftests exit0. This is a source/model ordering proof, not a phone
suspend or live ACK test.

## Ownership and lifetime limits

The qualified native provider is built in. Its whole-bank module preflight
rejects opt-in children before any legacy sibling can register or vote.
The five opt-in physical resources use the source-qualified RPM-managed-input
mode, with no Linux parent supply edge. Retired supplied legacy native rdevs
are deliberately retained inert in the built-in configuration: immutable
supply declarations cover unresolved-parent races and a stable tuple tombstone
prevents repeated registration growth or a reprobe reset. This is bounded
retention, not a claim that every supplied native object is freed. Untouched
parentless rdevs can free after their final consumer reference; a failed
registration after a bootstrap write preserves its tuple guard. The preexisting
generic regulator-core failure path after Linux supply resolution is outside
the native host model, and generic module-provider hot removal with legacy
parents is outside this built-in Dora activation qualification.

## Validation at source freeze

- Final cross-compile of `sdhci-msm.o`, `sdhci.o`, `cqhci.o`,
  `qcom_smd-regulator.o`, `qcom-rpm-corner.o`: exit0, no diagnostics;
  `/workspace/work/stage5/mmc-pm-final-build.log`.
- Existing `check-mmc-power.py` and extended `check-mmc-providers.py`: exit0.
- `check-mmc-physical.py --self-test`: normal/ASan/UBSan exit0.
- `check-mmc-clock-faults.py --self-test`: exit0,19 rejected mutations;
  `--sanitize`: exit0 (DT agent's frozen test artifact).
- `check-mmc-irq-faults.py --self-test`: normal/ASan/UBSan exit0,8 rejected mutations.
- `check-rpm-pm-order.py --self-test`: normal/ASan/UBSan exit0,7 rejected mutations.
- Four default compiled-tree clock/storage/physical/golden checks exited 0.
  The clean build and isolated diagnostic comparator also completed with 0;
  their separate evidence is recorded in dora-build-validation.txt.

Logs use `/workspace/work/stage5/{mmc-physical-final,mmc-irq-final,rpm-pm-order}`
with `.log` and `-sanitized.log`, plus the existing clock-fault test logs.

## Boot pending-status interpretation

The probe preserves the successful stale PWRCTL drain/acknowledgement
sequence from Sony4.4 `sdhci-msm.c:4726-4753`, adding bounded clear retries
and fault propagation before IRQ registration. It drains a
controller-reset event before IRQ registration; it is not a typed PMIC ACK or
the Linux consumer's claimed baseline. It does not by itself prove an unsafe
handoff. Linux first calls MMC_POWER_UP with card clock off, powers the card
through the IRQ path, delays, then requests f_init/MMC_POWER_ON
(`mmc_power_up()`, `core.c:2596-2626`; `sdhci_set_ios()` POWER_UP path). Fresh
typed rail ACKs must succeed before actual card-clock/command enable. Do not
replace this original controller event drain with invented inherited regulator
state. A failed clear still must be handled by the common IRQ fault closure.

## Precise next action

The requested source matrix is qualified after the bounded closures above.
The final clean build, physical/corner, CCF, bus and compiled-DT checks
are recorded in the build report. The optional workflow builds the
**separate experimental** SDHC1-only DT artifact:

- change only the experimental controller status to SDHC1 okay;
- leave SDHC2, UFS host/PHY, display and unrelated activation gates unchanged;
- retain aliases,8-bit rails/pins, original frequency plan, physical-ACK flag,
  bus vectors and all four variant-specific source data;
- run exact compiled-DT checks (allowing only that intentional status delta),
  then build the experimental artifact separately from the default four DTBs.

Do not require a prior phone boot to establish these source conditions. The
artifact remains experimental because enumeration, data integrity, tuning,
encryption, cold/warm reboot and real suspend are device-validation outcomes,
not results established by source review or an RPM protocol ACK.
