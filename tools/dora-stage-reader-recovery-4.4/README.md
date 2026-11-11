# Dora TWRP 4.4 early boot stage reader — SOURCE ONLY

This out-of-tree kernel module is UNBUILT, UNTESTED, NOT INSTALLED.
It reads one fixed reserved 4KiB page, 0xa7fff000, ONCE on insmod,
then exports a root-only /proc/dora_stage_raw binary snapshot.

## Memory reservation and limitations

The experimental 5.15 device tree reserves 0xa7fff000..0xa7ffffff
separately for early stage markers. The working LineageOS 21 Linux 4.4
device tree instead gives ALL of 0xa7f00000..0xa7ffffff to ramoops
and specifies a 128KiB pmsg zone. The recovery can therefore overwrite
previous markers BEFORE this module is inserted. Empty/missing markers
are inconclusive. The mirrored slots at +0xf00 are a best-effort fallback.

The module uses ioremap_wc and memcpy_fromio to take a read-only snapshot.
It DOES NOT change the diagnostic RAM bytes, DT, or disk partitions.
Loading a kernel module still changes live kernel state and could crash
if the ABI, symbols, or memory mappings are unsuitable.

## Mandatory compatibility audit before building or inserting

Use only the exact kernel source, config and generated symbols for the
RUNNING TWRP Linux 4.4.302+ kernel. The user currently has uncommitted
modifications in their local Linux 4.4 source; git HEAD alone is not an
accurate build identity.

Check adb shell uname -r. Collect exact running /proc/config.gz. Confirm
the kernel output directory's kernelrelease matches. If
CONFIG_MODVERSIONS=y, a matching Module.symvers from a complete build
is mandatory. Running modules_prepare alone is not sufficient.
Check CONFIG_MODULE_SIG_FORCE before attempting loading.

Typical build, AFTER verifying paths, config, release and symbol CRCs:

    K44=$HOME/android/lineage/kernel/sony/msm8996
    OUT=/path/to/confirmed/exact/working/kernel/output
    MOD=/path/to/tools/dora-stage-reader-recovery-4.4
    test -s "$OUT/Module.symvers" || exit 1
    make -s -C "$K44" O="$OUT" ARCH=arm64 kernelrelease
    make -C "$K44" O="$OUT" ARCH=arm64 CROSS_COMPILE="$CROSS" M="$MOD" modules

After ABI check and successful build, test ONLY from disposable TWRP 4.4:

    adb push dora_stage_reader.ko /tmp/dora_stage_reader.ko
    adb shell insmod /tmp/dora_stage_reader.ko
    adb exec-out cat /proc/dora_stage_raw > dora-stage.bin
    adb shell rmmod dora_stage_reader

The output must be exactly 4096 bytes. Decode with
scripts/dora_decode_early_markers.py from kernel-5.15.

Before attributing missing markers to a Linux 5.15 hang, ensure the
tested 5.15 kernel image actually contains the Stage 1..7 writes:
some previous experimental builds deliberately disabled them.
