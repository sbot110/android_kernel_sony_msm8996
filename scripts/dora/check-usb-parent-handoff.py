#!/usr/bin/env python3
"""Execute the complete production DWC3-QCOM probe against Dora root aliases.

Kernel API boundaries fail/count access; no USB/charger hardware is emulated
as working. The parent must reject before allocation, reset, clocks or VBUS.
"""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import runpy
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
POWER = runpy.run_path(str(ROOT / 'scripts/dora/check-power-dt.py'))
SHARED = runpy.run_path(str(ROOT / 'scripts/dora/check-cpu-shared-power.py'))
ALIASES = {'sony,dora-row', 'somc,dora-row'}


def aliases(props):
    names = props.get('compatible', b'').rstrip(b'\0').decode().split('\0')
    if not ALIASES.issubset(names):
        raise ValueError('Dora root aliases no longer match the USB parent guard')
    return names


PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define GFP_KERNEL 0
#define IORESOURCE_MEM 0
#define USB_DR_MODE_HOST 1
#define dev_err(...) ((void)0)
#define IS_ERR(p) (false)
#define PTR_ERR(p) (-EIO)
struct device_node { int n; };
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct resource { int n; };
struct dwc3_qcom { struct device *dev;void *resets,*qscratch_base;void **clks;
 int num_clocks,mode;struct platform_device *dwc3;bool is_suspended; };
