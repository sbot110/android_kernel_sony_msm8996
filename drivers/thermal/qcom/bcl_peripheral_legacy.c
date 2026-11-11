/*
 * Copyright (c) 2014-2017, The Linux Foundation. All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 and
 * only version 2 as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#define pr_fmt(fmt) "%s:%s " fmt, KBUILD_MODNAME, __func__

#include <linux/module.h>
#include <linux/interrupt.h>
#include <linux/workqueue.h>
#include <linux/kernel.h>
#include <linux/regmap.h>
#include <linux/io.h>
#include <linux/err.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_device.h>
#include <linux/spmi.h>
#include <linux/platform_device.h>
#include <linux/mutex.h>
#include <linux/msm_bcl.h>
#include <linux/power_supply.h>
#include <soc/qcom/scm.h>
#include <linux/slab.h>
#include <asm/cacheflush.h>

#define CREATE_TRACE_POINTS
#include <trace/events/sony_bcl.h>

#define BCL_DRIVER_NAME         "sony-msm8996-bcl"
#define BCL_VBAT_INT_NAME       "bcl-low-vbat-int"
#define BCL_IBAT_INT_NAME       "bcl-high-ibat-int"
#define BCL_PARAM_MAX_ATTR      3

#define BCL_MONITOR_EN          0x46
#define BCL_VBAT_VALUE          0x54
#define BCL_IBAT_VALUE          0x55
#define BCL_VBAT_MIN            0x58
#define BCL_IBAT_MAX            0x59
#define BCL_V_GAIN_BAT          0x60
#define BCL_I_GAIN_RSENSE       0x61
#define BCL_I_OFFSET_RSENSE     0x62
#define BCL_I_GAIN_BATFET       0x63
#define BCL_I_OFFSET_BATFET     0x64
#define BCL_I_SENSE_SRC         0x65
#define BCL_VBAT_MIN_CLR        0x66
#define BCL_IBAT_MAX_CLR        0x67
#define BCL_VBAT_TRIP           0x68
#define BCL_IBAT_TRIP           0x69

#define BCL_8998_VBAT_VALUE     0x58
#define BCL_8998_IBAT_VALUE     0x59
#define BCL_8998_VBAT_MIN       0x5C
#define BCL_8998_IBAT_MAX       0x5D
#define BCL_8998_MAX_MIN_CLR    0x48
#define BCL_8998_IBAT_MAX_CLR   3
#define BCL_8998_VBAT_MIN_CLR   2
#define BCL_8998_VBAT_ADC_LOW   0x72
#define BCL_8998_VBAT_COMP_LOW  0x75
#define BCL_8998_VBAT_COMP_TLOW 0x76
#define BCL_8998_IBAT_HIGH      0x78
#define BCL_8998_IBAT_TOO_HIGH  0x79
#define BCL_8998_LMH_CFG        0xA3
#define BCL_8998_BCL_CFG        0x6A
#define LMH_8998_INT_POL_HIGH   0x12
#define LMH_8998_INT_EN         0x15

#define BCL_8998_VBAT_SCALING   39000
#define BCL_8998_IBAT_SCALING   80000
#define BCL_VBAT_LOW_THRESHOLD  0x7 /* 3.1V */
#define BCL_VBAT_TLOW_THRESHOLD 0x5 /* 2.9v */
#define BCL_IBAT_HIGH_THRESH_UA 4300000
#define BCL_LMH_CFG_VAL         0x3
#define BCL_CFG_VAL             0x81
#define LMH_INT_VAL             0x7

#define BCL_CONSTANT_NUM        32
#define BCL_READ_RETRY_LIMIT    3
#define VAL_CP_REG_BUF_LEN      3
#define VAL_REG_BUF_OFFSET      0
#define VAL_CP_REG_BUF_OFFSET   2
#define PON_SPARE_FULL_CURRENT		0x0
#define PON_SPARE_DERATED_CURRENT	0x1

#define LMH_DCVSH               0x10
#define LMH_NODE_DCVS           0x44435653 /* DCVS */
#define LMH_SUB_FN_BCL          0x42434C00 /* BCL */
#define LMH_CLUSTER_0           0x6370302D /* cpAG */
#define LMH_CLUSTER_1           0x6370312D /* cpAU */
#define LMH_ALGO_ENABLE         0x454E424C /* ENBL */

#define READ_CONV_FACTOR(_node, _key, _val, _ret, _dest) do { \
		_ret = of_property_read_u32(_node, _key, &_val); \
		if (_ret) { \
			pr_err("Error reading key:%s. err:%d\n", _key, _ret); \
			goto bcl_dev_exit; \
		} \
		_dest = _val; \
	} while (0)

#define READ_OPTIONAL_PROP(_node, _key, _val, _ret, _dest) do { \
		_ret = of_property_read_u32(_node, _key, &_val); \
		if (_ret && _ret != -EINVAL) { \
			pr_err("Error reading key:%s. err:%d\n", _key, _ret); \
			goto bcl_dev_exit; \
		} else if (!_ret) { \
			_dest = _val; \
		} \
	} while (0)

enum bcl_monitor_state {
	BCL_PARAM_INACTIVE,
	BCL_PARAM_MONITOR,
	BCL_PARAM_POLLING,
};

enum bcl_hw_type {
	BCL_PMI8994,
	BCL_PMI8998,
	BCL_VERSION_MAX,
};

struct bcl_peripheral_data {
	struct bcl_param_data   *param_data;
	struct bcl_driver_ops   ops;
	enum bcl_monitor_state  state;
	struct delayed_work     poll_work;
	int                     irq_num;
	bool                    irq_requested;
	int                     high_trip;
	int                     low_trip;
	int                     trip_val;
	int                     scaling_factor;
	int                     offset_factor_num;
	int                     offset_factor_den;
	int                     offset;
	int                     gain_factor_num;
	int                     gain_factor_den;
	int                     gain;
	uint32_t                polling_delay_ms;
	int			inhibit_derating_ua;
	int (*read_max)         (int *adc_value);
	int (*clear_max)        (void);
	struct mutex            state_trans_lock;
};

struct bcl_device {
	bool				enabled;
	bool				ready;
	bool				stopping;
	struct workqueue_struct		*notify_wq;
	unsigned int			notify_generation;
	struct device			*dev;
	struct platform_device		*pdev;
	struct regmap			*regmap;
	uint16_t			base_addr;
	uint16_t			pon_spare_addr;
	uint16_t			fg_lmh_addr;
	int				i_src;
	struct bcl_peripheral_data	param[BCL_PARAM_MAX];
};

