/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __LINUX_REGULATOR_QCOM_RPM_PHYSICAL_H
#define __LINUX_REGULATOR_QCOM_RPM_PHYSICAL_H

#include <linux/errno.h>
#include <linux/kconfig.h>
#include <linux/types.h>

struct device;
struct device_node;
struct qcom_smd_rpm;
struct regulator;
struct regulator_desc;

#if IS_REACHABLE(CONFIG_REGULATOR_QCOM_RPM_PHYSICAL)
/* Native input metadata is intentionally unused only with the audited
 * qcom,rpm-managed-input contract; these resources have no Linux Vin parent.
 */
int qcom_rpm_physical_register(struct device *dev, struct device_node *node,
			      struct qcom_smd_rpm *rpm, u32 type, u32 id,
			      const struct regulator_desc *template,
			      const char *supply_name);
/* No-I/O protocol readiness only; an unknown, unowned baseline is permitted. */
int qcom_rpm_physical_check_ready(struct regulator *regulator);
/* Force core voltage/load aggregate replay, then fresh BOTH request ACKs;
 * never a measurement of the physical output voltage.
 */
int qcom_rpm_physical_sync(struct regulator *regulator, bool require_enabled);
/* Explicit recovery can replay only an existing acknowledged BOTH baseline. */
int qcom_rpm_physical_recover(struct regulator *regulator);
/* Publish a bank only after every child registered successfully; no I/O. */
int qcom_rpm_physical_activate(struct device *bank);
/* Native regulator-bank PM callbacks; untouched resources send no requests. */
int qcom_rpm_physical_suspend(struct device *bank);
int qcom_rpm_physical_resume(struct device *bank);
#else
static inline int qcom_rpm_physical_register(struct device *dev,
		struct device_node *node, struct qcom_smd_rpm *rpm, u32 type,
		u32 id, const struct regulator_desc *template, const char *supply_name)
{
	return -EOPNOTSUPP;
}
static inline int qcom_rpm_physical_check_ready(struct regulator *regulator)
{
	return -EOPNOTSUPP;
}
static inline int qcom_rpm_physical_sync(struct regulator *regulator,
				       bool require_enabled)
{
	return -EOPNOTSUPP;
}
static inline int qcom_rpm_physical_recover(struct regulator *regulator)
{
	return -EOPNOTSUPP;
}
static inline int qcom_rpm_physical_activate(struct device *bank)
{
	return 0;
}
static inline int qcom_rpm_physical_suspend(struct device *bank)
{
	return 0;
}
static inline int qcom_rpm_physical_resume(struct device *bank)
{
	return 0;
}
#endif
#endif
