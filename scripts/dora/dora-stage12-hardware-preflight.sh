#!/usr/bin/env bash
# Stage 12: read-only preflight. Never flashes, boots, reboots or mounts a phone.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export DORA_STAGE12_SCRIPT_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"
exec python3 - "$@" <<'PY_STAGE12'
"""Check real Stage 11 files and collect bounded, opt-in read-only ADB evidence.

Default: files -> memory -> JSON stdout, no device command or persistent output.
--output-dir creates a NEW private host directory. --collect-adb is explicit.
No CLI argument can qualify unknown Sony S1/CPU/watchdog hardware contracts.
"""
import argparse
import datetime
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zlib

BRANCH = 'kernel-4.19-701905467803253630'
STAGE11_HEAD = '30102a011d83b4dd0d30f3d47add10fba5fe45bb'
SOURCE_SHA = '1b8871be2d2ce04f7361816fa90aa686e5ece1a3'
RELEASE = '4.19.318-dora-4.19+'
S1 = '1299-4832_S1_Boot_MSM8996_LA2.0_O_204'
IMAGE_BYTES = 11051008
BACKUP_BYTES = 67108864
BACKUP_SHA = '07200b77a75f989533a477da355544dd25f1be99ee8c763df7ca75e5108d3178'
RAMDISK_SHA = '9a532793e779b33bad98df1576ec9b661bc04457f4f3f9148b31ba8d42d4f41e'
INIT_SHA = '74f3afc4adce6e0f0ad02993e7701e353c0d7777c935e417fca8b4c19edbb5c5'
KERNEL_SHA = '725a60106736e6eeabc45f93a0178e6a57d7f5958679d44c1414038252a146d2'
IMAGE_SHA = 'df80903f8168bd233559836d283e409266344faad1bdc627cc57afa1e8de15df'
MAP_SHA = '3bcdc40ee664d02f0329cede550d1d25058273fdcafd7389fee587720bd2979c'
AOSP_SHA = 'a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308'
QCOM_ID = '35afd6abb3972411a1bbe916a87557342ef46380'
PINS = {
    'A': ('boot-dora-lineage21-kernel419-stage11.img',
          'c7a248e58f04fc72c4640d144f5e6f1a47dc32c85c50f020cefd3cae2ea9dfd2', 0x82700000),
    'B': ('boot-dora-lineage21-kernel419-stage11-initrd822-experiment.img',
          '3c33a57da1c473cf239dbd9fdc0b3cad26206461b4ba8151c371c86d60941c18', 0x82200000),
}
DTB_SHAS = (
    '3b9f15d92beba0234c34465754a9a5f30b6ba8287b9a11600c63645404bd2d9f',
    'db5479cdaba00d4111c3bf24ad7093c384f7b866d42fb0bce1d16e2832bc8d17',
    '04e9dc339d8dfac76704463c83875419a43fdc806d15fca1e5c33a4ea998f250',
    'adb380173e6148c2281a0b3761934294eb0d67259be54703d3197d148ad855e1',
)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path, limit, exact=None):
    path = Path(path)
    require(stat.S_ISREG(path.stat().st_mode), 'not a regular host file: ' + str(path))
    size = path.stat().st_size
    require(size <= limit and (exact is None or size == exact), 'wrong file size: ' + str(path))
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    require(len(data) == size and len(data) <= limit, 'file changed while reading')
    return data


def align(value, unit):
    return (value + unit - 1) // unit * unit


def boot_sections(data, full_partition=False):
    require(len(data) >= 4096 and data[:8] == b'ANDROID!', 'invalid legacy boot magic/header')
    ks, ka, rs, ra, ss, sa, ta, page, dt_size, os_version = struct.unpack_from('<10I', data, 8)
    require(page == 4096 and dt_size == ss == 0, 'unqualified page/second/DT header layout')
    require(ks > 0 and rs > 0, 'missing kernel or ramdisk')
    offset, parts = page, {}
    for name, size in (('kernel', ks), ('ramdisk', rs), ('second', ss)):
        end = offset + size
        require(end <= len(data), 'truncated ' + name)
        parts[name] = data[offset:end]
        padded = align(end, page)
        require(padded <= len(data) and not any(data[end:padded]), 'nonzero/truncated section padding')
        offset = padded
    require(full_partition or offset == len(data), 'unexpected bytes beyond page-padded boot payload')
    digest = hashlib.sha1()
    for name in ('kernel', 'ramdisk', 'second'):
        digest.update(parts[name]); digest.update(struct.pack('<I', len(parts[name])))
    digest.update(struct.pack('<I', 0))
    require(data[576:596] == digest.digest() and not any(data[596:608]), 'legacy QCOM ID mismatch')
    cmdline = data[64:576].split(b'\0', 1)[0] + data[608:1632].split(b'\0', 1)[0]
    return dict(kernel_addr=ka, ramdisk_addr=ra, tags_addr=ta, page_size=page,
                kernel_size=ks, ramdisk_size=rs, second_size=ss, second_addr=sa,
                header_sha256=sha(data[:page]),
                legacy_dt_size=dt_size, payload_padded_end=offset, qcom_id=digest.hexdigest(),
                cmdline_sha256=sha(cmdline), cmdline=cmdline, os_version_word=os_version), parts


