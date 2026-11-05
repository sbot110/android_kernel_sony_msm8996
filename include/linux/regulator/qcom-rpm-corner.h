/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __LINUX_REGULATOR_QCOM_RPM_CORNER_H
#define __LINUX_REGULATOR_QCOM_RPM_CORNER_H

#include <linux/errno.h>
#include <linux/kconfig.h>
#include <linux/types.h>

struct regulator;

#if IS_REACHABLE(CONFIG_REGULATOR_QCOM_RPM_CORNER)
/* Force real ACTIVE/SLEEP ACKs; cached regulator success is not sufficient. */
int qcom_rpm_corner_sync(struct regulator *regulator, bool require_enabled);
#else
static inline int qcom_rpm_corner_sync(struct regulator *regulator,
				       bool require_enabled)
{
	return -EOPNOTSUPP;
}
#endif
#endif