static struct bcl_device *bcl_perph;
static struct power_supply_desc bcl_psy_d;
static struct power_supply *bcl_psy;
static const char bcl_psy_name[] = "fg_adc";
static bool calibration_done;
static DEFINE_MUTEX(bcl_calibration_lock);
static DEFINE_MUTEX(bcl_enable_mutex);
static enum bcl_hw_type bcl_perph_version;

/* One ordered callback queue keeps current/voltage owner callbacks serialized.
 * Hardware IRQ and polling paths only queue real threshold readings.
 */
struct bcl_notification {
	struct work_struct work;
	struct bcl_device *chip;
	struct bcl_peripheral_data *param;
	unsigned int generation;
	int value;
	enum bcl_trip_type trip;
};

static void bcl_notify_work(struct work_struct *work)
{
	struct bcl_notification *event = container_of(work,
					struct bcl_notification, work);
	struct bcl_device *chip = event->chip;

	if (READ_ONCE(chip->enabled) && !READ_ONCE(chip->stopping) &&
	    event->generation == READ_ONCE(chip->notify_generation))
		event->param->ops.notify(event->param->param_data,
					 event->value, event->trip);
	kfree(event);
}

static void bcl_queue_notification(struct bcl_peripheral_data *param, int value,
				   enum bcl_trip_type trip)
{
	struct bcl_notification *event;

	mutex_lock(&bcl_enable_mutex);
	if (!bcl_perph->enabled || bcl_perph->stopping)
		goto out;
	event = kzalloc(sizeof(*event), GFP_KERNEL);
	if (!event) {
		pr_err("Cannot queue BCL threshold notification\n");
		goto out;
	}
	event->chip = bcl_perph;
	event->param = param;
	event->generation = bcl_perph->notify_generation;
	event->value = value;
	event->trip = trip;
	INIT_WORK(&event->work, bcl_notify_work);
	queue_work(bcl_perph->notify_wq, &event->work);
 out:
	mutex_unlock(&bcl_enable_mutex);
}

static int bcl_read_multi_register(int16_t reg_offset, uint8_t *data, int len)
{
	int  ret = 0, trace_len = 0;

	if (!bcl_perph) {
		pr_err("BCL device not initialized\n");
		return -EINVAL;
	}
	ret = regmap_bulk_read(bcl_perph->regmap,
			       (bcl_perph->base_addr + reg_offset), data, len);
	if (ret < 0) {
		pr_err("Error reading register %d. err:%d", reg_offset, ret);
		return ret;
	}
	while (trace_len < len) {
		trace_sony_bcl_hw_reg_access("Read",
			bcl_perph->base_addr + reg_offset + trace_len,
			data[trace_len]);
		trace_len++;
	}

	return ret;
}

static int bcl_read_register(int16_t reg_offset, uint8_t *data)
{
	return bcl_read_multi_register(reg_offset, data, 1);
}

static int bcl_write_general_register(int16_t reg_offset,
					uint16_t base, uint8_t data)
{
	int  ret = 0;
	uint8_t *write_buf = &data;

	if (!bcl_perph) {
		pr_err("BCL device not initialized\n");
		return -EINVAL;
	}
	ret = regmap_write(bcl_perph->regmap, (base + reg_offset), *write_buf);
	if (ret < 0) {
		pr_err("Error reading register %d. err:%d", reg_offset, ret);
		return ret;
	}
	pr_debug("wrote 0x%02x to 0x%04x\n", data, base + reg_offset);
	trace_sony_bcl_hw_reg_access("write", base + reg_offset, data);

	return ret;
}

static int bcl_write_register(int16_t reg_offset, uint8_t data)
{
	return bcl_write_general_register(reg_offset,
			bcl_perph->base_addr, data);
}

static void convert_vbat_to_adc_val(int *val)
{
	struct bcl_peripheral_data *perph_data = NULL;

	switch (bcl_perph_version) {
	case BCL_PMI8994:
		if (!bcl_perph)
			return;
		perph_data = &bcl_perph->param[BCL_PARAM_VOLTAGE];
		*val = (*val * 100
			/ (100 + (perph_data->gain_factor_num
			* perph_data->gain) * BCL_CONSTANT_NUM
			/ perph_data->gain_factor_den))
			/ perph_data->scaling_factor;
		break;
	case BCL_PMI8998:
		*val = *val / BCL_8998_VBAT_SCALING;
		break;
	default:
		break;
	}

	return;
}

static void convert_adc_to_vbat_val(int *val)
{
	struct bcl_peripheral_data *perph_data = NULL;

	switch (bcl_perph_version) {
	case BCL_PMI8994:
		if (!bcl_perph)
			return;
		perph_data = &bcl_perph->param[BCL_PARAM_VOLTAGE];
		*val = ((*val + 2) * perph_data->scaling_factor)
			* (100 + (perph_data->gain_factor_num
			* perph_data->gain)
			* BCL_CONSTANT_NUM  / perph_data->gain_factor_den)
			/ 100;
		break;
	case BCL_PMI8998:
		*val = *val * BCL_8998_VBAT_SCALING;
		break;
	default:
		break;
	}

	return;
}

static void convert_ibat_to_adc_val(int *val)
{
	struct bcl_peripheral_data *perph_data = NULL;

	switch (bcl_perph_version) {
	case BCL_PMI8994:
		if (!bcl_perph)
			return;
		perph_data = &bcl_perph->param[BCL_PARAM_CURRENT];
		*val = (*val * 100
			/ (100 + (perph_data->gain_factor_num
			* perph_data->gain)
			* BCL_CONSTANT_NUM / perph_data->gain_factor_den)
			- (perph_data->offset_factor_num * perph_data->offset)
			/ perph_data->offset_factor_den)
			/  perph_data->scaling_factor;
		break;
	case BCL_PMI8998:
		*val = *val / BCL_8998_IBAT_SCALING;
		break;
	default:
		break;
	}

	return;
}

static void convert_adc_to_ibat_val(int *val)
{
	struct bcl_peripheral_data *perph_data = NULL;

	switch (bcl_perph_version) {
	case BCL_PMI8994:
		if (!bcl_perph)
			return;
		perph_data = &bcl_perph->param[BCL_PARAM_CURRENT];
		*val = (*val * perph_data->scaling_factor
			+ (perph_data->offset_factor_num * perph_data->offset)
			/ perph_data->offset_factor_den)
			* (100 + (perph_data->gain_factor_num
			* perph_data->gain) * BCL_CONSTANT_NUM /
			perph_data->gain_factor_den) / 100;
		break;
	case BCL_PMI8998:
		*val = *val * BCL_8998_IBAT_SCALING;
		break;
	default:
		break;
	}

	return;
}

static int bcl_set_high_vbat(int thresh_value)
{
	bcl_perph->param[BCL_PARAM_VOLTAGE].high_trip = thresh_value;
	return 0;
}

