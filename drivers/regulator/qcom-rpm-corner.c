// SPDX-License-Identifier: GPL-2.0-only
/*
 * MSM8996 RPM logical-corner resources using the native SMD transport.
 *
 * Resource, key, logical offset and aggregation semantics are adapted from
 * Sony 4.4 rpm-smd-regulator.c at 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1,
 * Copyright (c) 2012-2017, The Linux Foundation. All rights reserved.
 *
 * Unlike the old asynchronous implementation, both owned sets are explicitly
 * acknowledged. Failed transactions restore the acknowledged snapshot or
 * quarantine an unacknowledged initial transaction at max(old, attempted).
 * No initial physical voltage is inferred, no key named "uv" is sent, and
 * registration/removal never drops an unowned firmware vote.
 */
#include <linux/kref.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regulator/consumer.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/regulator/qcom-rpm-corner.h>
#include <linux/slab.h>
#include <linux/soc/qcom/smd-rpm.h>

#include "internal.h"

#define RPM_CORNER_NONE 1
#define RPM_CORNER_MAX 7
#define RPM_CORNER_ACTIVE BIT(0)
#define RPM_CORNER_SLEEP BIT(1)
#define RPM_CORNER_BOTH (RPM_CORNER_ACTIVE | RPM_CORNER_SLEEP)

enum rpm_corner_key { CORNER, FLOOR, ENABLE, CORNER_KEYS };
static const u32 rpm_corner_keys[] = {
	[CORNER] = 0x6e726f63, /* corn */
	[FLOOR] = 0x00636676, /* vfc */
	[ENABLE] = 0x6e657773, /* swen */
};

struct rpm_corner_values {
	u32 value[CORNER_KEYS];
	u8 valid;
};

struct rpm_corner_request {
	u32 corner; /* Wire value; logical API value is corner + 1. */
	bool voltage_valid;
	bool enabled;
	bool enable_valid;
};

struct rpm_corner_resource;
struct rpm_corner_client {
	struct rpm_corner_resource *resource;
	struct regulator_desc desc;
	struct regulator_init_data *init;
	struct regulator_dev *rdev;
	struct device_node *node;
	struct rpm_corner_request request;
	enum rpm_corner_key key;
	u8 sets;
	bool always_send;
};

struct rpm_corner_resource {
	struct mutex lock;
	struct kref refs;
	struct list_head registry;
	bool claimed;
	struct qcom_smd_rpm *rpm;
	struct rpm_corner_client *clients;
	unsigned int count;
	u32 type, id;
	struct rpm_corner_values wire[2];
	struct rpm_corner_values recovery[2];
	u8 owned;
	u8 recovery_mask;
	bool online;
	bool retired;
	bool suspended;
	bool quarantined;
	bool initial_uncertain;
	int last_error;
};

/* Exactly one apps-side aggregator may own a transport/type/id tuple. */
static LIST_HEAD(rpm_corner_resources);
static DEFINE_MUTEX(rpm_corner_registry_lock);

static int rpm_corner_claim(struct rpm_corner_resource *resource)
{
	struct rpm_corner_resource *other;
	int ret = 0;

	mutex_lock(&rpm_corner_registry_lock);
	list_for_each_entry(other, &rpm_corner_resources, registry)
		if (other->rpm == resource->rpm && other->type == resource->type &&
		    other->id == resource->id) {
			ret = -EBUSY;
			goto out;
		}
	ret = qcom_rpm_smd_claim(resource->rpm, resource->type, resource->id,
				 resource);
	if (ret)
		goto out;
	list_add_tail(&resource->registry, &rpm_corner_resources);
	resource->claimed = true;
out:
	mutex_unlock(&rpm_corner_registry_lock);
	return ret;
}

static void rpm_corner_unclaim(struct rpm_corner_resource *resource)
{
	mutex_lock(&rpm_corner_registry_lock);
	if (resource->claimed) {
		list_del_init(&resource->registry);
		resource->claimed = false;
		qcom_rpm_smd_release(resource->rpm, resource->type, resource->id,
				     resource, resource->owned || resource->quarantined);
	}
	mutex_unlock(&rpm_corner_registry_lock);
}

