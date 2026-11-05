/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright (c) 2017 The Linux Foundation. All rights reserved.
 */

#ifndef _QPNP_LABIBB_REGULATOR_H
#define _QPNP_LABIBB_REGULATOR_H

enum labibb_notify_event {
	LAB_VREG_OK = 1,
	LAB_VREG_NOT_OK,
};

#ifdef CONFIG_REGULATOR_QPNP_LABIBB
int qpnp_labibb_notifier_register(struct notifier_block *nb);
int qpnp_labibb_notifier_unregister(struct notifier_block *nb);
#else
static inline int qpnp_labibb_notifier_register(struct notifier_block *nb)
{
	return 0;
}
static inline int qpnp_labibb_notifier_unregister(struct notifier_block *nb)
{
	return 0;
}
#endif


#ifdef CONFIG_REGULATOR_QPNP_LABIBB_SOMC
struct regulator;
int qpnp_lab_set_precharge(struct regulator *regulator, u32 time, bool en);
int qpnp_lab_set_soft_start(struct regulator *regulator, u32 time);
int qpnp_lab_set_pull_down(struct regulator *regulator, u8 strength);
int qpnp_lab_set_current_max(struct regulator *regulator, u32 limit);
int qpnp_ibb_set_soft_start(struct regulator *regulator, u32 time);
int qpnp_ibb_set_pull_down(struct regulator *regulator, u8 strength);
int qpnp_ibb_set_current_max(struct regulator *regulator, u32 limit);
#endif

#endif