static int bcl_set_low_ibat(int thresh_value)
{
	bcl_perph->param[BCL_PARAM_CURRENT].low_trip = thresh_value;
	return 0;
}

static int bcl_set_high_ibat(int thresh_value)
{
	int ret = 0, ibat_ua;
	int8_t val = 0;
	int too_high_thresh = BCL_IBAT_HIGH_THRESH_UA;

	ibat_ua = thresh_value;
	convert_ibat_to_adc_val(&thresh_value);
	pr_debug("Setting Ibat high trip:%d. ADC_val:%d\n", ibat_ua,
			thresh_value);
	val = (int8_t)thresh_value;
	ret = bcl_write_register((bcl_perph_version == BCL_PMI8994) ?
		BCL_IBAT_TRIP : BCL_8998_IBAT_HIGH, val);
	if (ret) {
		pr_err("Error accessing BCL peripheral. err:%d\n", ret);
		return ret;
	}
	bcl_perph->param[BCL_PARAM_CURRENT].high_trip = thresh_value;
	if (bcl_perph_version == BCL_PMI8998) {
		convert_ibat_to_adc_val(&too_high_thresh);
		pr_debug("Setting Ibat too high trip:%d. ADC_val:%d\n",
			BCL_IBAT_HIGH_THRESH_UA, too_high_thresh);
		val = (int8_t)too_high_thresh;
		ret = bcl_write_register(BCL_8998_IBAT_TOO_HIGH, val);
		if (ret) {
			pr_err("Error accessing BCL peripheral. err:%d\n", ret);
			return ret;
		}
	}

	if (bcl_perph->param[BCL_PARAM_CURRENT].inhibit_derating_ua == 0
			|| bcl_perph->pon_spare_addr == 0)
		return ret;

	ret = bcl_write_general_register(bcl_perph->pon_spare_addr,
			PON_SPARE_FULL_CURRENT, val);
	if (ret) {
		pr_debug("Error accessing PON register. err:%d\n", ret);
		return ret;
	}
	thresh_value = ibat_ua
		- bcl_perph->param[BCL_PARAM_CURRENT].inhibit_derating_ua;
	convert_ibat_to_adc_val(&thresh_value);
	val = (int8_t)thresh_value;
	ret = bcl_write_general_register(bcl_perph->pon_spare_addr,
			PON_SPARE_DERATED_CURRENT, val);
	if (ret) {
		pr_debug("Error accessing PON register. err:%d\n", ret);
		return ret;
	}

	return ret;
}

static int bcl_set_low_vbat(int thresh_value)
{
	int ret = 0, vbat_uv;
	int8_t val = 0;

	vbat_uv = thresh_value;
	convert_vbat_to_adc_val(&thresh_value);
	pr_debug("Setting Vbat low trip:%d. ADC_val:%d\n", vbat_uv,
			thresh_value);
	val = (int8_t)thresh_value;
	ret = bcl_write_register((bcl_perph_version == BCL_PMI8994)
		? BCL_VBAT_TRIP : BCL_8998_VBAT_ADC_LOW, val);
	if (ret) {
		pr_err("Error accessing BCL peripheral. err:%d\n", ret);
		return ret;
	}
	if (bcl_perph_version == BCL_PMI8998) {
		ret = bcl_write_register(BCL_8998_VBAT_COMP_LOW,
			BCL_VBAT_LOW_THRESHOLD);
		if (ret) {
			pr_err("Error accessing BCL peripheral. err:%d\n", ret);
			return ret;
		}
		pr_debug("Setting Vbat low comparator threshold:0x%x.\n",
			BCL_VBAT_LOW_THRESHOLD);
		ret = bcl_write_register(BCL_8998_VBAT_COMP_TLOW,
			BCL_VBAT_TLOW_THRESHOLD);
		if (ret) {
			pr_err("Error accessing BCL peripheral. err:%d\n", ret);
			return ret;
		}
		pr_debug("Setting Vbat too low comparator threshold:0x%x.\n",
			BCL_VBAT_TLOW_THRESHOLD);
	}
	bcl_perph->param[BCL_PARAM_VOLTAGE].low_trip = thresh_value;

	return ret;
}

static int bcl_lmh_dcvs_enable(void)
{
	struct scm_desc desc_arg = {};
	int ret;
	uint32_t *payload = NULL;

	payload = kzalloc(sizeof(uint32_t) * 5,	GFP_KERNEL);
	if (!payload)
		return -ENOMEM;

	payload[0] = LMH_SUB_FN_BCL;
	payload[1] = 0; /* unused sub-algorithm */
	payload[2] = LMH_ALGO_ENABLE;
	payload[3] = 1; /* number of values */
	payload[4] = 1;

	desc_arg.args[0] = SCM_BUFFER_PHYS(payload);
	desc_arg.args[1] = sizeof(uint32_t) * 5;
	desc_arg.args[2] = LMH_NODE_DCVS;
	desc_arg.args[3] = LMH_CLUSTER_0;
	desc_arg.args[4] = 0; /* version */
	desc_arg.arginfo = SCM_ARGS(5, SCM_RO, SCM_VAL, SCM_VAL,
				SCM_VAL, SCM_VAL);

	dmac_flush_range(payload, (void *)payload + 5 * (sizeof(uint32_t)));
	ret = scm_call2(SCM_SIP_FNID(SCM_SVC_LMH, LMH_DCVSH), &desc_arg);
	if (ret)
		goto out;
	desc_arg.args[3] = LMH_CLUSTER_1;
	ret = scm_call2(SCM_SIP_FNID(SCM_SVC_LMH, LMH_DCVSH), &desc_arg);
 out:
	kfree(payload);
	return ret;
}

