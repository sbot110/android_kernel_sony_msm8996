#!/usr/bin/env python3
"""Exercise production minidump packing and sparse auxiliary cleanup on the host.

The MMIO reads use host memory; this checks bookkeeping, not firmware or dumps
on a device. Set MINIDUMP_TEST_SANITIZE=1 to enable address/undefined sanitizers.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/soc/qcom/peripheral-loader.c').read_text()


def function(name):
    match = re.search(r'static (?:void|unsigned int) ' + name +
                      r'\s*\([^;]*?\)\s*\{', source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


aux = re.search(r'struct aux_minidump_info \{[^}]+\};', source, re.S)
assert aux
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define __iomem
#define CONFIG_MSM_SUBSYSTEM_RESTART 1
#include "drivers/soc/qcom/minidump_private.h"
#include "include/soc/qcom/ramdump.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define pr_debug(...) ((void)0)
static u32 __raw_readl(const void *address)
{
    u32 value;
    memcpy(&value, address, sizeof(value));
    return value;
}
static const void *unmapped[8];
static unsigned unmap_count;
static void iounmap(const void *address)
{
    assert(address && unmap_count < ARRAY_SIZE(unmapped));
    unmapped[unmap_count++] = address;
}
'''
harness += aux.group(0) + '\n'
for name in ('unmap_aux_minidump_regions', 'prepare_minidump_segments',
             'prepare_aux_minidump_segments'):
    harness += function(name)
harness += r'''
static void region(struct md_ss_region *entry, const char *name, bool valid,
                   unsigned address, unsigned size)
{
    memset(entry, 0, sizeof(*entry));
    assert(strlen(name) < sizeof(entry->name));
    strcpy(entry->name, name);
    entry->md_valid = valid ? MD_REGION_VALID : MD_REGION_INVALID;
    entry->region_base_address = address;
    entry->region_size = size;
}
static void segment(const struct ramdump_segment *entry,
                    const struct md_ss_region *expected)
{
    assert(entry->name == expected->name);
    assert(!strcmp(entry->name, expected->name));
    assert(entry->address == expected->region_base_address);
    assert(entry->size == expected->region_size);
    assert(!entry->v_address);
}
int main(void)
{
    struct md_ss_region primary[4], first[3], second[1], third[2];
    struct ramdump_segment output[12] = {0};
    struct aux_minidump_info auxiliary[6] = {0};
    struct { int before, count, after; } checked = {0x1234, 4, 0x5678};
    unsigned next;
    int total;

    /* Invalid records must decrement the caller's value and compact output. */
    region(&primary[0], "bad0", false, 0x1000, 0x10);
    region(&primary[1], "main1", true, 0x2000, 0x20);
    region(&primary[2], "bad2", false, 0x3000, 0x30);
    region(&primary[3], "main3", true, 0x4000, 0x40);
    next = prepare_minidump_segments(output, primary, 4, &checked.count);
    assert(next == 2 && checked.count == 2);
    assert(checked.before == 0x1234 && checked.after == 0x5678);
    segment(&output[0], &primary[1]);
    segment(&output[1], &primary[3]);
    assert(!output[2].name);

    memset(output, 0, sizeof(output));
    primary[1].md_valid = primary[3].md_valid = MD_REGION_INVALID;
    checked.count = 4;
    assert(!prepare_minidump_segments(output, primary, 4, &checked.count));
    assert(!checked.count && !output[0].name);
    assert(checked.before == 0x1234 && checked.after == 0x5678);
    assert(!prepare_minidump_segments(output, NULL, 0, &checked.count));
    assert(!checked.count);

    /* Three mapped tables separated by disabled entries must append in order. */
    primary[1].md_valid = primary[3].md_valid = MD_REGION_VALID;
    region(&first[0], "aux10", true, 0x5000, 0x50);
    region(&first[1], "aux1bad", false, 0x6000, 0x60);
    region(&first[2], "aux12", true, 0x7000, 0x70);
    region(&second[0], "aux2", true, 0x8000, 0x80);
    region(&third[0], "aux3bad", false, 0x9000, 0x90);
    region(&third[1], "aux31", true, 0xa000, 0xa0);
    auxiliary[1] = (struct aux_minidump_info){first, ARRAY_SIZE(first)};
    auxiliary[3] = (struct aux_minidump_info){second, ARRAY_SIZE(second)};
    auxiliary[5] = (struct aux_minidump_info){third, ARRAY_SIZE(third)};
    total = ARRAY_SIZE(primary) + ARRAY_SIZE(first) + ARRAY_SIZE(second) + ARRAY_SIZE(third);
    next = prepare_minidump_segments(output, primary, ARRAY_SIZE(primary), &total);
    prepare_aux_minidump_segments(&output[next], auxiliary, &total, ARRAY_SIZE(auxiliary));
    assert(next == 2 && total == 6);
    segment(&output[0], &primary[1]);
    segment(&output[1], &primary[3]);
    segment(&output[2], &first[0]);
    segment(&output[3], &first[2]);
    segment(&output[4], &second[0]);
    segment(&output[5], &third[1]);
    assert(!output[6].name);

    /* Cleanup must visit mappings after every disabled/failed mapping slot. */
    unmap_aux_minidump_regions(auxiliary, ARRAY_SIZE(auxiliary));
    assert(unmap_count == 3);
    assert(unmapped[0] == first && unmapped[1] == second && unmapped[2] == third);
    unmap_aux_minidump_regions(NULL, 0);
    unmap_aux_minidump_regions(NULL, ARRAY_SIZE(auxiliary));
    assert(unmap_count == 3);
    puts("PASS: production minidump invalid counts, packed primary/auxiliary records and sparse cleanup");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-minidump-') as scratch:
    c, binary = Path(scratch) / 'minidump.c', Path(scratch) / 'minidump'
    c.write_text(harness)
    flags = ['-std=gnu11', '-O2', '-Wall', '-Werror', '-I', str(ROOT)]
    if os.environ.get('MINIDUMP_TEST_SANITIZE'):
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + flags +
                   [str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host MMIO model only; firmware tables and hardware dump collection remain untested.')
