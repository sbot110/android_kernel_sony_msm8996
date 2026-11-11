#!/usr/bin/env python3
"""Run actual bus clock/commit callbacks with injected resource failures.

This bounded model checks retries and vote ownership, not hardware sequencing.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/soc/qcom/msm_bus/msm_bus_fabric_adhoc.c').read_text()


def function(name):
    match = re.search(r'(?:static )?int ' + name + r'\s*\([^;]*?\)\s*\{', source, re.S)
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
#include <limits.h>
typedef uint64_t u64;
#define NUM_CTX 2
#define EPROBE_DEFER 517
#define MSM_BUS_ERR(...) ((void)0)
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p) >= (uintptr_t)-4095)
#define unlikely(x) (x)
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for(p=list_entry((h)->next,__typeof__(*p),m); &p->m!=(h); p=list_entry(p->m.next,__typeof__(*p),m))
#define list_for_each_entry_safe(p,n,h,m) \
    for(p=list_entry((h)->next,__typeof__(*p),m),n=list_entry(p->m.next,__typeof__(*p),m); \
        &p->m!=(h); p=n,n=list_entry(n->m.next,__typeof__(*n),m))
static void init_list(struct list_head *h) { h->next=h->prev=h; }
static bool list_empty(struct list_head *h) { return h->next==h; }
static void list_add_tail(struct list_head *e,struct list_head *h)
{ e->next=h;e->prev=h->prev;h->prev->next=e;h->prev=e; }
static void list_del_init(struct list_head *e)
{ e->prev->next=e->next;e->next->prev=e->prev;init_list(e); }
struct device { int unused; };
struct regulator { int refs,on_error,off_error; };
struct clk { int refs,on_error,rate_error; long round_error; unsigned rates; };
struct nodeclk { struct clk *clk;struct regulator *reg;u64 rate;
    bool dirty,enable_only_clk,setrate_only_clk,enable,reg_enabled;char reg_name[50]; };
struct nodebw { u64 cur_clk_hz; };
struct msm_bus_node_info_type { bool is_fab_dev,defer_qos; };
struct msm_bus_node_device_type { struct nodeclk clk[NUM_CTX],bus_qos_clk;
    struct device dev;struct msm_bus_node_info_type *node_info;
    struct nodebw node_bw[NUM_CTX];struct list_head link,devlist,dev_link;
    int qos_error,bw_error[NUM_CTX];unsigned bw_calls[NUM_CTX];bool dirty; };
static int get_error;
static int bus_get_reg(struct nodeclk *clock,struct device *dev)
{ (void)clock;(void)dev;return get_error; }
static int regulator_enable(struct regulator *reg)
{ if(reg->on_error)return reg->on_error;reg->refs++;return 0; }
static int regulator_disable(struct regulator *reg)
{ if(reg->off_error)return reg->off_error;assert(reg->refs>0);reg->refs--;return 0; }
static int clk_prepare_enable(struct clk *clk)
{ if(clk->on_error)return clk->on_error;clk->refs++;return 0; }
static void clk_disable_unprepare(struct clk *clk) { assert(clk->refs>0);clk->refs--; }
static long clk_round_rate(struct clk *clk,u64 rate) { return clk->round_error?: (long)rate; }
static int clk_set_rate(struct clk *clk,long rate)
{ (void)rate;clk->rates++;return clk->rate_error; }
static int flush_bw_data(struct msm_bus_node_device_type *node,int ctx)
{ node->bw_calls[ctx]++;return node->bw_error[ctx]; }
static int msm_bus_dev_init_qos(struct device *dev,void *data)
{ (void)data;return container_of(dev,struct msm_bus_node_device_type,dev)->qos_error; }
'''
for name in ('bus_enable_reg', 'bus_disable_reg', 'enable_nodeclk', 'disable_nodeclk',
             'setrate_nodeclk', 'flush_clk_data', 'msm_bus_agg_fab_clks', 'msm_bus_commit_data'):
    harness += function(name)
harness += r'''
static struct msm_bus_node_device_type node;
static struct msm_bus_node_info_type info;
static struct clk clock,qosclock;
static struct regulator rail,qosrail;
static void reset(void)
{
    memset(&node,0,sizeof(node));memset(&info,0,sizeof(info));
    memset(&clock,0,sizeof(clock));memset(&qosclock,0,sizeof(qosclock));
    memset(&rail,0,sizeof(rail));memset(&qosrail,0,sizeof(qosrail));
    get_error=0;node.node_info=&info;node.clk[0].clk=&clock;node.clk[0].reg=&rail;
    strcpy(node.clk[0].reg_name,"vdd");node.node_bw[0].cur_clk_hz=100;
    init_list(&node.devlist);init_list(&node.link);
}
int main(void)
{
    struct msm_bus_node_device_type second,third;
    struct msm_bus_node_info_type other={0};
    LIST_HEAD(pending);
    reset();clock.round_error=-EINVAL;
    assert(flush_clk_data(&node,0)==-EINVAL && node.clk[0].dirty && !node.clk[0].rate && !clock.refs);
    clock.round_error=0;clock.rate_error=-EIO;
    assert(flush_clk_data(&node,0)==-EIO && !node.clk[0].rate && !rail.refs);
    clock.rate_error=0;get_error=-EPROBE_DEFER;
    assert(flush_clk_data(&node,0)==-EPROBE_DEFER && !clock.refs && !rail.refs);
    get_error=0;rail.on_error=-EAGAIN;
    assert(flush_clk_data(&node,0)==-EAGAIN && !clock.refs && !node.clk[0].reg_enabled);
    rail.on_error=0;clock.on_error=-EIO;rail.off_error=-EBUSY;
    assert(flush_clk_data(&node,0)==-EIO && rail.refs==1 && !clock.refs && node.clk[0].reg_enabled);
    clock.on_error=0;rail.off_error=0;
    assert(flush_clk_data(&node,0)==0 && rail.refs==1 && clock.refs==1);
    assert(!node.clk[0].dirty && node.clk[0].rate==100);
    assert(flush_clk_data(&node,0)==0 && rail.refs==1 && clock.refs==1);
    node.node_bw[0].cur_clk_hz=0;rail.off_error=-EAGAIN;
    assert(flush_clk_data(&node,0)==-EAGAIN && !clock.refs && rail.refs==1 && node.clk[0].rate==100);
    rail.off_error=0;assert(flush_clk_data(&node,0)==0 && !rail.refs && !node.clk[0].dirty);
    reset();info.is_fab_dev=true;node.bus_qos_clk.clk=&qosclock;node.bus_qos_clk.reg=&qosrail;
    strcpy(node.bus_qos_clk.reg_name,"qos");qosclock.on_error=-EIO;
    assert(flush_clk_data(&node,0)==-EIO && clock.refs==1 && rail.refs==1 && !qosrail.refs);
    assert(node.clk[0].dirty && !node.clk[0].rate && node.node_bw[0].cur_clk_hz==100);
    qosclock.on_error=0;assert(flush_clk_data(&node,0)==0 && clock.refs==1 && qosclock.refs==1);
    assert(!node.node_bw[0].cur_clk_hz && !node.clk[0].dirty);
    assert(flush_clk_data(&node,0)==0 && !clock.refs && !qosclock.refs && !rail.refs && !qosrail.refs);
    reset();node.clk[0].clk=NULL;node.node_info->defer_qos=true;node.qos_error=-EPROBE_DEFER;
    memset(&second,0,sizeof(second));memset(&third,0,sizeof(third));
    second.node_info=third.node_info=&other;init_list(&second.link);init_list(&third.link);
    list_add_tail(&node.link,&pending);list_add_tail(&second.link,&pending);list_add_tail(&third.link,&pending);
    second.bw_error[0]=-EIO;second.bw_error[1]=-EAGAIN;
    assert(msm_bus_commit_data(&pending)==-EPROBE_DEFER);
    assert(node.dirty && second.dirty && list_empty(&third.link));
    assert(!node.bw_calls[0] && !node.bw_calls[1] && second.bw_calls[0]==1 && second.bw_calls[1]==1);
    node.qos_error=0;node.node_info->defer_qos=false;
    assert(msm_bus_commit_data(&pending)==-EIO && list_empty(&node.link) && second.dirty);
    second.bw_error[0]=second.bw_error[1]=0;
    assert(msm_bus_commit_data(&pending)==0 && list_empty(&pending) && !second.dirty);
    puts("PASS: actual bus clock/rail failures, deferred suppliers, cache retry, balanced votes and first commit error retention");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-bus-retry-') as scratch:
    c, binary = Path(scratch) / 'retry.c', Path(scratch) / 'retry'
    c.write_text(harness)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu11', '-O2', '-Wall', '-Wextra',
                    '-Werror', str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host fault model only; actual clocks, regulators and RPM hardware remain untested.')