static int bcl_access_monitor_enable(bool enable)
{
	struct bcl_peripheral_data *param;
	int i, ret;

	mutex_lock(&bcl_enable_mutex);
	if (!bcl_perph || !bcl_perph->ready ||
	    (enable && bcl_perph->stopping)) {
		mutex_unlock(&bcl_enable_mutex);
		return -ENODEV;
	}
	if (enable == bcl_perph->enabled)
		goto unlock_and_drain;
	if (enable && bcl_perph_version == BCL_PMI8998) {
		ret = bcl_lmh_dcvs_enable();
		if (ret) {
			mutex_unlock(&bcl_enable_mutex);
			return ret;
		}
	}
	for (i = 0; i < BCL_PARAM_MAX; i++) {
		param = &bcl_perph->param[i];
		mutex_lock(&param->state_trans_lock);
		if (enable) {
			param->state = BCL_PARAM_MONITOR;
			enable_irq(param->irq_num);
		} else {
			if (param->state == BCL_PARAM_MONITOR)
				disable_irq_nosync(param->irq_num);
			param->state = BCL_PARAM_INACTIVE;
			/* A running callback may update thresholds; never wait while
			 * holding a transition lock. Removal drains both workers.
			 */
			cancel_delayed_work(&param->poll_work);
		}
		mutex_unlock(&param->state_trans_lock);
	}
	bcl_perph->enabled = enable;
	if (!enable)
		bcl_perph->notify_generation++;
 unlock_and_drain:
	mutex_unlock(&bcl_enable_mutex);
	if (!enable) {
		/* Producers can queue after dropping their state lock; drain them
		 * before the callback queue or an old event could survive rearm.
		 */
		for (i = 0; i < BCL_PARAM_MAX; i++) {
			param = &bcl_perph->param[i];
			synchronize_irq(param->irq_num);
			cancel_delayed_work_sync(&param->poll_work);
		}
	}
	/* No transition lock is held while callbacks finish. A callback may
	 * disable monitoring itself; the ordered queue excludes a second owner
	 * callback, while the generation check drops pending old events.
	 */
	if (!enable && (!current_work() || current_work()->func != bcl_notify_work))
		flush_workqueue(bcl_perph->notify_wq);
	return 0;
}

static int bcl_monitor_enable(void)
{
	trace_sony_bcl_hw_event("BCL Enable");
	return bcl_access_monitor_enable(true);
}

static int bcl_monitor_disable(void)
{
	trace_sony_bcl_hw_event("BCL Disable");
	return bcl_access_monitor_enable(false);
}

static int bcl_read_ibat_high_trip(int *thresh_value)
{
	int ret = 0;
	int8_t val = 0;

	*thresh_value = (int)val;
	ret = bcl_read_register((bcl_perph_version == BCL_PMI8994) ?
		BCL_IBAT_TRIP : BCL_8998_IBAT_HIGH, &val);
	if (ret) {
		pr_err("BCL register read error. err:%d\n", ret);
		return ret;
	} else {
		*thresh_value = (int)val;
		convert_adc_to_ibat_val(thresh_value);
		pr_debug("Reading Ibat high trip:%d. ADC_val:%d\n",
				*thresh_value, val);
	}

	return ret;
}

static int bcl_read_ibat_low_trip(int *thresh_value)
{
	*thresh_value = bcl_perph->param[BCL_PARAM_CURRENT].low_trip;
	return 0;
}

static int bcl_read_vbat_low_trip(int *thresh_value)
{
	int ret = 0;
	int8_t val = 0;

	*thresh_value = (int)val;
	ret = bcl_read_register((bcl_perph_version == BCL_PMI8994)
			? BCL_VBAT_TRIP	: BCL_8998_VBAT_ADC_LOW,
			&val);
	if (ret) {
		pr_err("BCL register read error. err:%d\n", ret);
		return ret;
	} else {
		*thresh_value = (int)val;
		convert_adc_to_vbat_val(thresh_value);
		pr_debug("Reading Ibat high trip:%d. ADC_val:%d\n",
				*thresh_value, val);
	}

	return ret;
}

static int bcl_read_vbat_high_trip(int *thresh_value)
{
	*thresh_value = bcl_perph->param[BCL_PARAM_VOLTAGE].high_trip;
	return 0;
}

static int bcl_clear_vbat_min(void)
{
	int ret  = 0;

	if (bcl_perph_version == BCL_PMI8994)
		ret = bcl_write_register(BCL_VBAT_MIN_CLR, BIT(7));
	else
		ret = bcl_write_register(BCL_8998_MAX_MIN_CLR,
			BIT(BCL_8998_VBAT_MIN_CLR));
	if (ret)
		pr_err("Error in clearing vbat min reg. err:%d", ret);

	return ret;
}

static int bcl_clear_ibat_max(void)
{
	int ret  = 0;

	if (bcl_perph_version == BCL_PMI8994)
		ret = bcl_write_register(BCL_IBAT_MAX_CLR, BIT(7));
	else
		ret = bcl_write_register(BCL_8998_MAX_MIN_CLR,
			BIT(BCL_8998_IBAT_MAX_CLR));
	if (ret)
		pr_err("Error in clearing ibat max reg. err:%d", ret);

	return ret;
}

static int bcl_read_ibat_max(int *adc_value)
{
	int ret = 0, timeout = 0;
	int8_t val[VAL_CP_REG_BUF_LEN] = {0};

	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	do {
		ret = bcl_read_multi_register(
			(bcl_perph_version == BCL_PMI8994) ? BCL_IBAT_MAX
			: BCL_8998_IBAT_MAX, val,
			VAL_CP_REG_BUF_LEN);
		if (ret) {
			pr_err("BCL register read error. err:%d\n", ret);
			goto bcl_read_exit;
		}
	} while (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]
		&& timeout++ < BCL_READ_RETRY_LIMIT);
	if (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]) {
		ret = -ENODEV;
		goto bcl_read_exit;
	}
	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	convert_adc_to_ibat_val(adc_value);
	pr_debug("Ibat Max:%d. ADC_val:%d\n", *adc_value,
			val[VAL_REG_BUF_OFFSET]);
	trace_sony_bcl_hw_sensor_reading("Ibat Max[uA]", *adc_value);

bcl_read_exit:
	return ret;
}

static int bcl_read_vbat_min(int *adc_value)
{
	int ret = 0, timeout = 0;
	int8_t val[VAL_CP_REG_BUF_LEN] = {0};

	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	do {
		ret = bcl_read_multi_register(
			(bcl_perph_version == BCL_PMI8994) ? BCL_VBAT_MIN
			: BCL_8998_VBAT_MIN, val,
			VAL_CP_REG_BUF_LEN);
		if (ret) {
			pr_err("BCL register read error. err:%d\n", ret);
			goto bcl_read_exit;
		}
	} while (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]
		&& timeout++ < BCL_READ_RETRY_LIMIT);
	if (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]) {
		ret = -ENODEV;
		goto bcl_read_exit;
	}
	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	convert_adc_to_vbat_val(adc_value);
	pr_debug("Vbat Min:%d. ADC_val:%d\n", *adc_value,
			val[VAL_REG_BUF_OFFSET]);
	trace_sony_bcl_hw_sensor_reading("vbat Min[uV]", *adc_value);

bcl_read_exit:
	return ret;
}

