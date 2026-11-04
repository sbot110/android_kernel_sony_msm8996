// SPDX-License-Identifier: GPL-2.0-only
/*
 * Opt-in PM8994 physical regulators with acknowledged ACTIVE/SLEEP requests.
 *
 * Literal uv/ma/swen semantics follow Sony 4.4 rpm-smd-regulator.c at
 * 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1. The native regulator core owns
 * consumer voltage, load and enable aggregation. ACKs describe requests
 * processed by RPM; they are not measurements of the PMIC output voltage.
 */
#include <linux/kref.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/regulator/qcom-rpm-physical.h>
#include <linux/slab.h>
#include <linux/soc/qcom/smd-rpm.h>

#include "internal.h"

#define RPM_PHYSICAL_MAX_MA 0x1fff
#define RPM_PHYSICAL_BOTH 3

enum rpm_physical_key { PHYSICAL_UV, PHYSICAL_MA, PHYSICAL_SWEN, PHYSICAL_KEYS };
static const u32 rpm_physical_keys[] = {
	[PHYSICAL_UV] = 0x00007675, /* uv: microvolts */
	[PHYSICAL_MA] = 0x0000616d, /* ma: milliamps */
	[PHYSICAL_SWEN] = 0x6e657773, /* swen: software enable */
};

struct rpm_physical_values {
	u32 value[PHYSICAL_KEYS];
};

struct rpm_physical_request {
	u32 uv, ma;
	bool voltage_valid, load_valid;
	bool enabled, enable_valid;
};

struct rpm_physical {
	struct mutex lock;
	struct kref refs;
	struct list_head bank_list;
	struct device *bank;
	struct qcom_smd_rpm *rpm;
	struct regulator_desc desc;
	struct regulator_init_data *init;
	struct device_node *node;
	struct regulator_dev *rdev;
	struct regulator_linear_range *ranges;
	u32 type, id;
	struct rpm_physical_request request;
	struct rpm_physical_values baseline[2];
	bool baseline_valid;
	bool attempted;
	bool claimed;
	bool online;
	bool retired;
	bool suspended;
	bool quarantined;
	int last_error;
};

static const struct regulator_ops rpm_physical_ops;
static LIST_HEAD(rpm_physical_banks);
static DEFINE_MUTEX(rpm_physical_bank_lock);

/* All protocol state below is serialized by physical->lock. */
static int rpm_physical_ready(struct rpm_physical *physical)
{
	if (!physical->online)
		return physical->retired ? -ENODEV : -EPROBE_DEFER;
	if (physical->suspended)
		return -EBUSY;
	return 0;
}

static void rpm_physical_target(struct rpm_physical *physical,
				struct rpm_physical_values target[2])
{
	target[0].value[PHYSICAL_UV] = physical->request.uv;
	target[0].value[PHYSICAL_MA] = physical->request.ma;
	target[0].value[PHYSICAL_SWEN] = physical->request.enabled;
	target[1] = target[0];
}

static int rpm_physical_send(struct rpm_physical *physical, unsigned int state,
			     const struct rpm_physical_values *target)
{
	struct {
		__le32 key, length, value;
	} packet[PHYSICAL_KEYS];
	unsigned int key;

	for (key = 0; key < PHYSICAL_KEYS; key++) {
		packet[key].key = cpu_to_le32(rpm_physical_keys[key]);
		packet[key].length = cpu_to_le32(sizeof(u32));
		packet[key].value = cpu_to_le32(target->value[key]);
	}
	/* Even an error can follow a request accepted by firmware. Retirement
	 * must preserve this tuple after the very first attempted write.
	 */
	physical->attempted = true;
	return qcom_rpm_smd_write(physical->rpm,
		state ? QCOM_SMD_RPM_SLEEP_STATE : QCOM_SMD_RPM_ACTIVE_STATE,
		physical->type, physical->id, packet, sizeof(packet));
}

static int rpm_physical_write_pair(struct rpm_physical *physical,
				   const struct rpm_physical_values target[2])
{
	int first, second;

	first = rpm_physical_send(physical, 0, &target[0]);
	second = rpm_physical_send(physical, 1, &target[1]);
	return first ? first : second;
}

