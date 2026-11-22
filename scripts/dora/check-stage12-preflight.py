#!/usr/bin/env python3
"""Exercise the real preflight parser, target admission and no-write boundaries.

Uses actual Stage 11 files and synthetic ADB responses. No phone is contacted,
no hardware gate is promoted, and a host PASS never qualifies Test A or B.
"""
import argparse
import datetime
from pathlib import Path
import io
import json
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import zlib

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
        except (ValueError, OSError, struct.error, subprocess.TimeoutExpired):
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

    # These are local Python fixtures, never an ADB process. Exercise the actual
    # pipe reader rather than a mock which already returns bounded output.
    def local_command(program, limit=4096, timeout=3):
        original_popen, spawned = subprocess.Popen, []

        def track_process(*values, **options):
            process = original_popen(*values, **options)
            spawned.append(process)
            return process

        subprocess.Popen = track_process
        try:
            return api['bounded_command']([sys.executable, '-c', program], limit, timeout)
        finally:
            subprocess.Popen = original_popen
            assert len(spawned) == 1
            assert spawned[0].returncode is not None, 'local fixture child was not reaped'
            assert spawned[0].stdout.closed and spawned[0].stderr.closed, 'local fixture pipe leaked'

    for name, program in (
            ('stdout', 'import sys; sys.stdout.buffer.write(b"x" * 4097)'),
            ('stderr', 'import sys; sys.stderr.buffer.write(b"x" * 65537)'),
            ('both-pipes', 'import sys, threading\n'
             't = threading.Thread(target=lambda: sys.stderr.buffer.write(b"e" * 131072))\n'
             't.start()\nsys.stdout.buffer.write(b"o" * 131072)\nt.join()')):
        reject('bounded-command-' + name + '-overflow', local_command, program)
    reject('bounded-command-nonzero-status', local_command, 'raise SystemExit(17)')
    started = time.monotonic()
    reject('bounded-command-timeout', local_command, 'import time; time.sleep(30)', 4096, 0.15)
    assert time.monotonic() - started < 5, 'timeout failed to terminate local fixture promptly'
    assert local_command('import sys; sys.stdout.buffer.write(b"x" * 4096)') == b'x' * 4096
    checked.append('bounded-command-exact-stdout-boundary')
    assert local_command('import sys; sys.stderr.buffer.write(b"e" * 65536)') == b''
    checked.append('bounded-command-exact-stderr-boundary')
    assert local_command('pass', 0) == b''
    checked.append('bounded-command-empty-zero-boundary')
    checked.append('bounded-command-fixture-children-reaped-and-pipes-closed')

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as output:
        payload = b'synthetic host pstore fixture\n'
        member = tarfile.TarInfo('dmesg-ramoops-0'); member.size = len(payload)
        output.addfile(member, io.BytesIO(payload))
    valid_tar = archive.getvalue()
    ignored, a_parts = api['boot_sections'](a)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoder.decompress(a_parts['kernel'])
    dtb_length = struct.unpack_from('>I', decoder.unused_data, 4)[0]
    valid_fdt = decoder.unused_data[:dtb_length]
    trace = bytearray(2048); trace[:4] = b'DRB1'
    record = bytearray(64)
    struct.pack_into('<IHHQII', record, 0, 0x31425244, 1, 64, 1, 1, 0)
    struct.pack_into('<I', record, 20, zlib.crc32(record[:56]) & 0xffffffff)
    struct.pack_into('<Q', record, 56, (1 << 64) - 2)
    trace[64:128] = record
    valid_trace = bytes(trace)
    for name, payload in (('pstore-before-test.tar', valid_tar), ('runtime.fdt', valid_fdt),
                          ('dora_boot_trace.bin', valid_trace)):
        api['validate_capture'](name, payload)
        checked.append('valid-binary-capture-' + name)
    damaged_trace = bytearray(valid_trace); damaged_trace[96] ^= 1
    reject('trace-capture-corrupt-CRC', api['validate_capture'], 'dora_boot_trace.bin', bytes(damaged_trace))
    torn_trace = bytearray(valid_trace); torn_trace[127] ^= 1
    reject('trace-capture-uncommitted-sequence', api['validate_capture'], 'dora_boot_trace.bin', bytes(torn_trace))
    decoder_path = ROOT / 'scripts/dora/decode-boot-trace.py'
    original_decoder = {'__name__': 'stage11_decoder_host_test'}
    exec(compile(decoder_path.read_text(), str(decoder_path), 'exec'), original_decoder)
    decoded = original_decoder['decode'](valid_trace)
    assert decoded['records'] == [dict(sequence=1, stage='START', args=['0x0'] * 4)]
    assert decoded['volatile'] and not decoded['persistent'] and not decoded['hardware_qualified']
    checked.append('original-Stage11-decoder-agrees-on-valid-CRC-trace')
    reject('original-Stage11-decoder-rejects-same-corrupt-CRC', original_decoder['decode'], bytes(damaged_trace))
    reject('original-Stage11-decoder-rejects-same-torn-record', original_decoder['decode'], bytes(torn_trace))

    first_boot_id = '12345678-1234-1234-1234-123456789abc'
    second_boot_id = '87654321-4321-4321-4321-cba987654321'

    class FakeADB:
        def __init__(self, partname='boot', uid='0', device='dora', capacity=67108864,
                     raw=b'short', overrides=None, failure=None, boot_ids=None):
            self.calls = []; self.partname = partname; self.uid = uid
            self.device = device; self.capacity = capacity; self.raw = raw
            self.overrides = overrides or {}; self.failure = failure
            self.boot_ids = list(boot_ids or (first_boot_id, first_boot_id))

        def __call__(self, argv, **options):
            assert argv[:3] == [str(Path('fake-adb').resolve()), '-s', 'TEST_SERIAL']
            assert type(options.get('limit')) is int and options['limit'] >= 0
            assert options.get('timeout', 0) > 0
            words = argv[3:]; self.calls.append(words)
            key = tuple(words)
            if key == self.failure:
                raise ValueError('synthetic read-only transport failure')
            if key in self.overrides:
                return self.overrides[key]
            if words == ['shell', 'cat', '/proc/sys/kernel/random/boot_id']:
                return self.boot_ids.pop(0).encode()
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
                ('exec-out', 'tar', '-C', '/sys/fs/pstore', '-cf', '-', '.'): valid_tar,
                ('exec-out', 'cat', '/proc/cmdline'): b'private reference cmdline',
                ('exec-out', 'cat', '/sys/firmware/fdt'): valid_fdt,
                ('shell', 'dmesg'): b'host log boundary',
                ('exec-out', 'cat', '/proc/iomem'): b'host memory map boundary',
                ('exec-out', 'cat', '/proc/interrupts'): b'host IRQ counter boundary',
                ('exec-out', 'cat', '/dev/block/mmcblk0p42'): self.raw,
            }
            if words == ['exec-out', 'cat', '/proc/dora_boot_trace']:
                # Legacy exec-out can report transport success while remote
                # stderr is merged into stdout. This is not a valid trace.
                return b'cat: /proc/dora_boot_trace: No such file or directory\n'
            assert key in allowed, 'unexpected command: ' + repr(words)
            return allowed[key]

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
            partial = json.loads((dest / 'device-state.json').read_text())
            assert partial['capture_status'] == 'PARTIAL/REJECTED' and partial['capture_error']
        for name, fake, should_read in (
                ('unprivileged', FakeADB(uid='2000'), False),
                ('short-current-read', FakeADB(), True)):
            dest = tmp / name; dest.mkdir()
            opts = SimpleNamespace(collect_adb='TEST_SERIAL', adb=Path('fake-adb'), read_boot=True)
            captured = api['collect_adb'](opts, dest, fake)
            assert captured.get('raw_boot_read_error') and 'current_boot_sha256' not in captured
            assert not (dest / 'current-boot-readonly.img').exists()
            assert (dest / 'device-state.json').is_file()
            assert captured['capture_status'] == 'COMPLETE'
            assert (['exec-out', 'cat', '/dev/block/mmcblk0p42'] in fake.calls) == should_read
            checked.append('ADB-' + name + '-preserves-metadata-without-usable-dump')
        opts = SimpleNamespace(collect_adb='TEST_SERIAL;reboot', adb=Path('fake-adb'), read_boot=True)
        fake = FakeADB(); reject('serial-injection', api['collect_adb'], opts, tmp, fake); assert not fake.calls
        opts.collect_adb = 'TEST_SERIAL'; opts.read_boot = False
        fake = FakeADB(); dest = tmp / 'valid-metadata'; dest.mkdir()
        captured = api['collect_adb'](opts, dest, fake)
        assert captured['partname'] == 'boot' and captured['model'] == 'F8132'
        assert captured['capture_status'] == 'COMPLETE'
        assert captured['boot_id_before'] == captured['boot_id_after'] == first_boot_id
        assert captured['pstore-before-test.tar_sha256'] == api['sha'](valid_tar)
        assert captured['runtime.fdt_sha256'] == api['sha'](valid_fdt)
        assert 'dora_boot_trace.bin_sha256' not in captured
        assert captured['dora_boot_trace.bin'].startswith('UNAVAILABLE/INVALID:')
        assert not (dest / 'dora_boot_trace.bin').exists()
        assert not (dest / 'device-state.json.tmp').exists()
        assert ['exec-out', 'cat', '/dev/block/mmcblk0p42'] not in fake.calls
        checked.append('metadata-only-never-reads-raw-partition')
        checked.append('exec-out-success-error-text-not-saved-as-trace')
        checked.append('explicit-adb-path-resolved-before-runner')

        for name, words, filename, payload in (
                ('pstore-error', ('exec-out', 'tar', '-C', '/sys/fs/pstore', '-cf', '-', '.'),
                 'pstore-before-test.tar', b'tar: /sys/fs/pstore: Permission denied\n'),
                ('fdt-error', ('exec-out', 'cat', '/sys/firmware/fdt'),
                 'runtime.fdt', b'cat: /sys/firmware/fdt: Permission denied\n'),
                ('text-error', ('exec-out', 'cat', '/proc/cmdline'),
                 'cmdline.txt', b'cat: /proc/cmdline: Permission denied\n')):
            dest = tmp / name; dest.mkdir()
            state_error = api['collect_adb'](opts, dest, FakeADB(overrides={words: payload}))
            assert filename + '_sha256' not in state_error and not (dest / filename).exists()
            assert state_error[filename].startswith('UNAVAILABLE/INVALID:')
            checked.append('exec-out-success-' + name + '-not-valid-evidence')
        dest = tmp / 'valid-trace'; dest.mkdir()
        trace_state = api['collect_adb'](opts, dest, FakeADB(overrides={
            ('exec-out', 'cat', '/proc/dora_boot_trace'): valid_trace}))
        assert trace_state['dora_boot_trace.bin_sha256'] == api['sha'](valid_trace)
        assert (dest / 'dora_boot_trace.bin').read_bytes() == valid_trace
        checked.append('valid-CRC-trace-preserved-byte-for-byte')
        dest = tmp / 'interrupted-properties'; dest.mkdir()
        interrupted = FakeADB(failure=('shell', 'uname', '-r'))
        reject('ADB-property-failure-retains-partial-state', api['collect_adb'], opts, dest, interrupted)
        partial = json.loads((dest / 'device-state.json').read_text())
        assert partial['capture_status'] == 'PARTIAL/REJECTED'
        assert partial['device'] == 'dora' and partial['model'] == 'F8132'
        assert partial['capture_error'] and partial['capture_finished_utc']
        assert (dest / 'pstore-before-test.tar').read_bytes() == valid_tar
        assert not (dest / 'device-state.json.tmp').exists()
        dest = tmp / 'boot-changed'; dest.mkdir()
        changed_boot = FakeADB(boot_ids=(first_boot_id, second_boot_id))
        reject('ADB-reboot-during-capture-rejected', api['collect_adb'], opts, dest, changed_boot)
        partial = json.loads((dest / 'device-state.json').read_text())
        assert partial['capture_status'] == 'PARTIAL/REJECTED'
        assert partial['boot_id_before'] != partial['boot_id_after']
        assert partial['runtime.fdt_sha256'] == api['sha'](valid_fdt)

        historical = dict(captured, captured_utc='2000-01-01T00:00:00+00:00')
        provenance = api['capture_provenance'](historical, False)
        assert provenance['source'] == 'imported_historical_capture'
        assert provenance['age_seconds'] > 365 * 86400
        assert not provenance['current_connection_observed']
        checked.append('imported-old-capture-retains-age-and-historical-source')
        for name, fields in (
                ('missing-timestamp', {'captured_utc': None}),
                ('malformed-timestamp', {'captured_utc': 'not-a-date'}),
                ('timezone-missing', {'captured_utc': '2000-01-01T00:00:00'}),
                ('future-timestamp', {'captured_utc': (datetime.datetime.now(datetime.timezone.utc) +
                                                      datetime.timedelta(days=1)).isoformat()}),
                ('changed-boot', {'boot_id_after': second_boot_id}),
                ('missing-boot', {'boot_id_after': None}),
                ('partial', {'capture_status': 'PARTIAL/REJECTED'}),
                ('in-progress', {'capture_status': 'IN_PROGRESS'})):
            reject('capture-provenance-' + name, api['capture_provenance'], dict(historical, **fields), False)

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
        imported = tmp / 'imported-device-state.json'
        imported.write_text(json.dumps(historical))
        run = subprocess.run(base + ['--device-state', str(imported)], capture_output=True, text=True)
        result = json.loads(run.stdout)
        checks = {item['check']: item for item in result['checks']}
        assert run.returncode == 2 and not marker.exists()
        assert checks['capture_provenance']['status'] == 'WARN'
        assert checks['capture_provenance']['evidence']['source'] == 'imported_historical_capture'
        assert checks['capture_provenance']['evidence']['age_seconds'] > 365 * 86400
        assert checks['current_reference_runtime']['status'] == checks['boot_target_and_capacity']['status'] == 'WARN'
        assert checks['measured_S1_identity']['status'] == 'PASS'
        checked.append('real-cli-imported-old-state-never-promoted-to-current-runtime')
        imported.write_text(json.dumps(dict(historical, bootloader='OTHER_SONY_S1')))
        run = subprocess.run(base + ['--device-state', str(imported)], capture_output=True, text=True)
        result = json.loads(run.stdout)
        checks = {item['check']: item for item in result['checks']}
        assert run.returncode == 2 and not marker.exists()
        assert checks['measured_S1_identity']['status'] == 'STOP'
        assert checks['measured_S1_identity']['evidence']['measured'] == 'OTHER_SONY_S1'
        assert checks['current_reference_runtime']['status'] == 'WARN'
        checked.append('real-cli-measured-S1-mismatch-is-separate-STOP')
        imported.write_text(json.dumps(dict(historical, boot_id_after=second_boot_id)))
        run = subprocess.run(base + ['--device-state', str(imported)], capture_output=True, text=True)
        result = json.loads(run.stdout)
        checks = {item['check']: item for item in result['checks']}
        assert run.returncode == 2 and not marker.exists()
        assert checks['boot_target_and_capacity']['status'] == checks['current_reference_runtime']['status'] == 'STOP'
        checked.append('real-cli-mixed-boot-import-rejected')
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