static int bcl_read_ibat(int *adc_value)
{
	int ret = 0, timeout = 0;
	int8_t val[VAL_CP_REG_BUF_LEN] = {0};

	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	do {
		ret = bcl_read_multi_register(
			(bcl_perph_version == BCL_PMI8994) ? BCL_IBAT_VALUE
			: BCL_8998_IBAT_VALUE, val,
			VAL_CP_REG_BUF_LEN);
		if (ret) {
			pr_err("BCL register read error. err:%d\n", ret);
			goto bcl_read_exit;
		}
	} while (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]
		&& timeout++ < BCL_READ_RETRY_LIMIT);
	if (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]) {
		ret = -ENODEV;
		goto bcl_read_exit;
	}
	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	convert_adc_to_ibat_val(adc_value);
	pr_debug("Read Ibat:%d. ADC_val:%d\n", *adc_value,
			val[VAL_REG_BUF_OFFSET]);
	trace_sony_bcl_hw_sensor_reading("ibat[uA]", *adc_value);

bcl_read_exit:
	return ret;
}

static int bcl_read_vbat(int *adc_value)
{
	int ret = 0, timeout = 0;
	int8_t val[VAL_CP_REG_BUF_LEN] = {0};

	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	do {
		ret = bcl_read_multi_register(
			(bcl_perph_version == BCL_PMI8994) ? BCL_VBAT_VALUE :
			BCL_8998_VBAT_VALUE, val,
			VAL_CP_REG_BUF_LEN);
		if (ret) {
			pr_err("BCL register read error. err:%d\n", ret);
			goto bcl_read_exit;
		}
	} while (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]
		&& timeout++ < BCL_READ_RETRY_LIMIT);
	if (val[VAL_REG_BUF_OFFSET] != val[VAL_CP_REG_BUF_OFFSET]) {
		ret = -ENODEV;
		goto bcl_read_exit;
	}
	*adc_value = (int)val[VAL_REG_BUF_OFFSET];
	convert_adc_to_vbat_val(adc_value);
	pr_debug("Read Vbat:%d. ADC_val:%d\n", *adc_value,
			val[VAL_REG_BUF_OFFSET]);
	trace_sony_bcl_hw_sensor_reading("vbat[uV]", *adc_value);

bcl_read_exit:
	return ret;
}

static void bcl_poll_ibat_low(struct work_struct *work)
{
	int ret = 0, val = 0;
	bool notify = false;
	struct bcl_peripheral_data *perph_data =
		&bcl_perph->param[BCL_PARAM_CURRENT];

	trace_sony_bcl_hw_event("ibat poll low. Enter");
	mutex_lock(&perph_data->state_trans_lock);
	if (perph_data->state != BCL_PARAM_POLLING) {
		pr_err("Invalid ibat state %d\n", perph_data->state);
		goto exit_ibat;
	}

	ret = perph_data->read_max(&val);
	if (ret) {
		pr_err("Error in reading ibat. err:%d", ret);
		goto reschedule_ibat;
	}
	ret = perph_data->clear_max();
	if (ret)
		pr_err("Error clearing max ibat reg. err:%d\n", ret);
	if (val <= perph_data->low_trip) {
		pr_debug("Ibat reached low clear trip. ibat:%d\n", val);
		trace_sony_bcl_hw_state_event("Polling to Monitor. Ibat[uA]:", val);
		trace_sony_bcl_hw_mitigation("Ibat low trip. Ibat[uA]", val);
		notify = true;
		perph_data->state = BCL_PARAM_MONITOR;
		enable_irq(perph_data->irq_num);
	} else {
		goto reschedule_ibat;
	}

exit_ibat:
	mutex_unlock(&perph_data->state_trans_lock);
	if (notify)
		bcl_queue_notification(perph_data, val, BCL_LOW_TRIP);
	trace_sony_bcl_hw_event("ibat poll low. Exit");
	return;

reschedule_ibat:
	schedule_delayed_work(&perph_data->poll_work,
		msecs_to_jiffies(perph_data->polling_delay_ms));
	mutex_unlock(&perph_data->state_trans_lock);
	trace_sony_bcl_hw_event("ibat poll low. Exit");
	return;
}

static void bcl_poll_vbat_high(struct work_struct *work)
{
	int ret = 0, val = 0;
	bool notify = false;
	struct bcl_peripheral_data *perph_data =
		&bcl_perph->param[BCL_PARAM_VOLTAGE];

	trace_sony_bcl_hw_event("vbat poll high. Enter");
	mutex_lock(&perph_data->state_trans_lock);
	if (perph_data->state != BCL_PARAM_POLLING) {
		pr_err("Invalid vbat state %d\n", perph_data->state);
		goto exit_vbat;
	}

	ret = perph_data->read_max(&val);
	if (ret) {
		pr_err("Error in reading vbat. err:%d", ret);
		goto reschedule_vbat;
	}
	ret = perph_data->clear_max();
	if (ret)
		pr_err("Error clearing min vbat reg. err:%d\n", ret);
	if (val >= perph_data->high_trip) {
		pr_debug("Vbat reached high clear trip. vbat:%d\n", val);
		trace_sony_bcl_hw_state_event("Polling to Monitor. vbat[uV]:", val);
		trace_sony_bcl_hw_mitigation("vbat high trip. vbat[uV]", val);
		notify = true;
		perph_data->state = BCL_PARAM_MONITOR;
		enable_irq(perph_data->irq_num);
	} else {
		goto reschedule_vbat;
	}

exit_vbat:
	mutex_unlock(&perph_data->state_trans_lock);
	if (notify)
		bcl_queue_notification(perph_data, val, BCL_HIGH_TRIP);
	trace_sony_bcl_hw_event("vbat poll high. Exit");
	return;

reschedule_vbat:
	schedule_delayed_work(&perph_data->poll_work,
		msecs_to_jiffies(perph_data->polling_delay_ms));
	mutex_unlock(&perph_data->state_trans_lock);
	trace_sony_bcl_hw_event("vbat poll high. Exit");
	return;
}

