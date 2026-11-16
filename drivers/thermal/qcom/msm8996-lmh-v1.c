// SPDX-License-Identifier: GPL-2.0-only
/*
 * MSM8996 LMHv1, derived from Sony4.4 0c295b15 lmh_lite.c/lmh_interface.c
 * Copyright (c) 2014-2016, The Linux Foundation. All rights reserved.
 *
 * This is the original service0x13 packet ABI and S11 DPM/ODCM protocol,
 * not DCVSH/OSM. No CPU voltage/frequency is set and no protection or logging
 * mode is disabled. Probe preserves the unknown firmware profile. It requires
 * real source-named MMIO resources and the one SPM-controlled S11 provider.
 * The DT stays disabled until the complete gang/handoff has been qualified.
 */
#include <linux/err.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/platform_device.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/thermal.h>
#include <linux/workqueue.h>
#include <linux/soc/qcom/msm8996-lmh.h>
#include <asm/cacheflush.h>
#include <soc/qcom/scm.h>
#include "msm8996-lmh-state.h"

#define LMH_CHANGE_PROFILE 0x01
#define LMH_GET_PROFILES 0x02
#define LMH_GET_INTENSITY 0x06
#define LMH_GET_SENSORS 0x07
#define LMH_PACKET_COUNT 10
#define LMH_SENSOR_LIMIT 256
#define LMH_PROFILE_LIMIT 64
#define LMH_POLL_MS 30

struct lmh_sensor_info {
	u32 name, node_id, intensity, max_intensity, type;
};
struct lmh_sensor_packet {
	u32 count;
	struct lmh_sensor_info sensor[LMH_PACKET_COUNT];
};
struct lmh_v1;
struct lmh_sensor {
	struct lmh_v1 *lmh;
	struct thermal_zone_device *zone;
	struct lmh_sensor_info info;
	int intensity;
	char name[20];
};
struct lmh_v1 {
	struct device *dev;
	struct mutex lock;
	struct msm8996_lmh_power power;
	struct regulator *s11;
	struct notifier_block voltage;
	void __iomem *dpm;
	void __iomem *odcm[MSM8996_LMH_ODCM_COUNT];
	struct delayed_work poll;
	struct lmh_sensor *sensor;
	u32 *profiles;
	unsigned int sensor_count, profile_count, last_profile;
	int irq, sensor_error;
	bool online, notifier_bound, irq_bound, polling, wake_armed;
	bool profile_requested;
};

/* A lifetime-bounded physical endpoint claim. Removal never lets another
 * probe discard pending voltage state/quarantine. Public APIs return scalars
 * under registry->endpoint locks; no raw endpoint pointer escapes.
 */
static DEFINE_MUTEX(lmh_registry_lock);
static struct lmh_v1 *lmh_claim;
static struct lmh_v1 *lmh_published;
static const resource_size_t lmh_addresses[] = {
	0x09950804, 0x09981030, 0x09991030, 0x099a1028,
	0x099b1030, 0x099c1030, 0x099d1028,
};
static const char * const lmh_resource_names[] = {
	"dpm", "odcm-cpu0", "odcm-cpu1", "odcm-l2-0",
	"odcm-cpu2", "odcm-cpu3", "odcm-l2-1",
};

static int lmh_read_dpm(void *data, unsigned int *mv)
{
	struct lmh_v1 *lmh = data;

	*mv = readl(lmh->dpm);
	return 0;
}

static int lmh_read_odcm(void *data, bool *enabled)
{
	struct lmh_v1 *lmh = data;
	u32 first, value;
	unsigned int i;

	first = readl(lmh->odcm[0]);
	if (first > 1)
		return -EIO;
	for (i = 1; i < MSM8996_LMH_ODCM_COUNT; i++) {
		value = readl(lmh->odcm[i]);
		if (value != first)
			return -EIO;
	}
	*enabled = first;
	return 0;
}

