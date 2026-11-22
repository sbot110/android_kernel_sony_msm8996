#!/usr/bin/env python3
"""Exercise production SDHC power IRQ, clear, restore and resume fault paths.

The production functions and register/bit definitions are compiled unchanged.
MMIO, regulator/pin APIs, completion and scheduling are host models, with no
card or RPM traffic. Passing does not establish device IRQ behavior.
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
    match = re.search(r'^static\s+(?:int|void|bool|irqreturn_t)\s+' +
                      re.escape(name) + r'\s*\([^;]*?\)\s*\{', source, re.M | re.S)
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
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int irqreturn_t;
typedef long ktime_t;
#define IRQ_HANDLED 1
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define cmpxchg(p,o,n) __sync_val_compare_and_swap(p,o,n)
#define atomic_read(p) (*(p))
#define mb() ((void)0)
#define BIT(n) (1U<<(n))
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define mmc_log_string(...) ((void)0)
#define __WARN_printf(...) ((void)0)
#define WARN_ON(x) ((void)(x))
#define spin_lock_irqsave(p,f) do { assert(!*(p));*(p)=1;(f)=0; } while(0)
#define spin_unlock_irqrestore(p,f) do { (void)(f);assert(*(p));*(p)=0; } while(0)
struct mmc_host;
struct mmc_ops { int (*get_cd)(struct mmc_host *); };
struct mmc_card { bool sdio,ungate; };
struct mmc_ios { unsigned clock,timing; };
struct mmc_host {
    struct mmc_ops *ops;struct mmc_card *card;struct mmc_ios ios;unsigned actual_clock;
};
struct sdhci_host;
struct sdhci_ops { void (*check_power_status)(struct sdhci_host *,u32); };
struct sdhci_host {
    void *private,*ioaddr;struct mmc_host *mmc;struct sdhci_ops *ops;
    unsigned clock,pwr;int lock;
};
struct sdhci_pltfm_host { void *priv; };
struct sdhci_msm_reg_data { int power_error; };
struct sdhci_msm_slot_reg_data { struct sdhci_msm_reg_data *vdd_data,*vdd_io_data; };
struct sdhci_msm_pltfm_data { struct sdhci_msm_slot_reg_data *vreg_data; };
'''

PLUMBING = r'''
struct sdhci_msm_host {
    struct mmc_host *mmc;const struct sdhci_msm_offset *offset;
    struct sdhci_msm_pltfm_data *pdata;struct { int error; } msm_bus_vote;
    struct sdhci_msm_regs_restore regs_restore;
    int controller_clock,pwr_irq,pwr_irq_completion;
    unsigned caps_0,curr_pwr_state,curr_io_level,saved_tuning_phase;
    bool core_3_0v_support,vbias_skip_wa,pltfm_init_done,reg_store,mci_removed;
};
struct device { void *data; };
static struct sdhci_host host;
static struct sdhci_msm_host msm;
static unsigned char registers[4096];
static bool clear_stuck,wait_success,card_present;
static unsigned clears,completions,pad_writes,mask_enables,sdclk_stops,irq_enables;
static unsigned vreg_calls,pin_calls,vol_calls,dll_calls,phase_calls,waits;
static int vreg_error,pin_error,vol_error,dll_error,phase_error,trace_error;
static unsigned fail_vol_at,clear_losses;
static bool accessible(void) { return msm.controller_clock; }
static void *sdhci_priv(struct sdhci_host *h) { return h->private; }
static const char *mmc_hostname(struct mmc_host *m) { (void)m;return "irq-model"; }
static void udelay(unsigned d) { assert(d==10); }
static void sdhci_msm_dump_pwr_ctrl_regs(struct sdhci_host *h) { assert(h==&host); }
static u32 readl_relaxed(const void *p)
{ u32 value;assert(accessible());memcpy(&value,p,sizeof(value));return value; }
static void writel_relaxed(u32 value,void *p)
{
    assert(accessible());
    if(p==registers+msm.offset->CORE_VENDOR_SPEC)pad_writes++;
    if(p==registers+msm.offset->CORE_PWRCTL_MASK && value)mask_enables++;
    memcpy(p,&value,sizeof(value));
}
static u8 sdhci_msm_readb_relaxed(struct sdhci_host *h,unsigned reg)
{ assert(h==&host && accessible());return registers[reg]; }
static u32 sdhci_msm_readl_relaxed(struct sdhci_host *h,unsigned reg)
{ assert(h==&host);return readl_relaxed(registers+reg); }
static void sdhci_msm_writeb_relaxed(u8 value,struct sdhci_host *h,unsigned reg)
{
    assert(h==&host && accessible());
    if(reg==msm.offset->CORE_PWRCTL_CLEAR) {
        clears++;
        if(!clear_stuck && clears>clear_losses)
            registers[msm.offset->CORE_PWRCTL_STATUS]&=~value;
    }
    registers[reg]=value;
}
static void sdhci_msm_writel_relaxed(u32 value,struct sdhci_host *h,unsigned reg)
{ assert(h==&host);writel_relaxed(value,registers+reg); }
static void sdhci_writel(struct sdhci_host *h,u32 value,unsigned reg)
{ assert(h==&host);writel_relaxed(value,registers+reg); }
static void sdhci_writew(struct sdhci_host *h,u16 value,unsigned reg)
{
    assert(h==&host && accessible());
    if(reg==SDHCI_CLOCK_CONTROL && !value)sdclk_stops++;
    memcpy(registers+reg,&value,sizeof(value));
}
static void sdhci_writeb(struct sdhci_host *h,u8 value,unsigned reg)
{ assert(h==&host && accessible());registers[reg]=value; }
static void complete(int *done) { assert(done==&msm.pwr_irq_completion);(*done)++;completions++; }
static void init_completion(int *done) { *done=0; }
static unsigned msecs_to_jiffies(unsigned ms) { return ms; }
static unsigned wait_for_completion_timeout(int *done,unsigned timeout)
{ (void)done;assert(timeout);waits++;return wait_success; }
static int get_cd(struct mmc_host *m) { assert(m==host.mmc);return card_present; }
static int sdhci_msm_setup_vreg(struct sdhci_msm_pltfm_data *p,bool on,bool init)
{ assert(p==msm.pdata && !init);(void)on;vreg_calls++;return vreg_error; }
static int sdhci_msm_setup_pins(struct sdhci_msm_pltfm_data *p,bool on)
{ assert(p==msm.pdata);(void)on;pin_calls++;return pin_error; }
static int sdhci_msm_set_vdd_io_vol(struct sdhci_msm_pltfm_data *p,unsigned level,unsigned uv)
{ assert(p==msm.pdata && !uv && level<2);vol_calls++;return (!fail_vol_at || vol_calls==fail_vol_at)?vol_error:0; }
static void sdhci_msm_vbias_bypass_wa(struct sdhci_host *h) { assert(h==&host);pad_writes++; }
static int msm_init_cm_dll(struct sdhci_host *h,unsigned context)
{ assert(h==&host && context==DLL_INIT_FROM_CX_COLLAPSE_EXIT);dll_calls++;return dll_error; }
static int msm_config_cm_dll_phase(struct sdhci_host *h,unsigned phase)
{ assert(h==&host && phase==msm.saved_tuning_phase);phase_calls++;return phase_error; }
static void *dev_get_drvdata(struct device *d) { return d->data; }
static bool mmc_host_may_gate_card(struct mmc_card *c) { return c->ungate; }
static bool mmc_card_sdio(struct mmc_card *c) { return c->sdio; }
static ktime_t ktime_get(void) { return 0; }
static ktime_t ktime_sub(ktime_t a,ktime_t b) { return a-b; }
static long ktime_to_us(ktime_t t) { return t; }
static void trace_sdhci_msm_runtime_resume(const char *n,int err,long t)
{ (void)n;(void)t;trace_error=err; }
static void sdhci_msm_toggle_fifo_write_clk(struct sdhci_host *h) { assert(h==&host); }
static void sdhci_cfg_irq(struct sdhci_host *h,bool on,bool sync)
{ assert(h==&host && on && sync);irq_enables++; }
static void enable_irq(int irq) { assert(irq==msm.pwr_irq);irq_enables++; }
static int sdhci_msm_registers_error(struct sdhci_host *h);
static int sdhci_msm_enable_controller_clock(struct sdhci_host *h)
{ return sdhci_msm_registers_error(h); }
'''

TESTS = r'''
static struct mmc_host mmc;
static struct mmc_ops mmc_ops;
static struct sdhci_ops ops;
static struct sdhci_pltfm_host platform;
static struct sdhci_msm_pltfm_data pdata;
static struct sdhci_msm_slot_reg_data slot;
static struct sdhci_msm_reg_data rail;
static struct device dev;
static int sdhci_msm_registers_error(struct sdhci_host *h)
{ sdhci_msm_registers_restore(h);return sdhci_msm_host_error(&msm); }
static void reset(void)
{
    memset(registers,0,sizeof(registers));memset(&msm,0,sizeof(msm));
    memset(&mmc,0,sizeof(mmc));memset(&rail,0,sizeof(rail));
    platform.priv=&msm;mmc_ops.get_cd=get_cd;mmc.ops=&mmc_ops;
    host=(struct sdhci_host){.private=&platform,.mmc=&mmc,.ops=&ops,.ioaddr=registers,
                            .clock=400000};
    dev.data=&host;mmc.actual_clock=400000;ops.check_power_status=sdhci_msm_check_power_status;
    slot.vdd_data=&rail;slot.vdd_io_data=NULL;pdata.vreg_data=&slot;
    msm.pdata=&pdata;msm.mmc=&mmc;msm.offset=&sdhci_msm_offset_mci_present;
    msm.controller_clock=1;msm.pltfm_init_done=true;msm.caps_0=CORE_1_8V_SUPPORT;
    clear_stuck=wait_success=false;card_present=true;fail_vol_at=clear_losses=0;
    clears=completions=pad_writes=mask_enables=sdclk_stops=irq_enables=0;
    vreg_calls=pin_calls=vol_calls=dll_calls=phase_calls=waits=0;
    vreg_error=pin_error=vol_error=dll_error=phase_error=trace_error=0;
    registers[msm.offset->CORE_PWRCTL_MASK]=15;
}
static void faulted(int error)
{
    assert(sdhci_msm_host_error(&msm)==error);
    assert(!host.clock && !mmc.actual_clock && sdclk_stops);
    assert(!registers[msm.offset->CORE_PWRCTL_MASK]);
    assert(!host.lock);
}
static void irq(unsigned status)
{
    registers[msm.offset->CORE_PWRCTL_STATUS]=status;
    assert(sdhci_msm_pwr_irq(msm.pwr_irq,&host)==IRQ_HANDLED);
    assert(completions==1 && !host.lock);
}
static void failed_irq(int error,unsigned status)
{
    irq(status);faulted(error);
    unsigned ack=registers[msm.offset->CORE_PWRCTL_CTL];
    assert(!(ack&(CORE_PWRCTL_BUS_SUCCESS|CORE_PWRCTL_IO_SUCCESS)));
    if(status&(CORE_PWRCTL_BUS_ON|CORE_PWRCTL_BUS_OFF))assert(ack&CORE_PWRCTL_BUS_FAIL);
    if(status&(CORE_PWRCTL_IO_HIGH|CORE_PWRCTL_IO_LOW))assert(ack&CORE_PWRCTL_IO_FAIL);
    assert(!msm.curr_pwr_state && !msm.curr_io_level && !pad_writes);
}
static void restore_setup(void)
{
    msm.regs_restore.is_supported=msm.regs_restore.is_valid=true;
    msm.regs_restore.vendor_pwrctl_mask=15;
    msm.regs_restore.hc_2c_2e=1;
    mmc.ios.clock=200000000;mmc.ios.timing=MMC_TIMING_MMC_HS400;
}
int main(void)
{
    const unsigned all=CORE_PWRCTL_BUS_ON|CORE_PWRCTL_BUS_OFF|
                       CORE_PWRCTL_IO_HIGH|CORE_PWRCTL_IO_LOW;
    reset();clear_losses=3;registers[msm.offset->CORE_PWRCTL_STATUS]=all;
    assert(!sdhci_msm_clear_pwrctl_status(&host,all) && clears==4);
    reset();clear_stuck=true;failed_irq(-EBUSY,all);
    assert(clears==10 && !vreg_calls && !pin_calls && !vol_calls);
    reset();vreg_error=-EIO;failed_irq(-EIO,all);
    assert(vreg_calls==1 && !pin_calls && !vol_calls);
    reset();pin_error=-EREMOTEIO;failed_irq(-EREMOTEIO,all);
    assert(vreg_calls==1 && pin_calls==1 && !vol_calls);
    reset();vol_error=-ETIMEDOUT;fail_vol_at=1;failed_irq(-ETIMEDOUT,all);
    assert(vreg_calls==1 && pin_calls==1 && vol_calls==1);
    reset();vol_error=-ERANGE;fail_vol_at=1;
    failed_irq(-ERANGE,CORE_PWRCTL_IO_LOW|CORE_PWRCTL_IO_HIGH);assert(vol_calls==1);
    reset();vol_error=-EIO;fail_vol_at=2;
    failed_irq(-EIO,CORE_PWRCTL_BUS_ON|CORE_PWRCTL_IO_HIGH);
    assert(vreg_calls==1 && pin_calls==1 && vol_calls==2);
    reset();msm.msm_bus_vote.error=-EUCLEAN;failed_irq(-EUCLEAN,all);
    assert(!vreg_calls && !pin_calls && !vol_calls);
    reset();irq(CORE_PWRCTL_BUS_ON|CORE_PWRCTL_IO_HIGH);
    assert(!sdhci_msm_host_error(&msm) && msm.curr_pwr_state==REQ_BUS_ON);
    assert(msm.curr_io_level==REQ_IO_HIGH && pad_writes==1 && !sdclk_stops);
    reset();sdhci_msm_check_power_status(&host,REQ_BUS_ON);
    faulted(-ETIMEDOUT);assert(waits==1);
    /* Failed restore retains the IRQ mask and cannot re-enable GIC IRQs. */
    reset();restore_setup();clear_stuck=true;
    registers[msm.offset->CORE_PWRCTL_STATUS]=all;
    assert(sdhci_msm_runtime_resume(&dev)==-EBUSY);faulted(-EBUSY);
    assert(!dll_calls && !phase_calls && !mask_enables && !irq_enables);
    reset();restore_setup();dll_error=-EIO;
    assert(sdhci_msm_runtime_resume(&dev)==-EIO);faulted(-EIO);
    assert(dll_calls==1 && !phase_calls && !mask_enables && !irq_enables);
    reset();restore_setup();phase_error=-ERANGE;
    assert(sdhci_msm_runtime_resume(&dev)==-ERANGE);faulted(-ERANGE);
    assert(dll_calls==1 && phase_calls==1 && !mask_enables && !irq_enables);
    reset();restore_setup();assert(!sdhci_msm_runtime_resume(&dev));
    assert(dll_calls==1 && phase_calls==1 && mask_enables==1 && irq_enables==2);
    reset();msm.controller_clock=0;sdhci_msm_latch_error(&host,-EIO);
    assert(!sdclk_stops && !host.clock && !mmc.actual_clock);
    msm.controller_clock=1;sdhci_msm_latch_error(&host,-ERANGE);faulted(-EIO);
    puts("PASS: production IRQ first-error, clear retries, timeout, pad guard, restore/DLL and resume IRQ barriers");
    return 0;
}
'''


def harness(source):
    header = (ROOT / 'drivers/mmc/host/sdhci-msm.h').read_text()
    definitions = source + '\n' + (ROOT / 'drivers/mmc/host/sdhci.h').read_text()
    definitions += '\n' + (ROOT / 'include/linux/mmc/host.h').read_text()
    needed = {'SDHCI_CLOCK_CONTROL', 'SDHCI_AUTO_CMD_STATUS', 'SDHCI_SIGNAL_ENABLE',
              'SDHCI_INT_ENABLE', 'SDHCI_HOST_CONTROL', 'SDHCI_CAPABILITIES_1',
              'SDHCI_POWER_CONTROL', 'MMC_TIMING_MMC_HS400', 'MMC_TIMING_MMC_HS200',
              'MMC_TIMING_UHS_SDR104', 'CORE_FREQ_100MHZ', 'CORE_DLL_RST', 'CORE_DLL_PDN',
              'CORE_3_0V_SUPPORT', 'CORE_1_8V_SUPPORT', 'CORE_IO_PAD_PWR_SWITCH',
              'CORE_PWRCTL_BUS_ON', 'CORE_PWRCTL_BUS_OFF', 'CORE_PWRCTL_IO_LOW',
              'CORE_PWRCTL_IO_HIGH', 'CORE_PWRCTL_BUS_SUCCESS', 'CORE_PWRCTL_BUS_FAIL',
              'CORE_PWRCTL_IO_SUCCESS', 'CORE_PWRCTL_IO_FAIL', 'REQ_BUS_ON', 'REQ_BUS_OFF',
              'REQ_IO_LOW', 'REQ_IO_HIGH', 'SWITCHABLE_SIGNALLING_VOL', 'MSM_PWR_IRQ_TIMEOUT_MS'}
    text = HOST
    for name in sorted(needed):
        match = re.search(r'^#define\s+' + name + r'\s+[^\n]+', definitions, re.M)
        assert match, name
        text += match.group() + '\n'
    text += declaration(source, 'enum vdd_io_level {')
    text += declaration(source, 'enum dll_init_context {')
    text += declaration(source, 'struct sdhci_msm_offset {')
    text += declaration(source, 'struct sdhci_msm_offset sdhci_msm_offset_mci_present = {')
    text += declaration(header, 'struct sdhci_msm_regs_restore {') + PLUMBING
    for name in ('sdhci_msm_power_error', 'sdhci_msm_host_error', 'sdhci_msm_latch_error',
                 'sdhci_msm_clear_pwrctl_status', 'sdhci_msm_pwr_irq',
                 'sdhci_msm_check_power_status', 'sdhci_msm_registers_restore',
                 'sdhci_msm_runtime_resume'):
        text += function(source, name)
    return text + TESTS


def run(source, expect_failure=False):
    with tempfile.TemporaryDirectory(prefix='dora-mmc-irq-') as directory:
        c, binary = Path(directory) / 'irq.c', Path(directory) / 'irq'
        c.write_text(harness(source))
        flags = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wno-unused-function', '-Wno-unused-parameter',
                 '-fsanitize=' + os.environ.get('DORA_SANITIZERS', 'undefined'),
                 '-fno-sanitize-recover=all']
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + flags +
                       [str(c), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        if expect_failure:
            assert result.returncode, 'mutation survived'
        else:
            assert not result.returncode, result.stderr
            print(result.stdout, end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    source = (ROOT / 'drivers/mmc/host/sdhci-msm.c').read_text()
    # Probe must verify its stale reset-event drain before SUCCESS/GIC setup.
    probe = function(source, 'sdhci_msm_probe')
    assert probe.index('sdhci_msm_clear_pwrctl_status(') < probe.index('irq_ctl |= CORE_PWRCTL_BUS_SUCCESS')
    assert probe.index('sdhci_msm_clear_pwrctl_status(') < probe.index('devm_request_threaded_irq(')
    run(source)
    if args.self_test:
        mutations = (
            ('ret = sdhci_msm_clear_pwrctl_status(host, irq_status);',
             '(void)sdhci_msm_clear_pwrctl_status(host, irq_status); ret = 0;'),
            ('\t\tif (ret)\n\t\t\tgoto ack_status;\n\t\tirq_ack |= CORE_PWRCTL_BUS_SUCCESS;',
             '\t\t/* overwritten first error */\n\t\tirq_ack |= CORE_PWRCTL_BUS_SUCCESS;'),
            ('!sdhci_msm_host_error(msm_host) &&', '!sdhci_msm_power_error(msm_host->pdata) &&'),
            ('\t\tirq_ack &= ~(CORE_PWRCTL_BUS_SUCCESS | CORE_PWRCTL_IO_SUCCESS);',
             '\t\t/* stale success retained */'),
            ('\t\tmsm_host->offset->CORE_PWRCTL_MASK);',
             '\t\tmsm_host->offset->CORE_PWRCTL_CTL);'),
            ('ret = msm_init_cm_dll(host, DLL_INIT_FROM_CX_COLLAPSE_EXIT);',
             '(void)msm_init_cm_dll(host, DLL_INIT_FROM_CX_COLLAPSE_EXIT); ret = 0;'),
            ('ret = msm_config_cm_dll_phase(host, msm_host->saved_tuning_phase);',
             '(void)msm_config_cm_dll_phase(host, msm_host->saved_tuning_phase); ret = 0;'),
            ('\t\tsdhci_msm_latch_error(host, -ETIMEDOUT);', '\t\t/* lost timeout fault */'),
        )
        for before, after in mutations:
            assert before in source, before
            run(source.replace(before, after), expect_failure=True)
        print('PASS: rejected 8 IRQ, first-error, cache, mask, DLL and timeout mutations')
    print('Host API/MMIO model only; real IRQ scheduling, RPM ACKs and card behavior remain untested.')


if __name__ == '__main__':
    main()
