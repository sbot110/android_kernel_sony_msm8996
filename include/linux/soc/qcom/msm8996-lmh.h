/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __LINUX_SOC_QCOM_MSM8996_LMH_H
#define __LINUX_SOC_QCOM_MSM8996_LMH_H
#include <linux/errno.h>
#include <linux/kconfig.h>
#include <linux/types.h>
struct device_node;

/* Scalars only. Available/consistent is NOT a transferable firmware/CPR/SAW
 * exclusion grant. Voltage is a provider setpoint, not an ADC measurement.
 * last_profile is only the last acknowledged Linux request, never a query
 * of the bootloader's or autonomously changed firmware profile.
 */
struct qcom_msm8996_lmh_v1_state {
	unsigned int baseline_uv, dpm_mv, sensor_count, profile_count;
	unsigned int last_profile;
	bool online, retired, suspended, pending, quarantined;
	bool baseline_valid, odcm_enabled, profile_requested;
	int error, sensor_error;
};
#if IS_REACHABLE(CONFIG_SONY_MSM8996_LMH_V1)
int qcom_msm8996_lmh_v1_get_state_by_node(struct device_node *node,
		struct qcom_msm8996_lmh_v1_state *state);
int qcom_msm8996_lmh_v1_request_profile(struct device_node *node, unsigned int profile);
#else
static inline int qcom_msm8996_lmh_v1_get_state_by_node(struct device_node *node,
		struct qcom_msm8996_lmh_v1_state *state)
{
	return -EOPNOTSUPP;
}
static inline int qcom_msm8996_lmh_v1_request_profile(struct device_node *node,
		unsigned int profile)
{
	return -EOPNOTSUPP;
}
#endif
#endif