static int lmh_write_dpm(void *data, unsigned int mv)
{
	struct lmh_v1 *lmh = data;
	int ret;

	/* Same Linux SCM serialization as Sony. This mutex does not prevent
	 * autonomous firmware or thermal mitigation and is not a CPU lease.
	 */
	mutex_lock(&scm_lmh_lock);
	writel(mv, lmh->dpm);
	ret = readl(lmh->dpm) == mv ? 0 : -EIO;
	mutex_unlock(&scm_lmh_lock);
	return ret;
}

static int lmh_write_odcm(void *data, bool enabled)
{
	struct lmh_v1 *lmh = data;
	unsigned int i;
	int ret = 0;

	mutex_lock(&scm_lmh_lock);
	for (i = 0; i < MSM8996_LMH_ODCM_COUNT; i++) {
		writel(enabled, lmh->odcm[i]);
		if (readl(lmh->odcm[i]) != enabled) {
			ret = -EIO;
			break;
		}
	}
	mutex_unlock(&scm_lmh_lock);
	/* Partial writes are not guessed back to a supposedly safe baseline. */
	return ret;
}

static const struct msm8996_lmh_io lmh_io = {
	.read_dpm = lmh_read_dpm,
	.read_odcm = lmh_read_odcm,
	.write_dpm = lmh_write_dpm,
	.write_odcm = lmh_write_odcm,
};

static int lmh_voltage_event(struct notifier_block *block,
		unsigned long event, void *data)
{
	struct lmh_v1 *lmh = container_of(block, struct lmh_v1, voltage);
	struct pre_voltage_change_data *pre = data;
	int ret = 0;

	mutex_lock(&lmh->lock);
	switch (event) {
	case REGULATOR_EVENT_PRE_VOLTAGE_CHANGE:
		ret = pre ? msm8996_lmh_pre(&lmh->power, &lmh_io, lmh,
			pre->old_uV, pre->min_uV, pre->max_uV) : -EINVAL;
		break;
	case REGULATOR_EVENT_VOLTAGE_CHANGE:
		ret = msm8996_lmh_post(&lmh->power, &lmh_io, lmh,
				(unsigned long)data);
		break;
	case REGULATOR_EVENT_ABORT_VOLTAGE_CHANGE:
		ret = msm8996_lmh_abort(&lmh->power, (unsigned long)data);
		break;
	default:
		break;
	}
	mutex_unlock(&lmh->lock);
	/* Core honors PRE veto; it does not propagate a POST notifier error.
	 * A future CPU owner must check our sticky state after a voltage vote.
	 */
	return ret ? notifier_from_errno(ret) : NOTIFY_OK;
}

static int lmh_capabilities(void)
{
	static const u32 commands[] = {
		LMH_CHANGE_PROFILE, LMH_GET_PROFILES, LMH_GET_INTENSITY, LMH_GET_SENSORS,
	};
	unsigned int i;
	int ret;

	if (!is_scm_armv8())
		return -EOPNOTSUPP;
	for (i = 0; i < ARRAY_SIZE(commands); i++) {
		ret = scm_is_call_available(SCM_SVC_LMH, commands[i]);
		if (ret <= 0)
			return ret < 0 ? ret : -ENODEV;
	}
	return 0;
}

/* Original ARMv8 LMHv1 ABI passes a physical shared buffer, not a device DMA
 * IOVA. Keep the source's32bit legacy-compatible physical extent explicitly
 * bounded and cache-maintained; no newer DCVSH/shm firmware ABI is assumed.
 */
static void *lmh_buffer_alloc(size_t size)
{
	void *buffer;
	phys_addr_t phys;

	buffer = kzalloc(PAGE_ALIGN(size), GFP_KERNEL | GFP_DMA);
	if (!buffer)
		return ERR_PTR(-ENOMEM);
	phys = virt_to_phys(buffer);
	if (phys > U32_MAX || size - 1 > U32_MAX - phys) {
		kfree(buffer);
		return ERR_PTR(-ERANGE);
	}
	return buffer;
}

