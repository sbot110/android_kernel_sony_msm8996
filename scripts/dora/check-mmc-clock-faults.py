#!/usr/bin/env python3
"""Fault-test complete production SDHCI core and MSM clock functions.

Only API/data plumbing and register access are modeled. Object constants and
MSM DLL masks come from the production headers/source. Function-like register
encoders remain scaffolding; this test makes no protocol/register-layout claim.
"""
from pathlib import Path
import re
import argparse
import os
import shlex
import subprocess
import tempfile
import resource

ROOT = Path(__file__).resolve().parents[2]


def extract_core_function(source, name):
    match = re.search(r'^(?:(?:static|inline)\s+)*(?:void|int|bool)\s+' +
                      re.escape(name) + r'\s*\([^;]*?\)\s*\{', source,
                      re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


CORE_HOST = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define EPROBE_DEFER 517
#define WARN_ON(x) assert(!(x))
#define WARN(x,...) assert(!(x))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define pr_err(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_debug(...) ((void)0)
#define BUG() assert(0)
#define wmb() ((void)0)
#define mmc_log_string(...) ((void)0)
#define trace_mmc_cmd_rw_start(...) ((void)0)
#define mmiowb() ((void)0)
#define HZ 100
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define FIELD_GET(mask,v) ((v)&(mask))
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) (((~0U)<<(l)) & ((~0U)>>(31-(h))))
#define SDHCI_MAKE_CMD(op,flags) (((op)<<8)|(flags))
#define SDHCI_MAKE_BLKSZ(boundary,size) (size)
#define MMC_ERR_ICE_CFG 0
#define spin_lock_irqsave(p,flags) do { (flags)=0;assert(!*(p));*(p)=1; } while (0)
#define spin_unlock_irqrestore(p,flags) do { (void)(flags);assert(*(p));*(p)=0; } while (0)
struct mmc_request;
struct mmc_command;
typedef uint64_t u64;
#define __le64 uint64_t
#define __force
struct mmc_card { unsigned rca; };
struct mmc_data { int error; struct mmc_command *stop; };
struct mmc_command {
    int error; unsigned opcode, flags, arg, busy_timeout;
    struct mmc_request *mrq; struct mmc_data *data;
};
struct mmc_request { struct mmc_command *cmd, *sbc, *stop; struct mmc_data *data; };
struct mmc_ios { unsigned clock, timing, power_mode, vdd, drv_type, bus_width; };
struct sdhci_host;
struct mmc_host;
struct mmc_ops { int (*get_cd)(struct mmc_host *); };
struct mmc_host {
    struct sdhci_host *private; struct mmc_ops *ops;
    void *card; struct { void *vmmc; } supply;
    unsigned actual_clock, max_busy_timeout;
    void *cqe_private;bool cqe_on;unsigned err_stats[1];
};
struct sdhci_ops {
    int (*power_error)(struct sdhci_host *);
    void (*set_clock)(struct sdhci_host *, unsigned);
    int (*enable_controller_clock)(struct sdhci_host *);
    void (*set_power)(struct sdhci_host *, unsigned, unsigned);
    unsigned (*get_max_timeout_count)(struct sdhci_host *);
    void (*set_bus_width)(struct sdhci_host *, unsigned);
    void (*set_uhs_signaling)(struct sdhci_host *, unsigned);
    void (*platform_send_init_74_clocks)(struct sdhci_host *, unsigned);
    void (*config_auto_tuning_cmd)(struct sdhci_host *, bool, int);
};
struct sdhci_host {
    struct mmc_host *mmc; struct sdhci_ops *ops; int lock;
    unsigned clock, timing, pwr, flags, quirks, quirks2, version, timeout_clk, drv_type;
    bool reinit_uhs, preset_enabled, sdio_irq_async_status;
    struct mmc_command *cmd, *data_cmd; struct mmc_request *mrq;
    unsigned long long data_timeout; unsigned long data_start_time;
    int fault;
    unsigned desc_sz, sdma_boundary, ier, cqe_ier;bool cqe_on;
};
static unsigned reads, writes, prepared_data, timers, completions, finishes;
static unsigned clock_calls, other_calls, cd_calls;
static bool forbid_mmio;
static int clock_error;
static unsigned clock_fail_at;
static int power_fault;
static struct mmc_request *last_done, *last_finish;
static unsigned long jiffies;
static struct sdhci_host *mmc_priv(struct mmc_host *mmc) { return mmc->private; }
static int mmc_card_sdio(void *card) { return card != NULL; }
static const char *mmc_hostname(struct mmc_host *mmc) { (void)mmc;return "core-model"; }
static u32 sdhci_readl(struct sdhci_host *h, unsigned reg)
{ (void)h;(void)reg;assert(!forbid_mmio);reads++;return 0; }
static u16 sdhci_readw(struct sdhci_host *h, unsigned reg) { return sdhci_readl(h,reg); }
static u8 sdhci_readb(struct sdhci_host *h, unsigned reg) { return sdhci_readl(h,reg); }
static void sdhci_writel(struct sdhci_host *h, u32 value, unsigned reg)
{ (void)h;(void)value;(void)reg;assert(!forbid_mmio);writes++; }
static void sdhci_writew(struct sdhci_host *h, u16 value, unsigned reg) { sdhci_writel(h,value,reg); }
static void sdhci_writeb(struct sdhci_host *h, u8 value, unsigned reg) { sdhci_writel(h,value,reg); }
static void sdhci_finish_mrq(struct sdhci_host *h, struct mmc_request *mrq)
{ assert(h && mrq);finishes++;last_finish=mrq; }
static void mmc_request_done(struct mmc_host *mmc, struct mmc_request *mrq)
{ assert(mmc && mrq);completions++;last_done=mrq; }
static bool sdhci_data_line_cmd(struct mmc_command *cmd) { return cmd->data != NULL; }
static void sdhci_prepare_data(struct sdhci_host *h, struct mmc_command *cmd)
{ (void)h;(void)cmd;assert(!forbid_mmio);prepared_data++; }
static void sdhci_set_transfer_mode(struct sdhci_host *h, struct mmc_command *cmd)
{ (void)h;(void)cmd;assert(!forbid_mmio); }
static void sdhci_mod_timer(struct sdhci_host *h, struct mmc_request *mrq, unsigned long when)
{ (void)h;(void)mrq;(void)when;assert(!forbid_mmio);timers++; }
static unsigned long nsecs_to_jiffies(unsigned long long value) { return value; }
static unsigned long ktime_get(void) { return 0; }
static void udelay(unsigned value) { (void)value; }
static void sdhci_dumpregs(struct sdhci_host *h) { (void)h;assert(!forbid_mmio);other_calls++; }
static void sdhci_dump_state(struct sdhci_host *h) { (void)h;assert(!forbid_mmio);other_calls++; }
static bool sdhci_check_state(struct sdhci_host *h) { return !h->clock || !h->pwr; }
static void sdhci_led_activate(struct sdhci_host *h) { (void)h;other_calls++; }
static bool sdhci_auto_cmd12(struct sdhci_host *h, struct mmc_request *mrq)
{ (void)h;(void)mrq;return false; }
static bool sdhci_check_auto_tuning(struct sdhci_host *h, struct mmc_command *cmd)
{ (void)h;(void)cmd;return false; }
static int sdhci_get_tuning_cmd(struct sdhci_host *h) { (void)h;return 0; }
static int mmc_regulator_set_ocr(struct mmc_host *h, void *r, unsigned ocr)
{ (void)h;(void)r;(void)ocr;other_calls++;return 0; }
static void sdhci_enable_preset_value(struct sdhci_host *h, bool enable)
{ (void)h;(void)enable;other_calls++; }
static void sdhci_cfg_irq(struct sdhci_host *h, bool a, bool b)
{ (void)h;(void)a;(void)b;other_calls++; }
static void sdhci_enable_sdio_irq_nolock(struct sdhci_host *h, bool enable)
{ (void)h;(void)enable;other_calls++; }
static void sdhci_set_power(struct sdhci_host *h, unsigned mode, unsigned vdd)
{ (void)h;(void)mode;(void)vdd;other_calls++; }
static bool sdhci_presetable_values_change(struct sdhci_host *h, struct mmc_ios *ios)
{ (void)h;(void)ios;return false; }
static bool sdhci_preset_needed(struct sdhci_host *h, unsigned timing)
{ (void)h;(void)timing;return false; }
static u16 sdhci_get_preset_value(struct sdhci_host *h) { (void)h;return 0; }
static void sdhci_do_reset(struct sdhci_host *h, unsigned mask)
{ (void)h;(void)mask;assert(!forbid_mmio);other_calls++; }
static void sdhci_reinit(struct sdhci_host *h) { (void)h;other_calls++; }
static void test_set_bus_width(struct sdhci_host *h, unsigned width)
{ (void)h;(void)width;other_calls++; }
static void test_set_uhs(struct sdhci_host *h,unsigned timing)
{ (void)h;(void)timing;assert(!forbid_mmio);other_calls++; }
static int test_controller_clock(struct sdhci_host *h)
{ other_calls++;h->fault=power_fault;if(power_fault)forbid_mmio=true;return power_fault; }
static void test_set_power(struct sdhci_host *h,unsigned mode,unsigned vdd)
{ (void)mode;(void)vdd;other_calls++;h->fault=power_fault;if(power_fault)forbid_mmio=true; }
static int test_power_error(struct sdhci_host *h) { return h->fault; }
static void test_set_clock(struct sdhci_host *h, unsigned rate)
{
    assert(!h->lock);clock_calls++;
    if (clock_error && (!clock_fail_at || clock_calls==clock_fail_at))
        { h->fault=clock_error;h->clock=0;h->mmc->actual_clock=0;forbid_mmio=true; }
    else { h->mmc->actual_clock=rate;if(!rate)h->clock=0; }
}
static int test_get_cd(struct mmc_host *mmc) { (void)mmc;cd_calls++;return 1; }
struct cqhci_host;
struct cqhci_ops {
    void (*enable)(struct mmc_host *);
    void (*enhanced_strobe_mask)(struct mmc_host *, bool);
};
struct cqhci_host {
    struct cqhci_ops *ops;bool enabled,activated,recovery_halt;
    unsigned rca,qcnt;int lock;
    struct { struct mmc_request *mrq;unsigned flags; } slot[1];
};
static unsigned cqe_allocs,cqe_preparations,cqe_io,cqe_enables;
static u64 cqe_descriptor;
static void cqe_step(void) { assert(!forbid_mmio);cqe_preparations++; }
static int cqhci_tag(struct mmc_request *mrq) { assert(mrq);return 0; }
static int cqhci_host_alloc_tdl(struct cqhci_host *h) { (void)h;assert(!forbid_mmio);cqe_allocs++;return 0; }
static bool cqhci_host_is_crypto_supported(struct cqhci_host *h) { (void)h;return false; }
static void cqhci_crypto_enable(struct cqhci_host *h) { (void)h;cqe_step(); }
static void __cqhci_enable(struct cqhci_host *h) { assert(!forbid_mmio);cqe_enables++;h->activated=true; }
static u32 cqhci_readl(struct cqhci_host *h, unsigned reg) { (void)h;(void)reg;assert(!forbid_mmio);cqe_io++;return 0; }
static void cqhci_writel(struct cqhci_host *h,u32 val,unsigned reg) { (void)h;(void)val;(void)reg;assert(!forbid_mmio);cqe_io++; }
static int cqhci_crypto_get_ctx(struct cqhci_host *h,struct mmc_request *r,u64 *ctx) { (void)h;(void)r;*ctx=0;cqe_step();return 0; }
static void *get_desc(struct cqhci_host *h,int tag) { (void)h;(void)tag;cqe_step();return &cqe_descriptor; }
static void cqhci_prep_task_desc(struct mmc_request *r,u64 *data,int v) { (void)r;(void)v;*data=7;cqe_step(); }
static u64 cpu_to_le64(u64 v) { return v; }
static void cqhci_prep_crypto_desc(struct cqhci_host *h,u64 *desc,u64 ctx) { (void)h;(void)desc;(void)ctx;cqe_step(); }
static int cqhci_prep_tran_desc(struct mmc_request *r,struct cqhci_host *h,int tag) { (void)r;(void)h;(void)tag;cqe_step();return 0; }
static void sdhci_msm_pm_qos_irq_vote(struct sdhci_host *h) { (void)h;cqe_step(); }
static void cqhci_pm_qos_vote(struct sdhci_host *h,struct mmc_request *r) { (void)h;(void)r;cqe_step(); }
static void cqhci_prep_dcmd_desc(struct mmc_host *m,struct mmc_request *r) { (void)m;(void)r;cqe_step(); }
static void cqhci_dumpregs(struct cqhci_host *h) { (void)h;cqe_step(); }
static void cqhci_post_req(struct mmc_host *m,struct mmc_request *r) { (void)m;(void)r;cqe_step(); }
static void cqhci_complete_crypto_desc(struct cqhci_host *h,struct mmc_request *r,void *arg) { (void)h;(void)r;(void)arg;cqe_step(); }
'''

CORE_TESTS = r'''
static struct sdhci_host host;
static struct mmc_host mmc;
static struct sdhci_ops ops;
static struct mmc_ops mmc_ops;
static struct mmc_command cmd;
static struct mmc_data data;
static struct mmc_request request;
static struct cqhci_host cqe;
static struct cqhci_ops cqe_ops;
static void reset_core(void)
{
    memset(&host,0,sizeof(host));memset(&mmc,0,sizeof(mmc));memset(&ops,0,sizeof(ops));
    memset(&cmd,0,sizeof(cmd));memset(&data,0,sizeof(data));memset(&request,0,sizeof(request));
    memset(&cqe,0,sizeof(cqe));memset(&cqe_ops,0,sizeof(cqe_ops));
    ops.power_error=test_power_error;ops.set_clock=test_set_clock;ops.set_bus_width=test_set_bus_width;
    ops.set_uhs_signaling=test_set_uhs;
    mmc_ops.get_cd=test_get_cd;mmc.private=&host;mmc.ops=&mmc_ops;mmc.actual_clock=400000;
    host.mmc=&mmc;host.ops=&ops;host.clock=400000;host.pwr=1;
    request.cmd=&cmd;request.data=&data;cmd.mrq=&request;cmd.opcode=17;
    reads=writes=prepared_data=timers=completions=finishes=clock_calls=other_calls=cd_calls=0;
    last_done=last_finish=NULL;forbid_mmio=true;clock_error=power_fault=0;clock_fail_at=0;
    cqe_allocs=cqe_preparations=cqe_io=cqe_enables=0;mmc.cqe_private=&cqe;cqe.ops=&cqe_ops;cqe.enabled=true;
}
static void test_core_barriers(void)
{
    const int errors[]={-EIO,-ETIMEDOUT,-EPROBE_DEFER};
    unsigned i;
    for(i=0;i<sizeof(errors)/sizeof(errors[0]);i++) {
        reset_core();host.fault=errors[i];
        sdhci_request(&mmc,&request);
        assert(cmd.error==errors[i] && data.error==errors[i]);
        assert(completions==1 && last_done==&request && !finishes);
        assert(!reads && !writes && !prepared_data && !timers && !other_calls && !cd_calls && !host.lock);

        reset_core();host.fault=errors[i];request.data=NULL;
        sdhci_request(&mmc,&request);
        assert(cmd.error==errors[i] && completions==1 && last_done==&request && !finishes);
        assert(!reads && !writes && !prepared_data && !timers && !other_calls && !cd_calls && !host.lock);

        reset_core();host.fault=errors[i];
        sdhci_send_command(&host,&cmd);
        assert(cmd.error==errors[i] && finishes==1 && last_finish==&request && !completions);
        assert(!reads && !writes && !prepared_data && !timers && !other_calls && !host.cmd && !host.data_cmd);

        reset_core();clock_error=errors[i];
        struct mmc_ios ios={.clock=50000000,.timing=99,.power_mode=MMC_POWER_ON,.bus_width=8};
        sdhci_set_ios(&mmc,&ios);
        assert(host.fault==errors[i] && clock_calls==1 && host.clock==0 && mmc.actual_clock==0);
        assert(!reads && !writes && !prepared_data && !timers && !other_calls && !host.lock);

        reset_core();host.fault=errors[i];
        ios.clock=host.clock;
        sdhci_set_ios(&mmc,&ios);
        assert(host.fault==errors[i] && !clock_calls && !reads && !writes && !other_calls && !host.lock);
        reset_core();host.fault=errors[i];ios.clock=0;ios.power_mode=MMC_POWER_OFF;
        sdhci_set_ios(&mmc,&ios);
        assert(host.fault==errors[i] && clock_calls==1 && !host.clock && !mmc.actual_clock);
        assert(!reads && !writes && !other_calls && !host.lock);
        reset_core();host.fault=errors[i];host.version=SDHCI_SPEC_300;
        ios.clock=host.clock;ios.power_mode=MMC_POWER_UP;sdhci_set_ios(&mmc,&ios);
        assert(!clock_calls && !reads && !writes && !other_calls && !host.lock);

        for(unsigned fail_at=2;fail_at<=3;fail_at++) {
            reset_core();forbid_mmio=false;clock_error=errors[i];clock_fail_at=fail_at;
            host.version=SDHCI_SPEC_300;host.preset_enabled=true;
            host.quirks=SDHCI_QUIRK_RESET_CMD_DATA_ON_IOS;
            ios.clock=50000000;ios.power_mode=MMC_POWER_ON;
            sdhci_set_ios(&mmc,&ios);
            assert(host.fault==errors[i] && clock_calls==fail_at);
            assert(!host.clock && !mmc.actual_clock && !host.lock);
        }
        reset_core();power_fault=errors[i];ops.enable_controller_clock=test_controller_clock;
        ios.clock=host.clock;ios.timing=host.timing;ios.power_mode=MMC_POWER_UP;sdhci_set_ios(&mmc,&ios);
        assert(host.fault==errors[i] && other_calls==1 && !clock_calls && !reads && !writes && !host.lock);
        reset_core();power_fault=errors[i];ops.set_power=test_set_power;
        ios.clock=host.clock;ios.timing=host.timing;ios.power_mode=MMC_POWER_ON;sdhci_set_ios(&mmc,&ios);
        assert(host.fault==errors[i] && other_calls==1 && !clock_calls && !reads && !writes && !host.lock);

        reset_core();host.fault=errors[i];
        assert(cqhci_request(&mmc,&request)==errors[i]);
        assert(!cqe_allocs && !cqe_preparations && !cqe_io && !cqe_enables && !cqe.qcnt);
        struct mmc_card card={.rca=42};cqe.enabled=false;
        assert(cqhci_enable(&mmc,&card)==errors[i]);
        assert(!cqe_allocs && !cqe_preparations && !cqe_io && !cqe_enables && !cqe.enabled);
        sdhci_cqe_enable(&mmc);sdhci_msm_cqe_enable(&mmc);
        assert(!reads && !writes && !other_calls && !host.lock && !host.cqe_on);
    }
    /* Optional callback remains compatible with healthy generic hosts. */
    reset_core();ops.power_error=NULL;forbid_mmio=false;
    sdhci_send_command(&host,&cmd);
    assert(!cmd.error && host.cmd==&cmd && writes==2 && prepared_data==1 && timers==1);
    reset_core();forbid_mmio=false;
    sdhci_request(&mmc,&request);
    assert(!cmd.error && host.cmd==&cmd && writes==2 && cd_calls==1 && !host.lock);
    reset_core();forbid_mmio=false;
    assert(!cqhci_request(&mmc,&request) && cqe.qcnt==1 && cqe.slot[0].mrq==&request);
    assert(cqe_enables==1 && cqe_preparations && cqe_io && mmc.cqe_on && !cqe.lock);
    reset_core();forbid_mmio=false;cqe.enabled=false;
    struct mmc_card card={.rca=42};assert(!cqhci_enable(&mmc,&card));
    assert(cqe.enabled && cqe.rca==42 && cqe_allocs==1 && cqe_enables==1);
    puts("PASS: full production SDHCI request/command hooks reject faulted work before MMIO and preserve errno");
    puts("PASS: full production set_ios never publishes a failed platform clock request; optional hook stays compatible");
    puts("PASS: full production set_ios guards power callbacks and every preset clock restart; CQHCI/core/MSM CQE reject faulted work before MMIO");
}
int main(void) { test_core_barriers();return 0; }
'''


def core_harness(root, source_override=None, header_override=None, msm_override=None, cqhci_override=None):
    root = Path(root)
    source = source_override if source_override is not None else (root / 'drivers/mmc/host/sdhci.c').read_text()
    header = header_override if header_override is not None else (root / 'drivers/mmc/host/sdhci.h').read_text()
    bodies = extract_core_function(header, 'sdhci_power_error')
    for name in ('sdhci_send_command', 'sdhci_request', 'sdhci_set_ios', 'sdhci_cqe_enable'):
        bodies += extract_core_function(source, name)
    msm = msm_override if msm_override is not None else (root / 'drivers/mmc/host/sdhci-msm.c').read_text()
    cq = cqhci_override if cqhci_override is not None else (root / 'drivers/mmc/host/cqhci.c').read_text()
    bodies += extract_core_function(msm, 'sdhci_msm_cqe_enable')
    for name in ('cqhci_enable','cqhci_request'):
        bodies += extract_core_function(cq, name)
    defined = set(re.findall(r'^#define\s+(\w+)', CORE_HOST, re.M))
    constants = sorted(set(re.findall(r'\b(?:MMC|SDHCI|CQHCI)_[A-Z0-9_]+\b', bodies+CORE_TESTS)) - defined)
    macro_source = '\n'.join((root / filename).read_text() for filename in
                            ('drivers/mmc/host/sdhci.h','drivers/mmc/host/cqhci.h',
                             'include/linux/mmc/host.h','include/linux/mmc/core.h',
                             'include/linux/mmc/mmc.h'))
    macro_source = macro_source.replace('\\\n',' ')
    scaffolding = ''
    for name in constants:
        match = re.search(r'^#define\s+'+re.escape(name)+r'\s+[^\n]+', macro_source,re.M)
        assert match, 'unmodeled production constant: '+name
        scaffolding += match.group()+'\n'
    return CORE_HOST + scaffolding + bodies + CORE_TESTS


def core_mutations(root):
    """Yield focused source/header mutations; full production bodies stay intact."""
    root = Path(root)
    source = (root / 'drivers/mmc/host/sdhci.c').read_text()
    header = (root / 'drivers/mmc/host/sdhci.h').read_text()
    guard = '\terror = sdhci_power_error(host);'
    for name in ('sdhci_request','sdhci_send_command'):
        body = extract_core_function(source,name)
        assert body.count(guard)==1
        changed = body.replace(guard,'\terror = 0;',1)
        yield name + ' fault bypass', core_harness(root,source.replace(body,changed,1),header)
    body = extract_core_function(source,'sdhci_set_ios')
    guards = list(re.finditer(r'if \(sdhci_power_error\(host\)\)\s*\n\s*return;',body))
    assert len(guards)==5, 'set_ios post-callback guards changed; review fault coverage'
    labels = ['initial clock publication','controller power','platform power','preset first restart','preset second restart']
    for guard,label in zip(guards,labels):
        # The following post-power barrier also fences a failed controller
        # callback in this fixture. Do not demand failure for deleting a
        # redundant guard that leaves the tested observable contract intact.
        if label == 'controller power':
            continue
        changed = body[:guard.start()] + body[guard.end():]
        yield 'set_ios '+label+' guard omitted', core_harness(root,source.replace(body,changed,1),header)
    changed = body.replace('if (sdhci_power_error(host)) {','if (0) {',1)
    assert changed != body
    yield 'set_ios entry fault bypass', core_harness(root,source.replace(body,changed,1),header)
    body = extract_core_function(source,'sdhci_cqe_enable')
    changed = body.replace('if (sdhci_power_error(host))','if (0)',1)
    assert changed != body
    yield 'generic CQE fault bypass', core_harness(root,source.replace(body,changed,1),header)
    msm = (root / 'drivers/mmc/host/sdhci-msm.c').read_text()
    body = extract_core_function(msm,'sdhci_msm_cqe_enable')
    changed = body.replace('if (sdhci_power_error(host))','if (0)',1)
    assert changed != body
    yield 'MSM CQE fault bypass', core_harness(root,msm_override=msm.replace(body,changed,1))
    cq = (root / 'drivers/mmc/host/cqhci.c').read_text()
    for function in ('cqhci_enable','cqhci_request'):
        body = extract_core_function(cq,function)
        changed,n = re.subn(r'err = sdhci_power_error\([^;]+;', 'err = 0;',body,count=1)
        assert n==1
        yield function+' fault bypass', core_harness(root,cqhci_override=cq.replace(body,changed,1))


CLOCK_HOST = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint16_t u16;
#define U32_MAX UINT32_MAX
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,n) ((v)=(n))
#define cmpxchg(p,o,n) ({ int prior=*(p);if(prior==(o))*(p)=(n);prior; })
#define atomic_read(p) (*(p))
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p)>=(uintptr_t)-4095)
#define pr_err(...) ((void)0)
#define mb() ((void)0)
#define BIT(n) (1U<<(n))
struct clk { int id;unsigned long rate,physical_rate; };
struct mmc_card { bool strobe; };
struct mmc_ios { unsigned timing,old_rate; };
struct mmc_host { struct mmc_card *card;struct mmc_ios ios;unsigned actual_clock; };
struct sdhci_host { void *ioaddr,*private;struct mmc_host *mmc;unsigned clock; };
struct sdhci_pltfm_host { void *priv; };
struct sdhci_msm_reg_data { int power_error; };
struct sdhci_msm_slot_reg_data { struct sdhci_msm_reg_data *vdd_data,*vdd_io_data; };
struct sdhci_msm_pltfm_data { struct sdhci_msm_slot_reg_data *vreg_data;unsigned bus_clk_cnt; };
struct sdhci_msm_offset;
struct sdhci_msm_host {
    const struct sdhci_msm_offset *offset;
    struct sdhci_msm_pltfm_data *pdata;
    struct { int error;unsigned client_handle; } msm_bus_vote;
    struct clk *clk,*bus_aggr_clk;
    unsigned clk_rate;
    int controller_clock;
    bool tuning_done,enhanced_strobe,calibration_done,use_cdclp533;
};
enum fault { NO_FAULT,PREPARE_FAIL,PREBUS_FAIL,SOURCE_FAIL,SOURCE_ZERO,
    AGGR_INVALID,AGGR_FAIL,AGGR_ZERO,PIN_FAIL,FINALBUS_FAIL,FINAL_SOURCE_ZERO,
    DLL_TIMEOUT,SDCLK_TIMEOUT,DISABLE_FAIL };
static enum fault fault;
static unsigned source_gets,timing_reads,timing_writes,sdclk_stops,mask_writes;
static unsigned generic_clocks,polls,prepare_calls,pin_calls,bus_calls,source_sets,aggr_sets;
static u32 dll_bits;
static int injected_error=-EREMOTEIO;
static bool apply_before_error;
static char events[256];static unsigned nr_events;
static void event(char value) { assert(nr_events+1<sizeof(events));events[nr_events++]=value;events[nr_events]=0; }
static void *sdhci_priv(struct sdhci_host *host) { return host->private; }
static const char *mmc_hostname(struct mmc_host *mmc) { (void)mmc;return "clock-model"; }
static bool mmc_card_strobe(struct mmc_card *card) { return card->strobe; }
static u32 readl_relaxed(const void *address) { u32 v;event('t');timing_reads++;memcpy(&v,address,sizeof(v));return v; }
static void writel_relaxed(u32 value,void *address) { event('T');timing_writes++;memcpy(address,&value,sizeof(value)); }
static void sdhci_msm_writel_relaxed(u32 value,struct sdhci_host *h,unsigned reg)
{ assert(value==0);mask_writes++;event('M');memcpy((char *)h->ioaddr+reg,&value,sizeof(value)); }
static void sdhci_writew(struct sdhci_host *h,u16 value,unsigned reg)
{ assert(value==0);sdclk_stops++;event('X');memcpy((char *)h->ioaddr+reg,&value,sizeof(value)); }
static u16 sdhci_readw(struct sdhci_host *h,unsigned reg)
{ u16 value;memcpy(&value,(char *)h->ioaddr+reg,sizeof(value));return value; }
static int sdhci_msm_prepare_clocks(struct sdhci_host *h,bool enable)
{
    struct sdhci_msm_host *m=((struct sdhci_pltfm_host *)h->private)->priv;
    prepare_calls++;event(enable?'P':'p');
    if (enable && fault==PREPARE_FAIL)return injected_error;
    if (!enable && fault==DISABLE_FAIL)return injected_error;
    m->controller_clock=enable;return 0;
}
static u32 sdhci_msm_get_sup_clk_rate(struct sdhci_host *h,u32 rate) { (void)h;return rate; }
static unsigned sdhci_msm_bw_for_rate(struct mmc_ios *ios,unsigned rate) { (void)ios;return rate; }
static int sdhci_msm_bus_get_and_set_vote(struct sdhci_host *h,unsigned vote)
{ (void)h;(void)vote;event('B');bus_calls++;return fault==PREBUS_FAIL?injected_error:0; }
static int clk_set_rate(struct clk *clk,unsigned long rate)
{
    if(clk->id==1){event('R');source_sets++;if(fault==SOURCE_FAIL){if(apply_before_error)clk->physical_rate=rate;return injected_error;}}
    else {event('S');aggr_sets++;if(fault==AGGR_FAIL){if(apply_before_error)clk->physical_rate=rate;return injected_error;}}
    clk->rate=clk->physical_rate=rate;return 0;
}
static unsigned long clk_get_rate(struct clk *clk)
{
    if(clk->id==1){event('G');source_gets++;if(fault==SOURCE_ZERO || (fault==FINAL_SOURCE_ZERO && source_gets>1))return 0;}
    else {event('H');if(fault==AGGR_ZERO)return 0;}
    return clk->rate;
}
static long sdhci_msm_get_bus_aggr_clk_rate(struct sdhci_host *h,unsigned rate)
{ (void)h;event('A');return fault==AGGR_INVALID?-EINVAL:(long)rate/2; }
static int sdhci_msm_config_pinctrl_drv_type(struct sdhci_msm_pltfm_data *p,unsigned rate)
{ (void)p;(void)rate;event('I');pin_calls++;return fault==PIN_FAIL?injected_error:0; }
static int sdhci_msm_bus_voting(struct sdhci_host *h,int enabled)
{ (void)h;assert(enabled);event('V');bus_calls++;return fault==FINALBUS_FAIL?injected_error:0; }
static void sdhci_msm_toggle_fifo_write_clk(struct sdhci_host *h) { (void)h;event('Q'); }
#define readl_poll_timeout(address,value,condition,delay,timeout) ({ \
    (void)(address);(void)(delay);(void)(timeout);event('L');polls++; \
    (value)=dll_bits;(fault==DLL_TIMEOUT || !(condition)) ? -ETIMEDOUT : 0; })
'''

