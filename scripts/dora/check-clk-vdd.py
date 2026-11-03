#!/usr/bin/env python3
"""Execute actual vendor CCF voltage/prepare/rate paths with injected failures.

The bounded host model validates software leases, ACK gating, reference balance
and error propagation. It does not emulate RPM firmware, PLLs or IRQ concurrency.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/clk/clk.c').read_text()
header = (ROOT / 'include/linux/clk-provider.h').read_text()


def function(name):
    match = re.search(r'(?:static\s+)?(?:struct clk_core\s*\*|unsigned long|int|void|bool)\s*' +
                      name + r'\s*\([^;]*?\)\s*\{', source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n', source[match.start():match.end()-1].strip() + ';\n'


common = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
typedef uint64_t u64;
typedef uint8_t u8;
#define GFP_KERNEL 0
#define EPROBE_DEFER 517
#define BIT(n) (1UL<<(n))
#define CLK_SET_RATE_GATE BIT(0)
#define CLK_IS_CRITICAL BIT(1)
#define CLK_OPS_PARENT_ENABLE BIT(2)
#define CLK_SET_RATE_UNGATE BIT(3)
#define CLK_RECALC_NEW_RATES BIT(4)
#define CLK_CHILD_NO_RATE_PROP BIT(5)
#define PRE_RATE_CHANGE 1
#define POST_RATE_CHANGE 2
#define ABORT_RATE_CHANGE 3
#define NOTIFY_DONE 0
#define NOTIFY_STOP_MASK 0x8000
#define NOTIFY_BAD NOTIFY_STOP_MASK
#define EXPORT_SYMBOL_GPL(x)
#define DIV_ROUND_CLOSEST(x,y) (((x)+(y)/2)/(y))
#define max(a,b) ((a)>(b)?(a):(b))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p)||IS_ERR(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define WARN(c,...) (c)
#define WARN_ON(c) (c)
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define lockdep_assert_held(x) ((void)(x))
#define trace_clk_prepare(c) ((void)(c))
#define trace_clk_prepare_complete(c) ((void)(c))
#define trace_clk_unprepare(c) ((void)(c))
#define trace_clk_unprepare_complete(c) ((void)(c))
#define trace_clk_set_rate(c,r) ((void)(c),(void)(r))
#define trace_clk_set_rate_complete(c,r) ((void)(c),(void)(r))
#define trace_clk_set_parent(c,p) ((void)(c),(void)(p))
#define trace_clk_set_parent_complete(c,p) ((void)(c),(void)(p))
struct mutex { int unused; };
static struct mutex prepare_lock,vdd_class_list_lock;
#define mutex_lock(x) ((void)(x))
#define mutex_unlock(x) ((void)(x))
static void clk_prepare_lock(void) {}
static void clk_prepare_unlock(void) {}
struct list_head { struct list_head *next,*prev; };
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define LIST_HEAD(n) struct list_head n={&(n),&(n)}
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
 for(p=list_entry((h)->next,__typeof__(*p),m);&p->m!=(h);p=list_entry(p->m.next,__typeof__(*p),m))
#define list_for_each_entry_safe(p,n,h,m) \
 for(p=list_entry((h)->next,__typeof__(*p),m),n=list_entry(p->m.next,__typeof__(*p),m); \
 &p->m!=(h);p=n,n=list_entry(n->m.next,__typeof__(*n),m))
static void INIT_LIST_HEAD(struct list_head *h) { h->next=h->prev=h; }
static bool list_empty(struct list_head *h) { return h->next==h; }
static void list_add_tail(struct list_head *e,struct list_head *h)
{e->next=h;e->prev=h->prev;h->prev->next=e;h->prev=e;}
#define list_add(e,h) list_add_tail(e,h)
static void list_del(struct list_head *e) { e->prev->next=e->next;e->next->prev=e->prev; }
static void list_del_init(struct list_head *e) {list_del(e);INIT_LIST_HEAD(e);}
struct hlist_node { struct hlist_node *next; };
struct hlist_head { struct hlist_node *first; };
#define hlist_for_each_entry(p,h,m) \
 for(struct hlist_node *_n=(h)->first;_n && ((p)=container_of(_n,__typeof__(*p),m),1);_n=_n->next)
#define hlist_for_each_entry_safe(p,n,h,m) \
 for(struct hlist_node *_it=(h)->first;_it && ((p)=container_of(_it,__typeof__(*p),m),(n)=_it->next,1);_it=(n))
struct regulator { int voltage,enables,sets,syncs,set_error,on_error,off_error,sync_error;
 bool apply_disable_error; };
struct clk_vdd_class;
struct clk_core;
struct clk_hw { struct clk_core *core;unsigned long physical_rate;u8 selector; };
struct clk_ops {int (*prepare)(struct clk_hw *);void (*unprepare)(struct clk_hw *);
 int (*set_rate)(struct clk_hw *,unsigned long,unsigned long);
 int (*set_parent)(struct clk_hw *,u8);u8 (*get_parent)(struct clk_hw *);
 int (*set_rate_and_parent)(struct clk_hw *,unsigned long,unsigned long,u8);};
struct clk_core {
 const char *name;struct clk_vdd_class *vdd_class;int vdd_class_vote,new_vdd_class_vote;
 bool vdd_class_vote_held,new_vdd_class_vote_held,vdd_safety_lease,vdd_class_registered;
 int pending_vdd_action;u64 vdd_transaction;struct list_head rate_change_node;
 unsigned long *rate_max;int num_rate_max;unsigned prepare_count,enable_count,protect_count;
 unsigned long flags,rate,new_rate,req_rate;const struct clk_ops *ops;struct clk_hw *hw;
 struct clk_core *parent,*new_parent,*new_child,*held_old_parent,*parents[3];unsigned num_parents,new_parent_index;
 bool rate_change_failed,parent_state_uncertain,uncertain_rate_prepared,uncertain_rate_enabled;
 struct hlist_head children;struct hlist_node child_node;unsigned notifier_count;
 int pm_refs,pm_error,prepare_error,enable_error,set_error,parent_error;
 bool partial_rate,partial_parent,pre_reject;unsigned prepares,unprepares,sets,pre,post,abort;
};
static LIST_HEAD(clk_rate_change_list);
static LIST_HEAD(clk_handoff_vdd_list);
static unsigned set_rate_nesting_count;
static u64 clk_vdd_transaction_id;
static int clk_vdd_hardware_error;
static bool vdd_class_handoff_completed;
static int alloc_error;
static void *host_zalloc(size_t size) {return alloc_error?NULL:calloc(1,size);}
#define kzalloc(n,f) host_zalloc(n)
#define kfree(p) free(p)
static int regulator_set_voltage(struct regulator *r,int min,int max)
{(void)max;r->sets++;if(r->set_error)return r->set_error;r->voltage=min;return 0;}
static int regulator_enable(struct regulator *r)
{if(r->on_error)return r->on_error;r->enables++;return 0;}
static int regulator_disable(struct regulator *r)
{if(r->off_error){if(r->apply_disable_error)r->enables=0;return r->off_error;}assert(r->enables);r->enables--;return 0;}
static int regulator_sync_voltage(struct regulator *r)
{r->syncs++;return r->sync_error;}
static int clk_pm_runtime_get(struct clk_core *c)
{if(c->pm_error)return c->pm_error;c->pm_refs++;return 0;}
static void clk_pm_runtime_put(struct clk_core *c) {assert(c->pm_refs>0);c->pm_refs--;}
static void clk_core_rate_protect(struct clk_core *c) {c->protect_count++;}
static void clk_core_rate_unprotect(struct clk_core *c) {assert(c->protect_count);c->protect_count--;}
static int clk_core_prepare(struct clk_core *);
static void clk_core_unprepare(struct clk_core *);
static int clk_core_enable_lock(struct clk_core *c)
{
 int ret;if(!c)return 0;if(c->enable_error)return c->enable_error;
 if(!c->enable_count && c->parent){ret=clk_core_enable_lock(c->parent);if(ret)return ret;}
 c->enable_count++;return 0;
}
static void clk_core_disable_lock(struct clk_core *c)
{if(!c)return;assert(c->enable_count);if(!--c->enable_count)clk_core_disable_lock(c->parent);}
static int clk_core_prepare_enable(struct clk_core *c)
{int ret=clk_core_prepare(c);if(ret)return ret;ret=clk_core_enable_lock(c);if(ret)clk_core_unprepare(c);return ret;}
static void clk_core_disable_unprepare(struct clk_core *c)
{clk_core_disable_lock(c);clk_core_unprepare(c);}
static unsigned long clk_enable_lock(void) {return 0;}
static void clk_enable_unlock(unsigned long f) {(void)f;}
static void clk_reparent(struct clk_core *c,struct clk_core *p) {c->parent=p;}
static struct clk_core *clk_core_get_parent_by_index(struct clk_core *c,u8 i)
{return i<c->num_parents?c->parents[i]:NULL;}
static unsigned long clk_recalc(struct clk_core *c,unsigned long parent)
{(void)parent;return c->hw->physical_rate;}
static int __clk_notify(struct clk_core *c,unsigned long event,unsigned long old,unsigned long new)
{(void)old;(void)new;if(event==PRE_RATE_CHANGE){c->pre++;return c->pre_reject?NOTIFY_BAD:NOTIFY_DONE;}
 if(event==POST_RATE_CHANGE)c->post++;
 if(event==ABORT_RATE_CHANGE)c->abort++;
 return NOTIFY_DONE;}
static unsigned long clk_core_req_round_rate_nolock(struct clk_core *c,unsigned long r) {(void)c;return r;}
static unsigned long clk_core_get_rate_nolock(struct clk_core *c) {return c->rate;}
static bool clk_core_rate_is_protected(struct clk_core *c) {return c->protect_count!=0;}
static struct clk_core *clk_calc_new_rates(struct clk_core *,unsigned long);
'''
match = re.search(r'struct clk_vdd_class \{.*?\n\};', header, re.S)
assert match
common += match[0] + '\n'
match = re.search(r'struct clk_handoff_vdd \{.*?\n\};', source, re.S)
assert match
common += match[0] + '\n'
match = re.search(r'enum clk_vdd_action \{.*?\n\};', source, re.S)
assert match
common += match[0] + '\n'
names = ['clk_find_vdd_level','clk_apply_vdd_level','clk_sync_vdd','clk_update_vdd',
 'clk_vote_vdd_level','clk_unvote_vdd_level','clk_vote_rate_vdd','clk_vdd_class_init','clk_vdd_class_deinit','clk_vdd_class_release_handoff',
 'clk_sync_core_vdd','clk_drop_vdd_vote','clk_promote_vdd_vote','clk_finish_vdd_vote','clk_detach_vdd_core',
 'clk_retry_vdd_votes','clk_release_unprepared_vdd','clk_core_prepare','clk_core_unprepare',
 '__clk_set_parent_before','__clk_set_parent_after','clk_reconcile_parent','clk_parent_failed','__clk_set_parent',
 'clk_vote_new_rate_vdd','clk_propagate_rate_change','clk_change_rate','clk_finish_rate_vdd_votes',
 'clk_subtree_rate_failed','clk_tree_rate_failed','clk_core_set_rate_nolock']
for name in names:common += function(name)[1]
for name in names:common += function(name)[0]
common += r'''
static struct clk_core *clk_calc_new_rates(struct clk_core *c,unsigned long r)
{int ret;c->new_rate=r;ret=clk_vote_new_rate_vdd(c);return ret?ERR_PTR(ret):c;}
static int callback_prepare(struct clk_hw *hw)
{struct clk_core *c=hw->core;c->prepares++;return c->prepare_error;}
static void callback_unprepare(struct clk_hw *hw) {hw->core->unprepares++;}
static int callback_rate(struct clk_hw *hw,unsigned long rate,unsigned long parent)
{struct clk_core *c=hw->core;(void)parent;
 if(c->vdd_class && c->vdd_class->num_regulators)assert(c->vdd_class->regulator[0]->voltage >= (rate>100000?2000:1000));
 c->sets++;if(!c->set_error||c->partial_rate)hw->physical_rate=rate;return c->set_error;}
static int callback_parent(struct clk_hw *hw,u8 index)
{struct clk_core *c=hw->core;if(!c->parent_error||c->partial_parent)hw->selector=index;return c->parent_error;}
static unsigned getter_reads;
static u8 callback_get_parent(struct clk_hw *hw) {getter_reads++;return hw->selector;}
static int callback_both(struct clk_hw *hw,unsigned long rate,unsigned long parent,u8 index)
{int ret=callback_parent(hw,index);if(ret)return ret;return callback_rate(hw,rate,parent);}
static const struct clk_ops ops={.prepare=callback_prepare,.unprepare=callback_unprepare,
 .set_rate=callback_rate,.set_parent=callback_parent,.get_parent=callback_get_parent};
static const struct clk_ops combined_ops={.prepare=callback_prepare,.unprepare=callback_unprepare,
 .set_rate_and_parent=callback_both,.get_parent=callback_get_parent};
static unsigned long fmax[]={0,100000,200000};
static int uv[]={0,1000,2000},votes[3];
static struct regulator rail;
static struct regulator *rails[]={&rail};
static struct clk_vdd_class vdd;
static struct clk_core clk,oldp,newp;
static struct clk_hw hw,oldhw,newhw;
static int ack_error,ack_calls,ack_level;
static int typed_sync(struct clk_vdd_class *v,int level)
{ack_calls++;ack_level=level;if(ack_error)return ack_error;
 if(level && v->num_regulators && rail.enables==0)rail.enables=1;
 return 0;}
static void init_clock(struct clk_core *c,struct clk_hw *h,const char *name)
{memset(c,0,sizeof(*c));memset(h,0,sizeof(*h));c->name=name;c->hw=h;h->core=c;
 c->rate=c->new_rate=h->physical_rate=100000;c->ops=&ops;INIT_LIST_HEAD(&c->rate_change_node);}
static void reset(void)
{memset(&vdd,0,sizeof(vdd));memset(&rail,0,sizeof(rail));memset(votes,0,sizeof(votes));
 INIT_LIST_HEAD(&clk_rate_change_list);INIT_LIST_HEAD(&clk_handoff_vdd_list);
 vdd.class_name="test";vdd.regulator=rails;vdd.num_regulators=1;vdd.vdd_uv=uv;
 vdd.level_votes=votes;vdd.num_levels=3;vdd.cur_level=3;vdd.skip_handoff=true;vdd.sync_vdd=typed_sync;
 init_clock(&clk,&hw,"child");init_clock(&oldp,&oldhw,"old");init_clock(&newp,&newhw,"new");
 clk.vdd_class=&vdd;clk.rate_max=fmax;clk.num_rate_max=3;
 getter_reads=0;alloc_error=ack_error=ack_calls=ack_level=0;set_rate_nesting_count=0;clk_vdd_transaction_id=0;clk_vdd_hardware_error=0;
 vdd_class_handoff_completed=false;}
static void prepare(void) {assert(clk_core_prepare(&clk)==0);assert(votes[1]==1 && rail.enables==1 && clk.pm_refs==1);}
int main(void)
{
 reset();assert(clk_vote_vdd_level(&vdd,-1)==-EINVAL && !votes[0]);
 assert(clk_unvote_vdd_level(&vdd,-1)==-EINVAL);
 rail.on_error=-EIO;assert(clk_vote_vdd_level(&vdd,2)==-EIO);
 assert(vdd.error && vdd.cur_level==3 && !votes[2]);
 assert(clk_vote_vdd_level(&vdd,1)==-EIO); /* unknown bootstrap cannot drop */
 rail.on_error=0;assert(clk_vote_vdd_level(&vdd,2)==0 && rail.enables==1 && vdd.cur_level==2);
 assert(clk_unvote_vdd_level(&vdd,2)==0 && !rail.enables);
 reset();clk.parent=&oldp;rail.set_error=-EPROBE_DEFER;
 assert(clk_core_prepare(&clk)==-EPROBE_DEFER && !clk.pm_refs && !oldp.pm_refs && !oldp.prepare_count);
 reset();prepare();assert(clk_core_set_rate_nolock(&clk,200000)==0 && hw.physical_rate==200000 && votes[2]==1 && !votes[1]);
 rail.set_error=-EIO;assert(clk_core_set_rate_nolock(&clk,100000)==-EIO && hw.physical_rate==100000 && votes[2]==1 && votes[1]==1);
 rail.set_error=0;assert(clk_core_set_rate_nolock(&clk,100000)==0 && votes[1]==1 && !votes[2]);
 clk_core_unprepare(&clk);assert(!rail.enables && !clk.pm_refs);
 reset();prepare();clk.notifier_count=1;clk.pre_reject=true;
 assert(clk_core_set_rate_nolock(&clk,200000)==-EBUSY && !clk.sets && clk.abort==1 && votes[1]==1 && !votes[2]);
 clk.pre_reject=false;clk.set_error=-ETIMEDOUT;clk.partial_rate=true;
 assert(clk_core_set_rate_nolock(&clk,200000)==-ETIMEDOUT && clk.rate_change_failed && hw.physical_rate==200000);
 assert(votes[2]==1 && !votes[1] && clk.vdd_safety_lease);
 clk.set_error=0;assert(clk_core_set_rate_nolock(&clk,100000)==0 && hw.physical_rate==100000 && votes[1]==1 && !votes[2]);
 reset();clk.set_error=-EIO;clk.partial_rate=true;
 assert(clk_core_set_rate_nolock(&clk,200000)==-EIO && !clk.prepare_count && votes[2]==1 && clk.vdd_safety_lease);
 clk.set_error=0;assert(clk_core_set_rate_nolock(&clk,100000)==0 && !votes[2] && !votes[1] && !rail.enables);
 reset();prepare();rail.off_error=-EIO;rail.apply_disable_error=true;vdd.sync_vdd=NULL;
 clk_core_unprepare(&clk);assert(vdd.error && vdd.enable_uncertain && clk.vdd_class_vote_held && votes[1]==1);
 assert(clk_core_prepare(&clk)==-EIO); /* UV replay cannot prove SWEN */
 rail.off_error=0;rail.apply_disable_error=false;vdd.sync_vdd=typed_sync;rail.enables=1;
 assert(clk_core_prepare(&clk)==0 && votes[1]==1 && rail.enables==1);
 reset();prepare();clk.flags=CLK_OPS_PARENT_ENABLE;clk.parent=&oldp;
 /* Establish the pre-existing parent's normal prepare/enable relationship. */
 assert(clk_core_prepare(&oldp)==0);oldp.enable_count=1;clk.enable_count=1;
 clk.parents[0]=&oldp;clk.parents[1]=&newp;clk.num_parents=2;hw.selector=0;
 newp.prepare_error=-EAGAIN;assert(__clk_set_parent(&clk,&newp,1)==-EAGAIN && clk.parent==&oldp);
 assert(!newp.prepare_count && !newp.enable_count && oldp.prepare_count==1 && oldp.enable_count==1);
 newp.prepare_error=0;clk.parent_error=-EIO;clk.partial_parent=true;
 assert(__clk_set_parent(&clk,&newp,1)==-EIO && clk.parent==&newp && !clk.parent_state_uncertain);
 assert(!oldp.prepare_count && !oldp.enable_count && newp.prepare_count==1 && newp.enable_count==1);
 reset();prepare();clk.ops=&combined_ops;clk.flags=CLK_OPS_PARENT_ENABLE;clk.parent=&oldp;
 assert(clk_core_prepare(&oldp)==0);oldp.enable_count=clk.enable_count=1;
 clk.parents[0]=&oldp;clk.parents[1]=&newp;clk.num_parents=2;hw.selector=0;
 clk.new_parent=&newp;clk.new_parent_index=1;clk.parent_error=-EIO;
 assert(clk_core_set_rate_nolock(&clk,200000)==-EIO && clk.parent==&oldp && votes[2]==1);
 clk.parent_error=0;assert(clk_core_set_rate_nolock(&clk,200000)==0 && clk.parent==&newp && !clk.vdd_safety_lease);
 /* An invalid selector on an orphan must never confirm NULL old parent. */
 reset();clk.num_parents=1;clk.parents[0]=&newp;clk.parent_error=-EIO;hw.selector=7;
 assert(__clk_set_parent(&clk,&newp,0)==-EIO && clk.parent_state_uncertain);
 assert(clk_reconcile_parent(&clk)==-EIO);
 {unsigned reads=getter_reads;clk.pm_error=-EPROBE_DEFER;assert(clk_reconcile_parent(&clk)==-EPROBE_DEFER && getter_reads==reads);}
 clk.pm_error=0;hw.selector=0;assert(clk_reconcile_parent(&clk)==0 && !clk.pm_refs);
 reset();clk.flags=CLK_SET_RATE_UNGATE;clk.set_error=-EIO;clk.partial_rate=true;
 assert(clk_core_set_rate_nolock(&clk,200000)==-EIO && votes[2]==1 && clk.vdd_safety_lease && !clk.pm_refs);
 assert(clk_core_prepare(&clk)==0 && votes[2]==1 && !votes[1]);
 clk.set_error=0;assert(clk_core_set_rate_nolock(&clk,100000)==0 && votes[1]==1 && !votes[2]);
 /* A rejected ACK, even for a cached level, blocks all hardware writes. */
 reset();prepare();ack_error=-ETIMEDOUT;
 assert(clk_core_set_rate_nolock(&clk,75000)==-ETIMEDOUT && !clk.sets);
 ack_error=0;assert(clk_core_set_rate_nolock(&clk,75000)==0 && ack_calls>1);
 /* Failed release survives until real retry; unregister detaches global node. */
 reset();prepare();rail.off_error=-EIO;clk_core_unprepare(&clk);
 assert(!list_empty(&clk_rate_change_list) && clk.vdd_class_vote_held);
 clk_detach_vdd_core(&clk);assert(list_empty(&clk_rate_change_list) && vdd.orphaned && votes[1]==1);
 rail.off_error=0;assert(clk_vote_vdd_level(&vdd,2)==-ESHUTDOWN);
 reset();vdd.skip_handoff=false;alloc_error=1;
 assert(clk_vdd_class_init(&vdd)==-ENOMEM && !votes[2] && !rail.enables);
 alloc_error=0;assert(clk_vdd_class_init(&vdd)==0 && vdd.num_clocks==1 && votes[2]==1);
 clk_vdd_class_deinit(&vdd);assert(list_empty(&clk_handoff_vdd_list) && vdd.orphaned && votes[2]==1);
 reset();vdd.skip_handoff=false;vdd.require_handoff=true;vdd_class_handoff_completed=true;
 assert(clk_vdd_class_init(&vdd)==0 && votes[2]==1 && vdd.cur_level==2);
 assert(clk_core_prepare(&clk)==0 && votes[1]==1 && votes[2]==1 && rail.voltage==2000);
 rail.set_error=-EIO;assert(clk_vdd_class_release_handoff(&vdd)==-EIO && votes[2]==1 && !list_empty(&clk_handoff_vdd_list));
 rail.set_error=0;assert(clk_vdd_class_release_handoff(&vdd)==0 && !votes[2] && votes[1]==1 && list_empty(&clk_handoff_vdd_list));
 reset();vdd.skip_handoff=false;vdd.require_handoff=true;assert(clk_vdd_class_init(&vdd)==0);
 assert(clk_vdd_class_release_handoff(&vdd)==0 && votes[2]==1 && !list_empty(&clk_handoff_vdd_list));
 clk_vdd_class_deinit(&vdd);
 puts("PASS: actual CCF leases, ACK quarantine, bootstrap sentinel, PM balance, unprepared writes, mux readback, notifier abort and teardown");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-clk-vdd-') as scratch:
    c,binary=Path(scratch)/'ccf.c',Path(scratch)/'ccf'
    c.write_text(common)
    subprocess.run([os.environ.get('HOSTCC','cc'),'-std=gnu11','-O1','-Wall','-Wextra','-Werror',
                    '-Wno-unused-function','-Wno-unused-parameter','-Wno-unused-value',*(['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if os.environ.get('CCF_TEST_SANITIZE') else []),str(c),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
print('Host state model only; physical RPM/PLL behavior and IRQ races remain unverified.')