static const char *machine[8];static unsigned machine_count,accesses;
static bool of_machine_is_compatible(const char *s)
{ unsigned i;for(i=0;i<machine_count;i++)if(!strcmp(machine[i],s))return true;return false; }
static void *devm_kzalloc(struct device *d,size_t n,int g)
{ (void)d;(void)n;(void)g;accesses++;return NULL; }
static void platform_set_drvdata(struct platform_device *p,void *q) { (void)p;(void)q;accesses++; }
static void *devm_reset_control_array_get_optional_exclusive(struct device *d) { (void)d;accesses++;return NULL; }
static int reset_control_assert(void *p) { (void)p;accesses++;return -EIO; }
static int reset_control_deassert(void *p) { (void)p;accesses++;return -EIO; }
static void usleep_range(unsigned a,unsigned b) { (void)a;(void)b;accesses++; }
static int of_count_phandle_with_args(struct device_node *n,const char *s,const char *t) { (void)n;(void)s;(void)t;accesses++;return 0; }
static int dwc3_qcom_clk_init(struct dwc3_qcom *q,int n) { (void)q;(void)n;accesses++;return -EIO; }
static struct resource *platform_get_resource(struct platform_device *p,int t,unsigned i) { (void)p;(void)t;(void)i;accesses++;return NULL; }
static void *devm_ioremap_resource(struct device *d,struct resource *r) { (void)d;(void)r;accesses++;return NULL; }
static int dwc3_qcom_setup_irq(struct platform_device *p) { (void)p;accesses++;return -EIO; }
static struct device_node *of_get_child_by_name(struct device_node *n,const char *s) { (void)n;(void)s;accesses++;return NULL; }
static bool device_property_read_bool(struct device *d,const char *s) { (void)d;(void)s;accesses++;return false; }
static void dwc3_qcom_select_utmi_clk(struct dwc3_qcom *q) { (void)q;accesses++; }
static int of_platform_populate(struct device_node *n,void *a,void *b,struct device *d) { (void)n;(void)a;(void)b;(void)d;accesses++;return -EIO; }
static struct platform_device *of_find_device_by_node(struct device_node *n) { (void)n;accesses++;return NULL; }
static int usb_get_dr_mode(struct device *d) { (void)d;accesses++;return 0; }
static void dwc3_qcom_vbus_override_enable(struct dwc3_qcom *q,bool on) { (void)q;(void)on;accesses++; }
static int dwc3_qcom_register_extcon(struct dwc3_qcom *q) { (void)q;accesses++;return -EIO; }
static void device_init_wakeup(struct device *d,int on) { (void)d;(void)on;accesses++; }
static void pm_runtime_set_active(struct device *d) { (void)d;accesses++; }
static void pm_runtime_enable(struct device *d) { (void)d;accesses++; }
static void pm_runtime_forbid(struct device *d) { (void)d;accesses++; }
static void of_platform_depopulate(struct device *d) { (void)d;accesses++; }
static void clk_disable_unprepare(void *c) { (void)c;accesses++; }
static void clk_put(void *c) { (void)c;accesses++; }
'''

TESTS = r'''
static struct platform_device device;
static void *consumer(void *p)
{ unsigned i;(void)p;for(i=0;i<2000;i++)assert(dwc3_qcom_probe(&device)==-EOPNOTSUPP);return NULL; }
int main(int argc,char **argv)
{
 unsigned i;pthread_t threads[4];
 machine[0]="sony,dora-row";machine_count=1;assert(dwc3_qcom_probe(&device)==-EOPNOTSUPP && !accesses);
 machine[0]="somc,dora-row";assert(dwc3_qcom_probe(&device)==-EOPNOTSUPP && !accesses);
 assert(argc>1 && argc<=9);machine_count=argc-1;for(i=1;i<(unsigned)argc;i++)machine[i-1]=argv[i];
 for(i=0;i<4;i++)assert(!pthread_create(&threads[i],NULL,consumer,NULL));
 for(i=0;i<4;i++) { assert(!pthread_join(threads[i],NULL)); }
 assert(!accesses);
 machine[0]="qcom,other-board";machine_count=1;
 assert(dwc3_qcom_probe(&device)==-ENOMEM && accesses==1);
 puts("PASS: actual DWC3-QCOM probe rejects Dora before allocation/reset/clocks/UTMI/VBUS; generic path retained");return 0;
}
'''


def run(body, path, names, name, expected=True):
    c, binary = path / (name + '.c'), path / name
    c.write_text(PREAMBLE + body + TESTS)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('USB_PARENT_SANITIZE') else []
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + ['-std=gnu11', '-O1',
        '-Wall', '-Wextra', '-Werror', '-pthread', *flags, str(c), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary), *names], capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == expected, (name, result.stdout, result.stderr)
    if expected:
        print(result.stdout, end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path, action='append', default=[])
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    source = (ROOT / 'drivers/usb/dwc3/dwc3-qcom.c').read_text()
    body = SHARED['function'](source, 'dwc3_qcom_probe')
    root = (ROOT / 'arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-dora.dts').read_text()
    names = re.findall(r'"([^"]+)"', re.search(r'compatible\s*=\s*([^;]+);', root)[1])
    aliases({'compatible': ('\0'.join(names)+'\0').encode()})
    inspected = {}
    for path in args.dtb:
        inspected[str(path)] = aliases(POWER['parse_fdt'](path)['/']['props'])
    mutations = [body.replace('"sony,dora-row"', '"sony,wrong"'),
                 body.replace('"somc,dora-row"', '"somc,wrong"'),
                 body.replace('if (of_machine_is_compatible', 'reset_control_assert(NULL);\n\tif (of_machine_is_compatible', 1)]
    with tempfile.TemporaryDirectory(prefix='dora-usb-parent-') as tmp:
        path = Path(tmp)
        run(body, path, names, 'production')
        for i, values in enumerate(inspected.values()):
            run(body, path, values, 'tree'+str(i))
        if args.self_test:
            for i, changed in enumerate(mutations):
                assert body != changed
                run(changed, path, names, 'unsafe'+str(i), False)
            try:
                aliases({'compatible': b'qcom,msm8996\0'})
            except ValueError:
                pass
            else:
                raise AssertionError('missing board identity accepted')
            print('PASS: three unsafe parent mutations and missing-root-alias fixture rejected')
    if args.report:
        args.report.write_text(json.dumps(dict(inspected_trees=inspected,
            unsafe_source_mutations_rejected=3 if args.self_test else 0,
            parent_hardware_accesses_before_gate=0, physical_charging_verified=False), indent=2)+'\n')


if __name__ == '__main__':
    main()