CLOCK_TESTS = r'''
static struct sdhci_host host;
static struct sdhci_pltfm_host platform;
static struct sdhci_msm_host msm;
static struct sdhci_msm_pltfm_data pdata;
static struct sdhci_msm_slot_reg_data slot;
static struct sdhci_msm_reg_data rail;
static struct mmc_host mmc;
static struct mmc_card card;
static struct clk source,aggr;
static u32 registers[1024];
static void reset_clock(void)
{
    memset(&host,0,sizeof(host));memset(&platform,0,sizeof(platform));memset(&msm,0,sizeof(msm));
    memset(&pdata,0,sizeof(pdata));memset(&slot,0,sizeof(slot));memset(&rail,0,sizeof(rail));
    memset(&mmc,0,sizeof(mmc));memset(&card,0,sizeof(card));memset(registers,0,sizeof(registers));
    memset(events,0,sizeof(events));nr_events=0;
    host.private=&platform;platform.priv=&msm;host.mmc=&mmc;mmc.card=&card;host.ioaddr=registers;
    msm.pdata=&pdata;pdata.vreg_data=&slot;slot.vdd_data=&rail;msm.offset=&sdhci_msm_offset_mci_present;
    source=(struct clk){.id=1,.rate=400000,.physical_rate=400000};
    aggr=(struct clk){.id=2,.rate=200000,.physical_rate=200000};
    msm.clk=&source;msm.bus_aggr_clk=&aggr;pdata.bus_clk_cnt=1;msm.msm_bus_vote.client_handle=1;
    msm.clk_rate=host.clock=mmc.actual_clock=400000;msm.controller_clock=1;
    mmc.ios.timing=MMC_TIMING_MMC_HS400;
    mmc.ios.old_rate=0;dll_bits=CORE_DLL_LOCK|CORE_DDR_DLL_LOCK;
    u16 sdclk=SDHCI_CLOCK_INT_STABLE|SDHCI_CLOCK_CARD_EN;
    memcpy((char *)host.ioaddr+SDHCI_CLOCK_CONTROL,&sdclk,sizeof(sdclk));
    fault=NO_FAULT;apply_before_error=false;source_gets=timing_reads=timing_writes=sdclk_stops=mask_writes=0;
    generic_clocks=polls=prepare_calls=pin_calls=bus_calls=source_sets=aggr_sets=0;
}
static void assert_quarantined(int error,bool prerequisites_failed)
{
    if(msm.msm_bus_vote.error!=error || host.clock || mmc.actual_clock)
        fprintf(stderr,"clock fault %d: expected %d got %d, host=%u actual=%u events=%s\n",
            fault,error,msm.msm_bus_vote.error,host.clock,mmc.actual_clock,events);
    assert(msm.msm_bus_vote.error==error && host.clock==0 && mmc.actual_clock==0);
    assert(sdhci_readw(&host,SDHCI_CLOCK_CONTROL)==0 && sdclk_stops && mask_writes);
    if(prerequisites_failed)assert(!timing_reads && !timing_writes && !polls && !generic_clocks);
    unsigned events_before=nr_events,reads_before=timing_reads,writes_before=timing_writes;
    sdhci_msm_set_clock(&host,50000000);
    assert(msm.msm_bus_vote.error==error && !host.clock && !mmc.actual_clock);
    assert(timing_reads==reads_before && timing_writes==writes_before);
    assert(nr_events==events_before+2); /* only safe SDCLK stop + IRQ mask */
    assert(sdhci_msm_latch_error(&host,-EAGAIN)==error);
}
static void test_clock_prerequisites(void)
{
    const enum fault failures[]={PREPARE_FAIL,PREBUS_FAIL,SOURCE_FAIL,SOURCE_ZERO,
        AGGR_INVALID,AGGR_FAIL,AGGR_ZERO,PIN_FAIL,FINALBUS_FAIL,FINAL_SOURCE_ZERO};
    for(unsigned i=0;i<sizeof(failures)/sizeof(failures[0]);i++) {
        reset_clock();fault=failures[i];sdhci_msm_set_clock(&host,50000000);
        int expected=(fault==SOURCE_ZERO || fault==AGGR_ZERO || fault==FINAL_SOURCE_ZERO)?-EIO:
            fault==AGGR_INVALID?-EINVAL:injected_error;
        assert_quarantined(expected,true);
        if(fault==PREPARE_FAIL)assert(!source_sets && !aggr_sets && !bus_calls && !pin_calls);
        if(fault==PREBUS_FAIL)assert(!source_sets && !aggr_sets && !pin_calls);
        if(fault==SOURCE_FAIL || fault==SOURCE_ZERO)assert(!aggr_sets && !pin_calls);
    }
    reset_clock();rail.power_error=-ETIMEDOUT;sdhci_msm_set_clock(&host,50000000);
    assert_quarantined(-ETIMEDOUT,true);assert(!prepare_calls && !source_sets);
    reset_clock();msm.msm_bus_vote.error=-EIO;sdhci_msm_set_clock(&host,50000000);
    assert_quarantined(-EIO,true);assert(!prepare_calls && !source_sets);
    reset_clock();msm.controller_clock=0;rail.power_error=-EIO;
    sdhci_msm_set_clock(&host,50000000);
    assert(!host.clock && !mmc.actual_clock && msm.msm_bus_vote.error==-EIO);
    assert(!sdclk_stops && !mask_writes && !timing_reads && !timing_writes && !prepare_calls);
    for(unsigned i=0;i<2;i++) {
        reset_clock();fault=i?AGGR_FAIL:SOURCE_FAIL;apply_before_error=true;
        sdhci_msm_set_clock(&host,50000000);assert_quarantined(injected_error,true);
        if(i)assert(aggr.physical_rate==50000000 && aggr.rate==200000 && source.rate==100000000);
        else assert(source.physical_rate==100000000 && source.rate==400000 && !aggr_sets);
    }
    puts("PASS: complete MSM set_clock: ten prerequisite faults, first-error quarantine, safe SDCLK/IRQ stop, no timing/DLL MMIO");
}
static void test_clock_lock_and_success(void)
{
    reset_clock();fault=DLL_TIMEOUT;sdhci_msm_set_clock(&host,50000000);
    assert(polls==1 && !generic_clocks);assert_quarantined(-ETIMEDOUT,false);
    for(unsigned i=0;i<2;i++) {
        reset_clock();dll_bits=i?CORE_DLL_LOCK:CORE_DDR_DLL_LOCK;
        sdhci_msm_set_clock(&host,50000000);
        assert(polls==1 && !generic_clocks);assert_quarantined(-ETIMEDOUT,false);
    }
    reset_clock();fault=SDCLK_TIMEOUT;sdhci_msm_set_clock(&host,50000000);
    assert(generic_clocks==1);assert_quarantined(-ETIMEDOUT,false);
    reset_clock();sdhci_msm_set_clock(&host,50000000);
    assert(!msm.msm_bus_vote.error && source.rate==100000000 && msm.clk_rate==100000000);
    assert(mmc.actual_clock==50000000 && generic_clocks==1 && polls==1 && !sdclk_stops && !mask_writes);
    const char *prepare=strchr(events,'P'),*raise=strchr(events,'B'),*rate=strchr(events,'R');
    const char *pins=strchr(events,'I'),*vote=strchr(events,'V'),*timing=strchr(events,'t');
    assert(prepare && raise && rate && pins && vote && timing);
    assert(prepare<raise && raise<rate && rate<pins && pins<vote && vote<timing);
    reset_clock();mmc.ios.timing=MMC_TIMING_MMC_HS200;sdhci_msm_set_clock(&host,50000000);
    assert(!msm.msm_bus_vote.error && source.rate==50000000 && !polls && generic_clocks==1);
    puts("PASS: complete MSM set_clock: both DLL lock bits required, sticky DLL/SDCLK timeout, healthy HS200/HS400 order");
}
static void test_clock_stop(void)
{
    reset_clock();msm.controller_clock=0;sdhci_msm_set_clock(&host,0);
    assert(!timing_reads && !timing_writes && !generic_clocks && !mask_writes && !sdclk_stops);
    assert(!host.clock && !mmc.actual_clock && !msm.controller_clock && !msm.msm_bus_vote.error);
    reset_clock();sdhci_msm_set_clock(&host,0);
    assert(!host.clock && !mmc.actual_clock && !msm.controller_clock && !msm.msm_bus_vote.error);
    assert(timing_reads==1 && timing_writes==1 && generic_clocks==1 && prepare_calls==1);
    assert(strchr(events,'C')<strchr(events,'p'));assert(!sdhci_readw(&host,SDHCI_CLOCK_CONTROL));
    unsigned reads_before=timing_reads,writes_before=timing_writes;
    sdhci_msm_set_clock(&host,0);
    assert(timing_reads==reads_before && timing_writes==writes_before && generic_clocks==1);
    reset_clock();fault=DISABLE_FAIL;sdhci_msm_set_clock(&host,0);
    assert(msm.controller_clock && msm.msm_bus_vote.error==injected_error && !host.clock && !mmc.actual_clock);
    assert(generic_clocks==1 && sdclk_stops==1 && mask_writes==1);
    assert(strchr(events,'C')<strchr(events,'p'));
    reset_clock();sdhci_msm_set_clock(&host,U32_MAX);assert_quarantined(-ERANGE,true);
    reset_clock();mmc.ios.timing=MMC_TIMING_MMC_HS200;
    sdhci_msm_set_clock(&host,U32_MAX);assert_quarantined(-EINVAL,true);
    puts("PASS: complete MSM zero-clock hook: SDCLK stops before source gating, inaccessible controller receives no MMIO, disable failure stays sticky");
}
int main(void) { test_clock_prerequisites();test_clock_lock_and_success();test_clock_stop();return 0; }
'''