static irqreturn_t bcl_handle_ibat(int irq, void *data)
{
	int thresh_value = 0, ret = 0;
	bool notify = false;
	struct bcl_peripheral_data *perph_data =
		(struct bcl_peripheral_data *)data;

	trace_sony_bcl_hw_mitigation_event("Ibat interrupted");
	mutex_lock(&perph_data->state_trans_lock);
	if (perph_data->state == BCL_PARAM_MONITOR) {
		ret = perph_data->read_max(&perph_data->trip_val);
		if (ret) {
			pr_err("Error reading max/min reg. err:%d\n", ret);
			goto exit_intr;
		}
		ret = perph_data->clear_max();
		if (ret)
			pr_err("Error clearing max/min reg. err:%d\n", ret);
		thresh_value = perph_data->high_trip;
		convert_adc_to_ibat_val(&thresh_value);
		/* Account threshold trip from PBS threshold for dead time */
		thresh_value -= perph_data->inhibit_derating_ua;
		if (perph_data->trip_val < thresh_value) {
			pr_debug("False Ibat high trip. ibat:%d ibat_thresh_val:%d\n",
				perph_data->trip_val, thresh_value);
			trace_sony_bcl_hw_event("Ibat invalid interrupt");
			goto exit_intr;
		}
		pr_debug("Ibat reached high trip. ibat:%d\n",
				perph_data->trip_val);
		trace_sony_bcl_hw_state_event("Monitor to Polling. ibat[uA]:",
				perph_data->trip_val);
		disable_irq_nosync(perph_data->irq_num);
		perph_data->state = BCL_PARAM_POLLING;
		trace_sony_bcl_hw_mitigation("ibat high trip. ibat[uA]",
				perph_data->trip_val);
		notify = true;
		schedule_delayed_work(&perph_data->poll_work,
			msecs_to_jiffies(perph_data->polling_delay_ms));
	} else {
		pr_debug("Ignoring interrupt\n");
		trace_sony_bcl_hw_event("Ibat Ignoring interrupt");
	}

exit_intr:
	mutex_unlock(&perph_data->state_trans_lock);
	if (notify)
		bcl_queue_notification(perph_data, perph_data->trip_val,
				       BCL_HIGH_TRIP);
	return IRQ_HANDLED;
}

static irqreturn_t bcl_handle_vbat(int irq, void *data)
{
	int thresh_value = 0, ret = 0;
	bool notify = false;
	struct bcl_peripheral_data *perph_data =
		(struct bcl_peripheral_data *)data;

	trace_sony_bcl_hw_mitigation_event("Vbat Interrupted");
	mutex_lock(&perph_data->state_trans_lock);
	if (perph_data->state == BCL_PARAM_MONITOR) {
		ret = perph_data->read_max(&perph_data->trip_val);
		if (ret) {
			pr_err("Error reading max/min reg. err:%d\n", ret);
			goto exit_intr;
		}
		ret = perph_data->clear_max();
		if (ret)
			pr_err("Error clearing max/min reg. err:%d\n", ret);
		thresh_value = perph_data->low_trip;
		convert_adc_to_vbat_val(&thresh_value);
		if (perph_data->trip_val > thresh_value) {
			pr_debug("False vbat min trip. vbat:%d vbat_thresh_val:%d\n",
				perph_data->trip_val, thresh_value);
			trace_sony_bcl_hw_event("Vbat Invalid interrupt");
			goto exit_intr;
		}
		pr_debug("Vbat reached Low trip. vbat:%d\n",
			perph_data->trip_val);
		trace_sony_bcl_hw_state_event("Monitor to Polling. vbat[uV]:",
				perph_data->trip_val);
		disable_irq_nosync(perph_data->irq_num);
		perph_data->state = BCL_PARAM_POLLING;
		trace_sony_bcl_hw_mitigation("vbat low trip. vbat[uV]",
				perph_data->trip_val);
		notify = true;
		schedule_delayed_work(&perph_data->poll_work,
			msecs_to_jiffies(perph_data->polling_delay_ms));
	} else {
		pr_debug("Ignoring interrupt\n");
		trace_sony_bcl_hw_event("Vbat Ignoring interrupt");
	}

exit_intr:
	mutex_unlock(&perph_data->state_trans_lock);
	if (notify)
		bcl_queue_notification(perph_data, perph_data->trip_val,
				       BCL_LOW_TRIP);
	return IRQ_HANDLED;
}

static const __be32 *bcl_get_address(struct device_node *node, const char *name)
{
	int index = of_property_match_string(node, "reg-names", name);

	return index < 0 ? NULL : of_get_address(node, index, NULL, NULL);
}

static int bcl_get_devicetree_data(struct platform_device *pdev)
{
	int ret = 0, irq_num = 0, temp_val = 0;
	char *key = NULL;
	const __be32 *prop = NULL;
	struct device_node *dev_node = pdev->dev.of_node;

	prop = bcl_get_address(dev_node, "fg_user_adc");
	if (prop) {
		bcl_perph->base_addr = be32_to_cpu(*prop);
		pr_debug("fg_user_adc@%04x\n", bcl_perph->base_addr);
	} else {
		dev_err(&pdev->dev, "No fg_user_adc registers found\n");
		return -EINVAL;
	}

	prop = bcl_get_address(dev_node, "pon_spare");
	if (prop) {
		bcl_perph->pon_spare_addr = be32_to_cpu(*prop);
		pr_debug("pon_spare@%04x\n", bcl_perph->pon_spare_addr);
	}

	/* Register SPMI peripheral interrupt */
	irq_num = platform_get_irq_byname(pdev, BCL_VBAT_INT_NAME);
	if (irq_num < 0) {
		pr_err("Invalid vbat IRQ\n");
		ret = irq_num;
		goto bcl_dev_exit;
	}
	bcl_perph->param[BCL_PARAM_VOLTAGE].irq_num = irq_num;
	irq_num = platform_get_irq_byname(pdev, BCL_IBAT_INT_NAME);
	if (irq_num < 0) {
		pr_err("Invalid ibat IRQ\n");
		ret = irq_num;
		goto bcl_dev_exit;
	}
	bcl_perph->param[BCL_PARAM_CURRENT].irq_num = irq_num;

	if (bcl_perph_version == BCL_PMI8994) {
		/* Get VADC and IADC scaling factor */
		key = "qcom,vbat-scaling-factor";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_VOLTAGE].scaling_factor);
		key = "qcom,vbat-gain-numerator";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_VOLTAGE].gain_factor_num);
		key = "qcom,vbat-gain-denominator";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_VOLTAGE].gain_factor_den);
		key = "qcom,ibat-scaling-factor";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_CURRENT].scaling_factor);
		key = "qcom,ibat-offset-numerator";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_CURRENT].offset_factor_num);
		key = "qcom,ibat-offset-denominator";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_CURRENT].offset_factor_den);
		key = "qcom,ibat-gain-numerator";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_CURRENT].gain_factor_num);
		key = "qcom,ibat-gain-denominator";
		READ_CONV_FACTOR(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_CURRENT].gain_factor_den);
		key = "qcom,inhibit-derating-ua";
		READ_OPTIONAL_PROP(dev_node, key, temp_val, ret,
			bcl_perph->param[BCL_PARAM_CURRENT].
			inhibit_derating_ua);
	} else {
		prop = bcl_get_address(dev_node, "fg_lmh");
		if (prop) {
			bcl_perph->fg_lmh_addr = be32_to_cpu(*prop);
			pr_debug("fg_lmh@%04x\n", bcl_perph->fg_lmh_addr);
		} else {
			return -ENODEV;
		}
	}
	if (bcl_perph_version == BCL_PMI8994 &&
	    (bcl_perph->param[BCL_PARAM_VOLTAGE].scaling_factor <= 0 ||
	     bcl_perph->param[BCL_PARAM_VOLTAGE].gain_factor_den <= 0 ||
	     bcl_perph->param[BCL_PARAM_CURRENT].scaling_factor <= 0 ||
	     bcl_perph->param[BCL_PARAM_CURRENT].gain_factor_den <= 0 ||
	     bcl_perph->param[BCL_PARAM_CURRENT].offset_factor_den <= 0))
		return -EINVAL;
	key = "qcom,vbat-polling-delay-ms";
	READ_CONV_FACTOR(dev_node, key, temp_val, ret,
		bcl_perph->param[BCL_PARAM_VOLTAGE].polling_delay_ms);
	key = "qcom,ibat-polling-delay-ms";
	READ_CONV_FACTOR(dev_node, key, temp_val, ret,
		bcl_perph->param[BCL_PARAM_CURRENT].polling_delay_ms);

	if (!bcl_perph->param[BCL_PARAM_VOLTAGE].polling_delay_ms ||
	    !bcl_perph->param[BCL_PARAM_CURRENT].polling_delay_ms)
		return -EINVAL;
