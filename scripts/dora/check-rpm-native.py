#!/usr/bin/env python3
"""Exercise native RPM callbacks, publication and final-rdev lifetime on the host.

Production callbacks, registration and teardown are compiled with real voltage
helpers and a real PM8994 descriptor. Kernel APIs and transport are models;
these tests neither contact firmware nor establish physical rail behavior.
Regulator-core supply resolution and failed-registration cleanup are not run.
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
    match = re.search(r'^(?:static\s+)?(?:int|void|bool|unsigned int)\s+(?:__maybe_unused\s+)?' +
                      re.escape(name) + r'\s*\([^;]*?\)\s*\{', source,
                      re.M | re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def declaration(source, prefix):
    start = source.index(prefix)
    end, depth = source.index('{', start) + 1, 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    assert source[end] == ';'
    return source[start:end + 1] + '\n'


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
#include <stdatomic.h>
typedef uint32_t u32;
typedef uint32_t __le32;
#define GFP_KERNEL 0
#define __maybe_unused
#define EPROBE_DEFER 517
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define THIS_MODULE NULL
#define REGULATOR_VOLTAGE 0
#define REGULATOR_LINEAR_RANGE(v,a,b,s) { (v), (a), (b), (s) }
#define min(a,b) ((a) < (b) ? (a) : (b))
#define cpu_to_le32(x) ((u32)(x))
#define BUG_ON(x) assert(!(x))
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define PTR_ERR(x) ((int)(intptr_t)(x))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define dev_err(...) ((void)0)
#define QCOM_SMD_RPM_ACTIVE_STATE 0
#define QCOM_SMD_RPM_LDOA 0x616f646c
#define CONFIG_REGULATOR_QCOM_SMD_RPM 1
static bool native_builtin = true;
#define IS_BUILTIN(x) native_builtin
struct mutex { pthread_mutex_t value; };
static pthread_mutex_t transport_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t transport_condition = PTHREAD_COND_INITIALIZER;
static bool watch_retirement, retirement_lock_waiting, retirement_thread_known;
static struct mutex *watched_lock;
static pthread_t retirement_thread_id;
static void mutex_init(struct mutex *m)
{ assert(!pthread_mutex_init(&m->value, NULL)); }
static void mutex_lock(struct mutex *m)
{
    pthread_mutex_lock(&transport_mutex);
    if (watch_retirement && retirement_thread_known && m == watched_lock &&
        pthread_equal(pthread_self(), retirement_thread_id)) {
        retirement_lock_waiting = true;
        pthread_cond_broadcast(&transport_condition);
    }
    pthread_mutex_unlock(&transport_mutex);
    assert(!pthread_mutex_lock(&m->value));
}
static void mutex_unlock(struct mutex *m)
{ assert(!pthread_mutex_unlock(&m->value)); }
struct kref { atomic_uint count; };
static void kref_init(struct kref *r) { atomic_init(&r->count, 1); }
static void kref_get(struct kref *r) { assert(atomic_fetch_add(&r->count, 1)); }
static void kref_put(struct kref *r, void (*release)(struct kref *))
{ unsigned old = atomic_fetch_sub(&r->count, 1); assert(old); if (old == 1) release(r); }
struct device;
struct host_record { struct host_record *next; void (*release)(struct device *, void *); };
struct host_action { struct host_action *next; void (*action)(void *); void *data; };
struct device_node {
    const char *name; struct device_node *children, *next;
    const char *compatible; bool optin, rpm_property; unsigned refs, puts;
};
struct device {
    struct host_record *records; struct host_action *actions;
    struct device_node *of_node; struct device *parent; void *driver_data;
    const void *match_data;
};
struct platform_device { struct device dev; };
struct regulator_dev;
struct regulator_ops {
    int (*enable)(struct regulator_dev *);
    int (*disable)(struct regulator_dev *);
    int (*is_enabled)(struct regulator_dev *);
    int (*get_voltage)(struct regulator_dev *);
    int (*set_voltage)(struct regulator_dev *, int, int, unsigned *);
    int (*set_load)(struct regulator_dev *, int);
    int (*list_voltage)(struct regulator_dev *, unsigned);
};
struct regulator_linear_range { unsigned min_uV, min_sel, max_sel, uV_step; };
struct regulator_desc {
    const char *name, *supply_name, *of_match;
    const struct regulator_ops *ops; void *owner; unsigned type;
    unsigned n_voltages, n_linear_ranges;
    const struct regulator_linear_range *linear_ranges;
};
struct regulator_dev {
    struct device dev; const struct regulator_desc *desc; void *data;
    atomic_uint refs; void *supply;
};
struct regulator_config {
    struct device *dev; struct device_node *of_node; void *driver_data;
};
struct qcom_smd_rpm { unsigned unused; };
static struct qcom_smd_rpm endpoint;
static int endpoint_error;
static unsigned endpoint_gets;
static void *rdev_get_drvdata(struct regulator_dev *r) { return r->data; }
static void *dev_get_drvdata(struct device *d) { return d->driver_data; }
static struct qcom_smd_rpm *qcom_smd_rpm_get(struct device *d)
{ (void)d; endpoint_gets++; return endpoint_error ? ERR_PTR(endpoint_error) : &endpoint; }
static const void *of_device_get_match_data(struct device *d) { return d->match_data; }
static bool of_node_name_eq(struct device_node *n, const char *name)
{ return !strcmp(n->name, name); }
static bool of_property_read_bool(struct device_node *n, const char *key)
{ assert(!strcmp(key, "qcom,active-sleep-votes")); return n->optin; }
static bool of_device_is_compatible(struct device_node *n, const char *compatible)
{ return n->compatible && !strcmp(n->compatible, compatible); }
static void *of_find_property(struct device_node *n, const char *key, void *length)
{ (void)length; assert(!strcmp(key, "qcom,rpm")); return n->rpm_property ? n : NULL; }
static void of_node_put(struct device_node *n)
{ assert(n && n->refs); n->refs--; n->puts++; }
#define for_each_available_child_of_node(parent,node) \
    for ((node)=(parent)->children; (node); (node)=(node)->next)
static unsigned live_allocations, allocation_calls, fail_allocation;
static void *kzalloc(size_t size, unsigned flags)
{
    void *p; (void)flags;
    if (++allocation_calls == fail_allocation) return NULL;
    p = calloc(1, size); assert(p); live_allocations++; return p;
}
static void kfree(void *p)
{ if (p) { assert(live_allocations); live_allocations--; free(p); } }
static unsigned devres_allocations, devres_frees;
static bool fail_devres, fail_register, fail_action, raced_consumer;
static bool register_vote, register_vote_failure;
static bool parent_supply;
static bool parent_handle_valid;
static unsigned parent_withdrawals;
static int registration_vote_result;
static void *devres_alloc(void (*release)(struct device *, void *), size_t size,
                          unsigned flags)
{
    struct host_record *record; (void)flags;
    if (fail_devres) return NULL;
    record = calloc(1, sizeof(*record) + size); assert(record);
    record->release = release; devres_allocations++; return record + 1;
}
static void devres_free(void *data)
{ assert(data); devres_frees++; free((struct host_record *)data - 1); }
static void devres_add(struct device *dev, void *data)
{
    struct host_record *record = (struct host_record *)data - 1;
    record->next = dev->records; dev->records = record;
}
static int devm_add_action_or_reset(struct device *dev, void (*action)(void *), void *data)
{
    struct host_action *record;
    if (fail_action) { action(data); return -ENOMEM; }
    record = calloc(1, sizeof(*record)); assert(record);
    record->action = action; record->data = data;
    record->next = dev->actions; dev->actions = record; return 0;
}
static struct regulator_dev *published;
static unsigned registrations, unregistrations;
static struct device *expected_bank;
static struct device_node *expected_node;
static struct regulator_dev *regulator_register(const struct regulator_desc *desc,
                                                const struct regulator_config *config)
{
    struct regulator_dev *rdev;
    assert(devres_allocations > devres_frees);
    assert(config->dev == expected_bank && config->of_node == expected_node);
    rdev = calloc(1, sizeof(*rdev)); assert(rdev);
    rdev->desc = desc; rdev->data = config->driver_data;
    atomic_init(&rdev->refs, 1 + raced_consumer);
    rdev->supply = parent_supply ? &parent_handle_valid : NULL;
    if (parent_supply) parent_handle_valid = true;
    if (register_vote)
        registration_vote_result = desc->ops->enable(rdev);
    if (fail_register || register_vote_failure) {
        free(rdev); return ERR_PTR(-EIO);
    }
    registrations++; published = rdev; return rdev;
}
static void rdev_put(struct regulator_dev *rdev)
{
    unsigned old = atomic_fetch_sub(&rdev->refs, 1);
    assert(old);
    if (old != 1) return;
    while (rdev->dev.records) {
        struct host_record *record = rdev->dev.records;
        rdev->dev.records = record->next;
        record->release(&rdev->dev, record + 1); devres_free(record + 1);
    }
    free(rdev);
}
static void regulator_unregister(struct regulator_dev *rdev)
{
    unregistrations++;
    if (rdev->supply) { parent_withdrawals++; parent_handle_valid = false; }
    rdev_put(rdev);
}
static void bank_release(struct device *dev)
{
    while (dev->actions) {
        struct host_action *record = dev->actions;
        dev->actions = record->next;
        record->action(record->data); free(record);
    }
    while (dev->records) {
        struct host_record *record = dev->records;
        dev->records = record->next;
        record->release(dev, record + 1); devres_free(record + 1);
    }
}
struct host_claim { struct qcom_smd_rpm *rpm; u32 type, id; const void *owner; bool tombstone; };
static struct host_claim claim;
static unsigned claims, claim_releases;
static int claim_error;
static int qcom_rpm_smd_claim(struct qcom_smd_rpm *rpm, u32 type, u32 id,
                             const void *owner)
{
    if (claim_error) return claim_error;
    if (claim.owner || claim.tombstone) return -EBUSY;
    claim = (struct host_claim){rpm, type, id, owner, false}; claims++; return 0;
}
static void qcom_rpm_smd_release(struct qcom_smd_rpm *rpm, u32 type, u32 id,
                                 const void *owner, bool preserve)
{
    assert(claim.owner == owner && claim.rpm == rpm && claim.type == type && claim.id == id);
    claim.owner = NULL; claim.tombstone = preserve; claim_releases++;
}
static struct { u32 key, value; int state; u32 type, id; } sent[128];
static unsigned sends;
static int transport_result;
static bool block_transport, transport_blocked, release_transport;
static int qcom_rpm_smd_write(struct qcom_smd_rpm *rpm, int state,
                            u32 type, u32 id, void *data, size_t size)
{
    struct { u32 key, nbytes, value; } *packet = data;
    assert(rpm == &endpoint && state == QCOM_SMD_RPM_ACTIVE_STATE);
    assert(size == 12 && packet->nbytes == sizeof(u32) && sends < ARRAY_SIZE(sent));
    sent[sends].key = packet->key; sent[sends].value = packet->value;
    sent[sends].state = state; sent[sends].type = type; sent[sends].id = id; sends++;
    pthread_mutex_lock(&transport_mutex);
    if (block_transport) {
        transport_blocked = true; pthread_cond_broadcast(&transport_condition);
        while (!release_transport) pthread_cond_wait(&transport_condition, &transport_mutex);
    }
    pthread_mutex_unlock(&transport_mutex);
    return transport_result;
}
static unsigned physical_registrations, physical_activations, physical_suspends, physical_resumes;
static int physical_result, activation_result;
static int qcom_rpm_physical_register(struct device *dev, struct device_node *node,
    struct qcom_smd_rpm *rpm, u32 type, u32 id,
    const struct regulator_desc *template, const char *supply)
{
    assert(dev == expected_bank && node == expected_node && rpm == &endpoint);
    assert(type == QCOM_SMD_RPM_LDOA && id == 19 && template && supply);
    physical_registrations++; return physical_result;
}
static int qcom_rpm_physical_activate(struct device *d)
{ assert(d == expected_bank); physical_activations++; return activation_result; }
static int qcom_rpm_physical_suspend(struct device *d)
{ assert(d == expected_bank); physical_suspends++; return physical_result; }
static int qcom_rpm_physical_resume(struct device *d)
{ assert(d == expected_bank); physical_resumes++; return physical_result; }
'''

CALLBACK_TESTS = r'''
static void test_callbacks(void)
{
	struct device dev = { 0 };
	struct qcom_rpm_reg vreg = {
		.dev = &dev,
		.rpm = &endpoint,
		.type = 0x616f646c,
		.id = 7,
		.desc = pm8994_pldo,
		.online = true,
	};
	struct regulator_dev rdev = { .desc = &vreg.desc, .data = &vreg };
	const int errors[] = { -ETIMEDOUT, -EIO, 1 };
	const int loads[] = {
		0, 999, 1000, 1001, 8190999, 8191000, 8191999, 8192000, INT_MAX,
	};
	const u32 milliamps[] = { 0, 0, 1, 1, 8190, 8191, 8191, 8191, 8191 };
	unsigned i, before, selector;

	mutex_init(&vreg.lock);
	sends = 0;
	transport_result = 0;
	assert(vreg.desc.ops == &rpm_smps_ldo_ops);
	assert(rpm_reg_is_enabled(&rdev) == 0);
	assert(rpm_reg_get_voltage(&rdev) == 0);
	assert(!sends && !vreg.attempted);

	/* Repeated native requests still transmit, including an unchanged cache. */
	assert(!rpm_reg_enable(&rdev));
	assert(!rpm_reg_enable(&rdev));
	assert(sends == 2 && vreg.attempted && rpm_reg_is_enabled(&rdev) == 1);
	assert(sent[0].key == RPM_KEY_SWEN && sent[0].value == 1);
	assert(sent[1].key == RPM_KEY_SWEN && sent[1].value == 1);
	for (i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		before = sends;
		transport_result = errors[i];
		vreg.attempted = false;
		assert(rpm_reg_disable(&rdev) == errors[i]);
		assert(sends == before + 1 && vreg.attempted);
		assert(sent[before].key == RPM_KEY_SWEN && sent[before].value == 0);
		assert(rpm_reg_is_enabled(&rdev) == 1);
	}
	transport_result = 0;
	before = sends;
	assert(!rpm_reg_disable(&rdev));
	assert(!rpm_reg_disable(&rdev));
	assert(sends == before + 2 && rpm_reg_is_enabled(&rdev) == 0);
	assert(sent[before].key == RPM_KEY_SWEN && sent[before].value == 0);
	assert(sent[before + 1].key == RPM_KEY_SWEN && sent[before + 1].value == 0);
	for (i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		before = sends;
		transport_result = errors[i];
		vreg.attempted = false;
		assert(rpm_reg_enable(&rdev) == errors[i]);
		assert(sends == before + 1 && vreg.attempted);
		assert(sent[before].key == RPM_KEY_SWEN && sent[before].value == 1);
		assert(rpm_reg_is_enabled(&rdev) == 0);
	}
	transport_result = 0;

	/* Invalid and unsupported intervals have no transport side effects. */
	before = sends;
	selector = 55;
	vreg.attempted = false;
	assert(rpm_reg_set_voltage(&rdev, 0, 900000, &selector) == -EINVAL);
	assert(rpm_reg_set_voltage(&rdev, -1, 900000, &selector) == -EINVAL);
	assert(rpm_reg_set_voltage(&rdev, 1000000, 999999, &selector) == -EINVAL);
	assert(rpm_reg_set_voltage(&rdev, 1550001, 1574999, &selector) == -EINVAL);
	assert(sends == before && !vreg.attempted && selector == 55);
	assert(rpm_reg_get_voltage(&rdev) == 0);

	/* Real PM8994 PLDO ranges round upward to the lowest supported voltage. */
	assert(!rpm_reg_set_voltage(&rdev, 1550001, 1600000, &selector));
	assert(sends == before + 1 && vreg.attempted && selector == 65);
	assert(sent[before].key == RPM_KEY_UV && sent[before].value == 1575000);
	assert(rpm_reg_get_voltage(&rdev) == 1575000);
	for (i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		before = sends;
		selector = 55;
		transport_result = errors[i];
		vreg.attempted = false;
		assert(rpm_reg_set_voltage(&rdev, 1600000, 1650000, &selector) == errors[i]);
		assert(sends == before + 1 && vreg.attempted && selector == 55);
		assert(sent[before].key == RPM_KEY_UV && sent[before].value == 1600000);
		assert(rpm_reg_get_voltage(&rdev) == 1575000);
	}
	transport_result = 0;
	before = sends;
	assert(!rpm_reg_set_voltage(&rdev, 1625000, 1625000, NULL));
	assert(sends == before + 1 && sent[before].key == RPM_KEY_UV);
	assert(sent[before].value == 1625000 && rpm_reg_get_voltage(&rdev) == 1625000);

	/* A no-list native descriptor sends the requested minimum unchanged. */
	vreg.desc.ops = &rpm_smps_ldo_ops_fixed;
	vreg.desc.n_voltages = 1;
	selector = 55;
	before = sends;
	assert(!rpm_reg_set_voltage(&rdev, 1500001, 1500002, &selector));
	assert(sends == before + 1 && selector == 55);
	assert(sent[before].key == RPM_KEY_UV && sent[before].value == 1500001);
	assert(rpm_reg_get_voltage(&rdev) == 1500001);
	vreg.desc = pm8994_pldo;

	/* Negative loads cannot become a large unsigned RPM request. */
	before = sends;
	vreg.attempted = false;
	assert(rpm_reg_set_load(&rdev, -1) == -EINVAL);
	assert(rpm_reg_set_load(&rdev, INT_MIN) == -EINVAL);
	assert(sends == before && !vreg.attempted);
	for (i = 0; i < sizeof(loads) / sizeof(loads[0]); i++) {
		before = sends;
		assert(!rpm_reg_set_load(&rdev, loads[i]));
		assert(sends == before + 1 && vreg.attempted);
		assert(sent[before].key == RPM_KEY_MA && sent[before].value == milliamps[i]);
	}
	for (i = 0; i < sizeof(errors) / sizeof(errors[0]); i++) {
		before = sends;
		transport_result = errors[i];
		vreg.attempted = false;
		assert(rpm_reg_set_load(&rdev, 500000) == errors[i]);
		assert(sends == before + 1 && vreg.attempted);
		assert(sent[before].key == RPM_KEY_MA && sent[before].value == 500);
		assert(vreg.uV == 1500001 && vreg.is_enabled == 0);
	}
	transport_result = 0;

	/* Retirement blocks all otherwise-valid callbacks and hides cached state. */
	vreg.online = false;
	vreg.attempted = false;
	selector = 55;
	before = sends;
	assert(rpm_reg_enable(&rdev) == -ENODEV);
	assert(rpm_reg_disable(&rdev) == -ENODEV);
	assert(rpm_reg_is_enabled(&rdev) == -ENODEV);
	assert(rpm_reg_get_voltage(&rdev) == -ENODEV);
	assert(rpm_reg_set_voltage(&rdev, 1550001, 1600000, &selector) == -ENODEV);
	assert(rpm_reg_set_load(&rdev, 1000) == -ENODEV);
	assert(sends == before && !vreg.attempted && selector == 55);
	assert(vreg.uV == 1500001 && vreg.is_enabled == 0);
	/* Argument validation precedes the online check for these callbacks. */
	assert(rpm_reg_set_voltage(&rdev, 0, 900000, &selector) == -EINVAL);
	assert(rpm_reg_set_load(&rdev, -1) == -EINVAL);
	assert(sends == before && !vreg.attempted);

	/* Every observed callback packet belongs to this endpoint/resource/state. */
	assert(sends < sizeof(sent) / sizeof(sent[0]));
	for (i = 0; i < sends; i++) {
		assert(sent[i].state == QCOM_SMD_RPM_ACTIVE_STATE);
		assert(sent[i].type == vreg.type && sent[i].id == vreg.id);
	}
	/* Restore harness globals for the constructor/lifetime test cases. */
	sends = 0;
	transport_result = 0;
}
'''

TESTS = r'''
static struct device parent;
static struct device_node bank_node, node;
static struct platform_device pdev;
static void reset(void)
{
    assert(!live_allocations && !claim.owner);
    assert(!pdev.dev.actions && !pdev.dev.records);
    assert(devres_allocations == devres_frees);
    claim = (struct host_claim){0}; claim_error = 0;
    allocation_calls = fail_allocation = claims = claim_releases = 0;
    devres_allocations = devres_frees = registrations = unregistrations = 0;
    fail_devres = fail_register = fail_action = raced_consumer = false;
    register_vote = register_vote_failure = false; registration_vote_result = 0;
    parent_supply = parent_handle_valid = false; parent_withdrawals = 0;
    native_builtin = true; endpoint_gets = 0; endpoint_error = 0;
    sends = 0; transport_result = 0; published = NULL;
    block_transport = transport_blocked = release_transport = false;
    watch_retirement = retirement_lock_waiting = retirement_thread_known = false;
    watched_lock = NULL;
    physical_registrations = physical_activations = physical_suspends = physical_resumes = 0;
    physical_result = activation_result = 0;
    node = (struct device_node){ .name = "l19", .refs = 1 };
    native_data[0] = production_data[0];
    /* A parentless fixture exercises final-free independently of the real
     * PM8994 table's conservative upstream-lease retention policy. */
    native_data[0].supply = NULL;
    bank_node = (struct device_node){ .name = "regulators", .children = &node,
                                    .compatible = "qcom,rpm-pm8994-regulators" };
    parent = (struct device){ .driver_data = &endpoint };
    pdev = (struct platform_device){ .dev = {
        .parent = &parent, .of_node = &bank_node, .match_data = native_data,
    }};
    expected_bank = &pdev.dev; expected_node = &node;
}
static void no_resources(void)
{
    assert(!live_allocations && !claim.owner);
    assert(devres_allocations == devres_frees);
    assert(!pdev.dev.actions && !pdev.dev.records);
}
static void offline(struct regulator_dev *rdev)
{
    unsigned selector = 999, before = sends;
    assert(rdev->desc->name && !strcmp(rdev->desc->name, "l19"));
    assert(rdev->desc->supply_name == native_data[0].supply);
    assert(rpm_reg_enable(rdev) == -ENODEV);
    assert(rpm_reg_disable(rdev) == -ENODEV);
    assert(rpm_reg_get_voltage(rdev) == -ENODEV);
    assert(rpm_reg_is_enabled(rdev) == -ENODEV);
    assert(rpm_reg_set_voltage(rdev, 1550001, 1600000, &selector) == -ENODEV);
    assert(rpm_reg_set_load(rdev, 1000) == -ENODEV);
    assert(selector == 999 && sends == before);
}
static void test_publication(void)
{
    const void *owner;
    reset(); fail_allocation = 1;
    assert(rpm_reg_probe(&pdev) == -ENOMEM);
    assert(!registrations && !sends); no_resources();

    reset(); fail_devres = true;
    assert(rpm_reg_probe(&pdev) == -ENOMEM);
    assert(!registrations && !sends && !claim.tombstone); no_resources();

    reset(); fail_register = true;
    assert(rpm_reg_probe(&pdev) == -EIO);
    assert(!registrations && !unregistrations && !sends && !claim.tombstone);
    assert(claims == 1 && claim_releases == 1); no_resources();

    /* Registration-time constraints may already have sent an uncertain vote. */
    reset(); register_vote = register_vote_failure = true;
    transport_result = -ETIMEDOUT;
    assert(rpm_reg_probe(&pdev) == -EIO);
    assert(registration_vote_result == -ETIMEDOUT && sends == 1);
    assert(!registrations && !unregistrations && claim.tombstone);
    assert(claim_releases == 1); no_resources();

    reset(); claim_error = -ESHUTDOWN;
    assert(rpm_reg_probe(&pdev) == -ESHUTDOWN);
    assert(!registrations && !sends && !claims && !claim_releases); no_resources();

    reset(); assert(!rpm_reg_probe(&pdev));
    assert(registrations == 1 && !sends && live_allocations == 1 && claim.owner);
    owner = claim.owner;
    assert(rpm_reg_probe(&pdev) == -EBUSY);
    assert(registrations == 1 && !sends && claim.owner == owner && live_allocations == 1);
    assert(!claim_releases);
    bank_release(&pdev.dev);
    assert(unregistrations == 1 && !claim.tombstone); no_resources();

    /* The final-rdev record must already exist if adding bank cleanup fails. */
    reset(); fail_action = raced_consumer = true;
    assert(rpm_reg_probe(&pdev) == -ENOMEM);
    assert(registrations == 1 && unregistrations == 1 && live_allocations == 1);
    assert(!claim.owner && !claim.tombstone && !sends);
    offline(published); rdev_put(published); no_resources();

    reset(); register_vote = true;
    assert(!rpm_reg_probe(&pdev));
    assert(!registration_vote_result && sends == 1);
    assert(rpm_reg_is_enabled(published) == 1);
    bank_release(&pdev.dev);
    assert(claim.tombstone && sends == 1); no_resources();
}
static void test_final_rdev_lifetime(void)
{
    struct regulator_dev *retained;
    reset(); raced_consumer = true;
    assert(!rpm_reg_probe(&pdev)); retained = published;
    assert(!sends && registrations == 1 && live_allocations == 1);
    assert(rpm_reg_get_voltage(retained) == 0);
    bank_release(&pdev.dev);
    assert(unregistrations == 1 && live_allocations == 1);
    assert(!claim.owner && !claim.tombstone);
    offline(retained);
    assert(live_allocations == 1 && !sends);
    rdev_put(retained); no_resources();
}
static pthread_barrier_t final_ref_barrier;
static void final_ref_start(void)
{
    int ret = pthread_barrier_wait(&final_ref_barrier);
    assert(!ret || ret == PTHREAD_BARRIER_SERIAL_THREAD);
}
static void *final_consumer_put_thread(void *data)
{ final_ref_start(); rdev_put(data); return NULL; }
static void *final_provider_remove_thread(void *data)
{ final_ref_start(); bank_release(data); return NULL; }
static void test_raced_final_reference(void)
{
    unsigned i;
    for (i = 0; i < 16; i++) {
        pthread_t consumer, provider;
        reset(); raced_consumer = true;
        assert(!rpm_reg_probe(&pdev));
        assert(!pthread_barrier_init(&final_ref_barrier, NULL, 3));
        assert(!pthread_create(&consumer, NULL, final_consumer_put_thread, published));
        assert(!pthread_create(&provider, NULL, final_provider_remove_thread, &pdev.dev));
        final_ref_start();
        assert(!pthread_join(consumer, NULL) && !pthread_join(provider, NULL));
        assert(!pthread_barrier_destroy(&final_ref_barrier));
        assert(unregistrations == 1 && !claim.tombstone && !sends);
        no_resources();
    }
}
static bool retirement_finished;
static int pending_write_result;
static void *write_thread(void *unused)
{ (void)unused; pending_write_result = rpm_reg_enable(published); return NULL; }
static void *retire_thread(void *unused)
{
    (void)unused;
    pthread_mutex_lock(&transport_mutex); retirement_thread_id = pthread_self();
    retirement_thread_known = true;
    pthread_mutex_unlock(&transport_mutex);
    bank_release(&pdev.dev);
    pthread_mutex_lock(&transport_mutex); retirement_finished = true;
    pthread_cond_broadcast(&transport_condition); pthread_mutex_unlock(&transport_mutex);
    return NULL;
}
static void test_pending_write_removal(void)
{
    pthread_t writer, remover;
    struct regulator_dev *retained;
    reset(); raced_consumer = true;
    assert(!rpm_reg_probe(&pdev)); retained = published;
    watched_lock = &((struct qcom_rpm_reg *)rdev_get_drvdata(retained))->lock;
    block_transport = watch_retirement = true; retirement_finished = false;
    pending_write_result = -EINPROGRESS;
    assert(!pthread_create(&writer, NULL, write_thread, NULL));
    pthread_mutex_lock(&transport_mutex);
    while (!transport_blocked) pthread_cond_wait(&transport_condition, &transport_mutex);
    pthread_mutex_unlock(&transport_mutex);
    assert(!pthread_create(&remover, NULL, retire_thread, NULL));
    pthread_mutex_lock(&transport_mutex);
    while (!retirement_lock_waiting)
        pthread_cond_wait(&transport_condition, &transport_mutex);
    assert(!retirement_finished && live_allocations == 1 && !unregistrations);
    release_transport = true; pthread_cond_broadcast(&transport_condition);
    pthread_mutex_unlock(&transport_mutex);
    assert(!pthread_join(writer, NULL) && !pthread_join(remover, NULL));
    assert(!pending_write_result && retirement_finished && claim.tombstone);
    assert(sends == 1 && live_allocations == 1 && unregistrations == 1);
    offline(retained); rdev_put(retained); no_resources();
}
static void test_optin_dispatch(void)
{
    reset(); node.optin = true; bank_node.rpm_property = true;
    native_data[0] = production_data[0];
    assert(!rpm_reg_probe(&pdev));
    assert(physical_registrations == 1 && physical_activations == 1);
    assert(!registrations && !claims && !sends); no_resources();
    reset(); node.optin = true; bank_node.rpm_property = true; physical_result = -EPROBE_DEFER;
    native_data[0] = production_data[0];
    assert(rpm_reg_probe(&pdev) == -EPROBE_DEFER);
    assert(physical_registrations == 1 && !physical_activations);
    assert(!registrations && !claims && !sends); no_resources();
    reset(); node.optin = true; bank_node.rpm_property = true; activation_result = -EIO;
    native_data[0] = production_data[0];
    assert(rpm_reg_probe(&pdev) == -EIO);
    assert(physical_registrations == 1 && physical_activations == 1);
    assert(!registrations && !claims && !sends); no_resources();
    reset(); node.name = "unknown";
    assert(rpm_reg_probe(&pdev) == -EINVAL);
    assert(!physical_registrations && !registrations && !claims && !sends); no_resources();

    reset(); node.optin = true;
    assert(rpm_reg_probe(&pdev) == -EINVAL);
    assert(!physical_registrations && !registrations && !claims && !sends); no_resources();
    reset(); node.optin = true; bank_node.rpm_property = true;
    bank_node.compatible = "qcom,rpm-pm8998-regulators";
    assert(rpm_reg_probe(&pdev) == -EINVAL);
    assert(!physical_registrations && !registrations && !claims && !sends); no_resources();
    reset(); node.optin = true; bank_node.rpm_property = true; native_builtin = false;
    assert(rpm_reg_probe(&pdev) == -EINVAL);
    assert(!physical_registrations && !registrations && !claims && !sends); no_resources();
    reset(); bank_node.rpm_property = true; endpoint_error = -EPROBE_DEFER;
    assert(rpm_reg_probe(&pdev) == -EPROBE_DEFER);
    assert(endpoint_gets == 1 && !registrations && !claims && !sends); no_resources();
    reset(); parent.driver_data = NULL;
    assert(rpm_reg_probe(&pdev) == -EPROBE_DEFER);
    assert(!endpoint_gets && !registrations && !claims && !sends); no_resources();
    reset(); pdev.dev.match_data = NULL;
    assert(rpm_reg_probe(&pdev) == -ENODEV);
    assert(!registrations && !claims && !sends); no_resources();
}
static void test_module_bank_preflight(void)
{
    unsigned order;
    for (order = 0; order < 2; order++) {
        struct device_node second = { .name = "l19", .refs = 1 };
        struct device_node *optin;
        reset(); native_builtin = false; bank_node.rpm_property = true;
        register_vote = true; node.next = &second;
        optin = order ? &node : &second; optin->optin = true;
        /* Both are known production resources. In legacy-first order a
         * per-child guard alone would publish and send a constraint vote. */
        assert(!strcmp(node.name, production_data[0].name));
        assert(!strcmp(second.name, production_data[0].name));
        assert(rpm_reg_probe(&pdev) == -EINVAL);
        assert(!endpoint_gets && !allocation_calls && !devres_allocations);
        assert(!claims && !claim_releases && !registrations && !unregistrations);
        assert(!sends && !physical_registrations && !physical_activations);
        assert(optin->refs == 0 && optin->puts == 1);
        no_resources();
    }

    /* A module bank with only legacy resources preserves native behavior. */
    reset(); native_builtin = false; bank_node.rpm_property = true;
    register_vote = true;
    assert(!native_data[0].supply);
    assert(!rpm_reg_probe(&pdev));
    assert(endpoint_gets == 1 && registrations == 1 && claims == 1);
    assert(!registration_vote_result && sends == 1 && !physical_registrations);
    assert(rpm_reg_is_enabled(published) == 1);
    bank_release(&pdev.dev);
    assert(unregistrations == 1 && claim.tombstone && !claim.owner);
    no_resources();
}
static void test_partial_bank_and_pm(void)
{
    struct device_node second = { .name = "unknown", .refs = 1 };
    reset(); node.next = &second;
    assert(rpm_reg_probe(&pdev) == -EINVAL);
    assert(registrations == 1 && !physical_activations && !sends);
    /* Model the platform core's devres unwind after the failed probe. */
    bank_release(&pdev.dev); no_resources();
    reset(); physical_result = -ETIMEDOUT;
    assert(rpm_reg_suspend(&pdev.dev) == -ETIMEDOUT);
    assert(rpm_reg_resume(&pdev.dev) == -ETIMEDOUT);
    assert(physical_suspends == 1 && physical_resumes == 1 && !sends);
    no_resources();
}
static void test_parent_lease_retirement(void)
{
    struct regulator_dev *retained;
    reset(); raced_consumer = true;
    native_data[0] = production_data[0];
    assert(!rpm_reg_probe(&pdev)); retained = published;
    /* A declared supply may resolve outside the driver's vote lock. Keep
     * its registration bounded and inert even before supply is populated. */
    assert(!retained->supply);
    bank_release(&pdev.dev);
    assert(!unregistrations && live_allocations == 1 && claim.tombstone);
    offline(retained);
    assert(rpm_reg_probe(&pdev) == -EBUSY);
    assert(registrations == 1 && live_allocations == 1 && !sends);
    rdev_put(retained); rdev_put(retained); no_resources();

    reset(); parent_supply = raced_consumer = true;
    native_data[0] = production_data[0];
    assert(!rpm_reg_probe(&pdev)); retained = published;
    /* A surviving consumer enters core supply handling before native ops.
     * Even an untouched child must retain a resolved upstream handle. */
    bank_release(&pdev.dev);
    assert(!unregistrations && !parent_withdrawals && parent_handle_valid);
    assert(live_allocations == 1 && claim.tombstone);
    assert(retained->supply && *(bool *)retained->supply);
    offline(retained);
    assert(rpm_reg_probe(&pdev) == -EBUSY);
    assert(registrations == 1 && live_allocations == 1 && !sends);
    rdev_put(retained); rdev_put(retained); no_resources();

    reset(); parent_supply = true; register_vote = true;
    native_data[0] = production_data[0];
    assert(!rpm_reg_probe(&pdev)); retained = published;
    assert(sends == 1 && rpm_reg_is_enabled(retained) == 1);
    bank_release(&pdev.dev);
    assert(!unregistrations && !parent_withdrawals && live_allocations == 1);
    assert(claim.tombstone && !claim.owner);
    offline(retained);
    assert(rpm_reg_probe(&pdev) == -EBUSY);
    assert(registrations == 1 && live_allocations == 1 && sends == 1);
    assert(!unregistrations && !parent_withdrawals);
    /* Host process shutdown releases the intentionally retained registration.
     * Provider removal above must never perform this release on a device. */
    rdev_put(retained); no_resources();
}
int main(void)
{
    test_callbacks(); test_publication(); test_final_rdev_lifetime(); test_raced_final_reference();
    test_pending_write_removal(); test_optin_dispatch(); test_module_bank_preflight();
    test_partial_bank_and_pm();
    test_parent_lease_retirement();
    puts("PASS: production native RPM selectors, load bounds, transport failures and offline callbacks");
    puts("PASS: production native RPM claims, publication faults, final-rdev lifetime and blocked-write retirement");
    puts("PASS: production module bank opt-in preflight rejects both child orders before endpoint lookup or sibling votes");
    return 0;
}
'''


def harness(source):
    helpers = (ROOT / 'drivers/regulator/helpers.c').read_text()
    code = HOST
    code += function(helpers, 'regulator_map_voltage_iterate')
    code += function(helpers, 'regulator_list_voltage_linear_range')
    for match in re.finditer(r'^struct (\w+) \{', source, re.M):
        code += declaration(source, match.group(0))
    for match in re.finditer(r'^#define RPM_KEY_\w+[^\n]*', source, re.M):
        code += match.group(0) + '\n'
    callbacks = ('rpm_reg_write_active', 'rpm_reg_enable', 'rpm_reg_is_enabled',
                 'rpm_reg_disable', 'rpm_reg_get_voltage', 'rpm_reg_set_voltage',
                 'rpm_reg_set_load')
    for name in callbacks:
        code += function(source, name)
    for name in ('rpm_smps_ldo_ops', 'rpm_smps_ldo_ops_fixed',
                 'rpm_switch_ops', 'rpm_bob_ops'):
        code += declaration(source, 'static const struct regulator_ops ' + name)
    code += declaration(source, 'static const struct regulator_desc pm8994_pldo')
    entry = re.search(r'^\s*\{ "l19", QCOM_SMD_RPM_LDOA, 19, &pm8994_pldo,[^\n]+',
                      source, re.M)
    assert entry, 'actual PM8994 l19 entry'
    code += 'static const struct rpm_regulator_data production_data[] = {\n' + entry[0] + '\n{} };\n'
    code += 'static struct rpm_regulator_data native_data[2];\n'
    for match in re.finditer(r'^static (?:int|void|bool)\s+(?:__maybe_unused\s+)?(rpm_\w+)\s*\(',
                             source, re.M):
        name = match[1]
        if name not in callbacks and name not in ('rpm_reg_init', 'rpm_reg_exit'):
            code += function(source, name)
    return code + CALLBACK_TESTS + TESTS


def run(source, expect_failure=False, allow_timeout=False):
    with tempfile.TemporaryDirectory(prefix='dora-rpm-native-') as directory:
        path = Path(directory)
        c_file, binary = path / 'native.c', path / 'native'
        c_file.write_text(harness(source))
        flags = ['-std=gnu11', '-O2', '-pthread', '-Wall', '-Wextra', '-Werror',
                 '-Wno-unused-parameter', '-Wno-unused-function', '-Wno-unused-const-variable',
                 '-Wno-sign-compare', '-fsanitize=' + os.environ.get('DORA_SANITIZERS', 'undefined'),
                 '-fno-sanitize-recover=all']
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) + flags +
                       shlex.split(os.environ.get('HOSTCFLAGS', '')) +
                       [str(c_file), '-o', str(binary)], check=True)
        try:
            result = subprocess.run([str(binary)], check=False,
                                    capture_output=expect_failure, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            assert expect_failure and allow_timeout, 'native lifetime test timed out'
            return
        if expect_failure:
            assert result.returncode, 'production mutation escaped regression checks'
        else:
            assert not result.returncode, f'native host test exited {result.returncode}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true',
                        help='reject in-memory selector and lifetime regressions')
    args = parser.parse_args()
    source = (ROOT / 'drivers/regulator/qcom_smd-regulator.c').read_text()
    run(source)
    if args.self_test:
        mutations = (
            ('selector publication', '*selector = selected;', '(void)selected;'),
            ('negative load', 'if (load_uA < 0)', 'if (false)'),
            ('offline callbacks', 'if (!vreg->online)', 'if (false)'),
            ('final-rdev ownership', 'devres_add(&rdev->dev, ref);', 'devres_add(dev, ref);'),
            ('retired parent guard', 'vreg->attempted || retain_parent', 'vreg->attempted'),
        )
        for label, before, after in mutations:
            assert source.count(before) == 1, label
            run(source.replace(before, after, 1), expect_failure=True)
            print('PASS: rejected', label, 'mutation')
        probe = function(source, 'rpm_reg_probe')
        preflight = re.search(r'\tif \(!IS_BUILTIN\(CONFIG_REGULATOR_QCOM_SMD_RPM\)\)\s*\{',
                              probe)
        assert preflight, 'upfront native module opt-in preflight'
        end, depth = preflight.end(), 1
        while depth:
            depth += (probe[end] == '{') - (probe[end] == '}')
            end += 1
        removed = probe[:preflight.start()] + probe[end:]
        run(source.replace(probe, removed, 1), expect_failure=True)
        print('PASS: rejected module bank preflight removal while per-child opt-in guard remains')
        retirement = function(source, 'rpm_reg_unregister')
        before = 'mutex_lock(&vreg->lock);'
        assert retirement.count(before) == 1
        unlocked = retirement.replace(before, '(void)vreg;', 1).replace(
            'mutex_unlock(&vreg->lock);', '(void)vreg;', 1)
        run(source.replace(retirement, unlocked, 1), expect_failure=True, allow_timeout=True)
        print('PASS: rejected retirement-lock mutation')
        before = 'vreg->desc.supply_name;'
        assert retirement.count(before) == 1
        attempted_only = retirement.replace(before,
            '(vreg->attempted && vreg->rdev->supply);', 1)
        run(source.replace(retirement, attempted_only, 1), expect_failure=True)
        print('PASS: rejected untouched-parent retirement mutation')
    print('Host models only; firmware ACKs, physical rails and device removal remain hardware validation gates.')


if __name__ == '__main__':
    main()
