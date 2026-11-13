/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __LINUX_REGULATOR_QCOM_RPM_CORNER_H
#define __LINUX_REGULATOR_QCOM_RPM_CORNER_H

#include <linux/errno.h>
#include <linux/kconfig.h>
#include <linux/types.h>

struct regulator;
struct device_node;

/* Sets use BIT(0) for ACTIVE and BIT(1) for SLEEP. Key masks use BIT(0)
 * for corn, BIT(1) for vfc and BIT(2) for swen, independently of sets.
 */
struct qcom_rpm_corner_state {
	u32 resource_type;
	u32 resource_id;
	u32 key;
	u32 requested_corner_wire;
	u8 sets;
	u8 owned_keys;
	u8 recovery_keys;
	bool voltage_valid;
	bool enable_valid;
	bool enabled;
	bool online;
	bool retired;
	bool suspended;
	bool quarantined;
	bool initial_uncertain;
	int last_error;
};

#if IS_REACHABLE(CONFIG_REGULATOR_QCOM_RPM_CORNER)
/* Force real ACTIVE/SLEEP ACKs; cached regulator success is not sufficient. */
int qcom_rpm_corner_sync(struct regulator *regulator, bool require_enabled);
/* Read-only metadata, not a voltage readback or acquisition of an RPM lease.
 * key is the literal RPM corn/vfc key. requested_corner_wire is the cached
 * client request (logical API corner minus one), meaningful only when
 * voltage_valid is true; even then it is not proof of an acknowledged vote.
 * The caller must retain its regulator handle for the duration of the call.
 * Typed snapshots remain available on lifecycle/quarantine errors. Return 0
 * only means the provider is available, and may have voltage_valid == false.
 */
int qcom_rpm_corner_get_state(struct regulator *regulator,
			      struct qcom_rpm_corner_state *state);
/* Observe an exact published provider node without acquiring a regulator
 * consumer, resolving its supply or sending votes. Caller holds the OF node
 * reference; snapshots contain scalars only. Unpublished/disabled nodes defer.
 * All get_state caveats about metadata versus ACK/voltage/lease still apply.
 */
int qcom_rpm_corner_get_state_by_node(struct device_node *node,
				      struct qcom_rpm_corner_state *state);
#else
static inline int qcom_rpm_corner_sync(struct regulator *regulator,
				       bool require_enabled)
{
	return -EOPNOTSUPP;
}
static inline int qcom_rpm_corner_get_state(struct regulator *regulator,
					  struct qcom_rpm_corner_state *state)
{
	return -EOPNOTSUPP;
}
static inline int qcom_rpm_corner_get_state_by_node(struct device_node *node,
				      struct qcom_rpm_corner_state *state)
{
	return -EOPNOTSUPP;
}
#endif
#endif