static int lmh_buffer_call(unsigned int command, void *buffer, size_t size,
		unsigned int start, unsigned int *total)
{
	struct scm_desc desc = { };
	int ret;

	desc.args[0] = SCM_BUFFER_PHYS(buffer);
	desc.args[1] = size;
	if (command == LMH_GET_PROFILES) {
		desc.args[2] = start;
		desc.arginfo = SCM_ARGS(3, SCM_RW, SCM_VAL, SCM_VAL);
	} else {
		desc.arginfo = SCM_ARGS(2, SCM_RW, SCM_VAL);
	}
	dmac_flush_range(buffer, buffer + PAGE_ALIGN(size));
	ret = scm_call2(SCM_SIP_FNID(SCM_SVC_LMH, command), &desc);
	mb();
	dmac_inv_range(buffer, buffer + PAGE_ALIGN(size));
	if (!ret && total) {
		if (desc.ret[0] > UINT_MAX)
			return -EOVERFLOW;
		*total = desc.ret[0];
	}
	return ret;
}

static int lmh_word_name(u32 word, char *name)
{
	unsigned int i, length = 0;
	bool terminated = false;

	for (i = 0; i < sizeof(word); i++) {
		u8 value = word >> ((3 - i) * 8);

		if (!value) {
			terminated = true;
			continue;
		}
		if (terminated || value < ' ' || value > '~')
			return -EPROTO;
		name[length++] = value;
	}
	name[length] = 0;
	return length ? 0 : -EPROTO;
}

static int lmh_sensor_list(struct lmh_v1 *lmh)
{
	struct lmh_sensor_packet *packet;
	unsigned int total = 0, next = 0, amount, i, j;
	int ret = 0;

	packet = lmh_buffer_alloc(sizeof(*packet));
	if (IS_ERR(packet))
		return PTR_ERR(packet);
	do {
		memset(packet, 0, sizeof(*packet));
		packet->count = next;
		ret = lmh_buffer_call(LMH_GET_SENSORS, packet, sizeof(*packet), 0, NULL);
		if (ret)
			break;
		if (!packet->count || packet->count > LMH_SENSOR_LIMIT ||
		    (next && packet->count != total)) {
			ret = -EPROTO;
			break;
		}
		if (!next) {
			total = packet->count;
			lmh->sensor = kcalloc(total, sizeof(*lmh->sensor), GFP_KERNEL);
			if (!lmh->sensor) {
				ret = -ENOMEM;
				break;
			}
		}
		amount = min_t(unsigned int, LMH_PACKET_COUNT, total - next);
		for (i = 0; i < amount; i++) {
			struct lmh_sensor *sensor = &lmh->sensor[next + i];
			char name[5], id[5];

			sensor->lmh = lmh;
			sensor->info = packet->sensor[i];
			for (j = 0; j < next + i; j++)
				if (lmh->sensor[j].info.name == sensor->info.name &&
				    lmh->sensor[j].info.node_id == sensor->info.node_id) {
					ret = -EPROTO;
					goto out;
				}
			/* Source IDs are big-endian character words; a short name
			 * can be NUL padded. Never accept embedded binary names.
			 */
			ret = lmh_word_name(sensor->info.name, name);
			if (!ret)
				ret = lmh_word_name(sensor->info.node_id, id);
			if (ret)
				goto out;
			snprintf(sensor->name, sizeof(sensor->name), "%s_%s", name, id);
		}
		next += amount;
	} while (next < total);
	if (!ret)
		lmh->sensor_count = total;
out:
	kfree(packet);
	return ret;
}

static int lmh_profile_list(struct lmh_v1 *lmh)
{
	u32 *packet;
	unsigned int total = 0, returned, next = 0, amount, i, j;
	int ret;

	packet = lmh_buffer_alloc(sizeof(*packet) * LMH_PACKET_COUNT);
	if (IS_ERR(packet))
		return PTR_ERR(packet);
	do {
		memset(packet, 0, sizeof(*packet) * LMH_PACKET_COUNT);
		ret = lmh_buffer_call(LMH_GET_PROFILES, packet,
			sizeof(*packet) * LMH_PACKET_COUNT, next, &returned);
		if (ret)
			break;
		if (!returned || returned > LMH_PROFILE_LIMIT || (next && returned != total)) {
			ret = -EPROTO;
			break;
		}
		if (!next) {
			total = returned;
			lmh->profiles = kcalloc(total, sizeof(*lmh->profiles), GFP_KERNEL);
			if (!lmh->profiles) {
				ret = -ENOMEM;
				break;
			}
		}
		amount = min_t(unsigned int, LMH_PACKET_COUNT, total - next);
		for (i = 0; i < amount; i++) {
			for (j = 0; j < next + i; j++)
				if (lmh->profiles[j] == packet[i]) {
					ret = -EPROTO;
					goto out;
				}
			lmh->profiles[next + i] = packet[i];
		}
		next += amount;
	} while (next < total);
	if (!ret)
		lmh->profile_count = total;
out:
	kfree(packet);
	return ret;
}

