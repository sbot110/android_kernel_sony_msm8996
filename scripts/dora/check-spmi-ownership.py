#!/usr/bin/env python3
"""Run native SPMI probe against real PM8994 data with an SPM-owned APCC gang.

The host register model checks exclusion before type probing/registration. It
neither emulates the PMIC nor establishes that CPU voltage control is ready.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/regulator/qcom_spmi-regulator.c').read_text()


def function(name):
    match = re.search(r'static (?:int|bool) ' + name + r'\s*\([^;]*?\)\s*\{', source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def table(name):
    match = re.search(r'static const struct spmi_regulator_data ' + name + r'\[\] = \{.*?\n\};', source, re.S)
    assert match, name
    return match[0] + '\n'


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint16_t u16;
#define GFP_KERNEL 0
#define THIS_MODULE NULL
#define REGULATOR_VOLTAGE 0
#define SPMI_COMMON_REG_ENABLE 0x46
#define SPMI_COMMON_ENABLE_MASK 0x80
#define SPMI_COMMON_ENABLE 0x80
#define dev_err(...) ((void)0)
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
struct list_head { struct list_head *next, *prev; };
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for(p=list_entry((h)->next,__typeof__(*p),m); &p->m!=(h); p=list_entry(p->m.next,__typeof__(*p),m))
#define INIT_LIST_HEAD(h) ((h)->next=(h),(h)->prev=(h))
static void list_add(struct list_head *n,struct list_head *h)
{ n->next=h->next;n->prev=h;h->next->prev=n;h->next=n; }
struct property { int unused; };
struct device_node { bool owned; };
struct device { struct device_node *of_node;struct device *parent;void *data; };
struct platform_device { struct device dev; };
struct regmap { int unused; };
struct regulator_dev { int unused; };
struct regulator_ops { int (*set_voltage_sel)(struct regulator_dev *,unsigned); };
struct regulator_desc { int id,type;void *owner;unsigned enable_reg,enable_mask,enable_val;
    const char *name,*supply_name,*of_match;void *of_parse_cb,*of_map_mode;struct regulator_ops *ops; };
struct regulator_config { struct device *dev;void *driver_data;struct regmap *regmap; };
struct spmi_regulator { struct device *dev;u16 base;struct regmap *regmap;int ocp_irq;
    struct regulator_desc desc;struct list_head node;int ocp_work; };
struct spmi_regulator_data { const char *name;u16 base;const char *supply,*ocp;u16 force_type; };
struct of_device_id { const void *data; };
static struct of_device_id qcom_spmi_regulator_match[1],selected;
static struct regulator_ops plain_ops,spmi_saw_ops;
static struct regmap registers,*saw_regmap;
static struct regulator_dev rdev;
static bool missing_regmap;
static unsigned allocations,fail_allocation,probes,registrations,fail_registration;
static bool touched[0x10000];
static void *allocated[256];
static void *devm_kzalloc(struct device *dev,size_t size,int flags)
{
    (void)dev;(void)flags;allocations++;
    if(allocations==fail_allocation)return NULL;
    assert(allocations<256);return allocated[allocations]=calloc(1,size);
}
static void platform_set_drvdata(struct platform_device *pdev,void *data) { pdev->dev.data=data; }
static struct regmap *dev_get_regmap(struct device *dev,const char *name)
{ (void)dev;(void)name;return missing_regmap?NULL:&registers; }
static const struct of_device_id *of_match_device(const struct of_device_id *match,struct device *dev)
{ (void)match;(void)dev;return selected.data?&selected:NULL; }
static bool of_property_read_bool(struct device_node *node,const char *name)
{ assert(!strcmp(name,"qcom,spm-owned-apcc"));return node->owned; }
static struct property *of_find_property(struct device_node *node,const char *name,int *len)
{ (void)node;(void)name;(void)len;return NULL; }
static struct device_node *of_parse_phandle(struct device_node *node,const char *name,unsigned index)
{ (void)name;(void)index;return node; }
static struct regmap *syscon_node_to_regmap(struct device_node *node) { (void)node;return &registers; }
static void of_node_put(struct device_node *node) { (void)node; }
static struct device_node *of_get_child_by_name(struct device_node *node,const char *name)
{ (void)node;(void)name;return NULL; }
static int platform_get_irq_byname(struct platform_device *pdev,const char *name)
{ (void)pdev;(void)name;return -EINVAL; }
static int spmi_regulator_saw_set_voltage(struct regulator_dev *dev,unsigned selector)
{ (void)dev;(void)selector;return 0; }
#define spmi_regulator_of_parse NULL
#define spmi_regulator_of_map_mode NULL
static int spmi_regulator_match(struct spmi_regulator *reg,unsigned force_type)
{ (void)force_type;probes++;touched[reg->base]=true;reg->desc.ops=&plain_ops;return 0; }
static struct regulator_dev *devm_regulator_register(struct device *dev,struct regulator_desc *desc,
                                                     struct regulator_config *config)
{ (void)dev;(void)desc;(void)config;registrations++;return registrations==fail_registration?ERR_PTR(-EIO):&rdev; }
static void cancel_delayed_work_sync(int *work) { (void)work; }
'''
harness += table('pm8994_regulators') + table('pmi8994_regulators')
harness += function('spmi_regulator_spm_owned') + function('qcom_spmi_regulator_probe')
harness += r'''
static struct device_node node;
static struct device parent;
static struct platform_device pdev={.dev={.of_node=&node,.parent=&parent}};
static void reset(bool owned,bool pmi)
{
    unsigned i;
    for(i=0;i<256;i++){free(allocated[i]);allocated[i]=NULL;}
    memset(touched,0,sizeof(touched));allocations=fail_allocation=0;
    probes=registrations=fail_registration=0;saw_regmap=NULL;missing_regmap=false;
    node.owned=owned;selected.data=pmi?pmi8994_regulators:pm8994_regulators;
}
static void no_apcc_mmio(void)
{ assert(!touched[0x2900]&&!touched[0x2c00]&&!touched[0x2f00]&&!touched[0x3200]); }
int main(void)
{
    unsigned count,i;
    struct spmi_regulator_data name={.name="s8-other"};
    reset(false,false);assert(qcom_spmi_regulator_probe(&pdev)==0);
    assert(probes==46 && registrations==46 && touched[0x2900]&&touched[0x3200]);
    reset(true,false);assert(qcom_spmi_regulator_probe(&pdev)==0);
    assert(probes==42 && registrations==42);no_apcc_mmio();
    assert(touched[0x1400]&&touched[0x3500]&&touched[0x5f00]);count=allocations;
    for(i=1;i<=count;i++) {
        reset(true,false);fail_allocation=i;
        assert(qcom_spmi_regulator_probe(&pdev)==-ENOMEM);no_apcc_mmio();
    }
    reset(true,false);fail_registration=8;
    assert(qcom_spmi_regulator_probe(&pdev)==-EIO);no_apcc_mmio();
    reset(true,true);assert(qcom_spmi_regulator_probe(&pdev)==-EINVAL && !probes && !registrations);
    reset(false,true);assert(qcom_spmi_regulator_probe(&pdev)==0 && probes==4 && registrations==4);
    assert(!spmi_regulator_spm_owned(&name,true));name.name="s80";
    assert(!spmi_regulator_spm_owned(&name,true));name.name="s8";
    assert(spmi_regulator_spm_owned(&name,true));assert(!spmi_regulator_spm_owned(&name,false));
    reset(true,false);selected.data=NULL;
    assert(qcom_spmi_regulator_probe(&pdev)==-ENODEV && !probes);
    reset(true,false);missing_regmap=true;
    assert(qcom_spmi_regulator_probe(&pdev)==-ENODEV && !probes);
    reset(false,false);
    puts("PASS: actual SPMI probe excludes exactly PM8994 S8/S9/S10/S11 before MMIO, independent of SAW binding");
    puts("PASS: defaults, other PMIC rejection, every allocation failure and registration failure preserve ownership");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-spmi-ownership-') as scratch:
    c, binary = Path(scratch) / 'ownership.c', Path(scratch) / 'ownership'
    c.write_text(harness)
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-missing-field-initializers', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host model only; no PMIC writes, voltage, CPU bring-up or SPM operation tested.')