def balanced(source, pattern):
    match = re.search(pattern, source, re.M)
    assert match, pattern
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + ';\n'


def clock_harness(root, source_override=None):
    root = Path(root)
    source = source_override if source_override is not None else (root / 'drivers/mmc/host/sdhci-msm.c').read_text()
    definitions = ''
    # Use actual production macro values, not invented DLL-lock/register bits.
    macro_sources = source + '\n' + (root / 'drivers/mmc/host/sdhci.h').read_text() + '\n' + (root / 'include/linux/mmc/host.h').read_text()
    needed = {'CORE_CLK_PWRSAVE','CORE_HC_MCLK_SEL_MASK','CORE_HC_MCLK_SEL_HS400',
              'CORE_HC_MCLK_SEL_DFLT','CORE_HC_SELECT_IN_HS400','CORE_HC_SELECT_IN_EN',
              'CORE_HC_SELECT_IN_MASK','CORE_DLL_LOCK','CORE_DDR_DLL_LOCK','CORE_PWRSAVE_DLL',
              'SDHCI_CLOCK_CONTROL','SDHCI_CLOCK_INT_STABLE','SDHCI_CLOCK_CARD_EN',
              'MMC_TIMING_MMC_HS400','MMC_TIMING_MMC_HS200','MMC_TIMING_MMC_DDR52','MMC_TIMING_UHS_DDR50'}
    for name in sorted(needed):
        match = re.search(r'^#define\s+'+re.escape(name)+r'\s+[^\n]+', macro_sources, re.M)
        assert match, name
        definitions += match.group()+'\n'
    offsets = balanced(source, r'^struct sdhci_msm_offset\s*\{')
    offsets += balanced(source, r'^struct sdhci_msm_offset sdhci_msm_offset_mci_present\s*=\s*\{')
    bodies = ''.join(extract_core_function(source, name) for name in
                     ('sdhci_msm_power_error','sdhci_msm_host_error','sdhci_msm_latch_error','sdhci_msm_set_clock'))
    generic_clock = r'''
static void sdhci_set_clock(struct sdhci_host *h,unsigned rate)
{
    struct sdhci_msm_host *m=((struct sdhci_pltfm_host *)h->private)->priv;
    assert(m->controller_clock);
    generic_clocks++;event('C');h->mmc->actual_clock=rate;
    u16 value=(!rate || fault==SDCLK_TIMEOUT)?0:SDHCI_CLOCK_INT_STABLE|SDHCI_CLOCK_CARD_EN;
    memcpy((char *)h->ioaddr+SDHCI_CLOCK_CONTROL,&value,sizeof(value));
}
'''
    return CLOCK_HOST + definitions + offsets + generic_clock + bodies + CLOCK_TESTS