static int lmh_intensity(struct lmh_v1 *lmh, bool *active)
{
	struct lmh_sensor_packet *packet;
	unsigned int i, j, k;
	int value[LMH_SENSOR_LIMIT] = { };
	int ret;

	packet = lmh_buffer_alloc(sizeof(*packet));
	if (IS_ERR(packet)) {
		lmh->sensor_error = PTR_ERR(packet);
		return lmh->sensor_error;
	}
	ret = lmh_buffer_call(LMH_GET_INTENSITY, packet, sizeof(*packet), 0, NULL);
	if (ret)
		goto out;
	if (packet->count > LMH_PACKET_COUNT) {
		ret = -EPROTO;
		goto out;
	}
	for (i = 0; i < packet->count; i++) {
		for (k = 0; k < i; k++)
			if (packet->sensor[k].name == packet->sensor[i].name &&
			    packet->sensor[k].node_id == packet->sensor[i].node_id) {
				ret = -EPROTO;
				goto out;
			}
		for (j = 0; j < lmh->sensor_count; j++)
			if (lmh->sensor[j].info.name == packet->sensor[i].name &&
			    lmh->sensor[j].info.node_id == packet->sensor[i].node_id)
				break;
		if (j == lmh->sensor_count ||
		    (packet->sensor[i].max_intensity && packet->sensor[i].intensity > packet->sensor[i].max_intensity)) {
			ret = -EPROTO;
			goto out;
		}
		if (!packet->sensor[i].max_intensity && packet->sensor[i].intensity > INT_MAX) {
			ret = -EOVERFLOW;
			goto out;
		}
		value[j] = packet->sensor[i].max_intensity ?
			div_u64((u64)packet->sensor[i].intensity * 100,
				packet->sensor[i].max_intensity) : packet->sensor[i].intensity;
		if (value[j] < 0) {
			ret = -ERANGE;
			goto out;
		}
	}
	*active = false;
	for (j = 0; j < lmh->sensor_count; j++) {
		lmh->sensor[j].intensity = value[j];
		*active |= value[j] != 0;
	}
out:
	/* A failed read does not publish zero and falsely clear a throttle. */
	lmh->sensor_error = ret;
	kfree(packet);
	return ret;
}

static int lmh_sensor_temp(struct thermal_zone_device *zone, int *value)
{
	struct lmh_sensor *sensor = zone->devdata;
	struct lmh_v1 *lmh = sensor->lmh;
	int ret = 0;

	mutex_lock(&lmh->lock);
	if (!lmh->online || lmh->power.retired)
		ret = -ESHUTDOWN;
	else if (lmh->power.suspended)
		ret = -EBUSY;
	else if (lmh->sensor_error)
		ret = lmh->sensor_error;
	else
		*value = sensor->intensity;
	mutex_unlock(&lmh->lock);
	return ret;
}
static struct thermal_zone_device_ops lmh_sensor_ops = {
	.get_temp = lmh_sensor_temp,
};

static void lmh_notify_sensors(struct lmh_v1 *lmh)
{
	unsigned int i;

	/* Outside endpoint lock: thermal get_temp takes it again. */
	for (i = 0; i < lmh->sensor_count; i++)
		if (lmh->sensor[i].zone)
			thermal_zone_device_update(lmh->sensor[i].zone, THERMAL_EVENT_UNSPECIFIED);
}

