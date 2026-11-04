/* SPDX-License-Identifier: GPL-2.0-only */
/* Sony MSM8996 clock voltage contracts, adapted to native CCF.
 * Hardware limits: Sony 4.4 commit 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1.
 * Copyright (c) 2013-2017, The Linux Foundation. All rights reserved.
 */
#ifndef __QCOM_CLK_MSM8996_VOLTAGE_H
#define __QCOM_CLK_MSM8996_VOLTAGE_H

#include <linux/clk-provider.h>
#include <linux/err.h>
#include <linux/limits.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>
#include <linux/regulator/qcom-rpm-corner.h>
#include <linux/regulator/qcom-rpm-physical.h>
#include <linux/slab.h>

/* Logical RPM corners, not physical microvolt values. */
enum msm8996_voltage_level {
	MSM8996_VOLTAGE_NONE,
	MSM8996_VOLTAGE_LOWER,
	MSM8996_VOLTAGE_LOW,
	MSM8996_VOLTAGE_NOMINAL,
	MSM8996_VOLTAGE_HIGH,
	MSM8996_VOLTAGE_LEVELS,
};

static const unsigned int msm8996_logical_corner[] = { 1, 3, 4, 5, 7 };

struct msm8996_voltage {
	struct clk_vdd_class vdd;
	struct regulator *digital;
	struct regulator *analog;
	struct device *dev;
	int votes[MSM8996_VOLTAGE_LEVELS];
	int applied;
	bool digital_enabled;
	bool analog_enabled;
	bool analog_boot_load;
	bool analog_uncertain;
	bool initialized;
	bool ownership_known;
	bool faulted;
};

#define MSM8996_VOLTAGE_CLASS(_name) \
	{ .vdd = { .class_name = (_name), \
		.num_levels = MSM8996_VOLTAGE_LEVELS }, .applied = -1 }

struct msm8996_voltage_clock {
	struct clk_hw *hw;
	const struct clk_init_data *original;
	unsigned long fmax[MSM8996_VOLTAGE_LEVELS];
	bool analog;
	bool pll;
};

static bool msm8996_voltage_required(struct device *dev)
{
	return of_machine_is_compatible("somc,dora-row") ||
		of_machine_is_compatible("sony,dora-row") ||
		of_property_read_bool(dev->of_node,
				      "sony,msm8996-voltage-coupling");
}

/* Caller is serialized by the CCF vdd_class lock. Provider initialization
 * runs before its clocks are published. Every successful return is ACKed.
 */
static int msm8996_voltage_apply(struct msm8996_voltage *voltage, int level)
{
	int ret;

	if (level < 0 || level >= MSM8996_VOLTAGE_LEVELS)
		return -EINVAL;
	/* A typed provider quarantine must not be cleared by a CCF retry.
	 * Recovering an RPM request baseline alone does not reconcile clock
	 * ownership or a consumer enable count after a lost SWEN ACK.
	 */
	if (voltage->analog_uncertain)
		return -EIO;
	/* Preserve a real source-derived HIGH lease while firmware clock
	 * ownership/rates are unknown. Never infer a voltage from firmware.
	 */
	if (!voltage->ownership_known && level < MSM8996_VOLTAGE_HIGH)
		return -EAGAIN;
	ret = regulator_set_voltage(voltage->digital,
			msm8996_logical_corner[level],
			msm8996_logical_corner[MSM8996_VOLTAGE_HIGH]);
	if (ret)
		return ret;
	if (level && !voltage->digital_enabled) {
		ret = regulator_enable(voltage->digital);
		if (ret)
			return ret;
		voltage->digital_enabled = true;
	}
	if (voltage->analog && level) {
		/* Sony's temporary L12 clock proxy requests 10 mA. Steady clock
		 * consumers have no load request after qualified handoff.
		 */
		ret = regulator_set_load(voltage->analog,
				voltage->analog_boot_load ? 10000 : 0);
		if (ret) {
			voltage->analog_uncertain = true;
			return ret;
		}
		/* Original MMPLL4/MMPLL9 analog level is exactly 1.8 V. */
		ret = regulator_set_voltage(voltage->analog, 1800000, 1800000);
		if (ret) {
			voltage->analog_uncertain = true;
			return ret;
		}
		if (!voltage->analog_enabled) {
			ret = regulator_enable(voltage->analog);
			if (ret) {
				voltage->analog_uncertain = true;
				return ret;
			}
			voltage->analog_enabled = true;
		}
	}
	if (!level) {
		if (voltage->analog_enabled) {
			ret = regulator_disable(voltage->analog);
			if (ret) {
				voltage->analog_uncertain = true;
				return ret;
			}
			voltage->analog_enabled = false;
		}
		if (voltage->digital_enabled) {
			ret = regulator_disable(voltage->digital);
			if (ret)
				return ret;
			voltage->digital_enabled = false;
		}
	}
	if (voltage->analog) {
		ret = qcom_rpm_physical_sync(voltage->analog, level != 0);
		if (ret) {
			voltage->analog_uncertain = true;
			return ret;
		}
	}
	ret = qcom_rpm_corner_sync(voltage->digital, level != 0);
	if (ret)
		return ret;
	voltage->applied = level;
	voltage->faulted = false;
	return 0;
}