/* All aggregation/transaction functions below require resource->lock. */
static void rpm_corner_aggregate(struct rpm_corner_resource *resource,
				 struct rpm_corner_values target[2])
{
	struct rpm_corner_client *client;
	unsigned int i, state;

	memset(target, 0, 2 * sizeof(*target));
	for (i = 0; i < resource->count; i++) {
		client = &resource->clients[i];
		for (state = 0; state < 2; state++) {
			if (!(client->sets & BIT(state)))
				continue;
			if (client->request.voltage_valid) {
				target[state].valid |= BIT(client->key);
				target[state].value[client->key] = max(
					target[state].value[client->key],
					client->request.corner);
			}
			if (client->request.enable_valid) {
				target[state].valid |= BIT(ENABLE);
				target[state].value[ENABLE] |= client->request.enabled;
			}
		}
	}
}

static int rpm_corner_send(struct rpm_corner_resource *resource, unsigned int state,
			   const struct rpm_corner_values *target, u8 mask)
{
	struct {
		__le32 key, length, value;
	} packet[CORNER_KEYS] = { };
	unsigned int key, count = 0;
	int ret;

	for (key = 0; key < CORNER_KEYS; key++) {
		if (!(mask & BIT(key)))
			continue;
		packet[count].key = cpu_to_le32(rpm_corner_keys[key]);
		packet[count].length = cpu_to_le32(sizeof(u32));
		packet[count++].value = cpu_to_le32(target->value[key]);
	}
	if (!count)
		return -ENODATA;
	ret = qcom_rpm_smd_write(resource->rpm,
		state ? QCOM_SMD_RPM_SLEEP_STATE : QCOM_SMD_RPM_ACTIVE_STATE,
		resource->type, resource->id, packet, count * sizeof(packet[0]));
	if (ret) {
		/* A failed/lost ACK does not prove that firmware rejected the write. */
		resource->wire[state].valid &= ~mask;
		return ret;
	}
	for (key = 0; key < CORNER_KEYS; key++)
		if (mask & BIT(key))
			resource->wire[state].value[key] = target->value[key];
	resource->wire[state].valid |= mask;
	return 0;
}

static bool rpm_corner_dominates(const struct rpm_corner_values target[2],
				  const struct rpm_corner_values floor[2], u8 mask)
{
	unsigned int state, key;

	for (state = 0; state < 2; state++)
		for (key = 0; key < CORNER_KEYS; key++)
			if ((mask & BIT(key)) &&
			    target[state].value[key] < floor[state].value[key])
				return false;
	return true;
}

static int rpm_corner_write_pair(struct rpm_corner_resource *resource,
				const struct rpm_corner_values target[2], u8 mask)
{
	int first, second;

	first = rpm_corner_send(resource, 0, &target[0], mask);
	second = rpm_corner_send(resource, 1, &target[1], mask);
	return first ? first : second;
}