def clock_mutations(root):
    source = (root / 'drivers/mmc/host/sdhci-msm.c').read_text()
    mutations = [
        ('source clock error ignored', 'rc = clk_set_rate(msm_host->clk, sup_clock);',
         'clk_set_rate(msm_host->clk, sup_clock); rc = 0;'),
        ('bus vote error ignored', 'rc = sdhci_msm_bus_voting(host, 1);',
         'sdhci_msm_bus_voting(host, 1); rc = 0;'),
        ('pad error ignored', 'rc = sdhci_msm_config_pinctrl_drv_type(msm_host->pdata, clock);',
         'sdhci_msm_config_pinctrl_drv_type(msm_host->pdata, clock); rc = 0;'),
        ('fault cache retained', 'WRITE_ONCE(host->clock, 0);', '(void)host;'),
        ('actual clock cache retained', 'WRITE_ONCE(host->mmc->actual_clock, 0);', '(void)host;'),
        ('SDCLK stop omitted', 'sdhci_writew(host, 0, SDHCI_CLOCK_CONTROL);', '(void)host;'),
        ('one DLL lock accepted', '== (CORE_DLL_LOCK |\n\t\t\t\t\tCORE_DDR_DLL_LOCK)', '!= 0'),
        ('zero-clock accesses gated controller',
         'if (!atomic_read(&msm_host->controller_clock))\n\t\t\tgoto clocks_off;',
         'if (0)\n\t\t\tgoto clocks_off;'),
    ]
    for name, old, new in mutations:
        function = 'sdhci_msm_latch_error' if ('WRITE_ONCE' in old or 'sdhci_writew' in old) else 'sdhci_msm_set_clock'
        body = extract_core_function(source,function)
        assert old in body, name
        changed = body.replace(old,new,1)
        yield name, clock_harness(root, source.replace(body,changed,1))