static int rpm_physical_transaction(struct rpm_physical *physical,
				    const struct rpm_physical_values target[2])
{
	int ret;

	if (physical->quarantined)
		return -EUCLEAN;
	ret = rpm_physical_write_pair(physical, target);
	if (!ret) {
		memcpy(physical->baseline, target, sizeof(physical->baseline));
		physical->baseline_valid = true;
		physical->last_error = 0;
		return 0;
	}
	physical->quarantined = true;
	physical->last_error = ret;
	/* An earlier full BOTH ACK is the only rollback authority. A first
	 * failed request has no baseline: never substitute an invented zero.
	 * Successful rollback still needs a separate explicit recovery call.
	 */
	if (physical->baseline_valid)
		rpm_physical_write_pair(physical, physical->baseline);
	return ret;
}

static int rpm_physical_apply(struct rpm_physical *physical)
{
	struct rpm_physical_values target[2];

	if (physical->quarantined)
		return -EUCLEAN;
	if (!physical->request.voltage_valid || !physical->request.load_valid ||
	    !physical->request.enable_valid)
		return -ENODATA;
	rpm_physical_target(physical, target);
	return rpm_physical_transaction(physical, target);
}

enum rpm_physical_action { PHYSICAL_SET_UV, PHYSICAL_SET_LOAD,
			   PHYSICAL_ENABLE, PHYSICAL_DISABLE };

static int rpm_physical_change(struct rpm_physical *physical,
			       enum rpm_physical_action action, u32 value)
{
	struct rpm_physical_request old;
	int ret;

	/* Core calls this with its rdev lock held. PM/removal never take that
	 * lock: all physical mutations share bank -> resource ordering instead.
	 */
	mutex_lock(&rpm_physical_bank_lock);
	mutex_lock(&physical->lock);
	ret = rpm_physical_ready(physical);
	if (ret)
		goto out;
	if (physical->quarantined) {
		ret = -EUCLEAN;
		goto out;
	}
	old = physical->request;
	switch (action) {
	case PHYSICAL_SET_UV:
		physical->request.uv = value;
		physical->request.voltage_valid = true;
		break;
	case PHYSICAL_SET_LOAD:
		physical->request.ma = value;
		physical->request.load_valid = true;
		break;
	case PHYSICAL_ENABLE:
		if (!old.voltage_valid || !old.load_valid) {
			ret = -ENODATA;
			goto out;
		}
		physical->request.enabled = true;
		physical->request.enable_valid = true;
		break;
	case PHYSICAL_DISABLE:
		if (!old.enable_valid || !physical->baseline_valid) {
			ret = -ENODATA;
			goto out;
		}
		physical->request.enabled = false;
		break;
	default:
		ret = -EINVAL;
		goto out;
	}
	/* A disabled voltage/load request is cached until complete enable or
	 * explicit sync. It grants no ownership of a firmware enable vote.
	 */
	if ((action == PHYSICAL_SET_UV || action == PHYSICAL_SET_LOAD) &&
	    !physical->request.enabled) {
		ret = 0;
		goto out;
	}
	ret = rpm_physical_apply(physical);
	if (ret)
		physical->request = old;
out:
	mutex_unlock(&physical->lock);
	mutex_unlock(&rpm_physical_bank_lock);
	return ret;
}

static int rpm_physical_list_voltage(struct regulator_dev *rdev,
				      unsigned int selector)
{
	if (selector >= rdev->desc->n_voltages)
		return -EINVAL;
	return regulator_list_voltage_linear_range(rdev, selector);
}

static int rpm_physical_set_voltage(struct regulator_dev *rdev, int min_uV,
				     int max_uV, unsigned int *selector)
{
	int selection, voltage, ret;

	if (min_uV <= 0 || min_uV > max_uV)
		return -EINVAL;
	selection = regulator_map_voltage_iterate(rdev, min_uV, max_uV);
	if (selection < 0)
		return selection;
	voltage = rpm_physical_list_voltage(rdev, selection);
	if (voltage <= 0)
		return -EINVAL;
	ret = rpm_physical_change(rdev_get_drvdata(rdev), PHYSICAL_SET_UV, voltage);
	if (!ret && selector)
		*selector = selection;
	return ret;
}

