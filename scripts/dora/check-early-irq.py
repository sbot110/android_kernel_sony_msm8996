#!/usr/bin/env python3
"""Execute the production GICv3 walk and allocation failure paths on the host.

Only MMIO storage and kernel API boundaries are modeled. No IRQ delivery,
timer frequency, secure handoff or phone boot is qualified by these tests.
"""
import argparse
import copy
import os
from pathlib import Path
import re
import runpy
import shlex
import subprocess
import struct
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'drivers/irqchip/irq-gic-v3.c'


def function(source, name):
    match = re.search(r'^static int (?:__init )?' + name + r'\([^;]*?\)\s*\{', source, re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef uint64_t resource_size_t;
#define __iomem
#define __init
#define WARN_ON(x) (!!(x))
#define IS_ENABLED(x) 0
#define pr_warn(...) ((void)0)
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
#define static_branch_disable(x) ((void)(x))
#define static_branch_likely(x) (*(x))
#define DOMAIN_BUS_WIRED 1
struct fwnode_handle { int unused; };
'''

BOUNDARIES = r'''
static struct {
    void *dist_base, *domain;
    struct fwnode_handle *fwnode;
    struct redist_region *redist_regions;
    struct { u32 gicd_typer; int *rdist; bool has_vlpis, has_direct_lpi; } rdists;
    u64 redist_stride;
    u32 nr_redist_regions, irq_nr;
    bool has_rss;
} gic_data;
static int supports_deactivate_key, gic_irq_domain_ops, domain_storage, percpu_storage;
static int fail_domain, fail_percpu, tokens, removed, freed, initialized;
static unsigned char banks[2][0x100000], dist[0x10000];
static unsigned reads, visits, found_at;
static bool is_hyp_mode_available(void) { return false; }
static void *irq_domain_create_tree(struct fwnode_handle *h, int *ops, void *d)
{ (void)h; (void)ops; (void)d; return fail_domain ? NULL : &domain_storage; }
static void irq_domain_update_bus_token(void *domain, int token)
{ assert(domain == &domain_storage && token == DOMAIN_BUS_WIRED); tokens++; }
#define alloc_percpu(type) (fail_percpu ? NULL : &percpu_storage)
static void free_percpu(void *p) { if (p) { assert(p == &percpu_storage); freed++; } }
static void irq_domain_remove(void *d) { assert(d == &domain_storage); removed++; }
static void check_address(const void *p, unsigned bytes)
{
    uintptr_t addr = (uintptr_t)p;
    unsigned i;
    if (addr >= (uintptr_t)dist && addr + bytes <= (uintptr_t)dist + sizeof(dist)) return;
    for (i = 0; i < gic_data.nr_redist_regions; i++) {
        uintptr_t base = (uintptr_t)gic_data.redist_regions[i].redist_base;
        if (addr >= base && addr - base <= gic_data.redist_regions[i].size &&
            bytes <= gic_data.redist_regions[i].size - (addr - base)) return;
    }
    abort(); /* A real out-of-mapping read must fail, never return a fake ACK. */
}
static u32 readl_relaxed(const void *p)
{ u32 v; check_address(p, sizeof(v)); memcpy(&v, p, sizeof(v)); reads++; return v; }
static u64 gic_read_typer(const void *p)
{ u64 v; check_address(p, sizeof(v)); memcpy(&v, p, sizeof(v)); reads++; assert(reads < 100); return v; }
static int callback(struct redist_region *r, void *p)
{ (void)r; check_address(p, 2 * SZ_64K); visits++; return visits == found_at ? 0 : 1; }
static void gic_handle_irq(void) {}
static void set_handle_irq(void (*handler)(void)) { assert(handler == gic_handle_irq); initialized++; }
static int mbi_init(struct fwnode_handle *h, void *d) { (void)h; (void)d; return 0; }
static void gic_update_vlpi_properties(void) {}
static int gic_dist_supports_lpis(void) { return 0; }
#define its_init(...) ((void)0)
static void gic_smp_init(void) {}
static void gic_dist_init(void) {}
static void gic_cpu_init(void) {}
static void gic_cpu_pm_init(void) {}
'''

TESTS = r'''
static void reset(struct redist_region *regions, unsigned count, u64 stride)
{
    memset(&gic_data, 0, sizeof(gic_data));
    gic_data.redist_regions = regions; gic_data.nr_redist_regions = count;
    gic_data.redist_stride = stride;
    visits = reads = found_at = 0;
    fail_domain = fail_percpu = tokens = removed = freed = initialized = 0;
}
static void frame(unsigned bank, unsigned off, u64 typer)
{
    u32 pid = GIC_PIDR2_ARCH_GICv3;
    memcpy(banks[bank] + off + GICR_PIDR2, &pid, sizeof(pid));
    memcpy(banks[bank] + off + GICR_TYPER, &typer, sizeof(typer));
}
int main(void)
{
    struct redist_region r[2] = {
        { .redist_base = banks[0], .size = sizeof(banks[0]) },
        { .redist_base = banks[1], .size = sizeof(banks[1]) }
    };
    unsigned i;
    for (i = 0; i < 4; i++) frame(0, i * 0x40000, i == 3 ? GICR_TYPER_LAST : 0);
    reset(r, 1, 0x40000); found_at = 4;
    assert(!gic_iterate_rdists(callback) && visits == 4);
    /* Missing LAST: stop at the declared mapping, with a failure. */
    frame(0, 0xc0000, 0); reset(r, 1, 0x40000);
    assert(gic_iterate_rdists(callback) == -ENODEV && visits == 4);
    for (i = 0; i < 8; i++) frame(0, i * 0x20000, 0);
    reset(r, 1, 0); assert(gic_iterate_rdists(callback) == -ENODEV && visits == 8);
    for (i = 0; i < 4; i++) frame(0, i * 0x40000, GICR_TYPER_VLPIS);
    reset(r, 1, 0); assert(gic_iterate_rdists(callback) == -ENODEV && visits == 4);
    reset(r, 1, UINT64_MAX); assert(gic_iterate_rdists(callback) == -ENODEV && visits == 1);
    reset(r, 1, UINT64_MAX & ~(SZ_64K - 1ULL));
    assert(gic_iterate_rdists(callback) == -ENODEV && visits == 1);
    reset(r, 1, 1); assert(gic_iterate_rdists(callback) == -ENODEV && visits == 1);
    reset(r, 1, 0x28000); assert(gic_iterate_rdists(callback) == -ENODEV && visits == 1);
    r[0].size = 0x10000; reset(r, 1, 0);
    assert(gic_iterate_rdists(callback) == -ENODEV && !reads && !visits);
    r[0].size = sizeof(banks[0]); r[0].single_redist = true;
    reset(r, 1, 0); assert(gic_iterate_rdists(callback) == -ENODEV && visits == 1);
    r[0].single_redist = false;
    frame(1, 0, GICR_TYPER_LAST); reset(r, 2, 0x40000); found_at = 5;
    assert(!gic_iterate_rdists(callback) && visits == 5);
    frame(0, 0, GICR_TYPER_LAST); reset(r, 1, 0);
    assert(gic_iterate_rdists(callback) == -ENODEV && visits == 1);
    /* Exercise the entire production initializer, including cleanup. */
    reset(r, 1, 0); fail_domain = 1;
    assert(gic_init_bases(dist, r, 1, 0, NULL) == -ENOMEM);
    assert(!tokens && !removed && freed == 1 && !initialized);
    reset(r, 1, 0); fail_percpu = 1;
    assert(gic_init_bases(dist, r, 1, 0, NULL) == -ENOMEM);
    assert(!tokens && removed == 1 && !freed && !initialized);
    reset(r, 1, 0);
    assert(!gic_init_bases(dist, r, 1, 0, NULL));
    assert(tokens == 1 && initialized == 1 && !freed && !removed);
    return 0;
}
'''


def exercise(source, expect_success=True):
    header = (ROOT / 'include/linux/irqchip/arm-gic-v3.h').read_text()
    names = ('GICD_TYPER', 'GICD_PIDR2', 'GICD_TYPER_RSS', 'GICD_TYPER_MBIS',
             'GICD_TYPER_IRQS', 'GIC_PIDR2_ARCH_MASK', 'GIC_PIDR2_ARCH_GICv3',
             'GIC_PIDR2_ARCH_GICv4', 'GICR_TYPER', 'GICR_PIDR2',
             'GICR_TYPER_VLPIS', 'GICR_TYPER_LAST')
    defines = '\n'.join(re.search(r'^#define ' + name + r'(?:\s|\().*$', header, re.M)[0]
                        for name in names)
    structure = re.search(r'struct redist_region \{.*?\n\};', source, re.S)[0]
    harness = PREAMBLE + '\n#define SZ_64K 0x10000\n' + defines + '\n' + structure
    harness += BOUNDARIES + function(source, 'gic_iterate_rdists')
    harness += '\n' + function(source, 'gic_init_bases') + TESTS
    with tempfile.TemporaryDirectory(prefix='dora-early-irq-') as tmp:
        code, binary = Path(tmp) / 'gic.c', Path(tmp) / 'gic'
        code.write_text(harness)
        flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.getenv('EARLY_IRQ_SANITIZE') else []
        subprocess.run(shlex.split(os.getenv('HOSTCC', 'cc')) + ['-std=gnu11', '-O2',
                       '-Wall', '-Wextra', '-Werror', *flags, str(code), '-o', str(binary)], check=True)
        run = subprocess.run([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert (run.returncode == 0) == expect_success, run.stderr.decode()[-2000:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--dtb', type=Path, action='append', default=[])
    args = parser.parse_args()
    source = SOURCE.read_text()
    # Both firmware descriptions must retain the actual mapping length.
    for contract in ('rdist_regs[i].size = resource_size(&res)',
                     'redist_regs[count].size = size',
                     'redist_base, redist->length)', 'redist_base, size)'):
        assert contract in source, contract
    exercise(source)
    if args.self_test:
        mutations = [
            ('region->size < SZ_64K * 2', 'false'),
            ('stride > region->size - offset', 'false'),
            ('region->size - offset - stride < SZ_64K * 2', 'false'),
        ]
        for old, new in mutations:
            assert source.count(old) == 1
            try:
                exercise(source.replace(old, new), False)
            except AssertionError as error:
                raise AssertionError('mutation escaped: ' + old) from error
        token = '\tirq_domain_update_bus_token(gic_data.domain, DOMAIN_BUS_WIRED);\n'
        bad = source.replace(token, '')
        anchor = '\tgic_data.rdists.rdist = alloc_percpu'
        bad = bad.replace(anchor, token + anchor)
        exercise(bad, False)
    if args.dtb:
        parse = runpy.run_path(str(ROOT / 'scripts/dora/check-power-dt.py'))['parse_fdt']
        for path in args.dtb:
            nodes = parse(path)
            validate_dt(nodes)
            if args.self_test:
                for node, prop, bad_value in (
                        ('/timer', 'clock-frequency', struct.pack('>I', 0)),
                        ('/aliases', 'serial0', b'/soc/serial@75b1000\0'),
                        ('/chosen', 'stdout-path', b'\0'),
                        ('/soc/interrupt-controller@9bc0000', 'redistributor-stride', struct.pack('>Q', 1)),
                        ('/psci', 'method', b'hvc\0')):
                    bad = copy.deepcopy(nodes)
                    bad[node]['props'][prop] = bad_value
                    try:
                        validate_dt(bad)
                    except AssertionError:
                        continue
                    raise AssertionError('unsafe DT accepted: ' + prop)
    print('PASS: production GICv3 mapping bounds, LAST/stride failures, four-core walk and allocation cleanup')
    print('Host checks do not establish physical IRQ/timer/watchdog or Sony S1 handoff.')


def validate_dt(nodes):
    def cells(path, prop):
        data = nodes[path]['props'][prop]
        assert len(data) % 4 == 0
        return struct.unpack('>' + 'I' * (len(data) // 4), data)
    assert cells('/timer', 'clock-frequency') == (19200000,)
    assert cells('/timer', 'interrupts') == (1, 13, 8, 1, 14, 8, 1, 11, 8, 1, 10, 8)
    assert cells('/soc/interrupt-controller@9bc0000', 'reg') == (0x9bc0000, 0x10000, 0x9c00000, 0x100000)
    assert cells('/soc/interrupt-controller@9bc0000', 'redistributor-stride') == (0, 0x40000)
    assert nodes['/psci']['props']['method'] == b'smc\0'
    assert nodes['/psci']['props']['compatible'] == b'arm,psci-1.0\0'
    assert nodes['/aliases']['props']['serial0'] == b'/soc/serial@75b0000\0'
    assert nodes['/chosen']['props']['stdout-path'] == b'serial0\0'
    assert nodes['/soc/serial@75b0000']['props']['status'] == b'okay\0'
    assert cells('/soc/serial@75b0000', 'interrupts') == (0, 114, 4)
    assert nodes['/soc/serial@75b1000']['props']['status'] == b'disabled\0'


if __name__ == '__main__':
    main()
