#!/usr/bin/env python3
"""Execute actual MMC host-local UIM2, CD, scan and stop paths with faults.

This software GPIO/devres model does not detect a real SIM, card or tray.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SLOT = (ROOT / 'drivers/mmc/core/slot-gpio.c').read_text()
CORE = (ROOT / 'drivers/mmc/core/core.c').read_text()


def function(source, name):
    match = re.search(r'(?:static\s+)?(?:int|void|irqreturn_t)\s+' + name +
                      r'\s*\([^;]*?\)\s*\{', source, re.S)
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
typedef uint32_t u32;
typedef int irqreturn_t;
#define EPROBE_DEFER 517
#define IRQ_HANDLED 1
#define MMC_CAP_NONREMOVABLE 1U
#define MMC_CAP2_CD_ACTIVE_HIGH 1U
#define MMC_CAP2_NO_SDIO 2U
#define MMC_CAP2_NO_SD 4U
#define MMC_CAP2_NO_MMC 8U
#define GPIOD_OUT_LOW 0
#define EXTCON_MECHANICAL 0
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define pr_debug(...) ((void)0)
#define pr_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define unlikely(x) (x)
#define msecs_to_jiffies(x) (x)
struct mutex { bool held; };
static void mutex_lock(struct mutex *m) { assert(!m->held);m->held=true; }
static void mutex_unlock(struct mutex *m) { assert(m->held);m->held=false; }
struct gpio_desc { int raw,error,value;bool cansleep,active_low; };
struct device { bool property; };
struct extcon_dev { int value; };
struct mmc_host;
struct mmc_host_ops { int (*get_cd)(struct mmc_host *); };
struct mmc_bus_ops { void (*remove)(struct mmc_host *); };
struct mmc_host { struct device *parent;struct { void *handler_priv;int cd_irq; } slot;
    unsigned caps,caps2,f_init,ocr_avail,pm_flags;struct extcon_dev *extcon;
    struct mmc_host_ops *ops;struct mmc_bus_ops *bus_ops;int detect,rescan_disable;
    bool trigger_card_event,bus_dead; };
'''
start = SLOT.index('struct mmc_gpio {')
harness += SLOT[start:SLOT.index('\n};', start) + 4] + '\n'
harness += r'''
static struct gpio_desc cd,line;
static struct device parent;
static struct mmc_gpio ctx;
static struct mmc_host host;
static int get_error,action_error,get_calls,sets,detects,external_calls,external_value;
static int sd_result,sdio_result,mmc_result,bus_refs,claims,poweroffs;
static bool cancelled;
static void (*cleanup)(void *);
static void *cleanup_data;
static bool device_property_present(struct device *dev,const char *name)
{ assert(dev==&parent && !strcmp(name,"uim2-gpios"));return dev->property; }
static struct gpio_desc *devm_gpiod_get(struct device *dev,const char *name,int flags)
{
    assert(dev==&parent && !strcmp(name,"uim2") && flags==GPIOD_OUT_LOW);get_calls++;
    if(get_error)return ERR_PTR(get_error);
    line.value=0;return &line;
}
static int devm_add_action_or_reset(struct device *dev,void (*fn)(void *),void *data)
{
    assert(dev==&parent);if(action_error){fn(data);return action_error;}
    assert(!cleanup);cleanup=fn;cleanup_data=data;return 0;
}
static void gpiod_set_value_cansleep(struct gpio_desc *gpio,int value)
{ assert(gpio==&line && (value==0 || value==1));gpio->value=value;sets++; }
static int gpiod_cansleep(struct gpio_desc *gpio) { return gpio->cansleep; }
static int gpiod_get_raw_value(struct gpio_desc *gpio) { return gpio->error?:gpio->raw; }
static int gpiod_get_raw_value_cansleep(struct gpio_desc *gpio) { return gpiod_get_raw_value(gpio); }
static int gpiod_get_value(struct gpio_desc *gpio)
{ int value=gpiod_get_raw_value(gpio);return value<0?value:(gpio->active_low?!value:value); }
static int gpiod_get_value_cansleep(struct gpio_desc *gpio) { return gpiod_get_value(gpio); }
static int extcon_get_state(struct extcon_dev *dev,int id) { assert(!id);return dev->value; }
static int desc_to_gpio(struct gpio_desc *gpio) { return gpio?1:-EINVAL; }
static bool gpio_is_valid(int gpio) { return gpio>=0; }
static int gpio_get_value_cansleep(int gpio) { assert(gpio==1);return gpiod_get_raw_value(&cd); }
static void sony_ext_uim_ctrl_set_uim2_detect_en(int value) { external_calls++;external_value=value; }
static void mmc_detect_change(struct mmc_host *h,unsigned delay) { assert(h==&host && delay==200);detects++; }
static void mmc_gpio_set_cd_wake(struct mmc_host *h,bool value) { assert(h==&host && !value); }
static void disable_irq(int irq) { assert(irq==9); }
static void cancel_delayed_work_sync(int *work) { assert(work==&host.detect);cancelled=true; }
static void mmc_bus_get(struct mmc_host *h) { assert(h==&host);bus_refs++; }
static void mmc_bus_put(struct mmc_host *h) { assert(h==&host && bus_refs>0);bus_refs--; }
static void mmc_claim_host(struct mmc_host *h) { assert(h==&host && !claims);claims++; }
static void mmc_release_host(struct mmc_host *h) { assert(h==&host && claims==1);claims--; }
static void mmc_power_off(struct mmc_host *h) { assert(h==&host);poweroffs++; }
static void mmc_unregister_extcon(struct mmc_host *h) { assert(h==&host); }
static void mmc_detach_bus(struct mmc_host *h) { assert(h==&host); }
static void mmc_power_up(struct mmc_host *h,unsigned ocr) { assert(h==&host && ocr==123); }
static void mmc_hw_reset_for_init(struct mmc_host *h) { assert(h==&host); }
static void sdio_reset(struct mmc_host *h) { assert(h==&host); }
static void mmc_go_idle(struct mmc_host *h) { assert(h==&host); }
static void mmc_send_if_cond(struct mmc_host *h,unsigned ocr) { assert(h==&host && ocr==123); }
static int mmc_attach_sdio(struct mmc_host *h) { assert(h==&host);return sdio_result; }
static int mmc_attach_sd(struct mmc_host *h) { assert(h==&host);return sd_result; }
static int mmc_attach_mmc(struct mmc_host *h) { assert(h==&host);return mmc_result; }
'''
harness += function(SLOT, 'mmc_gpio_get_cd')
harness += '#ifdef CONFIG_SONY_MMC_EXTENSION\n' + function(SLOT, 'mmc_gpio_get_status') + '#endif\n'
for name in ('mmc_gpio_uim2_release', 'mmc_gpiod_request_uim2', 'mmc_gpio_set_uim2_en',
             'mmc_gpio_tray_close_set_uim2', 'mmc_gpio_cd_irqt'):
    harness += function(SLOT, name)
for name in ('mmc_rescan_try_freq', 'mmc_stop_host'):
    harness += function(CORE, name)
harness += r'''
static struct mmc_host_ops ops={.get_cd=mmc_gpio_get_cd};
static void reset(void)
{
    assert(!cleanup);memset(&host,0,sizeof(host));memset(&ctx,0,sizeof(ctx));
    memset(&cd,0,sizeof(cd));memset(&line,0,sizeof(line));memset(&parent,0,sizeof(parent));
    host.parent=&parent;host.slot.handler_priv=&ctx;host.slot.cd_irq=9;
    host.ops=&ops;host.caps2=MMC_CAP2_CD_ACTIVE_HIGH;host.ocr_avail=123;
    ctx.cd_gpio=&cd;ctx.override_cd_active_level=true;ctx.cd_debounce_delay_ms=200;
    parent.property=true;get_error=action_error=get_calls=sets=detects=external_calls=0;
    sd_result=sdio_result=mmc_result=-ENODEV;bus_refs=claims=poweroffs=0;cancelled=false;
}
static void release(void)
{ void (*fn)(void *)=cleanup;void *data=cleanup_data;assert(fn);cleanup=NULL;cleanup_data=NULL;fn(data); }
int main(void)
{
    reset();parent.property=false;assert(!mmc_gpiod_request_uim2(&host) && !get_calls && !ctx.uim2_gpio);
    reset();host.slot.handler_priv=NULL;assert(mmc_gpiod_request_uim2(&host)==-EINVAL);
    reset();host.caps=MMC_CAP_NONREMOVABLE;assert(mmc_gpiod_request_uim2(&host)==-EINVAL && !get_calls);
    reset();ctx.cd_gpio=NULL;assert(mmc_gpiod_request_uim2(&host)==-EINVAL && !get_calls);
    reset();get_error=-EPROBE_DEFER;assert(mmc_gpiod_request_uim2(&host)==-EPROBE_DEFER && !ctx.uim2_gpio);
    reset();get_error=-EIO;assert(mmc_gpiod_request_uim2(&host)==-EIO && !ctx.uim2_gpio);
    reset();action_error=-ENOMEM;line.value=1;
    assert(mmc_gpiod_request_uim2(&host)==-ENOMEM && !ctx.uim2_gpio && !line.value && sets==1);
    reset();assert(!mmc_gpiod_request_uim2(&host) && !line.value && ctx.uim2_local);
    assert(mmc_gpiod_request_uim2(&host)==-EBUSY && get_calls==1);
    cd.raw=1;mmc_gpio_tray_close_set_uim2(&host,1);assert(line.value==1 && !external_calls);
    cd.raw=0;mmc_gpio_tray_close_set_uim2(&host,1);assert(line.value==0 && !external_calls);
    cd.raw=1;cd.error=-EIO;mmc_gpio_tray_close_set_uim2(&host,1);assert(line.value==0);
    cd.cansleep=true;mmc_gpio_tray_close_set_uim2(&host,1);assert(!line.value);
    cd.error=0;ctx.override_cd_active_level=false;cd.active_low=true;cd.raw=0;
    mmc_gpio_tray_close_set_uim2(&host,1);assert(line.value==1);
    cd.raw=1;mmc_gpio_tray_close_set_uim2(&host,1);assert(!line.value);
    ctx.override_cd_active_level=true;host.caps2=MMC_CAP2_CD_ACTIVE_HIGH;
    cd.raw=1;mmc_gpio_set_uim2_en(&host,1);assert(line.value==1);
    cd.raw=0;mmc_gpio_cd_irqt(9,&host);assert(!line.value && !external_calls);
    cd.raw=1;mmc_gpio_set_uim2_en(&host,1);cd.error=-EIO;
    mmc_gpio_cd_irqt(9,&host);assert(!line.value && !external_calls);cd.error=0;
    sd_result=0;assert(!mmc_rescan_try_freq(&host,400000) && !line.value);
    sd_result=-ENODEV;cd.raw=1;
    assert(mmc_rescan_try_freq(&host,400000)==-EIO && line.value==1 && !external_calls);
    cd.raw=0;assert(mmc_rescan_try_freq(&host,400000)==-EIO && !line.value && !external_calls);
    cd.raw=1;mmc_gpio_set_uim2_en(&host,1);mmc_stop_host(&host);
    assert(!line.value && cancelled && host.rescan_disable && !bus_refs && !claims && !external_calls);
    mmc_gpio_set_uim2_en(&host,1);assert(line.value==1);release();
    assert(!line.value && !ctx.uim2_gpio && ctx.uim2_local);
#ifdef CONFIG_SONY_MMC_EXTENSION
    ctx.status=true;
#endif
    mmc_gpio_tray_close_set_uim2(&host,1);assert(!line.value && !external_calls);
    reset();parent.property=false;assert(!mmc_gpiod_request_uim2(&host));
#ifdef CONFIG_SONY_MMC_EXTENSION
    ctx.status=true;mmc_gpio_tray_close_set_uim2(&host,1);assert(external_calls==1 && external_value==1);
#endif
    puts("PASS: real MMC UIM2/CD/scan/stop: deferred/failed GPIO, devres failure, live tray checks, error/polarity and local ownership");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-mmc-uim2-') as scratch:
    source = Path(scratch) / 'uim2.c'
    source.write_text(harness)
    for label, defines in [('native', []), ('external-compat', ['-DCONFIG_SONY_MMC_EXTENSION', '-DCONFIG_SONY_EXT_UIM_CTRL'])]:
        binary = Path(scratch) / label
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                       ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-Wno-unused-function', *defines, str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
print('Host GPIO/devres model only; actual F8132 hybrid tray/SIM and electrical behavior remain unverified.')
