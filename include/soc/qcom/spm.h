/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (c) 2011-2016, The Linux Foundation. All rights reserved.
 * Copyright (c) 2016 Sony Mobile Communications Inc.
 */

#ifndef __SOC_QCOM_SPM_H
#define __SOC_QCOM_SPM_H

#include <linux/types.h>
#include <linux/err.h>
#include <linux/of.h>

enum msm_spm_pm_mode {
	MSM_SPM_MODE_DISABLED,
	MSM_SPM_MODE_CLOCK_GATING,
	MSM_SPM_MODE_POWER_COLLAPSE,
	MSM_SPM_MODE_STANDALONE_POWER_COLLAPSE,
	MSM_SPM_MODE_RETENTION,
	MSM_SPM_MODE_GDHS,
	MSM_SPM_MODE_NR
};

enum msm_spm_avs_irq {
	MSM_SPM_AVS_IRQ_MIN,
	MSM_SPM_AVS_IRQ_MAX,
};

#if IS_ENABLED(CONFIG_QCOM_SPM) || IS_ENABLED(CONFIG_MSM_SPM)

int msm_spm_set_low_power_mode(unsigned int mode, bool notify_rpm);
int msm_spm_set_vdd(unsigned int cpu, unsigned int vlevel);
int msm_spm_get_vdd(unsigned int cpu);
int msm_spm_probe_done(void);
bool msm_spm_is_saw_node(const struct device_node *np);
int msm_spm_is_avs_enabled(unsigned int cpu);
int msm_spm_avs_enable(unsigned int cpu);
int msm_spm_avs_disable(unsigned int cpu);
int msm_spm_avs_set_limit(unsigned int cpu, uint32_t min_lvl, uint32_t max_lvl);
int msm_spm_avs_clear_irq(unsigned int cpu, enum msm_spm_avs_irq irq);
int msm_spm_avs_enable_irq(unsigned int cpu, enum msm_spm_avs_irq irq);
int msm_spm_avs_disable_irq(unsigned int cpu, enum msm_spm_avs_irq irq);

#else

static inline int msm_spm_set_low_power_mode(unsigned int mode, bool notify_rpm)
{
	return -ENOSYS;
}

static inline int msm_spm_set_vdd(unsigned int cpu, unsigned int vlevel)
{
	return -ENOSYS;
}

static inline int msm_spm_get_vdd(unsigned int cpu)
{
	return -ENOSYS;
}

static inline int msm_spm_probe_done(void)
{
	return -ENOSYS;
}

static inline bool msm_spm_is_saw_node(const struct device_node *np)
{
	return false;
}

static inline int msm_spm_is_avs_enabled(unsigned int cpu)
{
	return -ENOSYS;
}

static inline int msm_spm_avs_enable(unsigned int cpu)
{
	return -ENOSYS;
}

static inline int msm_spm_avs_disable(unsigned int cpu)
{
	return -ENOSYS;
}

static inline int msm_spm_avs_set_limit(unsigned int cpu, uint32_t min_lvl,
					uint32_t max_lvl)
{
	return -ENOSYS;
}

static inline int msm_spm_avs_clear_irq(unsigned int cpu,
					enum msm_spm_avs_irq irq)
{
	return -ENOSYS;
}

static inline int msm_spm_avs_enable_irq(unsigned int cpu,
					 enum msm_spm_avs_irq irq)
{
	return -ENOSYS;
}

static inline int msm_spm_avs_disable_irq(unsigned int cpu,
					  enum msm_spm_avs_irq irq)
{
	return -ENOSYS;
}

#endif /* CONFIG_QCOM_SPM || CONFIG_MSM_SPM */

#endif /* __SOC_QCOM_SPM_H */