static int rpm_physical_get_voltage(struct regulator_dev *rdev)
{
	struct rpm_physical *physical = rdev_get_drvdata(rdev);
	int ret;

	mutex_lock(&physical->lock);
	ret = rpm_physical_ready(physical);
	if (!ret)
		ret = physical->quarantined ? -EUCLEAN :
			physical->baseline_valid ?
			(int)physical->baseline[0].value[PHYSICAL_UV] : -ENODATA;
	mutex_unlock(&physical->lock);
	return ret;
}

static int rpm_physical_set_load(struct regulator_dev *rdev, int load_uA)
{
	if (load_uA < 0)
		return -EINVAL;
	/* Core already sums consumers plus regulator-system-load, in uA. */
	return rpm_physical_change(rdev_get_drvdata(rdev), PHYSICAL_SET_LOAD,
				   min_t(u32, load_uA / 1000, RPM_PHYSICAL_MAX_MA));
}

static int rpm_physical_enable(struct regulator_dev *rdev)
{
	return rpm_physical_change(rdev_get_drvdata(rdev), PHYSICAL_ENABLE, 0);
}

static int rpm_physical_disable(struct regulator_dev *rdev)
{
	return rpm_physical_change(rdev_get_drvdata(rdev), PHYSICAL_DISABLE, 0);
}

static int rpm_physical_is_enabled(struct regulator_dev *rdev)
{
	struct rpm_physical *physical = rdev_get_drvdata(rdev);
	int ret;

	mutex_lock(&physical->lock);
	ret = rpm_physical_ready(physical);
	if (!ret)
		/* -EINVAL is the core's documented unknown-status path, allowing
		 * the first enable callback. It is not a physical off reading.
		 */
		ret = physical->quarantined ? -EUCLEAN :
			physical->baseline_valid ?
			(int)physical->baseline[0].value[PHYSICAL_SWEN] : -EINVAL;
	mutex_unlock(&physical->lock);
	return ret;
}

static int rpm_physical_error_flags(struct regulator_dev *rdev, unsigned int *flags)
{
	struct rpm_physical *physical = rdev_get_drvdata(rdev);

	mutex_lock(&physical->lock);
	*flags = physical->quarantined ? REGULATOR_ERROR_FAIL : 0;
	mutex_unlock(&physical->lock);
	return 0;
}

static const struct regulator_ops rpm_physical_ops = {
	.list_voltage = rpm_physical_list_voltage,
	.set_voltage = rpm_physical_set_voltage,
	.get_voltage = rpm_physical_get_voltage,
	.set_load = rpm_physical_set_load,
	.enable = rpm_physical_enable,
	.disable = rpm_physical_disable,
	.is_enabled = rpm_physical_is_enabled,
	.get_error_flags = rpm_physical_error_flags,
};

int qcom_rpm_physical_check_ready(struct regulator *regulator)
{
	struct rpm_physical *physical;
	int ret;

	if (!regulator || IS_ERR(regulator))
		return -EINVAL;
	if (regulator->rdev->desc->ops != &rpm_physical_ops)
		return -EOPNOTSUPP;
	physical = regulator_get_drvdata(regulator);
	mutex_lock(&physical->lock);
	ret = rpm_physical_ready(physical);
	if (!ret && physical->quarantined)
		ret = -EUCLEAN;
	mutex_unlock(&physical->lock);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_check_ready);

