// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2011-2016, The Linux Foundation. All rights reserved.
 * Copyright (c) 2016 Sony Mobile Communications Inc.
 *
 * Qualcomm Subsystem Power Manager (SPM) / SAW3 voltage & AVS backend.
 */

#define pr_fmt(fmt) "qcom-spm: " fmt

#include <linux/cpu.h>
#include <linux/cpumask.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/spinlock.h>
#include <soc/qcom/spm.h>

/*
 * SAW3 hardware register offsets for MSM8996 (matches Sony 4.4 msm_spm_reg_offsets_saw2_v3_0).
 */
enum saw3_reg {
	SAW3_SECURE		= 0x00,
	SAW3_ID			= 0x04,
	SAW3_CFG		= 0x08,
	SAW3_SPM_STS		= 0x0C,
	SAW3_AVS_STS		= 0x10,
	SAW3_PMIC_STS		= 0x14,
	SAW3_RST		= 0x18,
	SAW3_VCTL		= 0x1C,
	SAW3_AVS_CTL		= 0x20,
	SAW3_AVS_LIMIT		= 0x24,
	SAW3_AVS_DLY		= 0x28,
	SAW3_AVS_HYSTERESIS	= 0x2C,
	SAW3_SPM_CTL		= 0x30,
	SAW3_SPM_DLY		= 0x34,
	SAW3_STS2		= 0x38,
	SAW3_SPM_PMIC_DATA_0	= 0x40,
	SAW3_SPM_PMIC_DATA_1	= 0x44,
	SAW3_SPM_PMIC_DATA_2	= 0x48,
	SAW3_SPM_PMIC_DATA_3	= 0x4C,
	SAW3_SEQ_ENTRY		= 0x400,
	SAW3_VERSION		= 0xFD0,
};

#define SAW3_ID_PMIC_ARB_INTF_MASK	BIT(2)
#define SAW3_VCTL_DATA_MASK		0xFF
#define SAW3_VCTL_CLEAR_MASK		0x700FF
#define SAW3_AVS_CTL_EN_MASK		0x1
#define SAW3_AVS_CTL_IRQ_MIN_MASK	0x2
#define SAW3_AVS_CTL_IRQ_MAX_MASK	0x4
#define SAW3_AVS_CTL_CLEAR_MASK		0x7efc00
#define SAW3_AVS_CTL_TGGL_MASK		0x8000000
#define SAW3_AVS_CTL_VLVL_MIN		0x4
#define SAW3_AVS_CTL_VLVL_MAX		0x3f
#define SAW3_PMIC_STS_STATE_MASK	0x30000
#define SAW3_PMIC_STATE_IDLE		0x0
#define SAW3_VCTL_DEFAULT_TIMEOUT_US	500

struct qcom_spm_drvdata {
	struct device *dev;
	struct device_node *of_node;
	struct regmap *regmap;
	spinlock_t lock;
	bool state_uncertain;
	bool pmic_arb_present;
	u32 major;
	u32 minor;
	u32 ver_reg;
	u32 vctl_port;
	u32 vctl_timeout_us;
	struct cpumask cpus;
};

static struct qcom_spm_drvdata *spm_global_drvdata;
static DEFINE_PER_CPU(struct qcom_spm_drvdata *, cpu_spm_drvdata);

static struct qcom_spm_drvdata *msm_spm_get_drvdata(unsigned int cpu)
{
	if (cpu < nr_cpu_ids)
		return per_cpu(cpu_spm_drvdata, cpu);

	return NULL;
}

/**
 * msm_spm_probe_done() - Check if SPM devices have completed probe for all CPUs.
 *
 * Return: 0 if ready, -EPROBE_DEFER if not yet probed, or negative errno on failure.
 */
int msm_spm_probe_done(void)
{
	struct qcom_spm_drvdata *drv;
	int cpu;

	if (!spm_global_drvdata)
		return -EPROBE_DEFER;

	for_each_possible_cpu(cpu) {
		drv = per_cpu(cpu_spm_drvdata, cpu);
		if (!drv)
			return -EPROBE_DEFER;
		if (IS_ERR(drv))
			return PTR_ERR(drv);
	}

	return 0;
}
EXPORT_SYMBOL_GPL(msm_spm_probe_done);

/**
 * msm_spm_is_saw_node() - Verify that a DT phandle matches the active SPM/SAW3 node.
 * @np: device_node pointer to verify
 */