def fdt_nodes(data):
    require(len(data) >= 40, 'short FDT')
    magic, total, so, strings_at, reserves_at, version, compat, cpu, strings_size, size = struct.unpack_from('>10I', data)
    require(magic == 0xd00dfeed and total == len(data) and version == 17 and compat <= 17, 'FDT header')
    require(so % 4 == reserves_at % 8 == 0 and so + size <= total and strings_at + strings_size <= total, 'FDT bounds')
    strings, block = data[strings_at:strings_at + strings_size], data[so:so + size]
    nodes, stack, pos, finished = {}, [], 0, False
    while pos + 4 <= len(block):
        token = struct.unpack_from('>I', block, pos)[0]; pos += 4
        if token == 1:
            end = block.find(b'\0', pos); require(end >= pos, 'FDT node name')
            name = block[pos:end].decode('ascii'); pos = align(end + 1, 4)
            path = '/' if not stack else stack[-1].rstrip('/') + '/' + name
            require(path not in nodes, 'duplicate FDT node'); nodes[path] = {}; stack.append(path)
        elif token == 2:
            require(bool(stack), 'FDT stack underflow'); stack.pop()
        elif token == 3:
            require(bool(stack) and pos + 8 <= len(block), 'FDT property header')
            length, name_at = struct.unpack_from('>II', block, pos); pos += 8
            end = strings.find(b'\0', name_at)
            require(0 <= name_at < len(strings) and end >= name_at and pos + length <= len(block), 'FDT property bounds')
            name = strings[name_at:end].decode('ascii')
            require(name not in nodes[stack[-1]], 'duplicate FDT property')
            nodes[stack[-1]][name] = block[pos:pos + length]; pos = align(pos + length, 4)
        elif token == 4:
            continue
        elif token == 9:
            require(not stack, 'unclosed FDT'); finished = True; break
        else:
            raise ValueError('FDT token')
    require(finished, 'FDT end missing')
    return nodes