bcl_dev_exit:
	return ret;
}

static int bcl_calibrate(void)
{
	int ret = 0;
	int8_t i_src = 0, val = 0;

	ret = bcl_read_register(BCL_I_SENSE_SRC, &i_src);
	if (ret) {
		pr_err("Error reading current sense reg. err:%d\n", ret);
		goto bcl_cal_exit;
	}

	ret = bcl_read_register((i_src & 0x01) ? BCL_I_GAIN_RSENSE
		: BCL_I_GAIN_BATFET, &val);
	if (ret) {
		pr_err("Error reading %s current gain. err:%d\n",
			(i_src & 0x01) ? "rsense" : "batfet", ret);
		goto bcl_cal_exit;
	}
	bcl_perph->param[BCL_PARAM_CURRENT].gain = val;
	ret = bcl_read_register((i_src & 0x01) ? BCL_I_OFFSET_RSENSE
		: BCL_I_OFFSET_BATFET, &val);
	if (ret) {
		pr_err("Error reading %s current offset. err:%d\n",
			(i_src & 0x01) ? "rsense" : "batfet", ret);
		goto bcl_cal_exit;
	}
	bcl_perph->param[BCL_PARAM_CURRENT].offset = val;
	ret = bcl_read_register(BCL_V_GAIN_BAT, &val);
	if (ret) {
		pr_err("Error reading vbat offset. err:%d\n", ret);
		goto bcl_cal_exit;
	}
	bcl_perph->param[BCL_PARAM_VOLTAGE].gain = val;

	if (((i_src & 0x01) != bcl_perph->i_src)
		&& (bcl_perph->enabled)) {
		/* Backend trip fields are ADC codes; framework caches micro-units. */
		ret = bcl_set_low_vbat(bcl_perph->param[BCL_PARAM_VOLTAGE]
				.param_data->low_trip);
		if (ret)
			goto bcl_cal_exit;
		ret = bcl_set_high_ibat(bcl_perph->param[BCL_PARAM_CURRENT]
				.param_data->high_trip);
		if (ret)
			goto bcl_cal_exit;
	}
	bcl_perph->i_src = i_src;
bcl_cal_exit:
	return ret;
}

static void power_supply_callback(struct power_supply *psy)
{
	struct power_supply *bms;
	int ret;

	mutex_lock(&bcl_calibration_lock);
	if (calibration_done || !bcl_perph || bcl_perph->stopping)
		goto out;
	bms = power_supply_get_by_name("bms");
	if (!bms)
		goto out;
	ret = bcl_calibrate();
	power_supply_put(bms);
	if (!ret)
		calibration_done = true;
	else
		pr_err("Could not read calibration values: %d\n", ret);
 out:
	mutex_unlock(&bcl_calibration_lock);
}

static int bcl_psy_get_property(struct power_supply *psy,
			      enum power_supply_property prop,
			      union power_supply_propval *val)
{
	return -EINVAL;
}

static int bcl_update_data(void)
{
	int ret = 0;

	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.read = bcl_read_vbat;
	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.get_high_trip
		= bcl_read_vbat_high_trip;
	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.get_low_trip
		= bcl_read_vbat_low_trip;
	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.set_high_trip
		= bcl_set_high_vbat;
	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.set_low_trip
		= bcl_set_low_vbat;
	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.enable
		 = bcl_monitor_enable;
	bcl_perph->param[BCL_PARAM_VOLTAGE].ops.disable
		= bcl_monitor_disable;
	bcl_perph->param[BCL_PARAM_VOLTAGE].read_max
		 = bcl_read_vbat_min;
	bcl_perph->param[BCL_PARAM_VOLTAGE].clear_max
		 = bcl_clear_vbat_min;

	bcl_perph->param[BCL_PARAM_CURRENT].ops.read = bcl_read_ibat;
	bcl_perph->param[BCL_PARAM_CURRENT].ops.get_high_trip
		= bcl_read_ibat_high_trip;
	bcl_perph->param[BCL_PARAM_CURRENT].ops.get_low_trip
		= bcl_read_ibat_low_trip;
	bcl_perph->param[BCL_PARAM_CURRENT].ops.set_high_trip
		 = bcl_set_high_ibat;
	bcl_perph->param[BCL_PARAM_CURRENT].ops.set_low_trip
		 = bcl_set_low_ibat;
	bcl_perph->param[BCL_PARAM_CURRENT].ops.enable
		= bcl_monitor_enable;
	bcl_perph->param[BCL_PARAM_CURRENT].ops.disable
		= bcl_monitor_disable;
	bcl_perph->param[BCL_PARAM_CURRENT].read_max
		= bcl_read_ibat_max;
	bcl_perph->param[BCL_PARAM_CURRENT].clear_max
		= bcl_clear_ibat_max;


	bcl_perph->param[BCL_PARAM_VOLTAGE].param_data = msm_bcl_register_param(
		BCL_PARAM_VOLTAGE, &bcl_perph->param[BCL_PARAM_VOLTAGE].ops,
		"vbat");
	if (!bcl_perph->param[BCL_PARAM_VOLTAGE].param_data) {
		pr_err("register Vbat failed.\n");
		ret = -ENODEV;
		goto update_data_exit;
	}
	bcl_perph->param[BCL_PARAM_CURRENT].param_data = msm_bcl_register_param(
		BCL_PARAM_CURRENT, &bcl_perph->param[BCL_PARAM_CURRENT].ops,
		"ibat");
	if (!bcl_perph->param[BCL_PARAM_CURRENT].param_data) {
		pr_err("register Ibat failed.\n");
		ret = -ENODEV;
		goto update_data_exit;
	}

update_data_exit:
	return ret;
}

