#!/usr/bin/env python3
"""Check Dora's Android filesystem contract and selected real UAPI layouts.

The filesystem contract is pinned to tone-common 2701d2aecab52b30c3672ecebbad6ae1910cc212.
Legacy Binder/ION layout numbers come from Sony 4.4 commit 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1.
This never mounts an image, executes target Android, or proves vendor runtime ABI.
"""
import argparse
import copy
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def assignments(path):
    result = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"CONFIG_(\w+)=(.*)", line)
        disabled = re.fullmatch(r"# CONFIG_(\w+) is not set", line)
        if match:
            result[match[1]] = match[2]
        elif disabled:
            result[disabled[1]] = "n"
    return result


def config_check(config):
    # These are requirements of the actual pinned fstab/image format.  In
    # particular, read-only vendor still needs security.* xattrs for SELinux.
    required = """ARM64 COMPAT ANDROID_BINDER_IPC ANDROID_BINDERFS
        ION ION_LEGACY EXT4_FS EXT4_FS_SECURITY F2FS_FS F2FS_FS_SECURITY
        FS_ENCRYPTION DM_VERITY DM_VERITY_FEC BLK_DEV_LOOP
        SQUASHFS SQUASHFS_LZ4 LZ4_DECOMPRESS SQUASHFS_XATTR
        FAT_FS VFAT_FS NLS NLS_CODEPAGE_437 NLS_ISO8859_1
        SECURITY_SELINUX SECCOMP_FILTER BPF_SYSCALL BPF_JIT_ALWAYS_ON
        CGROUP_BPF PSI USB_GADGET USB_CONFIGFS""".split()
    missing = ["CONFIG_" + key for key in required if config.get(key) != "y"]
    if missing:
        raise ValueError("Android filesystem/interface built-ins missing: " + ", ".join(missing))
    if config.get("FAT_DEFAULT_CODEPAGE") != "437" or config.get("FAT_DEFAULT_IOCHARSET") != '"iso8859-1"':
        raise ValueError("firmware VFAT defaults changed; audit matching NLS and pinned fstab")
    if config.get("ANDROID_BINDER_IPC_32BIT", "n") != "n":
        raise ValueError("Dora ARM64 uses Binder protocol 8, including 32-bit Android clients")
    if config.get("ANDROID_BINDER_DEVICES") != '"binder,hwbinder,vndbinder"':
        raise ValueError("missing Android Binder device names")
    if config.get("DEFAULT_SECURITY") != '"selinux"' or config.get("SECURITY_SELINUX_DISABLE", "n") != "n":
        raise ValueError("SELinux default or runtime-disable policy changed")
    return len(required)


def config_selftest(config):
    for key in ("SQUASHFS_LZ4", "SQUASHFS_XATTR", "NLS_CODEPAGE_437", "NLS_ISO8859_1", "ANDROID_BINDER_IPC_32BIT"):
        broken = copy.copy(config)
        broken[key] = "y" if key == "ANDROID_BINDER_IPC_32BIT" else "n"
        try:
            config_check(broken)
        except ValueError:
            continue
        raise ValueError("negative config accepted: " + key)