static void lmh_poll(struct work_struct *work)
{
	struct lmh_v1 *lmh = container_of(work, struct lmh_v1, poll.work);
	bool active = true, notify = false;
	int ret;

	mutex_lock(&lmh->lock);
	if (lmh->power.retired || lmh->power.suspended || !lmh->online || !lmh->polling)
		goto out;
	ret = lmh_intensity(lmh, &active);
	if (ret)
		msm8996_lmh_fail(&lmh->power, ret);
	else
		notify = true;
	if (!ret && !active) {
		lmh->polling = false;
		enable_irq(lmh->irq);
	} else {
		mod_delayed_work(system_highpri_wq, &lmh->poll, msecs_to_jiffies(LMH_POLL_MS));
	}
out:
	mutex_unlock(&lmh->lock);
	if (notify)
		lmh_notify_sensors(lmh);
}

static irqreturn_t lmh_irq(int irq, void *data)
{
	struct lmh_v1 *lmh = data;

	mutex_lock(&lmh->lock);
	if (!lmh->power.retired && lmh->online && !lmh->polling) {
		disable_irq_nosync(irq);
		lmh->polling = true;
		mod_delayed_work(system_highpri_wq, &lmh->poll, 0);
	}
	mutex_unlock(&lmh->lock);
	return IRQ_HANDLED;
}

static int lmh_map_resources(struct platform_device *pdev, struct lmh_v1 *lmh)
{
	struct resource *res;
	void __iomem *map;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(lmh_addresses); i++) {
		res = platform_get_resource_byname(pdev, IORESOURCE_MEM, lmh_resource_names[i]);
		if (!res || res->start != lmh_addresses[i] || resource_size(res) != sizeof(u32))
			return -EINVAL;
		map = devm_ioremap_resource(&pdev->dev, res);
		if (IS_ERR(map))
			return PTR_ERR(map);
		if (!i)
			lmh->dpm = map;
		else
			lmh->odcm[i - 1] = map;
	}
	return 0;
}

static int lmh_supply(struct device *dev, struct lmh_v1 *lmh)
{
	struct device_node *node, *parent;
	const char *name;
	u32 value;
	int ret;

	node = of_parse_phandle(dev->of_node, "vdd-apss-supply", 0);
	if (!node)
		return -EINVAL;
	ret = of_property_read_string(node, "regulator-name", &name);
	if (!ret && (strcmp(name, "pm8994_s11") ||
	    !of_device_is_compatible(node, "qcom,spm-regulator")))
		ret = -EINVAL;
	if (!ret)
		ret = of_property_read_u32(node, "reg", &value);
	if (!ret && value != 0x3200)
		ret = -EINVAL;
	if (!ret)
		ret = of_property_read_u32(node, "regulator-min-microvolt", &lmh->power.min_uv);
	if (!ret)
		ret = of_property_read_u32(node, "regulator-max-microvolt", &lmh->power.max_uv);
	for (parent = of_node_get(node); parent;) {
		struct device_node *next = of_get_parent(parent);

		if (!of_device_is_available(parent) && !ret)
			ret = -EPROBE_DEFER;
		of_node_put(parent);
		parent = next;
	}
	of_node_put(node);
	if (ret)
		return ret;
	lmh->s11 = devm_regulator_get_optional(dev, "vdd-apss");
	return IS_ERR(lmh->s11) ? PTR_ERR(lmh->s11) : 0;
}

static void lmh_stop(struct lmh_v1 *lmh)
{
	unsigned int i;

	mutex_lock(&lmh_registry_lock);
	if (lmh_published == lmh)
		lmh_published = NULL;
	mutex_lock(&lmh->lock);
	lmh->power.retired = true;
	lmh->online = false;
	mutex_unlock(&lmh->lock);
	mutex_unlock(&lmh_registry_lock);
	if (lmh->notifier_bound) {
		regulator_unregister_notifier(lmh->s11, &lmh->voltage);
		lmh->notifier_bound = false;
	}
	if (lmh->irq_bound) {
		if (lmh->wake_armed)
			disable_irq_wake(lmh->irq);
		devm_free_irq(lmh->dev, lmh->irq, lmh);
		lmh->irq_bound = false;
	}
	cancel_delayed_work_sync(&lmh->poll);
	for (i = 0; i < lmh->sensor_count; i++)
		if (lmh->sensor[i].zone) {
			thermal_zone_device_unregister(lmh->sensor[i].zone);
			lmh->sensor[i].zone = NULL;
		}
	kfree(lmh->sensor);
	lmh->sensor = NULL;
	kfree(lmh->profiles);
	lmh->profiles = NULL;
	/* Firmware mitigation, voltage, DPM/ODCM and profile are not undone. */
}

