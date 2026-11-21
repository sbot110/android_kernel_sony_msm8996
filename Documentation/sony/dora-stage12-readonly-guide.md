# Stage 12: read-only WSL preflight

Stage 12 does not authorize a hardware boot by accepting risk. The current
Stage 11 Sony S1/RAM, CPU, watchdog, partition tail/write extent and failure-time
diagnostic contracts are unqualified. The shipped preflight therefore returns
**STOP**, even when every host artifact check passes. It cannot flash, perform
`fastboot boot`, reboot, mount, change root privileges or write a phone partition.
There are no force/approve/risk-acceptance switches. Host test PASS is separate
from the preflight decision.

The user previously confirmed that LOS 21 / Sony 4.4 boots and that TWRP was
recently checked. The original statement date is retained; a later tool run
does not make it a fresh check or measured ADB output. The earlier Stage 65
restore alone is not used as current evidence.

## Files and invocation

The portable script is
`scripts/dora/dora-stage12-hardware-preflight.sh`. It contains its independent
Python parser and needs bash/Python 3.8+ standard library. Both F8131 and F8132 use
the four pinned dora DTBs; no PMIC/revision is selected from the SIM model.

Default mode reads regular host files into memory and emits JSON on stdout.
It does not invoke ADB/fastboot. A reviewed AOSP unpacker, if supplied or next
to the script, creates private temporary HOST files and compares extracted
kernel/ramdisk bytes before deleting those temporary files. Input files remain
unchanged. `--output-dir` explicitly creates a NEW private host directory; an
existing directory is refused. Keep runtime captures/full boot reads private.

The kit includes the pinned official AOSP14 `unpack_bootimg.py`, original
Stage 11 `decode-boot-trace.py`, exact `System.map`, and the current user
confirmation. The images and full stable64M backup are supplied separately.

On the user's connected WSL host, first run host checks, using the actual
directory holding the two Stage 11 images. `/mnt/d/stage11-images` below is an
example HOST path, not a partition:

```sh
bash ./dora-stage12-hardware-preflight.sh \
  --artifacts-dir /mnt/d/stage11-images \
  --backup /mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img \
  --aosp-unpacker ./unpack_bootimg.py \
  --system-map ./System.map \
  --operator-state ./operator-confirmation.json
```

Exit codes are 0=PASS, 1=WARN, 2=STOP. A STOP here is expected. Missing file paths
are reported precisely; no input is guessed or reconstructed. Do not turn a
WARN/STOP into permission to run Test A. A future PASS would still require a
separate explicit user authorization and a concrete reviewed test/restore plan.

Technical review of real evidence and a corresponding source revision are
needed to qualify the missing contracts. Readiness concerns a safely bounded
experiment; successful execution of the new kernel remains the objective of
Test A. A safe read-only CPU test contract is assessed separately from full
native DVFS/six-lease support.

## Optional current-reference capture

Use the already available ADB connection to the CURRENT working Android 4.4.
Do not reboot or enter recovery just to run preflight. Enumerating attached
devices is a read-only identification step performed by the user:

```sh
adb devices -l
```

If platform-tools is a Windows installation, use `adb.exe` and pass its existing
executable path with `--adb`. Substitute the exact dora serial for `SERIAL`:

```sh
bash ./dora-stage12-hardware-preflight.sh \
  --artifacts-dir /mnt/d/stage11-images \
  --backup /mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img \
  --aosp-unpacker ./unpack_bootimg.py \
  --system-map ./System.map \
  --operator-state ./operator-confirmation.json \
  --collect-adb SERIAL --output-dir ./stage12-reference-capture-01
```

This explicitly opts in to fixed read-only commands: device/model, uname,
boot_completed/Lineage/bootloader/boot reason; canonical boot alias; sysfs
PARTNAME and 512-byte sector count; pstore archive, cmdline, FDT, dmesg, iomem,
interrupts and volatile trace if readable. A missing/unreadable capture is
reported as such. The script never invokes `adb root`, remount, recovery,
fastboot or a shell command supplied by the caller.

The collector checks `/proc/sys/kernel/random/boot_id` before and after the
sequence. A disconnect/failure preserves successful fields and a
`PARTIAL/REJECTED` status in `device-state.json`; a different boot ID rejects the
combined capture. Every successful field is checkpointed with atomic host-file
replacement. Retain partial evidence for review; it is not a complete capture.

Both stdout and stderr are limited while each ADB process is running, with a
per-command timeout. The limits are 64 KiB for metadata/stderr, 8 MiB for the
pstore tar, 16 MiB for other logs, and exactly the admitted partition capacity
for an optional full boot read. Over-limit processes are terminated on the
host; no phone reset is issued. Explicit `--adb ./adb.exe` paths are resolved
before use instead of selecting another executable from PATH.

