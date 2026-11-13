#!/usr/bin/env python3
"""Execute real SPM regulator/SAW APIs with fault-injected transports.

This proves software sequencing and exclusion, not physical CPR/SPM ownership,
autonomous AVS exclusion, power collapse safety, or board qualification.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/regulator/spm-regulator.c').read_text()
spm = (ROOT / 'drivers/soc/qcom/spm_devices.c').read_text()
driver = (ROOT / 'drivers/soc/qcom/msm-spm.c').read_text()
driver_header = (ROOT / 'drivers/soc/qcom/spm_driver.h').read_text()


def function(text, name):
    match = re.search(r'(?:static )?(?:inline )?(?:int|void|bool|uint32_t|unsigned int|struct msm_spm_device \*)\s*' +
                      re.escape(name) + r'\([^;]*?\)\s*\{', text, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end] + '\n'


preamble = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sched.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define EPROBE_DEFER 517
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define roundup(a,b) (DIV_ROUND_UP(a,b)*(b))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define likely(x) (x)
#define unlikely(x) (x)
#define REGULATOR_MODE_NORMAL 2
#define REGULATOR_MODE_IDLE 4
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define dev_err(...) ((void)0)
struct mutex { pthread_mutex_t native; };
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->native)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->native)); }
struct regulator_desc { const char *name; unsigned n_voltages; };
struct platform_device { int dev; };
struct regmap { u8 reg[256]; };
struct regulator_dev { void *data; };
#define rdev_get_drvdata(r) ((r)->data)
struct arm_smccc_res { long a0; };
static struct regmap hw;
static unsigned steps,fail_at,spmi_writes,spm_writes,spm_reads,smcs,limit_calls,mode_calls;
static int fault_code,smc_result,spm_level,limit_min,limit_max;
static bool partial_failure,drop_write;
static unsigned mode_history[8];
static int transport(void) { sched_yield(); return ++steps==fail_at?fault_code:0; }
static int regmap_bulk_read(struct regmap *map,unsigned off,void *out,size_t len)
{ int rc=transport();assert(map==&hw && off+len<=sizeof(hw.reg));if(!rc)memcpy(out,&map->reg[off],len);return rc; }
static int regmap_bulk_write(struct regmap *map,unsigned off,const void *in,size_t len)
{ int rc=transport();assert(map==&hw && off+len<=sizeof(hw.reg));spmi_writes++;
  if((!rc || partial_failure) && !drop_write)memcpy(&map->reg[off],in,len);
  return rc; }
static int regmap_write(struct regmap *map,unsigned off,unsigned value)
{ u8 v=value;if(off==0x45){if(mode_calls<8)mode_history[mode_calls]=value;mode_calls++;}
  return regmap_bulk_write(map,off,&v,1); }
static void udelay(unsigned delay) { (void)delay; }
static int msm_spm_set_vdd(unsigned cpu,unsigned level)
{ int rc=transport();assert(!cpu);spm_writes++;if(!rc || partial_failure){spm_level=level;hw.reg[0x41]=level;}return rc; }
static int msm_spm_get_vdd(unsigned cpu)
{ int rc=transport();assert(!cpu);spm_reads++;return rc?rc:spm_level; }
static int msm_spm_avs_set_limit(unsigned cpu,unsigned lo,unsigned hi)
{ int rc=transport();assert(!cpu && lo<=hi);limit_calls++;if(!rc){limit_min=lo;limit_max=hi;}return rc; }
static int msm_spm_avs_enable(unsigned cpu) { assert(!cpu);return transport(); }
static int msm_spm_avs_disable(unsigned cpu) { assert(!cpu);return transport(); }
static void arm_smccc_smc(unsigned fid,unsigned mask,unsigned cmd,unsigned a,unsigned b,
 unsigned c,unsigned d,unsigned e,struct arm_smccc_res *res)
{ assert(fid==0xC4000020 && mask==3 && cmd==2 && !(a|b|c|d|e));smcs++;res->a0=smc_result; }
'''
production = source[source.index('struct voltage_range {'):source.index('static int qpnp_smps_check_type(')]
production = re.sub(r'static struct regulator_ops \w+ = \{.*?\n\};', '', production, flags=re.S)
production += function(source, 'qpnp_smps_init_voltage')

# Probe must reject malformed non-progressing steps before exposing either rdev.
probe = function(source, 'spm_regulator_probe')
assert probe.index('if (!vreg->max_step_uV)') < probe.index('regulator_register(')
assert probe.index('vreg->has_avs =') < probe.index('regulator_register(')
assert 'spm_failed' not in production and 'falling back' not in production
assert 'of_node_put(avs_node)' in function(source, 'spm_regulator_avs_register')

tests = r'''
static struct spm_vreg v;
static struct regulator_dev main_rdev={&v},avs_rdev={&v};
static unsigned selector;
static void reset(void)
{
 memset(&v,0,sizeof(v));memset(&hw,0,sizeof(hw));assert(!pthread_mutex_init(&v.lock.native,NULL));
 v.regmap=&hw;v.range=&fts2_range0;v.regulator_type=QPNP_TYPE_FTS2;v.max_step_uV=100000;
 v.step_rate=1000;v.last_set_uV=v.uV=800000;v.last_set_vlevel=v.vlevel=160;v.has_avs=true;
 v.rdesc.n_voltages=(v.range->max_uV-v.range->set_point_min_uV)/v.range->step_uV+1;
 v.online=true;v.recal_cluster_mask=3;v.rdesc.name="gang";v.avs_rdesc.name="limit";
 hw.reg[0x41]=160;spm_level=160;steps=fail_at=spmi_writes=spm_writes=spm_reads=smcs=mode_calls=limit_calls=0;
 fault_code=-EIO;smc_result=0;partial_failure=drop_write=false;
}
static void finish(void) { assert(!pthread_mutex_destroy(&v.lock.native)); }
static int set_uv(int uv) { return spm_regulator_set_voltage(&main_rdev,uv,uv,&selector); }
static void invariant_fault(void)
{
 unsigned before=steps;
 assert(v.quarantined);
 assert(set_uv(800000)==-EIO);
 assert(spm_regulator_enable(&main_rdev)==-EIO);
 assert(spm_regulator_disable(&main_rdev)==-EIO);
 assert(spm_regulator_is_enabled(&main_rdev)==-EIO);
 assert(spm_regulator_get_voltage(&main_rdev)==-EIO);
 assert(spm_regulator_get_mode(&main_rdev)==0);
 assert(spm_regulator_set_mode(&main_rdev,REGULATOR_MODE_NORMAL)==-EIO);
 assert(spm_regulator_avs_set_voltage(&avs_rdev,700000,900000,&selector)==-EIO);
 assert(spm_regulator_avs_get_voltage(&avs_rdev)==-EIO);
 assert(spm_regulator_avs_enable(&avs_rdev)==-EIO);
 assert(spm_regulator_avs_disable(&avs_rdev)==-EIO);
 assert(spm_regulator_avs_is_enabled(&avs_rdev)==-EIO);
 assert(steps==before); /* No implicit retry or quarantine release. */
}
static void *consumer(void *arg)
{
 uintptr_t which=(uintptr_t)arg;unsigned i;
 for(i=0;i<300;i++) {
  if(which==0)assert(!set_uv(i&1?800000:950000));
  if(which==1) {assert(!spm_regulator_set_mode(&main_rdev,i&1?REGULATOR_MODE_NORMAL:REGULATOR_MODE_IDLE));
               assert(spm_regulator_get_voltage(&main_rdev)>0);}
  if(which==2) {unsigned s;assert(!spm_regulator_avs_set_voltage(&avs_rdev,700001,1004999,&s));
               assert(!spm_regulator_avs_enable(&avs_rdev));assert(!spm_regulator_avs_disable(&avs_rdev));}
 }
 return NULL;
}
int main(void)
{
 const struct voltage_range *ranges[]={&fts2_range0,&fts2_range1,&fts2p5_range0,&fts2p5_range1,
   &fts426_range,&hfs430_range,&ult_hf_range0,&ult_hf_range1,&hf_range0,&hf_range1};
 unsigned n,i;int errors[]={-EPROBE_DEFER,-EIO,-ETIMEDOUT,-ENODEV,1};pthread_t threads[3];
 for(i=0;i<sizeof(ranges)/sizeof(ranges[0]);i++) {
  int uv;reset();v.range=ranges[i];v.regulator_type=i==4?QPNP_TYPE_FTS426:i==5?QPNP_TYPE_HFS430:
       (i==6 || i==7)?QPNP_TYPE_ULT_HF:QPNP_TYPE_FTS2;
  for(uv=v.range->set_point_min_uV;uv<=v.range->max_uV;uv+=v.range->step_uV)
   assert(spm_regulator_vlevel_to_uv(&v,spm_regulator_uv_to_vlevel(&v,uv))==uv);
  finish();
 }
 reset();assert(!set_uv(1050000));assert(spm_writes==3 && mode_calls==2 && smcs==1);
 assert(v.last_set_uV==1050000 && spm_level==210);assert(mode_history[0]==0x80 && mode_history[1]==0x40);
 n=steps;finish();
 /* Fail every read, mode write, intermediate voltage step and final mode restore.
    A transport failure may already have applied, so retry from the cache is blocked. */
 for(i=1;i<=n;i++) {reset();fail_at=i;partial_failure=true;assert(set_uv(1050000)==-EIO);invariant_fault();finish();}
 for(i=0;i<sizeof(errors)/sizeof(errors[0]);i++) {
  reset();fail_at=3;fault_code=errors[i];partial_failure=true;
  assert(set_uv(1050000)==(errors[i]<0?errors[i]:-EIO));assert(spmi_writes==1);
  /* Only the earlier PWM write may use direct SPMI; never a voltage fallback. */
  invariant_fault();finish();
 }
 reset();smc_result=-ETIMEDOUT;assert(set_uv(900000)==-ETIMEDOUT);invariant_fault();finish();
 reset();smc_result=1;assert(set_uv(900000)==-EIO);invariant_fault();finish();
 reset();v.max_step_uV=0;assert(set_uv(900000)==-EINVAL && !spm_writes && !mode_calls);finish();
 reset();v.step_rate=0;assert(set_uv(900000)==-EINVAL && !spm_writes);finish();
 reset();v.online=false;assert(!set_uv(900000));assert(!spm_writes && spm_regulator_get_voltage(&main_rdev)==800000);
 assert(!spm_regulator_enable(&main_rdev) && v.online && spm_level==180);
 assert(!spm_regulator_disable(&main_rdev) && !spm_regulator_is_enabled(&main_rdev));finish();
 reset();v.has_avs=false;assert(spm_regulator_get_voltage(&main_rdev)==800000 && !spm_reads);finish();
 reset();v.has_avs=false;hw.reg[0x41]=1;
 assert(spm_regulator_get_voltage(&main_rdev)==-ERANGE && v.last_set_uV==800000);invariant_fault();finish();
 reset();hw.reg[0x41]=1;assert(qpnp_smps_init_voltage(&v)==-ERANGE && !spm_writes);finish();
 reset();spm_level=1;assert(spm_regulator_get_voltage(&main_rdev)==-ERANGE);invariant_fault();finish();
 reset();assert(!spm_regulator_set_mode(&main_rdev,REGULATOR_MODE_NORMAL));mode_calls=0;
 assert(!set_uv(900000) && !mode_calls && v.mode==QPNP_LOGICAL_MODE_PWM);finish();
 reset();fail_at=1;assert(spm_regulator_set_mode(&main_rdev,REGULATOR_MODE_NORMAL)==-EIO);
 assert(v.mode==QPNP_LOGICAL_MODE_AUTO);invariant_fault();finish();
 reset();v.bypass_spm=true;assert(!set_uv(900000) && !spm_writes && v.last_set_uV==900000);finish();
 reset();v.bypass_spm=true;v.mode=QPNP_LOGICAL_MODE_PWM;drop_write=true;
 assert(set_uv(900000)==-EIO && !spm_writes);invariant_fault();finish();
 reset();fail_at=2;fault_code=-EPROBE_DEFER;assert(qpnp_smps_init_voltage(&v)==-EPROBE_DEFER);finish();
 reset();assert(!qpnp_smps_init_voltage(&v) && v.uV==800000);finish();
 reset();assert(!spm_regulator_avs_set_voltage(&avs_rdev,700001,804999,&selector));
 assert(v.avs_min_uV==705000 && v.avs_max_uV==800000 && limit_max==160 && limit_min==141);
 assert(spm_regulator_avs_set_voltage(&avs_rdev,800001,804999,&selector)==-EINVAL);
 assert(spm_regulator_list_voltage(&main_rdev,selector)==705000);
 assert(!spm_regulator_list_voltage(&main_rdev,v.rdesc.n_voltages));finish();
 for(i=0;i<3;i++) {
  reset();fail_at=1;
  if(i==0)assert(spm_regulator_avs_enable(&avs_rdev)==-EIO);
  if(i==1)assert(spm_regulator_avs_disable(&avs_rdev)==-EIO);
  if(i==2)assert(spm_regulator_avs_set_voltage(&avs_rdev,700000,900000,&selector)==-EIO);
  invariant_fault();finish();
 }
 reset();for(i=0;i<3;i++)assert(!pthread_create(&threads[i],NULL,consumer,(void *)(uintptr_t)i));
 for(i=0;i<3;i++)assert(!pthread_join(threads[i],NULL));
 assert(!v.quarantined && v.last_set_uV>=800000 && v.last_set_uV<=950000);finish();
 puts("PASS: production SPM transactions: main/AVS exclusion, partial/lost ACK, quarantine, modes, SMC, real readback, bounded steps, AVS floor, boot handoff");
 return 0;
}
'''

# Independently execute actual SAW endpoint APIs: failed probes are error
# pointers, not a positive-success code or a valid reg_data address.
api_preamble = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <stdatomic.h>
#include <string.h>
#include <sched.h>
#define EPROBE_DEFER 517
#define VDD_DEFAULT 0xDEADF00D
#define VDD_UNKNOWN 0xDEADF00E
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p)||IS_ERR(p))
#define __iomem
#define per_cpu(name,cpu) ((name)[cpu])
#define for_each_possible_cpu(cpu) for((cpu)=0;(cpu)<4;(cpu)++)
#define READ_ONCE(v) __atomic_load_n(&(v),__ATOMIC_ACQUIRE)
#define WRITE_ONCE(v,n) __atomic_store_n(&(v),(n),__ATOMIC_RELEASE)
#define smp_load_acquire(p) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define smp_store_release(p,v) __atomic_store_n((p),(v),__ATOMIC_RELEASE)
struct mutex { pthread_mutex_t native; };
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->native)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->native)); }
typedef struct mutex raw_spinlock_t;
#define raw_spin_lock_init(m) assert(!pthread_mutex_init(&(m)->native,NULL))
#define raw_spin_lock_irqsave(m,f) do { (f)=0;mutex_lock(m); } while(0)
#define raw_spin_unlock_irqrestore(m,f) do { (void)(f);mutex_unlock(m); } while(0)
typedef atomic_int atomic_t;
#define atomic_set(p,n) atomic_store((p),(n))
#define atomic_read(p) atomic_load(p)
#define atomic_inc(p) atomic_fetch_add((p),1)
#define atomic_dec_and_test(p) (atomic_fetch_sub((p),1)==1)
typedef struct { pthread_mutex_t mutex;pthread_cond_t cond; } wait_queue_head_t;
#define init_waitqueue_head(q) do { assert(!pthread_mutex_init(&(q)->mutex,NULL));assert(!pthread_cond_init(&(q)->cond,NULL)); } while(0)
static void wake_up_all(wait_queue_head_t *q) { assert(!pthread_mutex_lock(&q->mutex));assert(!pthread_cond_broadcast(&q->cond));assert(!pthread_mutex_unlock(&q->mutex)); }
#define wait_event(q,condition) do { assert(!pthread_mutex_lock(&(q).mutex));while(!(condition))assert(!pthread_cond_wait(&(q).cond,&(q).mutex));assert(!pthread_mutex_unlock(&(q).mutex)); } while(0)
struct list_head { struct list_head *next,*prev; };
#define LIST_HEAD(n) struct list_head n={&n,&n}
#define INIT_LIST_HEAD(h) ((h)->next=(h)->prev=(h))
static void list_add_tail(struct list_head *n,struct list_head *h) { n->next=h;n->prev=h->prev;h->prev->next=n;h->prev=n; }
#define list_add(n,h) list_add_tail(n,h)
#define entry(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(p=entry((h)->next,__typeof__(*p),m);&p->m!=(h);p=entry(p->m.next,__typeof__(*p),m))
struct device_node { int unused; };
struct device { struct device_node *of_node;unsigned refs; };
struct platform_device { struct device dev;void *data; };
struct resource { unsigned start,end; };
struct msm_spm_device { struct list_head list,claim;struct device *owner;struct resource resource;
 raw_spinlock_t lock;atomic_t users;wait_queue_head_t drain;bool published,retired,initialized;
 int error;const char *name;unsigned reg_data,cpu_vdd,mask;void *modes; };
#define cpumask_intersects(a,b) (!!(*(a)&*(b)))
static struct device *get_device(struct device *d) { d->refs++;return d; }
static unsigned module_refs;
#define THIS_MODULE NULL
static void __module_get(void *m) { (void)m;module_refs++; }
static struct msm_spm_device *cpu_vctl_device[4];
static unsigned nr_cpu_ids=4;
static bool cpu_possible(unsigned cpu) { return cpu<4; }
struct msm_spm_vdd_info { struct msm_spm_device *vctl_dev;uint32_t vlevel;int err; };
enum msm_spm_avs_irq { MSM_SPM_AVS_IRQ_MIN,MSM_SPM_AVS_IRQ_MAX };
static int drv_result,ipi_result,drv_calls,maps,unmaps,writes;
static bool inaccessible;
static pthread_mutex_t boundary_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t boundary_cond=PTHREAD_COND_INITIALIZER;
static bool pause_driver,entered_driver,release_driver;
static int msm_spm_drv_set_vdd(unsigned *d,unsigned l)
{ assert(d && !inaccessible);(void)l;drv_calls++;
 assert(!pthread_mutex_lock(&boundary_lock));entered_driver=true;assert(!pthread_cond_broadcast(&boundary_cond));
 while(pause_driver && !release_driver)assert(!pthread_cond_wait(&boundary_cond,&boundary_lock));
 assert(!pthread_mutex_unlock(&boundary_lock));return drv_result; }
static int msm_spm_drv_get_vdd(unsigned *d) { assert(d && !inaccessible);drv_calls++;return drv_result?drv_result:160; }
static int msm_spm_drv_get_avs_enable(unsigned *d) { assert(d && !inaccessible);drv_calls++;return drv_result; }
static int msm_spm_drv_set_avs_enable(unsigned *d,bool en) { (void)en;return msm_spm_drv_get_avs_enable(d); }
static int msm_spm_drv_set_avs_limit(unsigned *d,unsigned l,unsigned h) { (void)l;(void)h;return msm_spm_drv_get_avs_enable(d); }
static int msm_spm_drv_set_avs_irq_enable(unsigned *d,enum msm_spm_avs_irq i,bool en) { (void)i;(void)en;return msm_spm_drv_get_avs_enable(d); }
static int msm_spm_drv_avs_clear_irq(unsigned *d,enum msm_spm_avs_irq i) { (void)i;return msm_spm_drv_get_avs_enable(d); }
static int smp_call_function_any(unsigned *mask,void (*fn)(void *),void *info,bool wait)
{ assert(mask && wait);if(ipi_result)return ipi_result;fn(info);return 0; }
static DEFINE_MUTEX(spm_owner_lock);
static LIST_HEAD(spm_list);
static LIST_HEAD(spm_claims);
'''
api_names = ['msm_spm_get','msm_spm_put','msm_spm_state_locked','msm_spm_fault_locked',
             'msm_spm_lifetime_init','msm_spm_retire','msm_spm_claim','msm_spm_get_device_by_name',
             'msm_spm_vctl_device', 'msm_spm_smp_set_vdd', 'msm_spm_probe_done',
             'msm_spm_set_vdd', 'msm_spm_get_vdd', 'msm_spm_is_avs_enabled',
             'msm_spm_avs_enable', 'msm_spm_avs_disable', 'msm_spm_avs_set_limit',
             'msm_spm_avs_enable_irq', 'msm_spm_avs_disable_irq', 'msm_spm_avs_clear_irq',
             'msm_spm_turn_on_cpu_rail']
api_production = ''.join(function(spm, name) for name in api_names)
api_tests = r'''
static void reject(unsigned cpu,int error)
{
 int before=drv_calls;
 assert(msm_spm_set_vdd(cpu,160)==error);
 assert(msm_spm_get_vdd(cpu)==error);
 assert(msm_spm_is_avs_enabled(cpu)==error);
 assert(msm_spm_avs_enable(cpu)==error);
 assert(msm_spm_avs_disable(cpu)==error);
 assert(msm_spm_avs_set_limit(cpu,140,180)==error);
 assert(msm_spm_avs_enable_irq(cpu,MSM_SPM_AVS_IRQ_MIN)==error);
 assert(msm_spm_avs_disable_irq(cpu,MSM_SPM_AVS_IRQ_MAX)==error);
 assert(msm_spm_avs_clear_irq(cpu,MSM_SPM_AVS_IRQ_MIN)==error);
 assert(drv_calls==before);
}
static void initialize(struct msm_spm_device *d)
{ unsigned i;memset(d,0,sizeof(*d));msm_spm_lifetime_init(d);d->cpu_vdd=VDD_DEFAULT;
 d->initialized=true;smp_store_release(&d->published,true);for(i=0;i<4;i++)cpu_vctl_device[i]=d;
 drv_result=ipi_result=0;inaccessible=false; }
static void destroy(struct msm_spm_device *d)
{ assert(!atomic_read(&d->users));assert(!pthread_mutex_destroy(&d->lock.native));
 assert(!pthread_cond_destroy(&d->drain.cond));assert(!pthread_mutex_destroy(&d->drain.mutex)); }
static atomic_bool retired_done;
static void *retire_worker(void *p) { msm_spm_retire(p,-ESHUTDOWN);atomic_store(&retired_done,true);return NULL; }
static void *voltage_worker(void *p) { (void)p;assert(!msm_spm_set_vdd(0,180));return NULL; }
int main(void)
{
 struct msm_spm_device d,other;struct device_node node={0},alias={1};
 struct platform_device provider={.dev={.of_node=&node}},competitor={.dev={.of_node=&alias}};
 struct resource resource={0x9a10000,0x9a10fff};unsigned i;int errors[]={-EIO,-EPROBE_DEFER,-ENOMEM};pthread_t thread,writer;
 reject(0,-EPROBE_DEFER);reject(4,-EINVAL);reject(~0U,-EINVAL);
 assert(msm_spm_probe_done()==-EPROBE_DEFER);
 for(i=0;i<3;i++) {cpu_vctl_device[0]=ERR_PTR(errors[i]);reject(0,errors[i]);assert(msm_spm_probe_done()==errors[i]);
 assert(msm_spm_turn_on_cpu_rail(NULL,160,0,0)==errors[i] && !maps);}
 initialize(&d);assert(!msm_spm_probe_done());
 assert(msm_spm_turn_on_cpu_rail(NULL,160,0,0)==-EAGAIN && !maps && !writes);
 assert(!msm_spm_set_vdd(0,160) && d.cpu_vdd==160);
 assert(msm_spm_get_vdd(0)==160);
 assert(!msm_spm_turn_on_cpu_rail(NULL,160,0,0) && !maps);
 drv_result=-ETIMEDOUT;assert(msm_spm_set_vdd(0,180)==-ETIMEDOUT && d.cpu_vdd==VDD_UNKNOWN);
 assert(msm_spm_turn_on_cpu_rail(NULL,160,0,0)==-ETIMEDOUT && !maps);
 drv_result=0;reject(1,-ETIMEDOUT); /* CPU aliases cannot clear the controller fault. */
 destroy(&d);initialize(&d);ipi_result=-ENXIO;
 assert(msm_spm_set_vdd(0,180)==-ENXIO && d.cpu_vdd==VDD_DEFAULT);
 assert(msm_spm_turn_on_cpu_rail(NULL,160,-1,0)==-EINVAL);
 cpu_vctl_device[0]=NULL;assert(msm_spm_turn_on_cpu_rail(NULL,160,0,0)==-EPROBE_DEFER);
 assert(!maps && !unmaps && !writes);destroy(&d);
 /* A raw reader held before retire pins mappings until its final put. */
 initialize(&d);assert(!msm_spm_get(&d));atomic_store(&retired_done,false);
 assert(!pthread_create(&thread,NULL,retire_worker,&d));
 while(!READ_ONCE(d.retired))sched_yield();
 assert(!atomic_load(&retired_done));reject(0,-ESHUTDOWN);
 msm_spm_put(&d);assert(!pthread_join(thread,NULL) && atomic_load(&retired_done));
 inaccessible=true;reject(0,-ESHUTDOWN);destroy(&d);
 /* Force removal to overlap a real production set_vdd sender and target. */
 initialize(&d);pause_driver=true;entered_driver=release_driver=false;atomic_store(&retired_done,false);
 assert(!pthread_create(&writer,NULL,voltage_worker,NULL));
 assert(!pthread_mutex_lock(&boundary_lock));while(!entered_driver)assert(!pthread_cond_wait(&boundary_cond,&boundary_lock));
 assert(!pthread_mutex_unlock(&boundary_lock));assert(!pthread_create(&thread,NULL,retire_worker,&d));
 assert(!atomic_load(&retired_done));
 assert(!pthread_mutex_lock(&boundary_lock));release_driver=true;assert(!pthread_cond_broadcast(&boundary_cond));assert(!pthread_mutex_unlock(&boundary_lock));
 assert(!pthread_join(writer,NULL) && !pthread_join(thread,NULL));pause_driver=false;inaccessible=true;
 reject(0,-ESHUTDOWN);destroy(&d);
 /* One permanent claim per node, physical range, CPU-vctl mask and name. */
 memset(&d,0,sizeof(d));d.name="gang";d.mask=15;inaccessible=false;
 assert(!msm_spm_claim(&provider,&d,&resource) && provider.dev.refs==1 && module_refs==1);
 d.initialized=true;smp_store_release(&d.published,true);list_add(&d.list,&spm_list);
 assert(msm_spm_get_device_by_name("gang")==&d);
 memset(&other,0,sizeof(other));other.name="alias";other.mask=0;
 assert(msm_spm_claim(&competitor,&other,&resource)==-EBUSY && !competitor.dev.refs);
 resource.start+=0x10000;resource.end+=0x10000;other.mask=1;
 assert(msm_spm_claim(&competitor,&other,&resource)==-EBUSY && !competitor.dev.refs);
 other.mask=0;other.name="gang";assert(msm_spm_claim(&competitor,&other,&resource)==-EBUSY);
 msm_spm_retire(&d,-EIO);assert(PTR_ERR(msm_spm_get_device_by_name("gang"))==-ESHUTDOWN);
 assert(msm_spm_claim(&competitor,&other,&resource)==-ESHUTDOWN && module_refs==1);
 for(i=0;i<4;i++) { cpu_vctl_device[i]=&d; }
 reject(0,-EIO);destroy(&d);
 puts("PASS: actual SAW APIs: signed errors, alias quarantine, counted atomic-safe lifetime, deterministic in-flight IPI retirement, persistent owner/reprobe and no unowned bootstrap MMIO");
 return 0;
}
'''

# Execute the real version tables and register helpers, rather than replacing
# those helpers with the API-boundary model above. MMIO is the only transport
# substituted here; these tests do not establish physical AVS exclusion.
register_preamble = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
#define __iomem
#define CONFIG_MSM_AVS_HW 1
#define GFP_KERNEL 0
#define VDD_DEFAULT 0xDEADF00D
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define module_param_named(...)
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define pr_info(...) ((void)0)
#define __WARN() assert(0)
#define mb() ((void)0)
#define wmb() ((void)0)
enum msm_spm_avs_irq { MSM_SPM_AVS_IRQ_MIN,MSM_SPM_AVS_IRQ_MAX };
static unsigned reads,writes,drop_at,allocations,frees,delays;
static bool drop_all,complete_pmic;
static uint32_t hwregs[0x1000/4];
static unsigned vctl_offset,pmic_offset;
static uint32_t __raw_readl(void *p)
{ uintptr_t offset=(uintptr_t)p-(uintptr_t)hwregs;
 assert(offset<sizeof(hwregs) && !(offset&3));reads++;return hwregs[offset/4]; }
static void __raw_writel(uint32_t value,void *p)
{ uintptr_t offset=(uintptr_t)p-(uintptr_t)hwregs;
 assert(offset<sizeof(hwregs) && !(offset&3));writes++;
 if(drop_all || writes==drop_at)return;
 hwregs[offset/4]=value;
 if(complete_pmic && offset==vctl_offset)hwregs[pmic_offset/4]=value&0xff; }
static void udelay(unsigned value) { delays+=value; }
static void *test_calloc(size_t n,size_t size)
{ void *p=calloc(n?n:1,size);if(p)allocations++;return p; }
#define kcalloc(n,s,f) test_calloc((n),(s))
#define kmalloc_array(n,s,f) test_calloc((n),(s))
static void kfree(void *p) { if(p){frees++;free(p);} }
'''
register_types = re.sub(r'^#include[^\n]*', '', driver_header, flags=re.M)
register_types += r'''
struct msm_spm_power_modes { uint32_t mode,ctl; };
struct msm_spm_device { bool initialized;uint32_t cpu_vdd,num_modes;
 struct msm_spm_driver_data reg_data;struct msm_spm_power_modes *modes; };
'''
register_production = driver[driver.index('#define MSM_SPM_PMIC_STATE_IDLE'):
                             driver.index('static void msm_spm_drv_flush_shadow')]
register_names = ['msm_spm_drv_flush_shadow', 'msm_spm_drv_load_shadow',
                  'msm_spm_drv_get_num_spm_entry',
                  'msm_spm_pmic_arb_present', 'msm_spm_drv_set_vctl2',
                  'msm_spm_drv_get_num_pmic_data', 'msm_spm_drv_get_sts_pmic_state',
                  'msm_spm_drv_get_sts_curr_pmic_data', 'msm_spm_drv_get_saw2_ver',
                  'msm_spm_drv_get_avs_enable', 'msm_spm_drv_set_avs_enable',
                  'msm_spm_drv_set_avs_limit', 'msm_spm_drv_avs_irq_mask',
                  'msm_spm_drv_set_avs_irq_enable', 'msm_spm_drv_avs_clear_irq',
                  'msm_spm_drv_flush_seq_entry', 'msm_spm_drv_write_seq_data',
                  'msm_spm_drv_get_vdd']
register_production += ''.join(function(driver, name) for name in register_names)
register_production += driver[driver.index('#ifdef CONFIG_MSM_AVS_HW'):
                              driver.index('static inline int msm_spm_drv_validate_data')]
register_production += ''.join(function(driver, name) for name in [
    'msm_spm_drv_validate_data', 'msm_spm_drv_set_vdd', 'msm_spm_drv_get_pmic_port',
    'msm_spm_drv_set_pmic_data', 'msm_spm_drv_reinit', 'msm_spm_drv_reg_init',
    'msm_spm_drv_init'])
register_production += function(spm, 'msm_spm_free_program')
register_production += function(spm, 'msm_spm_dev_init')
# A failure after publication prerequisites still drains before freeing data;
# a permanent descriptor does not require leaking its program allocations.
probe = function(spm, 'msm_spm_dev_probe')
remove = function(spm, 'msm_spm_dev_remove')
assert probe.index('msm_spm_retire(dev, ret)') < probe.index('msm_spm_free_program(dev)')
assert remove.index('msm_spm_retire(') < remove.index('msm_spm_free_program(')

register_tests = r'''
static struct msm_spm_driver_data d;
static struct msm_spm_platform_data data;
static unsigned regoff(unsigned r) { return d.reg_offsets[r]/4; }
static void reset(unsigned version)
{
 memset(hwregs,0,sizeof(hwregs));memset(&d,0,sizeof(d));memset(&data,0,sizeof(data));
 reads=writes=drop_at=delays=0;drop_all=complete_pmic=false;
 data.reg_base_addr=hwregs;data.reg_size=sizeof(hwregs);data.ver_reg=0xfd0;
 hwregs[data.ver_reg/4]=version;
 assert(!msm_spm_drv_reg_init(&d,&data));
 vctl_offset=d.reg_offsets[MSM_SPM_REG_SAW_VCTL];
 pmic_offset=d.reg_offsets[MSM_SPM_REG_SAW_PMIC_STS];
 d.vctl_port=0;d.vctl_port_ub=-1;d.phase_port=1;d.pfm_port=2;d.vctl_timeout_us=3;
 hwregs[regoff(MSM_SPM_REG_SAW_ID)]=BIT(2)|(7<<4)|(255U<<24);
 reads=writes=0;
}
static void finish_driver(void)
{ kfree(d.reg_seq_entry_shadow);d.reg_seq_entry_shadow=NULL;d.seq_entries=0; }
int main(void)
{
 unsigned versions[]={0x20010000,0x30000000,0x10000000,0x40010000},i,ctl,limit,before;
 struct msm_spm_driver_data second;
 struct msm_spm_device device;
 struct msm_spm_seq_entry modes[3];
 uint8_t long_cmd[257],short_cmd=0x0f;
 for(i=0;i<ARRAY_SIZE(versions);i++) {
  reset(versions[i]);ctl=regoff(MSM_SPM_REG_SAW_AVS_CTL);limit=regoff(MSM_SPM_REG_SAW_AVS_LIMIT);
  hwregs[ctl]=BIT(27)|BIT(6)|1;d.reg_shadow[MSM_SPM_REG_SAW_AVS_CTL]=0;
  assert(msm_spm_drv_get_avs_enable(&d)==1 && reads==1 && !writes);
  hwregs[ctl]&=~1U;assert(!msm_spm_drv_get_avs_enable(&d));
  assert(!msm_spm_drv_set_avs_enable(&d,true) && hwregs[ctl]==(BIT(27)|BIT(6)|1));
  before=writes;assert(!msm_spm_drv_set_avs_enable(&d,true) && writes==before);
  drop_all=true;assert(msm_spm_drv_set_avs_enable(&d,false)==-EIO);drop_all=false;
  assert(!msm_spm_drv_set_avs_enable(&d,false));
  assert(!msm_spm_drv_set_avs_limit(&d,140,180) && hwregs[limit]==((180U<<16)|140));
  drop_all=true;assert(msm_spm_drv_set_avs_limit(&d,150,190)==-EIO);drop_all=false;
  before=writes;assert(msm_spm_drv_set_avs_limit(&d,181,180)==-EINVAL);
  assert(msm_spm_drv_set_avs_limit(&d,0,256)==-EINVAL && writes==before);
  /* A stale shadow must not clear the live AVS enable or unrelated bits. */
  hwregs[ctl]=BIT(27)|BIT(6)|1;d.reg_shadow[MSM_SPM_REG_SAW_AVS_CTL]=0;
  assert(!msm_spm_drv_set_avs_irq_enable(&d,MSM_SPM_AVS_IRQ_MIN,true));
  assert(hwregs[ctl]==(BIT(27)|BIT(6)|1|BIT(1)));
  drop_all=true;assert(msm_spm_drv_set_avs_irq_enable(&d,MSM_SPM_AVS_IRQ_MAX,true)==-EIO);
  drop_all=false;before=writes;assert(msm_spm_drv_set_avs_irq_enable(&d,99,true)==-EINVAL && writes==before);
  drop_at=writes+1;assert(msm_spm_drv_avs_clear_irq(&d,MSM_SPM_AVS_IRQ_MIN)==-EIO);
  assert(hwregs[ctl]&BIT(1));drop_at=writes+2;
  assert(msm_spm_drv_avs_clear_irq(&d,MSM_SPM_AVS_IRQ_MIN)==-EIO && !(hwregs[ctl]&BIT(1)));
  drop_at=0;assert(!msm_spm_drv_set_avs_irq_enable(&d,MSM_SPM_AVS_IRQ_MIN,true));
  assert(!msm_spm_drv_avs_clear_irq(&d,MSM_SPM_AVS_IRQ_MIN));
  assert(hwregs[ctl]==(BIT(27)|BIT(6)|1|BIT(1)));
  hwregs[regoff(MSM_SPM_REG_SAW_PMIC_STS)]=160;
  assert(msm_spm_drv_get_vdd(&d)==160);
  for(before=1;before<=3;before++) {
   hwregs[regoff(MSM_SPM_REG_SAW_PMIC_STS)]=(before<<16)|160;
   assert(msm_spm_drv_get_vdd(&d)==-EBUSY);
  }
 }
 assert(msm_spm_drv_get_avs_enable(NULL)==-EINVAL && msm_spm_drv_get_vdd(NULL)==-EINVAL);
 reset(0x40010000);data.ver_reg=0xfd1;before=reads;
 assert(msm_spm_drv_reg_init(&d,&data)==-EINVAL && reads==before);
 data.ver_reg=0x1000;assert(msm_spm_drv_reg_init(&d,&data)==-EINVAL && reads==before);
 data.ver_reg=0xfd0;data.reg_size=0xfd0;assert(msm_spm_drv_reg_init(&d,&data)==-EINVAL && reads==before);
 reset(0x40010000);hwregs[data.ver_reg/4]=0x50000000;
 assert(msm_spm_drv_reg_init(&d,&data)==-ENODEV && !d.reg_offsets && !writes);
 reset(0x30000000);assert(!msm_spm_drv_init(&d,&data));
 assert(d.seq_entries==255 && d.num_pmic_data==7);
 memset(&second,0,sizeof(second));assert(!msm_spm_drv_reg_init(&second,&data));
 hwregs[regoff(MSM_SPM_REG_SAW_ID)]=BIT(2)|(2<<4)|(4U<<24);
 assert(!msm_spm_drv_init(&second,&data));
 assert(second.seq_entries==4 && second.num_pmic_data==2 && d.num_pmic_data==7);
 before=reads;msm_spm_drv_reinit(&second,false);i=reads-before;
 before=reads;msm_spm_drv_reinit(&d,false);assert(reads-before==i+5);
 kfree(second.reg_seq_entry_shadow);finish_driver();
 reset(0x30000000);data.reg_size=0x410;before=allocations;
 assert(msm_spm_drv_init(&d,&data)==-EINVAL && !d.reg_seq_entry_shadow && allocations==before && !writes);
 reset(0x30000000);before=reads;d.vctl_port=8;
 assert(msm_spm_drv_set_vdd(&d,160)==-EINVAL && reads==before && !writes);
 d.vctl_port=0;d.vctl_port_ub=-2;
 assert(msm_spm_drv_set_vdd(&d,160)==-EINVAL && reads==before && !writes);
 d.vctl_port_ub=-1;d.vctl_timeout_us=0;
 assert(msm_spm_drv_set_vdd(&d,160)==-EINVAL && reads==before && !writes);
 assert(msm_spm_drv_set_pmic_data(&d,MSM_SPM_PMIC_PHASE_PORT,1)==-EINVAL && !writes);
 d.vctl_timeout_us=3;d.phase_port=8;
 assert(msm_spm_drv_set_pmic_data(&d,MSM_SPM_PMIC_PHASE_PORT,1)==-ENODEV && !writes);
 d.phase_port=1;hwregs[regoff(MSM_SPM_REG_SAW_PMIC_STS)]=0x10000;
 assert(msm_spm_drv_set_pmic_data(&d,MSM_SPM_PMIC_PHASE_PORT,1)==-EIO && delays==3);
 reset(0x30000000);complete_pmic=true;
 assert(!msm_spm_drv_set_vdd(&d,180) && msm_spm_drv_get_vdd(&d)==180);
 reset(0x30000000);hwregs[regoff(MSM_SPM_REG_SAW_PMIC_STS)]=0x10000;
 assert(msm_spm_drv_set_vdd(&d,180)==-EIO && delays==3);
 /* The sequence fits advertised RAM, but a third start cannot wrap 9 bits. */
 reset(0x30000000);memset(&device,0,sizeof(device));device.reg_data=d;
 memset(modes,0,sizeof(modes));memset(long_cmd,0x11,sizeof(long_cmd));long_cmd[256]=0x0f;
 modes[0].cmd=modes[1].cmd=long_cmd;modes[0].cmd_len=modes[1].cmd_len=sizeof(long_cmd);
 modes[2].cmd=&short_cmd;modes[2].cmd_len=1;data.modes=modes;data.num_modes=3;
 assert(msm_spm_dev_init(&device,&data)==-E2BIG && !writes && !device.initialized);
 assert(!device.modes && !device.num_modes && !device.reg_data.reg_seq_entry_shadow && !device.reg_data.seq_entries);
 data.num_modes=1;assert(!msm_spm_dev_init(&device,&data) && device.initialized);
 assert(device.reg_data.seq_entries==255 && writes==255);
 msm_spm_free_program(&device);assert(!device.modes && !device.reg_data.reg_seq_entry_shadow);
 assert(allocations==frees);
 puts("PASS: actual SAW MMIO: version maps, live AVS/IRQ readback, lost writes, PMIC idle/timeout, per-device counts, mapped RAM bounds, start-address overflow and program cleanup");
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='dora-spm-transactions-') as tmp:
    for name, contents in [('regulator', preamble + production + tests),
                           ('saw', api_preamble + api_production + api_tests),
                           ('registers', register_preamble + register_types +
                            register_production + register_tests)]:
        c, binary = Path(tmp) / (name + '.c'), Path(tmp) / name
        c.write_text(contents)
        flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('SPM_SANITIZE') else []
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                       ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
                        *flags, str(c), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)