static int rpm_corner_apply(struct rpm_corner_resource *resource,
			    const struct rpm_corner_values target[2],
			    const struct rpm_corner_values previous[2], bool force)
{
	struct rpm_corner_values snapshot[2];
	u8 mask = resource->owned | target[0].valid | target[1].valid;
	bool known = true, changed = force;
	unsigned int state, key;
	int ret, rollback;

	if (!mask)
		return -ENODATA;
	resource->owned = mask;
	if (resource->quarantined) {
		/* With no prior ACK baseline, only an explicit dominating request
		 * can acquire ownership. Never recover by writing guessed NONE.
		 */
		if (resource->initial_uncertain &&
		    !rpm_corner_dominates(target, resource->recovery, resource->recovery_mask))
			return -EUCLEAN;
		ret = rpm_corner_write_pair(resource,
			resource->initial_uncertain ? target : resource->recovery,
			resource->recovery_mask);
		if (ret) {
			resource->last_error = ret;
			return ret;
		}
		resource->quarantined = false;
		resource->initial_uncertain = false;
		changed = true;
	}
	memcpy(snapshot, resource->wire, sizeof(snapshot));
	for (state = 0; state < 2; state++) {
		known &= (snapshot[state].valid & mask) == mask;
		for (key = 0; key < CORNER_KEYS; key++)
			if ((mask & BIT(key)) &&
			    (!(snapshot[state].valid & BIT(key)) ||
			     snapshot[state].value[key] != target[state].value[key]))
				changed = true;
	}
	if (!changed)
		return 0;
	ret = rpm_corner_write_pair(resource, target, mask);
	if (!ret) {
		resource->last_error = 0;
		return 0;
	}
	resource->quarantined = true;
	resource->initial_uncertain = !known;
	resource->recovery_mask = mask;
	for (state = 0; state < 2; state++) {
		resource->recovery[state] = snapshot[state];
		if (!known)
			for (key = 0; key < CORNER_KEYS; key++)
				resource->recovery[state].value[key] = max(
					max(previous[state].value[key], target[state].value[key]),
					(snapshot[state].valid & BIT(key)) ?
					 snapshot[state].value[key] : 0);
	}
	rollback = rpm_corner_write_pair(resource, resource->recovery, mask);
	if (!rollback && known)
		resource->quarantined = false;
	/* Even a confirmed conservative initial hold is not initial ownership.
	 * The failed caller sees its original error and must explicitly retry.
	 */
	resource->last_error = ret;
	return ret;
}

static int rpm_corner_ready(struct rpm_corner_resource *resource)
{
	if (!resource->online)
		return resource->retired ? -ENODEV : -EPROBE_DEFER;
	if (resource->suspended)
		return -EBUSY;
	return 0;
}

enum rpm_corner_action { SET_CORNER, SET_ENABLE, SET_DISABLE };
static int rpm_corner_change(struct rpm_corner_client *client,
			     enum rpm_corner_action action, u32 value)
{
	struct rpm_corner_resource *resource = client->resource;
	struct rpm_corner_request old;
	struct rpm_corner_values previous[2], target[2];
	bool transmit = false;
	unsigned int i;
	int ret;

	mutex_lock(&resource->lock);
	ret = rpm_corner_ready(resource);
	if (ret)
		goto out;
	old = client->request;
	rpm_corner_aggregate(resource, previous);
	if (action == SET_CORNER) {
		if (value < RPM_CORNER_NONE || value > RPM_CORNER_MAX) {
			ret = -EINVAL;
			goto out;
		}
		client->request.corner = value - RPM_CORNER_NONE;
		client->request.voltage_valid = true;
		transmit = client->always_send || resource->quarantined ||
			(resource->wire[0].valid & BIT(ENABLE)) ||
			(resource->wire[1].valid & BIT(ENABLE));
		for (i = 0; i < resource->count; i++)
			transmit |= resource->clients[i].request.enabled;
	} else {
		if (!client->request.voltage_valid ||
		    (action == SET_DISABLE && !old.enable_valid)) {
			ret = -ENODATA;
			goto out;
		}
		client->request.enabled = action == SET_ENABLE;
		client->request.enable_valid = true;
		transmit = true;
	}
	if (!transmit) {
		ret = 0; /* A disabled request is cached, not proof of voltage. */
		goto out;
	}
	rpm_corner_aggregate(resource, target);
	ret = rpm_corner_apply(resource, target, previous, resource->quarantined);
	if (ret)
		client->request = old;
out:
	mutex_unlock(&resource->lock);
	return ret;
}

static int rpm_corner_set_voltage(struct regulator_dev *rdev, int min, int max,
				 unsigned int *selector)
{
	int ret;

	if (min > max)
		return -EINVAL;
	ret = rpm_corner_change(rdev_get_drvdata(rdev), SET_CORNER, min);
	if (!ret && selector)
		*selector = min - RPM_CORNER_NONE;
	return ret;
}

