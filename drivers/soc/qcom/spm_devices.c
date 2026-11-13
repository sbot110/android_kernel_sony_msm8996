// SPDX-License-Identifier: GPL-2.0-only
/* Copyright (c) 2011-2020  The Linux Foundation. All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 and
 * only version 2 as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/slab.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/err.h>
#include <linux/platform_device.h>
#include <linux/err.h>
#include <linux/cpu.h>
#include <linux/atomic.h>
#include <linux/mutex.h>
#include <linux/spinlock.h>
#include <linux/wait.h>
#include <soc/qcom/spm.h>
#include "spm_driver.h"

#define VDD_DEFAULT 0xDEADF00D
#define VDD_UNKNOWN 0xDEADF00E
#define SLP_CMD_BIT 17
#define PC_MODE_BIT 16
#define RET_MODE_BIT 15
#define EVENT_SYNC_BIT 24
#define ISAR_BIT 3
#define SPM_EN_BIT 0

struct msm_spm_power_modes {
	uint32_t mode;
	uint32_t ctl;
};

struct msm_spm_device {
	struct list_head list;
	struct list_head claim;
	struct device *owner;
	struct resource resource;
	raw_spinlock_t lock;
	atomic_t users;
	wait_queue_head_t drain;
	bool published;
	bool retired;
	int error;
	bool initialized;
	const char *name;
	struct msm_spm_driver_data reg_data;
	struct msm_spm_power_modes *modes;
	uint32_t num_modes;
	uint32_t cpu_vdd;
	struct cpumask mask;
	void __iomem *q2s_reg;
	bool qchannel_ignore;
	bool allow_rpm_hs;
	bool use_spm_clk_gating;
	bool use_qchannel_for_wfi;
	void __iomem *flush_base_addr;
	void __iomem *slpreq_base_addr;
};

struct msm_spm_vdd_info {
	struct msm_spm_device *vctl_dev;
	uint32_t vlevel;
	int err;
};

static LIST_HEAD(spm_list);
static LIST_HEAD(spm_claims);
static DEFINE_MUTEX(spm_owner_lock);
static DEFINE_MUTEX(spm_probe_lock);
static DEFINE_PER_CPU_SHARED_ALIGNED(struct msm_spm_device, msm_cpu_spm_device);
static DEFINE_PER_CPU(struct msm_spm_device *, cpu_vctl_device);

/* Raw handles have no put API. Claimed descriptors are pinned for this boot;
 * active operations additionally pin their devres mappings until retirement
 * drains them. Acquisition is usable by IRQ-disabled idle/hotplug callers.
 */
static int msm_spm_get(struct msm_spm_device *dev)
{
	unsigned long flags;
	int ret = 0;

	if (IS_ERR_OR_NULL(dev))
		return dev ? PTR_ERR(dev) : -EPROBE_DEFER;
	if (!smp_load_acquire(&dev->published))
		return READ_ONCE(dev->retired) ?
			(READ_ONCE(dev->error) ? READ_ONCE(dev->error) : -ESHUTDOWN) :
			-EPROBE_DEFER;
	raw_spin_lock_irqsave(&dev->lock, flags);
	if (dev->retired)
		ret = dev->error ? dev->error : -ESHUTDOWN;
	else if (dev->error)
		ret = dev->error;
	else
		atomic_inc(&dev->users);
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	return ret;
}

static void msm_spm_put(struct msm_spm_device *dev)
{
	if (atomic_dec_and_test(&dev->users))
		wake_up_all(&dev->drain);
}

static int msm_spm_state_locked(struct msm_spm_device *dev)
{
	return dev->error ? dev->error : dev->retired ? -ESHUTDOWN : 0;
}

static int msm_spm_fault_locked(struct msm_spm_device *dev, int error)
{
	if (!dev->error)
		dev->error = error < 0 ? error : -EIO;
	dev->cpu_vdd = VDD_UNKNOWN;
	return dev->error;
}

static void msm_spm_lifetime_init(struct msm_spm_device *dev)
{
	raw_spin_lock_init(&dev->lock);
	atomic_set(&dev->users, 0);
	init_waitqueue_head(&dev->drain);
}

static void msm_spm_retire(struct msm_spm_device *dev, int error)
{
	unsigned long flags;

	raw_spin_lock_irqsave(&dev->lock, flags);
	dev->retired = true;
	dev->initialized = false;
	if (!dev->error)
		dev->error = error < 0 ? error : -ESHUTDOWN;
	smp_store_release(&dev->published, false);
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	wait_event(dev->drain, !atomic_read(&dev->users));
}

/* A failed probe is published as ERR_PTR for every CPU sharing this SAW. */
static struct msm_spm_device *msm_spm_vctl_device(unsigned int cpu)
{
	struct msm_spm_device *dev;

	if (cpu >= nr_cpu_ids || !cpu_possible(cpu))
		return ERR_PTR(-EINVAL);
	dev = READ_ONCE(per_cpu(cpu_vctl_device, cpu));
	if (!dev)
		return ERR_PTR(-EPROBE_DEFER);
	return dev;
}