bool msm_spm_is_saw_node(const struct device_node *np)
{
	if (!np || !spm_global_drvdata || IS_ERR(spm_global_drvdata))
		return false;

	return spm_global_drvdata->of_node == np;
}
EXPORT_SYMBOL_GPL(msm_spm_is_saw_node);

/**
 * msm_spm_set_vdd() - Set core voltage via SAW3
 * @cpu: CPU core number
 * @vlevel: 8-bit encoded PMIC voltage selector
 *
 * Enforces the Sony/Qualcomm MSM8996 msm-spm.c sequence:
 * 1. Verify PMIC arbiter presence and port 0 constraint (PMIC_STS updates only on port 0).
 * 2. Read AVS state; if enabled, validate 6-bit bounds [4..0x3F] and clear AVS toggle bit.
 * 3. Write 1 to SAW3_RST to kick the sequencer state machine back to idle.
 * 4. Write selector to SAW3_VCTL and sync to SAW3_SPM_PMIC_DATA_3, followed by mb().
 * 5. Poll SAW3_PMIC_STS with udelay(1) before each read: verify both PMIC FSM idle
 *    ((pmic_sts & 0x30000) == 0) and acknowledged voltage byte ((pmic_sts & 0xFF) == vlevel).
 * 6. On failure: do NOT re-enable AVS with unconfirmed rail state; latch state_uncertain
 *    and fail closed.
 * 7. On confirmed success: update AVS limits and re-enable AVS toggle.
 */
int msm_spm_set_vdd(unsigned int cpu, unsigned int vlevel)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	u32 vctl, data3, avs_ctl, pmic_sts = 0;
	unsigned long flags;
	bool avs_enabled;
	u32 timeout_us;
	int ret;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	if (!drv->pmic_arb_present)
		return -ENOSYS;

	/*
	 * VCTL_PORT must be 0 for SAW3_PMIC_STS to be updated by hardware.
	 */
	if (drv->vctl_port != 0 || vlevel > SAW3_VCTL_DATA_MASK)
		return -EINVAL;

	spin_lock_irqsave(&drv->lock, flags);

	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}

	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &avs_ctl);
	if (ret)
		goto fail_uncertain;

	ret = regmap_read(drv->regmap, SAW3_VCTL, &vctl);
	if (ret)
		goto fail_uncertain;

	ret = regmap_read(drv->regmap, SAW3_SPM_PMIC_DATA_3, &data3);
	if (ret)
		goto fail_uncertain;

	vctl = (vctl & ~SAW3_VCTL_CLEAR_MASK) | (vlevel & SAW3_VCTL_DATA_MASK) |
	       ((drv->vctl_port & 0x7) << 16);
	data3 = (data3 & ~SAW3_VCTL_CLEAR_MASK) | (vlevel & SAW3_VCTL_DATA_MASK) |
		((drv->vctl_port & 0x7) << 16);

	avs_enabled = !!(avs_ctl & SAW3_AVS_CTL_EN_MASK);

	/* AVS limit fields accept 6-bit selectors; reject before touching hardware. */
	if (avs_enabled &&
	    (vlevel < SAW3_AVS_CTL_VLVL_MIN || vlevel > SAW3_AVS_CTL_VLVL_MAX)) {
		ret = -ERANGE;
		goto out_unlock;
	}

	if (avs_enabled) {
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL,
				   avs_ctl & ~SAW3_AVS_CTL_TGGL_MASK);
		if (ret)
			goto fail_uncertain;
	}

	/* Kick the state machine back to idle */
	ret = regmap_write(drv->regmap, SAW3_RST, 1);
	if (ret)
		goto fail_uncertain;

	ret = regmap_write(drv->regmap, SAW3_VCTL, vctl);
	if (ret)
		goto fail_uncertain;

	ret = regmap_write(drv->regmap, SAW3_SPM_PMIC_DATA_3, data3);
	if (ret)
		goto fail_uncertain;

	/* Ensure VCTL and PMIC_DATA_3 writes drain before polling PMIC_STS */
	mb();

	timeout_us = drv->vctl_timeout_us;
	do {
		udelay(1);
		ret = regmap_read(drv->regmap, SAW3_PMIC_STS, &pmic_sts);
		if (ret)
			goto fail_uncertain;

		if (((pmic_sts & SAW3_PMIC_STS_STATE_MASK) == SAW3_PMIC_STATE_IDLE) &&
		    ((pmic_sts & SAW3_VCTL_DATA_MASK) == (vlevel & SAW3_VCTL_DATA_MASK)))
			break;
	} while (--timeout_us);

	if (!timeout_us) {
		ret = -ETIMEDOUT;
		dev_err(drv->dev,
			"SAW3 VCTL transaction timed out after %u us (sts=0x%08x, req=0x%02x)\n",
			drv->vctl_timeout_us, pmic_sts, vlevel);
		goto fail_uncertain;
	}

	if (avs_enabled) {
		u32 new_avs = (avs_ctl & ~SAW3_AVS_CTL_CLEAR_MASK) |
			      (((vlevel - 4) & 0x3f) << 10) |
			      ((vlevel & 0x3f) << 17);

		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, new_avs);
		if (ret)
			goto fail_uncertain;

		ret = regmap_write(drv->regmap, SAW3_AVS_CTL,
				   new_avs | SAW3_AVS_CTL_TGGL_MASK);
		if (ret)
			goto fail_uncertain;
		mb();
	}

	spin_unlock_irqrestore(&drv->lock, flags);
	return 0;