def run_harness(code, name, scratch, expect_failure=False, sanitized=False, label=None):
    source, binary = scratch / (name+'.c'), scratch / name
    source.write_text(code)
    command = shlex.split(os.environ.get('HOSTCC','cc'))
    flags = ['-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror',
             '-Wno-unused-function','-Wno-unused-parameter','-Wno-sign-compare']
    flags += shlex.split(os.environ.get('HOSTCFLAGS',''))
    if sanitized:
        flags += ['-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie']
    subprocess.run(command+flags+[str(source),'-o',str(binary)],check=True)
    env = dict(os.environ)
    if sanitized:
        env.setdefault('ASAN_OPTIONS','detect_leaks=0')
    result = subprocess.run([str(binary)],env=env,capture_output=expect_failure,text=True)
    if expect_failure:
        if result.returncode == 0:
            raise AssertionError('fault tests accepted unsafe mutation: '+name)
        print('PASS: mutation rejected:', label or name)
    elif result.returncode:
        raise subprocess.CalledProcessError(result.returncode,[str(binary)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true',help='also require unsafe source mutations to fail')
    parser.add_argument('--sanitize', action='store_true',help='ASan/UBSan for the unmodified production bodies')
    args = parser.parse_args()
    # Expected assertion failures in mutation children must not leave dumps.
    _, hard_limit = resource.getrlimit(resource.RLIMIT_CORE)
    resource.setrlimit(resource.RLIMIT_CORE,(0,hard_limit))
    with tempfile.TemporaryDirectory(prefix='dora-mmc-clock-faults-') as directory:
        scratch = Path(directory)
        run_harness(core_harness(ROOT),'core',scratch,sanitized=args.sanitize)
        run_harness(clock_harness(ROOT),'clock',scratch,sanitized=args.sanitize)
        if args.self_test:
            for index, (name, code) in enumerate(list(core_mutations(ROOT))+list(clock_mutations(ROOT))):
                run_harness(code,'mutation%d'%index,scratch,expect_failure=True,label=name)
    print('Software fault model only: API/MMIO boundaries are mocked; no RPM ACK, card access or electrical measurement is made.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