static void msm_spm_smp_set_vdd(void *data)
{
	struct msm_spm_vdd_info *info = (struct msm_spm_vdd_info *)data;
	struct msm_spm_device *dev = info->vctl_dev;
	unsigned long flags;

	raw_spin_lock_irqsave(&dev->lock, flags);
	info->err = msm_spm_state_locked(dev);
	if (!info->err) {
		info->err = msm_spm_drv_set_vdd(&dev->reg_data, info->vlevel);
		if (info->err)
			info->err = msm_spm_fault_locked(dev, info->err);
		else
			dev->cpu_vdd = info->vlevel;
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
}

/**
 * msm_spm_probe_done(): Verify and return the status of the cpu(s) and l2
 * probe.
 * Return: 0 if all spm devices have been probed, else return -EPROBE_DEFER.
 * if probe failed, then return the err number for that failure.
 */
int msm_spm_probe_done(void)
{
	struct msm_spm_device *dev;
	int cpu;

	for_each_possible_cpu(cpu) {
		int ret;

		dev = msm_spm_vctl_device(cpu);
		ret = msm_spm_get(dev);
		if (ret)
			return ret;
		msm_spm_put(dev);
	}

	return 0;
}
EXPORT_SYMBOL(msm_spm_probe_done);

void msm_spm_dump_regs(unsigned int cpu)
{
	struct msm_spm_device *dev;
	unsigned long flags;

	if (cpu >= nr_cpu_ids || !cpu_possible(cpu))
		return;
	dev = &per_cpu(msm_cpu_spm_device, cpu);
	if (msm_spm_get(dev))
		return;
	raw_spin_lock_irqsave(&dev->lock, flags);
	if (!msm_spm_state_locked(dev))
		dump_regs(&dev->reg_data, cpu);
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
}

/**
 * msm_spm_set_vdd(): Set core voltage
 * @cpu: core id
 * @vlevel: Encoded PMIC data.
 *
 * Return: 0 on success or -(ERRNO) on failure.
 */
int msm_spm_set_vdd(unsigned int cpu, unsigned int vlevel)
{
	struct msm_spm_vdd_info info;
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	int ret;

	ret = msm_spm_get(dev);
	if (ret)
		return ret;

	info.vctl_dev = dev;
	info.vlevel = vlevel;
	info.err = -ENXIO;

	ret = smp_call_function_any(&dev->mask, msm_spm_smp_set_vdd, &info,
					true);
	msm_spm_put(dev);
	return ret ? ret : info.err;
}
EXPORT_SYMBOL(msm_spm_set_vdd);

/**
 * msm_spm_get_vdd(): Get core voltage
 * @cpu: core id
 * @return: Returns encoded PMIC data.
 */
int msm_spm_get_vdd(unsigned int cpu)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_get_vdd(&dev->reg_data);
		if (ret <= 0)
			ret = msm_spm_fault_locked(dev, ret ? ret : -EIO);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}
EXPORT_SYMBOL(msm_spm_get_vdd);

static void msm_spm_config_q2s(struct msm_spm_device *dev, unsigned int mode)
{
	uint32_t spm_legacy_mode = 0;
	uint32_t qchannel_ignore = 0;
	uint32_t val = 0;

	if (!dev->q2s_reg)
		return;

	switch (mode) {
	case MSM_SPM_MODE_DISABLED:
	case MSM_SPM_MODE_CLOCK_GATING:
		qchannel_ignore = !dev->use_qchannel_for_wfi;
		spm_legacy_mode = 0;
		break;
	case MSM_SPM_MODE_RETENTION:
		qchannel_ignore = 0;
		spm_legacy_mode = 0;
		break;
	case MSM_SPM_MODE_GDHS:
	case MSM_SPM_MODE_STANDALONE_POWER_COLLAPSE:
	case MSM_SPM_MODE_POWER_COLLAPSE:
		qchannel_ignore = dev->qchannel_ignore;
		spm_legacy_mode = 1;
		break;
	default:
		break;
	}

	val = spm_legacy_mode << 2 | qchannel_ignore << 1;
	__raw_writel(val, dev->q2s_reg);
	mb(); /* Ensure flush */
}

static void msm_spm_config_hw_flush(struct msm_spm_device *dev,
		unsigned int mode)
{
	uint32_t val = 0;

	if (!dev->flush_base_addr)
		return;

	switch (mode) {
	case MSM_SPM_MODE_FASTPC:
	case MSM_SPM_MODE_STANDALONE_POWER_COLLAPSE:
	case MSM_SPM_MODE_POWER_COLLAPSE:
		val = BIT(0);
		break;
	default:
		break;
	}

	__raw_writel(val, dev->flush_base_addr);
}

static void msm_spm_config_slpreq(struct msm_spm_device *dev,
		unsigned int mode)
{
	uint32_t val = 0;

	if (!dev->slpreq_base_addr)
		return;

	switch (mode) {
	case MSM_SPM_MODE_FASTPC:
	case MSM_SPM_MODE_GDHS:
	case MSM_SPM_MODE_STANDALONE_POWER_COLLAPSE:
	case MSM_SPM_MODE_POWER_COLLAPSE:
		val = BIT(4);
		break;
	default:
		break;
	}

	val = (__raw_readl(dev->slpreq_base_addr) & ~BIT(4)) | val;
	__raw_writel(val, dev->slpreq_base_addr);
}

