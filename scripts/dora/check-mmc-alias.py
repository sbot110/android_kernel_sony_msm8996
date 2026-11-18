#!/usr/bin/env python3
"""Run actual MMC host allocation/release with OF-alias and IDA fault models.

This verifies stable host/block names and index ownership across probe order and
failures. It does not probe an SDHCI controller or discover real partitions.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'drivers/mmc/core/host.c').read_text()


def function(name):
    match = re.search(r'(?:static\s+)?(?:int|void|struct mmc_host\s*\*)\s*' + name +
                      r'\s*\([^;]*?\)\s*\{', SOURCE, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (SOURCE[end] == '{') - (SOURCE[end] == '}')
        end += 1
    return SOURCE[match.start():end] + '\n'


harness = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define GFP_KERNEL 0
#define PAGE_SIZE 4096
#define EPROBE_DEFER 517
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define cls_dev_to_mmc_host(d) container_of(d,struct mmc_host,class_dev)
struct device_node { int alias; };
struct class;
struct device { struct device_node *of_node; struct device *parent;
    struct class *class; char name[32]; bool initialized; };
struct class { void (*dev_release)(struct device *); };
struct mmc_host { struct device class_dev; struct device *parent; int index;
    int rescan_disable, lock, active_reqs, wq, detect, sdio_irq_work, retune_timer;
    unsigned max_segs,max_seg_size,max_req_size,max_blk_size,max_blk_count;
    int fixed_drv_type; struct { unsigned power_delay_ms; } ios; };
static int highest_alias, ida_error, allocation_error, gpio_error;
static unsigned live_hosts, releases, ida_calls, first_requested, end_requested;
static int allocated[32], mmc_host_ida;
static int of_alias_get_id(struct device_node *node,const char *stem)
{ assert(node && !strcmp(stem,"mmc"));return node->alias; }
static int of_alias_get_highest_id(const char *stem)
{ assert(!strcmp(stem,"mmc"));return highest_alias; }
static bool used(unsigned id)
{ unsigned i;for(i=0;i<32;i++)if(allocated[i]>=0 && (unsigned)allocated[i]==id)return true;return false; }
static int ida_simple_get(int *ida,unsigned first,unsigned end,int gfp)
{
    unsigned id=first, limit=end?end-1:INT_MAX, i;
    assert(ida==&mmc_host_ida && !gfp);ida_calls++;first_requested=first;end_requested=end;
    if(ida_error)return ida_error;
    if(first>INT_MAX || limit<first)return -ENOSPC;
    while(used(id)) { if(id==limit)return -ENOSPC;id++; }
    for(i=0;i<32;i++)if(allocated[i]<0){allocated[i]=(int)id;return (int)id;}
    return -ENOMEM;
}
static void ida_simple_remove(int *ida,int id)
{
    unsigned i;assert(ida==&mmc_host_ida && id>=0);
    for(i=0;i<32;i++)if(allocated[i]==id){allocated[i]=-1;return;}
    assert(!"removing an unallocated host index");
}
static void *kzalloc(size_t size,int gfp)
{ void *p;assert(!gfp);if(allocation_error)return NULL;p=calloc(1,size);if(p)live_hosts++;return p; }
static void kfree(void *p) { assert(p && live_hosts);live_hosts--;free(p); }
static void dev_set_name(struct device *dev,const char *format,int index)
{ assert(!strcmp(format,"mmc%d"));snprintf(dev->name,sizeof(dev->name),format,index); }
static void device_initialize(struct device *dev) { dev->initialized=true; }
static void device_enable_async_suspend(struct device *dev) { assert(dev->initialized); }
static int mmc_gpio_alloc(struct mmc_host *host) { assert(host->class_dev.initialized);return gpio_error; }
static void put_device(struct device *dev)
{ assert(dev->initialized && dev->class);releases++;dev->class->dev_release(dev); }
#define spin_lock_init(p) (*(p)=1)
#define atomic_set(p,n) (*(p)=(n))
#define init_waitqueue_head(p) (*(p)=1)
#define INIT_DELAYED_WORK(p,f) ((void)(f),*(p)=1)
#define timer_setup(p,f,flags) ((void)(f),(void)(flags),*(p)=1)
static void mmc_rescan(void) {}
static void sdio_irq_work(void) {}
static void mmc_retune_timer(void) {}
'''
harness += function('mmc_host_alloc_index')
harness += function('mmc_host_classdev_release')
harness += 'static struct class mmc_host_class = { .dev_release = mmc_host_classdev_release };\n'
harness += function('mmc_alloc_host')
harness += r'''
static struct device_node emmc_node={0},sd_node={1},unnamed_node={-ENODEV};
static struct device emmc={.of_node=&emmc_node},sd={.of_node=&sd_node},unnamed={.of_node=&unnamed_node};
static void reset(int max_alias)
{
    unsigned i;assert(!live_hosts);for(i=0;i<32;i++)assert(allocated[i]<0);
    highest_alias=max_alias;ida_error=allocation_error=gpio_error=0;releases=ida_calls=0;
}
static struct mmc_host *expect_host(struct device *dev,int index)
{
    char name[32];struct mmc_host *host=mmc_alloc_host(16,dev);assert(host);
    snprintf(name,sizeof(name),"mmc%d",index);
    assert(host->index==index && !strcmp(host->class_dev.name,name) && host->parent==dev);
    assert(host->rescan_disable && host->ios.power_delay_ms==10 && host->max_blk_size==512);
    return host;
}
int main(void)
{
    struct mmc_host *a,*b,*c,*d;unsigned i;
    for(i=0;i<32;i++)allocated[i]=-1;
    reset(1);a=expect_host(&sd,1);b=expect_host(&emmc,0);
    assert(end_requested==1 && first_requested==0);put_device(&a->class_dev);put_device(&b->class_dev);
    reset(1);a=expect_host(&unnamed,2);b=expect_host(&sd,1);c=expect_host(&emmc,0);
    assert(live_hosts==3);put_device(&c->class_dev);d=expect_host(NULL,3);
    assert(!used(0));put_device(&d->class_dev);put_device(&b->class_dev);put_device(&a->class_dev);
    reset(1);a=expect_host(&emmc,0);assert(mmc_alloc_host(0,&emmc)==NULL);
    assert(live_hosts==1 && used(0));put_device(&a->class_dev);b=expect_host(&emmc,0);put_device(&b->class_dev);
    reset(-ENODEV);a=expect_host(&unnamed,0);b=expect_host(NULL,1);
    put_device(&a->class_dev);c=expect_host(NULL,0);put_device(&c->class_dev);put_device(&b->class_dev);
    reset(3);a=expect_host(NULL,4);b=expect_host(&emmc,0);put_device(&a->class_dev);put_device(&b->class_dev);
    reset(1);allocation_error=1;assert(!mmc_alloc_host(0,&emmc) && !ida_calls && !live_hosts);
    allocation_error=0;ida_error=-ENOMEM;assert(!mmc_alloc_host(0,&emmc) && !used(0) && !live_hosts);
    ida_error=0;gpio_error=-EPROBE_DEFER;assert(!mmc_alloc_host(0,&emmc) && !used(0) && !live_hosts && releases==1);
    gpio_error=0;a=expect_host(&emmc,0);put_device(&a->class_dev);
    /* Sparse/malformed large aliases must not cause signed-bound overflow. */
    reset(INT_MAX);assert(!mmc_alloc_host(0,NULL) && first_requested==(unsigned)INT_MAX+1 && !live_hosts);
    emmc_node.alias=INT_MAX;a=expect_host(&emmc,INT_MAX);
    assert(first_requested==INT_MAX && end_requested==(unsigned)INT_MAX+1);put_device(&a->class_dev);emmc_node.alias=0;
    for(i=0;i<32;i++)assert(allocated[i]<0);
    puts("PASS: actual MMC allocation/release: fixed aliases, reverse/deferred probe, dynamic reservations, failures and ID reuse");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-mmc-alias-') as scratch:
    source, binary = Path(scratch) / 'alias.c', Path(scratch) / 'alias'
    source.write_text(harness)
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host index model only; physical eMMC/MicroSD enumeration and Android by-name links remain untested.')