static int rpm_corner_get_voltage(struct regulator_dev *rdev)
{
	struct rpm_corner_client *client = rdev_get_drvdata(rdev);
	struct rpm_corner_resource *resource = client->resource;
	int ret;

	mutex_lock(&resource->lock);
	ret = client->request.voltage_valid ?
		(int)client->request.corner + RPM_CORNER_NONE : -ENODATA;
	mutex_unlock(&resource->lock);
	return ret;
}

static int rpm_corner_enable(struct regulator_dev *rdev)
{
	return rpm_corner_change(rdev_get_drvdata(rdev), SET_ENABLE, 0);
}

static int rpm_corner_disable(struct regulator_dev *rdev)
{
	return rpm_corner_change(rdev_get_drvdata(rdev), SET_DISABLE, 0);
}

static int rpm_corner_is_enabled(struct regulator_dev *rdev)
{
	struct rpm_corner_client *client = rdev_get_drvdata(rdev);
	int enabled;

	/* This is the logical frontend's enable request, not a PMIC rail read. */
	mutex_lock(&client->resource->lock);
	enabled = client->request.enabled;
	mutex_unlock(&client->resource->lock);
	return enabled;
}

static int rpm_corner_error_flags(struct regulator_dev *rdev, unsigned int *flags)
{
	struct rpm_corner_client *client = rdev_get_drvdata(rdev);

	mutex_lock(&client->resource->lock);
	*flags = client->resource->quarantined ? REGULATOR_ERROR_FAIL : 0;
	mutex_unlock(&client->resource->lock);
	return 0;
}

static const struct regulator_ops rpm_corner_ops = {
	.set_voltage = rpm_corner_set_voltage,
	.get_voltage = rpm_corner_get_voltage,
	.enable = rpm_corner_enable,
	.disable = rpm_corner_disable,
	.is_enabled = rpm_corner_is_enabled,
	.get_error_flags = rpm_corner_error_flags,
	.list_voltage = regulator_list_voltage_linear,
};

int qcom_rpm_corner_sync(struct regulator *regulator, bool require_enabled)
{
	struct rpm_corner_client *client;
	struct rpm_corner_resource *resource;
	struct rpm_corner_values target[2];
	int ret;

	if (!regulator || IS_ERR(regulator))
		return -EINVAL;
	if (regulator->rdev->desc->ops != &rpm_corner_ops)
		return -EOPNOTSUPP;
	client = regulator_get_drvdata(regulator);
	resource = client->resource;
	mutex_lock(&resource->lock);
	ret = rpm_corner_ready(resource);
	if (ret)
		goto out;
	if ((require_enabled && !(client->sets & RPM_CORNER_ACTIVE)) ||
	    (!client->request.enable_valid &&
	     !(client->always_send && client->request.voltage_valid)) ||
	    (require_enabled && !client->request.enabled)) {
		ret = -EAGAIN;
		goto out;
	}
	rpm_corner_aggregate(resource, target);
	ret = rpm_corner_apply(resource, target, target, true);
out:
	mutex_unlock(&resource->lock);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_rpm_corner_sync);

static void rpm_corner_release(struct kref *ref)
{
	struct rpm_corner_resource *resource = container_of(ref,
					struct rpm_corner_resource, refs);
	unsigned int i;

	if (resource->clients)
		for (i = 0; i < resource->count; i++)
			of_node_put(resource->clients[i].node);
	kfree(resource->clients);
	kfree(resource);
}

static void rpm_corner_put(void *data)
{
	struct rpm_corner_resource *resource = data;

	kref_put(&resource->refs, rpm_corner_release);
}

static void rpm_corner_ref_release(struct device *dev, void *data)
{
	struct rpm_corner_resource **resource = data;

	rpm_corner_put(*resource);
}

