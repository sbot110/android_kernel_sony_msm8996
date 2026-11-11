#!/usr/bin/env python3
"""Build an isolated (no Stage 1..7 writes) diagnostic boot image for dora.

Run from the existing experimental worktree:
    python3 tools/dora_build_no_stages.py

This script never flashes a phone, never creates branches, and restores the
two temporarily changed source files after building. Output is intentionally
placed in a NEW directory; existing diagnostic images are not overwritten.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path.home() / "android/kernel-work"
EXPECTED_ROOT = WORK / "sony-msm8996-5.15-ad3dcab-test"
OUT = WORK / "out-sony-5.15-ad3dcab-test"
ROM_ZIP = Path.home() / "android/lineage/out/target/product/dora/lineage-21.0-20261030-UNOFFICIAL-dora.zip"
REFERENCE = Path("/mnt/c/platform-tools/dora-boot-reference.img")
DEST = Path("/mnt/c/platform-tools/dora515-preflight-no-stages")
HEAD = ROOT / "arch/arm64/kernel/head.S"
SETUP = ROOT / "arch/arm64/kernel/setup.c"
OLD_NO_PREMMU_SHA256 = "10930343a49a2c4fd9c0db760659c198b11e371108c634e067ecc8e30a76b132"


def die(message):
    raise RuntimeError(message)


def run(*argv):
    print("+", " ".join(map(str, argv)), flush=True)
    subprocess.run([str(x) for x in argv], check=True)


def read_cmd(*argv):
    return subprocess.check_output([str(x) for x in argv], text=True).strip()


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def disable_asm(s):
    segments = [
        ("STAGE1",
         "\t/*\n\t * Dora (MSM8996) experimental early-boot breadcrumb Slot 0",
         "\n\tdmb\tsy"),
        ("STAGE2",
         "\t/*\n\t * Dora (MSM8996) experimental early-boot breadcrumb Slot 1",
         "\n\tadrp\tx1, init_pg_dir"),
    ]
    ranges = []
    for name, start_marker, stop_marker in segments:
        if s.count(start_marker) != 1:
            die(f"{name}: missing or ambiguous start marker")
        start = s.index(start_marker)
        end = s.find(stop_marker, start)
        if end < 0:
            die(f"{name}: missing end marker")
        ranges.append((start, end, name))
    if ranges[0][1] >= ranges[1][0]:
        die("Overlapping Stage 1 and Stage 2 ranges")
    for start, end, name in reversed(ranges):
        s = (s[:start] +
             f"#if 0 /* DORA EXPERIMENT: {name} DISABLED */\n" +
             s[start:end] +
             f"\n#endif /* DORA {name} */" +
             s[end:])
    return s


def disable_c(s):
    old = "if (stage < 3 || stage > 7)\n\t\treturn;"
    if s.count(old) != 1:
        die("Missing or ambiguous Stage 3-7 guard in setup.c")
    # The function is kept for source compatibility, but compiled as a no-op.
    new = "if (1) /* DORA EXPERIMENT: STAGES 3-7 DISABLED */\n\t\treturn;"
    return s.replace(old, new, 1)


def main():
    print("=== DORA 5.15: BUILD WITHOUT STAGES 1-7 ===", flush=True)

    if ROOT != EXPECTED_ROOT.resolve():
        die(f"Wrong worktree: {ROOT}; expected {EXPECTED_ROOT}")
    if read_cmd("git", "-C", ROOT, "rev-parse", "--show-toplevel") != str(ROOT):
        die("Unexpected git worktree root")
    if DEST.exists():
        die(f"Output directory already exists; refusing overwrite: {DEST}")
    for item in (HEAD, SETUP, REFERENCE, ROM_ZIP,
                 ROOT / "tools/dora_prepare_boot515.py"):
        if not item.is_file():
            die(f"Required input missing: {item}")
    if not (OUT / ".config").is_file():
        die(f"Kernel build config missing: {OUT / '.config'}")

    status = read_cmd("git", "-C", ROOT, "status", "--porcelain", "--",
                      "arch/arm64/kernel/head.S", "arch/arm64/kernel/setup.c")
    if status:
        die(f"Source files have local modifications:\n{status}")

    original_head = HEAD.read_bytes()
    original_setup = SETUP.read_bytes()
    new_head = disable_asm(original_head.decode("utf-8"))
    new_setup = disable_c(original_setup.decode("utf-8"))
    if new_head.encode() == original_head or new_setup.encode() == original_setup:
        die("Both source files must be changed for this experiment")

    head_obj = OUT / "arch/arm64/kernel/head.o"
    setup_obj = OUT / "arch/arm64/kernel/setup.o"
    before = {p: sha(p) if p.is_file() else None
              for p in (head_obj, setup_obj)}

    print("Temporary edits: head.S Stage 1,2 off; setup.c Stage 3..7 off", flush=True)
    print("Output:", DEST, flush=True)
    try:
        HEAD.write_text(new_head)
        SETUP.write_text(new_setup)

        run("git", "-C", ROOT, "diff", "--check", "--",
            "arch/arm64/kernel/head.S", "arch/arm64/kernel/setup.c")
        run("make", "-C", ROOT, f"O={OUT}", "ARCH=arm64",
            "CROSS_COMPILE=aarch64-linux-gnu-",
            "CROSS_COMPILE_COMPAT=arm-linux-gnueabihf-",
            "-j8", "Image.gz-dtb", "dtbs")

        for path in (head_obj, setup_obj):
            if not path.is_file():
                die(f"Expected compiled object missing: {path}")
            if before[path] == sha(path):
                die(f"Object unchanged after disabling its boot stages: {path}")

        packed = OUT / "arch/arm64/boot/Image.gz-dtb"
        image = OUT / "arch/arm64/boot/Image"
        if not packed.is_file() or not image.is_file():
            die("Missing built kernel image")

        run(sys.executable, ROOT / "tools/dora_prepare_boot515.py",
            "--reference", REFERENCE,
            "--reference-zip", ROM_ZIP,
            "--kernel", packed,
            "--image", image,
            "--outdir", DEST)

        # Preserve precisely the images built from the temporarily edited sources.
        shutil.copy2(image, DEST / "Image")
        shutil.copy2(packed, DEST / "Image.gz-dtb")
        manifest = json.loads((DEST / "dora-515-diagnostic-manifest.json").read_text())
        if (sha(DEST / "Image.gz-dtb") != manifest["new_kernel_sha256"]
                or sha(DEST / "Image") != manifest["image_sha256"]):
            die("Copied images differ from the validated package inputs")
        full_sha = sha(DEST / "dora-515-diagnostic-boot-full64M.img")
        if full_sha == OLD_NO_PREMMU_SHA256:
            die("Result indistinguishable from the second experiment")
        if full_sha != manifest["new_boot_full64_sha256"]:
            die("New full-partition boot image does not match manifest")

        print("\n=== FINAL VERIFIED CHECKSUMS ===", flush=True)
        for path in (DEST / "Image", DEST / "Image.gz-dtb",
                     DEST / "dora-515-diagnostic-boot.img",
                     DEST / "dora-515-diagnostic-boot-full64M.img"):
            print(f"{sha(path)}  {path}", flush=True)
        print("\nPASS: third experimental boot image created; NOT FLASHED", flush=True)
        print("NOTE: OUT retains the no-stages kernel; source files will be restored.",
              flush=True)
    finally:
        # Restore exact source bytes, including original line endings.
        HEAD.write_bytes(original_head)
        SETUP.write_bytes(original_setup)
        print("SOURCE RESTORED: head.S and setup.c", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("STOP:", exc, file=sys.stderr)
        sys.exit(2)
