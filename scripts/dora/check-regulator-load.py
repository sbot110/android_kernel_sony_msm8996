#!/usr/bin/env python3
"""Execute the production regulator aggregate-load and explicit-replay APIs.

The host model supplies mutexes, consumer lists and a faulting provider. The
aggregate, cached request updates, permissions and replay use core.c unchanged.
These tests establish software behavior, not physical current measurements.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "drivers/regulator/core.c").read_text()


def function(name):
    match = re.search(r"(?:static\s+)?(?:inline\s+)?(?:int|void|bool)\s+" + name +
                      r"\([^;]*?\)\s*\{", SOURCE, re.S)
    if not match:
        raise ValueError("missing production regulator function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (SOURCE[end] == "{") - (SOURCE[end] == "}")
        end += 1
    return SOURCE[match.start():end] + "\n"


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define EXPORT_SYMBOL_GPL(name)
#define REGULATOR_CHANGE_MODE 0x4
#define REGULATOR_CHANGE_DRMS 0x10
#define REGULATOR_MODE_FAST 1
#define REGULATOR_MODE_NORMAL 2
#define REGULATOR_MODE_IDLE 4
#define REGULATOR_MODE_STANDBY 8
#define ERR_PTR(error) ((void *)(intptr_t)(error))
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p)>=(uintptr_t)-4095)
#define rdev_err(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct list_head { struct list_head *next,*prev; };
#define INIT_LIST_HEAD(head) ((head)->next=(head),(head)->prev=(head))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for(p=list_entry((h)->next,__typeof__(*p),m);&p->m!=(h);p=list_entry(p->m.next,__typeof__(*p),m))
static void list_add_tail(struct list_head *entry,struct list_head *head)
{ entry->next=head;entry->prev=head->prev;head->prev->next=entry;head->prev=entry; }
struct task_struct { unsigned unused; };
static _Thread_local struct task_struct task;
#define current (&task)
struct mutex { pthread_mutex_t value;pthread_t owner;bool held; };
static bool mutex_trylock(struct mutex *lock)
{
    int ret=pthread_mutex_trylock(&lock->value);
    assert(ret==0 || ret==EBUSY);
    if(ret)return false;
    lock->held=true;lock->owner=pthread_self();return true;
}
static void mutex_lock_nested(struct mutex *lock,unsigned subclass)
{ (void)subclass;assert(!pthread_mutex_lock(&lock->value));lock->held=true;lock->owner=pthread_self(); }
static void mutex_unlock(struct mutex *lock)
{ assert(lock->held && pthread_equal(lock->owner,pthread_self()));lock->held=false;assert(!pthread_mutex_unlock(&lock->value)); }
static void lockdep_assert_held_once(struct mutex *lock)
{ assert(lock->held && pthread_equal(lock->owner,pthread_self())); }
struct regulator_dev;
struct regulator_ops {
    int (*set_load)(struct regulator_dev *,int);
    unsigned (*get_optimum_mode)(struct regulator_dev *,int,int,int);
    int (*set_mode)(struct regulator_dev *,unsigned);
    unsigned (*get_mode)(struct regulator_dev *);
};
struct regulator_desc { const struct regulator_ops *ops; };
struct regulation_constraints { unsigned valid_ops_mask,valid_modes_mask;int system_load,input_uV; };
struct regulator { struct list_head list;struct regulator_dev *rdev;int uA_load,enabled; };
struct regulator_dev {
    struct list_head consumer_list;struct mutex mutex;
    _Atomic(struct task_struct *) mutex_owner;int ref_cnt;
    struct regulation_constraints *constraints;const struct regulator_desc *desc;
    struct regulator *supply;
};
/* The set_load path must not fall through to voltage/mode programming. */
static int _regulator_get_voltage(struct regulator_dev *rdev)
{ (void)rdev;assert(!"unexpected voltage operation");return -EIO; }
static int regulator_get_voltage(struct regulator *regulator)
{ (void)regulator;assert(!"unexpected supply voltage operation");return -EIO; }
'''

TESTS = r'''
enum { CLIENTS=4, MAX_CALLS=4096 };
static struct regulator_dev provider;
static struct regulation_constraints constraints;
static struct regulator_desc descriptor;
static struct regulator consumers[CLIENTS];
static unsigned calls;
static int load_history[MAX_CALLS],provider_applied_load,provider_error;
static bool partial_apply,initialized;
static atomic_uint in_callback;
static int set_load(struct regulator_dev *rdev,int load)
{
    assert(rdev==&provider);lockdep_assert_held_once(&rdev->mutex);
    assert(atomic_fetch_add(&in_callback,1)==0);
    assert(calls<MAX_CALLS);load_history[calls++]=load;
    sched_yield();
    if(!provider_error || partial_apply)provider_applied_load=load;
    assert(atomic_fetch_sub(&in_callback,1)==1);
    return provider_error;
}
static unsigned unused_mode(struct regulator_dev *rdev,int input,int output,int load)
{ (void)rdev;(void)input;(void)output;(void)load;assert(!"mode fallback entered");return 0; }
static const struct regulator_ops load_ops={.set_load=set_load};
static const struct regulator_ops unsupported_ops={0};
static const struct regulator_ops legacy_mode_ops={.get_optimum_mode=unused_mode};
static void setup(unsigned count,int system_load)
{
    assert(count<=CLIENTS && !atomic_load(&in_callback));
    if(initialized)assert(!pthread_mutex_destroy(&provider.mutex.value));
    memset(&provider,0,sizeof(provider));memset(consumers,0,sizeof(consumers));
    assert(!pthread_mutex_init(&provider.mutex.value,NULL));initialized=true;
    atomic_init(&provider.mutex_owner,NULL);INIT_LIST_HEAD(&provider.consumer_list);
    constraints=(struct regulation_constraints){.valid_ops_mask=REGULATOR_CHANGE_DRMS,.system_load=system_load};
    descriptor=(struct regulator_desc){.ops=&load_ops};
    provider.constraints=&constraints;provider.desc=&descriptor;
    for(unsigned i=0;i<count;i++) {
        consumers[i].rdev=&provider;consumers[i].enabled=i&1;
        list_add_tail(&consumers[i].list,&provider.consumer_list);
    }
    calls=0;provider_error=0;provider_applied_load=0;partial_apply=false;
}
static void initial_requests(void)
{
    setup(3,321);
    assert(!regulator_set_load(&consumers[0],100000));assert(calls==1 && load_history[0]==100321);
    assert(!regulator_set_load(&consumers[1],25000));assert(calls==2 && load_history[1]==125321);
    assert(!regulator_set_load(&consumers[2],5000));assert(calls==3 && load_history[2]==130321);
    assert(!consumers[0].enabled && consumers[1].enabled && !consumers[2].enabled);
}
static void sync_without_mutation(struct regulator *consumer,int result,int expected)
{
    int requests[CLIENTS],enabled[CLIENTS];unsigned before=calls;
    for(unsigned i=0;i<CLIENTS;i++){requests[i]=consumers[i].uA_load;enabled[i]=consumers[i].enabled;}
    assert(regulator_sync_load(consumer)==result);
    assert(calls==before+1 && load_history[before]==expected);
    for(unsigned i=0;i<CLIENTS;i++)assert(consumers[i].uA_load==requests[i] && consumers[i].enabled==enabled[i]);
}
static void sync_rejected(struct regulator *consumer,int error)
{
    int requests[CLIENTS],enabled[CLIENTS];unsigned before=calls;
    for(unsigned i=0;i<CLIENTS;i++){requests[i]=consumers[i].uA_load;enabled[i]=consumers[i].enabled;}
    assert(regulator_sync_load(consumer)==error && calls==before);
    for(unsigned i=0;i<CLIENTS;i++)assert(consumers[i].uA_load==requests[i] && consumers[i].enabled==enabled[i]);
}
struct worker { unsigned index; };
static pthread_barrier_t start;
static void *concurrent_requests(void *data)
{
    struct worker *worker=data;struct regulator *consumer=&consumers[worker->index];
    int ret=pthread_barrier_wait(&start);assert(ret==0 || ret==PTHREAD_BARRIER_SERIAL_THREAD);
    for(unsigned i=1;i<=100;i++) {
        assert(!regulator_set_load(consumer,(worker->index+1)*1000+i));
        if(i%5==0)assert(!regulator_sync_load(consumer));
    }
    return NULL;
}
int main(void)
{
    unsigned before;
    initial_requests();
    sync_without_mutation(&consumers[0],0,130321);
    assert(provider_applied_load==130321);
    /* Core includes disabled consumer requests and system_load exactly once. */
    consumers[0].enabled=1;consumers[1].enabled=0;
    sync_without_mutation(&consumers[2],0,130321);
    constraints.system_load=1234;
    sync_without_mutation(&consumers[1],0,131234);
    /* Withdrawing the last consumer load still leaves system_load once, and
       zero aggregate must also reach the provider on an explicit replay. */
    initial_requests();
    assert(!regulator_set_load(&consumers[0],0) && load_history[calls-1]==30321);
    assert(!regulator_set_load(&consumers[1],0) && load_history[calls-1]==5321);
    assert(!regulator_set_load(&consumers[2],0) && load_history[calls-1]==321);
    sync_without_mutation(&consumers[0],0,321);
    constraints.system_load=0;sync_without_mutation(&consumers[2],0,0);
    /* Actual 4.19 set_load has no identical-request shortcut. On failure the
       request remains cached, while both identical set and sync replay. */
    for(unsigned partial=0;partial<2;partial++) {
        initial_requests();provider_error=-ETIMEDOUT;partial_apply=partial;
        before=calls;assert(regulator_set_load(&consumers[1],40000)==-ETIMEDOUT);
        assert(calls==before+1 && load_history[before]==145321 && consumers[1].uA_load==40000);
        assert(provider_applied_load==(partial?145321:130321));
        provider_error=-EIO;before=calls;
        assert(regulator_set_load(&consumers[1],40000)==-EIO);
        assert(calls==before+1 && load_history[before]==145321 && consumers[1].uA_load==40000);
        provider_error=-ECOMM;sync_without_mutation(&consumers[0],-ECOMM,145321);
        provider_error=0;sync_without_mutation(&consumers[2],0,145321);
        assert(provider_applied_load==145321);
    }
    initial_requests();before=calls;
    sync_rejected(NULL,-EINVAL);
    sync_rejected(ERR_PTR(-ENODEV),-EINVAL);
    constraints.valid_ops_mask=0;
    sync_rejected(&consumers[0],-EOPNOTSUPP);
    assert(consumers[0].uA_load==100000);
    /* Original set_load still caches a request when DRMS permission is absent. */
    assert(!regulator_set_load(&consumers[0],70000) && calls==before);
    assert(consumers[0].uA_load==70000);
    constraints.valid_ops_mask=REGULATOR_CHANGE_DRMS;
    sync_without_mutation(&consumers[0],0,100321);
    descriptor.ops=&unsupported_ops;before=calls;
    sync_rejected(&consumers[0],-EOPNOTSUPP);
    descriptor.ops=&legacy_mode_ops;
    sync_rejected(&consumers[0],-EOPNOTSUPP);
    descriptor.ops=&load_ops;provider.constraints=NULL;
    sync_rejected(&consumers[0],-EOPNOTSUPP);
    provider.constraints=&constraints;
    /* Empty consumer list replays only the platform system load. */
    setup(0,713);regulator_lock(&provider);
    assert(!drms_uA_update(&provider) && calls==1 && load_history[0]==713);
    regulator_unlock(&provider);
    setup(CLIENTS,73);
    {
        pthread_t threads[CLIENTS];struct worker workers[CLIENTS];
        assert(!pthread_barrier_init(&start,NULL,CLIENTS));
        for(unsigned i=0;i<CLIENTS;i++) {
            workers[i].index=i;assert(!pthread_create(&threads[i],NULL,concurrent_requests,&workers[i]));
        }
        for(unsigned i=0;i<CLIENTS;i++)assert(!pthread_join(threads[i],NULL));
        assert(!pthread_barrier_destroy(&start));
    }
    assert(calls==480 && !atomic_load(&in_callback));
    assert(consumers[0].uA_load==1100 && consumers[1].uA_load==2100 &&
           consumers[2].uA_load==3100 && consumers[3].uA_load==4100);
    assert(load_history[calls-1]==10473 && provider_applied_load==10473);
    sync_without_mutation(&consumers[1],0,10473);
    assert(!provider.ref_cnt && !provider.mutex_owner);
    assert(!pthread_mutex_destroy(&provider.mutex.value));
    puts("PASS: actual regulator core consumer SUM + system_load once, disabled requests, failed cached requests,");
    puts("identical set_load replay, sync without mutation, callback errors/permissions and shared-provider serialization");
    puts("Core software/API checks only; provider callbacks are fault models, not current measurements.");
    return 0;
}
'''


def main():
    names = ("regulator_ops_is_valid", "regulator_lock_nested", "regulator_lock",
             "regulator_unlock", "regulator_mode_constrain", "drms_uA_update",
             "regulator_set_load", "regulator_sync_load")
    harness = PRELUDE + "\n".join(function(name) for name in names) + TESTS
    with tempfile.TemporaryDirectory(prefix="dora-regulator-load-") as directory:
        path = Path(directory)
        (path / "test.c").write_text(harness)
        subprocess.run(shlex.split(os.environ.get("HOSTCC", "cc")) +
                       ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(path / "test.c"), "-o", str(path / "test")], check=True)
        subprocess.run([str(path / "test")], check=True)
        # Compile the actual complete consumer header with CONFIG_REGULATOR
        # absent, rather than substituting a successful fake replay stub.
        shim = path / "include/linux"
        shim.mkdir(parents=True)
        (shim / "err.h").write_text("""
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <errno.h>
#define __must_check
#define ERR_PTR(error) ((void *)(intptr_t)(error))
""")
        (path / "stub.c").write_text("""
#include <assert.h>
#include <linux/regulator/consumer.h>
#ifdef CONFIG_REGULATOR
#error This test requires the disabled regulator API branch
#endif
int main(void)
{
    assert(regulator_sync_load(NULL) == -EOPNOTSUPP);
    assert(regulator_sync_load(ERR_PTR(-ENODEV)) == -EOPNOTSUPP);
    assert(regulator_set_load(NULL, 10000) == 0);
    return 0;
}
""")
        subprocess.run(shlex.split(os.environ.get("HOSTCC", "cc")) +
                       ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-I" + str(path / "include"),
                        "-I" + str(ROOT / "include"), str(path / "stub.c"),
                        "-o", str(path / "stub")], check=True)
        subprocess.run([str(path / "stub")], check=True)
        print("PASS: actual !CONFIG_REGULATOR consumer header rejects load replay with EOPNOTSUPP")


if __name__ == "__main__":
    main()
