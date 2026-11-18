#!/usr/bin/env python3
"""Run native Sony MSM8996-v3 callbacks together with the production engine.

Only MMIO, sysreg, IPI and power boundaries are models. These tests cannot
supply the missing CPR/SPM/APM/shared-gang guard or qualify physical hardware.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
QCOM = ROOT / 'drivers/clk/qcom'


def production(name):
    return re.sub(r'^#include[^\n]*\n', '', (QCOM / name).read_text(), flags=re.M)


preamble = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef int8_t s8;
typedef uint16_t u16;
typedef int16_t s16;
typedef int32_t s32;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
#define __iomem
#define EPROBE_DEFER 517
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BIT(b) (1U<<(b))
#define GENMASK(h,l) ((~0U<<(l)) & (~0U>>(31-(h))))
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define WARN_ON(v) (!!(v))
#define EXPORT_SYMBOL_GPL(x)
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p)>=(uintptr_t)-4095)
#define sys_reg(a,b,c,d,e) (e)
#define LINUX_VERSION_CODE 0
struct mutex { pthread_mutex_t value; };
#define DEFINE_MUTEX(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->value,NULL)); }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
#define DEFINE_RAW_SPINLOCK(n) struct mutex n={PTHREAD_MUTEX_INITIALIZER}
#define raw_spin_lock_irqsave(m,f) do { (f)=0;mutex_lock(m); } while(0)
#define raw_spin_unlock_irqrestore(m,f) do { (void)(f);mutex_unlock(m); } while(0)
struct regmap { u32 words[5]; };
typedef unsigned cpumask_t;
static unsigned online_mask=15,current_cpu;
#define cpumask_clear(p) (*(p)=0)
#define cpumask_set_cpu(c,p) (*(p)|=1U<<(c))
#define cpumask_empty(p) (!*(p))
#define for_each_online_cpu(c) for((c)=0;(c)<4;(c)++) if(online_mask & (1U<<(c)))
static void cpus_read_lock(void) {}
static void cpus_read_unlock(void) {}
static u64 cpu_logical_map(unsigned cpu) { return (cpu/2)*256+cpu%2; }
static u64 read_cpuid_mpidr(void) { return cpu_logical_map(current_cpu); }
static int ipi_error;
static int smp_call_function_any(const cpumask_t *mask,void (*fn)(void *),void *data,int wait)
{ unsigned cpu;assert(wait && *mask);if(ipi_error)return ipi_error;
  for(cpu=0;cpu<4;cpu++)if(*mask&(1U<<cpu))break;
  current_cpu=cpu;fn(data);return 0; }
static u32 apcc[2][0x200/4],cbf[0xf040/4],aux;
static struct regmap gcc;
static u64 l2[2][0x600],l2_index;
static unsigned mmio_reads,mmio_writes,sysreg_writes,polls;
static bool lock_fault,offline_fault,active_fault,user_fault,sysreg_fault,gcc_fault;
static void *drop_address,*unstable_address;
static u32 readl_relaxed(const void *address)
{ u32 value=*(const u32 *)address;mmio_reads++;
  if(address==unstable_address)value^=mmio_reads&1;
  return value; }
static void write_event(const void *,u32);
static void writel_relaxed(u32 value,void *address)
{
 unsigned i;bool mode=false,alt=false;mmio_writes++;
 if(address==drop_address)return;
 for(i=0;i<2;i++)if(address==&apcc[i][0] || address==&apcc[i][0x100/4]){
  mode=true;alt=address==&apcc[i][0x100/4];}
 if(address==&cbf[0xf000/4])mode=true;
 if(mode && alt) {
  if(value&BIT(7)) {if(!offline_fault)value|=BIT(28);else value&=~BIT(28);}
  else value&=~BIT(28);
  if(value&BIT(20)) {if(!(value&BIT(7)) && !lock_fault)value|=BIT(31);
                       if(!(value&BIT(7)) && !active_fault)value|=BIT(30);}
  else value&=~BIT(30);
 }
 *(u32 *)address=value;
 for(i=0;i<2;i++)if(address==&apcc[i][4/4]) {if(lock_fault)apcc[i][0]&=~BIT(31);else apcc[i][0]|=BIT(31);}
 if(address==&cbf[0xf008/4]) {if(lock_fault)cbf[0xf000/4]&=~BIT(31);else cbf[0xf000/4]|=BIT(31);}
 if(user_fault)for(i=0;i<2;i++)if(address==&apcc[i][0x110/4])apcc[i][0x110/4]^=BIT(20);
 write_event(address,value);
}
static int regmap_read(struct regmap *map,unsigned off,u32 *value)
{ assert(map==&gcc && !(off%4) && off<=0x10);if(gcc_fault)return -ETIMEDOUT;
  *value=map->words[off/4];return 0; }
static void udelay(unsigned us) { polls+=us; }
static void mb(void) {}
static void rmb(void) {}
static void isb(void) {}
static void sysreg_event(u64 reg,u64 value);
static void write_sysreg_s(u64 value,unsigned reg)
{ if(reg==6)l2_index=value;else {assert(reg==7 && l2_index<0x600);
  l2[current_cpu/2][l2_index]=value;sysreg_writes++;sysreg_event(l2_index,value);} }
static u64 read_sysreg_s(unsigned reg)
{ assert(reg==7);return l2[current_cpu/2][l2_index]^(sysreg_fault?1:0); }
'''
harness = preamble
for name in ['clk-msm8996-cpu-transaction.h', 'clk-msm8996-cpu-hw.h',
             'clk-msm8996-cpu-native.h', 'clk-msm8996-boot.h',
             'clk-msm8996-cpu-transaction.c', 'clk-msm8996-cpu-native.c']:
    harness += production(name)
plans = json.loads((ROOT / 'Documentation/sony/dora-cpu-dt.json').read_text())['frequency_plans']
for d, prefix in enumerate(['pwrcl', 'perfcl', 'cbf']):
    values = plans[f'qcom,{prefix}-speedbin0-v0']
    harness += f'static struct msm8996_cpu_plan source_plan{d}[]={{' + ','.join(
        f'{{{rate}UL,{level}}}' for rate, level in zip(values[::2], values[1::2])) + '};\n'

harness += r'''
static struct msm8996_cpu_native_shared shared;
static struct msm8996_cpu_native nodes[3];
static struct msm8996_cpu_transaction txns[3];
static bool created,guard,power_uncertain,ready_fault,enter_fault;
static unsigned vote_count,vote_fail,lease_level[3][2],firmware_level[3],event_count;
static struct { char op;unsigned domain;unsigned long value; } events[16000];
static void event(char op,unsigned d,unsigned long value)
{ assert(event_count<ARRAY_SIZE(events));events[event_count].op=op;events[event_count].domain=d;
  events[event_count++].value=value; }
static void sysreg_event(u64 reg,u64 value)
{ (void)value;assert(guard);event('S',current_cpu/2,reg); }
static unsigned level(unsigned d)
{ unsigned a=lease_level[d][0],b=lease_level[d][1];return a>b?a:b; }
static void sufficient(unsigned d,unsigned long rate)
{ struct msm8996_cpu_plan p;unsigned held=level(d);
  if(firmware_level[d]>held)held=firmware_level[d];
  assert(!msm8996_cpu_plan_lookup(shared.plans.domain[d],shared.plans.count[d],0,rate,true,&p));
  if(held<p.level)fprintf(stderr,"insufficient d=%u rate=%lu held=%u need=%u\n",d,rate,held,p.level);
  assert(held>=p.level); }
static void write_event(const void *address,u32 value)
{
 unsigned i;unsigned long rate;void *base;
 assert(guard); /* Every actual MMIO write must retain the shared power guard. */
 for(i=0;i<3;i++) {
  base=i==2?(void *)cbf:(void *)apcc[i];
  if(address==base+native_mux_offset(i)) {
   event('M',i,value);
   if(!native_read(&shared,i,&rate))sufficient(i,rate);
  }
  if(address==base+(i==2?0xf008:4))event('L',i,value);
  if(i!=2 && address==base+0x160)event('A',i,value);
  if(i==1 && address==base+0x104)event('a',i,value);
 }
}
static int power_enter(void *data)
{ assert(data==&shared);if(enter_fault)return -EAGAIN;assert(!guard);guard=true;event('G',3,0);return 0; }
static void power_leave(void *data)
{ assert(data==&shared && guard);guard=false;event('g',3,0); }
static int power_vote(void *data,unsigned d,unsigned lease,const struct msm8996_cpu_plan *p,bool enable)
{
 unsigned i;unsigned long rate;assert(data==&shared && guard && d<3 && lease<2);
 event(enable?'V':'v',d,enable?p->level:0);lease_level[d][lease]=enable?p->level:0;
 /* A lost ACK may have applied; tests never call that physical state verified. */
 if(++vote_count==vote_fail){power_uncertain=true;return -ETIMEDOUT;}
 power_uncertain=false;
 for(i=0;i<3;i++)if(!native_read(&shared,i,&rate))sufficient(i,rate);
 return 0;
}
static int power_ready(void *data)
{ assert(data==&shared && guard);event('K',3,0);return ready_fault || power_uncertain?-EIO:0; }
static const struct msm8996_cpu_native_power_ops power_ops={power_enter,power_leave,power_vote,power_ready};
static void initial_pll(unsigned d,unsigned long rate)
{
 u32 *base=d==2?&cbf[0xf000/4]:apcc[d];
 base[0]=LOCK|FSM|7;base[d==2?8/4:4/4]=rate/19200000;
 base[d==2?0x18/4:0x10/4]=BIT(8)|9;
 base[d==2?0x20/4:0x18/4]=0x200d4828;base[d==2?0x24/4:0x1c/4]=6;
 base[d==2?0x30/4:0x20/4]=0x1c000000;
 if(d==2)cbf[0x18/4]=1;else apcc[d][0x40/4]=2;
}
static void fixture(void)
{
 struct msm8996_cpu_native_maps maps={.apcc={apcc[0],apcc[1]},.cbf=cbf,.aux=&aux,.gcc=&gcc,.xo=19200000};
 struct msm8996_cpu_hw_plans p={.domain={source_plan0,source_plan1,source_plan2},
  .count={ARRAY_SIZE(source_plan0),ARRAY_SIZE(source_plan1),ARRAY_SIZE(source_plan2)}};
 unsigned i;struct msm8996_cpu_native_maps invalid;
 if(created) {for(i=0;i<3;i++)assert(!pthread_mutex_destroy(&txns[i].lock.value));
              assert(!pthread_mutex_destroy(&shared.lock.value));}
 /* Host fixture models a new boot, not an unbind/quarantine recovery path. */
 memset(&shared,0,sizeof(shared));memset(nodes,0,sizeof(nodes));memset(txns,0,sizeof(txns));native_owner=NULL;
 memset(apcc,0,sizeof(apcc));memset(cbf,0,sizeof(cbf));memset(l2,0,sizeof(l2));memset(lease_level,0,sizeof(lease_level));
 lock_fault=offline_fault=active_fault=user_fault=sysreg_fault=gcc_fault=power_uncertain=ready_fault=enter_fault=false;
 drop_address=unstable_address=NULL;ipi_error=0;online_mask=15;
 mmio_reads=mmio_writes=sysreg_writes=polls=vote_count=vote_fail=event_count=0;guard=false;
 gcc.words[0]=LOCK|7;gcc.words[1]=31;gcc.words[2]=0;gcc.words[3]=0x40;gcc.words[4]=BIT(24)|BIT(0);
 aux=1U<<16;
 initial_pll(0,652800000);initial_pll(1,652800000);initial_pll(2,672000000);
 apcc[1][0x100/4]=LOCK|ACTIVE|FSM|7;apcc[1][0x104/4]=16;
 apcc[1][0x110/4]=BIT(8)|9;apcc[1][0x118/4]=0x4001051b;
 for(i=0;i<3;i++)firmware_level[i]=i==2?6:5;
 assert(msm8996_cpu_native_shared_init(&shared,&maps,&p,NULL,&shared)==-EPROBE_DEFER && !mmio_writes);
 invalid=maps;invalid.xo=26000000;assert(msm8996_cpu_native_shared_init(&shared,&invalid,&p,&power_ops,&shared)==-EINVAL);
 invalid=maps;invalid.apcc[1]=invalid.apcc[0];assert(msm8996_cpu_native_shared_init(&shared,&invalid,&p,&power_ops,&shared)==-EINVAL);
 assert(!msm8996_cpu_native_shared_init(&shared,&maps,&p,&power_ops,&shared) && !mmio_writes);
 assert(msm8996_cpu_native_shared_init(&shared,&maps,&p,&power_ops,&shared)==-EBUSY);
 assert(!msm8996_cpu_native_bind(&nodes[0],&shared,0,&txns[0]));
 assert(msm8996_cpu_native_bind(&nodes[0],&shared,1,&txns[1])==-EBUSY);
 assert(msm8996_cpu_native_bind(&nodes[1],&shared,1,&txns[0])==-EBUSY);
 assert(nodes[0].domain==0 && txns[0].data==&nodes[0] && !shared.bound[1]);
 for(i=1;i<3;i++)assert(!msm8996_cpu_native_bind(&nodes[i],&shared,i,&txns[i]));
 assert(msm8996_cpu_native_bind(&nodes[0],&shared,0,&txns[0])==-EBUSY);
 created=true;
}
static void boot(unsigned d)
{ assert(!msm8996_cpu_transaction_bootstrap(&txns[d]));firmware_level[d]=0;
  assert(txns[d].initialized && !txns[d].quarantined && !shared.owner && !guard); }
static unsigned long measured(unsigned d)
{ unsigned long rate;assert(!native_read(&shared,d,&rate));return rate; }
static void all_boot(void) { unsigned i;for(i=0;i<3;i++)boot(i); }
static void check_quarantine(unsigned d)
{
 unsigned before=mmio_writes;
 assert(txns[d].quarantined && txns[d].guarded && shared.owner==&nodes[d] && guard);
 assert(msm8996_cpu_transaction_set_rate(&txns[d],307200000)==-EAGAIN);
 assert(native_enter(&nodes[(d+1)%3])==-EBUSY);
 assert(msm8996_cpu_native_suspend(&shared)==-EBUSY);
 assert(mmio_writes==before); /* Neither owner can release or steal it implicitly. */
}
static pthread_barrier_t barrier;
static void *race(void *data)
{
 unsigned d=(uintptr_t)data,i;int ret;
 ret=pthread_barrier_wait(&barrier);assert(!ret || ret==PTHREAD_BARRIER_SERIAL_THREAD);
 for(i=0;i<30;i++) {
  ret=msm8996_cpu_transaction_set_rate(&txns[d],shared.plans.domain[d][1+i%5].rate);
  assert(!ret || ret==-EBUSY);
 }
 return NULL;
}
int main(void)
{
 pthread_t threads[3];int status;
 unsigned d,i,before;unsigned long rate;struct msm8996_cpu_plan p;
 fixture();assert(native_read_rate(&nodes[0],&rate)==-EPERM);
 assert(native_program(&nodes[0],NULL)==-EINVAL);
 all_boot();
 for(d=0;d<3;d++) {
  for(i=1;i<shared.plans.count[d];i++)assert(!msm8996_cpu_transaction_set_rate(&txns[d],shared.plans.domain[d][i].rate));
  for(i=shared.plans.count[d]-1;i;i--)assert(!msm8996_cpu_transaction_set_rate(&txns[d],shared.plans.domain[d][i].rate));
  assert(measured(d)==307200000 && !shared.owner && !guard);
 }
 assert(sysreg_writes && mmio_writes && !shared.owner);
 /* Source ACD order differs between PWR and PERF; CBF never touches L2 ACD. */
 for(i=0;i<event_count;i++)if(events[i].op=='A') {
  assert(events[i].domain<2 && i>=3);
  if(!events[i].domain) {
   assert(events[i-1].op=='S' && events[i-1].value==0x589);
   assert(events[i-2].value==0x584 && events[i-3].value==0x581);
   assert(i+1<event_count && events[i+1].op=='S' && events[i+1].value==0x580);
  } else {
   assert(i>=4 && events[i-1].op=='S' && events[i-1].value==0x580);
   assert(events[i-2].value==0x589 && events[i-3].value==0x584 && events[i-4].value==0x581);
  }
 }
 assert(cbf[0xf030/4]==0x1c000000);
 /* Shared reservation is separate from each engine's lock. */
 fixture();all_boot();assert(!native_enter(&nodes[0]));
 assert(native_enter(&nodes[1])==-EBUSY && native_enter(&nodes[2])==-EBUSY);
 assert(native_read_rate(&nodes[1],&rate)==-EPERM);
 p=(struct msm8996_cpu_plan){652800000,99};assert(native_program(&nodes[0],&p)==-EINVAL);
 before=mmio_writes;apcc[1][0x40/4]=0;assert(native_ready(&nodes[0])==-EIO && mmio_writes==before);
 apcc[1][0x40/4]=2;native_leave(&nodes[0]);
 /* Every persistent failure retains guard + leases and needs explicit recovery. */
 fixture();all_boot();lock_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-ETIMEDOUT);
 check_quarantine(0);lock_fault=false;apcc[0][0]|=LOCK; /* model a confirmed hardware relock */
 assert(!msm8996_cpu_transaction_recover(&txns[0]));assert(measured(0)==652800000);
 fixture();all_boot();drop_address=&apcc[0][0x40/4];assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-EIO);
 check_quarantine(0);drop_address=NULL;assert(!msm8996_cpu_transaction_recover(&txns[0]));
 fixture();all_boot();gcc_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-ETIMEDOUT);
 check_quarantine(0);gcc_fault=false;assert(!msm8996_cpu_transaction_recover(&txns[0]));
 fixture();all_boot();offline_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[1],1324800000)==-ETIMEDOUT);
 check_quarantine(1);offline_fault=false;assert(!msm8996_cpu_transaction_recover(&txns[1]));
 fixture();all_boot();active_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[1],1324800000)==-ETIMEDOUT);
 check_quarantine(1);active_fault=false;assert(!msm8996_cpu_transaction_recover(&txns[1]));
 fixture();all_boot();user_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[1],1324800000)==-EIO);
 check_quarantine(1);user_fault=false;assert(!msm8996_cpu_transaction_recover(&txns[1]));
 fixture();all_boot();sysreg_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-EIO);
 check_quarantine(0);sysreg_fault=false;assert(!msm8996_cpu_transaction_recover(&txns[0]));
 fixture();all_boot();vote_fail=vote_count+1;assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-ETIMEDOUT);
 assert(!txns[0].quarantined && measured(0)==652800000); /* Actual old lease re-ACKs rollback. */
 fixture();all_boot();ready_fault=true;assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-EIO);
 check_quarantine(0);ready_fault=false;assert(!msm8996_cpu_transaction_recover(&txns[0]));
 fixture();all_boot();before=mmio_writes;ipi_error=-ENXIO;
 assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-ENXIO);check_quarantine(0);
 ipi_error=0;assert(!msm8996_cpu_transaction_recover(&txns[0]));assert(mmio_writes>before);
 fixture();all_boot();online_mask=3;assert(msm8996_cpu_transaction_set_rate(&txns[1],1324800000)==-ENODEV);
 check_quarantine(1);online_mask=15;assert(!msm8996_cpu_transaction_recover(&txns[1]));
 fixture();all_boot();assert(!msm8996_cpu_native_suspend(&shared) && shared.frozen && !guard);
 before=mmio_writes;assert(msm8996_cpu_transaction_set_rate(&txns[0],960000000)==-EBUSY);
 apcc[0][4/4]=35;assert(msm8996_cpu_native_resume(&shared)==-EIO && shared.frozen);
 apcc[0][4/4]=34;assert(!msm8996_cpu_native_resume(&shared) && !shared.frozen && before==mmio_writes);
 /* Reject unsupported/unstable firmware state before taking a lease or writing. */
 fixture();apcc[0][0x10/4]|=BIT(12);before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-EIO && !guard && before==mmio_writes);
 fixture();apcc[0][0x18/4]^=1;before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-EOPNOTSUPP && !guard && before==mmio_writes);
 fixture();apcc[1][4/4]=200;before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-ERANGE && !guard && before==mmio_writes);
 fixture();apcc[0][0x20/4]|=BIT(16);before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-EOPNOTSUPP && !guard && before==mmio_writes);
 fixture();cbf[0xf030/4]|=BIT(16);before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[2])==-EOPNOTSUPP && !guard && before==mmio_writes);
 fixture();apcc[0][0x10/4]|=BIT(24);before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-EOPNOTSUPP && !guard && before==mmio_writes);
 fixture();cbf[0xf018/4]=(cbf[0xf018/4]&~GENMASK(9,8))|3U<<8;before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[2])==-EOPNOTSUPP && !guard && before==mmio_writes);
 fixture();unstable_address=&apcc[0][0x40/4];before=mmio_writes;
 assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-EAGAIN && !guard && before==mmio_writes);
 fixture();enter_fault=true;assert(msm8996_cpu_transaction_bootstrap(&txns[0])==-EAGAIN && !guard && !mmio_writes);
 fixture();all_boot();assert(!native_enter(&nodes[1]));apcc[1][0x40/4]=3;
 assert(native_set_alt(&nodes[1],307200000)==-EBUSY);apcc[1][0x40/4]=2;native_leave(&nodes[1]);
 fixture();all_boot();assert(!pthread_barrier_init(&barrier,NULL,4));
 for(d=0;d<3;d++)assert(!pthread_create(&threads[d],NULL,race,(void *)(uintptr_t)d));
 status=pthread_barrier_wait(&barrier);assert(!status || status==PTHREAD_BARRIER_SERIAL_THREAD);
 for(d=0;d<3;d++)assert(!pthread_join(threads[d],NULL));
 assert(!pthread_barrier_destroy(&barrier) && !guard && !shared.owner);
 for(d=0;d<3;d++)assert(!txns[d].quarantined);
 puts("PASS: actual native APCC/CBF/ACD/PLL callbacks + engine, all bin0 rates, shared guard, leases, AUX, rollback, timeouts, readback, PM and unsupported boot state");
 puts("Host boundary models only: no physical CPR/SPM/APM gang guard or hardware/runtime qualification established.");
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='dora-cpu-native-') as tmp:
    c, binary = Path(tmp) / 'native.c', Path(tmp) / 'native'
    c.write_text(harness)
    if os.environ.get("CPU_NATIVE_DEBUG"):
        Path(os.environ["CPU_NATIVE_DEBUG"]).write_text(harness)
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('CPU_NATIVE_SANITIZE') else []
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
                    *flags, str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=60)