def function(source, name, prefix):
    match = re.search(prefix + r"\s+" + name + r"\s*\([^;]*?\)\s*\{", source, re.S)
    if not match:
        raise ValueError("real function missing: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


def compile_run(temp, name, source, compiler, flags=(), execute=True):
    path, binary = temp / (name + ".c"), temp / name
    path.write_text(source)
    args = shlex.split(compiler) + ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", str(temp / "shim")] + list(flags)
    args += [str(path), "-o", str(binary)] if execute else ["-c", str(path), "-o", str(binary) + ".o"]
    subprocess.run(args, check=True)
    if execute:
        subprocess.run([str(binary)], check=True)


def uapi_checks(temp):
    shim = temp / "shim/linux"
    shim.mkdir(parents=True)
    (shim / "types.h").write_text("""#ifndef TEST_LINUX_TYPES_H
#define TEST_LINUX_TYPES_H
typedef unsigned char __u8; typedef signed char __s8;
typedef unsigned short __u16; typedef signed short __s16;
typedef unsigned int __u32; typedef signed int __s32;
typedef unsigned long long __u64; typedef signed long long __s64;
#endif
""")
    (shim / "ioctl.h").write_text('#include "' + str(ROOT / "include/uapi/asm-generic/ioctl.h") + '"\n')
    (shim / "fs.h").write_text("#include <linux/ioctl.h>\n")
    ion = (ROOT / "drivers/staging/android/ion/ion-legacy.c").read_text()
    dispatch = function(ion, "ion_legacy_cmd", "bool")
    source = r'''
#include <stddef.h>
#include <stdbool.h>
typedef int pid_t;
typedef unsigned int uid_t;
#define __packed __attribute__((packed))
struct inode; struct file;
#define CONFIG_ION_LEGACY 1
'''
    for file in ("include/uapi/linux/android/binder.h", "include/uapi/linux/fscrypt.h", "drivers/staging/android/ion/ion_legacy.h"):
        source += '#include "' + str(ROOT / file) + '"\n'
    source += r'''
/* Fixed values independently checked against the original Sony vendor UAPI. */
_Static_assert(sizeof(struct binder_version) == 4, "Binder version layout");
_Static_assert(BINDER_VERSION == 0xc0046209U, "Binder version ioctl");
#ifdef BINDER_IPC_32BIT
_Static_assert(sizeof(struct binder_write_read) == 24, "Binder v7 write/read");
_Static_assert(sizeof(struct binder_transaction_data) == 40, "Binder v7 transaction");
_Static_assert(sizeof(struct flat_binder_object) == 16, "Binder v7 object");
_Static_assert(BINDER_WRITE_READ == 0xc0186201U, "Binder v7 ioctl");
_Static_assert(BINDER_CURRENT_PROTOCOL_VERSION == 7, "Binder v7 protocol");
#else
_Static_assert(sizeof(struct binder_write_read) == 48, "Binder v8 write/read");
_Static_assert(sizeof(struct binder_transaction_data) == 64, "Binder v8 transaction");
_Static_assert(sizeof(struct flat_binder_object) == 24, "Binder v8 object");
_Static_assert(BINDER_WRITE_READ == 0xc0306201U, "Binder v8 ioctl");
_Static_assert(BINDER_CURRENT_PROTOCOL_VERSION == 8, "Binder v8 protocol");
#endif
_Static_assert(sizeof(struct ion_old_allocation_data) == (sizeof(size_t) == 8 ? 32 : 20), "legacy ION native layout");
_Static_assert(ION_OLD_IOC_ALLOC == (sizeof(size_t) == 8 ? 0xc0204900U : 0xc0144900U), "legacy ION allocation ioctl");
_Static_assert(sizeof(struct ion_fd_data) == 8 && ION_IOC_SHARE == 0xc0084904U && ION_IOC_IMPORT == 0xc0084905U, "legacy ION shared fd ABI");
_Static_assert(sizeof(struct ion_handle_data) == 4 && ION_IOC_FREE == 0xc0044901U, "legacy ION handle ABI");
_Static_assert(sizeof(struct ion_allocation_data) == 24 && ION_IOC_ALLOC == 0xc0184900U, "modern ION fd allocation");
_Static_assert(sizeof(struct fscrypt_policy_v1) == 12 && sizeof(struct fscrypt_policy_v2) == 24, "fscrypt policies");
_Static_assert(sizeof(struct fscrypt_get_policy_ex_arg) == 32 && offsetof(struct fscrypt_get_policy_ex_arg, policy) == 8, "fscrypt variable policy prefix");
_Static_assert(FS_IOC_GET_ENCRYPTION_POLICY_EX == 0xc0096616U, "fscrypt get policy ioctl");
_Static_assert(sizeof(struct fscrypt_add_key_arg) == 80 && FS_IOC_ADD_ENCRYPTION_KEY == 0xc0506617U, "fscrypt add key ioctl");
'''
    source += dispatch
    source += r'''
int main(void)
{
    if (!ion_legacy_cmd(ION_OLD_IOC_ALLOC) || !ion_legacy_cmd(ION_IOC_FREE) ||
        !ion_legacy_cmd(ION_IOC_SHARE) || !ion_legacy_cmd(ION_IOC_IMPORT) ||
        !ion_legacy_cmd(ION_IOC_SYNC) || ion_legacy_cmd(ION_IOC_ALLOC) ||
        ion_legacy_cmd(0xffffffffU))
        return 1;
    return 0;
}
'''
    host = os.environ.get("HOSTCC", "cc")
    compile_run(temp, "uapi64", source, host)
    compile_run(temp, "uapi32-wire", source, host, ("-DBINDER_IPC_32BIT",))
    targets = []
    for key in ("CROSS_COMPILE", "CROSS_COMPILE_COMPAT"):
        if os.environ.get(key):
            compiler = os.environ[key] + "gcc"
            if not shutil.which(compiler):
                raise ValueError("configured target compiler is missing: " + compiler)
            compile_run(temp, key.lower() + "64wire", source, compiler, execute=False)
            compile_run(temp, key.lower() + "32wire", source, compiler, ("-DBINDER_IPC_32BIT",), execute=False)
            targets.append(key)
    print("PASS: actual Binder/ION/fscrypt headers and real legacy ION command dispatch")
    print("Target UAPI layout compilers: " + (", ".join(targets) if targets else "not supplied; host layout only"))


def squashfs_checks(temp):
    source = (ROOT / "fs/squashfs/decompressor.c").read_text()
    # Use the real supported/unsupported descriptor table and lookup function.
    block = source[source.index("static const struct squashfs_decompressor squashfs_lzma_"):source.index("static void *get_comp_opts")]
    struct_header = (ROOT / "fs/squashfs/decompressor.h").read_text()
    match = re.search(r"struct squashfs_decompressor \{.*?\n\};", struct_header, re.S)
    if not match:
        raise ValueError("SquashFS descriptor declaration missing")
    ids = "\n".join(line for line in (ROOT / "fs/squashfs/squashfs_fs.h").read_text().splitlines()
                    if re.match(r"#define\s+\w+_COMPRESSION\s+\d+", line))
    wrapper = (ROOT / "fs/squashfs/lz4_wrapper.c").read_text()
    descriptor = re.search(r"const struct squashfs_decompressor squashfs_lz4_comp_ops = \{.*?\n\};", wrapper, re.S)
    if not descriptor:
        raise ValueError("real LZ4 descriptor missing")
    # Lookup never calls the codec callbacks. Keep the actual descriptor's
    # format ID/name/support bit while replacing uncalled callback addresses.
    harness = r'''
#include <stddef.h>
#include <string.h>
struct squashfs_sb_info; struct buffer_head; struct squashfs_page_actor;
'''+match[0]+"\n"+ids+r'''
#ifdef CONFIG_SQUASHFS_LZ4
#define lz4_init NULL
#define lz4_comp_opts NULL
#define lz4_free NULL
#define lz4_uncompress NULL
'''+descriptor[0]+r'''
#endif
'''+block+r'''
int main(void)
{
    const struct squashfs_decompressor *lz4 = squashfs_lookup_decompressor(LZ4_COMPRESSION);
    if (strcmp(lz4->name, "lz4") || squashfs_lookup_decompressor(999)->supported)
        return 1;
#ifdef CONFIG_SQUASHFS_LZ4
    if (!lz4->supported) return 1;
#else
    if (lz4->supported) return 1;
#endif
    return 0;
}
'''
    host = os.environ.get("HOSTCC", "cc")
    compile_run(temp, "squashfs-lz4", harness, host, ("-DCONFIG_SQUASHFS_LZ4",))
    compile_run(temp, "squashfs-no-lz4", harness, host)
    print("PASS: real SquashFS decompressor lookup accepts LZ4 only with its implementation selected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    try:
        config = assignments(args.config)
        count = config_check(config)
        if args.selftest:
            config_selftest(config)
        with tempfile.TemporaryDirectory(prefix="dora-android-abi-") as scratch:
            temp = Path(scratch)
            uapi_checks(temp)
            squashfs_checks(temp)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print("FAIL:", error)
        return 1
    print("PASS: %d resolved Android/vendor filesystem requirements" % count)
    if args.selftest:
        print("PASS: five missing filesystem/security/wire-format fixtures rejected")
    print("No Android execution: Binder services, vendor heaps/cache/custom ioctls, fscrypt keys,")
    print("mounts, verifier/SELinux policy, BoringSSL and vold require new target logs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
