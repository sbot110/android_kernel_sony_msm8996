#!/usr/bin/env python3
"""Execute production APSS watchdog setup/PM/worker/teardown with fault injection.

Only kernel API boundaries and MMIO storage are modeled. Host results do not
qualify bootloader ownership, IRQ delivery, oscillator rate or physical timing.
Dora activation remains gated. --self-test rejects selected dangerous mutations.
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
DRIVER = ROOT / 'drivers/soc/qcom/watchdog_v2.c'


def block(source, pattern):
    match = re.search(pattern, source, re.M | re.S)
    assert match, pattern
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def function(source, name):
    return block(source, r'^static (?:__ref )?(?:int|void) ' + name + r'\([^;]*?\)\s*\{')


PREAMBLE = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sched.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef uintptr_t phys_addr_t;
typedef uintptr_t dma_addr_t;
typedef unsigned cpumask_t;
typedef atomic_int atomic_t;
#define __iomem
#define __percpu
#define __ref
#define NR_CPUS 2
#define NR_IPI 4
#define NR_TOP_HITTERS 10
#define MAX_RT_PRIO 100
#define BIT(n) (1U<<(n))
#define WDT0_RST 4
#define WDT0_EN 8
#define WDT0_STS 12
#define WDT0_BARK_TIME 16
#define WDT0_BITE_TIME 20
#define WDOG_ABSENT 0
#define EN 0
#define UNMASKED_INT_EN 1
#define NSEC_PER_MSEC 1000000ULL
#define EPROBE_DEFER 517
#define GFP_KERNEL 0
#define IORESOURCE_MEM 0
#define MINIDUMP_DEFAULT_ID 0
#define IRQF_TRIGGER_RISING 0
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define READ_ONCE(x) __atomic_load_n(&(x),__ATOMIC_ACQUIRE)
#define WRITE_ONCE(x,v) __atomic_store_n(&(x),(v),__ATOMIC_RELEASE)
#define U32_MAX UINT32_MAX
#define min(a,b) ((a)<(b)?(a):(b))
#define div_u64(a,b) ((u64)(a)/(b))
#define mb() atomic_thread_fence(memory_order_seq_cst)
#define dev_info(d,...) ((void)(d))
#define dev_warn(d,...) ((void)(d))
#define dev_err(d,...) ((void)(d))
#define pr_info(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define from_timer(v,p,m) container_of(p,struct msm_watchdog_data,m)
#define for_each_possible_cpu(cpu) for((cpu)=0;(cpu)<NR_CPUS;(cpu)++)
#define for_each_cpu(cpu,mask) for((cpu)=0;(cpu)<NR_CPUS;(cpu)++)
#define per_cpu_ptr(ptr,cpu) ((ptr)+(cpu))
#define alloc_percpu(type) ((type *)test_alloc_percpu())
#define rcu_access_pointer(p) (p)
#define rcu_assign_pointer(p,v) ((p)=(v))
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
struct mutex { pthread_mutex_t p; };
typedef struct mutex raw_spinlock_t;
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->p,NULL)); }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->p)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->p)); }
#define raw_spin_lock_init(m) mutex_init(m)
#define raw_spin_lock_irqsave(m,f) do {(f)=0;mutex_lock(m);}while(0)
#define raw_spin_unlock_irqrestore(m,f) do {(void)(f);mutex_unlock(m);}while(0)
struct device_node { u32 bark,pet; bool userspace,ipi,wakeup; int bark_error,pet_error; };
struct device { void *data;struct device_node *of_node; };
struct resource { uintptr_t start;size_t size;bool present; };
struct platform_device { struct device dev;struct resource res[2];int irqs[2]; };
struct device_attribute { unsigned bit; };
struct notifier_block { int (*notifier_call)(struct notifier_block *,unsigned long,void *); };
struct md_region { char name[32];uintptr_t virt_addr,phys_addr;size_t size;int id; };
struct work_struct { bool initialized; };
struct delayed_work { struct work_struct work;bool initialized;unsigned long delay; };
struct timer_list { bool initialized;atomic_bool pending;void (*fn)(struct timer_list *); };
typedef struct { pthread_mutex_t lock;pthread_cond_t cond;bool initialized; } wait_queue_head_t;
struct task_struct { pthread_t id;int (*fn)(void *);void *arg;atomic_bool stop;bool started;unsigned refs; };
static _Thread_local struct task_struct *current;
static int enable=1,ipi_en;
static long WDT_HZ=32765;
static struct msm_watchdog_data *wdog_data;
static DEFINE_MUTEX(wdog_probe_lock);
static struct notifier_block wdog_cpu_pm_nb,panic_notifier_list;
static struct device_attribute dev_attr_disable={1},dev_attr_pet_time={2},dev_attr_user_pet_enabled={4};
static const void *system_unbound_wq,*cpu_present_mask;
static unsigned long jiffies;
static atomic_uint queued_work,wait_calls;
static void __raw_writel(u32 v,void *p);
static u32 __raw_readl(void *p);
static u64 sched_clock(void);
static struct task_struct *kthread_create(int (*fn)(void *),void *arg,const char *name);
static void get_task_struct(struct task_struct *t);
static void put_task_struct(struct task_struct *t);
static void wake_up_process(struct task_struct *t);
static void kthread_stop(struct task_struct *t);
static bool kthread_should_stop(void) { return current && atomic_load(&current->stop); }
#define sched_setscheduler(t,p,s) ((void)(t),(void)(p),(void)(s))
static void init_waitqueue_head(wait_queue_head_t *q)
{ assert(!pthread_mutex_init(&q->lock,NULL));assert(!pthread_cond_init(&q->cond,NULL));q->initialized=true; }
static void wake_up(wait_queue_head_t *q)
{ assert(q->initialized);assert(!pthread_mutex_lock(&q->lock));assert(!pthread_cond_broadcast(&q->cond));assert(!pthread_mutex_unlock(&q->lock)); }
/* A timeout aborts a broken worker instead of hiding a stop/wakeup deadlock. */
#define wait_event_interruptible(q,condition) ({int e=0;struct timespec until;atomic_fetch_add(&wait_calls,1);clock_gettime(CLOCK_REALTIME,&until);until.tv_sec+=2; \
 pthread_mutex_lock(&(q).lock);while(!(condition)){e=pthread_cond_timedwait(&(q).cond,&(q).lock,&until);assert(!e);} \
 pthread_mutex_unlock(&(q).lock);0;})
#define INIT_WORK(w,fn) ((w)->initialized=true)
static void atomic_set(atomic_t *v,int n) { atomic_store(v,n); }
static void timer_setup(struct timer_list *t,void (*fn)(struct timer_list *),int flags)
{ assert(!flags);t->initialized=true;t->fn=fn;atomic_init(&t->pending,false); }
static unsigned long msecs_to_jiffies(unsigned long ms) { return ms; }
static void mod_timer(struct timer_list *t,unsigned long expires);
static void del_timer_sync(struct timer_list *t) { assert(t->initialized);atomic_store(&t->pending,false); }
static void queue_work(const void *q,struct work_struct *w) { (void)q;assert(w->initialized);atomic_fetch_add(&queued_work,1); }
static void cancel_work_sync(struct work_struct *w) { assert(w->initialized); }
static void compute_irq_stat(struct work_struct *w) { (void)w; }
static void ping_other_cpus(struct msm_watchdog_data *w) { (void)w; }
static void cpumask_clear(cpumask_t *m) { *m=0; }
static void *test_alloc_percpu(void);
static void free_percpu(void *p);
static int request_percpu_irq(int irq,void *fn,const char *name,void *data);
static int devm_request_irq(struct device *dev,int irq,void *fn,int flags,const char *name,void *data);
static void free_percpu_irq(int irq,void *data);
static void devm_free_irq(struct device *dev,int irq,void *data);
static void enable_percpu_irq(int irq,int type);
static void disable_percpu_irq(int irq);
static void on_each_cpu(void (*fn)(void *),void *data,int wait);
static int atomic_notifier_chain_register(void *chain,void *nb);
static void atomic_notifier_chain_unregister(void *chain,void *nb);
static int cpu_pm_register_notifier(void *nb);
static void cpu_pm_unregister_notifier(void *nb);
static int device_create_file(struct device *d,struct device_attribute *a);
static void device_remove_file(struct device *d,struct device_attribute *a);
static bool of_property_read_bool(struct device_node *n,const char *name);
static int of_property_read_u32(struct device_node *n,const char *name,u32 *v);
static int platform_get_irq(struct platform_device *p,int i) { return p->irqs[i]; }
static bool irq_is_percpu(int irq);
static struct resource *platform_get_resource_byname(struct platform_device *p,int type,const char *name);
static size_t resource_size(struct resource *r) { return r->size; }
static void *devm_ioremap_resource(struct device *d,struct resource *r);
static void *kzalloc(size_t n,int flags);
static void kfree(void *p);
static void *dev_get_drvdata(struct device *d) { return d->data; }
static void platform_set_drvdata(struct platform_device *p,void *v) { p->dev.data=v; }
static void *platform_get_drvdata(struct platform_device *p) { return p->dev.data; }
static void dump_pdata(struct msm_watchdog_data *d) { (void)d; }
static void log_buf_init(struct device *d);
static void log_buf_remove(void);
static void synchronize_rcu(void);
#define strlcpy test_strlcpy
static size_t test_strlcpy(char *d,const char *s,size_t n) { size_t len=strlen(s);snprintf(d,n,"%s",s);return len; }
static uintptr_t virt_to_phys(void *p) { return (uintptr_t)p; }
static int msm_minidump_add_region(struct md_region *r);
static int msm_minidump_remove_region(struct md_region *r);
static void get_device(struct device *d);
static void put_device(struct device *d);
static int panic_wdog_handler(struct notifier_block *a,unsigned long b,void *c) { (void)a;(void)b;(void)c;return 0; }
static void wdog_bark_handler(void) { }
static void wdog_ppi_bark(void) { }
'''

BOUNDARIES = r'''
static const char *fault;
static int fault_errno=-EIO;
static unsigned irq_alloc,percpu_alloc,panic_alloc,pm_alloc,attrs,objects,tasks,md_alloc;
static unsigned reads,rearms,ppi_enabled,ppi_disables,map_calls,cases;
static atomic_uint hardware_writes;
static u32 mmio[16],absent[4];
static bool ppi,changing_counter,md_remove_fail;
static atomic_uint rcu_readers;
static atomic_bool rcu_waiting;
static struct device_node node;
static struct platform_device platform;
/* Optional initial-log APIs: delayed work is explicitly advanced by tests. */
#define LOGBUF_TIMEOUT 100000U
static struct delayed_work log_buf_work;
static char *init_log_buf;
static unsigned int *log_buf_size;
static dma_addr_t log_buf_paddr;
static struct device *log_buf_dev;
static struct md_region log_buf_region;
static bool log_buf_registered;
static unsigned dma_buffers,device_pins;
static bool log_symbols_missing;
static unsigned log_length=64;
static char log_source[64],*log_pointer=log_source;
static u64 clock_ns=200000000000ULL;
#define INIT_DELAYED_WORK(w,fn) do {(w)->initialized=true;(w)->work.initialized=true;}while(0)
static void queue_delayed_work(const void *q,struct delayed_work *w,unsigned long delay)
{ (void)q;assert(w->initialized);w->delay=delay; }
static void cancel_delayed_work_sync(struct delayed_work *w) { assert(w->initialized); }
static uintptr_t kallsyms_lookup_name(const char *name)
{ if(log_symbols_missing)return 0;return !strcmp(name,"log_buf")?(uintptr_t)&log_pointer:(uintptr_t)&log_length; }
static void *dma_alloc_coherent(struct device *d,size_t size,dma_addr_t *p,int flags)
{ void *v;(void)flags;assert(d==&platform.dev&&size==64);if(fault&&!strcmp(fault,"logalloc"))return NULL;v=calloc(1,size);assert(v);*p=(uintptr_t)v;dma_buffers++;return v; }
static void dma_free_coherent(struct device *d,size_t size,void *v,dma_addr_t p)
{ assert(d==&platform.dev&&size==64&&(uintptr_t)v==p&&dma_buffers==1);dma_buffers--;free(v); }
static bool failing(const char *name) { return fault && !strcmp(fault,name); }
static u64 sched_clock(void) { return clock_ns; }
static void __raw_writel(u32 v,void *p)
{ u32 *r=p;assert((r>=mmio&&r<mmio+16)||(r>=absent&&r<absent+4));atomic_fetch_add(&hardware_writes,1);*r=v; }
static u32 __raw_readl(void *p)
{ u32 *r=p;assert(r>=mmio&&r<mmio+16);reads++;return changing_counter?(reads<<1):*r; }
static void mod_timer(struct timer_list *t,unsigned long expires)
{ (void)expires;assert(t->initialized);atomic_store(&t->pending,true);rearms++; }
static void *task_start(void *data)
{ struct task_struct *t=data;current=t;t->fn(t->arg);return NULL; }
static struct task_struct *kthread_create(int (*fn)(void *),void *arg,const char *name)
{ struct task_struct *t;(void)name;if(failing("thread"))return ERR_PTR(fault_errno);t=calloc(1,sizeof(*t));assert(t);t->fn=fn;t->arg=arg;t->refs=1;tasks++;return t; }
static void get_task_struct(struct task_struct *t) { assert(t->refs==1);t->refs++; }
static void put_task_struct(struct task_struct *t) { assert(t->refs==1);tasks--;free(t); }
static void wake_up_process(struct task_struct *t)
{ struct msm_watchdog_data *d=t->arg;assert(d->pet_timer.initialized && d->pet_complete.initialized && d->irq_counts_work.initialized);assert(!t->started);t->started=true;assert(!pthread_create(&t->id,NULL,task_start,t)); }
static void kthread_stop(struct task_struct *t)
{ struct msm_watchdog_data *d=t->arg;assert(t->refs==2);atomic_store(&t->stop,true);wake_up(&d->pet_complete);if(t->started)assert(!pthread_join(t->id,NULL));t->refs--; }
static void *test_alloc_percpu(void)
{ if(failing("percpu"))return NULL;percpu_alloc++;return calloc(NR_CPUS,sizeof(struct msm_watchdog_data *)); }
static void free_percpu(void *p) { assert(percpu_alloc==1);percpu_alloc--;free(p); }
static int request_percpu_irq(int irq,void *fn,const char *name,void *data)
{ struct msm_watchdog_data **p=data;int i;(void)fn;(void)name;assert(irq==28);for(i=0;i<NR_CPUS;i++)assert(p[i]==platform.dev.data);if(failing("irq"))return fault_errno;irq_alloc++;return 0; }
static int devm_request_irq(struct device *d,int irq,void *fn,int flags,const char *name,void *data)
{ (void)fn;(void)flags;(void)name;assert(d==&platform.dev && irq==28 && data==d->data);if(failing("irq"))return fault_errno;irq_alloc++;return 0; }
static void free_percpu_irq(int irq,void *data) { assert(irq==28 && data && irq_alloc==1);irq_alloc--; }
static void devm_free_irq(struct device *d,int irq,void *data) { assert(d==&platform.dev&&irq==28&&data==d->data&&irq_alloc==1);irq_alloc--; }
static void enable_percpu_irq(int irq,int type) { assert(irq==28&&!type);ppi_enabled++; }
static void disable_percpu_irq(int irq) { assert(irq==28);ppi_disables++; }
static void on_each_cpu(void (*fn)(void *),void *data,int wait) { int i;assert(wait);for(i=0;i<NR_CPUS;i++)fn(data); }
static int atomic_notifier_chain_register(void *chain,void *nb) { (void)chain;(void)nb;if(failing("panic"))return fault_errno;panic_alloc++;return 0; }
static void atomic_notifier_chain_unregister(void *chain,void *nb) { (void)chain;(void)nb;assert(panic_alloc==1);panic_alloc--; }
static int cpu_pm_register_notifier(void *nb) { (void)nb;if(failing("cpu_pm"))return fault_errno;pm_alloc++;return 0; }
static void cpu_pm_unregister_notifier(void *nb) { (void)nb;assert(pm_alloc==1);pm_alloc--; }
static int device_create_file(struct device *d,struct device_attribute *a)
{ const char *name=a->bit==1?"disable":a->bit==2?"pet_time":"user_pet";assert(d==&platform.dev&&!(attrs&a->bit));if(failing(name))return fault_errno;attrs|=a->bit;return 0; }
static void device_remove_file(struct device *d,struct device_attribute *a) { assert(d==&platform.dev&&(attrs&a->bit));attrs&=~a->bit; }
static bool of_property_read_bool(struct device_node *n,const char *name)
{ if(!strcmp(name,"qcom,userspace-watchdog"))return n->userspace;if(!strcmp(name,"qcom,ipi-ping"))return n->ipi;assert(!strcmp(name,"qcom,wakeup-enable"));return n->wakeup; }
static int of_property_read_u32(struct device_node *n,const char *name,u32 *v)
{ bool bark=!strcmp(name,"qcom,bark-time");int e=bark?n->bark_error:n->pet_error;if(e)return e;*v=bark?n->bark:n->pet;return 0; }
static bool irq_is_percpu(int irq) { assert(irq==28);return ppi; }
static struct resource *platform_get_resource_byname(struct platform_device *p,int type,const char *name)
{ unsigned i=!strcmp(name,"wdt-base")?0:1;(void)type;return p->res[i].present?&p->res[i]:NULL; }
static void *devm_ioremap_resource(struct device *d,struct resource *r)
{ bool optional=r==&platform.res[1];assert(d==&platform.dev);map_calls++;if(failing(optional?"map1":"map0"))return ERR_PTR(fault_errno);return optional?(void *)absent:(void *)mmio; }
static void *kzalloc(size_t n,int flags) { (void)flags;if(failing("kzalloc"))return NULL;objects++;return calloc(1,n); }
static void kfree(void *p)
{ struct msm_watchdog_data *d=p;assert(objects==1);objects--;if(d->pet_complete.initialized){pthread_mutex_destroy(&d->pet_complete.lock);pthread_cond_destroy(&d->pet_complete.cond);pthread_mutex_destroy(&d->hw_lock.p);pthread_mutex_destroy(&d->disable_lock.p);}free(p); }
static void synchronize_rcu(void) { atomic_store(&rcu_waiting,true);while(atomic_load(&rcu_readers))sched_yield(); }
static int msm_minidump_add_region(struct md_region *r) { assert(r->size==sizeof(struct msm_watchdog_data)||r->size==64);if(failing(r->size==64?"logminidump":"minidump"))return fault_errno;md_alloc++;return 0; }
static int msm_minidump_remove_region(struct md_region *r) { (void)r;assert(md_alloc>0);if(md_remove_fail)return -EIO;md_alloc--;return 0; }
static void get_device(struct device *d) { assert(d==&platform.dev);device_pins++; }
static void put_device(struct device *d) { assert(d==&platform.dev&&device_pins);device_pins--; }
'''

TESTS = r'''
static void setup(bool percpu)
{
 assert(!objects&&!tasks&&!irq_alloc&&!percpu_alloc&&!panic_alloc&&!pm_alloc&&!attrs&&!md_alloc&&!wdog_data&&!dma_buffers&&!device_pins);
 memset(&platform,0,sizeof(platform));memset(&node,0,sizeof(node));memset(mmio,0,sizeof(mmio));memset(absent,0,sizeof(absent));
 node.bark=12000;node.pet=10000;node.userspace=true;node.wakeup=true;platform.dev.of_node=&node;
 platform.irqs[0]=28;platform.irqs[1]=29;platform.res[0]=(struct resource){0x9830000,0x1000,true};
 platform.res[1]=(struct resource){0x1234,4,true}; /* synthetic optional resource, never added to Dora DT */
 fault=NULL;fault_errno=-EIO;ppi=percpu;changing_counter=false;md_remove_fail=false;
 log_symbols_missing=false;clock_ns=200000000000ULL;
 reads=rearms=ppi_enabled=ppi_disables=map_calls=0;atomic_store(&hardware_writes,0);atomic_store(&queued_work,0);atomic_store(&wait_calls,0);atomic_store(&rcu_waiting,false);
 enable=1;ipi_en=0;WDT_HZ=32765;cases++;
}
static void balanced(void)
{ assert(!objects&&!tasks&&!irq_alloc&&!percpu_alloc&&!panic_alloc&&!pm_alloc&&!attrs&&!md_alloc&&!wdog_data&&!platform.dev.data&&!dma_buffers&&!device_pins); }
static void failed(int error)
{ assert(msm_watchdog_probe(&platform)==error);assert(!atomic_load(&hardware_writes));balanced(); }
static void *pm_worker(void *data)
{ unsigned i;(void)data;for(i=0;i<4000;i++){msm_watchdog_suspend(&platform.dev);msm_watchdog_resume(&platform.dev);}return NULL; }
static void *remove_worker(void *data) { (void)data;assert(!msm_watchdog_remove(&platform));return NULL; }
int main(void)
{
 unsigned mode,i,before;pthread_t pm,remove;struct msm_watchdog_data *d;
 static const char *failures[]={"thread","irq","panic","cpu_pm","disable","pet_time","user_pet","map0","map1","kzalloc"};
 for(mode=0;mode<2;mode++) {
  for(i=0;i<sizeof(failures)/sizeof(failures[0]);i++){setup(mode);fault=failures[i];fault_errno=-EPROBE_DEFER;failed(!strcmp(fault,"kzalloc")?-ENOMEM:-EPROBE_DEFER);}
  setup(mode);platform.irqs[0]=-EPROBE_DEFER;failed(-EPROBE_DEFER);assert(!map_calls);
  setup(mode);platform.irqs[1]=-EIO;failed(-EIO);assert(!map_calls);
  setup(mode);platform.irqs[0]=0;failed(-EINVAL);
  setup(mode);platform.res[0].present=false;failed(-EINVAL);
  setup(mode);platform.res[0].size=20;failed(-EINVAL);
  setup(mode);platform.res[1].size=3;failed(-EINVAL);
  setup(mode);node.bark_error=-ENODATA;failed(-ENODATA);
  setup(mode);node.pet_error=-ENODATA;failed(-ENODATA);
  setup(mode);node.pet=0;failed(-EINVAL);
  setup(mode);node.pet=node.bark;failed(-EINVAL);
  setup(mode);WDT_HZ=0;failed(-EINVAL);
  setup(mode);WDT_HZ=-1;failed(-EINVAL);
  setup(mode);WDT_HZ=LONG_MAX;failed(-EINVAL);
  setup(mode);node.bark=UINT_MAX;failed(-ERANGE);
  setup(mode);node.pet=1;node.bark=2;WDT_HZ=1;failed(-ERANGE);
  setup(mode);WDT_HZ=UINT_MAX;node.bark=2;node.pet=1;failed(-ERANGE);
  if(mode){setup(true);fault="percpu";failed(-ENOMEM);}
  setup(mode);assert(!msm_watchdog_probe(&platform));d=platform.dev.data;
  assert(d&&wdog_data==d&&d->ready&&d->enabled&&attrs==7&&irq_alloc==1&&panic_alloc==1&&pm_alloc==1);
  assert(mmio[WDT0_BARK_TIME/4]==393180&&mmio[WDT0_BITE_TIME/4]==491475&&mmio[WDT0_EN/4]==3);
  assert(msm_watchdog_probe(&platform)==-EBUSY);assert(objects==1);
  assert(!msm_watchdog_suspend(&platform.dev));before=atomic_load(&hardware_writes);pet_watchdog(d);assert(atomic_load(&hardware_writes)==before);
  assert(!msm_watchdog_resume(&platform.dev));assert(d->enabled&&!d->suspended);
  changing_counter=true;reads=0;pet_watchdog(d);assert(reads==8);changing_counter=false;
  mmio[WDT0_STS/4]=UINT_MAX;pet_watchdog(d);assert(!d->min_slack_ticks);
  assert(!pthread_create(&pm,NULL,pm_worker,NULL));
  mutex_lock(&d->disable_lock);wdog_disable(d);mutex_unlock(&d->disable_lock);
  assert(!pthread_join(pm,NULL));before=atomic_load(&hardware_writes);msm_watchdog_resume(&platform.dev);pet_task_wakeup(&d->pet_timer);pet_watchdog(d);
  assert(atomic_load(&hardware_writes)==before&&!d->enabled&&!atomic_load(&d->pet_timer.pending));
  assert(!msm_watchdog_remove(&platform));if(mode)assert(ppi_enabled==1&&ppi_disables==NR_CPUS);balanced();
  /* Removal waits for RCU readers and stops the active sleeping worker. */
  setup(mode);node.wakeup=false;node.userspace=false;assert(!msm_watchdog_probe(&platform));d=platform.dev.data;
  assert(attrs==1);assert(!msm_watchdog_suspend(&platform.dev));assert(!d->enabled&&!mmio[WDT0_EN/4]);
  assert(!msm_watchdog_resume(&platform.dev));assert(d->enabled&&mmio[WDT0_EN/4]==1);
  atomic_store(&rcu_readers,1);assert(!pthread_create(&remove,NULL,remove_worker,NULL));
  for(i=0;!atomic_load(&rcu_waiting)&&i<1000000;i++)sched_yield();assert(atomic_load(&rcu_waiting));assert(objects==1&&tasks==1);
  atomic_store(&rcu_readers,0);assert(!pthread_join(remove,NULL));balanced();
  setup(mode);ipi_en=1;fault="minidump";assert(!msm_watchdog_probe(&platform));assert(!pm_alloc&&!md_alloc);assert(!msm_watchdog_remove(&platform));balanced();
  /* Execute a real worker pet/rearm transaction after the timer callback. */
  setup(mode);assert(!msm_watchdog_probe(&platform));d=platform.dev.data;
  pet_task_wakeup(&d->pet_timer);
  for(i=0;!atomic_load(&queued_work)&&i<1000000;i++)sched_yield();assert(atomic_load(&queued_work)==1);
  assert(!msm_watchdog_remove(&platform));balanced();
  /* Stop while the actual worker waits for the userspace pet. */
  setup(mode);assert(!msm_watchdog_probe(&platform));d=platform.dev.data;
  WRITE_ONCE(d->user_pet_enabled,true);WRITE_ONCE(d->user_pet_complete,false);pet_task_wakeup(&d->pet_timer);
  for(i=0;atomic_load(&wait_calls)<2&&i<1000000;i++)sched_yield();assert(atomic_load(&wait_calls)>=2);
  assert(!msm_watchdog_remove(&platform));assert(!atomic_load(&queued_work));balanced();
 }
 /* Missing symbols, failed DMA and failed optional minidump are all removable. */
 setup(false);assert(!msm_watchdog_probe(&platform));assert(!log_buf_work.delay);log_symbols_missing=true;log_buf_work_fn(&log_buf_work.work);assert(!init_log_buf);assert(!msm_watchdog_remove(&platform));balanced();
 setup(false);clock_ns=1000000000ULL;assert(!msm_watchdog_probe(&platform));assert(log_buf_work.delay==99000);fault="logalloc";log_buf_work_fn(&log_buf_work.work);assert(!init_log_buf);assert(!msm_watchdog_remove(&platform));balanced();
 setup(false);assert(!msm_watchdog_probe(&platform));fault="logminidump";log_buf_work_fn(&log_buf_work.work);assert(init_log_buf&&!log_buf_registered&&device_pins==1);assert(!msm_watchdog_remove(&platform));balanced();
 setup(false);assert(!msm_watchdog_probe(&platform));log_buf_work_fn(&log_buf_work.work);assert(init_log_buf&&log_buf_registered&&md_alloc==2);assert(!msm_watchdog_remove(&platform));balanced();
 /* An unretired log minidump retains both its DMA buffer and device. */
 setup(false);assert(!msm_watchdog_probe(&platform));log_buf_work_fn(&log_buf_work.work);md_remove_fail=true;log_buf_remove();assert(init_log_buf&&dma_buffers==1&&device_pins==1&&md_alloc==2);md_remove_fail=false;assert(!msm_watchdog_remove(&platform));balanced();
 /* Firmware retirement failure must retain registered backing memory. */
 setup(false);assert(!msm_watchdog_probe(&platform));d=platform.dev.data;md_remove_fail=true;assert(!msm_watchdog_remove(&platform));
 assert(objects==1&&md_alloc==1&&!wdog_data&&!tasks&&!irq_alloc&&!attrs&&device_pins==1);md_remove_fail=false;assert(!msm_minidump_remove_region(&d->minidump));put_device(d->dev);kfree(d);balanced();
 printf("{\"cases\":%u,\"hardware_verified\":false}\n",cases);return 0;
}
'''

FUNCTIONS = ['msm_watchdog_suspend', 'msm_watchdog_resume', 'wdog_disable',
             'pet_watchdog', 'pet_task_wakeup', 'watchdog_kthread',
             'remove_watchdog_sysfs', 'init_watchdog_sysfs',
             'wdog_disable_percpu_irq', 'release_watchdog_irq',
             'release_watchdog_notifiers', 'init_watchdog_data',
             'msm_wdog_dt_to_pdata', 'msm_watchdog_probe', 'msm_watchdog_remove']
LOG_FUNCTIONS = ['log_buf_remove', 'log_buf_work_fn', 'log_buf_init']


def build_source(source):
    structs = block(source, r'^struct irq_info \{') + ';\n'
    structs += block(source, r'^struct msm_watchdog_data \{') + ';\n'
    log_functions = '\n'.join(function(source, name) for name in LOG_FUNCTIONS)
    functions = '\n'.join(function(source, name) for name in FUNCTIONS)
    return PREAMBLE + structs + BOUNDARIES + log_functions + functions + TESTS


def execute(source, directory, label):
    c = directory / (label + '.c')
    binary = directory / label
    c.write_text(build_source(source))
    cc = shlex.split(os.environ.get('HOSTCC', 'cc'))
    sanitize = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('WATCHDOG_TEST_SANITIZE') == '1' else []
    result = subprocess.run(cc + sanitize + ['-std=gnu11', '-O1', '-g', '-pthread', '-Wall',
                                 '-Wextra', '-Wno-unused-parameter', '-Wno-unused-function',
                                 '-Wno-unused-variable', '-Wno-unused-value',
                                 '-Wno-misleading-indentation', str(c), '-o', str(binary)],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    source = DRIVER.read_text()
    config = (ROOT / 'arch/arm64/configs/tone_dora_defconfig').read_text()
    assert 'CONFIG_QCOM_WATCHDOG_V2=y' not in config, 'handoff remains unqualified'
    assert 'rcu_read_lock();' in source and 'synchronize_rcu();' in source
    bark = block(source, r'^static irqreturn_t wdog_bark_handler\([^;]*?\)\s*\{')
    assert 'do_div(wdog_dd->last_pet' not in bark
    assert 'msm_watchdog_bite(wdog_dd);' in bark
    with tempfile.TemporaryDirectory(prefix='dora-watchdog-') as tmp:
        directory = Path(tmp)
        result = execute(source, directory, 'production')
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        report['production_functions'] = len(FUNCTIONS) + len(LOG_FUNCTIONS)
        mutations = {
            'irq-error-success': ('if (ret)\n\t\tgoto free_irq;', 'if (0)\n\t\tgoto free_irq;'),
            'false-probe-success': ('ret = init_watchdog_data(wdog_dd);', 'ret = init_watchdog_data(wdog_dd); ret = 0;'),
            'lost-irq-defer': ('return ret ? ret : -EINVAL;', 'return -EINVAL;'),
            'unsafe-resume': ('/* A concurrent disable must never be undone by resume. */\n\tif (!wdog_dd->ready || wdog_dd->stopping || !enable)', 'if (!wdog_dd->ready)'),
            'uninitialized-timer': ('timer_setup(&wdog_dd->pet_timer, pet_task_wakeup, 0);', ';'),
            'lost-minidump-retention': ('if (!retain)\n\t\tkfree(wdog_dd);', 'if (1)\n\t\tkfree(wdog_dd);'),
            'unbounded-counter': ('i < 8', 'i < 1000'),
            'missing-sysfs-unwind': ('device_remove_file(wdog_dd->dev, &dev_attr_disable);\n\twdog_dd->sysfs_created = false;\n\treturn ret;', 'wdog_dd->sysfs_created = false;\n\treturn ret;'),
            'worker-stop-deadlock': ('READ_ONCE(wdog_dd->stopping) || kthread_should_stop());', 'false);'),
            'log-delay-underflow': ('unsigned int timeout = elapsed < LOGBUF_TIMEOUT ? LOGBUF_TIMEOUT - elapsed : 0;', 'unsigned int timeout = LOGBUF_TIMEOUT - elapsed;'),
            'missing-log-allocation-guard': ('if (!init_log_buf)\n\t\treturn;', ';'),
        }
        if args.self_test:
            for label, (old, new) in mutations.items():
                assert old in source, label
                changed = source.replace(old, new, 1)
                try:
                    bad = execute(changed, directory, label)
                    assert bad.returncode != 0, label + ' escaped detection'
                except subprocess.TimeoutExpired:
                    pass
            report['negative_mutations_rejected'] = len(mutations)
        print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
