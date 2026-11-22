#!/usr/bin/env python3
"""Execute the production MSM8996 CPU transaction engine with source fuse plans.

The bounded callback model verifies ordering, real lease-state bookkeeping,
rollback/quarantine and serialization. It is not an APCC/CPR hardware adapter.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
header = (ROOT / 'drivers/clk/qcom/clk-msm8996-cpu-transaction.h').read_text()
source = (ROOT / 'drivers/clk/qcom/clk-msm8996-cpu-transaction.c').read_text()
plans = json.loads((ROOT / 'Documentation/sony/dora-cpu-dt.json').read_text())['frequency_plans']

harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <pthread.h>
#define EXPORT_SYMBOL_GPL(x)
struct mutex { pthread_mutex_t value; };
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->value,NULL)); }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
'''
harness += re.sub(r'^#include[^\n]*\n', '', header, flags=re.M)
harness += re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)
for index, (name, cells) in enumerate(plans.items()):
    pairs = zip(cells[0::2], cells[1::2])
    harness += 'static const struct msm8996_cpu_plan plan_%d[] = {%s};\n' % (
        index, ','.join('{%dUL,%d}' % pair for pair in pairs))
harness += 'struct plan_set {const struct msm8996_cpu_plan *plans;unsigned count,bin;};\n'
harness += 'static const struct plan_set sets[] = {\n'
for index, (name, cells) in enumerate(plans.items()):
    speed_bin = int(re.search(r'speedbin(\d+)', name)[1])
    harness += '{plan_%d,%d,%d},\n' % (index, len(cells) // 2, speed_bin)
harness += '};\n'
harness += r'''
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
struct model {
    unsigned long rate,alt;
    unsigned level[2],steps,fail_at,plan_index;
    bool held[2],ack,guard,fail_forever,partial,read_zero;
    char trace[20000];unsigned trace_len;
};
static struct msm8996_cpu_transaction txn;
static struct model hw;
static bool created;
static int step(struct model *m,char op)
{
    assert(m->trace_len+1<sizeof(m->trace));m->trace[m->trace_len++]=op;
    m->trace[m->trace_len]=0;m->steps++;
    return m->fail_at && (m->steps==m->fail_at ||
           (m->fail_forever && m->steps>=m->fail_at)) ? -EIO:0;
}
static int lookup(void *data,unsigned long rate,bool boot,struct msm8996_cpu_plan *p)
{ struct model *m=data;const struct plan_set *set=&sets[m->plan_index];
  int ret=step(data,'L');return ret?:msm8996_cpu_plan_lookup(set->plans,set->count,set->bin,rate,boot,p); }
static int enter(void *data)
{ struct model *m=data;int ret=step(m,'G');if(ret)return ret;assert(!m->guard);m->guard=true;return 0; }
static void leave(void *data)
{ struct model *m=data;assert(m->guard);m->guard=false;m->trace[m->trace_len++]='U';m->trace[m->trace_len]=0; }
static int read_rate(void *data,unsigned long *rate)
{ struct model *m=data;int ret=step(m,'R');if(!ret)*rate=m->read_zero?0:m->rate;return ret; }
static int safe(void *data,unsigned long rate)
{
    struct model *m=data;int ret=step(m,'S');assert(m->guard && rate<=m->rate);
    if(!ret || m->partial)m->rate=rate;
    return ret;
}
static unsigned voltage_level(struct model *m)
{ unsigned a=m->held[0]?m->level[0]:0,b=m->held[1]?m->level[1]:0;return a>b?a:b; }
static void sufficient(struct model *m,unsigned long rate)
{
    struct msm8996_cpu_plan p;
    const struct plan_set *set=&sets[m->plan_index];
    assert(!msm8996_cpu_plan_lookup(set->plans,set->count,set->bin,rate,true,&p));
    assert(voltage_level(m)>=p.level);
}
static int program(void *data,const struct msm8996_cpu_plan *p)
{
    struct model *m=data;int ret=step(m,'P');
    assert(m->guard && m->ack);sufficient(m,p->rate);
    if(!ret || m->partial)m->rate=p->rate;
    return ret;
}
static int div2(void *data,unsigned long rate)
{
    struct model *m=data;int ret=step(m,'D');assert(m->guard && m->ack && rate<m->rate);
    if(!ret || m->partial)m->rate=rate;
    return ret;
}
static int read_alt(void *data,unsigned long *rate)
{ struct model *m=data;int ret=step(m,'A');if(!ret)*rate=m->alt;return ret; }
static int set_alt(void *data,unsigned long rate)
{ struct model *m=data;int ret=step(m,'a');assert(m->guard&&m->ack);if(!ret||m->partial)m->alt=rate;return ret; }
static int vote(void *data,unsigned lease,const struct msm8996_cpu_plan *p,bool enable)
{
    struct model *m=data;int ret=step(m,enable?'V':'v');assert(m->guard && lease<2);
    if(!ret || m->partial) {
        m->held[lease]=enable;m->level[lease]=enable?p->level:0;
        /* A delivered change must cover the current CPU output. */
        sufficient(m,m->rate);
    }
    m->ack=!ret;
    return ret;
}
static int ready(void *data)
{ struct model *m=data;int ret=step(m,'K');return ret?:m->ack?0:-EAGAIN; }
static const struct msm8996_cpu_transaction_ops ops={
    .lookup=lookup,.enter=enter,.leave=leave,.read_rate=read_rate,.safe=safe,
    .program=program,.div2=div2,.read_alt=read_alt,.set_alt=set_alt,.vote=vote,.power_ready=ready,
};
static void reset(unsigned long rate,bool acd,bool alt)
{
    struct msm8996_cpu_transaction_policy policy={.has_acd=acd,.has_alt_pll=alt,
                                                 .do_half_rate=true,.aux_rate=300000000};
    if(created)assert(!pthread_mutex_destroy(&txn.lock.value));
    memset(&hw,0,sizeof(hw));hw.rate=rate;hw.alt=307200000;hw.plan_index=1;
    assert(!msm8996_cpu_transaction_init(&txn,&ops,&hw,&policy));created=true;
}
static void boot(unsigned long rate,bool acd,bool alt)
{ reset(rate,acd,alt);assert(!msm8996_cpu_transaction_bootstrap(&txn));assert(txn.initialized&&!txn.guarded&&!hw.guard); }
static void clear_events(void) {hw.steps=hw.trace_len=0;hw.trace[0]=0;}
static void stable(unsigned long rate)
{
    assert(txn.initialized && !txn.quarantined && !txn.guarded && !hw.guard);
    assert(txn.active_plan.rate==rate && hw.rate==rate && hw.ack);
    assert(hw.held[txn.active] && !hw.held[txn.active^1]);
    assert(txn.lease[txn.active]==MSM8996_CPU_LEASE_HELD);
    assert(txn.lease[txn.active^1]==MSM8996_CPU_LEASE_EMPTY);sufficient(&hw,rate);
}
static void *thread(void *arg)
{
    unsigned i,offset=(unsigned)(size_t)arg;
    const unsigned long rates[]={307200000,652800000,1190400000,1555200000};
    for(i=0;i<25;i++)assert(!msm8996_cpu_transaction_set_rate(&txn,rates[(i+offset)%4]));
    return NULL;
}
int main(void)
{
    struct msm8996_cpu_plan p,copy[32];unsigned i,j,n,steps;int ret;
    pthread_t threads[3];char *raise,*release,*programmed,*safe_output;
    for(i=0;i<ARRAY_SIZE(sets);i++) {
        const struct plan_set *set=&sets[i];
        for(j=1;j<set->count;j++) {
            assert(!msm8996_cpu_plan_lookup(set->plans,set->count,set->bin,set->plans[j].rate,false,&p));
            assert(p.level==set->plans[j].level);
        }
        assert(msm8996_cpu_plan_lookup(set->plans,set->count,4,set->plans[1].rate,false,&p)==-EINVAL);
        assert(msm8996_cpu_plan_lookup(set->plans,set->count,set->bin,set->plans[set->count-1].rate+1,false,&p)==-ERANGE);
        assert(msm8996_cpu_plan_lookup(set->plans,set->count,set->bin,set->plans[1].rate+1,false,&p)==-EINVAL);
        assert(!msm8996_cpu_plan_lookup(set->plans,set->count,set->bin,set->plans[1].rate+1,true,&p));
        assert(p.level==set->plans[2].level);
    }
    memcpy(copy,plan_1,sizeof(plan_1));copy[2].rate=copy[1].rate;
    assert(msm8996_cpu_plan_lookup(copy,ARRAY_SIZE(plan_1),0,307200000,false,&p)==-EINVAL);
    reset(1190400000,true,true);
    assert(msm8996_cpu_transaction_set_rate(&txn,1555200000)==-EAGAIN && !hw.steps);
    assert(!msm8996_cpu_transaction_bootstrap(&txn));stable(1190400000);
    clear_events();assert(!msm8996_cpu_transaction_set_rate(&txn,1555200000));stable(1555200000);
    safe_output=strchr(hw.trace,'S');raise=strchr(hw.trace,'V');programmed=strchr(hw.trace,'P');release=strchr(hw.trace,'v');
    assert(safe_output && raise && programmed && release && safe_output<raise && raise<programmed && programmed<release);
    assert(hw.alt==556800000);steps=hw.steps;
    clear_events();assert(!msm8996_cpu_transaction_set_rate(&txn,1190400000));stable(1190400000);
    raise=strchr(hw.trace,'V');programmed=strchr(hw.trace,'P');safe_output=strchr(hw.trace,'S');release=strchr(hw.trace,'v');
    assert(raise<programmed && programmed<safe_output && safe_output<release && release<strrchr(hw.trace,'P'));
    assert(hw.alt==307200000);
    clear_events();assert(!msm8996_cpu_transaction_set_rate(&txn,556800000));stable(556800000);
    assert(strchr(hw.trace,'D') && strchr(hw.trace,'D')<strchr(hw.trace,'P'));
    clear_events();assert(!msm8996_cpu_transaction_set_rate(&txn,556800000));stable(556800000);
    assert(!strchr(hw.trace,'V')&&!strchr(hw.trace,'P'));
    /* Every forward callback failure, delivered or not, must retain errno and
       recover the old plan or quarantine with its guard/leases retained. */
    for(n=0;n<2;n++)for(i=1;i<=steps;i++) {
        boot(1190400000,true,true);clear_events();hw.fail_at=i;hw.partial=n;
        ret=msm8996_cpu_transaction_set_rate(&txn,1555200000);
        assert(ret==-EIO);
        if(txn.quarantined) {
            assert(txn.guarded&&hw.guard && txn.last_error==-EIO);
            hw.fail_at=0;assert(!msm8996_cpu_transaction_recover(&txn));
        }
        stable(1190400000);
    }
    boot(1555200000,true,true);clear_events();
    assert(!msm8996_cpu_transaction_set_rate(&txn,1190400000));steps=hw.steps;
    for(n=0;n<2;n++)for(i=1;i<=steps;i++) {
        boot(1555200000,true,true);clear_events();hw.fail_at=i;hw.partial=n;
        assert(msm8996_cpu_transaction_set_rate(&txn,1190400000)==-EIO);
        if(txn.quarantined) {
            assert(txn.guarded&&hw.guard);hw.fail_at=0;
            assert(!msm8996_cpu_transaction_recover(&txn));
        }
        stable(1555200000);
    }
    reset(1190400000,true,true);
    assert(!msm8996_cpu_transaction_bootstrap(&txn));steps=hw.steps;
    for(n=0;n<2;n++)for(i=1;i<=steps;i++) {
        reset(1190400000,true,true);hw.fail_at=i;hw.partial=n;
        assert(msm8996_cpu_transaction_bootstrap(&txn)==-EIO);
        if(txn.quarantined) {
            assert(txn.guarded&&hw.guard);hw.fail_at=0;
            assert(!msm8996_cpu_transaction_recover(&txn));
        }
        if(txn.initialized)stable(1190400000);
        else assert(!txn.guarded&&!hw.guard&&!hw.held[0]&&!hw.held[1]);
    }
    /* Failed rollback must prevent any subsequent rate program until an
       explicit ACK-confirmed reconciliation succeeds. */
    boot(1190400000,true,true);clear_events();hw.fail_at=7;hw.fail_forever=true;hw.partial=true;
    assert(msm8996_cpu_transaction_set_rate(&txn,1555200000)==-EIO);
    assert(txn.quarantined&&txn.guarded&&hw.guard&&txn.last_error==-EIO&&txn.recovery_error);
    n=hw.steps;assert(msm8996_cpu_transaction_set_rate(&txn,307200000)==-EAGAIN && hw.steps==n);
    hw.fail_at=0;hw.fail_forever=false;assert(!msm8996_cpu_transaction_recover(&txn));stable(1190400000);
    assert(msm8996_cpu_transaction_recover(&txn)==-EINVAL);
    /* A firmware XO/invalid source cannot be raised to a guessed AUX rate. */
    reset(19200000,true,false);ret=msm8996_cpu_transaction_bootstrap(&txn);
    assert(ret==-ERANGE && txn.quarantined && !hw.held[0] && !hw.held[1] && hw.rate==19200000);
    reset(1190400000,true,false);hw.plan_index=0;
    assert(!msm8996_cpu_transaction_bootstrap(&txn));
    assert(!msm8996_cpu_transaction_set_rate(&txn,1593600000));stable(1593600000);
    reset(1190400000,false,false);hw.plan_index=2;
    assert(!msm8996_cpu_transaction_bootstrap(&txn));clear_events();
    assert(!msm8996_cpu_transaction_set_rate(&txn,537600000));stable(537600000);
    assert(strchr(hw.trace,'D') && !strchr(hw.trace,'S'));
    boot(1190400000,true,true);clear_events();
    for(i=0;i<3;i++)assert(!pthread_create(&threads[i],NULL,thread,(void *)(size_t)i));
    for(i=0;i<3;i++)assert(!pthread_join(threads[i],NULL));
    stable(txn.active_plan.rate);
    assert(!pthread_mutex_destroy(&txn.lock.value));created=false;
    puts("PASS: production CPU transaction engine: all 12 Sony fuse plans, ACD/lease/PLL order, div2 and alt thresholds");
    puts("PASS: every forward fault, partial apply, rollback quarantine/recovery and concurrent serialized consumers");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-cpu-transaction-') as scratch:
    c, binary = Path(scratch) / 'transaction.c', Path(scratch) / 'transaction'
    c.write_text(harness)
    subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                   ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                    str(c), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host callback model only: hardware adapter unbound; no CPU DVFS, voltage or suspend claim.')