static int rpm_corner_register_one(struct device *dev,
				    struct rpm_corner_client *client)
{
	struct regulator_config config = { };
	struct rpm_corner_resource **ref;
	struct regulator_dev *rdev;

	/* Allocate before regulator_register publishes the rdev. An allocation
	 * failure after publication must not strand a raced consumer without
	 * its callback-data reference.
	 */
	ref = devres_alloc(rpm_corner_ref_release, sizeof(*ref), GFP_KERNEL);
	if (!ref)
		return -ENOMEM;
	config.dev = dev;
	config.of_node = client->node;
	config.init_data = client->init;
	config.driver_data = client;
	rdev = regulator_register(&client->desc, &config);
	if (IS_ERR(rdev)) {
		devres_free(ref);
		return PTR_ERR(rdev);
	}
	kref_get(&client->resource->refs);
	*ref = client->resource;
	/* devres_add is infallible; the rdev's registration reference prevents
	 * final release until this attachment is installed.
	 */
	devres_add(&rdev->dev, ref);
	client->rdev = rdev;
	return 0;
}

static void rpm_corner_unregister(struct rpm_corner_resource *resource)
{
	unsigned int i;

	mutex_lock(&resource->lock);
	resource->online = false;
	resource->retired = true;
	mutex_unlock(&resource->lock);
	rpm_corner_unclaim(resource);
	for (i = resource->count; i > 0; i--)
		if (resource->clients[i - 1].rdev) {
			regulator_unregister(resource->clients[i - 1].rdev);
			resource->clients[i - 1].rdev = NULL;
		}
}

static int rpm_corner_parse_client(struct device *dev, struct device_node *node,
				   struct rpm_corner_client *client)
{
	struct regulator_init_data *init;
	u32 sets;
	bool corner, floor;

	if (!of_device_is_compatible(node, "qcom,rpm-smd-regulator"))
		return -EINVAL;
	corner = of_property_read_bool(node, "qcom,use-voltage-corner");
	floor = of_property_read_bool(node, "qcom,use-voltage-floor-corner");
	if (corner == floor || of_property_read_u32(node, "qcom,set", &sets) ||
	    !sets || sets & ~RPM_CORNER_BOTH ||
	    of_find_property(node, "parent-supply", NULL))
		return -EINVAL;
	client->key = floor ? FLOOR : CORNER;
	client->sets = sets;
	client->always_send = of_property_read_bool(node, "qcom,always-send-voltage");
	client->node = of_node_get(node);
	client->desc.owner = THIS_MODULE;
	client->desc.type = REGULATOR_VOLTAGE;
	client->desc.ops = &rpm_corner_ops;
	client->desc.n_voltages = RPM_CORNER_MAX;
	client->desc.min_uV = RPM_CORNER_NONE;
	client->desc.uV_step = 1;
	init = of_get_regulator_init_data(dev, node, &client->desc);
	if (!init || !init->constraints.name)
		return -EINVAL;
	if (init->constraints.min_uV < RPM_CORNER_NONE ||
	    init->constraints.max_uV > RPM_CORNER_MAX ||
	    init->constraints.min_uV > init->constraints.max_uV ||
	    init->constraints.always_on || init->constraints.boot_on)
		return -EINVAL;
	init->constraints.valid_ops_mask = REGULATOR_CHANGE_VOLTAGE | REGULATOR_CHANGE_STATUS;
	client->desc.name = init->constraints.name;
	init->constraints.apply_uV = false;
	client->init = init;
	return 0;
}