static int msm8996_voltage_set(struct clk_vdd_class *vdd, int level)
{
	struct msm8996_voltage *voltage = container_of(vdd,
						struct msm8996_voltage, vdd);
	int old = voltage->applied;
	int ret, rollback;

	ret = msm8996_voltage_apply(voltage, level);
	if (!ret)
		return 0;
	/* Clock programming has not begun when a new vote fails. Restore the
	 * previously ACKed requirement. An unknown bootstrap may only recover
	 * to the stock HIGH corner, never to an invented lower voltage.
	 */
	rollback = msm8996_voltage_apply(voltage,
		old < 0 ? MSM8996_VOLTAGE_HIGH : old);
	voltage->faulted = rollback != 0;
	return ret;
}

static int msm8996_voltage_sync(struct clk_vdd_class *vdd, int level)
{
	struct msm8996_voltage *voltage = container_of(vdd,
						struct msm8996_voltage, vdd);
	int ret;

	if (voltage->analog_uncertain)
		return -EIO;
	if (voltage->faulted || voltage->applied != level)
		return msm8996_voltage_set(vdd, level);
	ret = qcom_rpm_corner_sync(voltage->digital, level != 0);
	if (!ret && voltage->analog) {
		ret = qcom_rpm_physical_sync(voltage->analog, level != 0);
		if (ret)
			voltage->analog_uncertain = true;
	}
	if (ret)
		voltage->faulted = true;
	return ret;
}

/* These built-in controllers already own static native clock objects. Keep
 * one bounded voltage object/real consumer lease across deferred retries.
 * A failed probe after a real bootstrap must not release unknown boot-active
 * clocks to NONE. Retrying reuses the same handles, not another enable vote.
 */
static int msm8996_voltage_init(struct device *dev,
		struct msm8996_voltage *voltage, const char *digital,
		const char *analog)
{
	struct regulator *dig, *ana = NULL;
	int ret;

	if (voltage->initialized) {
		if (voltage->dev != dev)
			return -EBUSY;
		voltage->ownership_known = false;
		return msm8996_voltage_set(&voltage->vdd, MSM8996_VOLTAGE_HIGH);
	}
	dig = regulator_get(dev, digital);
	if (IS_ERR(dig))
		return PTR_ERR(dig);
	if (analog) {
		ana = regulator_get(dev, analog);
		if (IS_ERR(ana)) {
			ret = PTR_ERR(ana);
			regulator_put(dig);
			return ret;
		}
		/* No bootstrap or retained handle until the complete physical bank
		 * is online. Registration may expose an rdev before activation.
		 */
		ret = qcom_rpm_physical_check_ready(ana);
		if (ret) {
			regulator_put(ana);
			regulator_put(dig);
			return ret;
		}
	}
	voltage->digital = dig;
	voltage->analog = ana;
	voltage->analog_boot_load = ana != NULL;
	/* Consumer regulator handles store a raw device pointer. Pin the
	 * bounded built-in quarantine object even if a platform device is
	 * dynamically removed after a failed probe; retries reuse this ref.
	 */
	voltage->dev = get_device(dev);
	voltage->vdd.level_votes = voltage->votes;
	voltage->vdd.set_vdd = msm8996_voltage_set;
	voltage->vdd.sync_vdd = msm8996_voltage_sync;
	/* The ordinary CCF handoff adds the HIGH lease and releases it only
	 * after unused clocks are parked. Do not skip that source protocol.
	 */
	voltage->vdd.skip_handoff = false;
	voltage->vdd.require_handoff = true;
	voltage->vdd.cur_level = 0;
	mutex_init(&voltage->vdd.lock);
	voltage->initialized = true;
	ret = msm8996_voltage_set(&voltage->vdd, MSM8996_VOLTAGE_HIGH);
	return ret;
}