static int _msm_spm_dev_set_low_power_mode(struct msm_spm_device *dev,
		unsigned int mode, bool notify_rpm, bool set_spm_enable)
{
	uint32_t i;
	int ret = -EINVAL;
	uint32_t ctl = 0;

	if (!dev) {
		pr_err("dev is NULL\n");
		return -ENODEV;
	}

	if (!dev->initialized)
		return -ENXIO;

	if (!dev->num_modes)
		return mode == MSM_SPM_MODE_DISABLED ? 0 : -EOPNOTSUPP;

	if (mode == MSM_SPM_MODE_DISABLED && set_spm_enable) {
		ret = msm_spm_drv_set_spm_enable(&dev->reg_data, false);
	} else {
		for (i = 0; i < dev->num_modes; i++) {
			if (dev->modes[i].mode != mode)
				continue;

			ctl = dev->modes[i].ctl;
			if (!dev->allow_rpm_hs && notify_rpm)
				ctl &= ~BIT(SLP_CMD_BIT);

			break;
		}
		if (i == dev->num_modes)
			return -EINVAL;
		if (set_spm_enable) {
			ret = msm_spm_drv_set_spm_enable(&dev->reg_data, true);
			if (ret)
				return msm_spm_fault_locked(dev, ret);
		}
		ret = msm_spm_drv_set_low_power_mode(&dev->reg_data, ctl);
	}

	if (ret)
		return msm_spm_fault_locked(dev, ret);
	msm_spm_config_q2s(dev, mode);
	msm_spm_config_hw_flush(dev, mode);
	msm_spm_config_slpreq(dev, mode);

	return ret;
}

static int msm_spm_dev_set_low_power_mode(struct msm_spm_device *dev,
		unsigned int mode, bool notify_rpm, bool set_spm_enable)
{
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret)
		ret = _msm_spm_dev_set_low_power_mode(dev, mode, notify_rpm,
						    set_spm_enable);
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

static void msm_spm_free_program(struct msm_spm_device *dev)
{
	kfree(dev->modes);
	dev->modes = NULL;
	dev->num_modes = 0;
	kfree(dev->reg_data.reg_seq_entry_shadow);
	dev->reg_data.reg_seq_entry_shadow = NULL;
	dev->reg_data.seq_entries = 0;
}

static int msm_spm_dev_init(struct msm_spm_device *dev,
		struct msm_spm_platform_data *data)
{
	unsigned int i;
	int ret = -ENOMEM;
	uint32_t offset = 0;

	if (dev->initialized)
		return -EBUSY;
	dev->cpu_vdd = VDD_DEFAULT;
	dev->num_modes = data->num_modes;
	dev->modes = kmalloc_array(
			dev->num_modes, sizeof(struct msm_spm_power_modes),
			GFP_KERNEL);

	if (!dev->modes)
		goto spm_failed_malloc;

	ret = msm_spm_drv_init(&dev->reg_data, data);

	if (ret)
		goto spm_failed_init;

	for (i = 0; i < dev->num_modes; i++) {
		if (offset > 0x1ff) {
			ret = -E2BIG;
			goto spm_failed_init;
		}

		/* Default offset is 0 and gets updated as we write more
		 * sequences into SPM
		 */
		dev->modes[i].ctl = data->modes[i].ctl | (offset << 4);
		ret = msm_spm_drv_write_seq_data(&dev->reg_data,
			data->modes[i].cmd, data->modes[i].cmd_len, &offset);
		if (ret < 0)
			goto spm_failed_init;

		dev->modes[i].mode = data->modes[i].mode;
	}

	msm_spm_drv_reinit(&dev->reg_data, dev->num_modes ? true : false);

	dev->initialized = true;

	return 0;

spm_failed_init:
	msm_spm_free_program(dev);
spm_failed_malloc:
	return ret;
}

/**
 * msm_spm_turn_on_cpu_rail(): Power on cpu rail before turning on core
 * @node: The SPM node that controls the voltage for the CPU
 * @val: The value to be set on the rail
 * @cpu: The cpu for this with rail is being powered on
 */
int msm_spm_turn_on_cpu_rail(struct device_node *vctl_node,
		unsigned int val, int cpu, int vctl_offset)
{
	struct msm_spm_device *dev;
	unsigned long flags;
	int ret;

	(void)vctl_node;
	(void)val;
	(void)vctl_offset;
	/* This old out-of-band bootstrap path had no hardware completion
	 * readback and raced the registered gang owner. Only an existing,
	 * confirmed SAW transition can establish an already-powered rail.
	 */
	if (cpu < 0 || (unsigned int)cpu >= nr_cpu_ids || !cpu_possible(cpu))
		return -EINVAL;
	dev = msm_spm_vctl_device(cpu);
	ret = msm_spm_get(dev);
	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret)
		ret = dev->cpu_vdd == VDD_UNKNOWN ? -EIO :
			dev->cpu_vdd == VDD_DEFAULT ? -EAGAIN : 0;
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_turn_on_cpu_rail);