`exec-out` transport success alone does not prove that remote `cat`/`tar`
succeeded. The collector validates tar framing/headers, FDT structure, and
trace size/anchor/magic/version/sequence/CRC before saving them as artifacts.
Remote error text with host exit 0 is reported as unavailable/invalid evidence.
An empty valid pstore archive still does not prove absence of kernel execution.

The target must resolve to internal `/dev/block/mmcblk0pN`, sysfs must identify
**PARTNAME=boot**, and capacity must equal the 67,108,864-byte known backup extent.
FOTAKernel/other partitions and a wrong phone/size are rejected before a raw
partition read. No partition number is guessed from the example host tests.

Only if ADB is ALREADY root and a full read is desired, repeat into a different
fresh host directory with `--read-boot` added. This performs `blockdev --getsize64`
and `exec-out cat` of the validated boot target, then checks the exact read
length. It never executes `dd`. An unavailable privilege/short read leaves
successful metadata captures intact but does not produce a usable boot dump.
The script does not obtain privilege or switch device modes for this step.

For reviewing an existing capture without reconnecting:

```sh
bash ./dora-stage12-hardware-preflight.sh \
  --artifacts-dir /mnt/d/stage11-images \
  --backup /mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img \
  --aosp-unpacker ./unpack_bootimg.py \
  --operator-state ./operator-confirmation.json \
  --device-state ./stage12-reference-capture-02/device-state.json \
  --boot-dump ./stage12-reference-capture-02/current-boot-readonly.img
```

Imported JSON is historical reported evidence, not a current connection,
attestation or hardware qualification grant. Its original timestamp and age
are reported; importing it never refreshes the runtime/capacity observation.
Legacy captures without same-boot checkpoints remain historical. Incomplete,
future-dated or inconsistent v2 captures are rejected. The measured Sony S1
version is compared separately with the reference instead of assumed from it.
Rerunning the tool also does not refresh the date of the user's TWRP statement.
The dump must match its recorded SHA256. Return `preflight.json`/JSON
stdout for analysis; retain raw dumps/cmdline/device identifiers privately.

## Tail and temporary-boot decisions

A correct 11,051,008-byte file says nothing about bytes beyond its new payload
in the actual boot partition. The optional `--reference` only illustrates the
tail of a regular reference FILE and is never called a current partition read.
The preflight can measure the current full read's tail but cannot establish
Sony S1's read boundary or a writer's semantics from it. A zero tail does not
automatically qualify short writes; a nonzero tail does not automatically
authorize erasing/zeroing the rest of the partition. Neither a padded image nor
a partition-writing command is generated.

`fastboot boot` is not qualified for this Sony S1 and is not invoked. It may
use a different loading/relocation path from a physical boot partition; a
capability claim or a successful host unpack is insufficient to equate them.

## Diagnostics and next gate

Do not initiate A or B while STOP remains. Test A must precede B; B additionally
needs analysis of A, fresh gates and new explicit authorization. If a future
authorized A fails, record A2/A3/A4/A5 observations without inventing a reached
kernel stage or watchdog cause. If it succeeds, require 4.19 `uname -r` and
`sys.boot_completed=1`, not a Sony logo or merely an `/init` exec milestone.

Do not enter ordinary TWRP solely to fetch volatile evidence. It previously
damaged 0xa7fff000 inside ramoops `[0xa7f00000,0xa8000000)`. Empty pstore does not
prove no kernel execution. The Stage 11 trace is volatile, not a reboot log.
A verified failure-time transport or reader that preserves the same reservation
and reads before any ramoops/pmsg overwrite is still required. No alternative
physical breadcrumb region has been selected.

If a valid live `/proc/dora_boot_trace` capture becomes available, preserve its
2048 bytes before a reset/recovery boot. Verify the bundled System.map SHA256
`3bcdc40ee664d02f0329cede550d1d25058273fdcafd7389fee587720bd2979c` and use the
original decoder:

```sh
python3 ./decode-boot-trace.py ./dora_boot_trace.bin
```

The decoder checks magic/version/CRC/sequence. Match addresses only to that
exact map/build; absent/invalid records do not prove failure before entry.
ADB on stable 4.4 alone is not a failure-time transport for an unbooted 4.19.

No partition-changing test or recovery command is issued at this STOP stage.
Before any future boot restore, the exact device, boot target, extent, verified
backup SHA, implications and separate permission must be reviewed. Afterwards
both full partition readback SHA and actual Android 4.4 boot must be confirmed.
