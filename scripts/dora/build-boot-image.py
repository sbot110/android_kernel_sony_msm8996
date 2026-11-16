#!/usr/bin/env python3
"""Package a private reference ramdisk with Linux 4.19, offline only.

A source-backed LK layout profile is explicit input, never an inferred
BoardConfig/default or a hardware ownership grant. This writes regular host
files only. It cannot flash, boot a phone or declare hardware readiness.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROFILE_PATH = ROOT / 'Documentation/sony/dora-msm8996-lk-source-layout.json'
spec = importlib.util.spec_from_file_location('dora_boot_inspector', HERE / 'check-boot-image.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
require = boot.require


def section(data, header, name):
    item = header['sections'][name]
    return data[item['offset']:item['offset'] + item['size']]


def selection(tree):
    keys = ('qcom,msm-id', 'qcom,board-id', 'qcom,pmic-id')
    value = tree['selection_tuples']
    require(all(key in value for key in keys), 'missing Sony DTB selection tuple')
    return tuple(tuple(value[key]) for key in keys)


def cpio_entries(data):
    # The production inspector has already checked bounds, CRC, duplicate and
    # canonical names, trailer and /init. Keep byte payloads private in memory.
    _, payload, tail, _ = boot.decompress(data, boot.MAX_RAMDISK, 'ramdisk')
    require(not tail, 'ramdisk trailer')
    result, pos = {}, 0
    while True:
        fields = [int(payload[pos + 6 + i:pos + 14 + i], 16) for i in range(0, 104, 8)]
        size, namesize = fields[6], fields[11]
        pos += 110
        name = payload[pos:pos + namesize - 1].decode('utf-8')
        pos = boot.align(pos + namesize, 4)
        content = payload[pos:pos + size]
        pos = boot.align(pos + size, 4)
        if name == 'TRAILER!!!':
            return result
        result[name.removeprefix('./')] = content


def enforcing_cmdline(original):
    tokens = original.split()
    result, changes = [], []
    for item in tokens:
        key, _, value = item.partition('=')
        require(key not in ('init', 'rdinit', 'skip_initramfs'),
                'alternate init path is not qualified for Android first-stage init')
        require(key not in ('nowatchdog', 'nosoftlockup') and
                not (key == 'thermal.off' and value == '1') and
                not (('watchdog' in key or 'wdog' in key or key == 'msm_thermal.enabled') and
                     value in ('0', 'off', 'false', 'disabled')),
                'watchdog/thermal protections must not be disabled by inherited arguments')
        if key == 'maxcpus':
            changes.append({'removed_key': key, 'reason': 'one explicit diagnostic CPU limit'})
            continue
        if key in ('androidboot.selinux', 'selinux', 'enforcing'):
            if (key == 'androidboot.selinux' and value != 'enforcing') or (
                    key in ('selinux', 'enforcing') and value != '1'):
                changes.append({'removed_key': key, 'reason': 'security bypass is not inherited'})
            continue
        result.append(item)
    result += ['androidboot.selinux=enforcing', 'maxcpus=2']
    parsed = boot.cmdline_info(' '.join(result).encode('ascii'))
    require(not parsed['security_bypasses'] and not parsed['duplicate_keys'],
            'ambiguous or insecure resulting command line')
    return parsed['text'], changes


def checked_config(config):
    actual = {}
    for line in config.splitlines():
        line = line.strip()
        match = re.fullmatch(r'CONFIG_(\w+)=(.+)', line)
        disabled = re.fullmatch(r'# CONFIG_(\w+) is not set', line)
        if match or disabled:
            key, value = (match[1], match[2]) if match else (disabled[1], 'n')
            require(key not in actual, 'duplicate/ambiguous configuration symbol ' + key)
            actual[key] = value
        elif line.startswith(('CONFIG_', '# CONFIG_')):
            raise ValueError('malformed kernel configuration assignment')
    required = ['ARM64', 'ARM64_4K_PAGES', 'BLK_DEV_INITRD', 'RD_XZ', 'SECURITY_SELINUX', 'EXT4_FS',
                'SQUASHFS', 'DEVTMPFS', 'EFI_PARTITION', 'MMC_BLOCK']
    for symbol in required:
        require(actual.get(symbol) == 'y', 'missing built-in ' + symbol)
    for symbol in ('CPU_FREQ', 'CPU_IDLE'):
        require(actual.get(symbol) == 'n', 'unqualified ' + symbol)
    require(actual.get('SECURITY_SELINUX_BOOTPARAM_VALUE') != '0',
            'SELinux cannot default to permissive')
    require(actual.get('SECURITY_SELINUX_DISABLE', 'n') == 'n', 'SELinux disabling is not permitted')
    require(actual.get('CMDLINE_FORCE', 'n') == 'n', 'forced kernel cmdline needs separate review')


def checked_layout(profile, kernel, trees, ramdisk_bytes, compressed_bytes, padded_image_bytes, page):
    require(profile == json.loads(PROFILE_PATH.read_text()),
            'layout differs from the reviewed pinned CAF source profile')
    require(profile.get('profile_version') == 1 and
            profile.get('profile_id') == 'caf-msm8996-source-layout-not-device-qualified', 'unknown layout profile')
    require(profile.get('hardware_verified') is False, 'this packer cannot promote a hardware claim')
    require(profile.get('source_provenance'), 'layout source provenance is required')
    require(profile.get('firmware_match_unverified') is True, 'firmware qualification cannot be inferred')
    addresses = dict(kernel=profile['kernel_addr'], ramdisk=profile['ramdisk_addr'],
        tags=profile['tags_addr'], ddr_base=profile['base_address'],
        decompression_input=profile['scratch_addr'] + page,
        decompression_output=profile['scratch_addr'] + padded_image_bytes + page)
    for key in ('kernel', 'ramdisk', 'tags', 'ddr_base', 'decompression_input', 'decompression_output'):
        require(type(addresses.get(key)) is int and 0 <= addresses[key] < 1 << 32,
                'invalid layout address ' + key)
    require(kernel['runtime_extent'], 'unknown expanded kernel extent')
    require((addresses['kernel'] - kernel['text_offset']) % (2 * 1024 * 1024) == 0,
            'ARM64 text_offset does not follow a 2MiB-aligned base')
    require(addresses['kernel'] - kernel['text_offset'] == addresses['ddr_base'],
            'kernel base disagrees with explicit source profile')
    require(page == profile['page_size'] and kernel['text_offset'] == profile['arm64_text_offset'],
            'reference page or kernel text_offset disagrees with source profile')
    workspace = profile['dtb_workspace_capacity_before_ramdisk']
    require(type(workspace) is int and 0 < workspace <= boot.MAX_FDT, 'invalid DTB workspace')
    require(type(profile['source_dtb_pad_bytes']) is int and profile['source_dtb_pad_bytes'] >= 0,
            'invalid source DTB growth padding')
    require(all(tree['bytes'] + profile['source_dtb_pad_bytes'] + 1536 <= workspace for tree in trees),
            'selected DTB and growth padding exceed workspace')
    ranges = [dict(source='kernel runtime (file + BSS)', start=addresses['kernel'], size=kernel['runtime_extent']),
              dict(source='compressed initrd', start=addresses['ramdisk'], size=ramdisk_bytes),
              dict(source='selected DTB workspace', start=addresses['tags'], size=workspace)]
    for left in range(len(ranges)):
        require(ranges[left]['start'] + ranges[left]['size'] <= 1 << 32, 'load range overflow')
        for right in range(left + 1, len(ranges)):
            require(not boot.overlap(ranges[left], ranges[right]), 'overlapping ' + ranges[left]['source'] + '/' + ranges[right]['source'])
    for tree in trees:
        for loaded in ranges:
            for reserved in tree['reserved_ranges']:
                require(not boot.overlap(loaded, reserved), 'load range overlaps fixed reservation ' + reserved['source'])
    # Source-backed scratch policy is checked, not asserted to match firmware.
    scratch = [dict(source='compressed staging', start=addresses['decompression_input'], size=compressed_bytes),
               dict(source='expanded staging', start=addresses['decompression_output'], size=kernel['runtime_extent'])]
    for area in scratch:
        require(area['start'] + area['size'] <= 1 << 32, 'scratch range overflow')
        require(profile['scratch_addr'] <= area['start'] and
                area['start'] + area['size'] <= profile['scratch_addr'] + profile['scratch_capacity'],
                'decompression staging exceeds source scratch window')
        for loaded in ranges:
            require(not boot.overlap(area, loaded), 'decompression staging overlaps a final load')
    require(not boot.overlap(scratch[0], scratch[1]), 'in-place decompression risk in explicit profile')
    for tree in trees:
        for area in scratch:
            for reserved in tree['reserved_ranges']:
                require(not boot.overlap(area, reserved), 'scratch intersects fixed reserved memory')
    return dict(addresses=addresses, ranges=ranges, scratch_ranges=scratch,
                fixed_reservations_checked_for_all_trees=True,
                declared_load_ranges_disjoint=True, source_scratch_ranges_disjoint=True,
                actual_ram_map_verified=False, actual_loader_relocation_verified=False,
                dynamic_reserved_memory_and_firmware_ownership='HARDWARE UNVERIFIED',
                profile_provenance=profile['source_provenance'])


def assemble(reference, image_gz, image, dtbs, profile, config, partition_size=None):
    checked_config(config)
    header = boot.boot_header(reference, partition_size)
    require(header['image_id']['verified'], 'reference header ID is absent or inconsistent')
    require(header['trailing_bytes'] == 0 or not any(reference[header['payload_padded_end']:]),
            'unknown signature/footer/trailer cannot be repacked')
    require(header['sections']['second']['size'] == 0, 'second-stage payload needs separate qualification')
    require(not any(reference[1632:header['page_size']]), 'unknown vendor header extension')
    for item in header['sections'].values():
        require(not any(reference[item['offset'] + item['size']:item['padded_end']]),
                'nonzero reference section padding cannot be silently discarded')
    old_kernel = boot.kernel_info(section(reference, header, 'kernel'))
    require(old_kernel['compression'] == 'gzip', 'reference compression is not supported by this packer')
    ramdisk = section(reference, header, 'ramdisk')
    ramdisk_meta = boot.ramdisk_info(ramdisk, config)
    require(ramdisk_meta.get('android14_dora_first_stage_candidate'), 'reference is not an identified Android14 Dora first-stage ramdisk')
    payload = cpio_entries(ramdisk)
    require(payload.get('init', b'')[:4] == b'\x7fELF', 'init must be the actual ELF first-stage executable')
    kernel = boot.kernel_info(image_gz)
    require(kernel['little_endian'], 'Android14 AArch64 ramdisk requires a little-endian kernel')
    require(kernel['flags'] & 6 == 2, 'ARM64 Image page-size flags disagree with 4KiB configuration')
    require(kernel['compression'] == 'gzip' and not kernel['appended_dtbs'] and not kernel['trailing_zero_padding'],
            'input must be the standalone built Image.gz')
    require(boot.decompress(image_gz, boot.MAX_KERNEL, 'kernel')[1] == image, 'Image and Image.gz disagree')
    releases = set(re.findall(rb'Linux version (\d+\.\d+\.\d+)(?=[ +\-\0])', image))
    require(releases == {b'4.19.318'}, 'new Image must identify the requested Linux 4.19.318')
    old_trees = old_kernel['appended_dtbs']
    require(len(old_trees) == len(dtbs) == 4, 'all four Sony variants are required')
    by_id = {}
    for blob in dtbs:
        tree = boot.fdt_info(blob)
        ident = selection(tree)
        require(ident not in by_id, 'duplicate DTB hardware tuple')
        by_id[ident] = (tree, blob)
    ordered = []
    for old in old_trees:
        require(selection(old) in by_id, 'reference hardware tuple has no new DTB')
        ordered.append(by_id.pop(selection(old)))
    require(not by_id, 'extra new DTB tuple')
    cmdline, changes = enforcing_cmdline(header['cmdline']['text'])
    encoded = cmdline.encode('ascii')
    require(len(encoded) < 1536, 'command line exceeds boot v0 slots')
    page = header['page_size']
    kernel_payload = image_gz + b''.join(blob for _, blob in ordered)
    padded_size = page + boot.align(len(kernel_payload), page) + boot.align(len(ramdisk), page)
    layout = checked_layout(profile, kernel, [tree for tree, _ in ordered], len(ramdisk),
                            len(kernel_payload), padded_size, page)
    new_header = bytearray(reference[:page])
    struct.pack_into('<I', new_header, 8, len(kernel_payload))
    struct.pack_into('<I', new_header, 12, layout['addresses']['kernel'])
    struct.pack_into('<I', new_header, 20, layout['addresses']['ramdisk'])
    struct.pack_into('<I', new_header, 32, layout['addresses']['tags'])
    new_header[64:576] = b'\0' * 512
    new_header[608:1632] = b'\0' * 1024
    new_header[64:64 + min(512, len(encoded))] = encoded[:512]
    if len(encoded) >= 512:
        new_header[608:608 + len(encoded[512:])] = encoded[512:]
    variants = header['image_id']['matching_variants']
    require(len(variants) == 1 and variants[0] in ('legacy_qcom_v0', 'aosp_v0'), 'ambiguous reference ID variant')
    digest = hashlib.sha1()
    for blob in (kernel_payload, ramdisk, b''):
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
    if variants[0] == 'legacy_qcom_v0':
        digest.update(struct.pack('<I', 0))
    new_header[576:608] = digest.digest() + b'\0' * 12
    result = bytes(new_header) + kernel_payload + b'\0' * (-len(kernel_payload) % page)
    result += ramdisk + b'\0' * (-len(ramdisk) % page)
    # A working reference proves at most a size lower bound, never a GPT value.
    require(len(result) <= len(reference), 'new image exceeds conservative reference-file size cap')
    if partition_size is not None:
        require(len(result) <= partition_size, 'new image exceeds observed partition size')
    inspect = boot.inspect_boot(result, partition_size, config)
    require(inspect['header']['image_id']['verified'], 'packaged ID mismatch')
    require(not inspect['layout']['load_overlaps'] and
            all(not tree['overlaps'] for tree in inspect['layout']['dtb_candidates']),
            'packaged declared load ranges intersect')
    require(section(result, inspect['header'], 'ramdisk') == ramdisk, 'ramdisk changed')
    require(section(result, inspect['header'], 'kernel') == kernel_payload, 'kernel round-trip mismatch')
    return result, dict(status='OFFLINE BUILT / HARDWARE UNVERIFIED', boot_test_ready=False,
        booted=False, daily_use_ready=False, intended_use='offline candidate; not qualified for flashing',
        input_reference_sha256=hashlib.sha256(reference).hexdigest(),
        ramdisk_sha256=hashlib.sha256(ramdisk).hexdigest(), ramdisk_unchanged=True,
        image_sha256=hashlib.sha256(image).hexdigest(), kernel_release='4.19.318',
        kernel_payload_sha256=hashlib.sha256(kernel_payload).hexdigest(),
        image_id_variant=variants[0], image_id=digest.hexdigest(), bytes=len(result),
        sha256=hashlib.sha256(result).hexdigest(), reference_file_size_cap=len(reference),
        actual_partition_size=partition_size, partition_size_inferred=False,
        relative_size_fits_reference=True, page_size=page,
        command_line_changes=changes, selinux_enforcing=True, maxcpus=2,
        original_header_addresses_preserved=False,
        header_address_reason='source-backed MSM8996 LK candidate; original declared overlap rejected',
        layout=layout, dtb_selection_order=[tree['selection_tuples'] for tree, _ in ordered],
        hardware_blockers=['actual Sony loader and RAM/relocation evidence',
            'complete CPU CPR/SPM/APM/LMHv1 transfer and six leases',
            'watchdog secure handoff', 'USB/charging protections and ownership',
            'phone eMMC/GPT/first-stage/SELinux/runtime logs'])


def write_outputs(image_path, image, report_path, report):
    # Open both with exclusive creation before writing either. On failure only
    # unlink our own newly created inodes; an input or raced replacement is
    # never removed. Existing images/reports are always preserved.
    created = []
    streams = []
    failure = None
    try:
        for path in (image_path, report_path):
            stream = path.open('xb')
            streams.append(stream)
            info = os.fstat(stream.fileno())
            created.append((path, info.st_dev, info.st_ino))
        streams[0].write(image)
        streams[1].write((json.dumps(report, indent=2) + '\n').encode())
        for stream in streams:
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as error:
        failure = error
    finally:
        for stream in streams:
            try:
                stream.close()
            except BaseException as error:
                if failure is None:
                    failure = error
                elif hasattr(failure, 'add_note'):
                    failure.add_note('Additional output close failure: ' + repr(error))
    if failure is not None:
        for path, device, inode in created:
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == (device, inode):
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                if hasattr(failure, 'add_note'):
                    failure.add_note('Output cleanup failed for %s: %s' % (path, error))
        raise failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--image-gz', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, action='append', required=True)
    parser.add_argument('--layout-profile', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--partition-size', type=int)
    parser.add_argument('--offline-candidate', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        paths = [args.reference, args.image_gz, args.image, *args.dtb, args.layout_profile, args.config]
        require(len({p.resolve() for p in paths}) == len(paths), 'input aliases refused')
        require(args.output.resolve() not in {p.resolve() for p in paths}, 'output aliases an input')
        require(args.report.resolve() not in {p.resolve() for p in paths} | {args.output.resolve()}, 'report aliases input/output')
        require(not args.output.exists() and not args.report.exists(), 'existing output/report refused')
        require(not args.output.is_symlink() and not args.report.is_symlink(), 'output symlink refused')
        require(args.output.parent.is_dir() and args.report.parent.is_dir(), 'output/report directory is missing')
        require(not args.output.resolve().is_relative_to(ROOT), 'private boot image output must stay outside Git source')
        stage_spec = importlib.util.spec_from_file_location('dora_first_stage', HERE / 'check-first-stage-init.py')
        first_stage = importlib.util.module_from_spec(stage_spec)
        stage_spec.loader.exec_module(first_stage)
        for path in args.dtb:
            first_stage.validate_boot_tree(path, config=args.config)
        data, report = assemble(boot.read_artifact(args.reference), boot.read_artifact(args.image_gz),
            boot.read_artifact(args.image), [boot.read_artifact(p, boot.MAX_FDT) for p in args.dtb],
            json.loads(boot.read_artifact(args.layout_profile, 1024 * 1024)),
            boot.read_artifact(args.config, 1024 * 1024).decode(), args.partition_size)
        write_outputs(args.output, data, args.report, report)
    except (ValueError, OSError, KeyError, TypeError, struct.error) as exc:
        parser.exit(1, 'Boot image packaging refused: %s\n' % exc)
    print('OFFLINE BUILT / HARDWARE UNVERIFIED:', args.output)
    print('SHA256:', report['sha256'])
    print('BOOT TEST READY: NO; actual firmware, CPU power and phone runtime remain unqualified.')


if __name__ == '__main__':
    main()
