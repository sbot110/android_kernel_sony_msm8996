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
    required = """ARM64 COMPAT COMPAT_VDSO CGROUPS MEMCG MEMCG_SWAP
        CPUSETS CGROUP_PIDS CGROUP_DEVICE CGROUP_BPF BPF_SYSCALL BPF_JIT
        NAMESPACES UTS_NS IPC_NS PID_NS NET_NS PSI MEMBARRIER SECCOMP
        SECCOMP_FILTER SECURITY_SELINUX EXT4_FS EXT4_ENCRYPTION DM_VERITY
        DM_VERITY_FEC INET IPV6 TUN FUSE_FS ANDROID_BINDER_IPC
        ANDROID_BINDERFS QCOM_KGSL FB_MSM_MDSS SCSI_UFS_QCOM""".split()
    for key in required:
        if actual.get(key) != "y":
            errors.append("required built-in not selected: CONFIG_" + key)
    if actual.get("ANDROID_BINDER_DEVICES") != '"binder,hwbinder,vndbinder"':
        errors.append("expected binder,hwbinder,vndbinder device names")
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
