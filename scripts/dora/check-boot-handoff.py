#!/usr/bin/env python3
"""Compare actual built Image/DTBs with Stage 10 and reported Stage 65B layout.

This uses the real packer's bounds checker without manufacturing a ramdisk or
boot image. A reported initrd address is not a firmware header contract.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('dora_handoff_packer', HERE / 'build-boot-image.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


def compare(image, image_gz, dtbs, self_test=False):
    kernel = packer.boot.kernel_info(image)
    assert packer.boot.decompress(image_gz, packer.boot.MAX_KERNEL, 'kernel')[1] == image
    assert len(dtbs) == 4
    trees = [packer.boot.fdt_info(blob) for blob in dtbs]
    assert len({packer.selection(tree) for tree in trees}) == 4
    profiles = [json.loads(path.read_text()) for path in (packer.PROFILE_PATH, packer.INITRD_EXPERIMENT_PATH)]
    ramdisk_size = profiles[1]['stage65b_observation']['initrd_bytes']
    compressed = len(image_gz) + sum(map(len, dtbs))
    padded = 4096 + packer.boot.align(compressed, 4096) + packer.boot.align(ramdisk_size, 4096)
    layouts = [packer.checked_layout(profile, kernel, trees, ramdisk_size, compressed, padded, 4096)
               for profile in profiles]
    if self_test:
        cases = []
        too_large = copy.deepcopy(kernel)
        too_large['runtime_extent'] = 0x2200000
        cases.append((too_large, trees, ramdisk_size))
        cases.append((kernel, trees, ramdisk_size + 1))
        for start in (0x80080000, 0x82200000, 0x82500000, 0x92301000):
            bad = copy.deepcopy(trees)
            bad[0]['reserved_ranges'].append(dict(source='negative fixture', start=start, size=4096))
            cases.append((kernel, bad, ramdisk_size))
        for bad_kernel, bad_trees, bad_size in cases:
            try:
                packer.checked_layout(profiles[1], bad_kernel, bad_trees, bad_size, compressed, padded, 4096)
            except ValueError:
                continue
            raise AssertionError('unsafe Stage 65B differential accepted')
    literal = [dict(source='kernel runtime', start=0x80008000, size=kernel['runtime_extent']),
               dict(source='header initrd', start=0x81000000, size=ramdisk_size)]
    return dict(status='OFFLINE ARITHMETIC CHECKED / HARDWARE UNVERIFIED', boot_test_ready=False,
                image=kernel, dtbs=trees, candidate_layouts=layouts,
                literal_working_header_kernel_initrd_overlap=packer.boot.overlap(*literal),
                literal_kernel_respects_text_offset_alignment=(0x80008000 - kernel['text_offset']) % 0x200000 == 0,
                reported_working_initrd=profiles[1]['stage65b_observation'],
                ramdisk_bytes_source='reported Stage 65B + Stage 10 metadata; payload required for packaging',
                ramdisk_payload_checked=False, original_stage10_image_available=False,
                unchanged_experiment_fields=['kernel_addr', 'tags_addr', 'scratch_addr', 'DTB tuple order'],
                changed_experiment_field='header ramdisk_addr: 0x82700000 -> 0x82200000',
                loader_may_ignore_header=True, actual_fdt_x0='UNKNOWN',
                persistent_diagnostic_region='NONE QUALIFIED; writes disabled',
                selftest_negative_cases=6 if self_test else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--image-gz', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, action='append', required=True)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.image.read_bytes(), args.image_gz.read_bytes(),
                         [path.read_bytes() for path in args.dtb], args.self_test)
        parse = runpy.run_path(str(HERE / 'check-power-dt.py'))['parse_fdt']
        result['compiled_memory_reg'] = [parse(path)['/memory']['props']['reg'].hex() for path in args.dtb]
        result['memory_reg_requires_firmware_fixup'] = True
        result['input_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in [args.image, args.image_gz, *args.dtb]}
        if args.report:
            assert args.report.resolve() not in {path.resolve() for path in [args.image, args.image_gz, *args.dtb]}
            with args.report.open('x') as output:
                json.dump(result, output, indent=2)
                output.write('\n')
    except (ValueError, AssertionError) as error:
        parser.error(str(error))
    print('PASS: actual Image+BSS and four-DTB bounds for Stage 10 and Stage 65B initrd differential')
    print('User-reported initrd is not an x0/FDT/header relocation proof. BOOT TEST READY: NO.')


if __name__ == '__main__':
    main()
