#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Execute the production offline packer with synthetic host-only components.

The raw header, SHA1 convention, gzip/CPIO components, tuple order, command
line and final memory ranges are checked independently of the producer's
inspector. Source-profile fixtures do not establish Sony firmware behavior.
No supplied image, ramdisk, fingerprint, device or partition is accessed.
"""
import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import resource
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / 'scripts/dora/build-boot-image.py'
PROFILE = ROOT / 'Documentation/sony/dora-msm8996-lk-source-layout.json'
PAGE = 4096
TUPLES = (
    ((246, 0x30000), (8, 0), (0x20009, 0x2000a, 0, 0)),
    ((246, 0x30001), (8, 0), (0x20009, 0x2000a, 0, 0)),
    ((246, 0x30000), (8, 0), (0x20009, 0x10013, 0, 0)),
    ((246, 0x30001), (8, 0), (0x20009, 0x10013, 0, 0)),
)
CONFIG = ''.join('CONFIG_' + key + '=y\n' for key in (
    'ARM64', 'ARM64_4K_PAGES', 'BLK_DEV_INITRD', 'RD_XZ', 'SECURITY_SELINUX', 'EXT4_FS',
    'SQUASHFS', 'DEVTMPFS', 'EFI_PARTITION', 'MMC_BLOCK'))
CONFIG += ('# CONFIG_CPU_FREQ is not set\n# CONFIG_CPU_IDLE is not set\n'
           'CONFIG_SECURITY_SELINUX_BOOTPARAM_VALUE=1\n')


def module(source=None):
    if source is None:
        spec = importlib.util.spec_from_file_location('dora_offline_production', PRODUCER)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result
    namespace = {'__file__': str(PRODUCER), '__name__': 'dora_offline_mutation'}
    exec(compile(source, str(PRODUCER), 'exec'), namespace)
    return type('MutationModule', (), namespace)


def aligned(value, page=PAGE):
    return (value + page - 1) // page * page


def cpio_entry(name, payload=b'', mode=0o100755):
    values = [1, mode, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(name) + 1, 0]
    data = b'070701' + b''.join(('%08x' % value).encode() for value in values)
    data += name.encode() + b'\0'
    data += b'\0' * (-len(data) % 4)
    data += payload
    return data + b'\0' * (-len(payload) % 4)


def first_stage_ramdisk(sdk=34, machine=183):
    # A bounded, non-executable-in-tests ELF geometry fixture, not user init.
    elf = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0' * 9,
                      2, machine, 1, 0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    elf += struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0, 120, 256, 4096)
    properties = ('ro.product.bootimage.device=dora\n'
                  'ro.product.bootimage.name=lineage_dora\n'
                  'ro.bootimage.build.version.release=14\n'
                  'ro.bootimage.build.version.sdk=%d\n' % sdk).encode()
    archive = cpio_entry('init', elf)
    archive += cpio_entry('system/etc/ramdisk/build.prop', properties, 0o100644)
    archive += cpio_entry('TRAILER!!!')
    return lzma.compress(archive), archive


def arm64_image(length=8192, runtime=0x200000, text=0x80000, flags=10, noisy=False):
    assert length >= 128
    data = bytearray(length)
    data[56:60] = b'ARM\x64'
    struct.pack_into('<QQQ', data, 8, text, runtime, flags)
    if noisy:
        # Deterministic incompressible bytes keep the old file cap realistic.
        for offset in range(64, length, 32):
            value = hashlib.sha256(struct.pack('<Q', offset)).digest()
            data[offset:offset + min(32, length - offset)] = value[:length - offset]
    banner = b'Linux version 4.19.318 (synthetic host fixture)\0'
    data[64:64 + len(banner)] = banner
    return bytes(data)


def fdt(ident, reservation=None, fill=0):
    names = [b'qcom,msm-id', b'qcom,board-id', b'qcom,pmic-id']
    values = [struct.pack('>%dI' % len(value), *value) for value in ident]
    if fill:
        names.append(b'fixture-padding')
        values.append(b'X' * fill)
    strings = b''
    offsets = []
    for name in names:
        offsets.append(len(strings)); strings += name + b'\0'
    block = struct.pack('>I', 1) + b'\0' * 4
    for offset, value in zip(offsets, values):
        block += struct.pack('>III', 3, len(value), offset) + value
        block += b'\0' * (-len(value) % 4)
    block += struct.pack('>II', 2, 9)
    reserves = struct.pack('>QQ', *reservation) if reservation else b''
    reserves += b'\0' * 16
    structoff = 40 + len(reserves)
    total = structoff + len(block) + len(strings)
    return (struct.pack('>10I', 0xd00dfeed, total, structoff, structoff + len(block),
                        40, 17, 16, 0, len(strings), len(block)) + reserves + block + strings)


def reference(kernel, ramdisk, *, variant='legacy_qcom_v0', cmdline=None, second=b'', page=PAGE):
    cmdline = cmdline or 'androidboot.hardware=qcom androidboot.selinux=permissive maxcpus=4 loglevel=7'
    header = bytearray(page)
    header[:8] = b'ANDROID!'
    struct.pack_into('<10I', header, 8, len(kernel), 0x80008000, len(ramdisk),
                     0x81000000, len(second), 0, 0x80000100, page, 0, 14 << 25)
    encoded = cmdline.encode()
    header[64:64 + min(512, len(encoded))] = encoded[:512]
    header[608:608 + len(encoded[512:])] = encoded[512:]
    digest = hashlib.sha1()
    for data in (kernel, ramdisk, second):
        digest.update(data); digest.update(struct.pack('<I', len(data)))
    if variant == 'legacy_qcom_v0':
        digest.update(struct.pack('<I', 0))
    header[576:596] = digest.digest()
    data = bytes(header)
    for payload in (kernel, ramdisk, second):
        data += payload + b'\0' * (-len(payload) % page)
    return data


def fixture():
    profile = json.loads(PROFILE.read_text())
    trees = [fdt(value) for value in TUPLES]
    ramdisk, archive = first_stage_ramdisk()
    old = gzip.compress(arm64_image(65536, 0x1d00000, noisy=True), mtime=0) + b''.join(trees)
    image = arm64_image()
    inputs = dict(reference=reference(old, ramdisk), image_gz=gzip.compress(image, mtime=0),
                  image=image, dtbs=[trees[i] for i in (2, 0, 3, 1)], profile=profile, config=CONFIG)
    return inputs, ramdisk, archive, trees


def independent_roundtrip(data, report, inputs, ramdisk, archive, ordered, variant):
    assert data[:8] == b'ANDROID!'
    ks, ka, rs, ra, ss, sa, ta, page, version, osver = struct.unpack_from('<10I', data, 8)
    assert version == 0 and page == PAGE and ss == sa == 0 and osver == 14 << 25
    profile = inputs['profile']
    assert (ka, ra, ta) == (profile['kernel_addr'], profile['ramdisk_addr'], profile['tags_addr'])
    kernel_offset = page
    ramdisk_offset = page + aligned(ks, page)
    assert len(data) == ramdisk_offset + aligned(rs, page)
    assert not any(data[1632:page])
    assert not any(data[kernel_offset + ks:ramdisk_offset])
    assert not any(data[ramdisk_offset + rs:])
    kernel = data[kernel_offset:kernel_offset + ks]
    packed_ramdisk = data[ramdisk_offset:ramdisk_offset + rs]
    assert packed_ramdisk == ramdisk and lzma.decompress(packed_ramdisk) == archive
    decoder = zlib.decompressobj(31)
    image = decoder.decompress(kernel)
    assert decoder.eof and image == inputs['image']
    assert decoder.unused_data == b''.join(ordered)
    offset = 0
    for tree in ordered:
        magic, size = struct.unpack_from('>II', decoder.unused_data, offset)
        assert magic == 0xd00dfeed and size == len(tree)
        offset += size
    assert offset == len(decoder.unused_data)
    assert image[56:60] == b'ARM\x64'
    text, runtime = struct.unpack_from('<QQ', image, 8)
    assert ka - text == profile['base_address'] and (ka - text) % 0x200000 == 0
    ranges = [(ka, ka + max(runtime, len(image))), (ra, ra + rs),
              (ta, ta + profile['dtb_workspace_capacity_before_ramdisk'])]
    for index, (start, end) in enumerate(ranges):
        assert start < end
        for other_start, other_end in ranges[index + 1:]:
            assert end <= other_start or other_end <= start
    command = (data[64:576].split(b'\0')[0] + data[608:1632].split(b'\0')[0]).decode().split()
    assert command.count('maxcpus=2') == 1
    assert command.count('androidboot.selinux=enforcing') == 1
    assert not any(x in command for x in ['maxcpus=4', 'androidboot.selinux=permissive', 'enforcing=0', 'selinux=0'])
    digest = hashlib.sha1()
    for payload in (kernel, packed_ramdisk, b''):
        digest.update(payload); digest.update(struct.pack('<I', len(payload)))
    if variant == 'legacy_qcom_v0':
        digest.update(struct.pack('<I', 0))
    assert digest.digest() == data[576:596] and not any(data[596:608])
    assert report['image_id_variant'] == variant and report['image_id'] == digest.hexdigest()
    assert report['sha256'] == hashlib.sha256(data).hexdigest() and report['ramdisk_unchanged']
    assert report['ramdisk_sha256'] == hashlib.sha256(ramdisk).hexdigest()
    assert report['image_sha256'] == hashlib.sha256(image).hexdigest()
    assert report['kernel_release'] == '4.19.318'
    assert report['status'] == 'OFFLINE BUILT / HARDWARE UNVERIFIED'
    assert report['boot_test_ready'] is report['booted'] is report['daily_use_ready'] is False
    assert report['layout']['actual_loader_relocation_verified'] is False
    assert len(data) <= len(inputs['reference']) and report['partition_size_inferred'] is False


def rejected(producer, inputs, label, expected=None):
    try:
        producer.assemble(**inputs)
    except (ValueError, KeyError, TypeError, struct.error) as error:
        if expected:
            assert expected in str(error), (label, str(error), expected)
        return
    raise AssertionError('unsafe packaging case accepted: ' + label)


def checks(producer, *, cli=True):
    inputs, ramdisk, archive, trees = fixture()
    positive, negative = 0, 0
    for variant in ('legacy_qcom_v0', 'aosp_v0'):
        current = dict(inputs)
        # Independently reconstruct the two real ID conventions.
        old_kernel = current['reference'][PAGE:PAGE + struct.unpack_from('<I', current['reference'], 8)[0]]
        current['reference'] = reference(old_kernel, ramdisk, variant=variant)
        data, report = producer.assemble(**current)
        independent_roundtrip(data, report, current, ramdisk, archive, trees, variant)
        positive += 1
    def fail(label, change, expected=None):
        nonlocal negative
        altered = copy.deepcopy(inputs)
        change(altered)
        rejected(producer, altered, label, expected)
        negative += 1
    def ref_byte(at, value):
        def change(params):
            data = bytearray(params['reference']); data[at] = value; params['reference'] = bytes(data)
        return change
    fail('inconsistent reference ID', ref_byte(576, inputs['reference'][576] ^ 1))
    fail('missing reference ID', lambda p: p.update(reference=p['reference'][:576] + b'\0' * 32 + p['reference'][608:]))
    fail('vendor header extension', ref_byte(1632, 1))
    fail('nonzero reference ID padding', ref_byte(596, 1))
    def reference_cmdline(params, line):
        size = struct.unpack_from('<I', params['reference'], 8)[0]
        old = params['reference'][PAGE:PAGE + size]
        params['reference'] = reference(old, ramdisk, cmdline=line)
    fail('ambiguous non-security cmdline key', lambda p: reference_cmdline(p, 'loglevel=1 loglevel=7'))
    fail('control character in cmdline', lambda p: reference_cmdline(p, 'loglevel=7\nselinux=0'))
    fail('enforcing cmdline outgrows slots', lambda p: reference_cmdline(p, 'x=' + 'a' * 1508))
    fail('alternate init bypasses ramdisk first-stage', lambda p: reference_cmdline(p, 'init=/bin/sh'))
    fail('alternate rdinit bypasses ramdisk first-stage', lambda p: reference_cmdline(p, 'rdinit=/bin/sh'))
    fail('nonzero reference kernel padding', lambda p: ref_byte(PAGE + struct.unpack_from('<I', p['reference'], 8)[0], 1)(p))
    fail('nonzero reference ramdisk padding', lambda p: ref_byte(PAGE + aligned(struct.unpack_from('<I', p['reference'], 8)[0]) + len(ramdisk), 1)(p))
    fail('unidentified nonzero footer', lambda p: p.update(reference=p['reference'] + b'AVB-or-vendor-fixture'))
    fail('mismatched Image', lambda p: p.update(image=p['image'] + b'X'))
    fail('appended trees in standalone gzip', lambda p: p.update(image_gz=p['image_gz'] + trees[0]))
    fail('truncated gzip', lambda p: p.update(image_gz=p['image_gz'][:-1]))
    fail('extra gzip member', lambda p: p.update(image_gz=p['image_gz'] + p['image_gz']))
    fail('missing fourth new variant', lambda p: p.update(dtbs=p['dtbs'][:3]))
    fail('duplicate new hardware tuple', lambda p: p.update(dtbs=[trees[0], trees[0], trees[2], trees[3]]))
    fail('foreign hardware tuple', lambda p: p.update(dtbs=[trees[0], trees[1], trees[2], fdt(((999, 1), (8, 0), (1, 2, 0, 0)))]))
    fail('fixed reservation in runtime BSS', lambda p: p.update(dtbs=[fdt(TUPLES[0], (p['profile']['kernel_addr'] + 0x10000, 4096)), *trees[1:]]))
    fail('fixed reservation in decompression staging', lambda p: p.update(dtbs=[fdt(TUPLES[0], (p['profile']['scratch_addr'] + PAGE, 4096)), *trees[1:]]))
    fail('DT workspace growth overflow', lambda p: p.update(dtbs=[fdt(TUPLES[0], fill=0x210000), *trees[1:]]))
    fail('wrong layout profile ID', lambda p: p['profile'].update(profile_id='wrong'))
    fail('missing source provenance', lambda p: p['profile'].update(source_provenance=[]))
    fail('altered source provenance hash', lambda p: p['profile']['source_provenance'][0].update(sha256='0' * 64))
    fail('unsubstantiated hardware grant', lambda p: p['profile'].update(hardware_verified=True))
    fail('unverified-firmware flag removed', lambda p: p['profile'].pop('firmware_match_unverified'))
    fail('negative source growth padding', lambda p: p['profile'].update(source_dtb_pad_bytes=-1))
    fail('changed kernel destination', lambda p: p['profile'].update(kernel_addr=p['profile']['kernel_addr'] + 0x1000))
    fail('unknown GPT capacity too small', lambda p: p.update(partition_size=4096))
    fail('independent observed GPT cap', lambda p: p.update(partition_size=len(p['reference']) - 1))
    for symbol in ['ARM64', 'RD_XZ', 'MMC_BLOCK', 'EFI_PARTITION', 'SECURITY_SELINUX']:
        fail('missing built-in ' + symbol, lambda p, symbol=symbol: p.update(config=p['config'].replace('CONFIG_' + symbol + '=y\n', '')))
    fail('unqualified DVFS', lambda p: p.update(config=p['config'].replace('# CONFIG_CPU_FREQ is not set\n', 'CONFIG_CPU_FREQ=y\n')))
    fail('unqualified idle', lambda p: p.update(config=p['config'].replace('# CONFIG_CPU_IDLE is not set\n', 'CONFIG_CPU_IDLE=y\n')))
    fail('permissive kernel default', lambda p: p.update(config=p['config'].replace('BOOTPARAM_VALUE=1', 'BOOTPARAM_VALUE=0')))
    fail('forced kernel command line', lambda p: p.update(config=p['config'] + 'CONFIG_CMDLINE_FORCE=y\n'))
    fail('conflicting DVFS config', lambda p: p.update(config=p['config'] + 'CONFIG_CPU_FREQ=y\n'))
    fail('conflicting SELinux config', lambda p: p.update(config=p['config'] + 'CONFIG_SECURITY_SELINUX=n\n'))
    def kernel_case(runtime=0x200000, text=0x80000, flags=10, length=8192, noisy=False):
        def change(params):
            params['image'] = arm64_image(length, runtime, text, flags, noisy)
            params['image_gz'] = gzip.compress(params['image'], mtime=0)
        return change
    def release_case(mode):
        def change(params):
            if mode == 'old':
                image = params['image'].replace(b'Linux version 4.19.318 ', b'Linux version 4.4.302 ', 1)
            elif mode == 'missing':
                image = params['image'].replace(b'Linux version ', b'Unknown state ', 1)
            else:
                data = bytearray(params['image'])
                extra = b'Linux version 4.4.302 (conflicting fixture)\0'
                data[128:128 + len(extra)] = extra
                image = bytes(data)
            params.update(image=image, image_gz=gzip.compress(image, mtime=0))
        return change
    for mode in ('old', 'missing', 'conflicting'):
        fail('wrong or ambiguous new kernel release: ' + mode, release_case(mode))
    fail('unknown kernel runtime extent', kernel_case(runtime=0))
    fail('runtime BSS crosses tags workspace', kernel_case(runtime=0x2600000))
    fail('wrong Image text_offset', kernel_case(text=0x8000))
    fail('big-endian Android kernel', kernel_case(flags=11))
    fail('16KiB Image granule', kernel_case(flags=4))
    fail('64KiB Image granule', kernel_case(flags=6))
    fail('unknown new Image granule', kernel_case(flags=0))
    fail('result exceeds working-reference file cap', kernel_case(length=131072, noisy=True))
    if cli:
        with tempfile.TemporaryDirectory(prefix='dora-boot-cli-') as temporary:
            folder = Path(temporary)
            paths = dict(reference=folder/'reference.img', image_gz=folder/'Image.gz', image=folder/'Image',
                         profile=folder/'profile.json', config=folder/'config')
            for key in ['reference', 'image_gz', 'image']:
                paths[key].write_bytes(inputs[key])
            paths['profile'].write_text(json.dumps(inputs['profile']))
            paths['config'].write_text(inputs['config'])
            dtbs = []
            for index, tree in enumerate(inputs['dtbs']):
                path = folder/('tree%d.dtb' % index); path.write_bytes(tree); dtbs.append(path)
            def cli_case(label, output, report, extra=None, expected=None):
                nonlocal negative
                command = [sys.executable, str(PRODUCER), '--offline-candidate',
                    '--reference', str(paths['reference']), '--image-gz', str(paths['image_gz']),
                    '--image', str(paths['image']), '--layout-profile', str(paths['profile']),
                    '--config', str(paths['config']), '--output', str(output), '--report', str(report)]
                for tree in dtbs: command += ['--dtb', str(tree)]
                if extra: command += extra
                before = {path: path.read_bytes() for path in folder.iterdir() if path.is_file() and not path.is_symlink()}
                result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                assert result.returncode and (expected is None or expected in result.stdout + result.stderr), (label, result.stdout, result.stderr)
                assert all(path.read_bytes() == data for path, data in before.items()), label
                negative += 1
            cli_case('output aliases reference', paths['reference'], folder/'report.json', expected='aliases')
            cli_case('report aliases reference', folder/'new.img', paths['reference'], expected='aliases')
            cli_case('report aliases output', folder/'same.img', folder/'same.img', expected='aliases')
            existing = folder/'existing.img'; existing.write_bytes(b'do not replace')
            cli_case('existing output', existing, folder/'report.json', expected='existing')
            symlink = folder/'output-link'; symlink.symlink_to(paths['reference'])
            cli_case('output symlink aliases input', symlink, folder/'report.json', expected='aliases')
            dangling = folder/'dangling'; dangling.symlink_to(folder/'absent')
            cli_case('dangling output symlink', dangling, folder/'report.json')
            assert not (folder/'absent').exists(), 'dangling symlink output target created'
            cli_case('missing report parent', folder/'missing-parent-output.img', folder/'missing'/'report.json', expected='directory')
            assert not (folder/'missing-parent-output.img').exists()
            cli_case('duplicate input inode path', folder/'new.img', folder/'report.json',
                     extra=['--image', str(paths['reference'])], expected='aliases')
            source_output = ROOT/'scripts/dora/packaging-test-must-not-write.img'
            assert not source_output.exists()
            cli_case('private output inside Git', source_output, folder/'report.json', expected='outside')
            assert not source_output.exists()
            # Actual production output lifecycle, independent of CLI DT qualification.
            output, report_path = folder/'exclusive.img', folder/'exclusive-report.json'
            producer.write_outputs(output, b'fixture', report_path, {'fixture': True})
            assert output.read_bytes() == b'fixture' and json.loads(report_path.read_text()) == {'fixture': True}
            try:
                producer.write_outputs(output, b'overwritten', folder/'second-report.json', {})
            except (ValueError, OSError):
                pass
            else:
                raise AssertionError('exclusive output writer overwrote an existing file')
            assert output.read_bytes() == b'fixture' and not (folder/'second-report.json').exists()
            negative += 1
            new_image = folder/'must-rollback.img'
            try:
                producer.write_outputs(new_image, b'fixture', report_path, {})
            except (ValueError, OSError):
                pass
            else:
                raise AssertionError('exclusive report creation accepted existing report')
            assert not new_image.exists() and json.loads(report_path.read_text()) == {'fixture': True}
            negative += 1
            for fail_on, race in [(1, False), (2, False), (1, True)]:
                new_image, new_report = folder/'write-failure.img', folder/'write-failure.json'
                calls = 0
                real_fsync = producer.os.fsync
                def fail_sync(fd):
                    nonlocal calls
                    calls += 1
                    if calls == fail_on:
                        if race:
                            new_image.unlink(); new_image.write_bytes(b'raced replacement')
                        raise OSError('injected fsync failure')
                    return real_fsync(fd)
                producer.os.fsync = fail_sync
                try:
                    producer.write_outputs(new_image, b'fixture', new_report, {})
                except OSError:
                    pass
                else:
                    raise AssertionError('injected writer failure returned success')
                finally:
                    producer.os.fsync = real_fsync
                assert not new_report.exists()
                if race:
                    assert new_image.read_bytes() == b'raced replacement'
                    new_image.unlink()
                else:
                    assert not new_image.exists()
                negative += 1
            class FaultStream:
                def __init__(self, stream, write_failure):
                    self.stream, self.write_failure = stream, write_failure
                def fileno(self): return self.stream.fileno()
                def write(self, data):
                    if self.write_failure: raise OSError('injected payload write failure')
                    return self.stream.write(data)
                def flush(self): return self.stream.flush()
                def close(self):
                    self.stream.close()
                    raise OSError('injected close failure')
            class FaultPath:
                def __init__(self, path, write_failure):
                    self.path, self.write_failure = path, write_failure
                def open(self, mode): return FaultStream(self.path.open(mode), self.write_failure)
                def lstat(self): return self.path.lstat()
                def unlink(self): return self.path.unlink()
            for write_failure in (False, True):
                new_image, new_report = folder/'close-failure.img', folder/'close-failure.json'
                try:
                    producer.write_outputs(FaultPath(new_image, write_failure), b'fixture', new_report, {})
                except OSError as error:
                    assert ('payload write' if write_failure else 'close') in str(error), 'first writer error was lost'
                else:
                    raise AssertionError('stream close failure returned success')
                assert not new_image.exists() and not new_report.exists(), 'write/close failure retained partial artifacts'
                negative += 1
    return dict(positive_roundtrips=positive, negative_cases=negative,
                actual_assembler_executed=True, independent_raw_verification=True,
                private_inputs_used=False, hardware_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    producer = module()
    result = checks(producer)
    mutations = []
    if args.self_test:
        source = PRODUCER.read_text()
        changes = [
            ('ignore-Image-mismatch', "require(boot.decompress(image_gz, boot.MAX_KERNEL, 'kernel')[1] == image, 'Image and Image.gz disagree')", 'pass'),
            ('reverse-reference-DTB-order', 'for old in old_trees:', 'for old in old_trees[::-1]:'),
            ('raise-CPU-limit', "result += ['androidboot.selinux=enforcing', 'maxcpus=2']", "result += ['androidboot.selinux=enforcing', 'maxcpus=4']"),
        ]
        for name, before, after in changes:
            assert source.count(before) == 1, name
            try:
                checks(module(source.replace(before, after, 1)), cli=False)
            except (AssertionError, ValueError):
                mutations.append(name)
            else:
                raise AssertionError('unsafe producer mutation was not detected: ' + name)
    result['unsafe_producer_mutations_rejected'] = mutations
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
    print('PASS: actual offline assembler, independent header/ID/gzip/ramdisk/DTB/memory round trips; '
          '%d positive and %d negative cases' % (result['positive_roundtrips'], result['negative_cases']))
    if mutations: print('PASS: %d unsafe producer mutations rejected' % len(mutations))
    print('Host-only synthetic fixtures; no Sony firmware, physical memory, eMMC or Android runtime qualification.')


if __name__ == '__main__':
    main()