void msm_spm_reinit(void)
{
	struct msm_spm_device *dev;
	unsigned long flags;
	unsigned int cpu;

	for_each_possible_cpu(cpu) {
		dev = &per_cpu(msm_cpu_spm_device, cpu);
		if (msm_spm_get(dev))
			continue;
		raw_spin_lock_irqsave(&dev->lock, flags);
		if (!msm_spm_state_locked(dev))
			msm_spm_drv_reinit(&dev->reg_data, true);
		raw_spin_unlock_irqrestore(&dev->lock, flags);
		msm_spm_put(dev);
	}
}

EXPORT_SYMBOL(msm_spm_reinit);

/*
 * msm_spm_is_mode_avail() - Specifies if a mode is available for the cpu
 * It should only be used to decide a mode before lpm driver is probed.
 * @mode: SPM LPM mode to be selected
 */
bool msm_spm_is_mode_avail(unsigned int mode)
{
	struct msm_spm_device *dev = this_cpu_ptr(&msm_cpu_spm_device);
	unsigned long flags;
	bool available = false;
	int i;

	if (msm_spm_get(dev))
		return false;
	raw_spin_lock_irqsave(&dev->lock, flags);
	if (!msm_spm_state_locked(dev))
		for (i = 0; i < dev->num_modes; i++)
			if (dev->modes[i].mode == mode) {
				available = true;
				break;
			}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return available;
}


/**
 * msm_spm_is_avs_enabled() - Functions returns 1 if AVS is enabled and
 *			      0 if it is not.
 * @cpu: specifies cpu's avs should be read
 *
 * Returns errno in case of failure or AVS enable state otherwise
 */
int msm_spm_is_avs_enabled(unsigned int cpu)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_get_avs_enable(&dev->reg_data);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_is_avs_enabled);

/**
 * msm_spm_avs_enable() - Enables AVS on the SAW that controls this cpu's
 *			  voltage.
 * @cpu: specifies which cpu's avs should be enabled
 *
 * Returns errno in case of failure or 0 if successful
 */
int msm_spm_avs_enable(unsigned int cpu)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_avs_enable(&dev->reg_data, true);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_avs_enable);

/**
 * msm_spm_avs_disable() - Disables AVS on the SAW that controls this cpu's
 *			   voltage.
 * @cpu: specifies which cpu's avs should be enabled
 *
 * Returns errno in case of failure or 0 if successful
 */
int msm_spm_avs_disable(unsigned int cpu)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_avs_enable(&dev->reg_data, false);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_avs_disable);

/**
 * msm_spm_avs_set_limit() - Set maximum and minimum AVS limits on the
 *			     SAW that controls this cpu's voltage.
 * @cpu: specify which cpu's avs should be configured
 * @min_lvl: specifies the minimum PMIC output voltage control register
 *		value that may be sent to the PMIC
 * @max_lvl: specifies the maximum PMIC output voltage control register
 *		value that may be sent to the PMIC
 * Returns errno in case of failure or 0 if successful
 */
int msm_spm_avs_set_limit(unsigned int cpu,
		uint32_t min_lvl, uint32_t max_lvl)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_avs_limit(&dev->reg_data, min_lvl, max_lvl);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_avs_set_limit);

/**
 * msm_spm_avs_enable_irq() - Enable an AVS interrupt
 * @cpu: specifies which CPU's AVS should be configured
 * @irq: specifies which interrupt to enable
 *
 * Returns errno in case of failure or 0 if successful.
 */
int msm_spm_avs_enable_irq(unsigned int cpu, enum msm_spm_avs_irq irq)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_avs_irq_enable(&dev->reg_data, irq, true);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_avs_enable_irq);

/**
 * msm_spm_avs_disable_irq() - Disable an AVS interrupt
 * @cpu: specifies which CPU's AVS should be configured
 * @irq: specifies which interrupt to disable
 *
 * Returns errno in case of failure or 0 if successful.
 */
int msm_spm_avs_disable_irq(unsigned int cpu, enum msm_spm_avs_irq irq)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_avs_irq_enable(&dev->reg_data, irq, false);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_avs_disable_irq);

/**
 * msm_spm_avs_clear_irq() - Clear a latched AVS interrupt
 * @cpu: specifies which CPU's AVS should be configured
 * @irq: specifies which interrupt to clear
 *
 * Returns errno in case of failure or 0 if successful.
 */
int msm_spm_avs_clear_irq(unsigned int cpu, enum msm_spm_avs_irq irq)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_avs_clear_irq(&dev->reg_data, irq);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_avs_clear_irq);

/**
 * msm_spm_set_low_power_mode() - Configure SPM start address for low power mode
 * @mode: SPM LPM mode to enter
 * @notify_rpm: Notify RPM in this mode
 */
int msm_spm_set_low_power_mode(unsigned int mode, bool notify_rpm)
{
	struct msm_spm_device *dev = this_cpu_ptr(&msm_cpu_spm_device);

	return msm_spm_dev_set_low_power_mode(dev, mode, notify_rpm, true);
}
EXPORT_SYMBOL(msm_spm_set_low_power_mode);