fail_uncertain:
	/*
	 * Do NOT re-enable AVS on a failed or timed-out voltage transition:
	 * the physical rail voltage is unconfirmed and restoring previous
	 * AVS limits can drive the rail out of bounds. Latch state_uncertain
	 * to block all subsequent voltage and AVS operations.
	 */
	drv->state_uncertain = true;
	dev_err(drv->dev,
		"SAW3 voltage transition to 0x%02x failed (%d); AVS kept disabled and further votes blocked\n",
		vlevel, ret);
out_unlock:
	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_set_vdd);

/**
 * msm_spm_get_vdd() - Read acknowledged PMIC voltage selector from SAW3
 * @cpu: CPU core number
 *
 * Safe to invoke from atomic context (e.g. CPU_CLUSTER_PM_ENTER in kryo-regulator).
 */
int msm_spm_get_vdd(unsigned int cpu)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 status = 0;
	u32 timeout_us;
	int ret;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}

	timeout_us = drv->vctl_timeout_us;
	do {
		ret = regmap_read(drv->regmap, SAW3_PMIC_STS, &status);
		if (ret) {
			drv->state_uncertain = true;
			spin_unlock_irqrestore(&drv->lock, flags);
			return ret;
		}
		if ((status & SAW3_PMIC_STS_STATE_MASK) == SAW3_PMIC_STATE_IDLE)
			break;
		udelay(1);
	} while (--timeout_us);

	if (!timeout_us) {
		drv->state_uncertain = true;
		dev_err(drv->dev,
			"SAW3 PMIC_STS busy timeout during get_vdd (sts=0x%08x)\n",
			status);
		spin_unlock_irqrestore(&drv->lock, flags);
		return -ETIMEDOUT;
	}

	spin_unlock_irqrestore(&drv->lock, flags);
	return status & SAW3_VCTL_DATA_MASK;
}
EXPORT_SYMBOL_GPL(msm_spm_get_vdd);

/**
 * msm_spm_is_avs_enabled() - Check if AVS is currently enabled
 * @cpu: CPU core number
 */
int msm_spm_is_avs_enabled(unsigned int cpu)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 val;
	int ret;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}
	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &val);
	if (ret)
		drv->state_uncertain = true;
	spin_unlock_irqrestore(&drv->lock, flags);
	if (ret)
		return ret;

	return !!(val & SAW3_AVS_CTL_EN_MASK);
}
EXPORT_SYMBOL_GPL(msm_spm_is_avs_enabled);

/**
 * msm_spm_avs_enable() - Enable AVS in SAW controller
 * @cpu: CPU core number
 */
int msm_spm_avs_enable(unsigned int cpu)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 val;
	int ret;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}
	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &val);
	if (!ret) {
		val |= SAW3_AVS_CTL_EN_MASK | SAW3_AVS_CTL_TGGL_MASK;
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, val);
		if (!ret)
			mb();
	}
	if (ret)
		drv->state_uncertain = true;
	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_avs_enable);

/**
 * msm_spm_avs_disable() - Disable AVS in SAW controller
 * @cpu: CPU core number
 */
int msm_spm_avs_disable(unsigned int cpu)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 val;
	int ret;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}
	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &val);
	if (!ret) {
		val &= ~(SAW3_AVS_CTL_EN_MASK | SAW3_AVS_CTL_TGGL_MASK);
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, val);
		if (!ret)
			mb();
	}
	if (ret)
		drv->state_uncertain = true;
	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_avs_disable);