static int lmh_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct lmh_v1 *lmh;
	unsigned int i;
	u32 irq[3], threshold;
	bool active;
	int ret, uv;

	if (!of_machine_is_compatible("somc,dora-row") &&
	    !of_machine_is_compatible("sony,dora-row"))
		return -ENODEV;
	ret = of_property_read_u32(dev->of_node, "qcom,lmh-odcm-disable-threshold-mA", &threshold);
	if (ret || threshold != MSM8996_LMH_ODCM_THRESHOLD_MV)
		return -EINVAL;
	if (of_property_count_u32_elems(dev->of_node, "interrupts") != 3 ||
	    of_property_read_u32_array(dev->of_node, "interrupts", irq, 3) ||
	    irq[0] || irq[1] != 23 || irq[2] != IRQ_TYPE_LEVEL_HIGH)
		return -EINVAL;
	ret = lmh_capabilities();
	if (ret)
		return ret;
	lmh = kzalloc(sizeof(*lmh), GFP_KERNEL);
	if (!lmh)
		return -ENOMEM;
	lmh->dev = dev;
	lmh->sensor_error = -ENODATA;
	mutex_init(&lmh->lock);
	INIT_DELAYED_WORK(&lmh->poll, lmh_poll);
	ret = lmh_supply(dev, lmh);
	if (ret)
		goto free;
	ret = lmh_map_resources(pdev, lmh);
	if (ret)
		goto free;
	lmh->irq = platform_get_irq(pdev, 0);
	if (lmh->irq < 0) {
		ret = lmh->irq;
		goto free;
	}
	mutex_lock(&lmh_registry_lock);
	if (lmh_claim) {
		ret = -EBUSY;
	} else {
		lmh_claim = lmh;
		get_device(dev);
		__module_get(THIS_MODULE);
		ret = 0;
	}
	mutex_unlock(&lmh_registry_lock);
	if (ret)
		goto free;
	ret = lmh_sensor_list(lmh);
	if (!ret)
		ret = lmh_profile_list(lmh);
	if (ret)
		goto stop;
	uv = regulator_get_voltage(lmh->s11);
	ret = uv > 0 ? msm8996_lmh_baseline(&lmh->power, &lmh_io, lmh, uv) : uv ? uv : -ENODATA;
	if (ret)
		goto stop;
	lmh->voltage.notifier_call = lmh_voltage_event;
	ret = regulator_register_notifier(lmh->s11, &lmh->voltage);
	if (ret)
		goto stop;
	lmh->notifier_bound = true;
	mutex_lock(&lmh->lock);
	ret = lmh_intensity(lmh, &active);
	mutex_unlock(&lmh->lock);
	if (ret)
		goto stop;
	lmh->online = true;
	for (i = 0; i < lmh->sensor_count; i++) {
		lmh->sensor[i].zone = thermal_zone_device_register(lmh->sensor[i].name,
			0, 0, &lmh->sensor[i], &lmh_sensor_ops, NULL, 0, 0);
		if (IS_ERR(lmh->sensor[i].zone)) {
			ret = PTR_ERR(lmh->sensor[i].zone);
			lmh->sensor[i].zone = NULL;
			goto stop;
		}
	}
	ret = devm_request_threaded_irq(dev, lmh->irq, NULL, lmh_irq,
		IRQF_TRIGGER_HIGH | IRQF_ONESHOT, "msm8996-lmh-v1", lmh);
	if (ret)
		goto stop;
	lmh->irq_bound = true;
	/* The initial query may have observed and acknowledged an existing
	 * throttle before IRQ registration. Keep polling it without inventing
	 * a zero baseline or waiting for a second edge on a level source.
	 */
	if (active)
		lmh_irq(lmh->irq, lmh);
	platform_set_drvdata(pdev, lmh);
	device_init_wakeup(dev, true);
	mutex_lock(&lmh_registry_lock);
	lmh_published = lmh;
	mutex_unlock(&lmh_registry_lock);
	dev_info(dev, "LMHv1 S11 notifier and firmware sensors bound; CPU write ownership is not granted\n");
	return 0;
