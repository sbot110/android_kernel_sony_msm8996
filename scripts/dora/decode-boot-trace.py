#!/usr/bin/env python3
"""Verify a raw /proc/dora_boot_trace or authorized live-memory capture.

This decoder never reads a device or assumes a persistent physical address.
An absent/invalid record is no evidence that a boot stage was not reached.
"""
import argparse
import binascii
import json
from pathlib import Path
import struct

STAGES = ('START', 'SETUP_ARCH', 'FDT_VALID', 'MEMBLOCK', 'PAGING_BEGIN',
          'PAGING_DONE', 'PSCI_DONE', 'ARCH_DONE', 'IRQ_BEGIN', 'IRQ_DONE',
          'TIMER_BEGIN', 'TIMER_DONE', 'IRQS_ON', 'KERNEL_INIT', 'SMP_BEGIN',
          'SMP_DONE', 'INITCALL_BEGIN', 'INITCALL_DONE', 'EXEC_INIT', 'EXEC_RESULT',
          'FDT_REJECTED', 'FDT_HEADER')
RECORD = struct.Struct('<IHHQII4QQ')


def decode(data):
    if len(data) != 32 * RECORD.size:
        raise ValueError('expected exactly 32 records of 64 bytes')
    records, sequences = [], set()
    for slot in range(1, 32):
        raw = data[slot * RECORD.size:(slot + 1) * RECORD.size]
        if not any(raw):
            continue
        magic, version, size, sequence, stage, checksum, *rest = RECORD.unpack(raw)
        if (magic, version, size) != (0x31425244, 1, 64):
            raise ValueError('invalid magic/version/size in slot ' + str(slot))
        if stage != slot or not 1 <= stage <= len(STAGES):
            raise ValueError('unknown or mislocated boot stage')
        if not sequence or sequence in sequences or rest[-1] != sequence ^ ((1 << 64) - 1):
            raise ValueError('uncommitted, torn or duplicate sequence')
        covered = raw[:20] + b'\0' * 4 + raw[24:56]
        if binascii.crc32(covered) & 0xffffffff != checksum:
            raise ValueError('CRC32 mismatch in slot ' + str(slot))
        sequences.add(sequence)
        records.append(dict(sequence=sequence, stage=STAGES[stage - 1],
                            args=[hex(x) for x in rest[:4]]))
    return dict(format='DRB1/v1', volatile=True, persistent=False,
                hardware_qualified=False, records=sorted(records, key=lambda r: r['sequence']),
                missing_records_prove_no_entry=False,
                init_exec_proves_android_boot_completed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    try:
        result = decode(args.capture.read_bytes())
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
