#!/usr/bin/env python3
"""Fault-test actual SDHCI-MSM regulator helpers and two-rail transactions.

The model separates physical PMIC state from regulator-core requests, use counts
and driver caches. Applied-but-unacknowledged requests and cached voltage restores
must leave the host permanently quarantined. No real PMIC request is made.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'drivers/mmc/host/sdhci-msm.c').read_text()
HEADER = (ROOT / 'drivers/mmc/host/sdhci-msm.h').read_text()


def function(name):
    match = re.search(r'static\s+(?:int|void)\s+' + name + r'\s*\([^;]*?\)\s*\{', SOURCE, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (SOURCE[end] == '{') - (SOURCE[end] == '}')
        end += 1
    return SOURCE[match.start():end] + '\n'


def structure(name):
    start = HEADER.index('struct ' + name + ' {')
    return HEADER[start:HEADER.index('\n};', start) + 4] + '\n'


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define EPROBE_DEFER 517
#define READ_ONCE(x) (x)
#define cmpxchg(p,old,new) ({ int prior=*(p);if(prior==(old))*(p)=(new);prior; })
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p) || IS_ERR(p))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define dev_err(...) ((void)0)
struct device { int unused; };
struct regulator {
    unsigned id,handles,refs,count[256],provider_count[256],cache_hits[256];
    int voltage_count,get_error,requested_load,min_uV,max_uV;
    int physical_load,physical_min_uV,physical_max_uV;
    bool physical_enabled;
};
'''
harness += structure('sdhci_msm_reg_data')
harness += structure('sdhci_msm_slot_reg_data')
harness += r'''
struct sdhci_msm_pltfm_data { struct sdhci_msm_slot_reg_data *vreg_data; };
struct fault { unsigned rail,at; char op; int error; bool applied; };
struct event { unsigned rail;char op;int a,b; };
static struct regulator rails[2];
static struct sdhci_msm_reg_data data[2];
static struct sdhci_msm_slot_reg_data slot;
static struct sdhci_msm_pltfm_data pdata;
static struct device dev;
static struct fault faults[16];
static struct event events[256];
static unsigned nr_faults,nr_events;
static struct fault *request(struct regulator *r,char op,int a,int b)
{
    unsigned i,call=++r->count[(unsigned char)op];
    assert(r>=rails && r<rails+2 && nr_events<ARRAY_SIZE(events));
    events[nr_events++]=(struct event){r->id,op,a,b};
    for(i=0;i<nr_faults;i++)
        if(faults[i].rail==r->id && faults[i].op==op && faults[i].at==call)
            return &faults[i];
    return NULL;
}
static int provider(struct regulator *r,char op,struct fault *fault)
{ r->provider_count[(unsigned char)op]++;return fault?fault->error:0; }
static void fail_at(unsigned rail,char op,unsigned at,int error,bool applied)
{
    assert(nr_faults<ARRAY_SIZE(faults));
    faults[nr_faults++]=(struct fault){rail,at,op,error,applied};
}
static void fail_next(unsigned rail,char op,int error,bool applied)
{ fail_at(rail,op,rails[rail].count[(unsigned char)op]+1,error,applied); }
static struct regulator *devm_regulator_get(struct device *d,const char *name)
{
    struct regulator *r;
    assert(d==&dev);r=!strcmp(name,"vdd")?&rails[0]:&rails[1];
    request(r,'G',0,0);
    if(r->get_error)return ERR_PTR(r->get_error);
    r->handles++;return r;
}
static void devm_regulator_put(struct regulator *r)
{ assert(!IS_ERR_OR_NULL(r) && r->handles && !r->refs);request(r,'P',0,0);r->handles--; }
static int regulator_count_voltages(struct regulator *r) { request(r,'C',0,0);return r->voltage_count; }
static int regulator_set_load(struct regulator *r,int load)
{
    struct fault *fault=request(r,'L',load,0);
    int ret;
    /* This kernel caches uA_load BEFORE calling the physical provider. */
    r->requested_load=load;ret=provider(r,'L',fault);
    if(ret>=0 || (fault && fault->applied))r->physical_load=load;
    return ret;
}
static int regulator_set_voltage(struct regulator *r,int min,int max)
{
    struct fault *fault=request(r,'V',min,max);
    int ret;
    /* regulator_set_voltage_unlocked() short-circuits an identical range.
     * On failure it restores the old consumer range, not the physical rail. */
    if(r->min_uV==min && r->max_uV==max){r->cache_hits['V']++;return 0;}
    ret=provider(r,'V',fault);
    if(!ret || (fault && fault->applied)) {
        r->physical_min_uV=min;r->physical_max_uV=max;
    }
    if(!ret){r->min_uV=min;r->max_uV=max;}
    return ret;
}
static int regulator_enable(struct regulator *r)
{
    struct fault *fault=request(r,'E',0,0);int ret;
    if(r->refs){r->refs++;r->cache_hits['E']++;return 0;}
    ret=provider(r,'E',fault);
    if(!ret || (fault && fault->applied))r->physical_enabled=true;
    if(!ret)r->refs++;
    return ret;
}
static int regulator_disable(struct regulator *r)
{
    struct fault *fault=request(r,'D',0,0);int ret;
    assert(r->refs);
    if(r->refs>1){r->refs--;r->cache_hits['D']++;return 0;}
    ret=provider(r,'D',fault);
    if(!ret || (fault && fault->applied))r->physical_enabled=false;
    if(!ret)r->refs--;
    return ret;
}
'''
for name in ('sdhci_msm_vreg_fault', 'sdhci_msm_power_error',
             'sdhci_msm_vreg_init_reg', 'sdhci_msm_vreg_deinit_reg',
             'sdhci_msm_vreg_set_optimum_mode', 'sdhci_msm_vreg_set_voltage',
             'sdhci_msm_vreg_enable', 'sdhci_msm_vreg_disable',
             'sdhci_msm_setup_vreg', 'sdhci_msm_vreg_init'):
    harness += function(name)
harness += r'''
static void reset(void)
{
    unsigned i;
    memset(rails,0,sizeof(rails));memset(data,0,sizeof(data));
    memset(faults,0,sizeof(faults));memset(events,0,sizeof(events));nr_faults=nr_events=0;
    for(i=0;i<2;i++) {
        rails[i].id=i;rails[i].voltage_count=1;
        data[i].name=i?"vdd-io":"vdd";
        data[i].low_vol_level=data[i].high_vol_level=i?1800000:2950000;
        data[i].hpm_uA=i?325000:570000;data[i].lpm_uA=i?110:200;
    }
    slot.vdd_data=&data[0];slot.vdd_io_data=&data[1];pdata.vreg_data=&slot;
}
static void initialize(void) { assert(!sdhci_msm_vreg_init(&dev,&pdata,true)); }
static void current(unsigned index)
{
    assert(data[index].is_enabled && rails[index].refs==1 && rails[index].physical_enabled);
    assert(data[index].load_uA==(int)data[index].hpm_uA);
    assert(rails[index].requested_load==(int)data[index].hpm_uA);
    assert(rails[index].physical_load==(int)data[index].hpm_uA);
    assert(data[index].min_uV==(int)data[index].low_vol_level);
    assert(rails[index].physical_min_uV==(int)data[index].low_vol_level);
}
static void idle(unsigned index)
{
    assert(!data[index].is_enabled && !rails[index].refs && !rails[index].physical_enabled);
    assert(!data[index].load_uA && !rails[index].requested_load && !rails[index].physical_load);
    assert(!data[index].min_uV && !rails[index].min_uV && !rails[index].physical_min_uV);
}
static void quarantined(unsigned index,int error)
{
    unsigned before=nr_events;
    assert(data[index].power_error==error && sdhci_msm_power_error(&pdata)==error);
    assert(sdhci_msm_vreg_enable(&data[index])==error);
    assert(sdhci_msm_vreg_set_optimum_mode(&data[index],data[index].hpm_uA)==error);
    assert(sdhci_msm_vreg_set_voltage(&data[index],1800000,2950000)==error);
    assert(sdhci_msm_vreg_init_reg(&dev,&data[index])==error);
    assert(sdhci_msm_setup_vreg(&pdata,true,false)<0);
    assert(sdhci_msm_vreg_init(&dev,&pdata,true)<0);
    /* No regulator APIs, even after cleanup has made all caches look idle. */
    assert(nr_events==before && data[index].power_error==error);
}
static void cleanup_preserves_fault(unsigned index,int error)
{
    nr_faults=0;
    assert(sdhci_msm_vreg_init(&dev,&pdata,false)<0);
    assert(data[index].power_error==error);
    quarantined(index,error);
}
int main(void)
{
    unsigned before,providers;int ret;
    /* No physical operation has happened: provider/parse errors remain retryable. */
    reset();rails[0].get_error=-EPROBE_DEFER;
    assert(sdhci_msm_vreg_init_reg(&dev,&data[0])==-EPROBE_DEFER && !data[0].reg && !rails[0].handles);
    assert(!data[0].power_error);rails[0].get_error=0;
    assert(!sdhci_msm_vreg_init_reg(&dev,&data[0]) && rails[0].handles==1);
    assert(!sdhci_msm_vreg_init_reg(&dev,&data[0]) && rails[0].handles==1);
    assert(!sdhci_msm_vreg_deinit_reg(&data[0]));assert(!data[0].reg && !rails[0].handles);
    data[0].reg=ERR_PTR(-EPROBE_DEFER);assert(!sdhci_msm_vreg_deinit_reg(&data[0]));assert(!data[0].reg);
    reset();data[0].hpm_uA=0;
    assert(sdhci_msm_vreg_init_reg(&dev,&data[0])==-EINVAL && !data[0].power_error);
    assert(sdhci_msm_vreg_init_reg(&dev,&data[0])==-EINVAL);
    data[0].hpm_uA=570000;assert(!sdhci_msm_vreg_init_reg(&dev,&data[0]));
    assert(data[0].set_voltage_sup && rails[0].handles==1);
    assert(!sdhci_msm_vreg_deinit_reg(&data[0]));
    reset();rails[0].voltage_count=-EIO;
    assert(sdhci_msm_vreg_init_reg(&dev,&data[0])==-EIO && !data[0].power_error);
    rails[0].voltage_count=1;assert(!sdhci_msm_vreg_init_reg(&dev,&data[0]));
    assert(data[0].set_voltage_sup && rails[0].handles==1);
    reset();rails[1].get_error=-EPROBE_DEFER;
    assert(sdhci_msm_vreg_init(&dev,&pdata,true)==-EPROBE_DEFER);
    assert(!data[0].reg && !data[1].reg && !rails[0].handles && !rails[1].handles);
    /* Repeated healthy always-on requests own only one generic enable reference. */
    reset();initialize();data[0].is_always_on=true;data[0].lpm_sup=true;
    assert(!sdhci_msm_vreg_enable(&data[0]));current(0);
    assert(!sdhci_msm_vreg_enable(&data[0]) && rails[0].refs==1 && rails[0].count['E']==1);
    assert(!sdhci_msm_vreg_disable(&data[0]) && rails[0].refs==1 && data[0].load_uA==200);
    assert(!sdhci_msm_vreg_enable(&data[0]));current(0);
    assert(!sdhci_msm_vreg_init(&dev,&pdata,false));idle(0);idle(1);
    assert(!data[0].reg && !data[0].load_uA && !data[0].min_uV && !data[0].max_uV);
    /* A nonnegative legacy load result is success, not a permanent fault. */
    reset();initialize();fail_next(0,'L',1,false);
    assert(!sdhci_msm_vreg_enable(&data[0]) && !data[0].power_error);current(0);
    /* Applied-but-unacknowledged load request: physical/provider/cache differ. */
    reset();initialize();fail_next(0,'L',-ETIMEDOUT,true);fail_at(0,'L',2,-EIO,false);
    assert(sdhci_msm_vreg_enable(&data[0])==-ETIMEDOUT);
    assert(rails[0].physical_load==570000 && !rails[0].requested_load && !data[0].load_uA);
    assert(!data[0].is_enabled && !rails[0].refs);
    quarantined(0,-ETIMEDOUT);cleanup_preserves_fault(0,-ETIMEDOUT);
    /* Voltage timeout restores only the generic range. The physical rail moved. */
    reset();initialize();assert(!sdhci_msm_vreg_set_voltage(&data[0],2950000,2950000));
    fail_next(0,'V',-EREMOTEIO,true);
    assert(sdhci_msm_vreg_set_voltage(&data[0],1800000,1800000)==-EREMOTEIO);
    assert(rails[0].min_uV==2950000 && data[0].min_uV==2950000 && rails[0].physical_min_uV==1800000);
    providers=rails[0].provider_count['V'];
    assert(!regulator_set_voltage(&rails[0],2950000,2950000));
    assert(rails[0].provider_count['V']==providers && rails[0].cache_hits['V']==1);
    assert(rails[0].physical_min_uV==1800000); /* cached rollback was not transmitted */
    quarantined(0,-EREMOTEIO);cleanup_preserves_fault(0,-EREMOTEIO);
    /* Enable timeout can leave power physically on with no generic enable ref. */
    reset();initialize();fail_next(0,'E',-ETIMEDOUT,true);
    assert(sdhci_msm_vreg_enable(&data[0])==-ETIMEDOUT);
    assert(rails[0].physical_enabled && !rails[0].refs && !data[0].is_enabled);
    quarantined(0,-ETIMEDOUT);cleanup_preserves_fault(0,-ETIMEDOUT);
    assert(rails[0].physical_enabled && !rails[0].refs && !data[0].reg);
    /* Even complete consumer teardown has not proved that physical power is off. */
    /* Disable timeout can leave power off while generic ref/cache still says on. */
    reset();initialize();assert(!sdhci_msm_vreg_enable(&data[0]));
    fail_next(0,'D',-ETIMEDOUT,true);
    assert(sdhci_msm_vreg_disable(&data[0])==-ETIMEDOUT);
    assert(!rails[0].physical_enabled && rails[0].refs==1 && data[0].is_enabled);
    quarantined(0,-ETIMEDOUT);cleanup_preserves_fault(0,-ETIMEDOUT);
    /* Rejected requests are also uncertain to the consumer and stay quarantined. */
    reset();initialize();fail_next(0,'V',-ERANGE,false);
    assert(sdhci_msm_vreg_enable(&data[0])==-ERANGE);
    assert(!rails[0].physical_enabled && !rails[0].physical_min_uV && !rails[0].count['E']);
    quarantined(0,-ERANGE);cleanup_preserves_fault(0,-ERANGE);
    /* Second-rail failure unwinds new healthy references in reverse order. */
    reset();initialize();data[0].is_always_on=true;fail_next(1,'E',-EIO,true);
    assert(sdhci_msm_setup_vreg(&pdata,true,true)==-EIO);
    idle(0);assert(!data[0].power_error && rails[1].physical_enabled && !rails[1].refs);
    quarantined(1,-EIO);cleanup_preserves_fault(1,-EIO);
    /* Existing first-rail ownership/load survive a failed two-rail enable. */
    reset();initialize();data[0].is_always_on=true;data[0].lpm_sup=true;
    assert(!sdhci_msm_vreg_enable(&data[0]));assert(!sdhci_msm_vreg_disable(&data[0]));
    fail_next(1,'E',-EREMOTEIO,false);
    assert(sdhci_msm_setup_vreg(&pdata,true,false)==-EREMOTEIO);
    assert(data[0].is_enabled && rails[0].refs==1 && data[0].load_uA==200 && rails[0].physical_load==200);
    assert(rails[0].count['E']==1 && !rails[0].count['D']);quarantined(1,-EREMOTEIO);
    /* A failed rollback must not claim to have dropped a still-owned reference. */
    reset();initialize();fail_next(1,'E',-EIO,false);fail_next(0,'D',-EBUSY,true);
    assert(sdhci_msm_setup_vreg(&pdata,true,true)==-EIO);
    assert(data[0].is_enabled && rails[0].refs==1 && !rails[0].physical_enabled);
    assert(data[0].power_error==-EBUSY && data[1].power_error==-EIO);
    assert(sdhci_msm_power_error(&pdata)==-EBUSY);quarantined(0,-EBUSY);
    /* The first failed disable does not skip a second-rail cleanup attempt. */
    reset();initialize();assert(!sdhci_msm_setup_vreg(&pdata,true,true));
    fail_next(0,'D',-EIO,false);fail_next(1,'L',-EAGAIN,true);
    ret=sdhci_msm_setup_vreg(&pdata,false,false);
    assert(ret==-EIO && data[0].is_enabled && rails[0].refs==1);
    assert(!data[1].is_enabled && !rails[1].refs && data[1].power_error==-EAGAIN);
    assert(sdhci_msm_power_error(&pdata)==-EIO);quarantined(0,-EIO);
    /* Cleanup errors may be retried to release ownership, never to resume I/O. */
    reset();initialize();assert(!sdhci_msm_setup_vreg(&pdata,true,true));
    fail_next(1,'D',-EBUSY,true);fail_next(0,'L',-EIO,false);
    assert(sdhci_msm_vreg_init(&dev,&pdata,false)==-EBUSY);
    assert(data[1].is_enabled && rails[1].refs==1 && rails[1].handles==1 && !rails[1].physical_enabled);
    assert(!data[0].is_enabled && !rails[0].refs && rails[0].handles==1);
    nr_faults=0;assert(sdhci_msm_vreg_init(&dev,&pdata,false)<0);
    assert(!rails[0].handles && !rails[1].handles && rails[0].count['D']==1 && rails[1].count['D']==2);
    assert(data[0].power_error==-EIO && data[1].power_error==-EBUSY);quarantined(0,-EIO);
    /* Later errors do not overwrite the first rail fault or permit same-pdata init. */
    reset();initialize();assert(!sdhci_msm_vreg_enable(&data[0]));
    fail_next(0,'V',-ETIMEDOUT,true);
    assert(sdhci_msm_vreg_set_voltage(&data[0],1800000,1800000)==-ETIMEDOUT);
    fail_next(0,'D',-EBUSY,false);assert(sdhci_msm_vreg_deinit_reg(&data[0])<0);
    assert(data[0].power_error==-ETIMEDOUT);quarantined(0,-ETIMEDOUT);
    cleanup_preserves_fault(0,-ETIMEDOUT);
    /* No-voltage/load provider still owns exactly one successful enable ref. */
    reset();rails[0].voltage_count=0;initialize();assert(!data[0].set_voltage_sup);
    assert(!sdhci_msm_vreg_enable(&data[0]) && !sdhci_msm_vreg_enable(&data[0]));
    assert(rails[0].refs==1 && !rails[0].count['L'] && !rails[0].count['V']);
    assert(!sdhci_msm_vreg_disable(&data[0]) && !rails[0].refs);
    before=nr_events;assert(!sdhci_msm_vreg_init(&dev,&pdata,false));assert(nr_events>=before);
    assert(!rails[0].handles && !rails[1].handles && !sdhci_msm_power_error(&pdata));
    puts("PASS: real SDHCI power helpers: physical/cache divergence, lost ACKs, cached restores, permanent first-error quarantine, two-rail unwind and cleanup");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-mmc-power-') as scratch:
    source, binary = Path(scratch) / 'power.c', Path(scratch) / 'power'
    source.write_text(harness)
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-sign-compare', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Bounded software model only; physical PMIC state cannot be certified by cache rollback or these host tests.')
