#!/usr/bin/env python3
"""Run published HMSS state and CPR quarantine production code.

Kernel registry/regulator/APM/IRQ/MMIO boundaries are explicit models. The
sample reports real code readback; it does not prove autonomous CPR/SAW or
firmware exclusion, voltage measurement, or safe writable CPU activation.
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
DRIVER = ROOT / 'drivers/regulator/cpr3-regulator.c'
HEADER = ROOT / 'include/linux/regulator/qcom-cpr3-cpu.h'


def function(source, name):
    match = re.search(r'^(?:static )?(?:inline )?(?:int|bool|void|u32|irqreturn_t)\s+' +
                      re.escape(name) + r'\([^;]*?\)\s*\{', source, re.M | re.S)
    assert match, name
    pos, depth = match.end(), 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos] + '\n'


PREAMBLE = r'''
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
typedef uint32_t u32;
typedef uint64_t u64;
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define U32_MAX UINT32_MAX
#define BIT(n) (1U<<(n))
#define EPROBE_DEFER 517
#define IS_ERR_OR_NULL(p) (!(p)||(uintptr_t)(p)>=(uintptr_t)-4095)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define cpr3_err(...) ((void)0)
#define cpr3_debug(...) ((void)0)
#define rmb() ((void)0)
#define mb() ((void)0)
#define local_irq_save(f) ((f)=0)
#define local_irq_restore(f) ((void)(f))
#define preempt_disable() ((void)0)
#define preempt_enable() ((void)0)
#define CPR3_REG_IRQ_STATUS 0x824
#define CPR3_REG_IRQ_CLEAR 0x820
#define CPR3_REG_CONT_CMD 0x800
#define CPR3_REG_LAST_MEASUREMENT 0x7f8
#define CPR3_REG_IRQ_EN 0x81c
#define CPR3_IRQ_UP BIT(3)
#define CPR3_IRQ_DOWN BIT(1)
#define CPR3_CONT_CMD_NACK 0
#define CPR3_CONT_CMD_ACK 1
#define CPR3_CPR_STATUS_BUSY_MASK BIT(0)
#define CPR3_REGISTER_WRITE_DELAY_US 100
#define MSM_SPM_AVS_IRQ_MAX 1
#define CPR3_CORNER_OFFSET 1
#define CPR3_REGULATOR_CORNER_INVALID (-1)
#define CPR_CTRL_TYPE_CPR3 0
#define CPR_CTRL_TYPE_CPR4 1
#define CPR_CTRL_TYPE_CPRH 2
#define CPR3_REG_CPR_CTL 0x4
#define CPR3_REG_CPR_STATUS 0x8
#define CPR3_REG_HW_CLOSED_LOOP 0x3000
#define CPR3_CPR_CTL_LOOP_EN_MASK BIT(0)
#define CPR3_CPR_CTL_LOOP_ENABLE BIT(0)
#define CPR3_CPR_CTL_LOOP_DISABLE 0
#define CPR3_HW_CLOSED_LOOP_ENABLE 0
#define CPR3_HW_CLOSED_LOOP_DISABLE 1
#define LDO_MODE 0
#define BHS_MODE 1
#define CPR4_REG_MARGIN_ADJ_CTL 0x7f8
#define CPR4_MARGIN_ADJ_CTL_HW_CLOSED_LOOP_EN_MASK BIT(4)
#define CPR4_MARGIN_ADJ_CTL_HW_CLOSED_LOOP_ENABLE BIT(4)
#define CPR4_MARGIN_ADJ_CTL_HW_CLOSED_LOOP_DISABLE 0
#define CPR3_REG_PD_THROTTLE 0xe8
#define CPR3_PD_THROTTLE_DISABLE 0
struct mutex { pthread_mutex_t value; };
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
struct list_head { struct list_head *next,*prev; };
#define LIST_HEAD(n) struct list_head n={&n,&n}
#define INIT_LIST_HEAD(n) ((n)->next=(n)->prev=(n))
static void list_add_tail(struct list_head *n,struct list_head *h)
{ n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n; }
static void list_del_init(struct list_head *n)
{ n->prev->next=n->next;n->next->prev=n->prev;INIT_LIST_HEAD(n); }
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) for(p=container_of((h)->next,__typeof__(*p),m); &p->m!=(h);p=container_of(p->m.next,__typeof__(*p),m))
struct device_node { const char *compatible; };
struct device { struct device_node *of_node;struct device *parent; };
struct regulator_ops { int tag; };
struct regulator_desc { struct regulator_ops *ops; };
struct regulator_dev { struct regulator_desc *desc;struct device dev;void *data; };
struct regulator { struct regulator_dev *rdev;unsigned refs; };
struct cpr4_sdelta { bool allow_boost; };
struct cpr3_corner { int floor_volt,ceiling_volt;struct cpr4_sdelta *sdelta;
 int last_volt;u32 irq_en; };
struct cpr3_controller;
struct cpr3_thread;
struct cpr3_regulator { struct cpr3_thread *thread;struct regulator_dev *rdev;
 struct device_node *of_node;
 int corner_count,current_corner;bool vreg_enabled,ldo_mode_allowed;
 struct regulator *ldo_regulator;int ldo_regulator_bypass; };
struct cpr3_thread { struct cpr3_controller *ctrl;unsigned thread_id;
 int vreg_count;struct cpr3_regulator *vreg; };
struct cpr3_controller { struct list_head list;struct mutex lock;
 struct cpr3_thread *thread;int thread_count;bool cpu_power_domain;
 int ctrl_type,power_error;bool cpr_enabled,cpr_suspended,supports_hw_closed_loop,use_hw_closed_loop;
 bool irq_quarantined,ceiling_irq_quarantined,cpr_allowed_sw,cpr_allowed_hw;
 bool support_ldo300_vreg;int proc_clock_throttle,step_volt;
 void *cpr_ctrl_base,*apm;struct cpr3_corner aggr_corner;
 struct regulator *vdd_regulator,*system_regulator; };
static DEFINE_MUTEX(cpr3_controller_list_mutex);
static LIST_HEAD(cpr3_controller_list);
static struct regulator_ops cpr3_regulator_ops,foreign_ops;
static u32 mmio[0x3004/4],read_counts[0x3004/4];
static unsigned reads,writes,enables,disables,gets,sets,updates,bhs_calls,apm_calls,clock_calls,irq_masks;
static int rail_uv,rail_error,update_error,bhs_error,apm_error;
static int scale_error,clear_irq_error;
static unsigned scales,clear_irqs,continuations;
static unsigned fail_step,step;
static void *unstable_address;
static bool unmapped,drift_after_voltage;
static u32 readl_relaxed(const void *address)
{ unsigned i=((const u32 *)address-mmio);assert(!unmapped && i<sizeof(mmio)/sizeof(mmio[0]));
 reads++;read_counts[i]++;return *(const u32 *)address ^ (address==unstable_address?(read_counts[i]&1):0); }
static void writel_relaxed(u32 value,void *address)
{ assert(!unmapped);writes++;if(address==mmio+CPR3_REG_CONT_CMD/4)continuations++;*(u32 *)address=value; }
static int of_device_is_compatible(struct device_node *n,const char *s)
{ return n && !strcmp(n->compatible,s); }
static void *rdev_get_drvdata(struct regulator_dev *r) { return r->data; }
static int regulator_enable(struct regulator *r)
{ enables++;r->refs++;return ++step==fail_step?-EIO:0; }
static int regulator_disable(struct regulator *r)
{ disables++;if(r->refs)r->refs--;return ++step==fail_step?-EIO:0; }
static int regulator_get_voltage(struct regulator *r)
{ (void)r;gets++;if(drift_after_voltage)mmio[1]^=1;return rail_error?rail_error:rail_uv; }
static int regulator_set_voltage(struct regulator *r,int lo,int hi)
{ (void)r;assert(lo<=hi);sets++;return ++step==fail_step?-EIO:0; }
static int cpr3_regulator_update_ctrl_state(struct cpr3_controller *c) { (void)c;updates++;return update_error; }
static int cpr3_regulator_set_bhs_mode(struct cpr3_regulator *v,int uv,int ceiling)
{ assert(uv<=ceiling);bhs_calls++;if(!bhs_error)v->ldo_regulator_bypass=BHS_MODE;return bhs_error; }
static int msm_apm_get_supply(void *p) { assert(p);apm_calls++;return apm_error; }
static int cpr3_clock_enable(struct cpr3_controller *c) { (void)c;clock_calls++;return 0; }
static void cpr3_clock_disable(struct cpr3_controller *c) { (void)c;clock_calls++; }
static int cpr3_ctrl_clear_cpr4_config(struct cpr3_controller *c) { (void)c;return 0; }
static void disable_irq_nosync(int irq) { assert(irq==30 || irq==31);irq_masks++; }
static void udelay(unsigned us) { (void)us; }
static int cpr3_regulator_get_dynamic_floor_volt(struct cpr3_controller *c,u32 raw)
{ (void)c;(void)raw;return 0; }
static bool cpr3_thread_busy(struct cpr3_thread *t) { (void)t;return false; }
static void cpr3_print_result(struct cpr3_thread *t) { (void)t; }
static int cpr3_regulator_scale_vdd_voltage(struct cpr3_controller *c,int uv,int old,struct cpr3_corner *a)
{ (void)c;(void)uv;(void)old;(void)a;scales++;return scale_error; }
static void cpr3_update_vreg_closed_loop_volt(struct cpr3_regulator *v,int uv,u32 raw)
{ (void)v;(void)uv;(void)raw; }
static int msm_spm_avs_clear_irq(unsigned cpu,unsigned irq)
{ assert(cpu==0 && irq==MSM_SPM_AVS_IRQ_MAX);clear_irqs++;return clear_irq_error; }
'''

FUNCTIONS = [
    'cpr3_read', 'cpr3_write', 'cpr3_masked_write', 'cpr3_ctrl_loop_enable',
    'cpr3_ctrl_loop_disable', 'cpr3_power_fault', 'cpr3_apm_ready',
    'cpr3_closed_loop_disable', 'cpr3_regulator_suspend', 'cpr3_regulator_resume',
    'cpr3_regulator_set_voltage', 'cpr3_regulator_get_voltage',
    'cpr3_regulator_is_enabled', 'cpr3_regulator_enable', 'cpr3_regulator_disable',
    'cpr3_cpu_hmss_node', 'cpr3_cpu_copy_state', 'qcom_cpr3_cpu_get_state',
    'qcom_cpr3_cpu_get_state_by_node', 'cpr3_irq_blocked',
    'cpr3_irq_handler', 'cpr3_ceiling_irq_handler', 'cpr3_debug_ldo_mode_allowed_set',
    'cpr3_debug_closed_loop_enable_set',
]

TESTS = r'''
static struct cpr3_controller ctrl;
static struct cpr3_thread thread;
static struct cpr3_regulator vreg;
static struct device_node node={"qcom,cpr3-msm8996-v3-hmss-regulator"};
static struct device_node vreg_node={"cpu-regulator"},foreign_node={"other-regulator"};
static struct device parent={&node,NULL};
static struct regulator_desc desc={&cpr3_regulator_ops};
static struct regulator_dev rdev={&desc,{NULL,&parent},&vreg};
static struct regulator consumer={&rdev,0},vdd,system_rail,ldo;
static bool created;
static void reset(void)
{
 if(created)assert(!pthread_mutex_destroy(&ctrl.lock.value));
 memset(&ctrl,0,sizeof(ctrl));assert(!pthread_mutex_init(&ctrl.lock.value,NULL));created=true;
 memset(&thread,0,sizeof(thread));memset(&vreg,0,sizeof(vreg));
 memset(mmio,0,sizeof(mmio));memset(read_counts,0,sizeof(read_counts));
 INIT_LIST_HEAD(&cpr3_controller_list);INIT_LIST_HEAD(&ctrl.list);list_add_tail(&ctrl.list,&cpr3_controller_list);
 desc.ops=&cpr3_regulator_ops;rdev.desc=&desc;rdev.data=&vreg;
 ctrl.cpu_power_domain=true;ctrl.ctrl_type=CPR_CTRL_TYPE_CPR3;
 ctrl.thread=&thread;ctrl.thread_count=1;thread.ctrl=&ctrl;thread.thread_id=1;thread.vreg=&vreg;thread.vreg_count=1;
 vreg.thread=&thread;vreg.rdev=&rdev;vreg.corner_count=4;vreg.current_corner=1;vreg.vreg_enabled=true;
 vreg.of_node=&vreg_node;
 vreg.ldo_regulator=&ldo;vreg.ldo_regulator_bypass=BHS_MODE;vreg.ldo_mode_allowed=true;
 ctrl.cpr_enabled=true;ctrl.supports_hw_closed_loop=true;ctrl.use_hw_closed_loop=true;
 ctrl.cpr_allowed_hw=ctrl.cpr_allowed_sw=true;ctrl.cpr_ctrl_base=mmio;ctrl.apm=&ctrl;
 ctrl.aggr_corner=(struct cpr3_corner){700000,1000000,NULL,850000,0};ctrl.vdd_regulator=&vdd;ctrl.system_regulator=&system_rail;
 ctrl.step_volt=10000;
 vdd.refs=ldo.refs=system_rail.refs=1;
 mmio[1]=1;mmio[2]=1;mmio[0x3000/4]=0;
 reads=writes=enables=disables=gets=sets=updates=bhs_calls=apm_calls=clock_calls=irq_masks=0;
 rail_uv=850000;rail_error=update_error=bhs_error=apm_error=0;step=fail_step=0;
 scales=clear_irqs=continuations=0;scale_error=clear_irq_error=0;
 unstable_address=NULL;unmapped=drift_after_voltage=false;
}
static void assert_quarantined(int error)
{
 unsigned e=enables,d=disables,u=updates,s=sets,b=bhs_calls,w=writes;
 unsigned selector=99;
 assert(ctrl.power_error==error);
 assert(cpr3_regulator_enable(&rdev)==error && cpr3_regulator_disable(&rdev)==error);
 assert(cpr3_regulator_set_voltage(&rdev,3,4,&selector)==error && selector==99);
 assert(cpr3_regulator_get_voltage(&rdev)==error && cpr3_regulator_is_enabled(&rdev)==error);
 assert(cpr3_debug_ldo_mode_allowed_set(&vreg,0)==error);
 assert(cpr3_debug_closed_loop_enable_set(&ctrl,1)==error);
 assert(enables==e && disables==d && updates==u && sets==s && bhs_calls==b && writes==w);
}
static void *reader(void *unused)
{ unsigned i;struct qcom_cpr3_cpu_state state;(void)unused;
 for(i=0;i<2000;i++) { assert(!qcom_cpr3_cpu_get_state(&consumer,&state) && state.raw_valid);
 assert(!qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state) && state.raw_valid); }
 return NULL; }
static void *retiring_reader(void *unused)
{
 unsigned i;struct qcom_cpr3_cpu_state state;int rc;(void)unused;
 for(i=0;i<2000;i++) {
  rc=qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state);
  assert((!rc && state.raw_valid) || (rc==-EPROBE_DEFER && !state.raw_valid));
 }
 return NULL;
}
int main(void)
{
 struct qcom_cpr3_cpu_state state;unsigned before,selector;int i;pthread_t readers[4];
 reset();assert(!qcom_cpr3_cpu_get_state(&consumer,&state));
 assert(state.thread_id==1 && state.corner==2 && state.corner_count==4 && state.vdd_uv==850000);
 assert(state.enabled && state.clocks_enabled && state.raw_valid && state.loop_ctl==1 && state.status==1);
 assert(state.hw_loop_supported && state.hw_loop_selected && state.hw_closed_loop==0 && !writes && !clock_calls);
 assert(!qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state) && state.raw_valid && !writes && !clock_calls && !enables);
 assert(qcom_cpr3_cpu_get_state_by_node(NULL,&state)==-EINVAL && qcom_cpr3_cpu_get_state_by_node(&vreg_node,NULL)==-EINVAL);
 assert(qcom_cpr3_cpu_get_state_by_node(&foreign_node,&state)==-EPROBE_DEFER && !state.raw_valid);
 assert(qcom_cpr3_cpu_get_state(NULL,&state)==-EINVAL && qcom_cpr3_cpu_get_state(&consumer,NULL)==-EINVAL);
 reset();ctrl.cpu_power_domain=false;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EOPNOTSUPP && !reads);
 assert(qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state)==-EOPNOTSUPP && !reads);
 reset();ctrl.ctrl_type=CPR_CTRL_TYPE_CPR4;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EOPNOTSUPP && !reads);
 reset();desc.ops=&foreign_ops;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EOPNOTSUPP && !reads);
 reset();ctrl.cpr_enabled=false;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ENODATA && !reads && !gets && !clock_calls && !state.raw_valid);
 reset();vreg.vreg_enabled=false;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ENODATA && !reads && !gets);
 reset();vreg.current_corner=-1;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ENODATA && !reads && !state.corner);
 reset();vreg.current_corner=INT_MAX;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ENODATA && !reads && !state.corner);
 reset();ctrl.cpr_suspended=true;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EBUSY && !reads);
 reset();ctrl.power_error=-ETIMEDOUT;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ETIMEDOUT && state.power_error==-ETIMEDOUT && !reads);
 reset();unstable_address=&mmio[1];assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EAGAIN && !state.raw_valid && !writes);
 reset();drift_after_voltage=true;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EAGAIN && !state.raw_valid && !writes);
 reset();mmio[0x3000/4]=2;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EIO && !state.raw_valid);
 reset();rail_error=-EPROBE_DEFER;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EPROBE_DEFER && !state.raw_valid);
 reset();rail_uv=0;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ENODATA && !state.raw_valid);
 reset();rail_uv=1100000;assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-ERANGE && !state.raw_valid);
 reset();ctrl.supports_hw_closed_loop=false;assert(!qcom_cpr3_cpu_get_state(&consumer,&state) && !state.hw_loop_supported && state.hw_closed_loop==U32_MAX);
 /* A stale rdev descriptor/driver data must not be touched after registry removal. */
 reset();mutex_lock(&cpr3_controller_list_mutex);list_del_init(&ctrl.list);mutex_unlock(&cpr3_controller_list_mutex);
 rdev.desc=ERR_PTR(-ENODEV);rdev.data=ERR_PTR(-ENODEV);unmapped=true;
 assert(qcom_cpr3_cpu_get_state(&consumer,&state)==-EPROBE_DEFER && !reads);
 assert(qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state)==-EPROBE_DEFER && !reads);
 reset();for(i=0;i<4;i++)assert(!pthread_create(&readers[i],NULL,reader,NULL));
 for(i=0;i<4;i++) { assert(!pthread_join(readers[i],NULL)); }
 assert(!writes && !clock_calls);
 /* Registry withdrawal is the provider retirement boundary being modeled.
  * Ordinary regulator unbind/reprobe lifetime is not proved by this race.
  */
 reset();for(i=0;i<4;i++)assert(!pthread_create(&readers[i],NULL,retiring_reader,NULL));
 mutex_lock(&cpr3_controller_list_mutex);list_del_init(&ctrl.list);
 rdev.desc=ERR_PTR(-ENODEV);rdev.data=ERR_PTR(-ENODEV);unmapped=true;
 mutex_unlock(&cpr3_controller_list_mutex);
 for(i=0;i<4;i++) { assert(!pthread_join(readers[i],NULL)); }
 reset();selector=99;assert(cpr3_regulator_set_voltage(&rdev,0,4,&selector)==-EINVAL && selector==99 && !updates);
 assert(cpr3_regulator_set_voltage(&rdev,INT_MIN,4,&selector)==-EINVAL && selector==99 && !updates);
 assert(cpr3_regulator_set_voltage(&rdev,3,INT_MIN,&selector)==-EINVAL && selector==99 && !updates);
 assert(cpr3_regulator_set_voltage(&rdev,5,5,&selector)==-EINVAL);
 assert(cpr3_regulator_set_voltage(&rdev,3,2,&selector)==-EINVAL);
 assert(cpr3_regulator_set_voltage(&rdev,3,4,NULL)==-EINVAL);
 reset();vreg.vreg_enabled=false;assert(!cpr3_regulator_set_voltage(&rdev,3,4,&selector) && vreg.current_corner==2 && selector==2 && !updates);
 reset();ctrl.power_error=-ETIMEDOUT;assert_quarantined(-ETIMEDOUT);
 reset();update_error=-ETIMEDOUT;selector=99;
 assert(cpr3_regulator_set_voltage(&rdev,3,4,&selector)==-ETIMEDOUT && vreg.current_corner==1 && selector==99);assert_quarantined(-ETIMEDOUT);
 /* All partial enables retain acknowledged references; no unsafe rollback. */
 for(i=1;i<=3;i++){
  reset();vreg.vreg_enabled=false;fail_step=i;
  assert(cpr3_regulator_enable(&rdev)==-EIO && !disables);assert_quarantined(-EIO);
 }
 reset();vreg.vreg_enabled=false;update_error=-ETIMEDOUT;
 assert(cpr3_regulator_enable(&rdev)==-ETIMEDOUT && enables==3 && !disables && vreg.vreg_enabled);assert_quarantined(-ETIMEDOUT);
 reset();vreg.vreg_enabled=false;assert(!cpr3_regulator_enable(&rdev) && enables==3 && !disables && updates==1);
 before=enables;assert(!cpr3_regulator_enable(&rdev) && enables==before);
 reset();apm_error=-ETIMEDOUT;assert(cpr3_regulator_disable(&rdev)==-ETIMEDOUT && !disables && !bhs_calls);assert_quarantined(-ETIMEDOUT);
 reset();apm_error=-ETIMEDOUT;vreg.vreg_enabled=false;assert(cpr3_regulator_enable(&rdev)==-ETIMEDOUT && !enables);assert_quarantined(-ETIMEDOUT);
 reset();fail_step=1;assert(cpr3_regulator_disable(&rdev)==-EIO && disables==1);assert_quarantined(-EIO);
 reset();update_error=-EIO;assert(cpr3_regulator_disable(&rdev)==-EIO && disables==2 && !enables);assert_quarantined(-EIO);
 reset();assert(!cpr3_regulator_disable(&rdev) && disables==3 && !vreg.vreg_enabled);
 reset();vreg.ldo_regulator_bypass=LDO_MODE;rail_error=-EIO;
 assert(cpr3_debug_ldo_mode_allowed_set(&vreg,0)==-EIO && vreg.ldo_mode_allowed);assert_quarantined(-EIO);
 reset();vreg.ldo_regulator_bypass=LDO_MODE;rail_uv=0;
 assert(cpr3_debug_ldo_mode_allowed_set(&vreg,0)==-ENODATA && !bhs_calls);assert_quarantined(-ENODATA);
 reset();vreg.ldo_regulator_bypass=LDO_MODE;rail_uv=0;
 assert(cpr3_regulator_disable(&rdev)==-ENODATA && !bhs_calls && !disables);assert_quarantined(-ENODATA);
 reset();vreg.ldo_regulator_bypass=LDO_MODE;bhs_error=-EIO;
 assert(cpr3_debug_ldo_mode_allowed_set(&vreg,0)==-EIO && vreg.ldo_mode_allowed);assert_quarantined(-EIO);
 reset();update_error=-EIO;assert(cpr3_debug_ldo_mode_allowed_set(&vreg,0)==-EIO && vreg.ldo_mode_allowed);assert_quarantined(-EIO);
 reset();assert(!cpr3_debug_ldo_mode_allowed_set(&vreg,1) && !updates);
 reset();ctrl.cpr_allowed_sw=false;ctrl.cpr_allowed_hw=false;
 assert(cpr3_debug_closed_loop_enable_set(&ctrl,1)==-EPERM && !ctrl.power_error && !updates);
 reset();ctrl.cpr_allowed_sw=false;update_error=-ETIMEDOUT;
 assert(cpr3_debug_closed_loop_enable_set(&ctrl,1)==-ETIMEDOUT);assert_quarantined(-ETIMEDOUT);
 reset();assert(!cpr3_irq_blocked(&ctrl,30,false) && !irq_masks);
 ctrl.power_error=-EIO;before=reads;
 assert(cpr3_irq_blocked(&ctrl,30,false) && cpr3_irq_blocked(&ctrl,30,false));
 assert(cpr3_irq_blocked(&ctrl,31,true) && cpr3_irq_blocked(&ctrl,31,true));
 assert(irq_masks==2 && reads==before && !writes);
 reset();ctrl.use_hw_closed_loop=false;mmio[CPR3_REG_IRQ_STATUS/4]=CPR3_IRQ_UP;scale_error=-ETIMEDOUT;
 assert(cpr3_irq_handler(30,&ctrl)==IRQ_HANDLED && scales==1 && ctrl.power_error==-ETIMEDOUT && irq_masks==1 && !continuations);
 assert(!mmio[CPR3_REG_IRQ_CLEAR/4]);before=reads;
 assert(cpr3_irq_handler(30,&ctrl)==IRQ_HANDLED && reads==before && scales==1 && irq_masks==1);
 reset();ctrl.use_hw_closed_loop=false;mmio[CPR3_REG_IRQ_STATUS/4]=CPR3_IRQ_UP;
 assert(cpr3_irq_handler(30,&ctrl)==IRQ_HANDLED && scales==1 && !ctrl.power_error && continuations==1 && !irq_masks);
 reset();rail_error=-EIO;
 assert(cpr3_ceiling_irq_handler(31,&ctrl)==IRQ_HANDLED && ctrl.power_error==-EIO && irq_masks==1 && !clear_irqs);
 before=reads;assert(cpr3_ceiling_irq_handler(31,&ctrl)==IRQ_HANDLED && reads==before && !clear_irqs && irq_masks==1);
 reset();rail_uv=0;assert(cpr3_ceiling_irq_handler(31,&ctrl)==IRQ_HANDLED && ctrl.power_error==-ENODATA && !clear_irqs);
 reset();rail_uv=ctrl.aggr_corner.ceiling_volt;clear_irq_error=-ETIMEDOUT;
 assert(cpr3_ceiling_irq_handler(31,&ctrl)==IRQ_HANDLED && clear_irqs==1 && ctrl.power_error==-ETIMEDOUT && irq_masks==1);
 reset();assert(!cpr3_regulator_suspend(&ctrl));
 assert(qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state)==-EBUSY && state.suspended && !state.raw_valid);
 update_error=-EPROBE_DEFER;assert(cpr3_regulator_resume(&ctrl)==-EPROBE_DEFER && ctrl.cpr_suspended);
 assert(qcom_cpr3_cpu_get_state_by_node(&vreg_node,&state)==-EPROBE_DEFER && state.power_error==-EPROBE_DEFER);
 before=updates;assert(cpr3_regulator_resume(&ctrl)==-EPROBE_DEFER && updates==before);
 puts("PASS: actual HMSS consumer/node-state registry/readback, corner/rail quarantine closure, complete IRQ/debugfs failures, concurrent snapshots/withdrawal and PM");
 puts("Readback and host boundary checks do not establish autonomous CPR/SAW/LMH exclusion or measured CPU voltage.");
 return 0;
}
'''


def harness(source):
    state = re.search(r'struct qcom_cpr3_cpu_state \{.*?\n\};', HEADER.read_text(), re.S)
    assert state
    return PREAMBLE + state[0] + '\n' + ''.join(function(source, name) for name in FUNCTIONS) + TESTS


def run(source, path, name, expected=True):
    c, binary = path / (name + '.c'), path / name
    c.write_text(harness(source))
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('CPR_CPU_STATE_SANITIZE') else []
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
                    *flags, str(c), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert (result.returncode == 0) == expected, (name, result.stdout, result.stderr)
    if expected:
        print(result.stdout, end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    source = DRIVER.read_text()
    for name, call in [('cpr3_irq_handler', 'cpr3_irq_blocked(ctrl, irq, false)'),
                       ('cpr3_ceiling_irq_handler', 'cpr3_irq_blocked(ctrl, irq, true)')]:
        body = function(source, name)
        assert body.index(call) < body.index('if (!ctrl->cpr_enabled)')
    for name in ['cpr3_debug_hw_closed_loop_enable_set', 'cpr3_debug_trigger_aging_measurement_set']:
        body = function(source, name)
        assert body.index('if (ctrl->power_error)') < body.index('cpr3_ctrl_loop_disable(ctrl)')
        assert body.rstrip().endswith('return rc;\n}')
    mutations = [
        ('enable-quarantine-bypass', 'cpr3_regulator_enable', 'rc = cpr3_apm_ready(ctrl);', 'rc = 0;'),
        ('disable-quarantine-bypass', 'cpr3_regulator_disable', 'rc = cpr3_apm_ready(ctrl);', 'rc = 0;'),
        ('cached-corner-after-fault', 'cpr3_regulator_get_voltage', 'ret = ctrl->power_error;', 'ret = 0;'),
        ('clear-unknown-corner', 'cpr3_cpu_copy_state', '!ctrl->cpr_enabled || !vreg->vreg_enabled ||', '!vreg->vreg_enabled ||'),
        ('accept-mmss-provider', 'qcom_cpr3_cpu_get_state', '!ctrl->cpu_power_domain ||', 'false ||'),
        ('skip-stable-readback', 'cpr3_cpu_copy_state', 'return -EAGAIN;', 'return 0;'),
        ('fake-enabled-after-fault', 'cpr3_regulator_is_enabled', 'ctrl->power_error ? ctrl->power_error : vreg->vreg_enabled', 'vreg->vreg_enabled'),
        ('drop-irq-quarantine', 'cpr3_irq_blocked', 'if (!ctrl->power_error)', 'if (true)'),
        ('swallow-debugfs-ldo-error', 'cpr3_debug_ldo_mode_allowed_set', 'return rc;', 'return 0;'),
        ('unsafe-enable-rollback', 'cpr3_regulator_enable', 'goto fault;\n\t}\n\n\tcpr3_debug', 'regulator_disable(ctrl->vdd_regulator);\n\t\tgoto fault;\n\t}\n\n\tcpr3_debug'),
        ('node-identity-bypass', 'qcom_cpr3_cpu_get_state_by_node', 'if (vreg->of_node != node)', 'if (false)'),
        ('node-untyped-provider', 'qcom_cpr3_cpu_get_state_by_node', '!ctrl->cpu_power_domain ||', 'false ||'),
        ('irq-continue-after-fault', 'cpr3_irq_handler', 'done:\n\tif (cpr3_irq_blocked(ctrl, irq, false)) {', 'done:\n\tif (false) {'),
        ('irq-failure-not-latched', 'cpr3_irq_handler', 'cpr3_power_fault(ctrl, rc);', '(void)rc;'),
        ('ceiling-voltage-error-ignored', 'cpr3_ceiling_irq_handler', 'cpr3_power_fault(ctrl, volt ? volt : -ENODATA);', '(void)volt;'),
        ('ceiling-ack-error-ignored', 'cpr3_ceiling_irq_handler', 'cpr3_power_fault(ctrl, rc);', '(void)rc;'),
    ]
    with tempfile.TemporaryDirectory(prefix='dora-cpr-cpu-state-') as tmp:
        path = Path(tmp)
        run(source, path, 'production')
        if args.self_test:
            for name, target, before, after in mutations:
                body = function(source, target)
                assert before in body, name
                changed = body.replace(before, after)
                run(source.replace(body, changed, 1), path, name, False)
            print('PASS: %d unsafe CPR ownership/readback mutations rejected' % len(mutations))
    if args.report:
        args.report.write_text(json.dumps(dict(production_functions=len(FUNCTIONS),
            unsafe_mutations_rejected=len(mutations) if args.self_test else 0,
            hardware_verified=False, autonomous_exclusion_established=False), indent=2)+'\n')


if __name__ == '__main__':
    main()
