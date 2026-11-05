#!/usr/bin/env python3
"""Execute the actual shared SMD RPM ownership registry in a host fault model.

This validates software owner exclusion and retained tombstones. Claim/release
performs no RPM I/O; an acknowledged request is not a rail measurement.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "drivers/soc/qcom/smd-rpm.c").read_text()
CORNER = (ROOT / "drivers/regulator/qcom-rpm-corner.c").read_text()


def function(name, source=SOURCE):
    match = re.search(r"(?:static )?(?:int|void|bool) " + name + r"\([^;]*?\)\s*\{", source, re.S)
    if not match:
        raise ValueError("missing production registry function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


PRELUDE = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
#define GFP_KERNEL 0
#define EXPORT_SYMBOL_GPL(x)
#define READ_ONCE(v) atomic_load(&(v))
#define ERR_PTR(error) ((void *)(intptr_t)(error))
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p) >= (uintptr_t)-4095)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct module { atomic_uint refs; atomic_bool live; };
static struct module rpm_module = { .refs=ATOMIC_VAR_INIT(0), .live=ATOMIC_VAR_INIT(true) };
static atomic_uint module_gets, module_puts;
#define THIS_MODULE (&rpm_module)
static bool try_module_get(struct module *module)
{
    assert(module==THIS_MODULE);atomic_fetch_add(&module_gets,1);
    if(!atomic_load(&module->live))return false;
    atomic_fetch_add(&module->refs,1);return true;
}
static void module_put(struct module *module)
{
    assert(module==THIS_MODULE && atomic_fetch_sub(&module->refs,1));
    atomic_fetch_add(&module_puts,1);
}
struct mutex { pthread_mutex_t value; };
#define DEFINE_MUTEX(n) struct mutex n = { PTHREAD_MUTEX_INITIALIZER }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for (p=list_entry((h)->next,__typeof__(*p),m); &p->m!=(h); \
         p=list_entry(p->m.next,__typeof__(*p),m))
static void list_add_tail(struct list_head *e, struct list_head *h)
{ e->next=h; e->prev=h->prev; h->prev->next=e; h->prev=e; }
static void list_del(struct list_head *e)
{ e->prev->next=e->next; e->next->prev=e->prev; e->next=e->prev=NULL; }
static void list_del_init(struct list_head *e)
{ list_del(e);e->next=e->prev=e; }
struct kref { atomic_uint count; };
struct device_node { const char *full_name; atomic_uint refs; };
struct device { struct device_node *of_node; atomic_uint refs; };
struct qcom_smd_rpm { struct device *dev; struct kref refs; atomic_bool online; };
static atomic_uint claim_allocations, claim_frees, rpm_frees, device_puts;
static atomic_bool fail_alloc, offline_at_alloc;
static struct qcom_smd_rpm *offline_target, *rpms[64];
static unsigned rpm_count;
static void *kzalloc(size_t size, unsigned flags)
{
    (void)flags;
    if (atomic_load(&fail_alloc)) return NULL;
    if (atomic_exchange(&offline_at_alloc, false)) {
        assert(offline_target); atomic_store(&offline_target->online, false);
    }
    void *p=calloc(1,size); assert(p); atomic_fetch_add(&claim_allocations,1); return p;
}
static void kfree(void *p)
{
    if (!p) return;
    for (unsigned i=0;i<rpm_count;i++) if (rpms[i]==p) {
        rpms[i]=NULL; atomic_fetch_add(&rpm_frees,1); free(p); return;
    }
    atomic_fetch_add(&claim_frees,1); free(p);
}
static void put_device(struct device *dev)
{ assert(atomic_fetch_sub(&dev->refs,1)); atomic_fetch_add(&device_puts,1); }
static void kref_get(struct kref *ref) { assert(atomic_fetch_add(&ref->count,1)); }
static void kref_put(struct kref *ref, void (*release)(struct kref *))
{ unsigned before=atomic_fetch_sub(&ref->count,1); assert(before); if(before==1)release(ref); }
static struct device_node *of_node_get(struct device_node *np)
{ if(np)assert(atomic_fetch_add(&np->refs,1)); return np; }
static void of_node_put(struct device_node *np)
{ if(np)assert(atomic_fetch_sub(&np->refs,1)); }
#define of_node_cmp(a,b) strcasecmp((a),(b))
/* A registry must never use this modeled transport. */
int qcom_rpm_smd_write(struct qcom_smd_rpm *rpm,int state,u32 type,u32 id,void *buf,size_t count)
{ (void)rpm;(void)state;(void)type;(void)id;(void)buf;(void)count;assert(!"registry attempted wire I/O");return -EIO; }
'''

TESTS = r'''
static unsigned claims(void)
{
    struct qcom_rpm_resource_claim *claim; unsigned count=0;
    list_for_each_entry(claim,&qcom_rpm_resource_claims,node) count++;
    return count;
}
static unsigned live_allocations(void)
{ return atomic_load(&claim_allocations)-atomic_load(&claim_frees); }
static struct qcom_smd_rpm *transport(struct device *dev)
{
    struct qcom_smd_rpm *rpm=calloc(1,sizeof(*rpm));assert(rpm && rpm_count<64);
    rpm->dev=dev;atomic_init(&rpm->refs.count,1);atomic_init(&rpm->online,true);
    atomic_fetch_add(&dev->refs,1);rpms[rpm_count++]=rpm;return rpm;
}
static void endpoint(struct device_node *node,struct device *dev,const char *name)
{ node->full_name=name;atomic_init(&node->refs,1);dev->of_node=node;atomic_init(&dev->refs,1); }
static void no_claim(struct qcom_smd_rpm *rpm,u32 type,u32 id,const void *owner,int error)
{
    bool valid=!IS_ERR_OR_NULL(rpm);
    unsigned n=claims(),live=live_allocations(),refs=valid?atomic_load(&rpm->refs.count):0;
    unsigned pins=atomic_load(&rpm_module.refs);
    assert(qcom_rpm_smd_claim(rpm,type,id,owner)==error);
    assert(claims()==n && live_allocations()==live);
    assert(atomic_load(&rpm_module.refs)==pins);
    if(valid)assert(atomic_load(&rpm->refs.count)==refs);
}
enum { NATIVE, CORNER, PHYSICAL, EXTRA, OWNER_COUNT };
static const unsigned owners[OWNER_COUNT];
static void corner_cross_owner(struct qcom_smd_rpm *rpm)
{
    struct rpm_corner_resource resource={.rpm=rpm,.type=0x61706d73,.id=8};
    assert(!qcom_rpm_smd_claim(rpm,resource.type,resource.id,&owners[NATIVE]));
    assert(rpm_corner_claim(&resource)==-EBUSY && !resource.claimed);
    qcom_rpm_smd_release(rpm,resource.type,resource.id,&owners[NATIVE],false);
    assert(!rpm_corner_claim(&resource) && resource.claimed);
    no_claim(rpm,resource.type,resource.id,&owners[PHYSICAL],-EBUSY);
    rpm_corner_unclaim(&resource);assert(!resource.claimed);
    assert(!qcom_rpm_smd_claim(rpm,resource.type,resource.id,&owners[PHYSICAL]));
    qcom_rpm_smd_release(rpm,resource.type,resource.id,&owners[PHYSICAL],false);
}
struct contender { struct qcom_smd_rpm *rpm;u32 id;int result; };
static pthread_barrier_t start;
static void *concurrent_claim(void *data)
{
    struct contender *c=data;int ret=pthread_barrier_wait(&start);
    assert(ret==0 || ret==PTHREAD_BARRIER_SERIAL_THREAD);
    c->result=qcom_rpm_smd_claim(c->rpm,0x61706d73,c->id,c);return NULL;
}
int main(void)
{
    struct device_node a_node,a_reprobe_node,b_node;
    struct device a_dev,a_reprobe_dev,b_dev,no_node_dev={0};
    struct qcom_smd_rpm *a,*a_generation,*a_sibling,*b,*no_node,*retiring;
    unsigned before,ref_count;
    endpoint(&a_node,&a_dev,"/rpm-glink/rpm_requests");
    endpoint(&a_reprobe_node,&a_reprobe_dev,"/rpm-glink/rpm_requests");
    endpoint(&b_node,&b_dev,"/other-rpm/rpm_requests");
    atomic_init(&no_node_dev.refs,1);
    a=transport(&a_dev);a_generation=transport(&a_reprobe_dev);
    a_sibling=transport(&a_reprobe_dev);
    b=transport(&b_dev);no_node=transport(&no_node_dev);
    no_claim(NULL,1,1,&owners[NATIVE],-EINVAL);
    no_claim(ERR_PTR(-ENODEV),1,1,&owners[NATIVE],-EINVAL);
    no_claim(ERR_PTR(-517),1,1,&owners[NATIVE],-EINVAL);
    no_claim(a,1,1,NULL,-EINVAL);no_claim(a,0,1,&owners[NATIVE],-EINVAL);
    atomic_store(&a->online,false);no_claim(a,1,1,&owners[NATIVE],-ENODEV);
    atomic_store(&a->online,true);atomic_store(&fail_alloc,true);
    no_claim(a,1,1,&owners[NATIVE],-ENOMEM);atomic_store(&fail_alloc,false);
    /* Online can be lost between the fast check and registry serialization. */
    offline_target=a;atomic_store(&offline_at_alloc,true);
    no_claim(a,1,1,&owners[NATIVE],-ENODEV);atomic_store(&a->online,true);
    /* Failure to pin an unloading transport module publishes no owner and
       releases the allocation without taking transport or endpoint refs. */
    atomic_store(&rpm_module.live,false);before=atomic_load(&module_gets);
    no_claim(a,1,1,&owners[NATIVE],-ENODEV);
    assert(atomic_load(&module_gets)==before+1 && atomic_load(&a_node.refs)==1);
    atomic_store(&rpm_module.live,true);
    assert(!claims() && !live_allocations() && !atomic_load(&rpm_module.refs));
    corner_cross_owner(a);assert(!claims() && !live_allocations());
    /* Same owner is not a second lease; all provider identities compete. */
    assert(!qcom_rpm_smd_claim(a,0x61706d73,2,&owners[NATIVE]));
    assert(atomic_load(&a->refs.count)==2 && atomic_load(&a_node.refs)==2);
    assert(atomic_load(&rpm_module.refs)==1);
    for(unsigned i=0;i<OWNER_COUNT;i++)
        no_claim(a,0x61706d73,2,&owners[i],-EBUSY);
    no_claim(a_generation,0x61706d73,2,&owners[PHYSICAL],-EBUSY);
    assert(!qcom_rpm_smd_claim(b,0x61706d73,2,&owners[PHYSICAL]));
    assert(!qcom_rpm_smd_claim(a,0x61706d73,3,&owners[CORNER]));
    assert(!qcom_rpm_smd_claim(a,0x626f646c,2,&owners[CORNER]));
    before=claims();ref_count=atomic_load(&a->refs.count);
    assert(atomic_load(&rpm_module.refs)==before);
    qcom_rpm_smd_release(a,0x61706d73,2,&owners[PHYSICAL],false);
    qcom_rpm_smd_release(a_generation,0x61706d73,2,&owners[NATIVE],false);
    qcom_rpm_smd_release(a,0x61706d73,2,NULL,false);
    qcom_rpm_smd_release(NULL,0x61706d73,2,&owners[NATIVE],false);
    qcom_rpm_smd_release(ERR_PTR(-ENODEV),0x61706d73,2,&owners[NATIVE],false);
    assert(claims()==before && atomic_load(&a->refs.count)==ref_count);
    assert(atomic_load(&rpm_module.refs)==before);
    qcom_rpm_smd_release(b,0x61706d73,2,&owners[PHYSICAL],false);
    qcom_rpm_smd_release(a,0x61706d73,3,&owners[CORNER],false);
    qcom_rpm_smd_release(a,0x626f646c,2,&owners[CORNER],false);
    qcom_rpm_smd_release(a,0x61706d73,2,&owners[NATIVE],false);
    assert(!claims() && !live_allocations() && !atomic_load(&rpm_module.refs));
    assert(atomic_load(&a->refs.count)==1 && atomic_load(&a_node.refs)==1);
    /* A pre-I/O owner can retire after transport removal, releasing its last
       transport reference and its endpoint node without leaving a claim. */
    retiring=transport(&b_dev);
    assert(!qcom_rpm_smd_claim(retiring,0x626f646c,77,&owners[NATIVE]));
    atomic_store(&retiring->online,false);qcom_smd_rpm_put(retiring);
    assert(atomic_load(&retiring->refs.count)==1);
    qcom_rpm_smd_release(retiring,0x626f646c,77,&owners[NATIVE],false);
    assert(atomic_load(&rpm_frees)==1 && atomic_load(&device_puts)==1);
    assert(!claims() && !live_allocations() && atomic_load(&b_node.refs)==1);
    assert(!atomic_load(&rpm_module.refs));
    /* Pre-I/O release permits retry; retained/uncertain votes never do. */
    assert(!qcom_rpm_smd_claim(a_generation,0x61706d73,2,&owners[PHYSICAL]));
    qcom_rpm_smd_release(a_generation,0x61706d73,2,&owners[PHYSICAL],false);
    assert(!qcom_rpm_smd_claim(a,0x61706d73,2,&owners[CORNER]));
    /* Retirement interleaves with new owners of the same endpoint. There is
       no unclaimed interval between the live owner and retained tombstone. */
    {
        enum { THREADS=12 };pthread_t threads[THREADS];struct contender c[THREADS];
        int ret;assert(!pthread_barrier_init(&start,NULL,THREADS+1));
        for(unsigned i=0;i<THREADS;i++) {
            c[i]=(struct contender){.rpm=a_generation,.id=2};
            assert(!pthread_create(&threads[i],NULL,concurrent_claim,&c[i]));
        }
        ret=pthread_barrier_wait(&start);assert(ret==0 || ret==PTHREAD_BARRIER_SERIAL_THREAD);
        qcom_rpm_smd_release(a,0x61706d73,2,&owners[CORNER],true);
        for(unsigned i=0;i<THREADS;i++) {
            assert(!pthread_join(threads[i],NULL));
            assert(c[i].result==-EBUSY || c[i].result==-ESHUTDOWN);
        }
        assert(!pthread_barrier_destroy(&start));
    }
    before=live_allocations();assert(before==1 && claims()==1);
    assert(atomic_load(&rpm_module.refs)==1);
    for(unsigned i=0;i<100;i++) {
        no_claim(a,0x61706d73,2,&owners[CORNER],-ESHUTDOWN);
        no_claim(a_generation,0x61706d73,2,&owners[PHYSICAL],-ESHUTDOWN);
        qcom_rpm_smd_release(a,0x61706d73,2,&owners[CORNER],false);
        qcom_rpm_smd_release(a_generation,0x61706d73,2,&owners[CORNER],false);
        assert(claims()==1 && live_allocations()==before);
        assert(atomic_load(&rpm_module.refs)==1);
    }
    /* Driver removal drops only its base ref; tombstone owns transport/node. */
    atomic_store(&a->online,false);qcom_smd_rpm_put(a);
    assert(atomic_load(&a->refs.count)==1 && atomic_load(&a_dev.refs)==2);
    assert(atomic_load(&a_node.refs)==2 && atomic_load(&rpm_frees)==1);
    assert(atomic_load(&rpm_module.refs)==1);
    no_claim(a_generation,0x61706d73,2,&owners[NATIVE],-ESHUTDOWN);
    assert(!qcom_rpm_smd_claim(b,0x61706d73,2,&owners[NATIVE]));
    qcom_rpm_smd_release(b,0x61706d73,2,&owners[NATIVE],false);
    /* No OF node still excludes duplicate owners of the exact live object. */
    assert(!qcom_rpm_smd_claim(no_node,1,0,&owners[NATIVE]));
    no_claim(no_node,1,0,&owners[PHYSICAL],-EBUSY);
    qcom_rpm_smd_release(no_node,1,0,&owners[NATIVE],false);
    /* Many native/corner/physical-like clients race for the same real tuple. */
    for(unsigned round=0;round<20;round++) {
        enum { THREADS=12 };pthread_t threads[THREADS];struct contender c[THREADS];
        unsigned won=0,winner=0;assert(!pthread_barrier_init(&start,NULL,THREADS));
        for(unsigned i=0;i<THREADS;i++) {
            c[i]=(struct contender){.rpm=(i&1)?a_generation:a_sibling,.id=99};
            /* This round shares one endpoint through two transport generations. */
            assert(!pthread_create(&threads[i],NULL,concurrent_claim,&c[i]));
        }
        for(unsigned i=0;i<THREADS;i++) {
            assert(!pthread_join(threads[i],NULL));
            if(!c[i].result){won++;winner=i;}else assert(c[i].result==-EBUSY);
        }
        assert(won==1 && claims()==2 && live_allocations()==2);
        assert(atomic_load(&rpm_module.refs)==2);
        qcom_rpm_smd_release(c[winner].rpm,0x61706d73,99,&c[winner],false);
        assert(claims()==1 && live_allocations()==1);
        assert(atomic_load(&rpm_module.refs)==1);
        assert(!pthread_barrier_destroy(&start));
    }
    qcom_smd_rpm_put(a_generation);qcom_smd_rpm_put(a_sibling);
    qcom_smd_rpm_put(b);qcom_smd_rpm_put(no_node);
    assert(atomic_load(&rpm_frees)==5 && atomic_load(&device_puts)==5);
    assert(atomic_load(&a_reprobe_dev.refs)==1 && atomic_load(&b_dev.refs)==1);
    assert(atomic_load(&a_reprobe_node.refs)==1 && atomic_load(&b_node.refs)==1);
    assert(claims()==1 && live_allocations()==1); /* Deliberate retained tombstone. */
    assert(atomic_load(&rpm_module.refs)==1); /* Tombstone prevents module unload. */
    assert(atomic_load(&module_gets)==atomic_load(&module_puts)+2); /* One failure, one retained pin. */
    puts("PASS: actual shared RPM claim/release: native/corner/physical exclusion, stable endpoint generations,");
    puts("allocation/online/module-pin faults, ERR_PTR rejection, exact-owner release, bounded tombstones,");
    puts("transport/node/module refs and concurrent claims");
    puts("Software registry only: no wire writes, no ACK or physical rail measurement claim.");
    return 0;
}
'''


def main():
    begin = SOURCE.index("struct qcom_rpm_resource_claim {")
    end = SOURCE.index("static int qcom_smd_rpm_match_node", begin)
    registry = SOURCE[begin:end]
    corner_types = r'''
struct rpm_corner_resource {
    struct list_head registry;struct qcom_smd_rpm *rpm;
    u32 type,id;bool claimed,quarantined;unsigned char owned;
};
static LIST_HEAD(rpm_corner_resources);
static DEFINE_MUTEX(rpm_corner_registry_lock);
'''
    harness = (PRELUDE + function("qcom_smd_rpm_release") + function("qcom_smd_rpm_put") +
               registry + corner_types + function("rpm_corner_claim", CORNER) +
               function("rpm_corner_unclaim", CORNER) + TESTS)
    with tempfile.TemporaryDirectory(prefix="dora-rpm-ownership-") as directory:
        path = Path(directory)
        (path / "test.c").write_text(harness)
        subprocess.run(shlex.split(os.environ.get("HOSTCC", "cc")) +
                       ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(path / "test.c"), "-o", str(path / "test")], check=True)
        subprocess.run([str(path / "test")], check=True)


if __name__ == "__main__":
    main()