void msm_spm_set_rpm_hs(bool allow_rpm_hs)
{
	struct msm_spm_device *dev = this_cpu_ptr(&msm_cpu_spm_device);
	unsigned long flags;

	if (msm_spm_get(dev))
		return;
	raw_spin_lock_irqsave(&dev->lock, flags);
	if (!msm_spm_state_locked(dev))
		dev->allow_rpm_hs = allow_rpm_hs;
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
}

EXPORT_SYMBOL(msm_spm_set_rpm_hs);

int msm_spm_config_low_power_mode_addr(struct msm_spm_device *dev,
		unsigned int mode, bool notify_rpm)
{
	return msm_spm_dev_set_low_power_mode(dev, mode, notify_rpm, false);
}

/**
 * msm_spm_init(): Board initalization function
 * @data: platform specific SPM register configuration data
 * @nr_devs: Number of SPM devices being initialized
 */
int __init msm_spm_init(struct msm_spm_platform_data *data, int nr_devs)
{
	unsigned int cpu;
	int ret = 0;

	if ((nr_devs < num_possible_cpus()) || !data)
		return -EINVAL;

	for_each_possible_cpu(cpu) {
		struct msm_spm_device *dev = &per_cpu(msm_cpu_spm_device, cpu);

		if (dev->initialized || dev->owner)
			return -EBUSY;
		msm_spm_lifetime_init(dev);
		ret = msm_spm_dev_init(dev, &data[cpu]);
		if (ret < 0) {
			pr_warn("%s():failed CPU:%u ret:%d\n", __func__,
					cpu, ret);
			break;
		}
		smp_store_release(&dev->published, true);
	}

	return ret;
}

struct msm_spm_device *msm_spm_get_device_by_name(const char *name)
{
	struct msm_spm_device *dev, *found = ERR_PTR(-ENODEV);

	if (!name)
		return ERR_PTR(-EINVAL);
	mutex_lock(&spm_owner_lock);
	list_for_each_entry(dev, &spm_list, list)
		if (dev->name && !strcmp(dev->name, name)) {
			found = dev->retired ? ERR_PTR(-ESHUTDOWN) : dev;
			break;
		}
	mutex_unlock(&spm_owner_lock);
	return found;
}


int msm_spm_config_low_power_mode(struct msm_spm_device *dev,
		unsigned int mode, bool notify_rpm)
{
	return msm_spm_dev_set_low_power_mode(dev, mode, notify_rpm, true);
}
#ifdef CONFIG_MSM_L2_SPM

/**
 * msm_spm_apcs_set_phase(): Set number of SMPS phases.
 * @cpu: cpu which is requesting the change in number of phases.
 * @phase_cnt: Number of phases to be set active
 */
int msm_spm_apcs_set_phase(int cpu, unsigned int phase_cnt)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_pmic_data(&dev->reg_data,
				MSM_SPM_PMIC_PHASE_PORT, phase_cnt);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_apcs_set_phase);

/** msm_spm_enable_fts_lpm() : Enable FTS to switch to low power
 *                             when the cores are in low power modes
 * @cpu: cpu that is entering low power mode.
 * @mode: The mode configuration for FTS
 */
int msm_spm_enable_fts_lpm(int cpu, uint32_t mode)
{
	struct msm_spm_device *dev = msm_spm_vctl_device(cpu);
	unsigned long flags;
	int ret = msm_spm_get(dev);

	if (ret)
		return ret;
	raw_spin_lock_irqsave(&dev->lock, flags);
	ret = msm_spm_state_locked(dev);
	if (!ret) {
		ret = msm_spm_drv_set_pmic_data(&dev->reg_data,
				MSM_SPM_PMIC_PFM_PORT, mode);
		if (ret < 0)
			ret = msm_spm_fault_locked(dev, ret);
	}
	raw_spin_unlock_irqrestore(&dev->lock, flags);
	msm_spm_put(dev);
	return ret;
}

EXPORT_SYMBOL(msm_spm_enable_fts_lpm);

#endif

static int get_cpu_id(struct device_node *node)
{
	struct device_node *cpu_node, *possible;
	u32 cpu;
	int found = -EINVAL;

	cpu_node = of_parse_phandle(node, "qcom,cpu", 0);
	if (!cpu_node)
		return num_possible_cpus();
	for_each_possible_cpu(cpu) {
		possible = of_get_cpu_node(cpu, NULL);
		if (possible == cpu_node)
			found = cpu;
		of_node_put(possible);
		if (found >= 0)
			break;
	}
	of_node_put(cpu_node);
	return found;
}


static struct msm_spm_device *msm_spm_get_device(struct platform_device *pdev)
{
	struct msm_spm_device *dev = NULL;
	const char *val = NULL;
	char *key = "qcom,name";
	int cpu = get_cpu_id(pdev->dev.of_node);

	if ((cpu >= 0) && cpu < num_possible_cpus())
		dev = &per_cpu(msm_cpu_spm_device, cpu);
	else if (cpu == num_possible_cpus()) {
		dev = kzalloc(sizeof(struct msm_spm_device), GFP_KERNEL);
		if (!dev)
			return ERR_PTR(-ENOMEM);
	}