def cell_values(data):
    require(len(data) % 4 == 0, 'unaligned DT cells')
    return list(struct.unpack('>' + 'I' * (len(data) // 4), data))


def check_ramdisk(data):
    require(len(data) == 1224724 and sha(data) == RAMDISK_SHA, 'working ramdisk differs')
    decoder = lzma.LZMADecompressor(memlimit=64 * 1024 * 1024)
    raw = decoder.decompress(data, max_length=16 * 1024 * 1024)
    require(decoder.eof and not decoder.unused_data, 'bounded XZ/newc stream')
    pos, init = 0, None
    while pos + 110 <= len(raw):
        require(raw[pos:pos + 6] == b'070701', 'ramdisk newc header')
        fields = [int(raw[pos + 6 + n * 8:pos + 14 + n * 8], 16) for n in range(13)]
        length, namesize = fields[6], fields[11]; pos += 110
        require(namesize > 0 and pos + namesize <= len(raw), 'CPIO name bounds')
        name = raw[pos:pos + namesize]; require(name.endswith(b'\0'), 'CPIO NUL name')
        pos = align(pos + namesize, 4); require(pos + length <= len(raw), 'CPIO data bounds')
        if name == b'TRAILER!!!\0':
            require(init is not None and sha(init) == INIT_SHA, 'working /init differs')
            require(init[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<H', init, 18)[0] == 183, 'init architecture')
            return dict(compression='xz', sha256=sha(data), bytes=len(data), init_sha256=sha(init))
        if (name[2:] if name.startswith(b'./') else name) == b'init\0':
            require(init is None, 'duplicate /init'); init = raw[pos:pos + length]
        pos = align(pos + length, 4)
    raise ValueError('CPIO trailer missing')


def inspect_image(data, label):
    require(label in PINS, 'unknown candidate')
    header, parts = boot_sections(data)
    require(header['qcom_id'] == QCOM_ID, 'unexpected QCOM image ID')
    require((header['kernel_addr'], header['tags_addr'], header['ramdisk_addr']) ==
            (0x80080000, 0x82500000, PINS[label][2]), 'wrong address candidate')
    require(sha(parts['kernel']) == KERNEL_SHA, 'wrong kernel payload')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = decoder.decompress(parts['kernel'], 64 * 1024 * 1024)
    require(decoder.eof and not decoder.unconsumed_tail and sha(raw) == IMAGE_SHA, 'expanded Image differs or exceeds bound')
    require(raw[56:60] == b'ARM\x64' and struct.unpack_from('<QQQ', raw, 8) == (0x80000, 22147072, 10), 'ARM64 header')
    require(set(re.findall(rb'Linux version ([^\0\r\n ]+)', raw)) == {RELEASE.encode()}, 'wrong actual kernel release')
    tail, identities = decoder.unused_data, []
    for index in range(4):
        require(len(tail) >= 40, 'missing appended DTB')
        magic, length = struct.unpack_from('>II', tail)
        require(magic == 0xd00dfeed and 40 <= length <= 2 * 1024 * 1024 and length <= len(tail), 'DTB size')
        blob = tail[:length]; tail = tail[length:]
        require(sha(blob) == DTB_SHAS[index], 'wrong DTB/order')
        nodes = fdt_nodes(blob)
        identity = {name: cell_values(nodes['/'][name]) for name in ('qcom,msm-id', 'qcom,board-id', 'qcom,pmic-id')}
        require(identity['qcom,msm-id'] == [246, 0x30000 + index % 2] and identity['qcom,board-id'] == [8, 0] and
                identity['qcom,pmic-id'] == [0x20009, 0x2000a if index < 2 else 0x10013, 0, 0], 'Sony DTB identity')
        require(nodes['/soc/sdhci@7464900']['status'] == b'okay\0', 'SDHC1 not active')
        identity.update(sha256=sha(blob), bytes=length); identities.append(identity)
    require(not tail, 'extra DTB/payload data')
    cmdline = header.pop('cmdline').decode('ascii')
    tokens = cmdline.split()
    require(tokens.count('androidboot.selinux=enforcing') == tokens.count('maxcpus=2') == 1, 'cmdline policy')
    require(not any(t in tokens for t in ('selinux=0', 'enforcing=0', 'nowatchdog', 'nosoftlockup', 'thermal.off=1')), 'protection bypass')
    return dict(sha256=sha(data), bytes=len(data), header=header, kernel_release=RELEASE,
                kernel_payload_sha256=sha(parts['kernel']), kernel_payload_bytes=len(parts['kernel']),
                image_sha256=sha(raw), image_size_with_bss=22147072,
                dtbs=identities, ramdisk=check_ramdisk(parts['ramdisk']), hardware_qualified=False), parts


def tail_info(data):
    tail = data[IMAGE_BYTES:]
    return dict(start=IMAGE_BYTES, bytes=len(tail), nonzero_bytes=len(tail) - tail.count(0), sha256=sha(tail),
                short_write_safe=False, automatic_zero_fill_safe=False)


def checked_target(state):
    require(isinstance(state, dict), 'device-state must be a JSON object')
    require(state.get('capture_kind') == 'read-only-adb', 'no live target capture')
    require(state.get('device') in ('dora', 'lineage_dora'), 'device is not confirmed dora')
    require(re.fullmatch(r'/dev/block/mmcblk0p[1-9][0-9]*', state.get('boot_path', '')) is not None, 'not canonical internal eMMC partition')
    require(state.get('partname') == 'boot', 'target PARTNAME is not boot (FOTAKernel/other targets refused)')
    size = state.get('boot_bytes')
    require(type(size) is int and size == BACKUP_BYTES and size >= IMAGE_BYTES, 'boot capacity/backup extent mismatch')
    for field in ('release', 'boot_completed', 'lineage'):
        require(isinstance(state.get(field), str), 'invalid/missing runtime field: ' + field)
    return size


def capture_provenance(state, live):
    timestamp = state.get('captured_utc')
    require(isinstance(timestamp, str), 'missing capture timestamp')
    captured = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    require(captured.tzinfo is not None, 'capture timestamp must include timezone')
    age = (datetime.datetime.now(datetime.timezone.utc) - captured).total_seconds()
    require(age >= -60, 'capture timestamp is in the future')
    complete = state.get('capture_status') == 'COMPLETE'
    same_boot = (isinstance(state.get('boot_id_before'), str) and
                 state.get('boot_id_before') == state.get('boot_id_after'))
    require(state.get('capture_status') not in ('IN_PROGRESS', 'PARTIAL/REJECTED'), 'capture incomplete/rejected; retain partial evidence for analysis')
    if state.get('format') == 'dora-stage12-device-v2':
        require(complete and same_boot, 'v2 capture lacks completed same-boot evidence')
    return dict(source='live_adb' if live else 'imported_historical_capture', captured_utc=timestamp,
                age_seconds=round(max(age, 0), 1), complete=complete, same_boot_verified=same_boot,
                current_connection_observed=bool(live and complete and same_boot))


def bounded_command(argv, limit, timeout=20):
    """Bound both pipes while reading, including a hung/oversized adb.exe.

    Reader threads work with pipes on both POSIX and Windows. communicate()
    would buffer unbounded output before a caller could check its length.
    """
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    buffers, failures = [bytearray(), bytearray()], []
    overflow = threading.Event()

    def drain(index, stream, bound):
        try:
            while True:
                chunk = stream.read1(min(65536, bound - len(buffers[index]) + 1))
                if not chunk:
                    return
                if len(buffers[index]) + len(chunk) > bound:
                    overflow.set()
                    return
                buffers[index].extend(chunk)
        except OSError as error:
            failures.append(error)
            overflow.set()

    readers = [threading.Thread(target=drain, args=(index, stream, bound), daemon=True)
               for index, stream, bound in ((0, process.stdout, limit), (1, process.stderr, 65536))]
    deadline = time.monotonic() + timeout
    try:
        for reader in readers:
            reader.start()
        while process.poll() is None or any(reader.is_alive() for reader in readers):
            require(not overflow.is_set(), 'read-only command exceeded stdout/stderr bound or pipe failed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            overflow.wait(min(remaining, 0.02))
        require(not overflow.is_set() and not failures, 'read-only command exceeded stdout/stderr bound or pipe failed')
        require(process.returncode == 0, 'read-only command failed')
        return bytes(buffers[0])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=1)
        for reader, stream in zip(readers, (process.stdout, process.stderr)):
            if not reader.is_alive():
                stream.close()


def validate_capture(name, data):
    # exec-out has no reliable remote exit-status framing: cat/tar error text
    # can arrive with host exit 0. A transport success is not artifact evidence.
    if name == 'pstore-before-test.tar':
        require(len(data) >= 1024 and len(data) % 512 == 0 and not any(data[-1024:]), 'invalid pstore tar framing')
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
            for count, member in enumerate(archive, 1):
                require(count <= 16384 and member.size <= 8 * 1024 * 1024, 'pstore tar bounds')
    elif name == 'runtime.fdt':
        fdt_nodes(data)
    elif name == 'dora_boot_trace.bin':
        require(len(data) == 2048 and data[:4] == b'DRB1' and not any(data[4:64]), 'invalid trace size/anchor')
        sequences = set()
        for slot in range(1, 32):
            raw = data[slot * 64:(slot + 1) * 64]
            if not any(raw):
                continue
            magic, version, size, sequence, stage, checksum = struct.unpack_from('<IHHQII', raw)
            committed = struct.unpack_from('<Q', raw, 56)[0]
            require((magic, version, size) == (0x31425244, 1, 64) and stage == slot <= 22, 'invalid trace record identity')
            require(sequence and sequence not in sequences and committed == sequence ^ ((1 << 64) - 1), 'uncommitted/torn trace sequence')
            require(zlib.crc32(raw[:20] + b'\0' * 4 + raw[24:56]) & 0xffffffff == checksum, 'trace CRC32 mismatch')
            sequences.add(sequence)
    else:
        require(bool(data.strip()) and b'\0' not in data, 'empty/binary text capture')
        require(not data.lstrip().startswith((b'cat:', b'dmesg:', b'tar:', b'/system/bin/sh:', b'sh:')), 'remote error text is not a capture')


def collect_adb(args, destination, runner=bounded_command):
    require(destination is not None, '--collect-adb requires a NEW --output-dir')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', args.collect_adb) is not None, 'invalid exact ADB serial')
    executable = args.adb.resolve() if args.adb else shutil.which('adb') or shutil.which('adb.exe')
    require(executable is not None, 'ADB client unavailable; run on the connected WSL host')
    prefix = [str(executable), '-s', args.collect_adb]

    def command(words, binary=False, timeout=20, limit=65536):
        result = runner(prefix + words, limit=limit, timeout=timeout)
        require(len(result) <= limit, 'read-only ADB output bound')
        if binary:
            return result
        return result.decode('utf-8', errors='replace').strip()

    state = dict(format='dora-stage12-device-v2', capture_kind='read-only-adb',
                 capture_status='IN_PROGRESS', captured_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 serial_sha256=sha(args.collect_adb.encode()))

    def persist():
        temporary = destination / 'device-state.json.tmp'
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(destination / 'device-state.json')

    def remember(key, value):
        state[key] = value
        persist()
        return value

    def boot_id():
        value = command(['shell', 'cat', '/proc/sys/kernel/random/boot_id'])
        require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value) is not None, 'invalid/unavailable boot ID')
        return value

    def artifact(name, words, limit=16 * 1024 * 1024):
        try:
            payload = command(words, True, limit=limit)
            validate_capture(name, payload)
            (destination / name).write_bytes(payload)
            remember(name + '_sha256', sha(payload))
        except (OSError, ValueError, struct.error, tarfile.TarError, subprocess.TimeoutExpired) as error:
            remember(name, 'UNAVAILABLE/INVALID: ' + str(error))

    def capture():
        require(command(['get-state']) == 'device', 'ADB device is not online/authorized; no mode switch attempted')
        remember('device', command(['shell', 'getprop', 'ro.product.device']))
        require(state['device'] in ('dora', 'lineage_dora'), 'wrong device; raw capture refused')
        remember('boot_id_before', boot_id())
        # Preserve readable pstore BEFORE A. An empty/invalid archive proves
        # nothing about entry of a different kernel on a previous attempt.
        artifact('pstore-before-test.tar', ['exec-out', 'tar', '-C', '/sys/fs/pstore', '-cf', '-', '.'], 8 * 1024 * 1024)
        for key, words in {
            'device': ['shell', 'getprop', 'ro.product.device'],
            'model': ['shell', 'getprop', 'ro.product.model'],
            'release': ['shell', 'uname', '-r'],
            'boot_completed': ['shell', 'getprop', 'sys.boot_completed'],
            'lineage': ['shell', 'getprop', 'ro.lineage.version'],
            'bootloader': ['shell', 'getprop', 'ro.bootloader'],
            'boot_reason': ['shell', 'getprop', 'ro.boot.bootreason'],
            'uid': ['shell', 'id', '-u'],
        }.items():
            remember(key, command(words))
        require(state['device'] in ('dora', 'lineage_dora'), 'wrong device; partition reads refused')
        for alias in ('/dev/block/bootdevice/by-name/boot', '/dev/block/platform/soc/7464900.sdhci/by-name/boot', '/dev/block/by-name/boot'):
            try:
                path = command(['shell', 'readlink', '-f', alias])
            except ValueError:
                continue
            if re.fullmatch(r'/dev/block/mmcblk0p[1-9][0-9]*', path):
                remember('boot_path', path); remember('boot_alias', alias); break
        require('boot_path' in state, 'canonical boot alias unavailable')
        block = state['boot_path'].rsplit('/', 1)[1]
        uevent = command(['shell', 'cat', '/sys/class/block/' + block + '/uevent'])
        values = dict(line.split('=', 1) for line in uevent.splitlines() if '=' in line)
        remember('partname', values.get('PARTNAME'))
        sectors = command(['shell', 'cat', '/sys/class/block/' + block + '/size'])
        require(sectors.isdecimal(), 'invalid sysfs partition capacity')
        remember('boot_bytes', int(sectors) * 512)
        checked_target(state)  # Never open a partition before these independent checks.
        for name, words in {
            'cmdline.txt': ['exec-out', 'cat', '/proc/cmdline'],
            'runtime.fdt': ['exec-out', 'cat', '/sys/firmware/fdt'],
            'dmesg.txt': ['shell', 'dmesg'],
            'iomem.txt': ['exec-out', 'cat', '/proc/iomem'],
            'interrupts.txt': ['exec-out', 'cat', '/proc/interrupts'],
            'dora_boot_trace.bin': ['exec-out', 'cat', '/proc/dora_boot_trace'],
        }.items():
            artifact(name, words)
        if args.read_boot:
            try:
                require(state['uid'] == '0', 'full boot read requires existing root ADB; no root/remount/reboot is attempted')
                size = command(['shell', 'blockdev', '--getsize64', state['boot_path']])
                require(size.isdecimal() and int(size) == state['boot_bytes'], 'sysfs/blockdev capacity disagreement')
                data = command(['exec-out', 'cat', state['boot_path']], True, 120, state['boot_bytes'])
                require(len(data) == state['boot_bytes'], 'incomplete/oversized current boot read')
                (destination / 'current-boot-readonly.img').write_bytes(data)
                remember('current_boot_sha256', sha(data))
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                # No partial image is saved as a usable restoration backup.
                remember('raw_boot_read_error', str(error))
        remember('boot_id_after', boot_id())
        require(state['boot_id_before'] == state['boot_id_after'], 'boot ID changed during capture; evidence spans different boots')

    persist()
    try:
        capture()
        state['capture_status'] = 'COMPLETE'
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        state['capture_status'] = 'PARTIAL/REJECTED'
        state['capture_error'] = str(error)
        raise
    finally:
        state['capture_finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        persist()
    return state


def evaluate(args):
    checks, images, parts = [], {}, {}
    output = None
    if args.output_dir:
        output = args.output_dir
        output.mkdir(mode=0o700, parents=False, exist_ok=False)

    def check(name, status, reason, evidence=None):
        item = dict(check=name, status=status, reason=reason)
        if evidence is not None:
            item['evidence'] = evidence
        checks.append(item)

    for label, (name, expected_sha, address) in PINS.items():
        try:
            data = read_file(args.artifacts_dir / name, IMAGE_BYTES, IMAGE_BYTES)
            require(sha(data) == expected_sha, 'exact Stage 11 SHA256 mismatch')
            images[label], parts[label] = inspect_image(data, label)
            check('image_' + label, 'PASS', 'real file: SHA/size, independent legacy QCOM/gzip/XZ/CPIO/FDT checks', images[label])
        except (OSError, ValueError, KeyError, UnicodeError, struct.error, zlib.error, lzma.LZMAError) as error:
            check('image_' + label, 'STOP', str(error))
    if len(images) == 2:
        a = read_file(args.artifacts_dir / PINS['A'][0], IMAGE_BYTES, IMAGE_BYTES)
        b = read_file(args.artifacts_dir / PINS['B'][0], IMAGE_BYTES, IMAGE_BYTES)
        differences = [n for n, (x, y) in enumerate(zip(a, b)) if x != y]
        same = all(parts['A'][name] == parts['B'][name] for name in ('kernel', 'ramdisk', 'second'))
        same &= images['A']['header']['cmdline_sha256'] == images['B']['header']['cmdline_sha256']
        check('A_B_differential', 'PASS' if differences == [22] and same else 'STOP',
              'only header ramdisk_addr may differ; does not establish Sony relocation', dict(changed_offsets=differences, same_payload_and_cmdline=bool(same)))
    else:
        check('A_B_differential', 'STOP', 'both real validated inputs are required')
    tool = args.aosp_unpacker
    if tool is None:
        candidate = Path(os.environ.get('DORA_STAGE12_SCRIPT_PATH', '.')).resolve().parent / 'unpack_bootimg.py'
        tool = candidate if candidate.is_file() else None
    if tool is not None and len(parts) == 2:
        try:
            require(sha(read_file(tool, 1024 * 1024)) == AOSP_SHA, 'AOSP unpacker is not the reviewed android-14.0.0_r1 file')
            with tempfile.TemporaryDirectory(prefix='dora12-aosp-') as scratch:
                for label in PINS:
                    target = Path(scratch) / label
                    run = subprocess.run([sys.executable, '-I', '-B', str(tool), '--boot_img',
                                          str(args.artifacts_dir / PINS[label][0]), '--out', str(target)],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
                    require(run.returncode == 0, 'official AOSP unpack failed')
                    for name in ('kernel', 'ramdisk'):
                        require(read_file(target / name, 32 * 1024 * 1024) == parts[label][name], 'AOSP extraction differs: ' + name)
            check('AOSP_independent_unpack', 'PASS', 'both actual images unpacked; kernel and ramdisk byte equality; temporary host files removed')
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            check('AOSP_independent_unpack', 'STOP', str(error))
    else:
        check('AOSP_independent_unpack', 'WARN', 'provide pinned AOSP unpack_bootimg.py; in-memory independent parser still ran')
    try:
        backup = read_file(args.backup, BACKUP_BYTES, BACKUP_BYTES)
        require(sha(backup) == BACKUP_SHA, 'working full64M backup SHA256 mismatch')
        header, ignored = boot_sections(backup, full_partition=True)
        header.pop('cmdline')
        check('working_backup', 'PASS', 'full working backup is available, exact size/SHA verified',
              dict(bytes=len(backup), sha256=sha(backup), header=header, tail_after_candidate=tail_info(backup)))
    except (OSError, ValueError, struct.error) as error:
        check('working_backup', 'STOP', str(error))
    if args.reference:
        try:
            reference = read_file(args.reference, BACKUP_BYTES)
            header, ignored = boot_sections(reference, full_partition=True)
            check('reference_file_tail', 'WARN', 'reference FILE illustration only; not a current partition measurement', tail_info(reference))
        except (OSError, ValueError, struct.error) as error:
            check('reference_file_tail', 'WARN', str(error))
    operator = {}
    if args.operator_state:
        try:
            operator = json.loads(read_file(args.operator_state, 65536))
            require(isinstance(operator, dict), 'operator-state must be a JSON object')
        except (OSError, ValueError) as error:
            operator = {}
            check('operator_statement', 'STOP', str(error))
    reported = operator.get('source') == 'user_statement'
    check('current_4_4_user_confirmation', 'PASS' if reported and operator.get('current_4_4_boots') is True else 'STOP',
          'user statement only; original date retained and current runtime checked separately',
          dict(confirmed=bool(reported and operator.get('current_4_4_boots') is True),
               statement_time_utc=operator.get('recorded_at_utc'), rerun_refreshes_statement=False))
    check('recovery_access', 'PASS' if reported and operator.get('twrp_recently_checked') is True else 'STOP',
          'current TWRP access must be confirmed; historical Stage 65 recovery alone is insufficient',
          dict(source='user_statement' if reported else 'UNAVAILABLE', confirmed=bool(reported and operator.get('twrp_recently_checked') is True),
               statement_time_utc=operator.get('recorded_at_utc'), exact_recovery_check_time=operator.get('exact_recovery_check_time'),
               rerun_refreshes_statement=False))
    state = {}
    try:
        if args.collect_adb:
            state = collect_adb(args, output)
        elif args.device_state:
            state = json.loads(read_file(args.device_state, 1024 * 1024))
        size = checked_target(state)
        provenance = capture_provenance(state, bool(args.collect_adb))
        live = provenance['current_connection_observed']
        check('capture_provenance', 'PASS' if live else 'WARN', 'imported data remains historical; rerunning preflight does not refresh it', provenance)
        check('boot_target_and_capacity', 'PASS' if live else 'WARN', 'captured canonical internal eMMC PARTNAME=boot, sysfs capacity equals full backup extent',
              {k: state.get(k) for k in ('capture_kind', 'serial_sha256', 'boot_path', 'boot_alias', 'partname', 'boot_bytes')})
        stable = state.get('release') == '4.4.302+' and state.get('boot_completed') == '1' and state.get('lineage', '').startswith('21.0-')
        check('current_reference_runtime', ('PASS' if live else 'WARN') if stable else 'STOP', 'captured uname/getprop; imported evidence is not a current runtime observation',
              {k: state.get(k) for k in ('release', 'boot_completed', 'lineage', 'bootloader')})
        check('measured_S1_identity', 'PASS' if state.get('bootloader') == S1 else 'STOP',
              'compare measured bootloader with reference; equality does not qualify its memory/CPU/watchdog contracts',
              dict(measured=state.get('bootloader'), expected=S1, capture_source=provenance['source']))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        if args.collect_adb and output and (output / 'device-state.json').is_file():
            partial = json.loads(read_file(output / 'device-state.json', 1024 * 1024))
            check('capture_provenance', 'STOP', 'partial evidence preserved; no complete current runtime observation',
                  {key: partial.get(key) for key in ('captured_utc', 'capture_finished_utc', 'capture_status', 'capture_error')})
        check('boot_target_and_capacity', 'STOP', str(error))
        check('current_reference_runtime', 'STOP', 'no successful current runtime capture; user statement is preserved separately')
    current = args.boot_dump or (output / 'current-boot-readonly.img' if output and args.read_boot else None)
    if current:
        try:
            checked_target(state)
            data = read_file(current, BACKUP_BYTES, BACKUP_BYTES)
            require(state.get('current_boot_sha256') == sha(data), 'dump not matched to read-only device capture')
            check('current_partition_tail', 'WARN', 'actual dump tail measured; no short-write or zero-fill policy is inferred', tail_info(data))
        except (OSError, ValueError) as error:
            check('current_partition_tail', 'STOP', str(error))
    else:
        check('current_partition_tail', 'STOP', 'no verified full CURRENT boot read; bytes after11051008 remain unknown')
    if args.system_map:
        try:
            require(sha(read_file(args.system_map, 16 * 1024 * 1024)) == MAP_SHA, 'System.map is not the exact Stage 11 build')
            check('trace_symbol_map', 'PASS', 'exact Stage 11 System.map SHA256; not a persistent capture channel')
        except (OSError, ValueError) as error:
            check('trace_symbol_map', 'STOP', str(error))
    else:
        check('trace_symbol_map', 'WARN', 'no exact System.map supplied for possible volatile trace decoding')
    # These are actual unresolved Stage 11 contracts, not operator toggles.
    # No --force / risk acceptance / permissive flag can promote them to PASS.
    for name, reason in (
        ('S1_memory_handoff', 'reviewed Sony S1 load/decompression contract, actual RAM bounds and safe Image/initrd/FDT placement are missing; reaching entry remains a test objective'),
        ('CPU_handoff', 'bootloader CPU/MX/APCC/CBF/CPR/SAW/LMH baseline and preservation/secondary-bringup contract are unqualified; native six-lease DVFS is also incomplete'),
        ('watchdog_handoff', 'firmware watchdog owner, initial state, servicing and legal Linux takeover are unqualified'),
        ('write_extent_and_tail_policy', 'Sony partition-read/write extent and old nonzero tail policy are unqualified; neither short write nor automatic zero-fill is approved'),
        ('diagnostic_transport', 'no reviewed failure-time UART/live reader/pstore preservation before TWRP; stable4.4 ADB alone is insufficient'),
    ):
        check(name, 'STOP', reason)
    check('temporary_fastboot_boot', 'WARN', 'not qualified on this Sony S1; not executed and not assumed equivalent to partition boot')
    severity = {'PASS': 0, 'WARN': 1, 'STOP': 2}
    status = max((item['status'] for item in checks), key=severity.get)
    result = dict(format='dora-stage12-preflight-v1', status=status, checks=checks,
                  image_source_sha=SOURCE_SHA, stage11_published_sha=STAGE11_HEAD, sony_s1_reference=S1,
                  hardware_test_A='NOT EXECUTED', hardware_test_B='NOT EXECUTED', boot_test_ready=False,
                  flash_or_reboot_performed=False, device_partition_writes=False,
                  explicit_user_authorization_is_separate=True, risk_acceptance_can_qualify_hardware=False,
                  result_time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if output:
        (output / 'preflight.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts-dir', type=Path, default=Path('/workspace/stage11-artifacts'))
    parser.add_argument('--backup', type=Path, default=Path('/mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img'))
    parser.add_argument('--reference', type=Path, help='optional regular reference file; never called a current partition dump')
    parser.add_argument('--aosp-unpacker', type=Path, help='pinned official AOSP14 script; unpack uses private temporary HOST files')
    parser.add_argument('--operator-state', type=Path, help='current user confirmations; never a hardware qualification grant')
    parser.add_argument('--system-map', type=Path)
    parser.add_argument('--output-dir', type=Path, help='NEW private HOST directory; existing paths are refused')
    parser.add_argument('--collect-adb', metavar='EXACT_SERIAL', help='explicit opt-in to fixed read-only commands; no mode change/root operation')
    parser.add_argument('--adb', type=Path, help='existing adb/adb.exe executable')
    parser.add_argument('--read-boot', action='store_true', help='opt-in full boot READ using existing root ADB; no dd or partition write')
    parser.add_argument('--device-state', type=Path, help='previous read-only device-state.json; imported evidence, not an attestation')
    parser.add_argument('--boot-dump', type=Path, help='previous full regular dump matched by device-state SHA256')
    args = parser.parse_args()
    parser.error('--read-boot requires --collect-adb') if args.read_boot and not args.collect_adb else None
    parser.error('--collect-adb and --device-state are mutually exclusive') if args.collect_adb and args.device_state else None
    try:
        result = evaluate(args)
    except (OSError, ValueError, KeyError, TypeError, struct.error, subprocess.TimeoutExpired) as error:
        result = dict(status='STOP', reason=str(error), hardware_test_A='NOT EXECUTED', hardware_test_B='NOT EXECUTED',
                      boot_test_ready=False, flash_or_reboot_performed=False)
    print(json.dumps(result, indent=2))
    print('Stage12 preflight: ' + result['status'] + '; Test A/B NOT EXECUTED; no phone writes/reboots.', file=sys.stderr)
    return {'PASS': 0, 'WARN': 1, 'STOP': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
PY_STAGE12
