#!/usr/bin/env python3
"""Exercise real RPM ACK/write, bus KVP and retry code in bounded host models.

These tests do not communicate with firmware or model real IRQ/driver-core
concurrency. They verify wire bytes, fail-closed parsing and software retries.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
rpm = (ROOT / 'drivers/soc/qcom/smd-rpm.c').read_text()
fabric = (ROOT / 'drivers/soc/qcom/msm_bus/msm_bus_fabric_adhoc.c').read_text()


def function(source, name):
    match = re.search(r'(?:static\s+)?(?:inline\s+)?(?:int|void)\s+' + name +
                      r'\s*\([^;]*?\)\s*\{', source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


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
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint32_t __le32;
typedef uint64_t __le64;
#define __iomem
#define HZ 100
#define GFP_KERNEL 0
#define NUM_CTX 2
#define DUAL_CTX 0
#define ACTIVE_CTX 1
#define QCOM_SMD_RPM_ACTIVE_STATE 0
#define QCOM_SMD_RPM_SLEEP_STATE 1
#define MSM_RPM_CTX_ACTIVE_SET 0
#define MSM_RPM_CTX_SLEEP_SET 1
#define CONFIG_QCOM_SMD_RPM 1
#define CONFIG_MSM_RPM_SMD 0
#define IS_REACHABLE(x) (x)
#define RPM_MASTER_FIELD_BW 0x00007762
#define RPM_BUS_MASTER_REQ 0x73616d62
#define RPM_BUS_SLAVE_REQ 0x766c7362
#define ALIGN(v,a) (((v)+(a)-1)&~((a)-1))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define BUILD_BUG_ON(x) _Static_assert(!(x), "invalid layout")
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define EXPORT_SYMBOL(x)
#define MSM_BUS_ERR(...) ((void)0)
#define MSM_BUS_DBG(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define dev_err(...) ((void)0)
#define trace_bus_agg_bw(node,id,ctx,bw) ((void)(node),(void)(id),(void)(ctx),(void)(bw))
#define unlikely(x) (x)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define cpu_to_le32(x) ((u32)(x))
#define cpu_to_le64(x) ((u64)(x))
static u32 get_unaligned_le32(const void *p)
{ const u8 *b=p; return b[0]|(u32)b[1]<<8|(u32)b[2]<<16|(u32)b[3]<<24; }
static u64 get_unaligned_le64(const void *p)
{ const u8 *b=p; return get_unaligned_le32(b)|(u64)get_unaligned_le32(b+4)<<32; }
'''

native = common + r'''
struct completion { unsigned done; };
struct mutex { int unused; };
struct kref { int unused; };
typedef int spinlock_t;
#define spin_lock_irqsave(lock,flags) ((void)(lock),(flags)=0)
#define spin_unlock_irqrestore(lock,flags) ((void)(lock),(void)(flags))
#define mutex_lock(lock) ((void)(lock))
#define mutex_unlock(lock) ((void)(lock))
static void reinit_completion(struct completion *c) { c->done=0; }
static void complete(struct completion *c) { c->done++; }
static unsigned long wait_for_completion_timeout(struct completion *c, unsigned long timeout)
{ (void)timeout; if(!c->done) return 0; c->done--; return 1; }
struct device { void *data; };
struct rpmsg_device { struct device dev; };
struct rpmsg_endpoint { int unused; };
static void *dev_get_drvdata(struct device *d) { return d->data; }
#define kmalloc(n,f) malloc(n)
#define kfree(p) free(p)
static int rpmsg_send(struct rpmsg_endpoint *, const void *, int);
'''
native += '\n'.join(line for line in rpm.splitlines() if line.startswith('#define RPM_')) + '\n'
native += rpm[rpm.index('struct qcom_smd_rpm {'):rpm.index('static struct rpmsg_driver qcom_smd_rpm_driver;')]
for name in ('qcom_rpm_smd_write', 'qcom_rpm_parse_ack', 'qcom_smd_rpm_callback',
             'qcom_smd_rpm_offline'):
    native += function(rpm, name)
native += r'''
static struct qcom_smd_rpm channel;
static struct rpmsg_device rpdev;
static struct rpmsg_endpoint endpoint;
static int behavior, sends, fail_on_send;
static u8 sent[8][256];
static unsigned sent_len[8];
enum { ACK_OK, ACK_STALE, SEND_FAIL, ACK_MISSING_RESOURCE, ACK_OTHER_ERROR, REMOVE_DURING_WRITE };
static void put32(u8 *p,u32 n) { p[0]=n;p[1]=n>>8;p[2]=n>>16;p[3]=n>>24; }
static size_t ack_packet(u8 *packet,u32 id,const char *error)
{
    size_t n=20,len;
    put32(packet,RPM_SERVICE_TYPE_REQUEST);
    put32(packet+8,RPM_MSG_TYPE_MSG_ID);put32(packet+12,4);put32(packet+16,id);
    if(error) {
        len=strlen(error)+1;
        put32(packet+n,RPM_MSG_TYPE_ERR);put32(packet+n+4,len);
        memcpy(packet+n+8,error,len);
        n+=8+ALIGN(len,4);
    }
    put32(packet+4,n-8);return n;
}
static int rpmsg_send(struct rpmsg_endpoint *e,const void *data,int count)
{
    u8 packet[96]={0};u32 id;size_t n;int action=behavior;
    assert(e==&endpoint && count>=28 && count<=256 && sends<8);
    memcpy(sent[sends],data,count);sent_len[sends]=count;sends++;
    assert(get_unaligned_le32(data)==RPM_SERVICE_TYPE_REQUEST);
    assert(get_unaligned_le32((const u8 *)data+4)==(u32)count-8);
    assert(get_unaligned_le32((const u8 *)data+24)==(u32)count-28);
    if(fail_on_send && sends!=fail_on_send) action=ACK_OK;
    if(action==SEND_FAIL) return -EIO;
    if(action==REMOVE_DURING_WRITE) { qcom_smd_rpm_offline(&channel);return 0; }
    id=get_unaligned_le32((const u8 *)data+8);
    n=ack_packet(packet,action==ACK_STALE?id-1:id,
        action==ACK_MISSING_RESOURCE?"resource does not exist":
        action==ACK_OTHER_ERROR?"bad request":NULL);
    assert(qcom_smd_rpm_callback(&rpdev,packet,n,NULL,0)==0);
    return 0;
}
static void reset(int mode)
{
    memset(&channel,0,sizeof(channel));channel.online=true;
    channel.rpm_channel=&endpoint;rpdev.dev.data=&channel;
    memset(sent,0,sizeof(sent));sends=0;behavior=mode;fail_on_send=0;
}
struct msm_bus_node_device_type;
struct msm_bus_noc_ops {
    bool (*update_bw_reg)(int);
    int (*set_bw)(struct msm_bus_node_device_type *,void *,u32,u32,u32);
};
struct msm_bus_fab_device_type {struct msm_bus_noc_ops noc_ops;void *qos_base;u32 base_offset,qos_off,qos_freq;};
struct msm_bus_node_info_type {int id,mas_rpm_id,slv_rpm_id;struct device *bus_device;struct {int mode;} qos_params;};
struct nodebw {u64 sum_ab,last_sum_ab;};
struct msm_bus_node_device_type {struct qcom_smd_rpm *rpm;struct msm_bus_node_info_type *node_info;struct nodebw node_bw[NUM_CTX];bool ap_owned;struct msm_bus_fab_device_type *fabdev;struct device dev;};
static struct msm_bus_node_device_type *to_msm_bus_node(struct device *d)
{return container_of(d,struct msm_bus_node_device_type,dev);}
'''
for name in ('msm_bus_rpm_vote', 'send_rpm_msg', 'flush_bw_data'):
    native += function(fabric, name)
native += r'''
int main(void)
{
    u8 packet[96]={0},payload[228]={0};size_t n;u32 id;int status,ret;
    struct msm_bus_node_info_type info={.id=123,.mas_rpm_id=11,.slv_rpm_id=22};
    struct msm_bus_node_device_type node={.rpm=&channel,.node_info=&info};
    reset(ACK_OK);assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==0);
    assert(sends==1 && get_unaligned_le32(sent[0]+8)==1);
    n=ack_packet(packet,1,NULL);
    assert(qcom_rpm_parse_ack(packet,n,&id,&status)==0 && id==1 && status==0);
    assert(qcom_rpm_parse_ack(packet,-1,&id,&status)==-EINVAL);
    assert(qcom_rpm_parse_ack(packet,7,&id,&status)==-EINVAL);
    put32(packet+4,500);assert(qcom_rpm_parse_ack(packet,n,&id,&status)==-EINVAL);
    n=ack_packet(packet,1,NULL);put32(packet+12,8);
    assert(qcom_rpm_parse_ack(packet,n,&id,&status)==-EINVAL);
    n=ack_packet(packet,1,NULL);put32(packet+8,0);
    assert(qcom_rpm_parse_ack(packet,n,&id,&status)==-EINVAL);
    n=ack_packet(packet,1,NULL);memcpy(packet+n,packet+8,12);put32(packet+4,n+12-8);
    assert(qcom_rpm_parse_ack(packet,n+12,&id,&status)==-EINVAL);
    n=ack_packet(packet,1,"bad request");put32(packet+24,0);
    assert(qcom_rpm_parse_ack(packet,n,&id,&status)==-EINVAL);
    n=ack_packet(packet,1,"bad request");put32(packet+24,90);
    assert(qcom_rpm_parse_ack(packet,n,&id,&status)==-EINVAL);
    reset(ACK_STALE);channel.ack.done=3;
    assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==-ETIMEDOUT);
    assert(!channel.waiting_msg_id && !channel.ack.done);
    n=ack_packet(packet,1,NULL);qcom_smd_rpm_callback(&rpdev,packet,n,NULL,0);
    assert(!channel.ack.done);behavior=ACK_OK;
    assert(qcom_rpm_smd_write(&channel,1,1,2,payload,4)==0);
    assert(get_unaligned_le32(sent[1]+8)==2 && get_unaligned_le32(sent[1]+12)==1);
    reset(SEND_FAIL);assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==-EIO);
    assert(!channel.waiting_msg_id);
    reset(ACK_MISSING_RESOURCE);assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==-ENXIO);
    reset(ACK_OTHER_ERROR);assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==-EINVAL);
    reset(REMOVE_DURING_WRITE);assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==-ENODEV);
    assert(!channel.online && !channel.rpm_channel);
    assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==-ENODEV && sends==1);
    reset(ACK_OK);assert(qcom_rpm_smd_write(&channel,2,1,2,payload,4)==-EINVAL);
    assert(qcom_rpm_smd_write(&channel,0,1,2,payload,228)==-EINVAL && !sends);
    assert(qcom_rpm_smd_write(&channel,0,1,2,payload,227)==0 && sent_len[0]==255);
    reset(ACK_OK);channel.next_msg_id=UINT32_MAX;
    assert(qcom_rpm_smd_write(&channel,0,1,2,payload,4)==0);
    assert(get_unaligned_le32(sent[0]+8)==1);
    reset(ACK_OK);node.node_bw[ACTIVE_CTX].sum_ab=UINT64_C(0x12345678deadbeef);
    assert(send_rpm_msg(&node,ACTIVE_CTX)==0 && sends==2);
    assert(get_unaligned_le32(sent[0]+12)==0);
    assert(get_unaligned_le32(sent[0]+16)==RPM_BUS_MASTER_REQ);
    assert(get_unaligned_le32(sent[1]+16)==RPM_BUS_SLAVE_REQ);
    assert(get_unaligned_le32(sent[0]+20)==11 && get_unaligned_le32(sent[1]+20)==22);
    assert(sent_len[0]==44 && get_unaligned_le32(sent[0]+28)==RPM_MASTER_FIELD_BW);
    assert(get_unaligned_le32(sent[0]+32)==8 && get_unaligned_le64(sent[0]+36)==UINT64_C(0x12345678deadbeef));
    reset(ACK_OK);node.node_bw[DUAL_CTX].sum_ab=UINT64_C(0x200000001);
    assert(send_rpm_msg(&node,DUAL_CTX)==0 && get_unaligned_le32(sent[0]+12)==1);
    reset(ACK_OTHER_ERROR);fail_on_send=2;node.node_bw[ACTIVE_CTX].last_sum_ab=42;
    ret=flush_bw_data(&node,ACTIVE_CTX);
    assert(ret==-EINVAL && sends==2 && node.node_bw[ACTIVE_CTX].last_sum_ab==42);
    reset(ACK_OK);assert(flush_bw_data(&node,ACTIVE_CTX)==0);
    assert(node.node_bw[ACTIVE_CTX].last_sum_ab==node.node_bw[ACTIVE_CTX].sum_ab);
    reset(ACK_OK);node.rpm=NULL;
    assert(send_rpm_msg(&node,ACTIVE_CTX)==-ENODEV && !sends);
    puts("PASS: actual RPM ACK/write correlation, bounds, errors, timeout/remove and full LE64 active/sleep bus KVP");
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='dora-rpm-bus-') as scratch:
    source, binary = Path(scratch) / 'native.c', Path(scratch) / 'native'
    source.write_text(native)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu99', '-O2', '-Wall',
                    '-Wextra', '-Werror', '-Wno-unused-parameter', str(source),
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print('Host software models only: RPM firmware ACKs, IRQ races and physical bandwidth remain untested.')
