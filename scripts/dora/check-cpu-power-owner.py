#!/usr/bin/env python3
"""Execute the production read-only CPU resource owner at modeled boundaries.

OF, device lifetime, CCF parent preparations and typed provider snapshots are
host models. No rail ACK, physical voltage, autonomous exclusion or writable
CPU hardware qualification follows from these software invariant tests.
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
QCOM = ROOT / 'drivers/clk/qcom'
DRIVER = QCOM / 'clk-msm8996-cpu-power.c'


def production(path):
    return re.sub(r'^#include[^\n]*\n', '', path.read_text(), flags=re.M)


def function(source, name):
    match = re.search(r'^(?:static )?(?:inline )?(?:int|bool|void|unsigned long|struct \w+ \*)\s*' +
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
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
typedef uint8_t u8;
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define EPROBE_DEFER 517
#define IS_REACHABLE(x) 1
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p)||IS_ERR(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define EXPORT_SYMBOL_GPL(x)
#define READ_ONCE(v) __atomic_load_n(&(v),__ATOMIC_RELAXED)
#define WRITE_ONCE(v,x) __atomic_store_n(&(v),(x),__ATOMIC_RELAXED)
#define smp_load_acquire(p) __atomic_load_n((p),__ATOMIC_ACQUIRE)
#define smp_store_release(p,v) __atomic_store_n((p),(v),__ATOMIC_RELEASE)
typedef atomic_int atomic_t;
#define ATOMIC_INIT(v) ATOMIC_VAR_INIT(v)
static int atomic_read(atomic_t *a) { return atomic_load(a); }
static int atomic_cmpxchg(atomic_t *a,int old,int value)
{ atomic_compare_exchange_strong(a,&old,value);return old; }
struct mutex { pthread_mutex_t value; };
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
#define DEFINE_RAW_SPINLOCK(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
#define raw_spin_lock_irqsave(m,f) do { (f)=0;mutex_lock(m); } while(0)
#define raw_spin_unlock_irqrestore(m,f) do { (void)(f);mutex_unlock(m); } while(0)
#define DL_FLAG_AUTOREMOVE_CONSUMER 1
#define THIS_MODULE NULL
#define GPLL0 0
struct device_node {
 const char *compatible,*regulator_name,*resource_name;
 struct device_node *parent;
 bool available,use_corner;
 u32 sets,resource_id;
 atomic_int refs;
};
struct device { struct device_node *of_node;void *driver;atomic_int refs; };
struct platform_device { struct device dev; };
struct device_link { int unused; };
struct of_phandle_args { struct device_node *np;int args_count;u32 args[1]; };
struct clk { unsigned id;unsigned long rate;atomic_int refs;unsigned prepared; };
static struct device_node tree_root,apcc_node,cbf_node,gcc_node,hmss_node,mx_parent,supply_node[4];
static struct device apcc_dev,cbf_dev;
static struct platform_device gcc_dev;
static struct device_link supplier_link;
static struct clk clocks[2],foreign_clock;
static bool dora,supplier_present,link_failure,supply_missing,clock_recalc;
static unsigned long cbf_readback;
static unsigned missing_supply,clock_get_failure,prepare_failure;
static unsigned supplier_args;
static atomic_uint prepares,unprepares,typed_cpr,typed_rpm,module_pins;
static int snapshot_cpr_error[3],snapshot_rpm_error;
static bool snapshot_raw,snapshot_enabled;
static u32 snapshot_mx_id;
static pthread_mutex_t prepare_lock=PTHREAD_MUTEX_INITIALIZER;
static void ccf_recalc(void);
static struct device *get_device(struct device *dev) { atomic_fetch_add(&dev->refs,1);return dev; }
static void put_device(struct device *dev) { assert(atomic_fetch_sub(&dev->refs,1)>0); }
static void __module_get(void *module) { (void)module;module_pins++; }
static int of_machine_is_compatible(const char *name)
{ return dora && !strcmp(name,"sony,dora-row"); }
static int of_device_is_compatible(struct device_node *node,const char *name)
{ return node && node->compatible && !strcmp(node->compatible,name); }
static int of_device_is_available(struct device_node *node) { return node->available; }
static struct device_node *of_node_get(struct device_node *node)
{ if(node)atomic_fetch_add(&node->refs,1);return node; }
static void of_node_put(struct device_node *node)
{ if(node)assert(atomic_fetch_sub(&node->refs,1)>0); }
static struct device_node *of_get_parent(struct device_node *node)
{ return of_node_get(node->parent); }
static struct device_node *of_parse_phandle(struct device_node *node,const char *name,unsigned index)
{
 static const char * const properties[]={"vdd-pwrcl-supply","vdd-perfcl-supply","vdd-cbf-supply","vdd-dig-supply"};
 unsigned i;assert(node==&apcc_node && !index);
 for(i=0;i<4;i++)if(!strcmp(properties[i],name))
  return supply_missing && missing_supply==i?NULL:of_node_get(&supply_node[i]);
 return NULL;
}
static int of_parse_phandle_with_args(struct device_node *node,const char *name,const char *cells,
 unsigned index,struct of_phandle_args *args)
{
 assert((node==&apcc_node || node==&cbf_node) && !strcmp(name,"clocks") && !strcmp(cells,"#clock-cells") && index==1);
 args->np=of_node_get(&gcc_node);args->args_count=supplier_args;args->args[0]=GPLL0;return 0;
}
static int of_property_read_string(struct device_node *node,const char *name,const char **value)
{
 if(!strcmp(name,"regulator-name"))*value=node->regulator_name;
 else if(!strcmp(name,"qcom,resource-name"))*value=node->resource_name;
 else return -EINVAL;
 return *value?0:-EINVAL;
}
static int of_property_read_u32(struct device_node *node,const char *name,u32 *value)
{
 if(!strcmp(name,"qcom,set"))*value=node->sets;
 else if(!strcmp(name,"qcom,resource-id"))*value=node->resource_id;
 else return -EINVAL;
 return 0;
}
static bool of_property_read_bool(struct device_node *node,const char *name)
{ assert(!strcmp(name,"qcom,use-voltage-corner"));return node->use_corner; }
static struct platform_device *of_find_device_by_node(struct device_node *node)
{ assert(node==&gcc_node);if(!supplier_present)return NULL;get_device(&gcc_dev.dev);return &gcc_dev; }
static void device_lock(struct device *dev) { assert(dev==&gcc_dev.dev); }
static void device_unlock(struct device *dev) { assert(dev==&gcc_dev.dev); }
static struct device_link *device_link_add(struct device *consumer,struct device *supplier,unsigned flags)
{ assert((consumer==&apcc_dev || consumer==&cbf_dev) && supplier==&gcc_dev.dev && flags==DL_FLAG_AUTOREMOVE_CONSUMER);return link_failure?NULL:&supplier_link; }
static struct clk *clk_get(struct device *dev,const char *name)
{
 unsigned id;assert(dev==&apcc_dev || dev==&cbf_dev);id=!strcmp(name,"aux");
 assert(id || !strcmp(name,"xo"));if(clock_get_failure==id+1)return ERR_PTR(-EPROBE_DEFER);
 atomic_fetch_add(&clocks[id].refs,1);return &clocks[id];
}
static void clk_put(struct clk *clk) { assert(atomic_fetch_sub(&clk->refs,1)>0); }
static bool clk_is_match(struct clk *a,struct clk *b) { return a->id==b->id; }
static unsigned long clk_get_rate(struct clk *clk)
{ unsigned long rate;assert(!pthread_mutex_lock(&prepare_lock));if(clock_recalc)ccf_recalc();rate=clk->rate;assert(!pthread_mutex_unlock(&prepare_lock));return rate; }
static int clk_prepare_enable(struct clk *clk)
{
 unsigned count;assert(!pthread_mutex_lock(&prepare_lock));
 if(clock_recalc)ccf_recalc();
 count=++prepares;
 if(count==prepare_failure){assert(!pthread_mutex_unlock(&prepare_lock));return -ETIMEDOUT;}
 clk->prepared++;assert(!pthread_mutex_unlock(&prepare_lock));return 0;
}
static void __attribute__((unused)) clk_disable_unprepare(struct clk *clk)
{ unprepares++;assert(clk->prepared);clk->prepared--; }
static int scnprintf(char *buffer,size_t size,const char *format,...)
{ va_list args;int length;va_start(args,format);length=vsnprintf(buffer,size,format,args);va_end(args);return size && length>=(int)size?(int)size-1:length; }
'''

SNAPSHOTS = r'''
static int qcom_cpr3_cpu_get_state_by_node(struct device_node *node,struct qcom_cpr3_cpu_state *state)
{
 unsigned i;for(i=0;i<3;i++)if(node==&supply_node[i])break;assert(i<3);typed_cpr++;
 memset(state,0,sizeof(*state));state->raw_valid=snapshot_raw;state->enabled=snapshot_enabled;
 return snapshot_cpr_error[i];
}
static int qcom_rpm_corner_get_state_by_node(struct device_node *node,struct qcom_rpm_corner_state *state)
{
 assert(node==&supply_node[3]);typed_rpm++;memset(state,0,sizeof(*state));
 state->resource_type=0x61706d73;state->resource_id=snapshot_mx_id;state->key=0x6e726f63;state->sets=1;
 /* No cached request or ACK is fabricated by the observation model. */
 return snapshot_rpm_error;
}
'''

TESTS = r'''
static struct msm8996_cpu_hw_plans plans;
static void ccf_recalc(void)
{
 /* CCF holds prepare_lock. Taking owner_lock in observe would deadlock an
  * attach preparing real parents while holding its lifecycle mutex. */
 if(cpu_power_port[1].claimed && !cpu_power_port[1].retired)
  (void)msm8996_cpu_power_observe(&cpu_power_port[1],2,cbf_readback);
}
static void node_refs(void)
{
 unsigned i;assert(!atomic_load(&tree_root.refs) && !atomic_load(&apcc_node.refs) && !atomic_load(&cbf_node.refs));
 assert(!atomic_load(&gcc_node.refs) && !atomic_load(&hmss_node.refs) && !atomic_load(&mx_parent.refs));
 for(i=0;i<4;i++)assert(!atomic_load(&supply_node[i].refs));
 assert(!atomic_load(&gcc_dev.dev.refs));
}
static void fixture(void)
{
 unsigned i;node_refs();
 /* Reset models a new boot, never unbind/reprobe or quarantine recovery. */
 memset(cpu_power_port,0,sizeof(cpu_power_port));memset(cpu_power_parent,0,sizeof(cpu_power_parent));
 memset(cpu_power_prepared,0,sizeof(cpu_power_prepared));memset(cpu_power_plan,0,sizeof(cpu_power_plan));
 memset(&cpu_power_plans,0,sizeof(cpu_power_plans));cpu_power_plans_valid=false;
 memset(cpu_power_rate,0,sizeof(cpu_power_rate));atomic_store(&cpu_power_fault,0);
 for(i=0;i<4;i++)cpu_power_reg_error[i]=-EPROBE_DEFER;
 tree_root=(struct device_node){.available=true};
 apcc_node=(struct device_node){.parent=&tree_root,.available=true};cbf_node=apcc_node;
 gcc_node=(struct device_node){.parent=&tree_root,.available=true,.compatible="qcom,gcc-msm8996"};
 hmss_node=(struct device_node){.parent=&tree_root};
 mx_parent=(struct device_node){.parent=&tree_root,.resource_name="smpa",.resource_id=2};
 supply_node[0]=(struct device_node){.parent=&hmss_node,.available=true,.regulator_name="apc0_pwrcl_corner"};
 supply_node[1]=(struct device_node){.parent=&hmss_node,.available=true,.regulator_name="apc1_corner"};
 supply_node[2]=(struct device_node){.parent=&hmss_node,.available=true,.regulator_name="apc0_cbf_corner"};
 supply_node[3]=(struct device_node){.parent=&mx_parent,.available=true,.regulator_name="pm8994_s2_corner_ao",.sets=1,.use_corner=true};
 apcc_dev=(struct device){.of_node=&apcc_node};cbf_dev=(struct device){.of_node=&cbf_node};
 gcc_dev=(struct platform_device){.dev={.of_node=&gcc_node,.driver=&gcc_dev}};
 clocks[0]=(struct clk){.id=0,.rate=19200000};clocks[1]=(struct clk){.id=1,.rate=600000000};foreign_clock=(struct clk){.id=2};
 plans=(struct msm8996_cpu_hw_plans){.domain={source_plan0,source_plan1,source_plan2},
  .count={ARRAY_SIZE(source_plan0),ARRAY_SIZE(source_plan1),ARRAY_SIZE(source_plan2)}};
 dora=supplier_present=clock_recalc=true;link_failure=supply_missing=false;
 cbf_readback=672000000;
 supplier_args=1;missing_supply=clock_get_failure=prepare_failure=0;
 prepares=unprepares=typed_cpr=typed_rpm=module_pins=0;
 memset(snapshot_cpr_error,0,sizeof(snapshot_cpr_error));snapshot_rpm_error=0;
 snapshot_raw=snapshot_enabled=true;snapshot_mx_id=2;
}
static struct msm8996_cpu_power_port *attach(bool cbf)
{ return msm8996_cpu_power_attach(cbf?&cbf_dev:&apcc_dev,cbf,&clocks[0],&clocks[1],cbf?NULL:&plans); }
static struct msm8996_cpu_power_port *good_attach(bool cbf)
{ struct msm8996_cpu_power_port *p=attach(cbf);assert(!IS_ERR_OR_NULL(p));node_refs();return p; }
static unsigned blockers(char *buffer)
{ char *p=strstr(buffer,"blockers=0x");unsigned value;assert(p && sscanf(p,"blockers=0x%x",&value)==1);return value; }
static void status(struct msm8996_cpu_power_port *p,char *buffer)
{
 assert(msm8996_cpu_power_format(p,buffer,512)>0);
 assert(strstr(buffer,"readonly=1") && strstr(buffer,"voltage_leases=0") && strstr(buffer,"mx_logical_required=4"));
 assert((blockers(buffer)&(CPU_POWER_LMH|CPU_POWER_AUTONOMOUS|CPU_POWER_LEASES))==(CPU_POWER_LMH|CPU_POWER_AUTONOMOUS|CPU_POWER_LEASES));
 assert(!unprepares);node_refs();
}
static void preclaim_failure(int expected)
{ assert(PTR_ERR(attach(false))==expected && !cpu_power_port[0].claimed && !prepares && !cpu_power_plans_valid);node_refs(); }
static void *claim_race(void *unused)
{ (void)unused;return attach(true); }
static void *observer_race(void *arg)
{
 struct msm8996_cpu_power_port *p=arg;unsigned i;char buffer[512];
 for(i=0;i<1000;i++){
  assert(!pthread_mutex_lock(&prepare_lock));
  assert(msm8996_cpu_power_observe(p,2,672000000)==672000000);
  assert(!pthread_mutex_unlock(&prepare_lock));
  assert(msm8996_cpu_power_format(p,buffer,sizeof(buffer))>0);
  assert(strstr(buffer,"readonly=1") && strstr(buffer,"voltage_leases=0"));
 }
 return NULL;
}
int main(void)
{
 struct msm8996_cpu_power_port *a,*b;char buffer[512];unsigned i,before,success;pthread_t threads[6];void *result;
 unsigned long saved_rate;
 fixture();dora=false;assert(!attach(false));assert(msm8996_cpu_power_observe(NULL,99,123)==123);
 assert(!msm8996_cpu_power_suspend(NULL) && !msm8996_cpu_power_resume(NULL));
 assert(msm8996_cpu_power_format(NULL,buffer,sizeof(buffer))==-EOPNOTSUPP && !prepares);
 fixture();assert(PTR_ERR(msm8996_cpu_power_attach(NULL,false,&clocks[0],&clocks[1],&plans))==-EINVAL);
 for(i=0;i<4;i++){fixture();supply_missing=true;missing_supply=i;preclaim_failure(-EINVAL);}
 fixture();supply_node[0].regulator_name="foreign";preclaim_failure(-EINVAL);
 fixture();supply_node[3].sets=3;preclaim_failure(-EINVAL);
 fixture();supply_node[3].use_corner=false;preclaim_failure(-EINVAL);
 fixture();mx_parent.resource_id=1;preclaim_failure(-EINVAL);
 fixture();mx_parent.resource_name="ldoa";preclaim_failure(-EINVAL);
 fixture();supplier_present=false;preclaim_failure(-EPROBE_DEFER);
 fixture();gcc_dev.dev.driver=NULL;preclaim_failure(-EPROBE_DEFER);
 fixture();supplier_args=0;preclaim_failure(-EINVAL);
 fixture();gcc_node.compatible="foreign,gcc";preclaim_failure(-EINVAL);
 fixture();link_failure=true;preclaim_failure(-ENOMEM);
 fixture();plans.speed_bin=4;preclaim_failure(-EINVAL);
 fixture();plans.count[0]=33;preclaim_failure(-EINVAL);
 for(i=1;i<=2;i++){fixture();clock_get_failure=i;preclaim_failure(-EPROBE_DEFER);assert(!atomic_load(&clocks[0].refs));}
 fixture();clocks[1].rate=300000000;preclaim_failure(-EINVAL);
 fixture();assert(PTR_ERR(msm8996_cpu_power_attach(&apcc_dev,false,&foreign_clock,&clocks[1],&plans))==-EINVAL);
 assert(!cpu_power_plans_valid && !prepares);node_refs();

 fixture();b=good_attach(true);assert(prepares==2 && !cpu_power_plans_valid);
 assert(msm8996_cpu_power_observe(b,2,672000000)==672000000);
 clock_get_failure=2;assert(PTR_ERR(attach(false))==-EPROBE_DEFER && !cpu_power_plans_valid);
 clock_get_failure=0;a=good_attach(false);assert(prepares==2 && cpu_power_plans_valid);
 assert(cpu_power_parent[0]==&clocks[0] && cpu_power_parent[1]==&clocks[1]);
 assert(clocks[0].prepared==1 && clocks[1].prepared==1 && atomic_load(&clocks[0].refs)==1 && atomic_load(&clocks[1].refs)==1);
 assert(msm8996_cpu_power_observe(a,0,652800000)==652800000 && msm8996_cpu_power_observe(a,1,652800000)==652800000);
 assert(cpu_power_copy_plans(&plans)==-EBUSY);
 saved_rate=source_plan2[1].rate;source_plan2[1].rate=0;
 assert(msm8996_cpu_power_observe(b,2,672000000)==672000000);source_plan2[1].rate=saved_rate;
 assert(PTR_ERR(attach(false))==-EBUSY && PTR_ERR(attach(true))==-EBUSY && prepares==2);
 status(a,buffer);assert(strstr(buffer,"domains=0x7") && !(blockers(buffer)&CPU_POWER_PEERS));
 assert((blockers(buffer)&(CPU_POWER_CPR|CPU_POWER_MX))==(CPU_POWER_CPR|CPU_POWER_MX) && !typed_cpr && !typed_rpm);
 hmss_node.available=mx_parent.available=true;status(a,buffer);
 assert(!(blockers(buffer)&(CPU_POWER_CPR|CPU_POWER_MX)) && typed_cpr==3 && typed_rpm==1);
 snapshot_cpr_error[1]=-ETIMEDOUT;status(a,buffer);assert(strstr(buffer,"cpr=0/-110/0") && (blockers(buffer)&CPU_POWER_CPR));
 snapshot_cpr_error[1]=0;snapshot_raw=false;status(a,buffer);assert(blockers(buffer)&CPU_POWER_CPR);
 snapshot_raw=true;snapshot_mx_id=1;status(a,buffer);assert(blockers(buffer)&CPU_POWER_MX);
 snapshot_mx_id=2;snapshot_rpm_error=-EPROBE_DEFER;status(a,buffer);assert(blockers(buffer)&CPU_POWER_MX);
 snapshot_rpm_error=0;before=prepares;
 assert(!msm8996_cpu_power_suspend(a) && !msm8996_cpu_power_suspend(b));status(a,buffer);
 assert(strstr(buffer,"suspended=0x7") && (blockers(buffer)&CPU_POWER_SUSPENDED));
 assert(!msm8996_cpu_power_resume(a) && !msm8996_cpu_power_resume(b));status(a,buffer);
 assert(strstr(buffer,"suspended=0x0") && !(blockers(buffer)&CPU_POWER_SUSPENDED) && before==prepares);
 for(i=0;i<4;i++)assert(!pthread_create(&threads[i],NULL,observer_race,b));
 for(i=0;i<4;i++)assert(!pthread_join(threads[i],NULL));
 node_refs();

 fixture();b=good_attach(true);cbf_readback=4000000000UL;
 assert(msm8996_cpu_power_observe(b,2,4000000000UL)==4000000000UL);
 assert(PTR_ERR(attach(false))==-ERANGE && !cpu_power_plans_valid && !cpu_power_port[0].claimed);
 assert(atomic_read(&cpu_power_fault)==-ERANGE && !msm8996_cpu_power_observe(b,2,672000000));
 assert(prepares==2 && !unprepares);node_refs();
 for(i=1;i<=2;i++){
  fixture();prepare_failure=i;assert(PTR_ERR(attach(false))==-ETIMEDOUT);
  assert(cpu_power_port[0].claimed && prepares==i && module_pins==1);
  assert(cpu_power_prepared[0]==(i==2) && !cpu_power_prepared[1]);
  assert(atomic_load(&clocks[0].refs)==1 && atomic_load(&clocks[1].refs)==1 && !unprepares);
  assert(PTR_ERR(attach(false))==-ETIMEDOUT && prepares==i);
  assert(msm8996_cpu_power_suspend(&cpu_power_port[0])==-ETIMEDOUT && msm8996_cpu_power_resume(&cpu_power_port[0])==-ETIMEDOUT);
  status(&cpu_power_port[0],buffer);assert(blockers(buffer)&CPU_POWER_FAULT);
 }
 fixture();a=good_attach(false);b=good_attach(true);
 assert(!msm8996_cpu_power_observe(a,0,0) && atomic_read(&cpu_power_fault)==-EIO);
 assert(!msm8996_cpu_power_observe(b,2,672000000) && PTR_ERR(attach(true))==-EIO);
 assert(msm8996_cpu_power_suspend(a)==-EIO && msm8996_cpu_power_resume(a)==-EIO);
 fixture();a=good_attach(false);assert(!msm8996_cpu_power_observe(a,2,672000000) && atomic_read(&cpu_power_fault)==-EIO);
 fixture();a=good_attach(false);before=prepares;msm8996_cpu_power_retire(a);
 assert(cpu_power_port[0].retired && atomic_read(&cpu_power_fault)==-ESHUTDOWN);
 assert(PTR_ERR(attach(false))==-ESHUTDOWN && !msm8996_cpu_power_observe(a,0,652800000));
 assert(prepares==before && !unprepares && clocks[0].prepared==1 && clocks[1].prepared==1);
 status(a,buffer);assert(blockers(buffer)&CPU_POWER_FAULT);

 fixture();a=good_attach(false);success=0;
 for(i=0;i<6;i++)assert(!pthread_create(&threads[i],NULL,claim_race,NULL));
 for(i=0;i<6;i++){assert(!pthread_join(threads[i],&result));if(!IS_ERR_OR_NULL(result))success++;else assert(PTR_ERR(result)==-EBUSY);}
 assert(success==1 && prepares==2 && module_pins==2);node_refs();
 puts("PASS: actual CPU owner plans/publication, parent preparations, prior CBF rates, MX AO identity, typed observations, partial failures, lifetime, quarantine, PM bookkeeping and concurrent claims/readers");
 puts("Host boundaries only: no CPU voltage lease, LMHv1/SAW exclusion, measured voltage or writable hardware qualification.");
 return 0;
}
'''


def harness(source):
    code = PREAMBLE
    for path in [QCOM / 'clk-msm8996-cpu-transaction.h', QCOM / 'clk-msm8996-cpu-hw.h',
                 QCOM / 'clk-msm8996-cpu-power.h']:
        code += production(path)
    for header, name in [('qcom-cpr3-cpu.h', 'qcom_cpr3_cpu_state'),
                         ('qcom-rpm-corner.h', 'qcom_rpm_corner_state')]:
        match = re.search(r'struct ' + name + r' \{.*?\n\};',
                          (ROOT / 'include/linux/regulator' / header).read_text(), re.S)
        assert match, header
        code += match[0] + '\n'
    code += SNAPSHOTS
    code += function((QCOM / 'clk-msm8996-cpu-transaction.c').read_text(), 'msm8996_cpu_plan_lookup')
    code += re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)
    plans = json.loads((ROOT / 'Documentation/sony/dora-cpu-dt.json').read_text())['frequency_plans']
    for domain, prefix in enumerate(['pwrcl', 'perfcl', 'cbf']):
        values = plans[f'qcom,{prefix}-speedbin0-v0']
        code += f'static struct msm8996_cpu_plan source_plan{domain}[]={{' + ','.join(
            f'{{{rate}UL,{level}}}' for rate, level in zip(values[::2], values[1::2])) + '};\n'
    return code + TESTS


def dt_block(source, label):
    match = re.search(r'\b' + re.escape(label) + r':[^{};]*\{', source)
    assert match, label
    pos, depth = match.end(), 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.end():pos - 1]


def check_gates(cpu, provider, owner):
    for name in ['sony_apcc_apm', 'sony_hmss_cpr', 'sony_apcc_spm',
                 'sony_kryo0', 'sony_kryo1', 'sony_pm8994_s11']:
        assert re.search(r'^\s*status\s*=\s*"disabled";', dt_block(cpu, name), re.M), name
    for rail in ['s8', 's9', 's10', 's11']:
        assert re.search(r'&pm8994_' + rail + r'\s*\{\s*status\s*=\s*"disabled";', cpu), rail
    assert 'vdd-dig-supply = <&pm8994_s2_corner_ao>;' in cpu
    for name in ['set_rate', 'determine_rate', 'set_parent', 'set_rate_and_parent']:
        assert not re.search(r'\.' + name + r'\s*=', provider), name
    assert 'msm8996_cpu_native_bind(' not in provider + owner
    assert 'regulator_get_optional(' not in owner
    assert 'regulator_enable(' not in owner and 'regulator_set_voltage(' not in owner


def run(source, path, name, expected=True):
    c, binary = path / (name + '.c'), path / name
    code = harness(source)
    c.write_text(code)
    if os.environ.get('CPU_POWER_OWNER_DEBUG'):
        Path(os.environ['CPU_POWER_OWNER_DEBUG']).write_text(code)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('CPU_POWER_OWNER_SANITIZE') else []
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
    cpu = (ROOT / 'arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-cpu.dtsi').read_text()
    provider = (QCOM / 'clk-cpu-8996.c').read_text()
    check_gates(cpu, provider, source)
    mutations = [
        ('duplicate-owner', 'msm8996_cpu_power_attach', 'if (port->claimed)', 'if (false)'),
        ('fake-parent-ack', 'msm8996_cpu_power_attach', 'ret = cpu_power_fail(ret);', 'ret = 0;'),
        ('foreign-mx-resource', 'cpu_power_supplies', 'value != 2', 'false'),
        ('wrong-mx-sets', 'cpu_power_supplies', 'value != 1', 'false'),
        ('clear-quarantine-on-resume', 'msm8996_cpu_power_resume', 'ret = atomic_read(&cpu_power_fault);', 'ret = 0;'),
        ('rewrite-published-plans', 'cpu_power_copy_plans', 'if (cpu_power_plans_valid)', 'if (false)'),
        ('ignore-prior-cbf-rate', 'cpu_power_copy_plans', 'if (!cpu_power_rate[i])', 'if (true)'),
    ]
    with tempfile.TemporaryDirectory(prefix='dora-cpu-power-owner-') as tmp:
        path = Path(tmp)
        run(source, path, 'production')
        if args.self_test:
            for name, target, before, after in mutations:
                body = function(source, target)
                assert before in body, name
                run(source.replace(body, body.replace(before, after), 1), path, name, False)
            for label in ['sony_hmss_cpr', 'sony_apcc_spm', 'sony_pm8994_s11']:
                body = dt_block(cpu, label)
                unsafe = cpu.replace(body, body.replace('status = "disabled";', 'status = "okay";', 1), 1)
                try:
                    check_gates(unsafe, provider, source)
                except AssertionError:
                    pass
                else:
                    raise AssertionError('unsafe DT activation accepted: ' + label)
            print('PASS: %d unsafe owner mutations and 3 CPU power activation mutations rejected' % len(mutations))
    if args.report:
        args.report.write_text(json.dumps(dict(production_owner_executed=True,
            unsafe_mutations_rejected=len(mutations) + 3 if args.self_test else 0,
            hardware_verified=False, writable_adapter_enabled=False), indent=2) + '\n')


if __name__ == '__main__':
    main()