int qcom_rpm_physical_sync(struct regulator *regulator, bool require_enabled)
{
	struct rpm_physical *physical;
	int ret;

	if (!regulator || IS_ERR(regulator))
		return -EINVAL;
	if (regulator->rdev->desc->ops != &rpm_physical_ops)
		return -EOPNOTSUPP;
	physical = regulator_get_drvdata(regulator);
	/* Check quarantine before calling core replay APIs. An unknown initial
	 * transaction cannot recover through their ordinary callback path.
	 */
	mutex_lock(&physical->lock);
	ret = rpm_physical_ready(physical);
	if (!ret && physical->quarantined)
		ret = -EUCLEAN;
	if (!ret && require_enabled && !physical->request.enabled)
		ret = -EAGAIN;
	if (!ret && !physical->request.enable_valid)
		ret = -ENODATA;
	mutex_unlock(&physical->lock);
	if (ret)
		return ret;
	/* A failed core set_load keeps its new consumer load cached even when
	 * this backend rolled its request back. Rebuild BOTH aggregate requests
	 * through real callbacks; cached consumer/provider success is insufficient.
	 * Never hold bank_lock while taking the regulator core's rdev lock.
	 */
	ret = regulator_sync_voltage(regulator);
	if (ret)
		return ret;
	ret = regulator_sync_load(regulator);
	if (ret)
		return ret;
	mutex_lock(&rpm_physical_bank_lock);
	mutex_lock(&physical->lock);
	ret = rpm_physical_ready(physical);
	if (!ret && physical->quarantined)
		ret = -EUCLEAN;
	if (!ret && require_enabled && !physical->request.enabled)
		ret = -EAGAIN;
	if (!ret)
		ret = rpm_physical_apply(physical);
	mutex_unlock(&physical->lock);
	mutex_unlock(&rpm_physical_bank_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_sync);

int qcom_rpm_physical_recover(struct regulator *regulator)
{
	struct rpm_physical *physical;
	int ret;

	if (!regulator || IS_ERR(regulator))
		return -EINVAL;
	if (regulator->rdev->desc->ops != &rpm_physical_ops)
		return -EOPNOTSUPP;
	physical = regulator_get_drvdata(regulator);
	mutex_lock(&rpm_physical_bank_lock);
	mutex_lock(&physical->lock);
	/* A failed resume remains suspended. Explicit baseline recovery is
	 * permitted there; ordinary callbacks stay blocked until resume ACKs.
	 */
	ret = physical->online ? 0 :
		physical->retired ? -ENODEV : -EPROBE_DEFER;
	if (ret)
		goto out;
	if (!physical->baseline_valid) {
		ret = -ENODATA;
		goto out;
	}
	ret = rpm_physical_write_pair(physical, physical->baseline);
	if (!ret) {
		physical->quarantined = false;
		physical->last_error = 0;
	} else {
		physical->quarantined = true;
		physical->last_error = ret;
	}
out:
	mutex_unlock(&physical->lock);
	mutex_unlock(&rpm_physical_bank_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_recover);

static int rpm_physical_bank_pm(struct device *bank, bool resume)
{
	struct rpm_physical *physical;
	struct rpm_physical_values baseline[2];
	int ret = 0, error;

	/* Removal takes these locks in the same order. The list reference is
	 * therefore stable through every sleeping transport transaction.
	 */
	mutex_lock(&rpm_physical_bank_lock);
	list_for_each_entry(physical, &rpm_physical_banks, bank_list) {
		if (physical->bank != bank)
			continue;
		mutex_lock(&physical->lock);
		error = 0;
		if (!physical->online)
			error = -ENODEV;
		else if (physical->quarantined)
			error = -EUCLEAN;
		else if (physical->baseline_valid) {
			/* PM preserves only the owned ACK baseline, including when
			 * disabled consumers cached a future voltage/load request.
			 */
			memcpy(baseline, physical->baseline, sizeof(baseline));
			error = rpm_physical_transaction(physical, baseline);
		}
		if (resume && !error)
			physical->suspended = false;
		if (!ret)
			ret = error;
		mutex_unlock(&physical->lock);
	}
	if (!resume && !ret)
		list_for_each_entry(physical, &rpm_physical_banks, bank_list) {
			if (physical->bank != bank)
				continue;
			mutex_lock(&physical->lock);
			physical->suspended = true;
			mutex_unlock(&physical->lock);
		}
	mutex_unlock(&rpm_physical_bank_lock);
	return ret;
}

int qcom_rpm_physical_suspend(struct device *bank)
{
	return rpm_physical_bank_pm(bank, false);
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_suspend);

int qcom_rpm_physical_resume(struct device *bank)
{
	return rpm_physical_bank_pm(bank, true);
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_resume);

int qcom_rpm_physical_activate(struct device *bank)
{
	struct rpm_physical *physical;

	mutex_lock(&rpm_physical_bank_lock);
	list_for_each_entry(physical, &rpm_physical_banks, bank_list) {
		if (physical->bank != bank)
			continue;
		mutex_lock(&physical->lock);
		physical->online = true;
		mutex_unlock(&physical->lock);
	}
	mutex_unlock(&rpm_physical_bank_lock);
	return 0;
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_activate);

static void rpm_physical_release(struct kref *ref)
{
	struct rpm_physical *physical = container_of(ref, struct rpm_physical, refs);

	of_node_put(physical->node);
	kfree(physical->ranges);
	kfree(physical);
}

static void rpm_physical_put(void *data)
{
	struct rpm_physical *physical = data;

	kref_put(&physical->refs, rpm_physical_release);
}

static void rpm_physical_ref_release(struct device *dev, void *data)
{
	struct rpm_physical **physical = data;

	rpm_physical_put(*physical);
}

static void rpm_physical_unregister(void *data)
{
	struct rpm_physical *physical = data;

	/* Wait for an in-flight write/rollback before making callbacks inert.
	 * Neither bank teardown nor surviving regulator handles send releases.
	 */
	mutex_lock(&rpm_physical_bank_lock);
	mutex_lock(&physical->lock);
	list_del_init(&physical->bank_list);
	physical->online = false;
	physical->retired = true;
	if (physical->claimed) {
		qcom_rpm_smd_release(physical->rpm, physical->type, physical->id,
				     physical, physical->attempted);
		physical->claimed = false;
	}
	mutex_unlock(&physical->lock);
	mutex_unlock(&rpm_physical_bank_lock);
	if (physical->rdev) {
		regulator_unregister(physical->rdev);
		physical->rdev = NULL;
	}
	rpm_physical_put(physical);
}

static int rpm_physical_register_one(struct device *dev,
				     struct rpm_physical *physical)
{
	struct regulator_config config = { };
	struct rpm_physical **ref;
	struct regulator_dev *rdev;

	/* A consumer can race publication. Allocate its final-rdev ownership
	 * record before regulator_register(), then install it infallibly.
	 */
	ref = devres_alloc(rpm_physical_ref_release, sizeof(*ref), GFP_KERNEL);
	if (!ref)
		return -ENOMEM;
	config.dev = dev;
	config.of_node = physical->node;
	config.init_data = physical->init;
	config.driver_data = physical;
	rdev = regulator_register(&physical->desc, &config);
	if (IS_ERR(rdev)) {
		devres_free(ref);
		return PTR_ERR(rdev);
	}
	kref_get(&physical->refs);
	*ref = physical;
	devres_add(&rdev->dev, ref);
	physical->rdev = rdev;
	return 0;
}

int qcom_rpm_physical_register(struct device *dev, struct device_node *node,
			      struct qcom_smd_rpm *rpm, u32 type, u32 id,
			      const struct regulator_desc *template,
			      const char *supply_name)
{
	struct rpm_physical *physical;
	struct regulator_init_data *init;
	u32 sets;
	int ret;

	if (!dev || !node || !rpm || IS_ERR(rpm) || !template ||
	    !template->ops || template->ops->list_voltage !=
					regulator_list_voltage_linear_range ||
	    !template->linear_ranges || !template->n_linear_ranges ||
	    !template->n_voltages ||
	    !of_property_read_bool(node, "qcom,active-sleep-votes") ||
	    !of_property_read_bool(node, "qcom,rpm-managed-input") ||
	    of_property_read_u32(node, "qcom,set", &sets) || sets != RPM_PHYSICAL_BOTH)
		return -EINVAL;
	if (!((type == QCOM_SMD_RPM_SMPA && id == 4) ||
	      (type == QCOM_SMD_RPM_LDOA &&
	       (id == 12 || id == 13 || id == 20 || id == 21))))
		return -EINVAL;
	/* Registration must not invoke the old automatic proxy consumer. The
	 * clock owner supplies L12's source-derived 10mA bootstrap explicitly.
	 */
	if (of_find_property(node, "qcom,proxy-consumer-enable", NULL) ||
	    of_find_property(node, "qcom,proxy-consumer-current", NULL) ||
	    of_find_property(node, "qcom,proxy-consumer-voltage", NULL) ||
	    of_find_property(node, "parent-supply", NULL) ||
	    of_find_property(node, "vin-supply", NULL) ||
	    of_property_read_bool(node, "qcom,use-voltage-corner") ||
	    of_property_read_bool(node, "qcom,use-voltage-floor-corner"))
		return -EINVAL;
	physical = kzalloc(sizeof(*physical), GFP_KERNEL);
	if (!physical)
		return -ENOMEM;
	mutex_init(&physical->lock);
	kref_init(&physical->refs);
	INIT_LIST_HEAD(&physical->bank_list);
	physical->bank = dev;
	physical->rpm = rpm;
	physical->type = type;
	physical->id = id;
	physical->node = of_node_get(node);
	physical->desc = *template;
	physical->ranges = kmemdup(template->linear_ranges,
			template->n_linear_ranges * sizeof(*physical->ranges), GFP_KERNEL);
	if (!physical->ranges) {
		ret = -ENOMEM;
		goto put;
	}
	physical->desc.linear_ranges = physical->ranges;
	physical->desc.name = node->name;
	/* The audited final Sony driver created these five resources without
	 * a Linux input supply: only explicit parent-supply populated its core
	 * dependency. The opt-in flag preserves that delegation to RPM rather
	 * than importing the incompatible upstream PM8994 bank input graph.
	 * No dummy provider, raw core-supply detachment or invented Vin exists.
	 */
	physical->desc.supply_name = NULL;
	physical->desc.of_match = node->name;
	physical->desc.owner = THIS_MODULE;
	physical->desc.type = REGULATOR_VOLTAGE;
	physical->desc.ops = &rpm_physical_ops;
	init = of_get_regulator_init_data(dev, node, &physical->desc);
	if (!init) {
		ret = -ENOMEM;
		goto put;
	}
	if (init->supply_regulator || init->constraints.boot_on ||
	    init->constraints.always_on ||
	    init->constraints.min_uV <= 0 ||
	    init->constraints.max_uV < init->constraints.min_uV) {
		ret = -EINVAL;
		goto put;
	}
	init->constraints.apply_uV = false;
	init->constraints.valid_ops_mask |= REGULATOR_CHANGE_VOLTAGE |
		REGULATOR_CHANGE_STATUS | REGULATOR_CHANGE_DRMS;
	physical->init = init;
	ret = qcom_rpm_smd_claim(rpm, type, id, physical);
	if (ret)
		goto put;
	physical->claimed = true;
	ret = rpm_physical_register_one(dev, physical);
	if (ret) {
		qcom_rpm_smd_release(rpm, type, id, physical, false);
		physical->claimed = false;
		goto put;
	}
	/* The bank owns retirement; registered handles retain callback memory
	 * until the final rdev reference. This action precedes its RPM put.
	 */
	ret = devm_add_action_or_reset(dev, rpm_physical_unregister, physical);
	if (ret)
		return ret;
	mutex_lock(&rpm_physical_bank_lock);
	mutex_lock(&physical->lock);
	list_add_tail(&physical->bank_list, &rpm_physical_banks);
	mutex_unlock(&physical->lock);
	mutex_unlock(&rpm_physical_bank_lock);
	return 0;
put:
	rpm_physical_put(physical);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_rpm_physical_register);

MODULE_DESCRIPTION("Acknowledged BOTH-set PM8994 physical regulator requests");
MODULE_LICENSE("GPL v2");
