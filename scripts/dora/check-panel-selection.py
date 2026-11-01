#!/usr/bin/env python3
"""Exercise the actual Sony selector against a small refcounted OF model.

This host check does not model panel hardware. It verifies that missing, invalid,
ambiguous and unmatched bootloader IDs never retain the preferred panel and that
successful selection and failure paths balance the node references they own.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'drivers/video/fbdev/msm/mdss_dsi_panel_driver.c').read_text()
start = source.index('static int __init lcdid_adc_setup(')
end = source.index('static int mdss_dsi_panel_driver_vreg_name_to_config(', start)
actual_functions = source[start:end]
manifest = json.loads((root / 'Documentation/sony/dora-panels.json').read_text())
ranges = ',\n'.join('{%d, %d}' % (p['adc_min'], p['adc_max'])
                    for p in manifest['panels'])
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
typedef uint32_t u32;
#define ADC_PNUM 2
#define ADC_RNG_MIN 0
#define ADC_RNG_MAX 1
#define __init
#define __setup(name, fn)
#define dev_err(...) ((void)0)
#define pr_notice(...) ((void)0)
static u32 lcdid_adc;
static bool lcdid_adc_valid;
struct device_node {
    int refs, index, index_rc, count, read_rc;
    bool has_range;
    u32 range[3];
    struct device_node *parent, *child, *next;
};
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
static struct device_node *of_node_get(struct device_node *np)
{
    if (np) { assert(np->refs > 0); np->refs++; }
    return np;
}
static void of_node_put(struct device_node *np)
{
    if (np) { assert(np->refs > 1); np->refs--; }
}
static struct device_node *of_get_parent(struct device_node *np)
{ return np ? of_node_get(np->parent) : NULL; }
static struct device_node *of_get_next_child(struct device_node *parent,
                                            struct device_node *prev)
{
    struct device_node *next = prev ? prev->next : parent->child;
    of_node_put(prev);
    return of_node_get(next);
}
#define for_each_child_of_node(parent, child) \
    for (child = of_get_next_child(parent, NULL); child != NULL; \
         child = of_get_next_child(parent, child))
static int of_property_read_u32(struct device_node *np, const char *name, u32 *v)
{
    (void)name;
    if (np->index_rc) return np->index_rc;
    *v = np->index;
    return 0;
}
static void *of_find_property(struct device_node *np, const char *name, int *len)
{ (void)name; (void)len; return np->has_range ? np : NULL; }
static int of_property_count_u32_elems(struct device_node *np, const char *name)
{ (void)name; return np->count; }
static int of_property_read_u32_array(struct device_node *np, const char *name,
                                     u32 *v, int count)
{
    (void)name;
    if (np->read_rc) return np->read_rc;
    memcpy(v, np->range, count * sizeof(*v));
    return 0;
}
static int kstrtou32(const char *s, int base, u32 *result)
{
    char *end;
    unsigned long long value;
    if (!s || *s == '-') return -EINVAL;
    errno = 0;
    value = strtoull(s, &end, base);
    if (errno || end == s || *end || value > UINT32_MAX) return -EINVAL;
    *result = value;
    return 0;
}
'''
harness += actual_functions
harness += r'''
static const u32 reference_ranges[5][2] = { RANGES };
static struct device_node parent, controller, panels[5];
static struct platform_device pdev;
static void init_tree(void)
{
    unsigned i;
    memset(&parent, 0, sizeof(parent)); parent.refs = 1;
    memset(&controller, 0, sizeof(controller)); controller.refs = 1;
    parent.child = panels;
    pdev.dev.of_node = &controller;
    memset(panels, 0, sizeof(panels));
    for (i = 0; i < 5; i++) {
        panels[i].refs = 1;
        panels[i].parent = &parent;
        panels[i].next = i == 4 ? NULL : &panels[i + 1];
        panels[i].count = 2;
        panels[i].has_range = true;
        memcpy(panels[i].range, reference_ranges[i], sizeof(reference_ranges[i]));
    }
    lcdid_adc_valid = false;
}
static void check_selection(int expected)
{
    unsigned i;
    struct device_node *np = of_node_get(&panels[0]);
    mdss_dsi_panel_driver_detection(&pdev, &np);
    assert(np == (expected < 0 ? NULL : &panels[expected]));
    of_node_put(np);
    assert(parent.refs == 1 && controller.refs == 1);
    for (i = 0; i < 5; i++) assert(panels[i].refs == 1);
}
static void select_adc(u32 adc, int expected)
{
    char text[32];
    snprintf(text, sizeof(text), "%u", adc);
    assert(lcdid_adc_setup(text) == 1);
    check_selection(expected);
}
int main(void)
{
    unsigned i;
    struct device_node *np = NULL;
    init_tree();
    mdss_dsi_panel_driver_detection(&pdev, &np);
    mdss_dsi_panel_driver_detection(&pdev, NULL);
    check_selection(-1); /* No bootloader ADC must not keep preferred panel. */
    for (i = 0; i < 5; i++) {
        select_adc(reference_ranges[i][0], i);
        select_adc(reference_ranges[i][1], i);
        select_adc((reference_ranges[i][0] + reference_ranges[i][1]) / 2, i);
    }
    lcdid_adc_setup("0x11FFEA"); check_selection(0);
    select_adc(1505000, -1); /* Old magic default lies outside Dora ranges. */
    select_adc(57001, -1); /* Gap immediately above Sharp ID9. */
    lcdid_adc_setup("invalid"); check_selection(-1);
    lcdid_adc_setup("4294967296"); check_selection(-1);
    lcdid_adc_setup("-1"); check_selection(-1);
    lcdid_adc_setup(NULL); check_selection(-1);
    lcdid_adc_setup(""); check_selection(-1);
    lcdid_adc_setup("0"); lcdid_adc_setup("invalid"); check_selection(-1);
    init_tree(); controller.index_rc = -EINVAL; select_adc(1179626, -1);
    init_tree(); panels[0].parent = NULL; select_adc(1179626, -1);
    init_tree(); panels[0].count = 1; select_adc(1179626, -1);
    init_tree(); panels[0].count = 3; select_adc(1179626, -1);
    init_tree(); panels[0].read_rc = -EINVAL; select_adc(1179626, -1);
    init_tree(); panels[0].range[0] = panels[0].range[1] + 1;
    select_adc(1179626, -1);
    init_tree(); memcpy(panels[1].range, panels[0].range, sizeof(panels[0].range));
    select_adc(1179626, -1); /* Ambiguity must release both node references. */
    init_tree(); panels[1].has_range = false; select_adc(1179626, 0);
    init_tree(); panels[1].index = 1; panels[1].count = 3;
    select_adc(1179626, 0); /* Other DSI controller is outside this search. */
    puts("PASS: actual Sony selector rejects missing/invalid/unmatched/ambiguous IDs");
    puts("PASS: all five ADC ranges include endpoints; returned OF references balance");
    return 0;
}
'''.replace('RANGES', ranges)
with tempfile.TemporaryDirectory(prefix='dora-panel-selector-') as scratch:
    c_file = Path(scratch) / 'panel-selection.c'
    binary = Path(scratch) / 'panel-selection'
    c_file.write_text(harness)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=c99', '-Wall', '-Wextra',
                    '-Werror', str(c_file), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host OF model only; physical panel/DSI/power behavior is untested.')