static void bcl_cleanup(struct platform_device *pdev)
{
	int i;

	mutex_lock(&bcl_calibration_lock);
	bcl_perph->stopping = true;
	mutex_unlock(&bcl_calibration_lock);
	if (bcl_perph->ready)
		bcl_monitor_disable();
	bcl_perph->ready = false;
	/* Stop public callbacks before withdrawing their static API objects. */
	if (bcl_psy) {
		power_supply_unregister(bcl_psy);
		bcl_psy = NULL;
	}
	for (i = 0; i < BCL_PARAM_MAX; i++) {
		struct bcl_peripheral_data *param = &bcl_perph->param[i];

		if (param->irq_requested)
			devm_free_irq(&pdev->dev, param->irq_num, param);
		cancel_delayed_work_sync(&param->poll_work);
	}
	if (bcl_perph->notify_wq)
		destroy_workqueue(bcl_perph->notify_wq);
	for (i = 0; i < BCL_PARAM_MAX; i++)
		if (bcl_perph->param[i].param_data)
			msm_bcl_unregister_param(bcl_perph->param[i].param_data);
	dev_set_drvdata(&pdev->dev, NULL);
	bcl_perph = NULL;
}

static int bcl_probe(struct platform_device *pdev)
{
	struct power_supply_config psy_cfg = {};
	const struct of_device_id *match;
	struct bcl_device *chip;
	int i, ret;

	if (bcl_perph)
		return -EBUSY;
	chip = devm_kzalloc(&pdev->dev, sizeof(*chip), GFP_KERNEL);
	if (!chip)
		return -ENOMEM;
	chip->regmap = dev_get_regmap(pdev->dev.parent, NULL);
	if (!chip->regmap)
		return -EPROBE_DEFER;
	match = of_match_device(pdev->dev.driver->of_match_table, &pdev->dev);
	if (!match)
		return -ENODEV;
	bcl_perph_version = (uintptr_t)match->data;
	chip->pdev = pdev;
	chip->dev = &pdev->dev;
	bcl_perph = chip;
	calibration_done = false;
	/* Initialize async objects even when DT parsing or calibration fails. */
	INIT_DELAYED_WORK(&chip->param[BCL_PARAM_VOLTAGE].poll_work,
			  bcl_poll_vbat_high);
	INIT_DELAYED_WORK(&chip->param[BCL_PARAM_CURRENT].poll_work,
			  bcl_poll_ibat_low);
	for (i = 0; i < BCL_PARAM_MAX; i++)
		mutex_init(&chip->param[i].state_trans_lock);
	chip->notify_wq = alloc_ordered_workqueue("sony-bcl-notify", WQ_MEM_RECLAIM);
	if (!chip->notify_wq) {
		ret = -ENOMEM;
		goto fail;
	}
	ret = bcl_get_devicetree_data(pdev);
	if (ret)
		goto fail;
	if (bcl_perph_version == BCL_PMI8994) {
		ret = bcl_calibrate();
		if (ret)
			goto fail;
	} else {
		ret = bcl_write_register(BCL_8998_LMH_CFG, BCL_LMH_CFG_VAL);
		if (ret)
			goto fail;
		ret = bcl_write_register(BCL_8998_BCL_CFG, BCL_CFG_VAL);
		if (ret)
			goto fail;
		ret = bcl_write_general_register(LMH_8998_INT_POL_HIGH,
				chip->fg_lmh_addr, LMH_INT_VAL);
		if (ret)
			goto fail;
		ret = bcl_write_general_register(LMH_8998_INT_EN,
				chip->fg_lmh_addr, LMH_INT_VAL);
		if (ret)
			goto fail;
	}
	ret = bcl_update_data();
	if (ret)
		goto fail;
	for (i = 0; i < BCL_PARAM_MAX; i++) {
		struct bcl_peripheral_data *param = &chip->param[i];

		mutex_lock(&param->state_trans_lock);
		ret = devm_request_threaded_irq(&pdev->dev, param->irq_num,
			NULL, i == BCL_PARAM_VOLTAGE ? bcl_handle_vbat : bcl_handle_ibat,
			IRQF_TRIGGER_RISING | IRQF_ONESHOT, "sony_bcl", param);
		if (!ret) {
			param->irq_requested = true;
			/* Wait for users to install real high/low thresholds. */
			disable_irq_nosync(param->irq_num);
		}
		mutex_unlock(&param->state_trans_lock);
		if (ret)
			goto fail;
	}
	ret = bcl_write_register(BCL_MONITOR_EN, BIT(7));
	if (ret)
		goto fail;
	if (bcl_perph_version == BCL_PMI8994) {
		bcl_psy_d.name = bcl_psy_name;
		bcl_psy_d.type = POWER_SUPPLY_TYPE_UNKNOWN;
		bcl_psy_d.get_property = bcl_psy_get_property;
		bcl_psy_d.external_power_changed = power_supply_callback;
		psy_cfg.drv_data = chip;
		bcl_psy = power_supply_register(&pdev->dev, &bcl_psy_d, &psy_cfg);
		if (IS_ERR(bcl_psy)) {
			ret = PTR_ERR(bcl_psy);
			bcl_psy = NULL;
			goto fail;
		}
	}
	chip->ready = true;
	dev_set_drvdata(&pdev->dev, chip);
	return 0;
 fail:
	bcl_cleanup(pdev);
	return ret;
}

static int bcl_remove(struct platform_device *pdev)
{
	bcl_cleanup(pdev);
	return 0;
}

static struct of_device_id bcl_match[] = {
	{	.compatible	= "qcom,msm-bcl",
		.data		= (void *) BCL_PMI8994,
	},
	{	.compatible	= "qcom,msm-bcl-lmh",
		.data		= (void *) BCL_PMI8998,
	},
	{},
};

static struct platform_driver bcl_driver = {
	.probe	= bcl_probe,
	.remove	= bcl_remove,
	.driver	= {
		.name		= BCL_DRIVER_NAME,
		.owner		= THIS_MODULE,
		.of_match_table	= bcl_match,
		.suppress_bind_attrs = true,
	},
};

static int __init bcl_perph_init(void)
{
	return platform_driver_register(&bcl_driver);
}

static void __exit bcl_perph_exit(void)
{
	platform_driver_unregister(&bcl_driver);
}
fs_initcall(bcl_perph_init);
module_exit(bcl_perph_exit);
MODULE_ALIAS("platform:" BCL_DRIVER_NAME);