stop:
	mutex_lock(&lmh->lock);
	msm8996_lmh_fail(&lmh->power, ret);
	mutex_unlock(&lmh->lock);
	lmh_stop(lmh);
	return ret;
free:
	kfree(lmh);
	return ret;
}

static int lmh_remove(struct platform_device *pdev)
{
	lmh_stop(platform_get_drvdata(pdev));
	device_init_wakeup(&pdev->dev, false);
	return 0;
}

static int __maybe_unused lmh_suspend(struct device *dev)
{
	struct lmh_v1 *lmh = dev_get_drvdata(dev);
	int ret;

	mutex_lock(&lmh->lock);
	ret = lmh->power.error;
	if (!ret && (lmh->power.pending || lmh->polling || lmh->power.retired))
		ret = -EBUSY;
	if (!ret)
		lmh->power.suspended = true;
	mutex_unlock(&lmh->lock);
	if (ret)
		return ret;
	ret = enable_irq_wake(lmh->irq);
	mutex_lock(&lmh->lock);
	if (ret)
		lmh->power.suspended = false;
	else
		lmh->wake_armed = true;
	mutex_unlock(&lmh->lock);
	return ret;
}

static int __maybe_unused lmh_resume(struct device *dev)
{
	struct lmh_v1 *lmh = dev_get_drvdata(dev);
	unsigned int mv;
	bool odcm;
	int ret = 0, uv;

	if (lmh->wake_armed) {
		ret = disable_irq_wake(lmh->irq);
		if (!ret)
			lmh->wake_armed = false;
	}
	/* Regulator core lock precedes notifier endpoint lock, not vice versa. */
	uv = regulator_get_voltage(lmh->s11);
	mutex_lock(&lmh->lock);
	if (!ret)
		ret = uv > 0 ? 0 : uv ? uv : -ENODATA;
	if (!ret)
		ret = lmh_read_dpm(lmh, &mv);
	if (!ret)
		ret = lmh_read_odcm(lmh, &odcm);
	if (!ret && ((unsigned int)uv != lmh->power.baseline_uv || mv != lmh->power.dpm_mv ||
	    odcm != lmh->power.odcm_enabled || lmh->power.pending))
		ret = -ESTALE;
	if (ret)
		ret = msm8996_lmh_fail(&lmh->power, ret);
	else if (lmh->power.error)
		ret = lmh->power.error;
	else
		lmh->power.suspended = false;
	/* A wake IRQ can be delivered after IRQ resume but before this normal
	 * device resume callback. Its worker must not query SCM while frozen;
	 * restart that pending poll only after the retained state is checked.
	 */
	if (!ret && lmh->polling)
		mod_delayed_work(system_highpri_wq, &lmh->poll, 0);
	mutex_unlock(&lmh->lock);
	return ret;
}

static int __maybe_unused lmh_suspend_noirq(struct device *dev)
{
	struct lmh_v1 *lmh = dev_get_drvdata(dev);
	int ret;

	/* A normal IRQ can start polling after ->suspend checked it. Recheck
	 * once normal interrupt delivery has stopped; never enter sleep with
	 * an in-flight firmware query/pending S11 notification hidden behind
	 * a wake IRQ. Firmware thermal mitigation itself is not disabled.
	 */
	mutex_lock(&lmh->lock);
	ret = lmh->power.error;
	if (!ret && (!lmh->power.suspended || lmh->power.pending || lmh->polling ||
	    lmh->power.retired))
		ret = -EBUSY;
	mutex_unlock(&lmh->lock);
	return ret;
}

int qcom_msm8996_lmh_v1_get_state_by_node(struct device_node *node,
		struct qcom_msm8996_lmh_v1_state *state)
{
	struct lmh_v1 *lmh;
	int ret = -EPROBE_DEFER;

