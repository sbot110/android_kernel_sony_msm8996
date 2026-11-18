#!/usr/bin/env python3
"""Exercise the real preflight parser, target admission and no-write boundaries.

Uses actual Stage 11 files and synthetic ADB responses. No phone is contacted,
no hardware gate is promoted, and a host PASS never qualifies Test A or B.
"""
import argparse
from pathlib import Path
import json
import os
import struct
import subprocess
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/dora/dora-stage12-hardware-preflight.sh'
code = SCRIPT.read_text().split("<<'PY_STAGE12'\n", 1)[1].rsplit('\nPY_STAGE12', 1)[0]
api = {'__name__': 'stage12_host_test'}
exec(compile(code, str(SCRIPT), 'exec'), api)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts-dir', type=Path, default=Path('/workspace/stage11-artifacts'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    checked = []

    def reject(name, function, *values):
        try:
            function(*values)
        except (ValueError, OSError, struct.error):
            checked.append(name)
            return
        raise AssertionError('unsafe admission: ' + name)

    images = {label: (args.artifacts_dir / entry[0]).read_bytes() for label, entry in api['PINS'].items()}
    for label, data in images.items():
        metadata, parts = api['inspect_image'](data, label)
        assert metadata['kernel_release'] == api['RELEASE'] and len(metadata['dtbs']) == 4
        assert api['sha'](data) == api['PINS'][label][1]
    a = images['A']
    for name, offset, value in (('magic', 0, b'INVALID!'), ('page', 36, struct.pack('<I', 2048)),
                                ('QCOM-ID', 576, b'\0' * 20)):
        bad = bytearray(a); bad[offset:offset + len(value)] = value
        reject(name, api['boot_sections'], bytes(bad))
    reject('truncated', api['boot_sections'], a[:-4096])
    reject('unknown-tail', api['boot_sections'], a + b'\1')
    bad = bytearray(a); struct.pack_into('<I', bad, 20, 0x82200000)
    reject('wrong-candidate-address-with-valid-ID', api['inspect_image'], bytes(bad), 'A')
    reject('bad-FDT', api['fdt_nodes'], b'\0' * 40)
    reject('changed-ramdisk', api['check_ramdisk'], b'invalid')
    state = dict(capture_kind='read-only-adb', device='dora', boot_path='/dev/block/mmcblk0p42',
                 partname='boot', boot_bytes=67108864, release='4.4.302+', boot_completed='1', lineage='21.0-test')
    assert api['checked_target'](state) == 67108864
    for key, value in (('partname', 'FOTAKernel'), ('partname', 'userdata'),
                       ('boot_path', '/dev/block/mmcblk0'), ('boot_path', '/dev/block/sda1'),
                       ('boot_bytes', 11051008), ('boot_bytes', True), ('device', 'other'), ('release', 44)):
        reject('target-' + key + '-' + str(value), api['checked_target'], dict(state, **{key: value}))
    reject('non-object-capture', api['checked_target'], [])
    for ending in (b'\0' * 10, b'old DTB\0data'):
        info = api['tail_info'](b'\0' * api['IMAGE_BYTES'] + ending)
        assert info['nonzero_bytes'] == len(ending) - ending.count(0)
        assert not info['short_write_safe'] and not info['automatic_zero_fill_safe']
    checked.append('zero-and-nonzero-tail-never-grant-write-policy')

    class FakeADB:
        def __init__(self, partname='boot', uid='0', device='dora', capacity=67108864, raw=b'short'):
            self.calls = []; self.partname = partname; self.uid = uid
            self.device = device; self.capacity = capacity; self.raw = raw

        def __call__(self, argv, **options):
            assert argv[:3] == ['fake-adb', '-s', 'TEST_SERIAL']
            words = argv[3:]; self.calls.append(words)
            allowed = {
                ('get-state',): b'device\n',
                ('shell', 'getprop', 'ro.product.device'): self.device.encode(),
                ('shell', 'getprop', 'ro.product.model'): b'F8132',
                ('shell', 'uname', '-r'): b'4.4.302+',
                ('shell', 'getprop', 'sys.boot_completed'): b'1',
                ('shell', 'getprop', 'ro.lineage.version'): b'21.0-reference',
                ('shell', 'getprop', 'ro.bootloader'): api['S1'].encode(),
                ('shell', 'getprop', 'ro.boot.bootreason'): b'host-only-reference',
                ('shell', 'id', '-u'): self.uid.encode(),
                ('shell', 'readlink', '-f', '/dev/block/bootdevice/by-name/boot'): b'/dev/block/mmcblk0p42',
                ('shell', 'cat', '/sys/class/block/mmcblk0p42/uevent'): ('PARTNAME=' + self.partname).encode(),
                ('shell', 'cat', '/sys/class/block/mmcblk0p42/size'): str(self.capacity // 512).encode(),
                ('shell', 'blockdev', '--getsize64', '/dev/block/mmcblk0p42'): str(self.capacity).encode(),
                ('exec-out', 'tar', '-C', '/sys/fs/pstore', '-cf', '-', '.'): b'host tar boundary',
                ('exec-out', 'cat', '/proc/cmdline'): b'private reference cmdline',
                ('exec-out', 'cat', '/sys/firmware/fdt'): b'host FDT boundary',
                ('shell', 'dmesg'): b'host log boundary',
                ('exec-out', 'cat', '/proc/iomem'): b'host memory map boundary',
                ('exec-out', 'cat', '/proc/interrupts'): b'host IRQ counter boundary',
                ('exec-out', 'cat', '/dev/block/mmcblk0p42'): self.raw,
            }
            if words == ['exec-out', 'cat', '/proc/dora_boot_trace']:
                return SimpleNamespace(returncode=1, stdout=b'', stderr=b'not present')
            assert tuple(words) in allowed, 'unexpected command: ' + repr(words)
            return SimpleNamespace(returncode=0, stdout=allowed[tuple(words)], stderr=b'')

    with tempfile.TemporaryDirectory(prefix='dora-stage12-host-') as tmp:
        tmp = Path(tmp)
        for name, fake, should_read in (
                ('FOTAKernel', FakeADB(partname='FOTAKernel'), False),
                ('wrong-phone', FakeADB(device='other'), False),
                ('wrong-capacity', FakeADB(capacity=33554432), False)):
            dest = tmp / name; dest.mkdir()
            opts = SimpleNamespace(collect_adb='TEST_SERIAL', adb=Path('fake-adb'), read_boot=True)
            reject('ADB-' + name, api['collect_adb'], opts, dest, fake)
            assert (['exec-out', 'cat', '/dev/block/mmcblk0p42'] in fake.calls) == should_read
        for name, fake, should_read in (
                ('unprivileged', FakeADB(uid='2000'), False),
                ('short-current-read', FakeADB(), True)):
            dest = tmp / name; dest.mkdir()
            opts = SimpleNamespace(collect_adb='TEST_SERIAL', adb=Path('fake-adb'), read_boot=True)
            captured = api['collect_adb'](opts, dest, fake)
            assert captured.get('raw_boot_read_error') and 'current_boot_sha256' not in captured
            assert not (dest / 'current-boot-readonly.img').exists()
            assert (dest / 'device-state.json').is_file()
            assert (['exec-out', 'cat', '/dev/block/mmcblk0p42'] in fake.calls) == should_read
            checked.append('ADB-' + name + '-preserves-metadata-without-usable-dump')
        opts = SimpleNamespace(collect_adb='TEST_SERIAL;reboot', adb=Path('fake-adb'), read_boot=True)
        fake = FakeADB(); reject('serial-injection', api['collect_adb'], opts, tmp, fake); assert not fake.calls
        opts.collect_adb = 'TEST_SERIAL'; opts.read_boot = False
        fake = FakeADB(); dest = tmp / 'valid-metadata'; dest.mkdir()
        captured = api['collect_adb'](opts, dest, fake)
        assert captured['partname'] == 'boot' and captured['model'] == 'F8132'
        assert ['exec-out', 'cat', '/dev/block/mmcblk0p42'] not in fake.calls
        checked.append('metadata-only-never-reads-raw-partition')
        marker = tmp / 'unexpected-device-call'
        witness = tmp / 'adb-witness'
        witness.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 77\n'); witness.chmod(0o700)
        operator = tmp / 'operator.json'
        operator.write_text(json.dumps({'source': 'user_statement', 'current_4_4_boots': True,
                                        'twrp_recently_checked': True, 'risk_accepted': True,
                                        'cpu_safe': True, 'boot_test_ready': True}))
        base = ['bash', str(SCRIPT), '--artifacts-dir', str(args.artifacts_dir), '--adb', str(witness),
                '--operator-state', str(operator), '--backup', str(tmp / 'absent-backup')]
        run = subprocess.run(base, capture_output=True, text=True)
        result = json.loads(run.stdout)
        assert run.returncode == 2 and result['status'] == 'STOP' and not marker.exists()
        checks = {item['check']: item for item in result['checks']}
        for gate in ('S1_memory_handoff', 'CPU_handoff', 'watchdog_handoff', 'write_extent_and_tail_policy', 'diagnostic_transport'):
            assert checks[gate]['status'] == 'STOP'
        assert checks['recovery_access']['status'] == checks['current_4_4_user_confirmation']['status'] == 'PASS'
        assert not result['boot_test_ready'] and not result['risk_acceptance_can_qualify_hardware']
        checked.append('real-cli-reported-recovery-does-not-override-hardware-STOP')
        for option in ('--force', '--allow-unsafe', '--flash', '--approve'):
            run = subprocess.run(base + [option], capture_output=True, text=True)
            assert run.returncode == 2 and not marker.exists()
            checked.append('no-risk-override-' + option)
        operator.write_text('[]')
        run = subprocess.run(base, capture_output=True, text=True)
        assert run.returncode == 2 and json.loads(run.stdout)['status'] == 'STOP'
        checked.append('malformed-confirmation-fails-closed')
        sentinel = tmp / 'preserve.txt'; sentinel.write_text('untouched')
        run = subprocess.run(base + ['--output-dir', str(tmp)], capture_output=True, text=True)
        assert run.returncode == 2 and sentinel.read_text() == 'untouched' and not marker.exists()
        checked.append('existing-output-dir-preserved')
        tool = tmp / 'unreviewed.py'; tool.write_text('raise RuntimeError("must not execute")')
        run = subprocess.run(base + ['--aosp-unpacker', str(tool)], capture_output=True, text=True)
        result = json.loads(run.stdout)
        assert next(c for c in result['checks'] if c['check'] == 'AOSP_independent_unpack')['status'] == 'STOP'
        checked.append('unreviewed-unpacker-refused-before-execution')
    report = dict(status='PASS: HOST TESTS ONLY', cases=len(checked), checked=checked,
                  hardware_test_A='NOT EXECUTED', hardware_test_B='NOT EXECUTED', boot_test_ready=False)
    if args.report:
        with args.report.open('x') as stream:
            stream.write(json.dumps(report, indent=2) + '\n')
    print('PASS:', len(checked), 'preflight parser/admission/no-write checks; hardware qualification absent')


if __name__ == '__main__':
    main()
