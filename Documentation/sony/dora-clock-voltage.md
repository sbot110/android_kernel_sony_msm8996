# Native GCC/MMCC voltage contracts

The source of the hardware limits is Sony 4.4 commit
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`,
`drivers/clk/msm/clock-gcc-8996.c`, `clock-mmss-8996.c` and
`vdd-level-8996.h`. All four final Dora reference DTBs select GCC and MMCC
`*-8996-v3`. GCC therefore executes its shared v2/v3 fixup; MMCC executes
its v3 fixup, without inheriting the separate v2 fixup.

[dora-clock-voltage.json](dora-clock-voltage.json) records all 56 GCC and 49
MMCC contracts after those overrides, including source lines. Sparse entries
are preserved: zero denotes an unsupported level, not a zero-bandwidth vote.
Frequency limits are unchanged from the reference. In particular:

| Main output/source | LOWER | LOW | NOMINAL | HIGH |
| --- | ---: | ---: | ---: | ---: |
| GCC GPLL4 | — | — | 384 MHz | — |
| GCC SDCC1 apps | 19.2 MHz | 50 MHz | 400 MHz | — |
| GCC SDCC2 apps | 19.2 MHz | 50 MHz | 200 MHz | — |
| MMCC MDP | 171.43 MHz | 275 MHz | 330 MHz | 412.5 MHz |
| MMCC DSI byte 0/1 | 131.25 MHz | 210 MHz | 262.5 MHz | — |
| MMCC DSI pixel 0/1 | 175 MHz | 280 MHz | 350 MHz | — |
| MMCC MMPLL4 | 480 MHz | — | 960 MHz | — |
| MMCC MMPLL9 | 624 MHz | 624 MHz | 1248 MHz | 1248 MHz |

SDCC2 source entries 40 MHz and 80 MHz are restored exactly. SDCC1 keeps its
GPLL4 parent. The GPLL4 contract applies to the main postdivider output, not
the raw PLL VCO. Raw `*_EARLY` PLL numeric IDs are hidden on coupled boards;
the internal parent objects remain registered, so normal regulated child
rate propagation still works. No new PLL rate is programmed at probe.
GPLL0 additionally retains the source fixed 600 MHz VCO/main-divider state:
its native programming operations are removed, the observed main rate is
validated before provider publication, and the main root is critical so
unused-clock cleanup cannot gate the CPU/CBF auxiliary source.

`clk-msm8996-voltage.h` supplies real regulator consumers and CCF callbacks.
Source logical levels map to regulator corners 1/3/4/5/7, never microvolts.
Digital changes and repeated pre-operation checks call the typed RPM frontend
for actual ACTIVE and SLEEP ACKs. An increase is acknowledged before clock
programming; decreases and rollback use the CCF aggregate contract. MMPLL4
and MMPLL9 additionally require physical L12 at exactly 1.8 V.

The built-in provider first obtains an acknowledged HIGH lease. Its static,
bounded consumer/device lifetime survives deferred or failed probe without
releasing unknown boot clocks or multiplying enable counts. CCF registers
the source highest-corner handoff, accounts for firmware-active clocks and
only releases that lease after unused clocks are parked. An active clock
whose parent rate is unknown prevents lowering. Provider ownership is marked
only after all registration and child-population steps succeed.
A raw PLL still enabled without a prepared main-output owner also retains
HIGH. This conservative lease can remain after early cleanup; lower power
operation requires establishing that clock's ownership rather than assuming
the VCO is idle. A provider deferred beyond late init explicitly requests a
tracked HIGH handoff lease too. Only a complete ownership barrier may release
that late lease; release failures preserve the recorded vote for retry.

Stage 5 replaces the legacy L12 request path with the typed physical RPM
backend. A no-I/O readiness check precedes retaining consumer handles or
bootstrapping the class: a visible but not yet activated bank defers cleanly.
The source L12 boot proxy is requested explicitly as 1.8 V and 10,000 uA;
ordinary clock consumers have no steady load request. Every successful
analog transition and repeated pre-operation sync forces fresh UV/MA/SWEN
ACTIVE and SLEEP ACKs through the real regulator-core aggregates. A cached
enable count is insufficient. Requests processed by RPM are not measured
voltages or a proof of physical settling.

The temporary 10 mA lease is removed only with known clock ownership and
completed CCF handoff accounting. `clk_vdd_class_handoff_done()` takes the
prepare/list locks before the voltage-class lock is acquired; it does not
measure hardware activity. Both the MMCC late-init hook after CCF cleanup
and a provider completing late probe use this check. An unowned active PLL,
ignored unused-clock cleanup or failed handoff keeps the bootstrap lease.
Repeated release is idempotent under the class lock. Failed load release
restores the consumer's 10 mA request as well as retaining the lease flag:
this core caches a changed load even when the provider returns an error.
The original failure and quarantine remain visible.

A failed digital transition attempts to restore the last acknowledged
contract. Physical L12 failures latch the analog class and block subsequent
clock operations. The physical backend has a separate explicit recovery API
for a known BOTH baseline; replaying that request alone cannot reconcile a
CCF consumer count or clock ownership after a lost ACK, and does not clear
the analog latch automatically. Unknown initial transactions have no guessed
recovery baseline. No clock-level automatic recovery or gate removal is
claimed by this implementation.

The bounded built-in consumer lease survives provider deferral without
multiplying enable references. A different case remains deliberately sticky:
if partial clock registration unwinds the last class member while its HIGH
handoff is held, CCF marks that class orphaned and rejects re-registration.
There is no qualified same-owner reattachment API yet. Retaining power is
safe against a guessed voltage drop, but this is an availability limitation,
not successful deferred-probe recovery. See clk_vdd_class_deinit/init and
qcom_cc_really_probe. The read-only APCC/CBF observer additionally checks
stable source samples and resolves the original GPLL0/2 AUX leg; it still
provides no CPU power/rate transaction adapter.

The host checker executes the production helper with delivered/lost-ACK
failures and checks source maps against the manifest, including a rejected
map mutation. `--reference-commit` independently re-parses every contract and
final fixup from the saved Sony Git object. CCF ordering and late handoff,
and RPM transport/aggregation, have separate host
tests. These checks establish software contracts; they do not measure CX/L12,
prove eMMC/display runtime operation or authorize removal of hardware gates.
No synthetic NoC fmax table was added: original Sony NoC rate/bandwidth votes
remain the responsibility of the existing real RPM bus provider. DSI PHY
analog sequencing and GPU/CPU voltage domains remain separately gated.
