#!/usr/bin/env python3
"""Fault-test actual SDHCI provider resolution, clock unwind and boot selection.

Uses host API models, not electrical or storage runtime validation.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'drivers/mmc/host/sdhci-msm.c').read_text()

def function(name):
    m = re.search(r'static\s+(?:inline\s+)?(?:int|bool|void|unsigned int|struct clk\s*\*)\s*' + name + r'\s*\([^;]*?\)\s*\{', SOURCE, re.S)
    assert m, name
    end, depth = m.end(), 1
    while depth:
        depth += (SOURCE[end] == '{') - (SOURCE[end] == '}')
        end += 1
    return SOURCE[m.start():end] + '\n'

# Check that the real probe uses the tested resolvers before any clock enables,
# that initial bandwidth errors are checked, and that failed probes publish no slot.
probe = function('sdhci_msm_probe')
for call in ['sdhci_msm_get_clocks', 'sdhci_msm_get_irqs', 'msm_bus_scale_client_init',
             'sdhci_msm_vreg_init', 'sdhci_msm_bus_register', 'sdhci_msm_bus_voting']:
    assert probe.index(call) < probe.index('clk_set_rate'), call
assert 'of_find_compatible_node' not in probe
assert 'if (IS_ERR(msm_host->pdata))' in probe
assert probe.index('sdhci_slot[slot_id - 1] = msm_host') > probe.index('/* Successful initialization */')
assert probe.index('init_completion') < probe.index('devm_request_threaded_irq')
assert probe.index('sdhci_msm_free_irqs(host, true)') < probe.index('sdhci_pltfm_free(pdev)')
harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#define EPROBE_DEFER 517
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p)||IS_ERR(p))
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define dev_err(...) ((void)0)
#define MMC_TIMING_MMC_HS400 1
#define SDHCI_CLOCK_CONTROL 0
#define mb() ((void)0)
#define atomic_read(p) (*(p))
#define atomic_set(p,v) (*(p)=(v))
typedef unsigned int u32;
typedef long ktime_t;
struct clk { int refs,error,rate_error;unsigned long rate; };
struct device_node { bool names,irq_names;int malformed,irq_malformed;unsigned present,irq_present; };
struct device { struct device_node *of_node;void *data; };
struct platform_device { struct device dev; };
struct mmc_card { bool ungate,sdio; };
struct mmc_host { struct mmc_card *card;struct { int timing; } ios;bool card_clock_off; };
struct sdhci_msm_pltfm_data { unsigned ice_clk_max;int sdiowakeup_irq,testbus_trigger_irq,power_error; };
struct sdhci_msm_host { struct clk *bus_clk,*pclk,*bus_aggr_clk,*ice_clk,*clk,*ff_clk,*sleep_clk;
    struct sdhci_msm_pltfm_data *pdata;unsigned ice_clk_rate;
    int controller_clock,clks_on,pwr_irq; };
struct sdhci_pltfm_host { struct sdhci_msm_host *priv; };
struct sdhci_host { struct sdhci_pltfm_host priv;struct mmc_host *mmc; };
static const char * const names[]={"bus_clk","iface_clk","bus_aggr_clk","ice_core_clk","core_clk","cal_clk","sleep_clk"};
static const char * const irqs[]={"pwr_irq","sdiowakeup_irq","tb_trig_irq"};
static struct clk clocks[7];
static int get_errors[7],irq_errors[3],gets,prepares,disables,vote_error,votes,restores,saves,host_irq,power_irq,toggles,trace_error;
static int find_name(const char *name,const char *const *list,int n) { for(int i=0;i<n;i++)if(!strcmp(name,list[i]))return i;assert(0);return -1; }
static void *of_find_property(struct device_node *np,const char *name,void *unused) { assert(!strcmp(name,"clock-names"));return np->names?np:NULL; }
static int of_property_match_string(struct device_node *np,const char *prop,const char *name) {
    if(!strcmp(prop,"clock-names")) { if(np->malformed)return np->malformed;int i=find_name(name,names,7);return np->present&(1U<<i)?i:-ENODATA; }
    assert(!strcmp(prop,"interrupt-names"));if(!np->irq_names)return -EINVAL;
    if(np->irq_malformed)return np->irq_malformed;int i=find_name(name,irqs,3);return np->irq_present&(1U<<i)?i:-ENODATA;
}
static struct clk *devm_clk_get(struct device *dev,const char *name) { int i=find_name(name,names,7);gets++;if(get_errors[i])return ERR_PTR(get_errors[i]);if(!(dev->of_node->present&(1U<<i)))return ERR_PTR(-ENOENT);return &clocks[i]; }
static int platform_get_irq_byname(struct platform_device *p,const char *name) { int i=find_name(name,irqs,3);return irq_errors[i]?irq_errors[i]:100+i; }
static int clk_set_rate(struct clk *c,unsigned long rate) { assert(c&&!IS_ERR(c));if(c->rate_error)return c->rate_error;c->rate=rate;return 0; }
static int clk_prepare_enable(struct clk *c) { if(!c)return 0;assert(!IS_ERR(c));prepares++;if(c->error)return c->error;c->refs++;return 0; }
static void clk_disable_unprepare(struct clk *c) { if(!c)return;assert(!IS_ERR(c)&&c->refs>0);disables++;c->refs--; }
static void *sdhci_priv(struct sdhci_host *h) { return &h->priv; }
static const char *mmc_hostname(struct mmc_host *h) { return "mmc0"; }
static int sdhci_msm_host_error(struct sdhci_msm_host *h) { return h->pdata->power_error; }
static int sdhci_msm_bus_voting(struct sdhci_host *h,u32 enable) { if(enable&&vote_error)return vote_error;votes=enable;return 0; }
static void sdhci_msm_registers_restore(struct sdhci_host *h) { restores++; }
static void sdhci_msm_registers_save(struct sdhci_host *h) { saves++; }
static void sdhci_writew(struct sdhci_host *h,int v,int reg) { }
static void *dev_get_drvdata(struct device *dev) { return dev->data; }
static bool mmc_host_may_gate_card(struct mmc_card *c) { return c->ungate; }
static bool mmc_card_sdio(struct mmc_card *c) { return c->sdio; }
static ktime_t ktime_get(void) { return 0; }
static ktime_t ktime_sub(ktime_t a,ktime_t b) { return a-b; }
static long ktime_to_us(ktime_t t) { return t; }
static void trace_sdhci_msm_runtime_resume(const char *n,int err,long t) { trace_error=err; }
static void sdhci_msm_toggle_fifo_write_clk(struct sdhci_host *h) { toggles++; }
static void sdhci_cfg_irq(struct sdhci_host *h,bool on,bool sync) { host_irq=on; }
static void enable_irq(int irq) { power_irq++; }
'''
for name in ['sdhci_msm_bootdevice_matches','sdhci_msm_clk_get','sdhci_msm_get_clocks',
             'sdhci_msm_get_irqs','sdhci_msm_setup_ice_clk','sdhci_msm_enable_controller_clock',
             'sdhci_msm_disable_controller_clock','sdhci_msm_prepare_clocks',
             'sdhci_msm_unprepare_clocks','sdhci_msm_runtime_resume']:
    harness += function(name)
harness += r'''
static struct device_node node;
static struct platform_device pdev;
static struct sdhci_msm_pltfm_data pdata;
static struct sdhci_msm_host msm;
static struct mmc_host mmc;
static struct sdhci_host host;
static void reset(void) {
    memset(clocks,0,sizeof(clocks));memset(get_errors,0,sizeof(get_errors));memset(irq_errors,0,sizeof(irq_errors));
    node=(struct device_node){.names=true,.irq_names=true,.present=127,.irq_present=7};
    pdata=(struct sdhci_msm_pltfm_data){.ice_clk_max=300000000};
    msm=(struct sdhci_msm_host){.pdata=&pdata};mmc=(struct mmc_host){0};
    host=(struct sdhci_host){.priv={.priv=&msm},.mmc=&mmc};
    pdev=(struct platform_device){.dev={.of_node=&node,.data=&host}};
    gets=prepares=disables=votes=vote_error=restores=saves=host_irq=power_irq=toggles=trace_error=0;
}
static void empty_refs(void) { for(int i=0;i<7;i++)assert(!clocks[i].refs); }
int main(void) {
    assert(sdhci_msm_bootdevice_matches("","7464900.sdhci"));
    assert(sdhci_msm_bootdevice_matches("x=1 androidboot.bootdevice=7464900.sdhci z=3","7464900.sdhci"));
    assert(!sdhci_msm_bootdevice_matches("androidboot.bootdevice=7464900.sdhci-extra","7464900.sdhci"));
    assert(sdhci_msm_bootdevice_matches("fakeandroidboot.bootdevice=other","7464900.sdhci"));
    assert(!sdhci_msm_bootdevice_matches("androidboot.bootdevice=7464900.sdhci androidboot.bootdevice=other","7464900.sdhci"));
    assert(!sdhci_msm_bootdevice_matches("androidboot.bootdevice=","7464900.sdhci"));
    reset();node.names=false;assert(!sdhci_msm_clk_get(&pdev.dev,"ice_core_clk",false)&&!gets);
    reset();node.present=(1U<<1)|(1U<<4);assert(!sdhci_msm_get_clocks(&msm,&pdev.dev)&&gets==2&&!msm.ice_clk&&!msm.sleep_clk);
    reset();node.malformed=-EILSEQ;assert(PTR_ERR(sdhci_msm_clk_get(&pdev.dev,"sleep_clk",false))==-EILSEQ&&!gets);
    for(int i=0;i<7;i++) { reset();get_errors[i]=-EPROBE_DEFER;assert(sdhci_msm_get_clocks(&msm,&pdev.dev)==-EPROBE_DEFER&&!prepares); }
    reset();node.irq_present=1;assert(!sdhci_msm_get_irqs(&msm,&pdev)&&msm.pwr_irq==100&&pdata.sdiowakeup_irq==-ENXIO);
    for(int i=0;i<3;i++) { reset();irq_errors[i]=-EPROBE_DEFER;assert(sdhci_msm_get_irqs(&msm,&pdev)==-EPROBE_DEFER); }
    reset();assert(!sdhci_msm_setup_ice_clk(&msm,&pdev));
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));pdata.ice_clk_max=0;assert(sdhci_msm_setup_ice_clk(&msm,&pdev)==-EINVAL&&!prepares);
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));clocks[3].rate_error=-EIO;assert(sdhci_msm_setup_ice_clk(&msm,&pdev)==-EIO&&!msm.ice_clk_rate);empty_refs();
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));clocks[3].error=-ETIMEDOUT;assert(sdhci_msm_setup_ice_clk(&msm,&pdev)==-ETIMEDOUT&&!msm.ice_clk_rate);empty_refs();
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));assert(!sdhci_msm_setup_ice_clk(&msm,&pdev)&&msm.ice_clk_rate==300000000);clk_disable_unprepare(msm.ice_clk);
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));vote_error=-ETIMEDOUT;assert(sdhci_msm_enable_controller_clock(&host)==-ETIMEDOUT&&!prepares&&!restores);empty_refs();
    for(int i=1;i<=4;i++) {
        reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));clocks[i].error=-EIO;
        assert(sdhci_msm_enable_controller_clock(&host)==-EIO&&!msm.controller_clock&&!votes&&!restores);empty_refs();
        clocks[i].error=0;assert(!sdhci_msm_enable_controller_clock(&host)&&msm.controller_clock&&restores==1);
        int old=prepares;assert(!sdhci_msm_enable_controller_clock(&host)&&prepares==old);
        sdhci_msm_disable_controller_clock(&host);empty_refs();assert(!votes&&!msm.controller_clock&&saves==1);
    }
    for(int i=0;i<7;i++) {
        reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));clocks[i].error=-EIO;
        assert(sdhci_msm_prepare_clocks(&host,true)==-EIO&&!msm.clks_on&&!msm.controller_clock&&!votes);empty_refs();
    }
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));assert(!sdhci_msm_prepare_clocks(&host,true));
    assert(msm.clks_on&&msm.controller_clock);mmc.card_clock_off=true;
    sdhci_msm_unprepare_clocks(&msm);empty_refs();assert(!msm.clks_on&&!msm.controller_clock);
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));vote_error=-EIO;mmc.ios.timing=MMC_TIMING_MMC_HS400;
    assert(sdhci_msm_runtime_resume(&pdev.dev)==-EIO&&trace_error==-EIO&&!host_irq&&!power_irq&&!toggles&&!prepares);
    reset();assert(!sdhci_msm_get_clocks(&msm,&pdev.dev));pdata.power_error=-ETIMEDOUT;
    assert(sdhci_msm_enable_controller_clock(&host)==-ETIMEDOUT&&!prepares&&!votes);
    msm.controller_clock=1;assert(sdhci_msm_enable_controller_clock(&host)==-ETIMEDOUT);
    msm.clks_on=1;assert(sdhci_msm_prepare_clocks(&host,true)==-ETIMEDOUT&&!prepares&&!votes);
    puts("PASS: actual SDHCI provider/defer, exact bootdevice, ICE, initial/runtime bus fault and clock unwind helpers");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-mmc-providers-') as scratch:
    src = Path(scratch) / 'providers.c'; src.write_text(harness)
    binary = Path(scratch) / 'providers'
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-function', '-Wno-sign-compare', '-Wno-misleading-indentation',
                    str(src), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Provider API model only; full probe IRQ scheduling and physical SDHCI/HS400 resume remain unverified.')

# A second model executes the real bus cache lock/commit path concurrently.
# In particular, a lost ACK must never make the old cached vote reusable.
bus_harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <pthread.h>
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define pr_err(...) ((void)0)
#define WARN_ON(x) assert(!(x))
#define MMC_BUS_WIDTH_1 0
#define MMC_BUS_WIDTH_4 2
#define MMC_BUS_WIDTH_8 3
struct mutex { pthread_mutex_t lock; };
struct spinlock { pthread_mutex_t lock; };
static _Thread_local bool spin_held,bus_held;
static void mutex_lock(struct mutex *m) { assert(!spin_held&&!bus_held);assert(!pthread_mutex_lock(&m->lock));bus_held=true; }
static void mutex_unlock(struct mutex *m) { assert(bus_held);bus_held=false;assert(!pthread_mutex_unlock(&m->lock)); }
#define spin_lock_irqsave(l,f) do { assert(!spin_held&&!bus_held);assert(!pthread_mutex_lock(&(l)->lock));spin_held=true;(f)=0; } while(0)
#define spin_unlock_irqrestore(l,f) do { (void)(f);assert(spin_held);spin_held=false;assert(!pthread_mutex_unlock(&(l)->lock)); } while(0)
struct mmc_ios { unsigned bus_width; };
struct sdhci_host { struct spinlock lock;void *mmc; };
struct platform_device { struct sdhci_host *host; };
struct sdhci_msm_pltfm_data { int power_error; };
struct sdhci_msm_host { struct platform_device *pdev;struct sdhci_msm_pltfm_data *pdata;
    struct { struct mutex lock;int error;unsigned curr_vote,client_handle; } msm_bus_vote; };
static unsigned physical_vote,calls;
static int fault;
static struct sdhci_host *platform_get_drvdata(struct platform_device *p) { return p->host; }
static int sdhci_msm_power_error(struct sdhci_msm_pltfm_data *p) { return p->power_error; }
static int msm_bus_scale_client_update_request(unsigned handle,unsigned vote) {
    assert(handle==1&&bus_held&&!spin_held);calls++;physical_vote=vote;
    if(fault){int error=fault;fault=0;return error;}return 0;
}
'''
for name in ['sdhci_msm_bw_for_rate', 'sdhci_msm_bus_set_vote', 'sdhci_msm_host_error']:
    bus_harness += function(name)
bus_harness += r'''
static struct sdhci_host host;
static struct platform_device pdev;
static struct sdhci_msm_pltfm_data pdata;
static struct sdhci_msm_host msm;
static int vote(unsigned index) {
    unsigned long flags;int ret;spin_lock_irqsave(&host.lock,flags);
    ret=sdhci_msm_bus_set_vote(&msm,index,&flags);assert(spin_held&&!bus_held);
    spin_unlock_irqrestore(&host.lock,flags);return ret;
}
static void *consumer(void *arg) {
    unsigned seed=(uintptr_t)arg;
    for(unsigned i=0;i<1000;i++)assert(!vote((seed+i)%3));return NULL;
}
int main(void) {
    pthread_t a,b;
    assert(!pthread_mutex_init(&host.lock.lock,NULL));
    assert(!pthread_mutex_init(&msm.msm_bus_vote.lock.lock,NULL));
    pdev.host=&host;msm.pdev=&pdev;msm.pdata=&pdata;
    msm.msm_bus_vote.client_handle=1;msm.msm_bus_vote.curr_vote=UINT32_MAX;
    assert(!vote(0)&&calls==1&&physical_vote==0);
    assert(!vote(0)&&calls==1);
    assert(!pthread_create(&a,NULL,consumer,(void *)(uintptr_t)1));
    assert(!pthread_create(&b,NULL,consumer,(void *)(uintptr_t)2));
    assert(!pthread_join(a,NULL)&&!pthread_join(b,NULL));
    assert(physical_vote==msm.msm_bus_vote.curr_vote&&!sdhci_msm_host_error(&msm));
    assert(!vote(2));fault=-ETIMEDOUT;
    assert(vote(0)==-ETIMEDOUT&&physical_vote==0&&msm.msm_bus_vote.curr_vote==2);
    unsigned old=calls;
    assert(vote(2)==-ETIMEDOUT&&calls==old&&physical_vote==0);
    assert(sdhci_msm_host_error(&msm)==-ETIMEDOUT);
    pdata.power_error=-EIO;assert(sdhci_msm_host_error(&msm)==-ETIMEDOUT);
    struct mmc_ios ios={.bus_width=MMC_BUS_WIDTH_8};
    assert(sdhci_msm_bw_for_rate(&ios,384000000)==384000000);
    ios.bus_width=MMC_BUS_WIDTH_4;assert(sdhci_msm_bw_for_rate(&ios,384000000)==192000000);
    ios.bus_width=MMC_BUS_WIDTH_1;assert(sdhci_msm_bw_for_rate(&ios,400000)==50000);
    puts("PASS: actual SDHCI bus cache/mutex: concurrent votes, first submission and lost-ACK quarantine");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-mmc-bus-') as scratch:
    src = Path(scratch) / 'bus.c'; src.write_text(bus_harness)
    binary = Path(scratch) / 'bus'
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-misleading-indentation', '-Wno-sign-compare', '-pthread',
                    str(src), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