	if (!dev)
		return NULL;
	if (dev->owner)
		return ERR_PTR(dev->retired ? -ESHUTDOWN : -EBUSY);
	if (dev->initialized)
		return ERR_PTR(-EBUSY);

	if (of_property_read_string(pdev->dev.of_node, key, &val)) {
		pr_err("%s(): Cannot find a required node key:%s\n",
				__func__, key);
		if (cpu == num_possible_cpus())
			kfree(dev);
		return ERR_PTR(-EINVAL);
	}
	dev->name = val;

	return dev;
}

static void get_cpumask(struct device_node *node, struct cpumask *mask)
{
	unsigned int cpu;
	int idx = 0;
	struct device_node *cpu_node, *possible;

	cpumask_clear(mask);
	while ((cpu_node = of_parse_phandle(node, "qcom,cpu-vctl-list", idx++))) {
		for_each_possible_cpu(cpu) {
			possible = of_get_cpu_node(cpu, NULL);
			if (possible == cpu_node)
				cpumask_set_cpu(cpu, mask);
			of_node_put(possible);
		}
		of_node_put(cpu_node);
	}
}

static int msm_spm_claim(struct platform_device *pdev,
		struct msm_spm_device *dev, struct resource *resource)
{
	struct msm_spm_device *old;
	int ret = 0;

	mutex_lock(&spm_owner_lock);
	list_for_each_entry(old, &spm_claims, claim) {
		if (old->owner->of_node == pdev->dev.of_node ||
		    (resource->start <= old->resource.end &&
		     old->resource.start <= resource->end) ||
		    cpumask_intersects(&dev->mask, &old->mask) ||
		    !strcmp(old->name, dev->name)) {
			ret = old->retired ? -ESHUTDOWN : -EBUSY;
			goto out;
		}
	}
	msm_spm_lifetime_init(dev);
	dev->resource = *resource;
	dev->owner = get_device(&pdev->dev);
	__module_get(THIS_MODULE);
	list_add_tail(&dev->claim, &spm_claims);
out:
	mutex_unlock(&spm_owner_lock);
	return ret;
}


