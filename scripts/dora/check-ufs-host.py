#!/usr/bin/env python3
"""Run real UFS host registration/power error paths in a bounded host model.

This tests error propagation and state ownership, not UFS hardware or storage.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'drivers/scsi/ufs/ufs-qcom.c').read_text()


def function(name):
    match = re.search(r'static int ' + name + r'\s*\([^;]*?\)\s*\{', source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define EPROBE_DEFER 517
#define CONFIG_QCOM_BUS_SCALING 1
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define dev_err(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p) >= (uintptr_t)-4095)
typedef uint32_t u32;
struct device_node { int unused; };
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct device_attribute { void *show, *store;
    struct { const char *name; unsigned mode; } attr; };
struct list_head { bool empty; };
struct clk { int unused; };
struct ufs_clk_info { struct clk *clk; const char *name; bool enabled; };
struct ufs_qcom_host;
struct ufs_hba { struct device *dev; struct list_head clk_list_head;
    int pwr_info; struct ufs_qcom_host *variant; };
struct ufs_qcom_host { struct ufs_hba *hba; void *generic_phy;
    bool is_phy_pwr_on; int clks_on;
    struct { uint32_t client_handle, curr_vote; int min_bw_vote, max_bw_vote;
        bool is_max_bw_needed; struct device_attribute max_bus_bw; } bus_vote; };
struct msm_bus_scale_pdata { int num_usecases; };
enum ufs_notify_change_status { PRE_CHANGE, POST_CHANGE };
static struct device_node node;
static struct platform_device pdev = { .dev = { .of_node = &node } };
static struct msm_bus_scale_pdata pdata;
static struct ufs_hba hba;
static struct ufs_qcom_host host;
static int provider_error, register_handle, create_error, count;
static int pdata_calls, registrations, unregisters;
static bool has_pdata, has_min, has_max, hs, hibern8;
static int on_error, on_calls, off_calls, ref_calls;
static int msm_bus_scale_client_init(struct device *dev)
{ (void)dev; return provider_error; }
static struct platform_device *to_platform_device(struct device *dev)
{ return (struct platform_device *)dev; }
static struct msm_bus_scale_pdata *msm_bus_cl_get_pdata(struct platform_device *dev)
{ (void)dev; pdata_calls++; return has_pdata ? &pdata : NULL; }
static int of_property_count_strings(struct device_node *n, const char *key)
{ (void)n; (void)key; return count; }
static int of_property_match_string(struct device_node *n, const char *key, const char *value)
{ (void)n; (void)key;
  if (!strcmp(value, "MIN")) return has_min ? 0 : -ENODATA;
  if (!strcmp(value, "MAX")) return has_max ? 11 : -ENODATA;
  return -ENODATA; }
static uint32_t msm_bus_scale_register_client(struct msm_bus_scale_pdata *p)
{ (void)p; registrations++; return register_handle; }
static void msm_bus_scale_unregister_client(uint32_t h)
{ assert(h == 7); unregisters++; }
#define show_ufs_to_mem_max_bus_bw NULL
#define store_ufs_to_mem_max_bus_bw NULL
#define sysfs_attr_init(a) ((void)(a))
static int device_create_file(struct device *dev, struct device_attribute *a)
{ (void)dev; assert(!strcmp(a->attr.name, "max_bus_bw")); return create_error; }
static struct ufs_qcom_host *ufshcd_get_variant(struct ufs_hba *h)
{ return h->variant; }
static int phy_power_on(void *p) { assert(p); on_calls++; return on_error; }
static int phy_power_off(void *p) { assert(p); off_calls++; return 0; }
static bool ufshcd_is_hs_mode(int *p) { (void)p; return hs; }
static bool ufshcd_is_auto_hibern8_enabled(struct ufs_hba *h) { (void)h; return hibern8; }
static bool ufs_qcom_is_link_active(struct ufs_hba *h) { (void)h; return !hibern8; }
static void ufs_qcom_dev_ref_clk_ctrl(struct ufs_qcom_host *h, bool on)
{ (void)h; (void)on; ref_calls++; }
#define atomic_set(a, value) (*(a) = (value))
#define list_empty(head) ((head)->empty)
#define list_for_each_entry(entry, head, member) for ((entry) = NULL; (entry); (entry) = NULL)
static void clk_disable_unprepare(struct clk *clk) { (void)clk; }
'''
for name in ('ufs_qcom_get_bus_vote', 'ufs_qcom_bus_register', 'ufs_qcom_setup_clocks'):
    harness += function(name)
harness += r'''
static void reset(void)
{
    memset(&host, 0, sizeof(host)); memset(&hba, 0, sizeof(hba));
    hba.dev = &pdev.dev; hba.variant = &host; hba.clk_list_head.empty = true;
    host.hba = &hba; host.generic_phy = &node;
    pdata.num_usecases = count = 12;
    provider_error = create_error = on_error = 0; register_handle = 7;
    pdata_calls = registrations = unregisters = 0;
    has_pdata = has_min = has_max = true; hs = hibern8 = false;
    on_calls = off_calls = ref_calls = 0;
}
int main(void)
{
    reset(); provider_error = -EPROBE_DEFER;
    assert(ufs_qcom_bus_register(&host) == -EPROBE_DEFER);
    assert(!pdata_calls && !registrations);
    reset(); has_pdata = false;
    assert(ufs_qcom_bus_register(&host) == -ENODATA && !registrations);
    reset(); count = 11;
    assert(ufs_qcom_bus_register(&host) == -EINVAL && !registrations);
    reset(); count = -EILSEQ;
    assert(ufs_qcom_bus_register(&host) == -EILSEQ && !registrations);
    reset(); register_handle = 0;
    assert(ufs_qcom_bus_register(&host) == -EFAULT && !unregisters);
    reset(); has_min = false;
    assert(ufs_qcom_bus_register(&host) == -EINVAL && unregisters == 1);
    assert(!host.bus_vote.client_handle);
    reset(); has_max = false;
    assert(ufs_qcom_bus_register(&host) == -EINVAL && unregisters == 1);
    reset(); create_error = -ENOMEM;
    assert(ufs_qcom_bus_register(&host) == -ENOMEM && unregisters == 1);
    assert(!host.bus_vote.client_handle);
    reset(); assert(ufs_qcom_bus_register(&host) == 0);
    assert(host.bus_vote.curr_vote == UINT32_MAX);
    assert(host.bus_vote.min_bw_vote == 0 && host.bus_vote.max_bw_vote == 11);
    reset(); on_error = -EPROBE_DEFER; hs = true;
    assert(ufs_qcom_setup_clocks(&hba, true, POST_CHANGE) == -EPROBE_DEFER);
    assert(on_calls == 1 && !host.is_phy_pwr_on && !host.clks_on && !ref_calls);
    on_error = 0;
    assert(ufs_qcom_setup_clocks(&hba, true, POST_CHANGE) == 0);
    assert(on_calls == 2 && host.is_phy_pwr_on && host.clks_on && ref_calls == 1);
    assert(ufs_qcom_setup_clocks(&hba, true, POST_CHANGE) == 0 && on_calls == 2);
    hibern8 = true;
    assert(ufs_qcom_setup_clocks(&hba, false, PRE_CHANGE) == 0);
    assert(off_calls == 1 && !host.is_phy_pwr_on && !host.clks_on);
    puts("PASS: actual UFS host rejects missing/deferred providers, invalid vectors and sysfs failures");
    puts("PASS: failed PHY power-on never marks hardware powered; successful retry and collapse balance state");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-ufs-host-') as scratch:
    c, binary = Path(scratch) / 'host.c', Path(scratch) / 'host'
    c.write_text(harness)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu99', '-Wall', '-Wextra',
                    '-Werror', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host API model only: PHY, RPM acknowledgements and real storage require device tests')
