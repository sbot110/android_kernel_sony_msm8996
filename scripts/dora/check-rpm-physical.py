#!/usr/bin/env python3
"""Execute the physical RPM provider against fault-injected host APIs.

The compiled transaction and registration code comes from the production
translation unit. Transport responses and regulator-core aggregation are host
models; these tests neither contact firmware nor measure a physical rail.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r'^(?:static\s+)?(?:int|void|bool|unsigned int)\s+' +
                      re.escape(name) + r'\s*\([^;]*?\)\s*\{', source,
                      re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


HOST = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
typedef uint32_t u32;
typedef uint8_t u8;
typedef uint32_t __le32;
#define BIT(x) (1U << (x))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define EPROBE_DEFER 517
#define GFP_KERNEL 0
#define REGULATOR_VOLTAGE 0
#define THIS_MODULE NULL
#define EXPORT_SYMBOL_GPL(x)
#define MODULE_DESCRIPTION(x)
#define MODULE_LICENSE(x)
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define PTR_ERR(x) ((int)(intptr_t)(x))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define IS_ERR_OR_NULL(p) (!(p) || IS_ERR(p))
#define REGULATOR_ERROR_FAIL BIT(4)
#define REGULATOR_CHANGE_VOLTAGE BIT(0)
#define REGULATOR_CHANGE_STATUS BIT(1)
#define REGULATOR_CHANGE_DRMS BIT(2)
#define QCOM_SMD_RPM_ACTIVE_STATE 0
#define QCOM_SMD_RPM_SLEEP_STATE 1
#define QCOM_SMD_RPM_LDOA 0x616f646c
#define QCOM_SMD_RPM_SMPA 0x61706d73
#define min(a,b) ((a) < (b) ? (a) : (b))
#define max(a,b) ((a) > (b) ? (a) : (b))
#define min_t(t,a,b) min((t)(a),(t)(b))
#define cpu_to_le32(x) (x)
#define DIV_ROUND_UP(n,d) (((n) + (d) - 1) / (d))
#define BUG_ON(x) assert(!(x))
#define lockdep_assert_held_once(x) ((void)0)
#define rdev_err(...) ((void)0)
#define PM_SUSPEND_ON 0
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define REGULATOR_LINEAR_RANGE(v,a,b,s) { (v), (a), (b), (s) }
struct mutex { pthread_mutex_t value; unsigned id; };
#define DEFINE_MUTEX(n) struct mutex n = { PTHREAD_MUTEX_INITIALIZER, 0 }
static pthread_mutex_t freeze_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t freeze_condition = PTHREAD_COND_INITIALIZER;
static _Thread_local bool pm_freeze_thread, mutating_thread;
static bool pause_pm_after_ack, pm_ack_paused, release_pm_freeze;
static bool mutation_bank_attempt, mutation_finished;
static int mutation_result;
static void mutex_init(struct mutex *m)
{ assert(!pthread_mutex_init(&m->value, NULL)); m->id = 1; }
static void mutex_lock(struct mutex *m)
{
    if (mutating_thread && !m->id) {
        pthread_mutex_lock(&freeze_lock); mutation_bank_attempt = true;
        pthread_cond_broadcast(&freeze_condition); pthread_mutex_unlock(&freeze_lock);
    }
    assert(!pthread_mutex_lock(&m->value));
}
static void mutex_unlock(struct mutex *m)
{
    assert(!pthread_mutex_unlock(&m->value));
    if (pm_freeze_thread && m->id) {
        pthread_mutex_lock(&freeze_lock);
        if (pause_pm_after_ack) {
            pause_pm_after_ack = false; pm_ack_paused = true;
            pthread_cond_broadcast(&freeze_condition);
            while (!release_pm_freeze) pthread_cond_wait(&freeze_condition, &freeze_lock);
        }
        pthread_mutex_unlock(&freeze_lock);
    }
}
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n = { &(n), &(n) }
#define INIT_LIST_HEAD(h) ((h)->next = (h), (h)->prev = (h))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(p,h,m) \
    for (p = list_entry((h)->next, __typeof__(*p), m); &p->m != (h); \
         p = list_entry(p->m.next, __typeof__(*p), m))
static void list_add_tail(struct list_head *e, struct list_head *h)
{ e->next = h; e->prev = h->prev; h->prev->next = e; h->prev = e; }
static void list_del_init(struct list_head *e)
{ e->prev->next = e->next; e->next->prev = e->prev; INIT_LIST_HEAD(e); }
struct kref { unsigned count; };
static void kref_init(struct kref *r) { r->count = 1; }
static void kref_get(struct kref *r) { assert(r->count); r->count++; }
static void kref_put(struct kref *r, void (*release)(struct kref *))
{ assert(r->count); if (!--r->count) release(r); }
struct device;
struct host_record { struct host_record *next; void (*release)(struct device *, void *); };
struct host_action { struct host_action *next; void (*action)(void *); void *data; };
struct device { struct host_record *records; struct host_action *actions; unsigned refs; };
struct device_node {
    unsigned refs; bool optin, managed_input, always_on, boot_on, proxy, corner;
    bool parent_supply, vin_supply; u32 sets;
    int min_uV, max_uV; const char *name;
};
struct regulation_constraints {
    const char *name; int min_uV, max_uV, system_load, input_uV;
    unsigned valid_ops_mask; bool apply_uV, always_on, boot_on;
};
struct regulator_init_data { struct regulation_constraints constraints; const char *supply_regulator; };
struct regulator_dev;
struct regulator_ops {
    int (*enable)(struct regulator_dev *);
    int (*disable)(struct regulator_dev *);
    int (*is_enabled)(struct regulator_dev *);
    int (*get_voltage)(struct regulator_dev *);
    int (*set_voltage)(struct regulator_dev *, int, int, unsigned *);
    int (*set_load)(struct regulator_dev *, int);
    int (*list_voltage)(struct regulator_dev *, unsigned);
    int (*get_error_flags)(struct regulator_dev *, unsigned *);
    int (*set_voltage_sel)(struct regulator_dev *, unsigned);
    unsigned (*get_optimum_mode)(struct regulator_dev *, int, int, int);
    unsigned (*get_mode)(struct regulator_dev *);
    int (*set_mode)(struct regulator_dev *, unsigned);
};
struct regulator_linear_range { unsigned min_uV, min_sel, max_sel, uV_step; };
struct regulator_desc {
    const char *name, *supply_name, *of_match;
    const struct regulator_ops *ops; void *owner; unsigned type;
    unsigned n_voltages, n_linear_ranges, linear_min_sel;
    int min_uV, uV_step, fixed_uV;
    const struct regulator_linear_range *linear_ranges;
};
struct regulator_dev {
    struct device dev; const struct regulator_desc *desc; void *data;
    unsigned refs; struct regulator_init_data *init;
    struct mutex mutex; struct list_head consumer_list;
    struct regulation_constraints *constraints; struct regulator *supply;
};
struct regulator_voltage { int min_uV, max_uV; };
struct regulator {
    struct regulator_dev *rdev; struct list_head list; int uA_load;
    struct regulator_voltage voltage[1];
};
struct regulator_config {
    struct device *dev; struct device_node *of_node;
    struct regulator_init_data *init_data; void *driver_data;
};
struct qcom_smd_rpm { unsigned unused; };
static struct qcom_smd_rpm endpoint;
static void *rdev_get_drvdata(struct regulator_dev *r) { return r->data; }
static void *regulator_get_drvdata(struct regulator *r) { return r->rdev->data; }
static unsigned live_allocations, allocation_calls, fail_allocation;
static void *kzalloc(size_t size, unsigned flags)
{
    void *p; (void)flags;
    if (++allocation_calls == fail_allocation) return NULL;
    p = calloc(1, size); assert(p); live_allocations++; return p;
}
static void *kmemdup(const void *src, size_t size, unsigned flags)
{ void *p = kzalloc(size, flags); if (p) memcpy(p, src, size); return p; }
static char *kstrdup(const char *text, unsigned flags)
{ return kmemdup(text, strlen(text) + 1, flags); }
static void kfree(void *p) { if (p) { assert(live_allocations); live_allocations--; free(p); } }
static struct device_node *of_node_get(struct device_node *n)
{ if (n) n->refs++; return n; }
static void of_node_put(struct device_node *n) { if (n) { assert(n->refs); n->refs--; } }
static bool of_property_read_bool(struct device_node *n, const char *key)
{
    if (!strcmp(key, "qcom,active-sleep-votes")) return n->optin;
    if (!strcmp(key, "qcom,rpm-managed-input")) return n->managed_input;
    if (!strcmp(key, "qcom,use-voltage-corner")) return n->corner;
    return false;
}
static void *of_find_property(struct device_node *n, const char *key, void *length)
{
    (void)length;
    if (!strcmp(key, "parent-supply") && n->parent_supply) return n;
    if (!strcmp(key, "vin-supply") && n->vin_supply) return n;
    return n->proxy && strstr(key, "qcom,proxy-consumer-") ? n : NULL;
}
static int of_property_read_u32(struct device_node *n, const char *key, u32 *value)
{
    if (!strcmp(key, "qcom,set")) { *value = n->sets; return 0; }
    return -EINVAL;
}
static bool fail_init;
static struct regulator_init_data *of_get_regulator_init_data(
    struct device *dev, struct device_node *n, const struct regulator_desc *desc)
{
    static struct regulator_init_data inits[1024];
    static unsigned count;
    struct regulator_init_data *init;
    (void)dev; (void)desc;
    if (fail_init) return NULL;
    assert(count < ARRAY_SIZE(inits)); init = &inits[count++];
    memset(init, 0, sizeof(*init));
    init->constraints.name = n->name;
    init->constraints.min_uV = n->min_uV; init->constraints.max_uV = n->max_uV;
    init->constraints.always_on = n->always_on; init->constraints.boot_on = n->boot_on;
    init->constraints.apply_uV = true; /* OF defaults must be explicitly cleared. */
    return init;
}
static unsigned devres_allocations, devres_releases;
static bool fail_devres, fail_register, fail_action, raced_consumer;
static void *devres_alloc(void (*release)(struct device *, void *), size_t size, unsigned flags)
{
    struct host_record *record; (void)flags;
    if (fail_devres) return NULL;
    record = calloc(1, sizeof(*record) + size); assert(record);
    record->release = release; devres_allocations++; return record + 1;
}
static void devres_free(void *data)
{ assert(data); devres_releases++; free((struct host_record *)data - 1); }
static void devres_add(struct device *dev, void *data)
{
    struct host_record *record = (struct host_record *)data - 1;
    record->next = dev->records; dev->records = record;
}
static int devm_add_action_or_reset(struct device *dev, void (*action)(void *), void *data)
{
    if (fail_action) { action(data); return -ENOMEM; }
    struct host_action *record = calloc(1, sizeof(*record)); assert(record);
    record->action = action; record->data = data;
    record->next = dev->actions; dev->actions = record; return 0;
}
static struct regulator_dev *published;
static unsigned registrations, unregistrations;
static struct device *expected_bank;
static struct device_node *expected_node;
static unsigned publication_callbacks;
static struct regulator_dev *regulator_register(const struct regulator_desc *desc,
                                                const struct regulator_config *config)
{
    struct regulator_dev *rdev;
    assert(config->dev == expected_bank && config->of_node == expected_node);
    assert(config->init_data && !config->init_data->constraints.apply_uV);
    assert(!desc->supply_name); /* Audited mode delegates inputs to RPM, never dummy Linux parents. */
    if (fail_register) return ERR_PTR(-EIO);
    rdev = calloc(1, sizeof(*rdev)); assert(rdev);
    rdev->desc = desc; rdev->data = config->driver_data;
    rdev->init = config->init_data; rdev->refs = 1 + raced_consumer;
    rdev->constraints = &rdev->init->constraints;
    mutex_init(&rdev->mutex); INIT_LIST_HEAD(&rdev->consumer_list);
    assert(desc->ops->is_enabled(rdev) == -EPROBE_DEFER);
    assert(desc->ops->set_load(rdev, 1000) == -EPROBE_DEFER);
    publication_callbacks++;
    registrations++; published = rdev; return rdev;
}
static void rdev_put(struct regulator_dev *rdev)
{
    assert(rdev->refs);
    if (--rdev->refs) return;
    while (rdev->dev.records) {
        struct host_record *record = rdev->dev.records;
        rdev->dev.records = record->next;
        record->release(&rdev->dev, record + 1); devres_free(record + 1);
    }
    assert(!pthread_mutex_destroy(&rdev->mutex.value));
    free(rdev);
}
static void regulator_unregister(struct regulator_dev *rdev)
{ unregistrations++; rdev_put(rdev); }
static void bank_release(struct device *dev)
{
    while (dev->actions) {
        struct host_action *record = dev->actions;
        dev->actions = record->next;
        record->action(record->data); free(record);
    }
}
struct host_claim { struct qcom_smd_rpm *rpm; u32 type, id; const void *owner; bool tombstone; };
static struct host_claim claim;
static unsigned claims, claim_releases;
static int qcom_rpm_smd_claim(struct qcom_smd_rpm *rpm, u32 type, u32 id, const void *owner)
{
    if (claim.owner || claim.tombstone) return -EBUSY;
    claim = (struct host_claim){ rpm, type, id, owner, false }; claims++; return 0;
}
static void qcom_rpm_smd_release(struct qcom_smd_rpm *rpm, u32 type, u32 id,
                                 const void *owner, bool preserve)
{
    assert(claim.owner == owner && claim.rpm == rpm && claim.type == type && claim.id == id);
    claim.owner = NULL; claim.tombstone = preserve; claim_releases++;
}
static bool replay_core;
static int core_min_uV, core_max_uV, core_loads[2], core_system_load;
static unsigned voltage_replays, load_replays;
static struct regulator core_secondary;
static int host_actual_regulator_sync_voltage(struct regulator *reg);
static int host_actual_regulator_sync_load(struct regulator *reg);
static int regulator_set_load(struct regulator *reg, int load);
static void regulator_lock(struct regulator_dev *rdev) { mutex_lock(&rdev->mutex); }
static void regulator_unlock(struct regulator_dev *rdev) { mutex_unlock(&rdev->mutex); }
static bool regulator_ops_is_valid(struct regulator_dev *rdev, unsigned mask)
{ return !!(rdev->constraints->valid_ops_mask & mask); }
static int _regulator_get_voltage(struct regulator_dev *rdev)
{ return rdev->desc->ops->get_voltage(rdev); }
static int regulator_get_voltage(struct regulator *reg) { return _regulator_get_voltage(reg->rdev); }
static int regulator_mode_constrain(struct regulator_dev *rdev, unsigned *mode)
{ (void)rdev; (void)mode; assert(0); return -EOPNOTSUPP; }
static int regulator_check_voltage(struct regulator_dev *rdev, int *low, int *high)
{
    *low = max(*low, rdev->constraints->min_uV);
    *high = min(*high, rdev->constraints->max_uV);
    return *low <= *high ? 0 : -EINVAL;
}
static int regulator_check_consumers(struct regulator_dev *rdev, int *low, int *high, int state)
{ (void)rdev; (void)low; (void)high; (void)state; return 0; }
static int _regulator_do_set_voltage(struct regulator_dev *rdev, int low, int high)
{ unsigned selector; return rdev->desc->ops->set_voltage(rdev, low, high, &selector); }
static int regulator_sync_voltage(struct regulator *reg)
{
    voltage_replays++;
    if (!replay_core) return 0;
    reg->voltage[PM_SUSPEND_ON] = (struct regulator_voltage){core_min_uV, core_max_uV};
    return host_actual_regulator_sync_voltage(reg);
}
static int regulator_sync_load(struct regulator *reg)
{
    load_replays++;
    if (!replay_core) return 0;
    reg->uA_load = core_loads[0]; core_secondary.uA_load = core_loads[1];
    reg->rdev->constraints->system_load = core_system_load;
    return host_actual_regulator_sync_load(reg);
}
static unsigned sends, failures[16384];
static bool failed_write_applied;
static u32 physical[2][3], sent_values[16384][3];
static unsigned sent_states[16384];
static pthread_mutex_t transport_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t transport_condition = PTHREAD_COND_INITIALIZER;
static bool block_transport, transport_blocked, release_transport;
static int qcom_rpm_smd_write(struct qcom_smd_rpm *rpm, int state,
                            u32 type, u32 id, void *data, size_t size)
{
    struct kvp { u32 key, length, value; } *packet = data;
    unsigned i, mask = 0, call = sends++;
    assert(rpm == &endpoint && state <= 1 && type == QCOM_SMD_RPM_LDOA && id == 13);
    assert(size == 36 && call < ARRAY_SIZE(failures));
    for (i = 0; i < 3; i++) {
        unsigned key;
        assert(packet[i].length == 4);
        switch (packet[i].key) {
        case 0x00007675: key = 0; break; /* uv */
        case 0x0000616d: key = 1; break; /* ma */
        case 0x6e657773: key = 2; break; /* swen */
        default: assert(0); key = 0;
        }
        assert(!(mask & BIT(key))); mask |= BIT(key);
        sent_values[call][key] = packet[i].value;
        if (key == 1) assert(packet[i].value <= 8191);
        if (key == 2) assert(packet[i].value <= 1);
        if (!failures[call] || failed_write_applied)
            physical[state][key] = packet[i].value;
    }
    assert(mask == 7); sent_states[call] = state;
    pthread_mutex_lock(&transport_mutex);
    if (block_transport) {
        transport_blocked = true; pthread_cond_broadcast(&transport_condition);
        while (!release_transport) pthread_cond_wait(&transport_condition, &transport_mutex);
    }
    pthread_mutex_unlock(&transport_mutex);
    return -(int)failures[call];
}
'''

TESTS = r'''
static struct device bank;
static struct device_node node;
static struct regulator reg;
static struct regulator_desc native_template;
static struct regulator_linear_range native_ranges[3];
static char supply_name[] = "vdd_l13_l19_l23_l24";
static void setup(void)
{
    assert(!live_allocations && !claim.owner);
    memset(&bank, 0, sizeof(bank));
    node = (struct device_node){ .optin = true, .managed_input = true,
                               .sets = 3, .min_uV = 1800000,
                               .max_uV = 2950000, .name = "l13" };
    native_template = pm8994_pldo;
    memcpy(native_ranges, pm8994_pldo.linear_ranges, sizeof(native_ranges));
    native_template.linear_ranges = native_ranges;
    expected_bank = &bank; expected_node = &node;
    memset(&claim, 0, sizeof(claim)); memset(failures, 0, sizeof(failures));
    memset(sent_values, 0, sizeof(sent_values)); memset(physical, 0xa5, sizeof(physical));
    sends = 0; failed_write_applied = false; fail_allocation = 0;
    fail_devres = fail_register = fail_action = fail_init = raced_consumer = false; published = NULL;
    memcpy(supply_name, "vdd_l13_l19_l23_l24", sizeof(supply_name));
    block_transport = transport_blocked = release_transport = false;
    replay_core = false; voltage_replays = load_replays = 0;
    core_min_uV = core_max_uV = 1800000;
    core_loads[0] = 10000; core_loads[1] = core_system_load = 0;
    pause_pm_after_ack = pm_ack_paused = release_pm_freeze = false;
    mutation_bank_attempt = mutation_finished = false;
}
static int create_deferred(void)
{
    int ret = qcom_rpm_physical_register(&bank, &node, &endpoint,
        QCOM_SMD_RPM_LDOA, 13, &native_template, supply_name);
    if (!ret) {
        reg.rdev = published; assert(reg.rdev);
        reg.uA_load = 0; memset(reg.voltage, 0, sizeof(reg.voltage));
        INIT_LIST_HEAD(&reg.list); list_add_tail(&reg.list, &reg.rdev->consumer_list);
        memset(&core_secondary, 0, sizeof(core_secondary)); core_secondary.rdev = reg.rdev;
        INIT_LIST_HEAD(&core_secondary.list);
        list_add_tail(&core_secondary.list, &reg.rdev->consumer_list);
    }
    return ret;
}
static int create(void)
{
    int ret = create_deferred();
    return ret ? ret : qcom_rpm_physical_activate(&bank);
}
static void cleanup(void)
{
    struct regulator_dev *held = published;
    unsigned before = sends;
    bank_release(&bank);
    assert(sends == before); /* Retirement is not a firmware release. */
    if (raced_consumer && held) {
        assert(held->desc->ops->set_load(held, 1000) == -ENODEV);
        assert(held->desc->ops->get_voltage(held) == -ENODEV);
        assert(qcom_rpm_physical_check_ready(&(struct regulator){.rdev = held}) == -ENODEV);
        assert(qcom_rpm_physical_sync(&(struct regulator){.rdev = held}, true) == -ENODEV);
        rdev_put(held);
    }
    assert(!live_allocations && !node.refs);
    assert(devres_allocations == devres_releases);
}
static void bootstrap(void)
{
    unsigned selector = UINT_MAX;
    assert(create() == 0 && !sends);
    assert(reg.rdev->desc->ops->is_enabled(reg.rdev) == -EINVAL);
    assert(reg.rdev->desc->ops->get_voltage(reg.rdev) == -ENODATA);
    assert(reg.rdev->desc->ops->enable(reg.rdev) == -ENODATA && !sends);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 1800000, 1800000, &selector) == 0);
    assert(selector == 74 && !sends);
    assert(reg.rdev->desc->ops->enable(reg.rdev) == -ENODATA && !sends);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 10000) == 0 && !sends);
    assert(qcom_rpm_physical_sync(&reg, true) == -EAGAIN && !sends);
    assert(qcom_rpm_physical_sync(&reg, false) == -ENODATA && !sends);
    assert(reg.rdev->desc->ops->enable(reg.rdev) == 0 && sends == 2);
    assert(reg.rdev->desc->ops->get_voltage(reg.rdev) == 1800000);
    assert(reg.rdev->desc->ops->is_enabled(reg.rdev) == 1);
    for (unsigned s = 0; s < 2; s++) {
        assert(physical[s][0] == 1800000 && physical[s][1] == 10 && physical[s][2] == 1);
        assert(sent_states[s] == s);
    }
}
static void test_voltage_and_load(void)
{
    unsigned before, selector = UINT_MAX;
    setup(); bootstrap();
    assert(reg.rdev->desc->linear_ranges != native_ranges);
    assert(!reg.rdev->desc->supply_name);
    supply_name[0] = 'X';
    assert(!reg.rdev->desc->supply_name);
    native_ranges[1].min_uV = 1;
    assert(reg.rdev->desc->ops->list_voltage(reg.rdev, 74) == 1800000);
    assert(reg.rdev->desc->ops->list_voltage(reg.rdev, 164) == -EINVAL);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 1800001, 1824999, NULL) == -EINVAL);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 1825000, 1850000, &selector) == 0);
    assert(selector == 75 && physical[0][0] == 1825000 && physical[1][0] == 1825000);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 0, 1800000, NULL) == -EINVAL);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 2950000, 1800000, NULL) == -EINVAL);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, -1) == -EINVAL);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 999) == 0 && physical[0][1] == 0);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 1999) == 0 && physical[1][1] == 1);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, INT_MAX) == 0 && physical[0][1] == 8191);
    before = sends; assert(qcom_rpm_physical_sync(&reg, true) == 0 && sends == before + 2);
    assert(reg.rdev->desc->ops->disable(reg.rdev) == 0 && !physical[0][2] && !physical[1][2]);
    before = sends;
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 22000) == 0);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 2950000, 2950000, NULL) == 0 && sends == before);
    assert(reg.rdev->desc->ops->get_voltage(reg.rdev) == 1825000);
    assert(qcom_rpm_physical_sync(&reg, true) == -EAGAIN && sends == before);
    assert(qcom_rpm_physical_sync(&reg, false) == 0 && sends == before + 2);
    assert(physical[0][0] == 2950000 && physical[1][1] == 22 && !physical[1][2]);
    cleanup();
}
static void test_failures(void)
{
    unsigned before, flags;
    for (unsigned state = 0; state < 2; state++) {
        setup(); bootstrap(); before = sends;
        failed_write_applied = true; failures[before + state] = ETIMEDOUT;
        if (state) failures[before + 2] = EIO; /* Preserve first error if rollback fails too. */
        assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 2950000, 2950000, NULL) == -ETIMEDOUT);
        assert(sends == before + 4); /* Attempt BOTH, then restore known BOTH. */
        assert(physical[0][0] == 1800000 && physical[1][0] == 1800000);
        assert(reg.rdev->desc->ops->get_error_flags(reg.rdev, &flags) == 0 && flags == REGULATOR_ERROR_FAIL);
        before = sends;
        assert(qcom_rpm_physical_check_ready(&reg) == -EUCLEAN && sends == before);
        assert(qcom_rpm_physical_sync(&reg, true) == -EUCLEAN && sends == before);
        assert(reg.rdev->desc->ops->set_load(reg.rdev, 50000) == -EUCLEAN && sends == before);
        assert(reg.rdev->desc->ops->disable(reg.rdev) == -EUCLEAN && sends == before);
        failures[before] = ENODEV;
        assert(qcom_rpm_physical_recover(&reg) == -ENODEV && sends == before + 2);
        before = sends;
        assert(qcom_rpm_physical_sync(&reg, true) == -EUCLEAN && sends == before);
        assert(qcom_rpm_physical_recover(&reg) == 0 && sends == before + 2);
        assert(reg.rdev->desc->ops->get_error_flags(reg.rdev, &flags) == 0 && !flags);
        assert(reg.rdev->desc->ops->get_voltage(reg.rdev) == 1800000);
        cleanup();
    }
    setup(); assert(create() == 0);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 2950000, 2950000, NULL) == 0);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 22000) == 0);
    failed_write_applied = true; failures[1] = ETIMEDOUT;
    assert(reg.rdev->desc->ops->enable(reg.rdev) == -ETIMEDOUT && sends == 2);
    before = sends;
    assert(qcom_rpm_physical_recover(&reg) == -ENODATA && sends == before);
    assert(qcom_rpm_physical_check_ready(&reg) == -EUCLEAN && sends == before);
    assert(qcom_rpm_physical_sync(&reg, false) == -EUCLEAN && sends == before);
    assert(reg.rdev->desc->ops->enable(reg.rdev) == -EUCLEAN && sends == before);
    assert(reg.rdev->desc->ops->get_voltage(reg.rdev) == -EUCLEAN);
    cleanup(); assert(claim.tombstone);
    /* A new registration cannot erase an attempted, unacknowledged tuple. */
    assert(create() == -EBUSY && sends == before && !live_allocations);
    assert(!node.refs);
}
static void test_constructor_and_lifetime(void)
{
    unsigned before;
    setup(); node.optin = false; assert(create() == -EINVAL && !live_allocations);
    node.optin = true; node.managed_input = false;
    assert(create() == -EINVAL && !live_allocations);
    node.managed_input = true; node.parent_supply = true;
    assert(create() == -EINVAL && !live_allocations);
    node.parent_supply = false; node.vin_supply = true;
    assert(create() == -EINVAL && !live_allocations);
    node.vin_supply = false; node.sets = 1; assert(create() == -EINVAL && !live_allocations);
    node.sets = 3; node.boot_on = true; assert(create() == -EINVAL && !live_allocations);
    node.boot_on = false; node.always_on = true; assert(create() == -EINVAL && !live_allocations);
    node.always_on = false; node.proxy = true; assert(create() == -EINVAL && !live_allocations);
    node.proxy = false; node.corner = true; assert(create() == -EINVAL && !live_allocations);
    node.corner = false;
    assert(qcom_rpm_physical_register(&bank, &node, &endpoint,
        QCOM_SMD_RPM_LDOA, 26, &native_template, supply_name) == -EINVAL);
    assert(qcom_rpm_physical_register(&bank, &node, &endpoint,
        QCOM_SMD_RPM_SMPA, 1, &native_template, supply_name) == -EINVAL);
    fail_init = true; assert(create() == -ENOMEM && !live_allocations && !node.refs);
    fail_init = false;
    for (unsigned i = 1; i <= 2; i++) {
        fail_allocation = allocation_calls + i;
        assert(create() == -ENOMEM && !live_allocations && !node.refs);
    }
    fail_allocation = 0; fail_devres = true;
    before = devres_allocations;
    assert(create() == -ENOMEM && !live_allocations && devres_allocations == before);
    fail_devres = false; fail_register = true;
    assert(create() == -EIO && !live_allocations && devres_allocations == devres_releases);
    fail_register = false; raced_consumer = true;
    fail_action = true;
    assert(create() == -ENOMEM && published && live_allocations >= 2);
    reg.rdev = published;
    cleanup(); fail_action = false;
    assert(create_deferred() == 0 && !sends);
    assert(qcom_rpm_physical_check_ready(&reg) == -EPROBE_DEFER && !sends);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 1800000, 1800000, NULL) == -EPROBE_DEFER);
    assert(qcom_rpm_physical_sync(&reg, false) == -EPROBE_DEFER && !sends);
    assert(qcom_rpm_physical_activate(&bank) == 0);
    assert(qcom_rpm_physical_check_ready(&reg) == 0 && !sends);
    assert(reg.rdev->desc->ops->get_voltage(reg.rdev) == -ENODATA);
    assert(reg.rdev->desc->ops->is_enabled(reg.rdev) == -EINVAL);
    before = registrations;
    assert(create() == -EBUSY && registrations == before && !sends);
    assert(reg.rdev->refs == 2);
    cleanup(); assert(!claim.tombstone);
    /* Untouched publication may be retried after partial-bank unwind. */
    raced_consumer = false; assert(create() == 0 && !sends); cleanup();
    assert(publication_callbacks == registrations);
    assert(qcom_rpm_physical_check_ready(NULL) == -EINVAL);
    assert(qcom_rpm_physical_check_ready(ERR_PTR(-ENODEV)) == -EINVAL);
    assert(qcom_rpm_physical_sync(NULL, true) == -EINVAL);
    assert(qcom_rpm_physical_recover(ERR_PTR(-ENODEV)) == -EINVAL);
    {
        struct regulator_desc desc = {0}; struct regulator_dev dev = {.desc = &desc};
        struct regulator alien = {.rdev = &dev};
        assert(qcom_rpm_physical_check_ready(&alien) == -EOPNOTSUPP);
        assert(qcom_rpm_physical_sync(&alien, true) == -EOPNOTSUPP);
        assert(qcom_rpm_physical_recover(&alien) == -EOPNOTSUPP);
    }
}
static void test_pm(void)
{
    struct device other_bank = {0};
    unsigned before;
    setup(); assert(create() == 0);
    assert(qcom_rpm_physical_suspend(&bank) == 0 && !sends);
    assert(qcom_rpm_physical_check_ready(&reg) == -EBUSY && !sends);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 10000) == -EBUSY);
    assert(qcom_rpm_physical_sync(&reg, false) == -EBUSY && !sends);
    assert(qcom_rpm_physical_resume(&bank) == 0 && !sends);
    assert(qcom_rpm_physical_check_ready(&reg) == 0 && !sends);
    cleanup(); assert(!claim.tombstone);
    setup(); bootstrap();
    before = sends;
    assert(qcom_rpm_physical_suspend(&other_bank) == 0 && sends == before);
    assert(qcom_rpm_physical_resume(&other_bank) == 0 && sends == before);
    assert(reg.rdev->desc->ops->disable(reg.rdev) == 0);
    assert(reg.rdev->desc->ops->set_voltage(reg.rdev, 2950000, 2950000, NULL) == 0);
    assert(reg.rdev->desc->ops->set_load(reg.rdev, 22000) == 0);
    before = sends;
    assert(qcom_rpm_physical_suspend(&bank) == 0 && sends == before + 2);
    assert(physical[0][0] == 1800000 && physical[1][1] == 10 && !physical[0][2]);
    assert(reg.rdev->desc->ops->enable(reg.rdev) == -EBUSY);
    before = sends;
    assert(qcom_rpm_physical_resume(&bank) == 0 && sends == before + 2);
    assert(physical[1][0] == 1800000 && physical[0][1] == 10 && !physical[1][2]);
    assert(reg.rdev->desc->ops->enable(reg.rdev) == 0);
    assert(physical[0][0] == 2950000 && physical[1][1] == 22 && physical[0][2]);
    before = sends; failures[before + 1] = ETIMEDOUT; failed_write_applied = true;
    assert(qcom_rpm_physical_suspend(&bank) == -ETIMEDOUT && sends == before + 4);
    before = sends;
    assert(qcom_rpm_physical_suspend(&bank) == -EUCLEAN && sends == before);
    assert(qcom_rpm_physical_recover(&reg) == 0);
    assert(qcom_rpm_physical_suspend(&bank) == 0);
    before = sends; failures[before] = ETIMEDOUT;
    assert(qcom_rpm_physical_resume(&bank) == -ETIMEDOUT && sends == before + 4);
    before = sends;
    assert(qcom_rpm_physical_sync(&reg, true) == -EBUSY && sends == before);
    assert(qcom_rpm_physical_resume(&bank) == -EUCLEAN && sends == before);
    assert(qcom_rpm_physical_recover(&reg) == 0 && sends == before + 2);
    before = sends;
    assert(qcom_rpm_physical_sync(&reg, true) == -EBUSY && sends == before);
    assert(qcom_rpm_physical_resume(&bank) == 0 && sends == before + 2);
    assert(qcom_rpm_physical_sync(&reg, true) == 0);
    cleanup();
}
static int host_core_set_load(unsigned consumer, int load_uA)
{
    /* Native core keeps this cache even when the provider callback fails. */
    core_loads[consumer] = load_uA;
    return regulator_set_load(consumer ? &core_secondary : &reg, load_uA);
}
static void test_core_cache_replay(void)
{
    unsigned before, uv_before, load_before;
    setup(); bootstrap(); replay_core = true;
    before = sends; failed_write_applied = true; failures[before] = ETIMEDOUT;
    assert(host_core_set_load(0, 22000) == -ETIMEDOUT && sends == before + 4);
    assert(reg.uA_load == 22000 && core_loads[0] == 22000);
    assert(physical[0][1] == 10 && physical[1][1] == 10);
    before = sends;
    assert(qcom_rpm_physical_sync(&reg, true) == -EUCLEAN && sends == before);
    assert(qcom_rpm_physical_recover(&reg) == 0);
    before = sends;
    /* Recovery restored the provider baseline; the failed core request remains
     * 22mA. Reconcile it without the consumer rewriting its request. */
    assert(reg.uA_load == 22000 && physical[0][1] == 10);
    uv_before = voltage_replays; load_before = load_replays;
    assert(qcom_rpm_physical_sync(&reg, true) == 0 && sends == before + 6);
    assert(voltage_replays == uv_before + 1 && load_replays == load_before + 1);
    assert(physical[0][1] == 22 && physical[1][1] == 22);
    before = sends;
    assert(host_core_set_load(0, 22000) == 0 && sends == before + 2);
    /* Actual regulator_set_load always invokes DRMS, including equal loads. */
    core_loads[1] = 11000; core_system_load = 1000;
    assert(qcom_rpm_physical_sync(&reg, true) == 0);
    assert(physical[0][1] == 34 && physical[1][1] == 34);
    before = sends; load_before = load_replays; core_min_uV = core_max_uV = 0;
    assert(qcom_rpm_physical_sync(&reg, true) == -EINVAL && sends == before);
    assert(load_replays == load_before); /* Bad core voltage must stop load replay. */
    cleanup();
}
static void *freeze_pm_thread(void *unused)
{
    (void)unused; pm_freeze_thread = true;
    assert(qcom_rpm_physical_suspend(&bank) == 0);
    pm_freeze_thread = false; return NULL;
}
static void *mutate_during_freeze(void *unused)
{
    (void)unused; mutating_thread = true;
    mutation_result = reg.rdev->desc->ops->set_load(reg.rdev, 22000);
    mutating_thread = false;
    pthread_mutex_lock(&freeze_lock); mutation_finished = true;
    pthread_cond_broadcast(&freeze_condition); pthread_mutex_unlock(&freeze_lock);
    return NULL;
}
static void test_pm_ack_freeze(void)
{
    pthread_t freezing, mutating;
    unsigned before;
    setup(); bootstrap(); pause_pm_after_ack = true;
    assert(!pthread_create(&freezing, NULL, freeze_pm_thread, NULL));
    pthread_mutex_lock(&freeze_lock);
    while (!pm_ack_paused) pthread_cond_wait(&freeze_condition, &freeze_lock);
    pthread_mutex_unlock(&freeze_lock);
    before = sends;
    assert(!pthread_create(&mutating, NULL, mutate_during_freeze, NULL));
    pthread_mutex_lock(&freeze_lock);
    while (!mutation_bank_attempt && !mutation_finished)
        pthread_cond_wait(&freeze_condition, &freeze_lock);
    /* Pause precisely after PM ACK but before the second-pass freeze flag.
     * An ordinary mutation must wait at the same outer bank lock. */
    assert(mutation_bank_attempt && !mutation_finished);
    release_pm_freeze = true; pthread_cond_broadcast(&freeze_condition);
    pthread_mutex_unlock(&freeze_lock);
    assert(!pthread_join(freezing, NULL) && !pthread_join(mutating, NULL));
    assert(mutation_result == -EBUSY && sends == before);
    assert(physical[0][1] == 10 && physical[1][1] == 10);
    assert(qcom_rpm_physical_resume(&bank) == 0); cleanup();
}
/* Model core serialization and its aggregate callback arguments, not a second
 * implementation of RPM transactions. Native core adds system load once. */
static pthread_mutex_t core_lock = PTHREAD_MUTEX_INITIALIZER;
static int consumer_uv[2], consumer_load[2];
static void *consumer_thread(void *arg)
{
    unsigned index = (uintptr_t)arg;
    for (unsigned i = 0; i < 150; i++) {
        int uv, load;
        pthread_mutex_lock(&core_lock);
        consumer_uv[index] = index ? 2950000 : 1800000;
        consumer_load[index] = index ? 22000 : 10000;
        uv = max(consumer_uv[0], consumer_uv[1]);
        load = consumer_load[0] + consumer_load[1] + 1000;
        assert(reg.rdev->desc->ops->set_voltage(reg.rdev, uv, 2950000, NULL) == 0);
        assert(reg.rdev->desc->ops->set_load(reg.rdev, load) == 0);
        assert(qcom_rpm_physical_sync(&reg, true) == 0);
        pthread_mutex_unlock(&core_lock);
    }
    return NULL;
}
static void *sync_thread(void *unused)
{ (void)unused; assert(qcom_rpm_physical_sync(&reg, true) == 0); return NULL; }
static bool retirement_started, retirement_finished;
static void *retire_thread(void *unused)
{
    (void)unused;
    pthread_mutex_lock(&transport_mutex);
    retirement_started = true; pthread_cond_broadcast(&transport_condition);
    pthread_mutex_unlock(&transport_mutex);
    bank_release(&bank);
    pthread_mutex_lock(&transport_mutex);
    retirement_finished = true; pthread_cond_broadcast(&transport_condition);
    pthread_mutex_unlock(&transport_mutex);
    return NULL;
}
static void test_concurrency(void)
{
    pthread_t first, second;
    unsigned before;
    setup(); bootstrap(); memset(consumer_uv, 0, sizeof(consumer_uv));
    memset(consumer_load, 0, sizeof(consumer_load));
    assert(!pthread_create(&first, NULL, consumer_thread, (void *)(uintptr_t)0));
    assert(!pthread_create(&second, NULL, consumer_thread, (void *)(uintptr_t)1));
    assert(!pthread_join(first, NULL) && !pthread_join(second, NULL));
    assert(physical[0][0] == 2950000 && physical[1][0] == 2950000);
    assert(physical[0][1] == 33 && physical[1][1] == 33);
    cleanup();
    setup(); raced_consumer = true; bootstrap();
    block_transport = true; retirement_started = retirement_finished = false;
    assert(!pthread_create(&first, NULL, sync_thread, NULL));
    pthread_mutex_lock(&transport_mutex);
    while (!transport_blocked) pthread_cond_wait(&transport_condition, &transport_mutex);
    pthread_mutex_unlock(&transport_mutex);
    assert(!pthread_create(&second, NULL, retire_thread, NULL));
    pthread_mutex_lock(&transport_mutex);
    while (!retirement_started) pthread_cond_wait(&transport_condition, &transport_mutex);
    assert(!retirement_finished); release_transport = true;
    pthread_cond_broadcast(&transport_condition); pthread_mutex_unlock(&transport_mutex);
    assert(!pthread_join(first, NULL) && !pthread_join(second, NULL));
    assert(retirement_finished && live_allocations >= 2 && claim.tombstone);
    before = sends;
    assert(qcom_rpm_physical_sync(&reg, true) == -ENODEV && sends == before);
    assert(qcom_rpm_physical_recover(&reg) == -ENODEV && sends == before);
    rdev_put(reg.rdev); assert(!live_allocations && !node.refs);
    assert(devres_allocations == devres_releases);
}
int main(void)
{
    test_voltage_and_load(); test_failures(); test_constructor_and_lifetime(); test_pm();
    test_core_cache_replay(); test_pm_ack_freeze(); test_concurrency();
    puts("PASS: production physical UV/MA/SWEN BOTH transactions, literal values and voltage selectors");
    puts("PASS: partial/lost ACK rollback, explicit recovery, sticky unknown baseline and core aggregate loads");
    puts("PASS: publication allocation failure, final-rdev lifetime, competing consumers and pending-write retirement");
    puts("PASS: bank activation, PM baseline replay, suspended callbacks and explicit failed-resume recovery");
    puts("PASS: failed core-cache replay and mutation blocked between PM ACK and freeze");
    return 0;
}
'''


def harness(source):
    helpers = (ROOT / 'drivers/regulator/helpers.c').read_text()
    core = (ROOT / 'drivers/regulator/core.c').read_text()
    native = (ROOT / 'drivers/regulator/qcom_smd-regulator.c').read_text()
    start = native.index('static const struct regulator_desc pm8994_pldo =')
    template = native[start:native.index('\n};', start) + 3]
    template = template.replace('&rpm_smps_ldo_ops', '&native_ops')
    code = HOST
    code += function(helpers, 'regulator_list_voltage_linear_range')
    code += function(helpers, 'regulator_map_voltage_iterate')
    code += function(core, 'drms_uA_update')
    code += function(core, 'regulator_set_load')
    for name in ('regulator_sync_voltage', 'regulator_sync_load'):
        code += function(core, name).replace('int ' + name + '(', 'static int host_actual_' + name + '(', 1)
    code += 'static const struct regulator_ops native_ops = { .list_voltage = regulator_list_voltage_linear_range };\n'
    code += template + '\n'
    # Compile all production definitions, including constructor and teardown.
    code += re.sub(r'^#include[^\n]*\n', '', source, flags=re.M)
    return code + TESTS


def run(source, compiler_flags, expect_failure=False):
    with tempfile.TemporaryDirectory(prefix='dora-rpm-physical-') as scratch:
        path = Path(scratch)
        c_file, binary = path / 'physical.c', path / 'physical'
        c_file.write_text(harness(source))
        subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu11', '-O2', '-pthread',
                        '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-Wno-unused-function', '-Wno-sign-compare', *compiler_flags,
                        str(c_file), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=expect_failure, text=True)
        if expect_failure:
            assert result.returncode != 0, 'production mutation escaped regression checks'
        else:
            assert result.returncode == 0, f'physical host test exited {result.returncode}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', help='reject in-memory protocol/lifetime regressions')
    args = parser.parse_args()
    source = (ROOT / 'drivers/regulator/qcom-rpm-physical.c').read_text()
    flags = shlex.split(os.environ.get('HOSTCFLAGS', ''))
    run(source, flags)
    if args.self_test:
        mutations = (
            ('UV key', '0x00007675', '0x6e726f63'),
            ('load scaling', 'load_uA / 1000', 'load_uA / 100'),
            ('unknown baseline recovery', 'if (!physical->baseline_valid) {', 'if (false) {'),
            ('descriptor lifetime', 'physical->desc.linear_ranges = physical->ranges;',
             'physical->desc.linear_ranges = template->linear_ranges;'),
            ('core load replay', 'ret = regulator_sync_load(regulator);', 'ret = 0;'),
        )
        for label, before, after in mutations:
            assert before in source, label
            run(source.replace(before, after, 1), flags, expect_failure=True)
            print('PASS: rejected', label, 'mutation')
        change = function(source, 'rpm_physical_change')
        no_bank_lock = change.replace('\tmutex_lock(&rpm_physical_bank_lock);\n', '', 1)
        no_bank_lock = no_bank_lock.replace('\tmutex_unlock(&rpm_physical_bank_lock);\n', '', 1)
        assert no_bank_lock != change
        run(source.replace(change, no_bank_lock, 1), flags, expect_failure=True)
        print('PASS: rejected PM freeze locking mutation')
    print('Host models only; RPM firmware, physical voltage and on-device power collapse remain unverified.')


if __name__ == '__main__':
    main()