/**
 * msm_spm_avs_set_limit() - Configure AVS min and max voltage limits
 * @cpu: CPU core number
 * @min_lvl: minimum PMIC selector
 * @max_lvl: maximum PMIC selector
 *
 * SAW3_AVS_LIMIT (offset 0x24) encodes min_lvl in bits [7:0] and max_lvl in
 * bits [23:16]. When SAW3_AVS_CTL_EN_MASK is active, SAW3_AVS_CTL also limits
 * selectors to 6-bit values [0x0..0x3f]. Reject inverted ranges (min > max)
 * and out-of-range selectors before touching hardware.
 */
int msm_spm_avs_set_limit(unsigned int cpu, uint32_t min_lvl, uint32_t max_lvl)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 avs_ctl = 0;
	u32 value;
	int ret;

	if (min_lvl > max_lvl)
		return -EINVAL;
	if (min_lvl > SAW3_VCTL_DATA_MASK || max_lvl > SAW3_VCTL_DATA_MASK)
		return -ERANGE;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	value = ((max_lvl & SAW3_VCTL_DATA_MASK) << 16) |
		(min_lvl & SAW3_VCTL_DATA_MASK);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}

	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &avs_ctl);
	if (ret) {
		drv->state_uncertain = true;
		spin_unlock_irqrestore(&drv->lock, flags);
		return ret;
	}

	if ((avs_ctl & SAW3_AVS_CTL_EN_MASK) &&
	    (min_lvl > SAW3_AVS_CTL_VLVL_MAX || max_lvl > SAW3_AVS_CTL_VLVL_MAX)) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -ERANGE;
	}

	ret = regmap_write(drv->regmap, SAW3_AVS_LIMIT, value);
	if (ret)
		drv->state_uncertain = true;
	else
		mb();

	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_avs_set_limit);

/**
 * msm_spm_avs_enable_irq() - Enable an AVS interrupt
 * @cpu: CPU core number
 * @irq: interrupt type (MIN or MAX)
 */
int msm_spm_avs_enable_irq(unsigned int cpu, enum msm_spm_avs_irq irq)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 mask, val;
	int ret;

	if (irq == MSM_SPM_AVS_IRQ_MIN)
		mask = SAW3_AVS_CTL_IRQ_MIN_MASK;
	else if (irq == MSM_SPM_AVS_IRQ_MAX)
		mask = SAW3_AVS_CTL_IRQ_MAX_MASK;
	else
		return -EINVAL;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}
	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &val);
	if (!ret && !(val & mask)) {
		val |= mask;
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, val);
		if (!ret)
			mb();
	}
	if (ret)
		drv->state_uncertain = true;
	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_avs_enable_irq);

/**
 * msm_spm_avs_disable_irq() - Disable an AVS interrupt
 * @cpu: CPU core number
 * @irq: interrupt type (MIN or MAX)
 */
int msm_spm_avs_disable_irq(unsigned int cpu, enum msm_spm_avs_irq irq)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 mask, val;
	int ret;

	if (irq == MSM_SPM_AVS_IRQ_MIN)
		mask = SAW3_AVS_CTL_IRQ_MIN_MASK;
	else if (irq == MSM_SPM_AVS_IRQ_MAX)
		mask = SAW3_AVS_CTL_IRQ_MAX_MASK;
	else
		return -EINVAL;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}
	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &val);
	if (!ret && (val & mask)) {
		val &= ~mask;
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, val);
		if (!ret)
			mb();
	}
	if (ret)
		drv->state_uncertain = true;
	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_avs_disable_irq);

/**
 * msm_spm_avs_clear_irq() - Clear latched AVS interrupt
 * @cpu: CPU core number
 * @irq: interrupt type (MIN or MAX)
 *
 * Clearing a latched AVS IRQ requires a two-step write toggling the IRQ enable
 * bit off and back on. Any failure during the read or either write step leaves
 * SAW3_AVS_CTL in an uncertain state and latches state_uncertain = true.
 */