static int msm_spm_dev_probe(struct platform_device *pdev)
{
	int ret = 0;
	int cpu = 0;
	int i = 0;
	struct device_node *node = pdev->dev.of_node;
	struct device_node *n = NULL;
	struct msm_spm_platform_data spm_data;
	char *key = NULL;
	uint32_t val = 0;
	struct msm_spm_seq_entry modes[MSM_SPM_MODE_NR];
	int len = 0;
	struct msm_spm_device *dev = NULL;
	struct resource *res = NULL;
	struct resource *saw_res;
	uint32_t mode_count = 0;

	struct spm_of {
		char *key;
		uint32_t id;
	};

	struct spm_of spm_of_data[] = {
		{"qcom,saw2-cfg", MSM_SPM_REG_SAW_CFG},
		{"qcom,saw2-avs-ctl", MSM_SPM_REG_SAW_AVS_CTL},
		{"qcom,saw2-avs-hysteresis", MSM_SPM_REG_SAW_AVS_HYSTERESIS},
		{"qcom,saw2-avs-limit", MSM_SPM_REG_SAW_AVS_LIMIT},
		{"qcom,saw2-avs-dly", MSM_SPM_REG_SAW_AVS_DLY},
		{"qcom,saw2-spm-dly", MSM_SPM_REG_SAW_SPM_DLY},
		{"qcom,saw2-spm-ctl", MSM_SPM_REG_SAW_SPM_CTL},
		{"qcom,saw2-pmic-data0", MSM_SPM_REG_SAW_PMIC_DATA_0},
		{"qcom,saw2-pmic-data1", MSM_SPM_REG_SAW_PMIC_DATA_1},
		{"qcom,saw2-pmic-data2", MSM_SPM_REG_SAW_PMIC_DATA_2},
		{"qcom,saw2-pmic-data3", MSM_SPM_REG_SAW_PMIC_DATA_3},
		{"qcom,saw2-pmic-data4", MSM_SPM_REG_SAW_PMIC_DATA_4},
		{"qcom,saw2-pmic-data5", MSM_SPM_REG_SAW_PMIC_DATA_5},
		{"qcom,saw2-pmic-data6", MSM_SPM_REG_SAW_PMIC_DATA_6},
		{"qcom,saw2-pmic-data7", MSM_SPM_REG_SAW_PMIC_DATA_7},
	};

	struct mode_of {
		char *key;
		uint32_t id;
	};

	struct mode_of mode_of_data[] = {
		{"qcom,saw2-spm-cmd-wfi", MSM_SPM_MODE_CLOCK_GATING},
		{"qcom,saw2-spm-cmd-ret", MSM_SPM_MODE_RETENTION},
		{"qcom,saw2-spm-cmd-gdhs", MSM_SPM_MODE_GDHS},
		{"qcom,saw2-spm-cmd-spc",
				MSM_SPM_MODE_STANDALONE_POWER_COLLAPSE},
		{"qcom,saw2-spm-cmd-pc", MSM_SPM_MODE_POWER_COLLAPSE},
		{"qcom,saw2-spm-cmd-fpc", MSM_SPM_MODE_FASTPC},
	};

	mutex_lock(&spm_probe_lock);
	dev = msm_spm_get_device(pdev);
	if (IS_ERR(dev)) {
		ret = PTR_ERR(dev);
		goto unlock;
	}
	if (!dev) {
		/*
		 * For partial goods support some CPUs might not be available
		 * in which case, shouldn't throw an error
		 */
		goto unlock;
	}
	get_cpumask(node, &dev->mask);

	memset(&spm_data, 0, sizeof(struct msm_spm_platform_data));
	memset(modes, 0, sizeof(modes));

	key = "qcom,saw2-ver-reg";
	ret = of_property_read_u32(node, key, &val);
	if (ret)
		goto fail;
	spm_data.ver_reg = val;

	key = "qcom,vctl-timeout-us";
	ret = of_property_read_u32(node, key, &val);
	if (!ret)
		spm_data.vctl_timeout_us = val;

	/* SAW start address */
	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res || (res->start & 3) || res->end < res->start ||
	    resource_size(res) < sizeof(u32) ||
	    resource_size(res) > U32_MAX ||
	    spm_data.ver_reg > resource_size(res) - sizeof(u32)) {
		ret = -EFAULT;
		goto fail;
	}
	saw_res = res;

	spm_data.reg_base_addr = devm_ioremap(&pdev->dev, res->start,
					resource_size(res));
	if (!spm_data.reg_base_addr) {
		ret = -ENOMEM;
		goto fail;
	}
	spm_data.reg_size = resource_size(res);

	spm_data.vctl_port = -1;
	spm_data.vctl_port_ub = -1;
	spm_data.phase_port = -1;
	spm_data.pfm_port = -1;

	key = "qcom,vctl-port";
	of_property_read_u32(node, key, &spm_data.vctl_port);

	key = "qcom,vctl-port-ub";
	of_property_read_u32(node, key, &spm_data.vctl_port_ub);

	key = "qcom,phase-port";
	of_property_read_u32(node, key, &spm_data.phase_port);

	key = "qcom,pfm-port";
	of_property_read_u32(node, key, &spm_data.pfm_port);

	/* Q2S (QChannel-2-SPM) register */
	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "q2s");
	if (res) {
		dev->q2s_reg = devm_ioremap(&pdev->dev, res->start,
						resource_size(res));
		if (!dev->q2s_reg) {
			pr_err("%s(): Unable to iomap Q2S register\n",
					__func__);
			ret = -EADDRNOTAVAIL;
			goto fail;
		}
	}

	key = "qcom,use-qchannel-for-pc";
	dev->qchannel_ignore = !of_property_read_bool(node, key);

	key = "qcom,use-spm-clock-gating";
	dev->use_spm_clk_gating = of_property_read_bool(node, key);

	key = "qcom,use-qchannel-for-wfi";
	dev->use_qchannel_for_wfi = of_property_read_bool(node, key);

	/* HW flush address */
	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "hw-flush");
	if (res) {
		dev->flush_base_addr = devm_ioremap_resource(&pdev->dev, res);
		if (IS_ERR(dev->flush_base_addr)) {
			ret = PTR_ERR(dev->flush_base_addr);
			pr_err("%s(): Unable to iomap hw flush register %d\n",
					__func__, ret);
			goto fail;
		}
	}

	/* Sleep req address */
	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "slpreq");
	if (res) {
		dev->slpreq_base_addr = devm_ioremap(&pdev->dev, res->start,
					resource_size(res));
		if (!dev->slpreq_base_addr) {
			ret = -ENOMEM;
			pr_err("%s(): Unable to iomap slpreq register\n",
					__func__);
			ret = -EADDRNOTAVAIL;
			goto fail;
		}
	}

	/*
	 * At system boot, cpus and or clusters can remain in reset. CCI SPM
	 * will not be triggered unless SPM_LEGACY_MODE bit is set for the
	 * cluster in reset. Initialize q2s registers and set the
	 * SPM_LEGACY_MODE bit.
	 */
	ret = msm_spm_claim(pdev, dev, saw_res);
	if (ret)
		goto fail;
	ret = msm_spm_drv_reg_init(&dev->reg_data, &spm_data);
	if (ret)
		goto fail;
	msm_spm_config_q2s(dev, MSM_SPM_MODE_POWER_COLLAPSE);

	for (i = 0; i < ARRAY_SIZE(spm_of_data); i++) {
		ret = of_property_read_u32(node, spm_of_data[i].key, &val);
		if (ret)
			continue;
		msm_spm_drv_upd_reg_shadow(&dev->reg_data, spm_of_data[i].id,
				val);
	}

	for_each_child_of_node(node, n) {
		const char *name;
		bool bit_set;
		int sync;

		if (!n->name)
			continue;

		ret = of_property_read_string(n, "qcom,label", &name);
		if (ret)
			continue;

		for (i = 0; i < ARRAY_SIZE(mode_of_data); i++)
			if (!strcmp(name, mode_of_data[i].key))
				break;

		if (i == ARRAY_SIZE(mode_of_data)) {
			pr_err("Mode name invalid %s\n", name);
			ret = -EINVAL;
			of_node_put(n);
			goto fail;
		}
		if (mode_count >= ARRAY_SIZE(modes)) {
			ret = -E2BIG;
			of_node_put(n);
			goto fail;
		}

		modes[mode_count].mode = mode_of_data[i].id;
		modes[mode_count].cmd =
			(uint8_t *)of_get_property(n, "qcom,sequence", &len);
		if (!modes[mode_count].cmd || len <= 0) {
			pr_err("cmd is empty\n");
			continue;
		}
		modes[mode_count].cmd_len = len;

		bit_set = of_property_read_bool(n, "qcom,pc_mode");
		modes[mode_count].ctl |= bit_set ? BIT(PC_MODE_BIT) : 0;

		bit_set = of_property_read_bool(n, "qcom,ret_mode");
		modes[mode_count].ctl |= bit_set ? BIT(RET_MODE_BIT) : 0;

		bit_set = of_property_read_bool(n, "qcom,slp_cmd_mode");
		modes[mode_count].ctl |= bit_set ? BIT(SLP_CMD_BIT) : 0;

		bit_set = of_property_read_bool(n, "qcom,isar");
		modes[mode_count].ctl |= bit_set ? BIT(ISAR_BIT) : 0;

		bit_set = of_property_read_bool(n, "qcom,spm_en");
		modes[mode_count].ctl |= bit_set ? BIT(SPM_EN_BIT) : 0;

		ret = of_property_read_u32(n, "qcom,event_sync", &sync);
		if (!ret)
			modes[mode_count].ctl |= sync << EVENT_SYNC_BIT;

		mode_count++;
	}

	spm_data.modes = modes;
	spm_data.num_modes = mode_count;

	key = "qcom,supports-rpm-hs";
	dev->allow_rpm_hs = of_property_read_bool(pdev->dev.of_node, key);

	ret = msm_spm_dev_init(dev, &spm_data);
	if (ret) {
		pr_err("SPM modes programming is not available from HLOS\n");
		goto fail;
	}

	cpu = get_cpu_id(pdev->dev.of_node);

	/* For CPUs that are online, the SPM has to be programmed for
	 * clockgating mode to ensure that it can use SPM for entering these
	 * low power modes.
	 */
	get_online_cpus();
	if (spm_data.num_modes && cpu >= 0 && cpu < nr_cpu_ids && cpu_online(cpu))
		ret = _msm_spm_dev_set_low_power_mode(dev,
				MSM_SPM_MODE_CLOCK_GATING, false, true);
	put_online_cpus();
	if (ret)
		goto fail;

	/* Publish only after every fallible initial operation. Raw descriptors
	 * are permanent; retirement rejects access before devres is unmapped.
	 */
	mutex_lock(&spm_owner_lock);
	list_add(&dev->list, &spm_list);
	smp_store_release(&dev->published, true);
	for_each_cpu(cpu, &dev->mask)
		WRITE_ONCE(per_cpu(cpu_vctl_device, cpu), dev);
	platform_set_drvdata(pdev, dev);
	mutex_unlock(&spm_owner_lock);
	ret = 0;
	goto unlock;

