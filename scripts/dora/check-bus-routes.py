#!/usr/bin/env python3
"""Execute real msm-bus route/client code against Sony's complete bus graph.

This bounded host model checks graph traversal, allocation failure unwinds and
reference ownership. It does not validate bandwidth registers or RPM firmware.
"""
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/soc/qcom/msm_bus/msm_bus_arb_adhoc.c').read_text()
fixture = json.loads((ROOT / 'Documentation/sony/dora-bus-dt.json').read_text())['topology']


def function(name):
    match = re.search(r'(?:static )?(?:struct msm_bus_client_handle\s*\*|uint32_t|int|void)\s*\n?' + name + r'\s*\([^;]*?\)\s*\{', source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define GFP_KERNEL 0
#define NUM_LNODES 3
#define NUM_CL_HANDLES 50
#define NUM_CTX 2
#define MAX_STR_CL 50
#define MSM_BUS_ERR(...) ((void)0)
#define MSM_BUS_DBG(...) ((void)0)
#define pr_err(...) ((void)0)
#define MSM_BUS_DBG_REGISTER 0
#define MSM_BUS_DBG_UNREGISTER 1
#define msm_bus_dbg_client_data(...) ((void)0)
#define msm_bus_dbg_add_client(...) ((void)0)
#define msm_bus_dbg_remove_client(...) ((void)0)
#define ZERO_OR_NULL_PTR(p) (!(p))
#define IS_ERR_OR_NULL(p) (!(p))
#define rt_mutex_lock(p) ((void)(p))
#define rt_mutex_unlock(p) ((void)(p))
static int msm_bus_adhoc_lock;
static bool adhoc_online = true;
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for (p=list_entry((h)->next,__typeof__(*p),m); &p->m != (h); \
         p=list_entry(p->m.next,__typeof__(*p),m))
#define list_for_each_entry_safe(p,n,h,m) \
    for(p=list_entry((h)->next,__typeof__(*p),m),n=list_entry(p->m.next,__typeof__(*p),m); \
        &p->m != (h); p=n,n=list_entry(n->m.next,__typeof__(*n),m))
static void list_add_tail(struct list_head *e, struct list_head *h)
{ e->next=h; e->prev=h->prev; h->prev->next=e; h->prev=e; }
static void list_del(struct list_head *e)
{ e->prev->next=e->next; e->next->prev=e->prev; }
struct device { int refs; };
struct msm_bus_node_info_type {
    unsigned id, num_connections, num_blist;
    struct device **dev_connections, **black_connections;
};
struct link_node {
    bool in_use; const char *cl_name; int next; struct device *next_dev;
    uint64_t lnode_ib[NUM_CTX], lnode_ab[NUM_CTX];
};
struct msm_bus_node_device_type {
    struct msm_bus_node_info_type *node_info;
    int num_lnodes; struct link_node *lnode_list;
    struct device dev; struct list_head link;
};
static struct msm_bus_node_device_type *to_msm_bus_node(struct device *d)
{ return container_of(d,struct msm_bus_node_device_type,dev); }
static struct device *get_device(struct device *d)
{ if(d) d->refs++; return d; }
static void put_device(struct device *d)
{ if(d) { assert(d->refs > 1); d->refs--; } }
static int allocation_calls, fail_at, live_transient;
static bool failed_allocation(void)
{ allocation_calls++; return fail_at && allocation_calls==fail_at; }
static void *host_alloc(size_t size, bool clear)
{
    void *p;
    if(failed_allocation()) return NULL;
    p=clear ? calloc(1,size) : malloc(size);
    assert(p); live_transient++; return p;
}
#define kzalloc(s,f) host_alloc(s,true)
#define kcalloc(n,s,f) host_alloc((n)*(s),true)
#define kmalloc_array(n,s,f) host_alloc((n)*(s),false)
#define krealloc(p,s,f) host_realloc(p,s)
static void *host_realloc(void *p,size_t size)
{
    void *next;
    if(failed_allocation()) return NULL;
    next=realloc(p,size); assert(next); return next;
}
static void kfree(void *p) { if(p) { live_transient--; free(p); } }
static void *msm_bus_realloc_devmem(struct device *dev, void *p, size_t old,
                                  size_t size, int flags)
{
    void *next;
    (void)dev; (void)flags;
    if(failed_allocation()) return NULL;
    next=calloc(1,size); assert(next);
    if(old) memcpy(next,p,old);
    free(p); return next;
}
static size_t strnlen_local(const char *p,size_t max)
{ size_t n=0; while(n<max && p[n]) n++; return n; }
#define strnlen strnlen_local
static size_t host_strlcpy(char *out,const char *s,size_t size)
{ size_t n=strlen(s); if(size) { size_t c=n<size-1?n:size-1; memcpy(out,s,c); out[c]=0; } return n; }

#define strlcpy host_strlcpy
struct msm_bus_vectors { unsigned src, dst; uint64_t ib, ab; };
struct msm_bus_paths { int num_paths; struct msm_bus_vectors *vectors; };
struct msm_bus_scale_pdata {
    const char *name; int num_usecases; struct msm_bus_paths *usecase; bool active_only;
};
struct msm_bus_client {
    struct msm_bus_scale_pdata *pdata; int curr; int *src_pnode; struct device **src_devs;
};
struct msm_bus_client_handle {
    char *name; bool active_only; unsigned mas, slv; struct device *mas_dev; int first_hop;
    uint64_t cur_act_ib,cur_act_ab;
};
static struct { int num_entries; struct msm_bus_client **cl_list; } handle_list;
static int msm_bus_type;
static struct msm_bus_node_device_type graph[160];
static struct msm_bus_node_info_type info[160];
static struct device *connections[160][160], *blacklist[160][160];
static int graph_count;
static struct device *bus_find_device(void *bus,void *start,void *id,
                                     int (*match)(struct device *,void *))
{
    int i; (void)bus; (void)start;
    for(i=0;i<graph_count;i++) if(match(&graph[i].dev,id)) return get_device(&graph[i].dev);
    return NULL;
}
static int update_path(struct device *dev,int dst,uint64_t ai,uint64_t aa,
                       uint64_t si,uint64_t sa,uint64_t ci,uint64_t ca,int idx,int ctx)
{ (void)dev;(void)dst;(void)ai;(void)aa;(void)si;(void)sa;(void)ci;(void)ca;(void)idx;(void)ctx;return 0; }
static int commit_data(void) { return 0; }
'''
a = source.index('struct bus_search_type {')
b = source.index('struct handle_type', a)
harness += source[a:b]
for name in ('msm_bus_device_match_adhoc', 'gen_lnode', 'remove_lnode',
             'free_unvoted_path', 'getpath', 'remove_path', 'unregister_client_adhoc',
             'alloc_handle_lst', 'gen_handle', 'register_client_adhoc', 'free_cl_mem',
             'unregister_adhoc', 'register_adhoc'):
    harness += function(name)
harness += r'''
static int by_id(unsigned id)
{ int i; for(i=0;i<graph_count;i++) if(info[i].id==id) return i; assert(0); return -1; }
static void reset_graph(void)
{
    int i;
    for(i=0;i<graph_count;i++) {
        assert(graph[i].dev.refs==1);
        free(graph[i].lnode_list); graph[i].lnode_list=NULL; graph[i].num_lnodes=0;
        assert(graph[i].link.next==(void *)(uintptr_t)0x1234);
        assert(graph[i].link.prev==(void *)(uintptr_t)0x5678);
    }
    assert(live_transient==0);
    allocation_calls=fail_at=0;
}
static void no_paths(void)
{
    int i,j;
    for(i=0;i<graph_count;i++) {
        assert(graph[i].dev.refs==1);
        for(j=0;j<graph[i].num_lnodes;j++) assert(!graph[i].lnode_list[j].in_use);
    }
}
static int verify_path(unsigned src,unsigned dst)
{
    struct device *d=&graph[by_id(src)].dev;
    int idx=getpath(d,dst,"host-test"), first=idx, hops=0;
    assert(idx>=0);
    while(d) {
        struct msm_bus_node_device_type *n=to_msm_bus_node(d);
        struct link_node *l=&n->lnode_list[idx];
        assert(l->in_use); assert(++hops<=graph_count);
        if(!l->next_dev) assert(n->node_info->id==dst);
        else {
            unsigned i; bool connected=false;
            for(i=0;i<n->node_info->num_connections;i++)
                if(n->node_info->dev_connections[i]==l->next_dev) connected=true;
            assert(connected);
        }
        idx=l->next; d=l->next_dev;
    }
    free_unvoted_path(&graph[by_id(src)].dev,first);
    no_paths(); return first;
}
static void init_graph(void)
{
'''
entries = [(path, props) for path, props in fixture.items() if 'cell-id' in props]
indices = {path: i for i, (path, _) in enumerate(entries)}
harness += f'graph_count={len(entries)};\n'
for i, (path, props) in enumerate(entries):
    ident = struct.unpack('>I', bytes.fromhex(props['cell-id']))[0]
    harness += f'info[{i}].id={ident};\n'
    for key, field, array in [('qcom,connections', 'num_connections', 'connections'),
                               ('qcom,blacklist', 'num_blist', 'blacklist')]:
        paths = props.get(key, [])
        assert len(paths) <= 160
        harness += f'info[{i}].{field}={len(paths)};\n'
        for j, target in enumerate(paths):
            harness += f'{array}[{i}][{j}]=&graph[{indices[target]}].dev;\n'
harness += r'''
    for(int i=0;i<graph_count;i++) {
        graph[i].node_info=&info[i]; graph[i].dev.refs=1;
        info[i].dev_connections=connections[i]; info[i].black_connections=blacklist[i];
        graph[i].link.next=(void *)(uintptr_t)0x1234;
        graph[i].link.prev=(void *)(uintptr_t)0x5678;
    }
}
int main(void)
{
    int i, attempts;
    struct msm_bus_vectors vectors[2]={{95,512,0,0},{1,650,0,0}};
    struct msm_bus_paths usecases[2]={{2,vectors},{2,vectors}};
    struct msm_bus_scale_pdata pdata={"ufs1",2,usecases,false};
    struct msm_bus_client_handle *dynamic;
    unsigned handle;
    init_graph();
    verify_path(95,512); verify_path(1,650);
    /* MDSS0/1 memory paths use the original graph as well. */
    verify_path(22,512); verify_path(23,512);
    reset_graph();
    verify_path(95,512); attempts=allocation_calls;
    reset_graph();
    for(i=1;i<=attempts;i++) {
        fail_at=i;
        int path=getpath(&graph[by_id(95)].dev,512,"fault");
        if(path>=0) free_unvoted_path(&graph[by_id(95)].dev,path);
        no_paths(); reset_graph();
    }
    /* Force an unreachable destination and confirm no state is retained. */
    assert(getpath(&graph[by_id(95)].dev,999999,"missing")<0);
    no_paths(); reset_graph();
    handle=register_client_adhoc(&pdata); assert(handle);
    unregister_client_adhoc(handle); no_paths();
    kfree(handle_list.cl_list); memset(&handle_list,0,sizeof(handle_list));
    reset_graph();
    handle=register_client_adhoc(&pdata); assert(handle);
    attempts=allocation_calls;
    unregister_client_adhoc(handle);
    kfree(handle_list.cl_list); memset(&handle_list,0,sizeof(handle_list));
    reset_graph();
    for(i=1;i<=attempts;i++) {
        fail_at=i;
        handle=register_client_adhoc(&pdata);
        if(handle) unregister_client_adhoc(handle);
        kfree(handle_list.cl_list); memset(&handle_list,0,sizeof(handle_list));
        no_paths(); reset_graph();
    }
    dynamic=register_adhoc(95,512,"x",false); assert(dynamic);
    unregister_adhoc(dynamic); no_paths(); reset_graph();
    dynamic=register_adhoc(95,999999,"x",false); assert(!dynamic);
    no_paths(); reset_graph();
    /* Duplicate edges and a genuine back-edge must not terminate BFS. */
    graph_count=4;
    for(i=0;i<4;i++) { info[i].id=1000+i; info[i].num_blist=0; }
    info[0].num_connections=2; connections[0][0]=&graph[1].dev; connections[0][1]=&graph[2].dev;
    info[1].num_connections=2; connections[1][0]=&graph[0].dev; connections[1][1]=&graph[3].dev;
    info[2].num_connections=1; connections[2][0]=&graph[3].dev;
    info[3].num_connections=0;
    verify_path(1000,1003); reset_graph();
    info[0].num_blist=1; blacklist[0][0]=&graph[1].dev;
    verify_path(1000,1003); reset_graph();
    blacklist[0][0]=&graph[3].dev;
    assert(getpath(&graph[0].dev,1003,"blocked")<0);
    no_paths(); reset_graph();
    info[0].num_blist=0; connections[0][0]=NULL;
    assert(getpath(&graph[0].dev,1003,"broken graph")==-ENODEV);
    no_paths(); reset_graph();
    puts("PASS: real route/client code: full 145-node Sony graph, UFS+MDSS routes, allocation failures, reference balance and pending-list preservation");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-bus-route-') as temp:
    c = Path(temp) / 'routes.c'
    exe = Path(temp) / 'routes'
    c.write_text(harness)
    subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu11', '-O1', '-Wall', '-Wextra',
                    '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', '-o', str(exe), str(c)], check=True)
    subprocess.run([str(exe)], check=True)
print('Host graph/error model only; device firmware, QoS and actual bandwidth remain untested.')