int msm_spm_avs_clear_irq(unsigned int cpu, enum msm_spm_avs_irq irq)
{
	struct qcom_spm_drvdata *drv = msm_spm_get_drvdata(cpu);
	unsigned long flags;
	u32 mask, val;
	int ret;

	if (irq == MSM_SPM_AVS_IRQ_MIN)
		mask = SAW3_AVS_CTL_IRQ_MIN_MASK;
	else if (irq == MSM_SPM_AVS_IRQ_MAX)
		mask = SAW3_AVS_CTL_IRQ_MAX_MASK;
	else
		return -EINVAL;

	if (!drv)
		return -EPROBE_DEFER;
	if (IS_ERR(drv))
		return PTR_ERR(drv);

	spin_lock_irqsave(&drv->lock, flags);
	if (drv->state_uncertain) {
		spin_unlock_irqrestore(&drv->lock, flags);
		return -EIO;
	}
	ret = regmap_read(drv->regmap, SAW3_AVS_CTL, &val);
	if (!ret && (val & mask)) {
		/* Clear by toggling interrupt enable bit off and back on */
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, val & ~mask);
		if (!ret) {
			mb();
			ret = regmap_write(drv->regmap, SAW3_AVS_CTL, val);
			if (!ret)
				mb();
		}
	}
	if (ret) {
		drv->state_uncertain = true;
		dev_err(drv->dev,
			"SAW3 AVS clear_irq(%d) failed (%d); controller state uncertain\n",
			irq, ret);
	}
	spin_unlock_irqrestore(&drv->lock, flags);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_spm_avs_clear_irq);

/**
 * msm_spm_set_low_power_mode() - Reject HLOS SPM low-power mode programming
 * @mode: requested low-power mode
 * @notify_rpm: RPM notification flag
 *
 * On MSM8996, per-core and L2/cluster low-power state sequences are owned by
 * ARM64 PSCI firmware in TrustZone (qcom,use-psci), and the HLOS-visible
 * CBF SAW3 block (saw3@9a10000) is a dedicated PMIC/AVS voltage controller
 * with no HLOS SPM command sequences. Never fake success; fail closed with
 * -EOPNOTSUPP if invoked.
 */
int msm_spm_set_low_power_mode(unsigned int mode, bool notify_rpm)
{
	int ret = msm_spm_probe_done();

	if (ret)
		return ret;

	pr_warn_once("HLOS msm_spm_set_low_power_mode(mode=%u, rpm=%d) is unsupported on MSM8996 PSCI; rejecting\n",
		     mode, notify_rpm);
	return -EOPNOTSUPP;
}
EXPORT_SYMBOL_GPL(msm_spm_set_low_power_mode);

static const struct regmap_config qcom_saw3_regmap_config = {
	.reg_bits = 32,
	.val_bits = 32,
	.reg_stride = 4,
	.max_register = SAW3_VERSION,
	.fast_io = true,
};

static int qcom_spm_parse_cpu_vctl_list(struct device *dev,
					struct device_node *node,
					struct cpumask *mask)
{
	struct device_node *cpu_node, *dev_cpu_node;
	int idx = 0, cpu;

	cpumask_clear(mask);

	while ((cpu_node = of_parse_phandle(node, "qcom,cpu-vctl-list", idx++))) {
		bool matched = false;

		for_each_possible_cpu(cpu) {
			dev_cpu_node = of_cpu_device_node_get(cpu);
			if (dev_cpu_node == cpu_node) {
				cpumask_set_cpu(cpu, mask);
				matched = true;
			}
			of_node_put(dev_cpu_node);
		}
		of_node_put(cpu_node);

		if (!matched) {
			dev_err(dev, "qcom,cpu-vctl-list entry %d does not match any possible CPU\n",
				idx - 1);
			return -EINVAL;
		}
	}

	/*
	 * On MSM8996 both CPU clusters (CPU0..CPU1 Silver, CPU2..CPU3 Gold)
	 * share the CBF SAW3 rail controller and must be explicitly listed.
	 */
	for_each_possible_cpu(cpu) {
		if (!cpumask_test_cpu(cpu, mask)) {
			dev_err(dev, "CPU%d missing from qcom,cpu-vctl-list\n",
				cpu);
			return -EINVAL;
		}
	}

	return 0;
}

