#!/usr/bin/env python3
"""Check production PM callback phases and the SDHC clock-supplier barrier.

Compile the real PM macros, callback tables, event selectors, parent/supplier
waits, device reordering, RPM PM hooks and MMC link helper. Kernel object/OF/
transport plumbing and the global PM phase scheduler are host models. This
does not run Linux suspend or establish hardware/RPM interrupt behavior.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r'^(?:static\s+)?(?:int|void|bool|pm_callback_t)\s+'
                      r'(?:__maybe_unused\s+)?' + re.escape(name) +
                      r'\s*\([^;]*?\)\s*\{', source, re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def declaration(source, prefix):
    start = source.index(prefix)
    end, depth = source.index('{', start) + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    assert source[end] == ';'
    return source[start:end + 1] + '\n'


HOST = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define CONFIG_PM_SLEEP 1
#define CONFIG_PM 1
#define CONFIG_SUSPEND 1
#define CONFIG_HIBERNATE_CALLBACKS 1
#define __maybe_unused
#define EPROBE_DEFER 517
#define READ_ONCE(x) (x)
#define DL_STATE_NONE 0
#define DL_STATE_DORMANT 1
#define DL_FLAG_STATELESS 1
#define DL_FLAG_AUTOREMOVE_CONSUMER 2
#define DL_FLAG_MANAGED 64
#define DL_FLAG_SYNC_STATE_ONLY 128
struct device;
typedef struct { int event; } pm_message_t;
typedef int (*pm_callback_t)(struct device *);
struct device_link;
struct list_head { struct device_link *first; };
struct device_node { int refs; };
struct device {
    const char *name; struct device *parent; void *data;
    const struct dev_pm_ops *ops; struct device_node *of_node;
    struct { struct list_head suppliers, consumers; } links;
    bool initialized, complete, bound;
    int refs, locked;
};
struct platform_device { struct device dev; };
struct device_link {
    struct device *consumer, *supplier; unsigned flags; int status;
    struct device_link *next_c_node, *next_s_node;
};
#define list_for_each_entry_rcu(p,h,m) \
    for ((p)=(h)->first;(p);(p)=(p)->next_##m)
#define list_for_each_entry(p,h,m) list_for_each_entry_rcu(p,h,m)
static struct device *order[16];
static unsigned nr_order, nr_links;
static struct device_link links[24];
static int dpm_list_mtx;
static void mutex_lock(int *lock) { assert(!*lock); *lock=1; }
static void mutex_unlock(int *lock) { assert(*lock); *lock=0; }
static bool device_pm_initialized(struct device *d) { return d->initialized; }
static bool device_is_registered(struct device *d) { return d->initialized; }
static struct device *get_device(struct device *d) { if(d)d->refs++;return d; }
static void put_device(struct device *d) { if(d){assert(d->refs>0);d->refs--;} }
static int device_links_read_lock(void) { return 0; }
static void device_links_read_unlock(int i) { (void)i; }
static unsigned nr_waits;
static void dpm_wait(struct device *d, bool async)
{ (void)async; if(d){nr_waits++;assert(d->complete);} }
static void devices_kset_move_last(struct device *d) { (void)d; }
static void device_pm_move_last(struct device *d)
{
    unsigned i;
    for(i=0;i<nr_order && order[i]!=d;i++);
    assert(i<nr_order);
    memmove(order+i,order+i+1,(nr_order-i-1)*sizeof(*order));
    order[nr_order-1]=d;
}
static int device_for_each_child(struct device *d, void *arg,
                                int (*fn)(struct device *,void *))
{
    struct device *children[16]; unsigned i,n=0;
    for(i=0;i<nr_order;i++)if(order[i]->parent==d)children[n++]=order[i];
    for(i=0;i<n;i++)fn(children[i],arg);
    return 0;
}
static int device_reorder_to_tail(struct device *,void *);
static bool link_fail, qualify;
static unsigned link_calls, parses, finds, of_puts;
static int clock_count, parse_fail;
static struct device_node clock_node;
static struct platform_device gcc;
static bool supplier_missing;
static struct device_link *device_link_add(struct device *c,struct device *s,
                                           unsigned flags)
{
    struct device_link *l; unsigned i;
    link_calls++;
    if(link_fail)return NULL;
    assert(c!=s && s->initialized);
    for(i=0;i<nr_links;i++)if(links[i].consumer==c && links[i].supplier==s)
        return &links[i];
    assert(nr_links<24);l=&links[nr_links++];
    *l=(struct device_link){.consumer=c,.supplier=s,
         .flags=flags|((flags&DL_FLAG_STATELESS)?0:DL_FLAG_MANAGED),
         .status=DL_STATE_NONE};
    l->next_c_node=c->links.suppliers.first;c->links.suppliers.first=l;
    l->next_s_node=s->links.consumers.first;s->links.consumers.first=l;
    device_reorder_to_tail(c,NULL);
    return l;
}
struct of_phandle_args { struct device_node *np; };
static bool of_property_read_bool(struct device_node *n,const char *name)
{ assert(n);assert(!strcmp(name,"sony,require-rpm-physical-acks"));return qualify; }
static int of_count_phandle_with_args(struct device_node *n,const char *p,const char *c)
{ assert(n && !strcmp(p,"clocks") && !strcmp(c,"#clock-cells"));return clock_count; }
static int of_parse_phandle_with_args(struct device_node *n,const char *p,
                        const char *c,int index,struct of_phandle_args *spec)
{
    assert(n && !strcmp(p,"clocks") && !strcmp(c,"#clock-cells"));parses++;
    if(index==parse_fail)return -EINVAL;
    spec->np=&clock_node;clock_node.refs++;return 0;
}
static struct platform_device *of_find_device_by_node(struct device_node *n)
{ assert(n==&clock_node);finds++;if(supplier_missing)return NULL;get_device(&gcc.dev);return &gcc; }
static void of_node_put(struct device_node *n) { assert(n->refs>0);n->refs--;of_puts++; }
static void device_lock(struct device *d) { assert(!d->locked);d->locked=1; }
static void device_unlock(struct device *d) { assert(d->locked);d->locked=0; }
static bool device_is_bound(struct device *d) { assert(d->locked);return d->bound; }
struct rpm_physical { bool online,retired,suspended; };
struct rpm_corner_values { unsigned value; };
struct rpm_corner_resource { int lock; bool online,retired,suspended,owned; };
static struct rpm_physical rail;
static struct rpm_corner_resource corner;
static int physical_error,corner_error,host_fault;
static unsigned physical_acks,corner_acks,host_entries;
static void *dev_get_drvdata(struct device *d) { return d->data; }
static int qcom_rpm_physical_suspend(struct device *d)
{ (void)d;physical_acks++;if(!physical_error)rail.suspended=true;return physical_error; }
static int qcom_rpm_physical_resume(struct device *d)
{ (void)d;physical_acks++;if(!physical_error)rail.suspended=false;return physical_error; }
static void rpm_corner_aggregate(struct rpm_corner_resource *r,struct rpm_corner_values t[2])
{ assert(r->lock);memset(t,0,2*sizeof(*t)); }
static int rpm_corner_apply(struct rpm_corner_resource *r,
 const struct rpm_corner_values t[2],const struct rpm_corner_values p[2],bool force)
{ (void)t;(void)p;assert(r->lock && force);corner_acks++;return corner_error; }
static int rpm_physical_ready(struct rpm_physical *);
static int rpm_corner_ready(struct rpm_corner_resource *);
static int sdhci_msm_suspend(struct device *d) { (void)d;return 0; }
static int sdhci_msm_resume(struct device *d)
{
    int ret;(void)d;host_entries++;
    ret=rpm_physical_ready(&rail);
    if(!ret)ret=rpm_corner_ready(&corner);
    if(ret && !host_fault)host_fault=ret;
    return host_fault;
}
static int sdhci_msm_runtime_suspend(struct device *d) { (void)d;return 0; }
static int sdhci_msm_runtime_resume(struct device *d) { return sdhci_msm_resume(d); }
static int sdhci_msm_suspend_noirq(struct device *d) { (void)d;return 0; }
'''

TESTS = r'''
static struct device mmc, bank, cx_bank, rail_rdev, cx_rdev, transport;
static struct device_node mmc_node;
static void reset(void)
{
    nr_order=nr_links=link_calls=parses=finds=of_puts=0;
    memset(links,0,sizeof(links));
    mmc=(struct device){.name="mmc",.of_node=&mmc_node,.ops=&sdhci_msm_pmops};
    gcc.dev=(struct device){.name="gcc"};
    bank=(struct device){.name="physical-bank",.ops=&rpm_reg_pm_ops};
    cx_bank=(struct device){.name="corner-bank",.ops=&rpm_corner_pm_ops,.data=&corner};
    rail_rdev=(struct device){.name="physical-rdev",.parent=&bank};
    cx_rdev=(struct device){.name="cx-rdev",.parent=&cx_bank};
    transport=(struct device){.name="rpm-transport"};
    struct device *initial[]={&transport,&bank,&rail_rdev,&mmc,&gcc.dev,&cx_bank,&cx_rdev};
    for(unsigned i=0;i<7;i++) {
        order[nr_order++]=initial[i];initial[i]->initialized=initial[i]->bound=true;
    }
    qualify=true;clock_count=3;parse_fail=-1;supplier_missing=link_fail=false;
    physical_error=corner_error=host_fault=0;
    physical_acks=corner_acks=host_entries=0;
    rail=(struct rpm_physical){.online=true};
    corner=(struct rpm_corner_resource){.online=true,.owned=true};
    clock_node.refs=0;
}
static void balanced(void)
{ assert(!clock_node.refs && !gcc.dev.refs && !gcc.dev.locked); }
static void test_lookup(void)
{
    reset();qualify=false;assert(!sdhci_msm_link_clock_providers(&mmc));
    assert(!parses && !finds && !link_calls);balanced();
    reset();clock_count=-EINVAL;assert(sdhci_msm_link_clock_providers(&mmc)==-EINVAL);balanced();
    reset();clock_count=0;assert(sdhci_msm_link_clock_providers(&mmc)==-EINVAL);balanced();
    reset();parse_fail=0;assert(sdhci_msm_link_clock_providers(&mmc)==-EINVAL);
    assert(!finds && !link_calls);balanced();
    reset();supplier_missing=true;assert(sdhci_msm_link_clock_providers(&mmc)==-EPROBE_DEFER);
    assert(of_puts==1 && !link_calls);balanced();
    reset();gcc.dev.bound=false;assert(sdhci_msm_link_clock_providers(&mmc)==-EPROBE_DEFER);
    assert(of_puts==1 && !link_calls);balanced();
    reset();link_fail=true;assert(sdhci_msm_link_clock_providers(&mmc)==-ENOMEM);balanced();
    reset();parse_fail=1;assert(sdhci_msm_link_clock_providers(&mmc)==-EINVAL);
    assert(nr_links==1 && parses==2 && of_puts==1);balanced();
    reset();assert(!sdhci_msm_link_clock_providers(&mmc));
    assert(parses==3 && of_puts==3 && link_calls==3 && nr_links==1);
    assert(links[0].flags==(DL_FLAG_AUTOREMOVE_CONSUMER|DL_FLAG_MANAGED));balanced();
}
static void graph(void)
{
    assert(device_link_add(&bank,&transport,DL_FLAG_AUTOREMOVE_CONSUMER));
    assert(device_link_add(&cx_bank,&transport,DL_FLAG_AUTOREMOVE_CONSUMER));
    assert(device_link_add(&mmc,&rail_rdev,DL_FLAG_STATELESS));
    assert(device_link_add(&gcc.dev,&cx_rdev,DL_FLAG_STATELESS));
    assert(!sdhci_msm_link_clock_providers(&mmc));
}
static void test_supplier_wait(void)
{
    reset();graph();
    for(unsigned i=0;i<nr_order;i++)order[i]->complete=true;
    nr_waits=0;assert(dpm_wait_for_superior(&mmc,true));assert(nr_waits==2);
    nr_waits=0;assert(dpm_wait_for_superior(&gcc.dev,true));assert(nr_waits==1);
    nr_waits=0;assert(dpm_wait_for_superior(&cx_rdev,true));assert(nr_waits==1);
    nr_waits=0;assert(dpm_wait_for_superior(&cx_bank,true));assert(nr_waits==1);
}
static void phase(int event,bool late,bool resume)
{
    unsigned i;
    for(i=0;i<nr_order;i++)order[i]->complete=false;
    for(i=0;i<nr_order;i++) {
        struct device *d=order[resume?i:nr_order-i-1];
        pm_callback_t cb=NULL;
        if(resume)assert(dpm_wait_for_superior(d,true));
        if(d->ops)cb=(late?pm_late_early_op:pm_op)(d->ops,(pm_message_t){event});
        if(cb)(void)cb(d); /* Linux logs resume errors and continues. */
        d->complete=true;
    }
}
static void cycle(int suspend_event,int resume_event)
{
    reset();graph();
    phase(suspend_event,false,false);phase(suspend_event,true,false);
    assert(rail.suspended && corner.suspended);
    phase(resume_event,true,true);phase(resume_event,false,true);
    assert(!host_fault && host_entries==1 && !rail.suspended && !corner.suspended);
    assert(!pm_op(&rpm_reg_pm_ops,(pm_message_t){suspend_event}));
    assert(!pm_op(&rpm_corner_pm_ops,(pm_message_t){resume_event}));
    assert(physical_acks==2 && corner_acks==2);balanced();
}
int main(void)
{
    test_lookup();
    test_supplier_wait();
    cycle(PM_EVENT_SUSPEND,PM_EVENT_RESUME);
    cycle(PM_EVENT_FREEZE,PM_EVENT_THAW);
    cycle(PM_EVENT_HIBERNATE,PM_EVENT_RESTORE);
    cycle(PM_EVENT_QUIESCE,PM_EVENT_RECOVER);
    reset();graph();phase(PM_EVENT_SUSPEND,true,false);corner_error=-EIO;
    phase(PM_EVENT_RESUME,true,true);
    assert(corner.suspended && !rail.suspended && host_fault==-EBUSY);
    corner_error=0;assert(!rpm_corner_resume(&cx_bank));
    assert(sdhci_msm_resume(&mmc)==-EBUSY); /* no automatic host recovery */
    reset();graph();phase(PM_EVENT_SUSPEND,true,false);physical_error=-ETIMEDOUT;
    phase(PM_EVENT_RESUME,true,true);
    assert(rail.suspended && !corner.suspended && host_fault==-EBUSY);
    puts("PASS: production PM phases, supplier ordering, RPM hooks and MMC clock-provider lookup faults");
    return 0;
}
'''


def harness(sources):
    pm, main, core, native, corner, mmc, physical = sources
    # These are real registration edges; their APIs are modeled below, rather
    # than claiming to execute the entire regulator core/transport probe.
    reg = (ROOT / 'drivers/regulator/core.c').read_text()
    transport = (ROOT / 'drivers/soc/qcom/smd-rpm.c').read_text()
    assert 'device_link_add(dev, &rdev->dev, DL_FLAG_STATELESS)' in reg
    assert 'rdev->dev.parent = dev;' in reg
    assert 'device_link_add(consumer, supplier, DL_FLAG_AUTOREMOVE_CONSUMER)' in transport
    add = core[core.index('struct device_link *device_link_add('):]
    assert 'device_reorder_to_tail(consumer, NULL);' in add.split('EXPORT_SYMBOL_GPL(device_link_add)')[0]
    assert main.index('void dpm_resume_start(') < main.index('void dpm_resume_end(')
    assert 'dpm_wait_for_superior(dev, async)' in function(main, 'device_resume_early')
    probe = function(mmc, 'sdhci_msm_probe')
    assert probe.index('sdhci_msm_link_clock_providers(') < probe.index('sdhci_msm_get_clocks(')
    macros = []
    for name in ('SET_SYSTEM_SLEEP_PM_OPS', 'SET_LATE_SYSTEM_SLEEP_PM_OPS',
                 'SET_RUNTIME_PM_OPS'):
        match = re.search(r'^#define ' + name + r'\([^\n]*\n(?:[^\n]*\\\n)*[^\n]*\n', pm, re.M)
        assert match, name
        macros.append(match.group())
    events = '\n'.join(line for line in pm.splitlines() if line.startswith('#define PM_EVENT_'))
    text = HOST + declaration(pm, 'struct dev_pm_ops {') + events + '\n' + ''.join(macros)
    for name in ('dpm_wait_for_suppliers', 'dpm_wait_for_superior', 'pm_op', 'pm_late_early_op'):
        text += function(main, name)
    text += function(core, 'device_reorder_to_tail')
    text += function(physical, 'rpm_physical_ready')
    text += function(corner, 'rpm_corner_ready')
    for source, names in ((native, ('rpm_reg_suspend', 'rpm_reg_resume')),
                          (corner, ('rpm_corner_suspend', 'rpm_corner_resume')),
                          (mmc, ('sdhci_msm_link_clock_providers',))):
        for name in names:
            text += function(source, name)
    for source, name in ((native, 'rpm_reg_pm_ops'), (corner, 'rpm_corner_pm_ops'),
                         (mmc, 'sdhci_msm_pmops')):
        text += declaration(source, 'static const struct dev_pm_ops ' + name)
    return text + TESTS


def run(sources, expect_failure=False):
    try:
        code = harness(sources)
    except (AssertionError, ValueError):
        if expect_failure:
            return
        raise
    with tempfile.TemporaryDirectory(prefix='dora-rpm-pm-') as scratch:
        source, binary = Path(scratch) / 'pm.c', Path(scratch) / 'pm'
        source.write_text(code)
        flags = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wno-unused-function', '-Wno-unused-parameter',
                 '-fsanitize=' + os.environ.get('DORA_SANITIZERS', 'undefined'),
                 '-fno-sanitize-recover=all']
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + flags +
                       [str(source), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        if expect_failure:
            assert result.returncode != 0, 'mutation survived'
        else:
            assert result.returncode == 0, result.stderr
            print(result.stdout, end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    files = ('include/linux/pm.h', 'drivers/base/power/main.c', 'drivers/base/core.c',
             'drivers/regulator/qcom_smd-regulator.c', 'drivers/regulator/qcom-rpm-corner.c',
             'drivers/mmc/host/sdhci-msm.c', 'drivers/regulator/qcom-rpm-physical.c')
    sources = [ (ROOT / name).read_text() for name in files ]
    run(sources)
    if args.self_test:
        mutations = (
            (3, 'SET_LATE_SYSTEM_SLEEP_PM_OPS', 'SET_SYSTEM_SLEEP_PM_OPS'),
            (4, 'SET_LATE_SYSTEM_SLEEP_PM_OPS', 'SET_SYSTEM_SLEEP_PM_OPS'),
            (5, 'ret = sdhci_msm_link_clock_providers(&pdev->dev);', 'ret = 0;'),
            (5, 'if (!device_is_bound(&supplier->dev))', 'if (false)'),
            (5, 'ret = link ? 0 : -ENOMEM;', 'ret = link ? 0 : 0;'),
            (5, '\t\tof_node_put(spec.np);', '\t\t/* leaked OF ref */'),
            (1, '\tdpm_wait_for_suppliers(dev, async);', '\t/* missing supplier wait */'),
        )
        for index, before, after in mutations:
            assert before in sources[index], before
            mutated = list(sources)
            mutated[index] = mutated[index].replace(before, after, 1)
            run(mutated, expect_failure=True)
        print('PASS: rejected 7 phase, supplier-link, defer/error and reference mutations')
    print('Host PM/OF/transport model only; real suspend and RPM IRQ/ACK timing remain untested.')


if __name__ == '__main__':
    main()