static int rpm_corner_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rpm_corner_resource *resource;
	struct device_node *node;
	const char *type;
	unsigned int i = 0;
	int ret;

	resource = kzalloc(sizeof(*resource), GFP_KERNEL);
	if (!resource)
		return -ENOMEM;
	mutex_init(&resource->lock);
	kref_init(&resource->refs);
	INIT_LIST_HEAD(&resource->registry);
	ret = of_property_read_string(dev->of_node, "qcom,resource-name", &type);
	if (ret || of_property_read_u32(dev->of_node, "qcom,resource-id", &resource->id)) {
		ret = -EINVAL;
		goto put;
	}
	if (!strcmp(type, "smpa") && (resource->id == 1 || resource->id == 2))
		resource->type = QCOM_SMD_RPM_SMPA;
	else if (!strcmp(type, "ldoa") && resource->id == 26)
		resource->type = QCOM_SMD_RPM_LDOA;
	else {
		ret = -EINVAL;
		goto put;
	}
	resource->count = of_get_available_child_count(dev->of_node);
	if (!resource->count) {
		ret = -EINVAL;
		goto put;
	}
	resource->clients = kcalloc(resource->count, sizeof(*resource->clients), GFP_KERNEL);
	if (!resource->clients) {
		ret = -ENOMEM;
		goto put;
	}
	for_each_available_child_of_node(dev->of_node, node) {
		resource->clients[i].resource = resource;
		ret = rpm_corner_parse_client(dev, node, &resource->clients[i++]);
		if (ret) {
			of_node_put(node);
			goto put;
		}
	}
	resource->rpm = qcom_smd_rpm_get(dev);
	if (IS_ERR(resource->rpm)) {
		ret = PTR_ERR(resource->rpm);
		goto put;
	}
	ret = rpm_corner_claim(resource);
	if (ret)
		goto put;
	/* No provider is operational until every logical client is registered. */
	for (i = 0; i < resource->count; i++) {
		ret = rpm_corner_register_one(dev, &resource->clients[i]);
		if (ret)
			goto unregister;
	}
	platform_set_drvdata(pdev, resource);
	mutex_lock(&resource->lock);
	resource->online = true;
	mutex_unlock(&resource->lock);
	return 0;
unregister:
	rpm_corner_unregister(resource);
put:
	rpm_corner_put(resource);
	return ret;
}

static int rpm_corner_remove(struct platform_device *pdev)
{
	struct rpm_corner_resource *resource = platform_get_drvdata(pdev);

	rpm_corner_unregister(resource);
	platform_set_drvdata(pdev, NULL);
	rpm_corner_put(resource);
	return 0;
}

static int rpm_corner_suspend(struct device *dev)
{
	struct rpm_corner_resource *resource = dev_get_drvdata(dev);
	struct rpm_corner_values target[2];
	int ret = 0;

	mutex_lock(&resource->lock);
	if (resource->owned) {
		rpm_corner_aggregate(resource, target);
		ret = rpm_corner_apply(resource, target, target, true);
	}
	if (!ret)
		resource->suspended = true;
	mutex_unlock(&resource->lock);
	return ret;
}

static int rpm_corner_resume(struct device *dev)
{
	struct rpm_corner_resource *resource = dev_get_drvdata(dev);
	struct rpm_corner_values target[2];
	int ret = 0;

	mutex_lock(&resource->lock);
	if (resource->owned) {
		rpm_corner_aggregate(resource, target);
		ret = rpm_corner_apply(resource, target, target, true);
	}
	if (!ret)
		resource->suspended = false;
	mutex_unlock(&resource->lock);
	return ret;
}

static const struct dev_pm_ops rpm_corner_pm_ops = {
	SET_LATE_SYSTEM_SLEEP_PM_OPS(rpm_corner_suspend, rpm_corner_resume)
};

static const struct of_device_id rpm_corner_match[] = {
	{ .compatible = "qcom,msm8996-rpm-corner-resource" },
	{ }
};
MODULE_DEVICE_TABLE(of, rpm_corner_match);

static struct platform_driver rpm_corner_driver = {
	.probe = rpm_corner_probe,
	.remove = rpm_corner_remove,
	.driver = {
		.name = "msm8996-rpm-corner",
		.of_match_table = rpm_corner_match,
		.pm = &rpm_corner_pm_ops,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(rpm_corner_driver);
MODULE_DESCRIPTION("MSM8996 logical corn/vfc RPM regulator frontend");
MODULE_LICENSE("GPL v2");
