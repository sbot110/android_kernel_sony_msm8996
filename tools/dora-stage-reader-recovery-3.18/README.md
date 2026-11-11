# Recovery 3.18 read-only stage reader (experimental)

This **out-of-tree kernel module** is for the Sony Xperia X Performance
(dora, F8131/F8132) running the existing TWRP 3.18.71-sjll recovery.
It is **not** built against Linux 5.15. No firmware, partition, device tree,
recovery image or kernel boot settings are modified by the module.

The precise recovery kernel commit reported by uname as
`gc3bcf7e2f4cb` is:
`sjllls/android_kernel_sony_msm8996@c3bcf7e2f4cba5b4166a9c143e2691311bbcae9d`.
The original `dora_defconfig` specifies `CONFIG_LOCALVERSION="-sjll"`,
`CONFIG_LOCALVERSION_AUTO=y`, `CONFIG_MODVERSIONS=y`,
`CONFIG_MODULE_SIG=y`, `CONFIG_MODULE_SIG_FORCE=n`.

Before building, obtain the **exact running recovery config** from
`/proc/config.gz` and compare the build's `make -s kernelrelease` with
`adb shell uname -r`. With `CONFIG_MODVERSIONS=y`, **modules_prepare
alone is insufficient**: a compatible, complete kernel build and
`Module.symvers` are needed. The same kernel source commit does not
guarantee matching symbol CRCs without matching config and build options.

Suggested out-of-tree build (set these paths and the toolchain yourself):

```sh
REC=$HOME/android/kernel-work/sony-msm8996-twrp-3.18
ROUT=$HOME/android/kernel-work/out-twrp-3.18
MOD=$HOME/android/kernel-work/sony-msm8996-5.15-ad3dcab-test/tools/dora-stage-reader-recovery-3.18

# After checking out commit c3bcf7e2f4cba5b4166a9c143e2691311bbcae9d:
mkdir -p "$ROUT"
gzip -dc /tmp/dora-twrp-config.gz > "$ROUT/.config"
# You must establish appropriate CROSS_COMPILE and kernelrelease.
make -C "$REC" O="$ROUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" olddefconfig
make -s -C "$REC" O="$ROUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" kernelrelease
# Do not install a module if kernelrelease differs from the TWRP uname -r.
make -C "$REC" O="$ROUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" -j8 Image modules
test -s "$ROUT/Module.symvers"
make -C "$REC" O="$ROUT" ARCH=arm64 CROSS_COMPILE="$CROSS_COMPILE" M="$MOD" modules
```

When the exact kernel release and ABI are confirmed, an initial module
load test can be done under recovery with:

```sh
ADB=/mnt/c/platform-tools/adb.exe
"$ADB" push "$MOD/dora_stage_reader.ko" /tmp/dora_stage_reader.ko
"$ADB" shell "insmod /tmp/dora_stage_reader.ko"
"$ADB" shell "ls -l /proc/dora_stage_raw"
"$ADB" exec-out "cat /proc/dora_stage_raw" > /tmp/dora-stage.bin
wc -c /tmp/dora-stage.bin # must be 4096
"$ADB" shell "rmmod dora_stage_reader"
```

Use `scripts/dora_decode_early_markers.py` from Linux 5.15 branch to
decode the raw dump after a controlled 5.15 test boot. A module load or
read can still fail due to kernel security checks, mismatched module
versions, missing exported symbols, or incompatible memory mappings.
Read-only ioremap is not guaranteed harmless on every platform; do not
continue if the recovery reports warnings or faults.

## Memory layout

Recovery ramoops occupies 0xa7f00000..0xa7ffffff (1 MiB), with
console 0x40000, ftrace 0x1000, and pmsg 0x1000. Its pmsg zone is the
final 4 KiB page, `0xa7fff000..0xa7ffffff`. Recovery can reset the pmsg
header and overwrite old contents when it initializes. The Linux 5.15
breadcrumbs use a primary table at +0x000 and a mirrored table at
+0xf00. The mirror may survive, but is **not guaranteed** to survive
recovery's pmsg operations.

Never write to the physical page or to the pstore files as part of
the diagnostic read. A missing marker may mean the bootloader/recovery
overwrote it; it does not alone prove the kernel did not execute.
