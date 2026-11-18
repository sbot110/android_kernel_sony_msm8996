#!/usr/bin/env python3
"""Execute the real volatile record writer and independently verify its bytes."""
import importlib.util
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('dora_trace_decoder', HERE / 'decode-boot-trace.py')
decoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decoder)


def block(text, pattern):
    match = re.search(pattern, text, re.M | re.S)
    assert match, pattern
    end, depth = match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


source = (ROOT / 'arch/arm64/kernel/dora-boot-trace.c').read_text()
header = (ROOT / 'include/linux/dora-boot-trace.h').read_text()
structure = re.search(r'struct dora_boot_record \{.*?\n\};', source, re.S)[0]
enum = re.search(r'enum dora_boot_stage \{.*?\n\};', header, re.S)[0]
harness = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef uint8_t u8;
typedef uint16_t __le16;
typedef uint32_t __le32;
typedef uint64_t __le64;
#define notrace
#define DORA_BOOT_MAGIC 0x31425244
#define cpu_to_le16(x) (x)
#define cpu_to_le32(x) (x)
#define cpu_to_le64(x) (x)
#define BUILD_BUG_ON(x) _Static_assert(!(x), "record ABI")
#define WRITE_ONCE(x, y) ((x) = (y))
#define smp_wmb() __sync_synchronize()
enum { SYSTEM_BOOTING, SYSTEM_RUNNING } system_state;
static struct task_struct { int pid; } init_task, init_thread = {1}, worker = {42};
static struct task_struct *current = &init_task;
static u32 crc32_le(u32 crc, const u8 *p, size_t size)
{
    while (size--) {
        unsigned bit;
        crc ^= *p++;
        for (bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (0xedb88320U & -(crc & 1));
    }
    return crc;
}
'''
harness += enum + '\n' + structure + '\n'
harness += 'struct dora_boot_record dora_boot_records[DORA_BOOT_SLOTS];\nstatic u64 dora_boot_sequence;\n'
harness += block(source, r'^void notrace dora_boot_mark\([^;]*?\)\s*\{')
harness += r'''
int main(void)
{
    unsigned stage;
    dora_boot_mark(0, 1, 2, 3, 4);
    dora_boot_mark(DORA_BOOT_SLOTS, 1, 2, 3, 4);
    assert(!dora_boot_sequence);
    for (stage = 1; stage <= DORA_BOOT_SMP_DONE; stage++)
        dora_boot_mark(stage, 0x82200000, 0x8232b014, UINT64_MAX, stage);
    assert(dora_boot_sequence == DORA_BOOT_SMP_DONE);
    current = &worker;
    dora_boot_mark(DORA_BOOT_INITCALL_BEGIN, 123, 0, 0, 0);
    assert(!dora_boot_records[DORA_BOOT_INITCALL_BEGIN].committed);
    current = &init_thread;
    dora_boot_mark(DORA_BOOT_INITCALL_BEGIN, 456, 0, 0, 0);
    dora_boot_mark(DORA_BOOT_INITCALL_DONE, 456, (u64)-5, 0, 0);
    system_state = SYSTEM_RUNNING;
    dora_boot_mark(DORA_BOOT_INITCALL_BEGIN, 789, 0, 0, 0);
    assert(dora_boot_records[DORA_BOOT_INITCALL_BEGIN].args[0] == 456);
    /* PID 1 sets SYSTEM_RUNNING before trying /init. Keep these records. */
    dora_boot_mark(DORA_BOOT_EXEC_INIT, 1, 0, 0, 0);
    dora_boot_mark(DORA_BOOT_EXEC_RESULT, 0, 0, 0, 0);
    assert(dora_boot_records[DORA_BOOT_EXEC_RESULT].committed);
    assert(fwrite(dora_boot_records, sizeof(dora_boot_records), 1, stdout) == 1);
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-boot-trace-') as tmp:
    code, binary = Path(tmp) / 'trace.c', Path(tmp) / 'trace'
    code.write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.getenv('BOOT_TRACE_SANITIZE') else []
    subprocess.run(shlex.split(os.getenv('HOSTCC', 'cc')) + ['-std=gnu11', '-O2', '-Wall',
                   '-Wextra', '-Werror', *flags, str(code), '-o', str(binary)], check=True)
    data = subprocess.check_output([str(binary)])
result = decoder.decode(data)
assert len(result['records']) == 20
assert result['records'][-1]['stage'] == 'EXEC_RESULT'
assert result['records'][0]['args'][0] == '0x82200000'
for offset in (64, 68, 70, 72, 80, 84, 88, 120):
    bad = bytearray(data)
    bad[offset] ^= 1
    try:
        decoder.decode(bad)
    except ValueError:
        continue
    raise AssertionError('corrupt record accepted at ' + str(offset))
try:
    decoder.decode(data[:-1])
except ValueError:
    pass
else:
    raise AssertionError('truncated capture accepted')
assert 'proc_create("dora_boot_trace", 0400' in source
assert not re.search(r'\b(?:ioremap|writel|memblock_reserve|phys_to_virt)\s*\(', source)
print('PASS: production volatile boot records, independent CRC/sequence decoder, writer isolation and torn/corrupt captures')
print('No persistent region or physical boot-stage claim; /init exec is not Android completion.')
