#!/usr/bin/env python3
"""Check real version, requested Kconfig symbols and resolved dora configuration.

Passing is a configuration check, not evidence of successful device bring-up.
"""
import argparse
import re
from pathlib import Path


def assignments(path):
    values = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r"CONFIG_(\w+)=(.*)", line)
        if match:
            values[match[1]] = match[2]
        else:
            match = re.fullmatch(r"# CONFIG_(\w+) is not set", line)
            if match:
                values[match[1]] = "n"
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="resolved O=/.config")
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.source.resolve()
    makefile = (root / "Makefile").read_text()
    version = tuple(int(re.search(r"^" + key + r"\s*=\s*(\d+)",
                                  makefile, re.M)[1])
                    for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL"))
    errors = []
    if version[:2] != (4, 19):
        errors.append("expected real Linux 4.19 core, found %s" % (version,))
    symbols = set()
    for path in root.rglob("*Kconfig*"):
        if path.is_file():
            symbols.update(re.findall(r"^\s*(?:menu)?config\s+(\w+)",
                                      path.read_text(errors="replace"), re.M))
    requested = assignments(root / "arch/arm64/configs/tone_dora_defconfig")
    actual = assignments(args.config)
    for key, value in requested.items():
        if key not in symbols:
            errors.append("undefined Kconfig symbol: CONFIG_" + key)
        elif actual.get(key, "n") != value:
            errors.append("CONFIG_%s requested %s, resolved %s" %
                          (key, value, actual.get(key, "n")))
    # Check the resolved interfaces as well as explicit defconfig entries.
    # Several of these (MEMFD_CREATE, SOCK_CGROUP_DATA, CONFIGFS_FS and
    # FS_ENCRYPTION_ALGS) are selected by dependencies rather than prompts.
    # bpffs is built by BPF_SYSCALL in this tree; there is no BPF_FS symbol.
    required = """ARM64 COMPAT COMPAT_VDSO BLK_DEV_INITRD RD_XZ
        CGROUPS MEMCG MEMCG_SWAP BLK_CGROUP CGROUP_SCHED FAIR_GROUP_SCHED
        CGROUP_CPUACCT CGROUP_FREEZER CPUSETS CGROUP_PIDS CGROUP_DEVICE
        CGROUP_BPF SOCK_CGROUP_DATA BPF BPF_SYSCALL HAVE_EBPF_JIT BPF_JIT
        BPF_JIT_ALWAYS_ON NETFILTER_XT_MATCH_BPF NET_CLS_BPF NET_ACT_BPF
        NAMESPACES UTS_NS IPC_NS PID_NS NET_NS PSI MEMBARRIER SECCOMP
        SECCOMP_FILTER SECURITY SECURITY_NETWORK SECURITY_SELINUX
        EXT4_FS EXT4_FS_POSIX_ACL EXT4_FS_SECURITY EXT4_ENCRYPTION
        F2FS_FS F2FS_FS_XATTR F2FS_FS_SECURITY F2FS_FS_ENCRYPTION
        FS_ENCRYPTION FS_ENCRYPTION_ALGS QUOTA QFMT_V2 DM_CRYPT DM_VERITY
        DM_VERITY_FEC BLK_DEV_LOOP OVERLAY_FS INET IPV6 TUN FUSE_FS
        TMPFS TMPFS_POSIX_ACL MEMFD_CREATE ASHMEM ION ION_LEGACY SYNC_FILE
        USB_GADGET CONFIGFS_FS USB_CONFIGFS USB_CONFIGFS_F_FS
        USB_CONFIGFS_F_MTP USB_CONFIGFS_F_PTP USB_CONFIGFS_F_ACC
        USB_CONFIGFS_RNDIS PM_AUTOSLEEP PM_WAKELOCKS ANDROID_BINDER_IPC
        ANDROID_BINDERFS QCOM_KGSL FB_MSM_MDSS SCSI_UFS_QCOM SIMPLE_PM_BUS
        PINCTRL_QCOM_SPMI_PMIC QCOM_APCS_IPC RPMSG_QCOM_GLINK_RPM
        PHY_QCOM_QUSB2 PHY_QCOM_UFS_QMP_14NM QCOM_BUS_SCALING
        QCOM_SMD_RPM REGULATOR_QCOM_GENPD PM_GENERIC_DOMAINS
        QCOM_MSM8996_BOOT_CLOCK REGULATOR_CPR3_HMSS REGULATOR_CPR3_MMSS
        REGULATOR_KRYO REGULATOR_SPM REGULATOR_MEM_ACC MSM_APM MSM_SPM
        LEDS_QPNP_WLED LEDS_TRIGGERS REGULATOR_QCOM_RPM_CORNER
        REGULATOR_QCOM_SMD_RPM REGULATOR_QCOM_RPM_PHYSICAL
        MMC MMC_BLOCK MMC_SDHCI MMC_SDHCI_PLTFM MMC_SDHCI_MSM EFI_PARTITION
        SQUASHFS SQUASHFS_LZ4 SQUASHFS_XATTR VFAT_FS
        NLS_CODEPAGE_437 NLS_ISO8859_1""".split()
    for key in required:
        if actual.get(key) != "y":
            errors.append("required built-in not selected: CONFIG_" + key)
    if actual.get("ANDROID_BINDER_DEVICES") != '"binder,hwbinder,vndbinder"':
        errors.append("expected binder,hwbinder,vndbinder device names")
    for key in ("PSI_DEFAULT_DISABLED", "SECURITY_SELINUX_DISABLE",
                "ANDROID_BINDER_IPC_32BIT", "CPU_FREQ", "CPU_IDLE",
                "BACKLIGHT_QCOM_SPMI_WLED"):
        if actual.get(key, "n") != "n":
            errors.append("incompatible with dora Android userspace: CONFIG_" + key)
    if actual.get("DEFAULT_SECURITY") != '"selinux"':
        errors.append("SELinux must remain the default security module")
    if errors:
        for message in errors:
            print("FAIL:", message)
        return 1
    print("PASS: Linux %s; %d explicit assignments and %d required built-ins" %
          (".".join(map(str, version)), len(requested), len(required)))
    print("Configuration only: dora hardware, vendor ABI and boot remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