static int qcom_spm_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *node = dev->of_node;
	struct qcom_spm_drvdata *drv;
	void __iomem *base;
	u32 ver = 0, saw_id = 0, avs_ctl_init = 0;
	int cpu, ret;

	if (spm_global_drvdata) {
		dev_err(dev, "Duplicate SAW3/SPM voltage controller registration rejected\n");
		return -EBUSY;
	}

	drv = devm_kzalloc(dev, sizeof(*drv), GFP_KERNEL);
	if (!drv)
		return -ENOMEM;

	drv->dev = dev;
	drv->of_node = node;
	spin_lock_init(&drv->lock);

	ret = qcom_spm_parse_cpu_vctl_list(dev, node, &drv->cpus);
	if (ret)
		goto err_mark_cpus;

	if (of_property_read_u32(node, "qcom,saw2-ver-reg", &drv->ver_reg))
		drv->ver_reg = SAW3_VERSION;

	if (of_property_read_u32(node, "qcom,vctl-port", &drv->vctl_port))
		drv->vctl_port = 0;

	if (drv->vctl_port != 0) {
		dev_err(dev, "Invalid qcom,vctl-port %u (must be 0 for PMIC_STS updates)\n",
			drv->vctl_port);
		ret = -EINVAL;
		goto err_mark_cpus;
	}

	if (of_property_read_u32(node, "qcom,vctl-timeout-us",
				 &drv->vctl_timeout_us) ||
	    !drv->vctl_timeout_us)
		drv->vctl_timeout_us = SAW3_VCTL_DEFAULT_TIMEOUT_US;

	/*
	 * Claim exclusive MMIO ownership via devm_platform_ioremap_resource()
	 * so no second driver or syscon consumer can map this register block.
	 */
	base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(base)) {
		ret = PTR_ERR(base);
		goto err_mark_cpus;
	}

	drv->regmap = devm_regmap_init_mmio(dev, base,
					    &qcom_saw3_regmap_config);
	if (IS_ERR(drv->regmap)) {
		ret = PTR_ERR(drv->regmap);
		goto err_mark_cpus;
	}

	ret = regmap_read(drv->regmap, drv->ver_reg, &ver);
	if (ret)
		goto err_mark_cpus;

	drv->major = (ver >> 28) & 0xF;
	drv->minor = (ver >> 16) & 0xFFF;

	/* Match SAW_v3.0 (1.0) or SAW_v2.3 (3.0) using the v3_0 register offset table */
	if (!((drv->major == 1 && drv->minor == 0) ||
	      (drv->major == 3 && drv->minor == 0))) {
		dev_err(dev, "Unsupported SAW hardware version %u.%u (raw=0x%08x)\n",
			drv->major, drv->minor, ver);
		ret = -ENODEV;
		goto err_mark_cpus;
	}

	ret = regmap_read(drv->regmap, SAW3_ID, &saw_id);
	if (ret)
		goto err_mark_cpus;

	drv->pmic_arb_present = !!(saw_id & SAW3_ID_PMIC_ARB_INTF_MASK);
	if (!drv->pmic_arb_present) {
		dev_err(dev, "SAW3 instance lacks PMIC arbiter interface (SAW3_ID=0x%08x)\n",
			saw_id);
		ret = -ENODEV;
		goto err_mark_cpus;
	}

	if (!of_property_read_u32(node, "qcom,saw2-avs-ctl", &avs_ctl_init)) {
		ret = regmap_write(drv->regmap, SAW3_AVS_CTL, avs_ctl_init);
		if (ret)
			goto err_mark_cpus;
		mb();
	}

	for_each_cpu(cpu, &drv->cpus)
		per_cpu(cpu_spm_drvdata, cpu) = drv;

	spm_global_drvdata = drv;
	platform_set_drvdata(pdev, drv);

	dev_info(dev, "Qualcomm SAW3 v%u.%u voltage backend probed for %*pbl (timeout %u us)\n",
		 drv->major, drv->minor, cpumask_pr_args(&drv->cpus),
		 drv->vctl_timeout_us);

	return 0;

err_mark_cpus:
	for_each_possible_cpu(cpu)
		per_cpu(cpu_spm_drvdata, cpu) = ERR_PTR(ret);
	return ret;
}

static const struct of_device_id qcom_spm_match_table[] = {
	{ .compatible = "qcom,msm8996-saw3" },
	{ .compatible = "qcom,spm-v2" },
	{ }
};
MODULE_DEVICE_TABLE(of, qcom_spm_match_table);

static struct platform_driver qcom_spm_driver = {
	.probe = qcom_spm_probe,
	.driver = {
		.name = "qcom-spm",
		.of_match_table = qcom_spm_match_table,
		.suppress_bind_attrs = true,
	},
};

static int __init qcom_spm_init(void)
{
	return platform_driver_register(&qcom_spm_driver);
}
postcore_initcall(qcom_spm_init);

MODULE_DESCRIPTION("Qualcomm MSM8996 SAW3 / SPM voltage and AVS backend");
MODULE_LICENSE("GPL v2");