fail:
	if (dev->owner) {
		msm_spm_retire(dev, ret);
		msm_spm_free_program(dev);
		for_each_cpu(cpu, &dev->mask)
			WRITE_ONCE(per_cpu(cpu_vctl_device, cpu), dev);
	} else if (get_cpu_id(node) == num_possible_cpus()) {
		kfree(dev);
	}

	pr_err("%s: CPU%d SPM device probe failed: %d\n", __func__, cpu, ret);

unlock:
	mutex_unlock(&spm_probe_lock);
	return ret;
}

static int msm_spm_dev_remove(struct platform_device *pdev)
{
	struct msm_spm_device *dev = platform_get_drvdata(pdev);

	if (!dev)
		return 0;
	/* No new API operation can start after the flag; queued operations
	 * recheck under the hardware lock. Drain the IPI sender as well as
	 * its target before the platform core releases managed mappings.
	 */
	msm_spm_retire(dev, -ESHUTDOWN);
	msm_spm_free_program(dev);
	platform_set_drvdata(pdev, NULL);
	return 0;
}

static const struct of_device_id msm_spm_match_table[] = {
	{.compatible = "qcom,spm-v2"},
	{},
};

static struct platform_driver msm_spm_device_driver = {
	.probe = msm_spm_dev_probe,
	.remove = msm_spm_dev_remove,
	.driver = {
		.name = "spm-v2",
		.suppress_bind_attrs = true,
		.of_match_table = msm_spm_match_table,
	},
};

/**
 * msm_spm_device_init(): Device tree initialization function
 */
int __init msm_spm_device_init(void)
{
	static bool registered;

	if (registered)
		return 0;
	registered = true;
	return platform_driver_register(&msm_spm_device_driver);
}
arch_initcall(msm_spm_device_init);
