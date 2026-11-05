#!/usr/bin/env python3
"""Execute the production logical-corner transaction code in a bounded host model.

The real native SMD ACK parser/write tests remain check-rpm-bus.py. This model
injects their returned errors, including applied-but-unacknowledged requests;
it does not communicate with RPM hardware or prove voltage measurements.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/regulator/qcom-rpm-corner.c').read_text()


def function(name, code=source):
    match = re.search(r'(?:static )?(?:int|void|bool) ' + name + r'\s*\([^;]*?\)\s*\{', code, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (code[end] == '{') - (code[end] == '}')
        end += 1
    return code[match.start():end] + '\n'


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
typedef uint32_t u32;
typedef uint8_t u8;
typedef uint32_t __le32;
#define BIT(x) (1U<<(x))
#define EPROBE_DEFER 517
#define GFP_KERNEL 0
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define PTR_ERR(x) ((int)(intptr_t)(x))
#define REGULATOR_ERROR_FAIL BIT(4)
#define QCOM_SMD_RPM_ACTIVE_STATE 0
#define QCOM_SMD_RPM_SLEEP_STATE 1
#define max(a,b) ((a)>(b)?(a):(b))
#define cpu_to_le32(x) (x)
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct mutex { pthread_mutex_t value; };
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->value)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->value)); }
struct list_head { struct list_head *next,*prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
#define DEFINE_MUTEX(n) struct mutex n = { PTHREAD_MUTEX_INITIALIZER }
#define INIT_LIST_HEAD(h) ((h)->next=(h),(h)->prev=(h))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for(p=list_entry((h)->next,__typeof__(*p),m);&p->m!=(h);p=list_entry(p->m.next,__typeof__(*p),m))
static void list_add_tail(struct list_head *e,struct list_head *h)
{ e->next=h;e->prev=h->prev;h->prev->next=e;h->prev=e; }
static void list_del_init(struct list_head *e)
{ e->prev->next=e->next;e->next->prev=e->prev;INIT_LIST_HEAD(e); }
struct kref { unsigned count; };
struct device { void *data;void *release_record; };
struct device_node { unsigned refs; };
struct regulator_ops { int unused; };
struct regulator_desc { const struct regulator_ops *ops;unsigned n_voltages,linear_min_sel;int min_uV,uV_step; };
struct regulator_dev { struct device dev;struct regulator_desc *desc;void *data;unsigned refs;void (*put)(void *);void *put_data; };
struct regulator { struct regulator_dev *rdev; };
struct regulator_config { struct device *dev;struct device_node *of_node;struct regulator_init_data *init_data;void *driver_data; };
static void *rdev_get_drvdata(struct regulator_dev *rdev) { return rdev->data; }
static void *regulator_get_drvdata(struct regulator *reg) { return reg->rdev->data; }
static void *dev_get_drvdata(struct device *dev) { return dev->data; }
static const struct regulator_ops rpm_corner_ops;
struct qcom_smd_rpm { int unused; };
static struct qcom_smd_rpm endpoint;
/* This checker models the shared-registry boundary. Its real implementation,
   refs, generation identity and concurrency run in check-rpm-ownership.py. */
static struct { struct qcom_smd_rpm *rpm;u32 type,id;const void *owner;bool held,preserved; } claims[8];
static unsigned preserved_claims,common_claims,common_releases;
static int common_claim_error;
static int qcom_rpm_smd_claim(struct qcom_smd_rpm *rpm,u32 type,u32 id,const void *owner)
{
    unsigned i;
    assert(rpm && owner && type);
    if(common_claim_error)return common_claim_error;
    for(i=0;i<8;i++)if(claims[i].held && claims[i].rpm==rpm && claims[i].type==type && claims[i].id==id)
        return claims[i].preserved?-ESHUTDOWN:-EBUSY;
    for(i=0;i<8;i++)if(!claims[i].held) {
        claims[i].rpm=rpm;claims[i].type=type;claims[i].id=id;claims[i].owner=owner;
        claims[i].held=true;common_claims++;return 0;
    }
    assert(!"registry boundary model exhausted");return -ENOMEM;
}
static void qcom_rpm_smd_release(struct qcom_smd_rpm *rpm,u32 type,u32 id,const void *owner,bool preserve)
{
    for(unsigned i=0;i<8;i++)if(claims[i].held && claims[i].rpm==rpm && claims[i].type==type &&
                               claims[i].id==id && claims[i].owner==owner) {
        common_releases++;
        if(preserve) { claims[i].preserved=true;claims[i].owner=NULL;preserved_claims++; }
        else memset(&claims[i],0,sizeof(claims[i]));
        return;
    }
    assert(!"release of absent registry boundary claim");
}
static unsigned sends, fails[8192], applied_failed, freed_resources, freed_arrays;
static u32 physical[2][3];
static unsigned masks[8192], states[8192];
static u32 sent[8192][3];
static int qcom_rpm_smd_write(struct qcom_smd_rpm *, int, u32, u32, void *, size_t);

'''
harness += '\n'.join(l for l in source.splitlines() if l.startswith('#define RPM_CORNER_'))+'\n'
harness += source[source.index('enum rpm_corner_key'):source.index('/* All aggregation/')]
for name in ('rpm_corner_aggregate', 'rpm_corner_send', 'rpm_corner_dominates',
             'rpm_corner_write_pair', 'rpm_corner_apply', 'rpm_corner_ready'):
    harness += function(name)
harness += 'enum rpm_corner_action { SET_CORNER, SET_ENABLE, SET_DISABLE };\n'
for name in ('rpm_corner_change', 'rpm_corner_set_voltage', 'rpm_corner_get_voltage',
             'rpm_corner_enable', 'rpm_corner_disable', 'rpm_corner_is_enabled',
             'rpm_corner_error_flags', 'qcom_rpm_corner_sync'):
    harness += function(name)
harness += r'''
static void of_node_put(struct device_node *node) { if(node) { assert(node->refs);node->refs--; } }
static void kfree(void *p) { if(p) { if(freed_resources) freed_arrays++;else freed_resources++; } free(p); }
static void kref_get(struct kref *ref) { assert(ref->count);ref->count++; }
static void kref_put(struct kref *ref,void (*release)(struct kref *)) { assert(ref->count);if(!--ref->count)release(ref); }
'''
harness += function('rpm_corner_release') + function('rpm_corner_put') + function('rpm_corner_ref_release')
harness += r'''
struct host_devres { void (*release)(struct device *,void *); };
static bool fail_alloc,fail_registration;
static unsigned registrations,allocations,releases;
static struct regulator_dev published_rdev;
static void *devres_alloc(void (*release)(struct device *,void *),size_t size,unsigned flags)
{
    struct host_devres *record;
    (void)flags;if(fail_alloc)return NULL;
    record=calloc(1,sizeof(*record)+size);assert(record);record->release=release;allocations++;
    return record+1;
}
static void devres_free(void *data) { assert(data);releases++;free((struct host_devres *)data-1); }
static void devres_add(struct device *dev,void *data) { assert(!dev->release_record);dev->release_record=data; }
static struct regulator_dev *regulator_register(struct regulator_desc *desc,struct regulator_config *config)
{
    struct rpm_corner_client *client=config->driver_data;
    if(fail_registration)return ERR_PTR(-EIO);
    registrations++;
    memset(&published_rdev,0,sizeof(published_rdev));published_rdev.desc=desc;
    published_rdev.data=client;published_rdev.refs=2; /* Registration plus raced consumer. */
    assert(rpm_corner_change(client,SET_CORNER,5)==-EPROBE_DEFER);
    return &published_rdev;
}
'''
harness += function('rpm_corner_register_one')
harness += r'''
static void rdev_put(struct regulator_dev *rdev)
{
    assert(rdev->refs);
    if(!--rdev->refs) {
        if(rdev->dev.release_record) {
            void *data=rdev->dev.release_record;
            struct host_devres *record=(struct host_devres *)data-1;
            record->release(&rdev->dev,data);devres_free(data);rdev->dev.release_record=NULL;
        } else if(rdev->put) rdev->put(rdev->put_data);
    }
}
static void regulator_unregister(struct regulator_dev *rdev) { rdev_put(rdev); }
'''
harness += function('regulator_list_voltage_linear', (ROOT / 'drivers/regulator/helpers.c').read_text())
harness += function('rpm_corner_unregister')+function('rpm_corner_suspend')+function('rpm_corner_resume')
harness += r'''
static int qcom_rpm_smd_write(struct qcom_smd_rpm *rpm,int state,u32 type,u32 id,void *data,size_t size)
{
    struct kvp { u32 key,len,value; } kvp[3];
    assert(size<=sizeof(kvp));memcpy(kvp,data,size);
    unsigned i,key,mask=0,call=sends++;
    assert(rpm==&endpoint && (state==0||state==1) && type==0x61706d73 && id==1);
    assert(size && size%12==0 && size<=36 && call<8192);
    for(i=0;i<size/12;i++) {
        for(key=0;key<3;key++)if(kvp[i].key==rpm_corner_keys[key])break;
        assert(key<3 && kvp[i].len==4 && !(mask&BIT(key)));
        assert(kvp[i].value<=(key==ENABLE?1U:6U));
        mask|=BIT(key);sent[call][key]=kvp[i].value;
        if(!fails[call]||applied_failed)physical[state][key]=kvp[i].value;
    }
    states[call]=state;masks[call]=mask;
    return -(int)fails[call];
}
static struct rpm_corner_resource resource;
static struct rpm_corner_client clients[3];
static struct regulator_dev rdev[3];
static struct regulator regs[3];
static struct device device;
static void reset(void)
{
    unsigned i;
    memset(&resource,0,sizeof(resource));memset(clients,0,sizeof(clients));memset(rdev,0,sizeof(rdev));
    memset(fails,0,sizeof(fails));memset(sent,0,sizeof(sent));memset(masks,0,sizeof(masks));
    memset(physical,0xa5,sizeof(physical));sends=applied_failed=0;
    assert(!pthread_mutex_init(&resource.lock.value,NULL));
    INIT_LIST_HEAD(&resource.registry);resource.rpm=&endpoint;resource.type=0x61706d73;resource.id=1;resource.count=3;resource.clients=clients;resource.online=true;
    device.data=&resource;
    for(i=0;i<3;i++) {
        clients[i].resource=&resource;clients[i].sets=RPM_CORNER_BOTH;clients[i].key=CORNER;
        rdev[i].data=&clients[i];rdev[i].desc=&clients[i].desc;clients[i].desc.ops=&rpm_corner_ops;clients[i].desc.n_voltages=7;clients[i].desc.min_uV=1;clients[i].desc.uV_step=1;
        regs[i].rdev=&rdev[i];
    }
    clients[1].sets=RPM_CORNER_ACTIVE;clients[2].key=FLOOR;clients[2].always_send=true;
}
static void done(void) { assert(!pthread_mutex_destroy(&resource.lock.value)); }
static void bootstrap(void)
{
    assert(rpm_corner_get_voltage(&rdev[0])==-ENODATA);
    assert(rpm_corner_set_voltage(&rdev[0],5,7,NULL)==0 && !sends);
    assert(qcom_rpm_corner_sync(&regs[0],true)==-EAGAIN && !sends);
    assert(rpm_corner_enable(&rdev[0])==0 && sends==2);
    assert(rpm_corner_is_enabled(&rdev[0])==1);
    assert(physical[0][CORNER]==4 && physical[1][CORNER]==4 && physical[0][ENABLE]==1 && physical[1][ENABLE]==1);
}
static void *contend(void *arg)
{
    unsigned i,index=(uintptr_t)arg;
    for(i=0;i<250;i++) {
        assert(rpm_corner_set_voltage(&rdev[index],index?7:5,7,NULL)==0);
        assert(rpm_corner_enable(&rdev[index])==0);
        assert(qcom_rpm_corner_sync(&regs[index],true)==0);
    }
    return NULL;
}
int main(void)
{
    unsigned before,flags,i,selector;
    pthread_t first,second;
    struct rpm_corner_resource *heap;
    struct rpm_corner_client *held;
    struct device provider={0};
    struct device_node nodes[2]={{1},{1}};
    struct regulator_ops foreign={1};
    reset();bootstrap();
    selector=99;
    assert(rpm_corner_set_voltage(&rdev[0],5,7,&selector)==0 && selector==4);
    assert(regulator_list_voltage_linear(&rdev[0],selector)==5);
    assert(regulator_list_voltage_linear(&rdev[0],7)==-EINVAL);
    selector=99;assert(rpm_corner_set_voltage(&rdev[0],0,7,&selector)==-EINVAL && selector==99);
    before=sends;assert(qcom_rpm_corner_sync(&regs[0],true)==0 && sends==before+2);
    assert(rpm_corner_set_voltage(&rdev[0],0,7,NULL)==-EINVAL);
    assert(rpm_corner_set_voltage(&rdev[0],8,9,NULL)==-EINVAL);
    assert(rpm_corner_set_voltage(&rdev[0],6,5,NULL)==-EINVAL);
    assert(rpm_corner_set_voltage(&rdev[1],7,7,NULL)==0);
    assert(physical[0][CORNER]==6 && physical[1][CORNER]==4);
    assert(rpm_corner_enable(&rdev[1])==0 && rpm_corner_disable(&rdev[1])==0);
    assert(physical[0][CORNER]==6); /* Stored disabled corner still aggregates. */
    assert(rpm_corner_set_voltage(&rdev[1],1,7,NULL)==0 && physical[0][CORNER]==4);
    assert(rpm_corner_set_voltage(&rdev[2],4,7,NULL)==0);
    before=sends;assert(qcom_rpm_corner_sync(&regs[2],false)==0 && sends==before+2);
    assert(physical[0][FLOOR]==3 && physical[1][FLOOR]==3 && physical[0][CORNER]==4);
    assert(rpm_corner_set_voltage(&rdev[2],1,7,NULL)==0 && physical[0][FLOOR]==0);
    clients[2].desc.ops=&foreign;before=sends;
    assert(qcom_rpm_corner_sync(&regs[2],false)==-EOPNOTSUPP && sends==before);
    done();
    /* Applied-but-unacknowledged ACTIVE/SLEEP, plus explicit ACK error/offline. */
    for(i=0;i<4;i++) {
        reset();bootstrap();before=sends;applied_failed=1;
        fails[before+(i&1)]=(i==0?ETIMEDOUT:i==1?EINVAL:i==2?ENODEV:EIO);
        assert(rpm_corner_set_voltage(&rdev[0],7,7,NULL)==-(int)fails[before+(i&1)]);
        assert(clients[0].request.corner==4 && !resource.quarantined);
        assert(physical[0][CORNER]==4 && physical[1][CORNER]==4);
        assert(qcom_rpm_corner_sync(&regs[0],true)==0);
        done();
    }
    reset();bootstrap();before=sends;applied_failed=1;
    fails[before+1]=ETIMEDOUT;fails[before+2]=EIO; /* Sleep failure, active rollback failure. */
    assert(rpm_corner_set_voltage(&rdev[0],7,7,NULL)==-ETIMEDOUT && resource.quarantined);
    assert(!rpm_corner_error_flags(&rdev[0],&flags) && flags==REGULATOR_ERROR_FAIL);
    fails[sends]=ENODEV;
    assert(qcom_rpm_corner_sync(&regs[0],true)==-ENODEV && resource.quarantined);
    assert(qcom_rpm_corner_sync(&regs[0],true)==0 && !resource.quarantined);
    assert(physical[0][CORNER]==4 && physical[1][CORNER]==4);
    done();
    /* First failed bootstrap has no assumed physical baseline. Hold max and
       require an explicit dominating enabled request before ownership. */
    reset();assert(rpm_corner_set_voltage(&rdev[0],5,7,NULL)==0);
    fails[1]=ETIMEDOUT;applied_failed=1;
    assert(rpm_corner_enable(&rdev[0])==-ETIMEDOUT && resource.quarantined && resource.initial_uncertain);
    assert(!clients[0].request.enable_valid && physical[0][CORNER]==4 && physical[0][ENABLE]==1);
    before=sends;assert(qcom_rpm_corner_sync(&regs[0],true)==-EAGAIN && sends==before);
    assert(rpm_corner_set_voltage(&rdev[0],1,7,NULL)==-EUCLEAN);
    assert(clients[0].request.corner==4);
    assert(rpm_corner_enable(&rdev[0])==0 && !resource.quarantined);
    assert(qcom_rpm_corner_sync(&regs[0],true)==0);
    done();
    reset();bootstrap();assert(rpm_corner_suspend(&device)==0 && resource.suspended);
    assert(rpm_corner_set_voltage(&rdev[0],7,7,NULL)==-EBUSY);
    fails[sends]=ENODEV;
    assert(rpm_corner_resume(&device)==-ENODEV && resource.suspended);
    assert(rpm_corner_resume(&device)==0 && !resource.suspended);
    resource.online=false;resource.retired=true;before=sends;
    assert(qcom_rpm_corner_sync(&regs[0],true)==-ENODEV && sends==before);
    done();
    reset();bootstrap();assert(!pthread_create(&first,NULL,contend,(void *)(uintptr_t)0));
    assert(!pthread_create(&second,NULL,contend,(void *)(uintptr_t)1));
    assert(!pthread_join(first,NULL) && !pthread_join(second,NULL));
    assert(physical[0][CORNER]==6 && physical[1][CORNER]==4 && !resource.quarantined);
    done();
    reset();
    common_claim_error=-EPROBE_DEFER;
    assert(rpm_corner_claim(&resource)==-EPROBE_DEFER && !resource.claimed && !common_claims);
    common_claim_error=0;
    assert(rpm_corner_claim(&resource)==0);
    {
        struct rpm_corner_resource duplicate={.rpm=&endpoint,.type=0x61706d73,.id=1};
        INIT_LIST_HEAD(&duplicate.registry);
        assert(rpm_corner_claim(&duplicate)==-EBUSY);
        duplicate.id=2;assert(rpm_corner_claim(&duplicate)==0);
        rpm_corner_unclaim(&duplicate);rpm_corner_unclaim(&resource);
        assert(common_claims==2 && common_releases==2);
    }
    done();
    /* Real registration helper allocates its release record before publishing.
       Failure before/after registration retains a raced published rdev. */
    heap=calloc(1,sizeof(*heap));heap->clients=calloc(2,sizeof(*heap->clients));heap->count=2;
    assert(!pthread_mutex_init(&heap->lock.value,NULL));heap->refs.count=1;
    for(i=0;i<2;i++) { heap->clients[i].resource=heap;heap->clients[i].node=&nodes[i]; }
    held=&heap->clients[0];fail_alloc=true;
    assert(rpm_corner_register_one(&provider,held)==-ENOMEM && !registrations && heap->refs.count==1);
    fail_alloc=false;fail_registration=true;
    assert(rpm_corner_register_one(&provider,held)==-EIO && allocations==releases && heap->refs.count==1);
    fail_registration=false;
    assert(rpm_corner_register_one(&provider,held)==0 && registrations==1 && heap->refs.count==2);
    assert(published_rdev.dev.release_record);
    fail_alloc=true;
    assert(rpm_corner_register_one(&provider,&heap->clients[1])==-ENOMEM);
    rpm_corner_unregister(heap);rpm_corner_put(heap);
    assert(!freed_resources && !freed_arrays && held->resource->retired);
    assert(rpm_corner_change(held,SET_CORNER,5)==-ENODEV);
    assert(!pthread_mutex_destroy(&heap->lock.value));rdev_put(&published_rdev);
    assert(freed_resources==1 && freed_arrays==1 && !nodes[0].refs && !nodes[1].refs);
    assert(allocations==releases);
    /* Actual corner unclaim forwards owned state without sending NONE. */
    reset();assert(!rpm_corner_claim(&resource));bootstrap();before=sends;
    rpm_corner_unclaim(&resource);
    assert(!resource.claimed && preserved_claims==1 && sends==before);
    assert(qcom_rpm_smd_claim(&endpoint,resource.type,resource.id,&provider)==-ESHUTDOWN);
    done();
    puts("PASS: actual corn/vfc/swen aggregation, BOTH/AO, ACK timeout/offline/partial rollback, initial quarantine, forced sync, PM and competing consumers");
    puts("PASS: final-rdev resource lifetime and offline callbacks retain valid state");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-rpm-corners-') as scratch:
    c, binary = Path(scratch) / 'corners.c', Path(scratch) / 'corners'
    c.write_text(harness)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu11', '-O2', '-pthread',
                    '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(c),
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host fault/locking model only; RPM firmware, physical voltage and on-device suspend remain unverified.')