/* The CCF query takes the prepare/list locks and must precede vdd.lock.
 * A failed drop remains quarantined; it is never reported as a released
 * source proxy merely because a consumer load request was cached.
 */
static int msm8996_voltage_finish_handoff(struct msm8996_voltage *voltage)
{
	int ret = 0;

	if (!voltage->initialized || !voltage->analog_boot_load ||
	    !clk_vdd_class_handoff_done(&voltage->vdd))
		return 0;
	mutex_lock(&voltage->vdd.lock);
	if (!voltage->analog_boot_load || !voltage->ownership_known ||
	    voltage->faulted || voltage->analog_uncertain)
		goto out;
	ret = regulator_set_load(voltage->analog, 0);
	if (!ret)
		ret = qcom_rpm_physical_sync(voltage->analog,
					      voltage->analog_enabled);
	if (ret) {
		/* set_load caches the consumer request even on failure. Restore
		 * our source lease as well as retaining the flag. A quarantined
		 * backend rejects the wire operation; that is not recovery.
		 */
		regulator_set_load(voltage->analog, 10000);
		voltage->analog_uncertain = true;
		voltage->faulted = true;
	} else {
		voltage->analog_boot_load = false;
	}
out:
	mutex_unlock(&voltage->vdd.lock);
	return ret;
}

static int msm8996_voltage_attach(struct device *dev,
		struct msm8996_voltage_clock *clocks, unsigned int count,
		struct msm8996_voltage *digital,
		struct msm8996_voltage *analog)
{
	struct clk_init_data *init;
	unsigned int i;

	for (i = 0; i < count; i++) {
		if (!clocks[i].original)
			clocks[i].original = clocks[i].hw->init;
		init = devm_kmemdup(dev, clocks[i].original, sizeof(*init),
				    GFP_KERNEL);
		if (!init)
			return -ENOMEM;
		if (clocks[i].analog && !analog)
			return -EINVAL;
		init->vdd_class = clocks[i].analog ? &analog->vdd : &digital->vdd;
		init->rate_max = clocks[i].fmax;
		init->num_rate_max = MSM8996_VOLTAGE_LEVELS;
		init->flags |= CLK_ENABLE_HAND_OFF;
		clocks[i].hw->init = init;
	}
	return 0;
}

static void msm8996_voltage_ownership(struct device *dev,
		struct msm8996_voltage_clock *clocks, unsigned int count,
		struct msm8996_voltage *digital,
		struct msm8996_voltage *analog)
{
	unsigned int i;
	bool known = true;

	for (i = 0; i < count; i++) {
		/* An orphaned boot-active DSI parent must not silently become a
		 * zero-Hz/LOWER vote merely because the panel graph is gated.
		 */
		if (clk_hw_is_enabled(clocks[i].hw) &&
		    !clk_hw_get_rate(clocks[i].hw))
			known = false;
		/* A main postdivider has no .is_enabled callback. Its raw PLL
		 * may still be running with no counted output owner, especially
		 * for a provider probing after unused-clock cleanup. Retain HIGH
		 * until ownership is established; do not infer an idle VCO.
		 */
		if (clocks[i].pll &&
		    clk_hw_is_enabled(clk_hw_get_parent(clocks[i].hw)) &&
		    !clk_hw_is_prepared(clocks[i].hw))
			known = false;
	}
	digital->ownership_known = known;
	if (analog)
		analog->ownership_known = known;
	if (!known)
		dev_warn(dev, "unknown boot clock parent: retaining ACKed HIGH corner handoff\n");
	else {
		/* Before late init this retains the normal handoff lease until
		 * unused clocks are parked. A late probe can transfer it only
		 * after every boot-active clock has a counted owner/rate.
		 * An unsuccessful release keeps the CCF record and HIGH vote.
		 */
		if (clk_vdd_class_release_handoff(&digital->vdd))
			dev_warn(dev, "retaining digital HIGH lease after release failure\n");
		if (analog && clk_vdd_class_release_handoff(&analog->vdd))
			dev_warn(dev, "retaining analog HIGH lease after release failure\n");
		if (analog && msm8996_voltage_finish_handoff(analog))
			dev_warn(dev, "retaining quarantined analog bootstrap load\n");
	}
}

#endif
