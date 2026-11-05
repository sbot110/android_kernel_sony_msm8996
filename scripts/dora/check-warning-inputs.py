#!/usr/bin/env python3
"""Fault-test actual DHD command allocation and SDHCI debug-input parsing.

Only API calls are modeled. No firmware command, runtime PM request or MMIO
access reaches hardware; these checks do not establish device operation.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DHD = (ROOT / 'drivers/net/wireless/bcmdhd/dhd_linux.c').read_text()
MMC = (ROOT / 'drivers/mmc/host/sdhci-msm.c').read_text()
BCM = (ROOT / 'drivers/net/wireless/bcmdhd/bcmutils.c').read_text()
KSTRTOX = (ROOT / 'lib/kstrtox.c').read_text()


def function(source, name):
    match = re.search(r'^(?:static\s+)?(?:const char \*|unsigned int|int|uint|ssize_t)'
                      r'\s*' + re.escape(name) + r'\s*\([^;]*?\)\s*\{',
                      source, re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def macro(source, name):
    match = re.search(r'^#define\s+' + re.escape(name) + r'\s+([^\n]+)', source, re.M)
    assert match, name
    return '#define ' + name + ' ' + match[1].split('/*', 1)[0].rstrip() + '\n'


ioctl_defs = (ROOT / 'drivers/net/wireless/bcmdhd/include/devctrl_if/wlioctl_defs.h').read_text()
bcm_defs = (ROOT / 'drivers/net/wireless/bcmdhd/include/bcmutils.h').read_text()
harness = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#include <ctype.h>
#include <sys/types.h>
typedef unsigned int uint;
typedef uint32_t u32;
#define U32_MAX UINT32_MAX
#define GFP_KERNEL 0
#define EPROBE_DEFER 517
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define pr_info(...) ((void)0)
#define pr_debug(...) ((void)0)
#define unlikely(x) (x)
#undef _tolower
#define _tolower(x) tolower(x)
#define div_u64(a,b) ((a) / (b))
#define kstrtoul _kstrtoul
'''
for source, names in (
        (ioctl_defs, ('WLC_IOCTL_MAXLEN', 'WLC_GET_VAR', 'WLC_SET_VAR')),
        (bcm_defs, ('BCME_BADARG', 'BCME_NOMEM', 'BCME_BUFTOOSHORT')),
        (MMC, ('MAX_TESTBUS',)),
        ((ROOT / 'lib/kstrtox.h').read_text(), ('KSTRTOX_OVERFLOW',))):
    for name in names:
        harness += macro(source, name)
for name in ('_parse_integer_fixup_radix', '_parse_integer_limit', '_parse_integer',
             '_kstrtoull', 'kstrtoull', '_kstrtoul'):
    harness += function(KSTRTOX, name)
harness += r'''
static void *active;
static size_t allocation_size;
static unsigned allocations, frees;
static bool allocation_failure;
static void *kzalloc(size_t length, int flags)
{
    (void)flags; assert(!active && length && length <= WLC_IOCTL_MAXLEN);
    allocations++; allocation_size = length;
    if (allocation_failure) return NULL;
    active = calloc(1, length); assert(active); return active;
}
static char *kstrndup(const char *text, size_t count, int flags)
{
    size_t length = strnlen(text, count);
    char *copy = kzalloc(length + 1, flags);
    if (copy) memcpy(copy, text, length);
    return copy;
}
static void kfree(void *buffer)
{
    if (!buffer) return;
    assert(buffer == active); free(buffer); active = NULL; frees++;
}
struct device { void *driver_data; };
struct device_attribute { int unused; };
struct sdhci_host { void *mmc; };
static struct sdhci_host host;
static struct device device = { &host };
static struct device_attribute attribute;
static unsigned pm_gets, pm_puts, pm_noidles, configurations;
static int pm_result, pm_usage;
static u32 configured[4];
static void *dev_get_drvdata(struct device *dev) { return dev->driver_data; }
static int pm_runtime_get_sync(struct device *dev)
{ assert(dev == &device); pm_gets++; pm_usage++; return pm_result; }
static int pm_runtime_put_sync(struct device *dev)
{ assert(dev == &device && pm_usage > 0 && pm_result >= 0); pm_puts++; pm_usage--; return 0; }
static void pm_runtime_put_noidle(struct device *dev)
{ assert(dev == &device && pm_usage > 0); pm_noidles++; pm_usage--; }
static void sdhci_msm_mm_dbg_configure(struct sdhci_host *h, u32 mask,
                                     u32 match, u32 shift, u32 bus)
{
    assert(h == &host && pm_result >= 0 && pm_usage == 1); configurations++;
    configured[0] = mask; configured[1] = match;
    configured[2] = shift; configured[3] = bus;
}
typedef struct { int unused; } dhd_pub_t;
typedef struct { uint cmd; void *buf; uint len; uint8_t set; uint used, needed; } wl_ioctl_t;
static dhd_pub_t pub;
static unsigned builders, ioctls;
static bool builder_failure;
static int ioctl_result;
static const char *expected_name;
static const char *expected_payload;
static uint expected_length;
static int expected_set;
'''
harness += function(BCM, 'bcm_mkiovar').replace('bcm_mkiovar(', 'real_bcm_mkiovar(', 1)
harness += r'''
static uint bcm_mkiovar(const char *name, char *data, uint count, char *buffer, uint length)
{
    builders++;
    if (builder_failure) return 0;
    return real_bcm_mkiovar(name, data, count, buffer, length);
}
static int dhd_wl_ioctl(dhd_pub_t *p, int index, wl_ioctl_t *ioc, void *buffer, int length)
{
    size_t prefix = strlen(expected_name) + 1;
    assert(p == &pub && index == 3 && buffer == active && buffer == ioc->buf);
    assert(length > 0 && (size_t)length == allocation_size && ioc->len == (uint)length);
    assert(ioc->cmd == (expected_set ? WLC_SET_VAR : WLC_GET_VAR));
    assert(ioc->set == expected_set && prefix + expected_length == (size_t)length);
    assert(!memcmp(buffer, expected_name, prefix));
    if (expected_length)
        assert(!memcmp((char *)buffer + prefix, expected_payload, expected_length));
    ioctls++;
    /* Even a failed or SET command may modify its private response buffer. */
    memset(buffer, 'R', expected_length);
    return ioctl_result;
}
'''
dhd_function = function(DHD, 'dhd_iovar')
mmc_function = function(MMC, 'store_mask_and_match')
harness += dhd_function
harness += mmc_function
harness += r'''
static void reset(void)
{
    assert(!active && !pm_usage); allocations = frees = builders = ioctls = 0;
    pm_gets = pm_puts = pm_noidles = configurations = 0; pm_result = 0;
    allocation_failure = builder_failure = false; ioctl_result = 0;
    device.driver_data = &host; memset(configured, 0, sizeof(configured));
}
static ssize_t debug(const char *text)
{ return store_mask_and_match(&device, &attribute, text, strlen(text)); }
static void bad_debug(const char *text, int error)
{
    reset(); assert(debug(text) == error);
    assert(!pm_gets && !pm_puts && !pm_noidles && !pm_usage && !configurations && !active);
    assert(allocations == frees);
}
static int command(char *name, char *payload, uint length, int set)
{
    expected_name = name; expected_payload = payload;
    expected_length = length; expected_set = set;
    return dhd_iovar(&pub, 3, name, payload, length, set);
}
static void bad_command(char *name, char *payload, uint length)
{
    reset(); assert(command(name, payload, length, 1) == BCME_BADARG);
    assert(!allocations && !builders && !ioctls && !active);
}
int main(void)
{
    static const char readonly[] = "  0xffffffff\t+2 31 127\n";
    char counted[] = "1 2 3 4ignored";
    char payload[WLC_IOCTL_MAXLEN + 1], original[sizeof(payload)];
    char name[WLC_IOCTL_MAXLEN + 1];
    const int pm_errors[] = { -EIO, -EPROBE_DEFER };
    size_t i;
    reset(); assert(debug(readonly) == (ssize_t)strlen(readonly));
    assert(!strcmp(readonly, "  0xffffffff\t+2 31 127\n"));
    assert(configured[0] == UINT32_MAX && configured[1] == 2);
    assert(configured[2] == 31 && configured[3] == MAX_TESTBUS);
    assert(pm_gets == 1 && pm_puts == 1 && !pm_noidles && !pm_usage && configurations == 1);
    assert(allocations == 1 && frees == 1 && !active);
    reset(); assert(store_mask_and_match(&device, &attribute, counted, 7) == 7);
    assert(!strcmp(counted, "1 2 3 4ignored") && configured[3] == 4);
    bad_debug("", -EINVAL); bad_debug(" \t\n", -EINVAL);
    bad_debug("1 2 3", -EINVAL); bad_debug("1 2 3 4 5", -EINVAL);
    bad_debug("1 junk 3 4", -EINVAL); bad_debug("1 -2 3 4", -EINVAL);
    bad_debug("1 2 32 4", -ERANGE); bad_debug("1 2 3 128", -ERANGE);
    bad_debug("4294967296 2 3 4", -ERANGE);
    bad_debug("18446744073709551616 2 3 4", -ERANGE);
    reset(); allocation_failure = true;
    assert(debug("1 2 3 4") == -ENOMEM && allocations == 1 && !frees);
    assert(!configurations && !pm_gets && !pm_puts && !active);
    reset(); device.driver_data = NULL;
    assert(debug("1 2 3 4") == -EINVAL && !allocations && !configurations);

    /* Failed get_sync still acquires a usage reference; never access MMIO. */
    for (i = 0; i < ARRAY_SIZE(pm_errors); i++) {
        reset(); pm_result = pm_errors[i];
        assert(debug("1 2 3 4") == pm_errors[i]);
        assert(pm_gets == 1 && pm_noidles == 1 && !pm_puts && !pm_usage);
        assert(!configurations && allocations == 1 && frees == 1 && !active);
    }
    /* A positive get_sync result reports an already-active successful device. */
    reset(); pm_result = 1;
    assert(debug("1 2 3 4") == 7);
    assert(pm_gets == 1 && pm_puts == 1 && !pm_noidles && !pm_usage && configurations == 1);
    assert(allocations == 1 && frees == 1 && !active);

    memset(payload, 'P', sizeof(payload)); memcpy(original, payload, sizeof(payload));
    bad_command("x", payload, UINT_MAX);
    bad_command("x", payload, WLC_IOCTL_MAXLEN);
    bad_command(NULL, payload, 1); bad_command("x", NULL, 1);
    reset(); assert(command("x", NULL, 0, 1) == 0);
    assert(allocations == 1 && frees == 1 && builders == 1 && ioctls == 1 && !active);
    memset(name, 'n', sizeof(name));
    bad_command(name, payload, 1); /* The bounded name scan must not require a NUL. */
    name[WLC_IOCTL_MAXLEN - 1] = '\0'; bad_command(name, payload, 1);
    reset(); assert(command(name, payload, 0, 1) == 0);
    assert(allocation_size == WLC_IOCTL_MAXLEN && builders == 1 && ioctls == 1);
    assert(allocations == 1 && frees == 1 && !active);
    reset(); assert(command("x", payload, WLC_IOCTL_MAXLEN - 2, 1) == 0);
    assert(allocation_size == WLC_IOCTL_MAXLEN && !memcmp(payload, original, sizeof(payload)));
    assert(allocations == 1 && frees == 1 && builders == 1 && ioctls == 1 && !active);
    reset(); assert(command("x", payload, 4, 0) == 0);
    for (i = 0; i < 4; i++) assert(payload[i] == 'R');
    assert(!memcmp(payload + 4, original + 4, sizeof(payload) - 4));
    assert(allocations == 1 && frees == 1 && !active);
    memcpy(payload, original, sizeof(payload)); reset(); ioctl_result = -EIO;
    assert(command("x", payload, 4, 0) == -EIO);
    assert(!memcmp(payload, original, sizeof(payload)) && ioctls == 1 && frees == 1 && !active);
    reset(); allocation_failure = true;
    assert(command("x", payload, 4, 1) == BCME_NOMEM);
    assert(allocations == 1 && !frees && !builders && !ioctls && !active);
    reset(); builder_failure = true;
    assert(command("x", payload, 4, 1) == BCME_BUFTOOSHORT);
    assert(allocations == 1 && frees == 1 && builders == 1 && !ioctls && !active);
    puts("PASS: actual SDHCI parser preserves input and rejects malformed/range/heap failures before PM or MMIO");
    puts("PASS: actual SDHCI debug runtime PM failures preserve errors, balance usage and prevent MMIO");
    puts("PASS: actual DHD command builder bounds heap allocation and balances success/failure cleanup and GET copying");
    return 0;
}
'''
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--self-test', action='store_true',
                    help='also reject in-memory parser, runtime PM, cleanup and VLA regressions')
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='dora-warning-inputs-') as scratch:
    c, binary = Path(scratch) / 'host.c', Path(scratch) / 'host'
    c.write_text(harness)
    compiler = shlex.split(os.environ.get('HOSTCC', 'cc'))
    sanitizers = os.environ.get('DORA_SANITIZERS', 'undefined')
    flags = ['-std=gnu99', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
             '-Wvla', '-fsanitize=' + sanitizers, '-fno-sanitize-recover=all']
    subprocess.run(compiler + flags + [str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    if args.self_test:
        parse_guard = '\t\tif (ret)\n\t\t\tgoto out;\n'
        pm_guard = '\tif (ret < 0) {'
        pm_rollback = '\t\tpm_runtime_put_noidle(dev);'
        assert mmc_function.count(parse_guard) == 1
        assert mmc_function.count(pm_guard) == 1
        assert mmc_function.count(pm_rollback) == 1
        assert dhd_function.count('\tkfree(buf);\n') == 1
        mutations = {
            'ignored numeric parse failure': harness.replace(
                mmc_function, mmc_function.replace(parse_guard, '', 1)),
            'leaked command buffer': harness.replace(
                dhd_function, dhd_function.replace('\tkfree(buf);\n', '', 1)),
            'ignored runtime PM failure': harness.replace(
                mmc_function, mmc_function.replace(pm_guard, '\tif (false) {', 1)),
            'missing runtime PM usage rollback': harness.replace(
                mmc_function, mmc_function.replace(pm_rollback,
                                                   '\t\t(void)pm_runtime_put_noidle;', 1)),
        }
        for label, mutated in mutations.items():
            c.write_text(mutated)
            subprocess.run(compiler + flags + [str(c), '-o', str(binary)],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            assert result.returncode, 'accepted ' + label
        stack = dhd_function.replace('{\n',
            '{\n\tchar regression_stack[cmd_len + 1];\n\t(void)regression_stack;\n', 1)
        c.write_text(harness.replace(dhd_function, stack))
        result = subprocess.run(compiler + flags + [str(c), '-o', str(binary)],
                                capture_output=True, text=True)
        assert result.returncode and '-Werror=vla' in result.stderr, 'accepted dynamic command stack buffer'
        print('PASS: ignored parse/PM failures, missing PM rollback, command-buffer leaks and VLA mutations rejected')
print('Host API checks only: WLAN firmware and SDHCI hardware remain device validation gates')
