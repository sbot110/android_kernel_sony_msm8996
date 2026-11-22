#!/usr/bin/env python3
"""Execute Sony MSM8996 LMHv1 power and secure-packet production code.

Kernel/MMIO/SCM boundaries are models. No physical voltage, autonomous
CPR/SAW/firmware exclusion, effective thermal policy or boot is certified.
"""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / 'drivers/thermal/qcom'
DRIVER = DIR / 'msm8996-lmh-v1.c'
STATE = DIR / 'msm8996-lmh-state.c'
HEADER = DIR / 'msm8996-lmh-state.h'
PUBLIC = ROOT / 'include/linux/soc/qcom/msm8996-lmh.h'


def without_includes(source):
    return re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)


def function(source, name):
    match = re.search(r'^(?:static )?(?:int\s+|void\s*\*\s*|void\s+|irqreturn_t\s+)(?:__maybe_unused\s+)?' +
                      re.escape(name) + r'\([^;]*?\)\s*\{', source, re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


PRE = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef uint64_t resource_size_t;
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define __maybe_unused
#define __iomem
#define BIT(n) (1U<<(n))
#define EPROBE_DEFER 517
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define U32_MAX UINT32_MAX
#define PAGE_SIZE 4096U
#define PAGE_ALIGN(x) (((x)+4095U)&~4095U)
#define GFP_KERNEL 0
#define GFP_DMA 0
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define container_of(p,type,member) ((type *)((char *)(p)-offsetof(type,member)))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define mb() ((void)0)
#define REGULATOR_EVENT_VOLTAGE_CHANGE 0x40
#define REGULATOR_EVENT_PRE_VOLTAGE_CHANGE 0x100
#define REGULATOR_EVENT_ABORT_VOLTAGE_CHANGE 0x200
#define NOTIFY_OK 1
#define NOTIFY_STOP_MASK 0x8000
#define THERMAL_EVENT_UNSPECIFIED 0
static int notifier_from_errno(int err) { return err ? NOTIFY_STOP_MASK|(NOTIFY_OK-err):NOTIFY_OK; }
struct mutex { pthread_mutex_t value; };
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
struct device_node { unsigned id; };
struct device { struct device_node *of_node;void *driver_data; };
struct regulator { int uv; };
struct pre_voltage_change_data { unsigned long old_uV,min_uV,max_uV; };
struct notifier_block { unsigned member; };
struct work_struct { unsigned member; };
struct delayed_work { struct work_struct work; };
struct thermal_zone_device { void *devdata; };
struct qtee_shm { unsigned member; };
static unsigned allocations,frees;
static unsigned read_count,write_count,drop_write,call_count,flushes,invalidates;
static unsigned queue_count,notify_count,irq_depth,wake_count;
static bool stopped_notifier,stopped_irq,work_drained;
static unsigned zones_removed;
static int wake_error,scm_error,packet_mode;
static bool armv8=true,queued;
static int capabilities[256];
static void *shared_buffer;
static phys_addr_t physical=0x20000000;
static u32 dpm_register,odcm_register[6];
static char trace[1024];static unsigned trace_count;
static unsigned sensor_total=17,profile_total=13,profile_base=1,active_count=2;
static unsigned profile_requests;
static unsigned active_indices[10]={0,5},active_values[10]={66,40};
static u32 readl(void *p) { read_count++;return *(u32 *)p; }
static void writel(u32 value,void *p)
{
 write_count++;trace[trace_count++]=p==&dpm_register?'D':value?'E':'d';
 if(write_count!=drop_write)*(u32 *)p=value;
}
static void *kzalloc(size_t size,unsigned flags)
{ (void)flags;void *p=calloc(1,size);if(p)allocations++;return p; }
static void *kcalloc(size_t count,size_t size,unsigned flags)
{ return kzalloc(count*size,flags); }
static void before_free(void *p);
static void kfree(void *p) { if(p){before_free(p);frees++;free(p);} }
static phys_addr_t virt_to_phys(void *p) { shared_buffer=p;return physical; }
#define SCM_BUFFER_PHYS(p) virt_to_phys(p)
static void dmac_flush_range(void *p,void *end)
{ assert(p==shared_buffer && (uintptr_t)end-(uintptr_t)p==PAGE_SIZE);flushes++; }
static void dmac_inv_range(void *p,void *end)
{ assert(p==shared_buffer && (uintptr_t)end-(uintptr_t)p==PAGE_SIZE);invalidates++; }
static u64 div_u64(u64 n,u32 d) { return n/d; }
static bool is_scm_armv8(void) { return armv8; }
static int scm_is_call_available(unsigned service,unsigned command)
{ assert(service==0x13);return capabilities[command]; }
static void *dev_get_drvdata(struct device *dev) { return dev->driver_data; }
static int regulator_get_voltage(struct regulator *reg) { return reg->uv; }
static void enable_irq(unsigned irq) { assert(irq==55 && irq_depth);irq_depth--; }
static void disable_irq_nosync(unsigned irq) { assert(irq==55);irq_depth++; }
static int enable_irq_wake(unsigned irq) { assert(irq==55);if(!wake_error)wake_count++;return wake_error; }
static int disable_irq_wake(unsigned irq) { assert(irq==55);if(!wake_error){assert(wake_count);wake_count--;}return wake_error; }
static unsigned msecs_to_jiffies(unsigned value) { assert(value==30 || !value);return value; }
static void *system_highpri_wq=(void *)1;
static bool mod_delayed_work(void *q,struct delayed_work *work,unsigned delay)
{ assert(q==system_highpri_wq && work && (delay==30 || !delay));queued=true;queue_count++;return true; }
static int lmh_sensor_temp(struct thermal_zone_device *,int *);
static void thermal_zone_device_update(struct thermal_zone_device *zone,unsigned event)
{ int value;assert(!event && !lmh_sensor_temp(zone,&value));notify_count++; }
'''

BOUNDARY = r'''
static u32 name_word(unsigned index)
{
 char s[5];assert(index<10000);assert(snprintf(s,sizeof(s),"%04u",index)==4);
 return (u32)s[0]<<24|(u32)s[1]<<16|(u32)s[2]<<8|(u32)s[3];
}
static int scm_call2(u32 command,struct scm_desc *desc)
{
 unsigned id=command&255,i,start;
 assert(command==SCM_SIP_FNID(0x13,id));call_count++;
 if(scm_error)return scm_error;
 if(id==LMH_CHANGE_PROFILE){assert(desc->arginfo==SCM_ARGS(1,SCM_VAL));profile_requests++;return 0;}
 assert(desc->args[0]==physical);
 if(id==LMH_GET_PROFILES){
  u32 *p=shared_buffer;start=desc->args[2];
  assert(desc->arginfo==SCM_ARGS(3,SCM_RW,SCM_VAL,SCM_VAL));
  assert(desc->args[1]==40);desc->ret[0]=profile_total;
  if(packet_mode==5 && start)desc->ret[0]++;
  for(i=0;i<10 && start+i<profile_total;i++)p[i]=start+i+profile_base;
  if(packet_mode==6)p[1]=p[0];
  return 0;
 }
 assert(desc->arginfo==SCM_ARGS(2,SCM_RW,SCM_VAL));
 assert(desc->args[1]==sizeof(struct lmh_sensor_packet));
 struct lmh_sensor_packet *p=shared_buffer;
 if(id==LMH_GET_SENSORS){
  start=p->count;p->count=sensor_total;
  if(packet_mode==1 && start)p->count++;
  for(i=0;i<10 && start+i<sensor_total;i++){
   p->sensor[i].name=0x54454d50;p->sensor[i].node_id=name_word(start+i);
  }
  if(packet_mode==2)p->sensor[1]=p->sensor[0];
  return 0;
 }
 assert(id==LMH_GET_INTENSITY);p->count=active_count;
 for(i=0;i<active_count && i<10;i++){
  p->sensor[i].name=0x54454d50;p->sensor[i].node_id=name_word(active_indices[i]);
  p->sensor[i].intensity=active_values[i]*10;p->sensor[i].max_intensity=1000;
 }
 if(packet_mode==3)p->sensor[0].node_id=name_word(9999);
 if(packet_mode==4)p->sensor[1]=p->sensor[0];
 if(packet_mode==7){p->sensor[0].max_intensity=0;p->sensor[0].intensity=250;}
 if(packet_mode==8){p->sensor[0].max_intensity=0;p->sensor[0].intensity=UINT_MAX;}
 return 0;
}
static DEFINE_MUTEX(scm_lmh_lock);
static DEFINE_MUTEX(lmh_registry_lock);
static struct lmh_v1 *lmh_claim,*lmh_published;
static struct lmh_v1 *stopping;
static void before_free(void *p)
{
 if(stopping && p==stopping->sensor)assert(work_drained && zones_removed==stopping->sensor_count);
 if(stopping && p==stopping->profiles)assert(work_drained);
}
static int regulator_unregister_notifier(struct regulator *reg,struct notifier_block *notifier)
{
 assert(stopping && reg==stopping->s11 && notifier==&stopping->voltage);
 assert(!lmh_published && stopping->power.retired && !stopping->online);
 assert(!pthread_mutex_trylock(&stopping->lock.value));assert(!pthread_mutex_unlock(&stopping->lock.value));
 stopped_notifier=true;return 0;
}
static void devm_free_irq(struct device *dev,unsigned irq,void *data)
{
 assert(stopping==data && dev==stopping->dev && irq==55 && stopped_notifier && !stopping->notifier_bound);
 /* Actual kernel irq_shutdown resets descriptor depth to1. */
 irq_depth=1;stopped_irq=true;
}
static bool cancel_delayed_work_sync(struct delayed_work *work)
{
 assert(stopping && work==&stopping->poll && stopped_irq && !stopping->irq_bound);
 queued=false;work_drained=true;return true;
}
static void thermal_zone_device_unregister(struct thermal_zone_device *zone)
{ assert(zone && work_drained);zones_removed++; }
'''

TESTS = r'''
static struct device_node node={1},other_node={2};
static struct device device={.of_node=&node};
static struct regulator regulator={.uv=800000};
static void reset(struct lmh_v1 *lmh,unsigned uv,bool odcm)
{
 unsigned i;memset(lmh,0,sizeof(*lmh));assert(!pthread_mutex_init(&lmh->lock.value,NULL));
 lmh->dev=&device;device.driver_data=lmh;lmh->s11=&regulator;regulator.uv=uv;
 lmh->dpm=&dpm_register;for(i=0;i<6;i++){lmh->odcm[i]=&odcm_register[i];odcm_register[i]=odcm;}
 lmh->power.min_uv=475000;lmh->power.max_uv=1140000;lmh->irq=55;
 dpm_register=uv/1000;read_count=write_count=drop_write=trace_count=0;
 irq_depth=queue_count=notify_count=wake_count=0;wake_error=scm_error=packet_mode=0;
 queued=false;sensor_total=17;profile_total=13;profile_base=1;active_count=2;
 lmh->sensor_error=-ENODATA;
 active_indices[0]=0;active_indices[1]=5;active_values[0]=66;active_values[1]=40;
 assert(!msm8996_lmh_baseline(&lmh->power,&lmh_io,lmh,uv));
 lmh->online=true;lmh_claim=lmh_published=lmh;
}
static void done(struct lmh_v1 *lmh)
{
 kfree(lmh->sensor);kfree(lmh->profiles);assert(!pthread_mutex_destroy(&lmh->lock.value));
 lmh_claim=lmh_published=NULL;
}
static void event_test(void)
{
 struct lmh_v1 lmh;struct pre_voltage_change_data pre;unsigned before,i;
 reset(&lmh,800000,false);pre=(struct pre_voltage_change_data){800000,900000,900000};
 assert(lmh_voltage_event(&lmh.voltage,REGULATOR_EVENT_PRE_VOLTAGE_CHANGE,&pre)==NOTIFY_OK);
 assert(write_count==7 && trace[0]=='D' && dpm_register==900);
 for(i=0;i<6;i++)assert(odcm_register[i]==1);
 assert(lmh.power.pending && lmh.power.baseline_uv==800000);
 before=write_count;assert(lmh_voltage_event(&lmh.voltage,REGULATOR_EVENT_VOLTAGE_CHANGE,(void *)900000UL)==NOTIFY_OK);
 assert(write_count==before && !lmh.power.pending && lmh.power.baseline_uv==900000);
 pre=(struct pre_voltage_change_data){900000,800000,800000};before=write_count;
 assert(lmh_voltage_event(&lmh.voltage,REGULATOR_EVENT_PRE_VOLTAGE_CHANGE,&pre)==NOTIFY_OK && write_count==before);
 trace[trace_count++]='V';
 assert(lmh_voltage_event(&lmh.voltage,REGULATOR_EVENT_VOLTAGE_CHANGE,(void *)800000UL)==NOTIFY_OK);
 assert(trace[before]=='V' && trace[before+1]=='D' && trace[before+2]=='d');
 assert(dpm_register==800 && !lmh.power.odcm_enabled && !lmh.power.pending);
 done(&lmh);
 reset(&lmh,800000,false);before=read_count;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,850000,900000)==-EOPNOTSUPP);
 assert(!lmh.power.error && !lmh.power.pending && !write_count && read_count==before);
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==0);
 before=write_count;assert(msm8996_lmh_abort(&lmh.power,800000)==-EIO);
 assert(write_count==before && dpm_register==900 && lmh.power.odcm_enabled && lmh.power.pending);
 assert(msm8996_lmh_post(&lmh.power,&lmh_io,&lmh,900000)==-EIO && write_count==before);
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-EIO && write_count==before);
 assert(msm8996_lmh_fail(&lmh.power,-ETIMEDOUT)==-EIO);done(&lmh);
 reset(&lmh,800000,false);drop_write=4;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-EIO);
 assert(write_count==4 && dpm_register==900 && odcm_register[0]==1 && odcm_register[1]==1 && !odcm_register[2]);
 before=write_count;assert(msm8996_lmh_abort(&lmh.power,800000)==-EIO && write_count==before);done(&lmh);
 reset(&lmh,800000,false);drop_write=1;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-EIO && write_count==1);
 assert(dpm_register==800 && !odcm_register[0]);done(&lmh);
 reset(&lmh,800000,false);dpm_register=801;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-EIO && !write_count);done(&lmh);
 reset(&lmh,800000,false);assert(!msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000));before=write_count;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-EBUSY && write_count==before);done(&lmh);
 reset(&lmh,800000,false);assert(!msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000));before=write_count;
 assert(msm8996_lmh_post(&lmh.power,&lmh_io,&lmh,895000)==-EPROTO && write_count==before);done(&lmh);
 reset(&lmh,850000,false);assert(!msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,850000,850000,850000));
 assert(!msm8996_lmh_post(&lmh.power,&lmh_io,&lmh,850000) && !lmh.power.odcm_enabled);done(&lmh);
 reset(&lmh,850000,false);assert(!msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,850000,851000,851000));
 assert(lmh.power.odcm_enabled);done(&lmh);
 reset(&lmh,800000,false);lmh.power.suspended=true;before=read_count;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-EBUSY && read_count==before);
 lmh.power.suspended=false;lmh.power.retired=true;
 assert(msm8996_lmh_pre(&lmh.power,&lmh_io,&lmh,800000,900000,900000)==-ESHUTDOWN && read_count==before);done(&lmh);
 reset(&lmh,800000,false);lmh.power.baseline_valid=false;dpm_register=801;before=write_count;
 assert(msm8996_lmh_baseline(&lmh.power,&lmh_io,&lmh,800000)==-ENODATA && !lmh.power.baseline_valid && write_count==before);done(&lmh);
 reset(&lmh,800000,false);lmh.power.baseline_valid=false;odcm_register[5]=1;
 assert(msm8996_lmh_baseline(&lmh.power,&lmh_io,&lmh,800000)==-EIO && !lmh.power.baseline_valid);done(&lmh);
 reset(&lmh,900000,true);lmh.power.baseline_valid=false;for(i=0;i<6;i++)odcm_register[i]=0;
 assert(msm8996_lmh_baseline(&lmh.power,&lmh_io,&lmh,900000)==-ENODATA);done(&lmh);
}
static void packet_test(void)
{
 struct lmh_v1 lmh;unsigned i,before;char name[5];
 for(i=0;i<256;i++)capabilities[i]=1;
 assert(!lmh_capabilities());armv8=false;assert(lmh_capabilities()==-EOPNOTSUPP);armv8=true;
 capabilities[LMH_GET_INTENSITY]=-ETIMEDOUT;assert(lmh_capabilities()==-ETIMEDOUT);
 capabilities[LMH_GET_INTENSITY]=0;assert(lmh_capabilities()==-ENODEV);capabilities[LMH_GET_INTENSITY]=1;
 assert(!lmh_word_name(0x43505500,name) && !strcmp(name,"CPU"));
 assert(lmh_word_name(0x43005055,name)==-EPROTO && lmh_word_name(0,name)==-EPROTO);
 physical=UINT64_C(0x100000000);before=allocations;assert(PTR_ERR(lmh_buffer_alloc(204))==-ERANGE && allocations==before+1);physical=0x20000000;
 reset(&lmh,800000,false);assert(!lmh_sensor_list(&lmh) && lmh.sensor_count==17);
 assert(!strcmp(lmh.sensor[0].name,"TEMP_0000") && !strcmp(lmh.sensor[16].name,"TEMP_0016"));
 assert(!lmh_profile_list(&lmh) && lmh.profile_count==13 && lmh.profiles[12]==13);
 bool active=false;assert(!lmh_intensity(&lmh,&active) && active && lmh.sensor[0].intensity==66 && lmh.sensor[5].intensity==40);
 scm_error=-ETIMEDOUT;assert(lmh_intensity(&lmh,&active)==-ETIMEDOUT && lmh.sensor[0].intensity==66);scm_error=0;
 for(i=3;i<=4;i++){packet_mode=i;assert(lmh_intensity(&lmh,&active)==-EPROTO && lmh.sensor[0].intensity==66);}
 packet_mode=7;assert(!lmh_intensity(&lmh,&active) && lmh.sensor[0].intensity==250);
 packet_mode=8;assert(lmh_intensity(&lmh,&active)==-EOVERFLOW && lmh.sensor[0].intensity==250);
 packet_mode=0;active_count=11;assert(lmh_intensity(&lmh,&active)==-EPROTO);active_count=0;
 assert(!lmh_intensity(&lmh,&active) && !active && !lmh.sensor[0].intensity);done(&lmh);
 for(i=1;i<=2;i++){reset(&lmh,800000,false);packet_mode=i;assert(lmh_sensor_list(&lmh)==-EPROTO);done(&lmh);}
 for(i=5;i<=6;i++){reset(&lmh,800000,false);packet_mode=i;assert(lmh_profile_list(&lmh)==-EPROTO);done(&lmh);}
 reset(&lmh,800000,false);sensor_total=257;assert(lmh_sensor_list(&lmh)==-EPROTO);done(&lmh);
 reset(&lmh,800000,false);profile_total=65;assert(lmh_profile_list(&lmh)==-EPROTO);done(&lmh);
 assert(flushes==invalidates);
}
static void state_pm_test(void)
{
 struct lmh_v1 lmh;struct qcom_msm8996_lmh_v1_state state;unsigned before;
 reset(&lmh,800000,false);assert(!lmh_profile_list(&lmh) && !lmh_sensor_list(&lmh));
 bool active=false;assert(!lmh_intensity(&lmh,&active));
 before=read_count+write_count+call_count;
 assert(!qcom_msm8996_lmh_v1_get_state_by_node(&node,&state));
 assert(state.online && state.baseline_uv==800000 && !state.profile_requested);
 assert(read_count+write_count+call_count==before);
 assert(qcom_msm8996_lmh_v1_get_state_by_node(&other_node,&state)==-EPROBE_DEFER);
 lmh_published=NULL;assert(qcom_msm8996_lmh_v1_get_state_by_node(&node,&state)==-EPROBE_DEFER && !state.baseline_valid);lmh_published=&lmh;
 assert(!qcom_msm8996_lmh_v1_request_profile(&node,3) && lmh.profile_requested && lmh.last_profile==3);
 before=call_count;assert(qcom_msm8996_lmh_v1_request_profile(&node,0)==-EINVAL && call_count==before);
 assert(qcom_msm8996_lmh_v1_request_profile(&node,99)==-EINVAL && call_count==before);
 assert(!lmh_suspend(&device) && wake_count==1 && lmh.power.suspended);
 assert(!lmh_suspend_noirq(&device));lmh.polling=true;
 assert(lmh_suspend_noirq(&device)==-EBUSY);lmh.polling=false;
 assert(qcom_msm8996_lmh_v1_request_profile(&node,3)==-EBUSY && call_count==before);
 assert(!lmh_resume(&device) && !wake_count && !lmh.power.suspended);
 assert(!lmh_suspend(&device));regulator.uv=850000;
 assert(lmh_resume(&device)==-ESTALE && lmh.power.error==-ESTALE && lmh.power.suspended);
 before=call_count;assert(qcom_msm8996_lmh_v1_request_profile(&node,3)==-ESTALE && call_count==before);
 lmh.power.retired=true;lmh.online=false;lmh_published=NULL;
 assert(qcom_msm8996_lmh_v1_get_state_by_node(&node,&state)==-ESHUTDOWN && state.retired && state.quarantined);done(&lmh);
 reset(&lmh,800000,false);assert(!lmh_sensor_list(&lmh));
 struct thermal_zone_device zones[17];for(unsigned i=0;i<17;i++){zones[i].devdata=&lmh.sensor[i];lmh.sensor[i].zone=&zones[i];}
 int value;assert(lmh_sensor_temp(&zones[0],&value)==-ENODATA);
 assert(lmh_irq(55,&lmh)==IRQ_HANDLED && irq_depth==1 && lmh.polling && queued);
 queued=false;lmh_poll(&lmh.poll.work);assert(irq_depth==1 && lmh.polling && queued && notify_count==17);
 assert(lmh_suspend(&device)==-EBUSY && !wake_count);
 scm_error=-ETIMEDOUT;lmh_poll(&lmh.poll.work);assert(lmh_sensor_temp(&zones[0],&value)==-ETIMEDOUT && lmh.sensor[0].intensity==66 && notify_count==17);
 scm_error=0;
 assert(lmh_suspend(&device)==-ETIMEDOUT && !wake_count);
 active_count=0;queued=false;lmh_poll(&lmh.poll.work);
 assert(!irq_depth && !lmh.polling && !queued && notify_count==34 && lmh.power.error==-ETIMEDOUT && !lmh.sensor_error);
 assert(!lmh_sensor_temp(&zones[0],&value) && !value);done(&lmh);
 reset(&lmh,800000,false);assert(!lmh_profile_list(&lmh));scm_error=-ETIMEDOUT;
 assert(qcom_msm8996_lmh_v1_request_profile(&node,3)==-ETIMEDOUT && !lmh.profile_requested && lmh.power.error==-ETIMEDOUT);done(&lmh);
 reset(&lmh,800000,false);profile_base=0;before=profile_requests;
 assert(!lmh_profile_list(&lmh) && lmh.profiles[0]==0 && profile_requests==before && !lmh.profile_requested);
 assert(!qcom_msm8996_lmh_v1_request_profile(&node,0) && lmh.profile_requested && !lmh.last_profile && profile_requests==before+1);done(&lmh);
}
static void pm_irq_race_test(void)
{
 struct lmh_v1 lmh;struct thermal_zone_device zones[17];bool active=false;int value;
 reset(&lmh,800000,false);assert(!lmh_sensor_list(&lmh) && !lmh_intensity(&lmh,&active));
 for(unsigned i=0;i<17;i++){zones[i].devdata=&lmh.sensor[i];lmh.sensor[i].zone=&zones[i];}
 assert(!lmh_suspend(&device));
 assert(lmh_sensor_temp(&zones[0],&value)==-EBUSY);
 assert(lmh_irq(55,&lmh)==IRQ_HANDLED && lmh.polling && irq_depth==1 && queued);
 unsigned before=call_count;queued=false;lmh_poll(&lmh.poll.work);
 assert(call_count==before && !queued && lmh.polling);
 assert(!lmh_resume(&device) && queued && !lmh.power.suspended);
 active_count=0;queued=false;lmh_poll(&lmh.poll.work);
 assert(!irq_depth && !lmh.polling && !queued);done(&lmh);
}
static void *read_until_retirement(void *unused)
{
 unsigned i;struct qcom_msm8996_lmh_v1_state state;(void)unused;
 for(i=0;i<10000;i++){
  int ret=qcom_msm8996_lmh_v1_get_state_by_node(&node,&state);
  assert(ret==0 || ret==-ESHUTDOWN);
  assert(state.sensor_count==17 && state.profile_count==13);
 }
 return NULL;
}
static void lifetime_test(void)
{
 struct lmh_v1 lmh;struct thermal_zone_device zones[17];pthread_t reader;
 reset(&lmh,800000,false);assert(!lmh_sensor_list(&lmh) && !lmh_profile_list(&lmh));
 bool active=false;assert(!lmh_intensity(&lmh,&active));
 for(unsigned i=0;i<17;i++){zones[i].devdata=&lmh.sensor[i];lmh.sensor[i].zone=&zones[i];}
 lmh.notifier_bound=lmh.irq_bound=true;stopping=&lmh;
 stopped_notifier=stopped_irq=work_drained=false;zones_removed=0;
 assert(lmh_irq(55,&lmh)==IRQ_HANDLED && irq_depth==1 && queued);
 assert(!pthread_create(&reader,NULL,read_until_retirement,NULL));
 lmh_stop(&lmh);assert(!pthread_join(reader,NULL));
 assert(lmh.power.retired && !lmh.online && !lmh_published && !lmh.sensor && !lmh.profiles);
 assert(stopped_notifier && stopped_irq && work_drained && zones_removed==17 && !queued);
 unsigned before=call_count;assert(qcom_msm8996_lmh_v1_request_profile(&node,3)==-EPROBE_DEFER && call_count==before);
 stopping=NULL;done(&lmh);
}
int main(void)
{
 event_test();packet_test();state_pm_test();pm_irq_race_test();lifetime_test();assert(allocations==frees);
 puts("PASS: actual LMHv1 S11 before-rise/after-drop,850mV boundary, lost/partial ACK, ambiguous-range veto, abort quarantine, real MMIO readback and sticky faults");
 puts("PASS: original service0x13 SCM sensor/profile/intensity ABI, bounded pagination, malformed packets, capability failures, raw intensity, no fabricated unthrottle");
 puts("PASS: actual published-node snapshots, source profile request, IRQ poll/rearm, noirq/PM mismatch, concurrent retirement and drain-before-free lifecycle");
 return 0;
}
'''

FUNCTIONS = ['lmh_read_dpm', 'lmh_read_odcm', 'lmh_write_dpm', 'lmh_write_odcm',
             'lmh_voltage_event', 'lmh_capabilities', 'lmh_buffer_alloc', 'lmh_buffer_call',
             'lmh_word_name', 'lmh_sensor_list', 'lmh_profile_list', 'lmh_intensity',
             'lmh_sensor_temp', 'lmh_notify_sensors', 'lmh_poll', 'lmh_irq',
             'lmh_suspend', 'lmh_resume', 'lmh_suspend_noirq', 'lmh_stop', 'qcom_msm8996_lmh_v1_get_state_by_node',
             'qcom_msm8996_lmh_v1_request_profile']


def harness(state, driver):
    scm = (ROOT / 'include/soc/qcom/scm.h').read_text()
    abi = next(line for line in scm.splitlines() if line.startswith('#define MAX_SCM_ARGS')) + '\n'
    abi += next(line for line in scm.splitlines() if line.startswith('#define MAX_SCM_RETS')) + '\n'
    abi += scm[scm.index('enum scm_arg_types'):scm.index('/**\n * struct scm_desc')]
    abi += re.search(r'struct scm_desc \{.*?\n\};', scm, re.S).group() + '\n'
    abi += next(line for line in scm.splitlines() if line.startswith('#define SCM_SIP_FNID')) + '\n'
    abi += next(line for line in scm.splitlines() if line.startswith('#define SCM_SVC_LMH')) + '\n'
    definitions = '\n'.join(line for line in driver.splitlines() if line.startswith('#define LMH_')) + '\n'
    structures = driver[driver.index('struct lmh_sensor_info {'):driver.index('/* A lifetime-bounded')]
    public = re.search(r'struct qcom_msm8996_lmh_v1_state \{.*?\n\};', PUBLIC.read_text(), re.S).group()+'\n'
    core = without_includes(HEADER.read_text()) + without_includes(state)
    output = PRE + abi + definitions + core + structures + public + BOUNDARY
    for name in FUNCTIONS:
        output += function(driver, name)
        if name == 'lmh_write_odcm':
            output += re.search(r'static const struct msm8996_lmh_io lmh_io = \{.*?\n\};', driver, re.S).group() + '\n'
    return output + TESTS


def run(state, driver, path, name, expected=True):
    c, binary = path/(name+'.c'), path/name
    c.write_text(harness(state, driver))
    if os.environ.get('LMH_V1_DEBUG'):
        Path(os.environ['LMH_V1_DEBUG']).write_text(c.read_text())
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('LMH_V1_SANITIZE') else []
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + ['-std=gnu11','-O1','-Wall','-Wextra','-Werror','-pthread',*flags,str(c),'-o',str(binary)],check=True)
    result = subprocess.run([str(binary)],capture_output=True,text=True,timeout=60)
    assert (result.returncode==0)==expected,(name,result.stdout,result.stderr)
    if expected: print(result.stdout,end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--report',type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    state, driver = STATE.read_text(), DRIVER.read_text()
    mutations = [
        ('raise-scale-after-supply','core','msm8996_lmh_pre','if (min_uv > old_uv)', 'if (false)'),
        ('drop-scale-before-confirmation','core','msm8996_lmh_pre','if (min_uv > old_uv)', 'if (true)'),
        ('lower-mitigation-after-abort','core','msm8996_lmh_abort','return msm8996_lmh_fail(power, -EIO);', 'power->pending = false; return 0;'),
        ('accept-ambiguous-voltage','core','msm8996_lmh_pre','if (min_uv != max_uv)','if (false)'),
        ('accept-wrong-post-voltage','core','msm8996_lmh_post','uv != power->target_uv','false'),
        ('replace-first-fault','core','msm8996_lmh_fail','if (!power->error)','if (true)'),
        ('ignore-dpm-readback','driver','lmh_write_dpm','readl(lmh->dpm) == mv ? 0 : -EIO','0'),
        ('ignore-partial-odcm-write','driver','lmh_write_odcm','ret = -EIO;','ret = 0;'),
        ('accept-missing-secure-service','driver','lmh_capabilities','if (ret <= 0)','if (ret < 0)'),
        ('publish-missing-irq-throttle','driver','lmh_intensity','if (ret)\n\t\tgoto out;','if (false)\n\t\tgoto out;'),
        ('accept-unpublished-state','driver','qcom_msm8996_lmh_v1_get_state_by_node','if (lmh_published != lmh && !lmh->power.retired)','if (false)'),
        ('forget-suspended-profile','driver','qcom_msm8996_lmh_v1_request_profile','lmh->power.suspended ||','false ||'),
        ('forget-noirq-polling','driver','lmh_suspend_noirq','lmh->polling ||','false ||'),
        ('publish-stale-intensity','driver','lmh_sensor_temp','else if (lmh->sensor_error)','else if (false)'),
        ('poll-before-resume','driver','lmh_poll','lmh->power.suspended ||','false ||'),
        ('lose-wake-poll','driver','lmh_resume','if (!ret && lmh->polling)','if (false)'),
    ]
    with tempfile.TemporaryDirectory(prefix='dora-lmh-v1-') as tmp:
        path=Path(tmp);run(state,driver,path,'production')
        if args.self_test:
            for name,kind,target,before,after in mutations:
                text=state if kind=='core' else driver;body=function(text,target);assert before in body,name
                changed=text.replace(body,body.replace(before,after),1)
                run(changed if kind=='core' else state,changed if kind=='driver' else driver,path,name,False)
            print('PASS: %d unsafe LMHv1 power/SCM/PM/ownership mutations rejected'%len(mutations))
    if args.report:
        args.report.write_text(json.dumps(dict(production_state_functions=len(re.findall(r'^(?:static )?int\s+msm8996_lmh_',state,re.M)),production_backend_functions=len(FUNCTIONS),unsafe_mutations_rejected=len(mutations) if args.self_test else 0,hardware_verified=False,autonomous_exclusion_granted=False),indent=2)+'\n')


if __name__=='__main__':
    main()
