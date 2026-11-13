/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __LINUX_REGULATOR_QCOM_CPR3_CPU_H
#define __LINUX_REGULATOR_QCOM_CPR3_CPU_H

#include <linux/errno.h>
#include <linux/kconfig.h>
#include <linux/types.h>

struct regulator;
struct device_node;

/* Observations only. A stable sample does not exclude autonomous CPR/SAW,
 * LMH/firmware, hotplug or collapse and must not authorize clock writes.
 * vdd_uv is the existing parent provider's reported voltage/setpoint, not
 * an ADC measurement or an additional transport acknowledgement.
 */
struct qcom_cpr3_cpu_state {
	unsigned int thread_id;
	unsigned int corner;
	unsigned int corner_count;
	int vdd_uv;
	int power_error;
	u32 loop_ctl;
	u32 status;
	u32 hw_closed_loop;
	bool enabled;
	bool clocks_enabled;
	bool suspended;
	bool raw_valid;
	bool hw_loop_supported;
	bool hw_loop_selected;
};

#if IS_REACHABLE(CONFIG_REGULATOR_CPR3)
/* No clock preparation or hardware writes. ENODATA means no confirmed active
 * corner/clock state, EAGAIN an unstable sample, EBUSY suspended, and a sticky
 * controller power error is propagated. Only a published CPR3 HMSS rdev is
 * accepted; callbacks/PM/remove are serialized while copying scalar data.
 */
int qcom_cpr3_cpu_get_state(struct regulator *regulator,
			  struct qcom_cpr3_cpu_state *state);
/* The caller retains node ownership while sampling. Unpublished/disabled
 * providers defer. This acquires no regulator consumer or enable reference.
 */
int qcom_cpr3_cpu_get_state_by_node(struct device_node *node,
				  struct qcom_cpr3_cpu_state *state);
#else
static inline int qcom_cpr3_cpu_get_state(struct regulator *regulator,
					struct qcom_cpr3_cpu_state *state)
{
	return -EOPNOTSUPP;
}

static inline int qcom_cpr3_cpu_get_state_by_node(struct device_node *node,
					struct qcom_cpr3_cpu_state *state)
{
	return -EOPNOTSUPP;
}
#endif

#endif