	if (!node || IS_ERR(node) || !state)
		return -EINVAL;
	memset(state, 0, sizeof(*state));
	mutex_lock(&lmh_registry_lock);
	lmh = lmh_claim;
	if (!lmh || lmh->dev->of_node != node)
		goto out;
	mutex_lock(&lmh->lock);
	/* Probe owns mutable arrays/baseline until publication. A permanent
	 * retired descriptor remains observable, without touching its maps.
	 */
	if (lmh_published != lmh && !lmh->power.retired)
		goto unlock;
	state->online = lmh->online;
	state->retired = lmh->power.retired;
	state->suspended = lmh->power.suspended;
	state->pending = lmh->power.pending;
	state->quarantined = !!lmh->power.error;
	state->baseline_valid = lmh->power.baseline_valid;
	state->baseline_uv = lmh->power.baseline_uv;
	state->dpm_mv = lmh->power.dpm_mv;
	state->odcm_enabled = lmh->power.odcm_enabled;
	state->sensor_count = lmh->sensor_count;
	state->profile_count = lmh->profile_count;
	state->last_profile = lmh->last_profile;
	state->profile_requested = lmh->profile_requested;
	state->error = lmh->power.error;
	state->sensor_error = lmh->sensor_error;
	ret = state->retired ? -ESHUTDOWN : state->error ? state->error :
		state->sensor_error ? state->sensor_error : state->suspended ? -EBUSY :
		lmh_published == lmh ? 0 : -EPROBE_DEFER;
unlock:
	mutex_unlock(&lmh->lock);
out:
	mutex_unlock(&lmh_registry_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_msm8996_lmh_v1_get_state_by_node);

int qcom_msm8996_lmh_v1_request_profile(struct device_node *node, unsigned int profile)
{
	struct lmh_v1 *lmh;
	struct scm_desc desc = { };
	unsigned int i;
	int ret = -EPROBE_DEFER;

	if (!node || IS_ERR(node))
		return -EINVAL;
	mutex_lock(&lmh_registry_lock);
	lmh = lmh_published;
	if (!lmh || lmh->dev->of_node != node)
		goto out;
	mutex_lock(&lmh->lock);
	ret = lmh->power.error;
	if (!ret && (lmh->power.retired || lmh->power.suspended || lmh->power.pending))
		ret = -EBUSY;
	if (ret)
		goto unlock;
	for (i = 0; i < lmh->profile_count && lmh->profiles[i] != profile; i++)
		;
	if (i == lmh->profile_count) {
		ret = -EINVAL;
		goto unlock;
	}
	desc.arginfo = SCM_ARGS(1, SCM_VAL);
	desc.args[0] = profile;
	ret = scm_call2(SCM_SIP_FNID(SCM_SVC_LMH, LMH_CHANGE_PROFILE), &desc);
	if (ret) {
		ret = msm8996_lmh_fail(&lmh->power, ret);
	} else {
		lmh->last_profile = profile;
		lmh->profile_requested = true;
	}
unlock:
	mutex_unlock(&lmh->lock);
out:
	mutex_unlock(&lmh_registry_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(qcom_msm8996_lmh_v1_request_profile);

static const struct dev_pm_ops lmh_pm = {
	SET_SYSTEM_SLEEP_PM_OPS(lmh_suspend, lmh_resume)
	SET_NOIRQ_SYSTEM_SLEEP_PM_OPS(lmh_suspend_noirq, NULL)
};
static const struct of_device_id lmh_match[] = {
	{ .compatible = "qcom,lmh_v1" },
	{ }
};
static struct platform_driver lmh_driver = {
	.probe = lmh_probe,
	.remove = lmh_remove,
	.driver = {
		.name = "msm8996-lmh-v1",
		.of_match_table = lmh_match,
		.pm = &lmh_pm,
		.suppress_bind_attrs = true,
	},
};
builtin_platform_driver(lmh_driver);
MODULE_DESCRIPTION("Sony MSM8996 LMHv1 S11 DPM/ODCM and secure monitor backend");
MODULE_LICENSE("GPL v2");
