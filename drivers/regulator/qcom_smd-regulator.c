/*
 * Copyright (c) 2015, Sony Mobile Communications AB.
 * Copyright (c) 2012-2013, The Linux Foundation. All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 and
 * only version 2 as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#include <linux/module.h>
#include <linux/kref.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/of_regulator.h>
#include <linux/regulator/qcom-rpm-physical.h>
#include <linux/soc/qcom/smd-rpm.h>

#include "internal.h"

struct qcom_rpm_reg {
	struct device *dev;

	struct qcom_smd_rpm *rpm;

	u32 type;
	u32 id;

	struct regulator_desc desc;
	struct regulator_dev *rdev;
	struct mutex lock;
	struct kref refs;
	bool online;
	bool attempted;
	bool claimed;

	int is_enabled;
	int uV;
};

struct rpm_regulator_req {
	__le32 key;
	__le32 nbytes;
	__le32 value;
};

#define RPM_KEY_SWEN	0x6e657773 /* "swen" */
#define RPM_KEY_UV	0x00007675 /* "uv" */
#define RPM_KEY_MA	0x0000616d /* "ma" */

static int rpm_reg_write_active(struct qcom_rpm_reg *vreg,
				struct rpm_regulator_req *req,
				size_t size)
{
	if (!vreg->online)
		return -ENODEV;
	vreg->attempted = true;
	return qcom_rpm_smd_write(vreg->rpm,
				  QCOM_SMD_RPM_ACTIVE_STATE,
				  vreg->type,
				  vreg->id,
				  req, size);
}

static int rpm_reg_enable(struct regulator_dev *rdev)
{
	struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);
	struct rpm_regulator_req req;
	int ret;

	req.key = cpu_to_le32(RPM_KEY_SWEN);
	req.nbytes = cpu_to_le32(sizeof(u32));
	req.value = cpu_to_le32(1);

	mutex_lock(&vreg->lock);
	ret = rpm_reg_write_active(vreg, &req, sizeof(req));
	if (!ret)
		vreg->is_enabled = 1;
	mutex_unlock(&vreg->lock);

	return ret;
}

static int rpm_reg_is_enabled(struct regulator_dev *rdev)
{
	struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);
	int ret;

	mutex_lock(&vreg->lock);
	ret = vreg->online ? vreg->is_enabled : -ENODEV;
	mutex_unlock(&vreg->lock);
	return ret;
}

static int rpm_reg_disable(struct regulator_dev *rdev)
{
	struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);
	struct rpm_regulator_req req;
	int ret;

	req.key = cpu_to_le32(RPM_KEY_SWEN);
	req.nbytes = cpu_to_le32(sizeof(u32));
	req.value = 0;

	mutex_lock(&vreg->lock);
	ret = rpm_reg_write_active(vreg, &req, sizeof(req));
	if (!ret)
		vreg->is_enabled = 0;
	mutex_unlock(&vreg->lock);

	return ret;
}

static int rpm_reg_get_voltage(struct regulator_dev *rdev)
{
	struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);
	int ret;

	mutex_lock(&vreg->lock);
	ret = vreg->online ? vreg->uV : -ENODEV;
	mutex_unlock(&vreg->lock);
	return ret;
}

static int rpm_reg_set_voltage(struct regulator_dev *rdev,
			       int min_uV,
			       int max_uV,
			       unsigned *selector)
{
	struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);
	struct rpm_regulator_req req;
	int ret, selected = -1;

	if (min_uV <= 0 || min_uV > max_uV)
		return -EINVAL;
	if (rdev->desc->ops->list_voltage && rdev->desc->n_voltages) {
		selected = regulator_map_voltage_iterate(rdev, min_uV, max_uV);
		if (selected < 0)
			return selected;
		min_uV = rdev->desc->ops->list_voltage(rdev, selected);
		if (min_uV <= 0)
			return -EINVAL;
	}

	req.key = cpu_to_le32(RPM_KEY_UV);
	req.nbytes = cpu_to_le32(sizeof(u32));
	req.value = cpu_to_le32(min_uV);

	mutex_lock(&vreg->lock);
	ret = rpm_reg_write_active(vreg, &req, sizeof(req));
	if (!ret)
		vreg->uV = min_uV;
	mutex_unlock(&vreg->lock);
	if (!ret && selector && selected >= 0)
		*selector = selected;

	return ret;
}

static int rpm_reg_set_load(struct regulator_dev *rdev, int load_uA)
{
	struct qcom_rpm_reg *vreg = rdev_get_drvdata(rdev);
	struct rpm_regulator_req req;
	int ret;

	if (load_uA < 0)
		return -EINVAL;

	req.key = cpu_to_le32(RPM_KEY_MA);
	req.nbytes = cpu_to_le32(sizeof(u32));
	req.value = cpu_to_le32(min(load_uA / 1000, 0x1fff));

	mutex_lock(&vreg->lock);
	ret = rpm_reg_write_active(vreg, &req, sizeof(req));
	mutex_unlock(&vreg->lock);
	return ret;
}

static const struct regulator_ops rpm_smps_ldo_ops = {
	.enable = rpm_reg_enable,
	.disable = rpm_reg_disable,
	.is_enabled = rpm_reg_is_enabled,
	.list_voltage = regulator_list_voltage_linear_range,

	.get_voltage = rpm_reg_get_voltage,
	.set_voltage = rpm_reg_set_voltage,

	.set_load = rpm_reg_set_load,
};

static const struct regulator_ops rpm_smps_ldo_ops_fixed = {
	.enable = rpm_reg_enable,
	.disable = rpm_reg_disable,
	.is_enabled = rpm_reg_is_enabled,

	.get_voltage = rpm_reg_get_voltage,
	.set_voltage = rpm_reg_set_voltage,

	.set_load = rpm_reg_set_load,
};

static const struct regulator_ops rpm_switch_ops = {
	.enable = rpm_reg_enable,
	.disable = rpm_reg_disable,
	.is_enabled = rpm_reg_is_enabled,
};

static const struct regulator_ops rpm_bob_ops = {
	.enable = rpm_reg_enable,
	.disable = rpm_reg_disable,
	.is_enabled = rpm_reg_is_enabled,

	.get_voltage = rpm_reg_get_voltage,
	.set_voltage = rpm_reg_set_voltage,
};

static const struct regulator_desc pma8084_hfsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(375000,  0,  95, 12500),
		REGULATOR_LINEAR_RANGE(1550000, 96, 158, 25000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 159,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pma8084_ftsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(350000,  0, 184, 5000),
		REGULATOR_LINEAR_RANGE(1280000, 185, 261, 10000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 262,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pma8084_pldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE( 750000,  0,  63, 12500),
		REGULATOR_LINEAR_RANGE(1550000, 64, 126, 25000),
		REGULATOR_LINEAR_RANGE(3100000, 127, 163, 50000),
	},
	.n_linear_ranges = 3,
	.n_voltages = 164,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pma8084_nldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(750000, 0, 63, 12500),
	},
	.n_linear_ranges = 1,
	.n_voltages = 64,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pma8084_switch = {
	.ops = &rpm_switch_ops,
};

static const struct regulator_desc pm8x41_hfsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE( 375000,  0,  95, 12500),
		REGULATOR_LINEAR_RANGE(1575000, 96, 158, 25000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 159,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8841_ftsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(350000,  0, 184, 5000),
		REGULATOR_LINEAR_RANGE(1280000, 185, 261, 10000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 262,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8941_boost = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(4000000, 0, 30, 50000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 31,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8941_pldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE( 750000,  0,  63, 12500),
		REGULATOR_LINEAR_RANGE(1550000, 64, 126, 25000),
		REGULATOR_LINEAR_RANGE(3100000, 127, 163, 50000),
	},
	.n_linear_ranges = 3,
	.n_voltages = 164,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8941_nldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(750000, 0, 63, 12500),
	},
	.n_linear_ranges = 1,
	.n_voltages = 64,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8941_lnldo = {
	.fixed_uV = 1740000,
	.n_voltages = 1,
	.ops = &rpm_smps_ldo_ops_fixed,
};

static const struct regulator_desc pm8941_switch = {
	.ops = &rpm_switch_ops,
};

static const struct regulator_desc pm8916_pldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(750000, 0, 208, 12500),
	},
	.n_linear_ranges = 1,
	.n_voltages = 209,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8916_nldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(375000, 0, 93, 12500),
	},
	.n_linear_ranges = 1,
	.n_voltages = 94,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8916_buck_lvo_smps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(375000, 0, 95, 12500),
		REGULATOR_LINEAR_RANGE(750000, 96, 127, 25000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 128,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8916_buck_hvo_smps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(1550000, 0, 31, 25000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 32,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8994_hfsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE( 375000,  0,  95, 12500),
		REGULATOR_LINEAR_RANGE(1550000, 96, 158, 25000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 159,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8994_ftsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(350000,  0, 199, 5000),
		REGULATOR_LINEAR_RANGE(700000, 200, 349, 10000),
	},
	.n_linear_ranges = 2,
	.n_voltages = 350,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8994_nldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(750000, 0, 63, 12500),
	},
	.n_linear_ranges = 1,
	.n_voltages = 64,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8994_pldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE( 750000,  0,  63, 12500),
		REGULATOR_LINEAR_RANGE(1550000, 64, 126, 25000),
		REGULATOR_LINEAR_RANGE(3100000, 127, 163, 50000),
	},
	.n_linear_ranges = 3,
	.n_voltages = 164,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8994_switch = {
	.ops = &rpm_switch_ops,
};

static const struct regulator_desc pm8994_lnldo = {
	.fixed_uV = 1740000,
	.n_voltages = 1,
	.ops = &rpm_smps_ldo_ops_fixed,
};

static const struct regulator_desc pm8998_ftsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(320000, 0, 258, 4000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 259,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8998_hfsmps = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(320000, 0, 215, 8000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 216,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8998_nldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(312000, 0, 127, 8000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 128,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8998_pldo = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(1664000, 0, 255, 8000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 256,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8998_pldo_lv = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(1256000, 0, 127, 8000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 128,
	.ops = &rpm_smps_ldo_ops,
};

static const struct regulator_desc pm8998_switch = {
	.ops = &rpm_switch_ops,
};

static const struct regulator_desc pmi8998_bob = {
	.linear_ranges = (struct regulator_linear_range[]) {
		REGULATOR_LINEAR_RANGE(1824000, 0, 83, 32000),
	},
	.n_linear_ranges = 1,
	.n_voltages = 84,
	.ops = &rpm_bob_ops,
};

struct rpm_regulator_data {
	const char *name;
	u32 type;
	u32 id;
	const struct regulator_desc *desc;
	const char *supply;
};

static const struct rpm_regulator_data rpm_pm8841_regulators[] = {
	{ "s1", QCOM_SMD_RPM_SMPB, 1, &pm8x41_hfsmps, "vdd_s1" },
	{ "s2", QCOM_SMD_RPM_SMPB, 2, &pm8841_ftsmps, "vdd_s2" },
	{ "s3", QCOM_SMD_RPM_SMPB, 3, &pm8x41_hfsmps, "vdd_s3" },
	{ "s4", QCOM_SMD_RPM_SMPB, 4, &pm8841_ftsmps, "vdd_s4" },
	{ "s5", QCOM_SMD_RPM_SMPB, 5, &pm8841_ftsmps, "vdd_s5" },
	{ "s6", QCOM_SMD_RPM_SMPB, 6, &pm8841_ftsmps, "vdd_s6" },
	{ "s7", QCOM_SMD_RPM_SMPB, 7, &pm8841_ftsmps, "vdd_s7" },
	{ "s8", QCOM_SMD_RPM_SMPB, 8, &pm8841_ftsmps, "vdd_s8" },
	{}
};

static const struct rpm_regulator_data rpm_pm8916_regulators[] = {
	{ "s1", QCOM_SMD_RPM_SMPA, 1, &pm8916_buck_lvo_smps, "vdd_s1" },
	{ "s2", QCOM_SMD_RPM_SMPA, 2, &pm8916_buck_lvo_smps, "vdd_s2" },
	{ "s3", QCOM_SMD_RPM_SMPA, 3, &pm8916_buck_lvo_smps, "vdd_s3" },
	{ "s4", QCOM_SMD_RPM_SMPA, 4, &pm8916_buck_hvo_smps, "vdd_s4" },
	{ "l1", QCOM_SMD_RPM_LDOA, 1, &pm8916_nldo, "vdd_l1_l2_l3" },
	{ "l2", QCOM_SMD_RPM_LDOA, 2, &pm8916_nldo, "vdd_l1_l2_l3" },
	{ "l3", QCOM_SMD_RPM_LDOA, 3, &pm8916_nldo, "vdd_l1_l2_l3" },
	{ "l4", QCOM_SMD_RPM_LDOA, 4, &pm8916_pldo, "vdd_l4_l5_l6" },
	{ "l5", QCOM_SMD_RPM_LDOA, 5, &pm8916_pldo, "vdd_l4_l5_l6" },
	{ "l6", QCOM_SMD_RPM_LDOA, 6, &pm8916_pldo, "vdd_l4_l5_l6" },
	{ "l7", QCOM_SMD_RPM_LDOA, 7, &pm8916_pldo, "vdd_l7" },
	{ "l8", QCOM_SMD_RPM_LDOA, 8, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18" },
	{ "l9", QCOM_SMD_RPM_LDOA, 9, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18" },
	{ "l10", QCOM_SMD_RPM_LDOA, 10, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l11", QCOM_SMD_RPM_LDOA, 11, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l12", QCOM_SMD_RPM_LDOA, 12, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l13", QCOM_SMD_RPM_LDOA, 13, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l14", QCOM_SMD_RPM_LDOA, 14, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l15", QCOM_SMD_RPM_LDOA, 15, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l16", QCOM_SMD_RPM_LDOA, 16, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l17", QCOM_SMD_RPM_LDOA, 17, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{ "l18", QCOM_SMD_RPM_LDOA, 18, &pm8916_pldo, "vdd_l8_l9_l10_l11_l12_l13_l14_l15_l16_l17_l18"},
	{}
};

static const struct rpm_regulator_data rpm_pm8941_regulators[] = {
	{ "s1", QCOM_SMD_RPM_SMPA, 1, &pm8x41_hfsmps, "vdd_s1" },
	{ "s2", QCOM_SMD_RPM_SMPA, 2, &pm8x41_hfsmps, "vdd_s2" },
	{ "s3", QCOM_SMD_RPM_SMPA, 3, &pm8x41_hfsmps, "vdd_s3" },
	{ "s4", QCOM_SMD_RPM_BOOST, 1, &pm8941_boost },

	{ "l1", QCOM_SMD_RPM_LDOA, 1, &pm8941_nldo, "vdd_l1_l3" },
	{ "l2", QCOM_SMD_RPM_LDOA, 2, &pm8941_nldo, "vdd_l2_lvs1_2_3" },
	{ "l3", QCOM_SMD_RPM_LDOA, 3, &pm8941_nldo, "vdd_l1_l3" },
	{ "l4", QCOM_SMD_RPM_LDOA, 4, &pm8941_nldo, "vdd_l4_l11" },
	{ "l5", QCOM_SMD_RPM_LDOA, 5, &pm8941_lnldo, "vdd_l5_l7" },
	{ "l6", QCOM_SMD_RPM_LDOA, 6, &pm8941_pldo, "vdd_l6_l12_l14_l15" },
	{ "l7", QCOM_SMD_RPM_LDOA, 7, &pm8941_lnldo, "vdd_l5_l7" },
	{ "l8", QCOM_SMD_RPM_LDOA, 8, &pm8941_pldo, "vdd_l8_l16_l18_l19" },
	{ "l9", QCOM_SMD_RPM_LDOA, 9, &pm8941_pldo, "vdd_l9_l10_l17_l22" },
	{ "l10", QCOM_SMD_RPM_LDOA, 10, &pm8941_pldo, "vdd_l9_l10_l17_l22" },
	{ "l11", QCOM_SMD_RPM_LDOA, 11, &pm8941_nldo, "vdd_l4_l11" },
	{ "l12", QCOM_SMD_RPM_LDOA, 12, &pm8941_pldo, "vdd_l6_l12_l14_l15" },
	{ "l13", QCOM_SMD_RPM_LDOA, 13, &pm8941_pldo, "vdd_l13_l20_l23_l24" },
	{ "l14", QCOM_SMD_RPM_LDOA, 14, &pm8941_pldo, "vdd_l6_l12_l14_l15" },
	{ "l15", QCOM_SMD_RPM_LDOA, 15, &pm8941_pldo, "vdd_l6_l12_l14_l15" },
	{ "l16", QCOM_SMD_RPM_LDOA, 16, &pm8941_pldo, "vdd_l8_l16_l18_l19" },
	{ "l17", QCOM_SMD_RPM_LDOA, 17, &pm8941_pldo, "vdd_l9_l10_l17_l22" },
	{ "l18", QCOM_SMD_RPM_LDOA, 18, &pm8941_pldo, "vdd_l8_l16_l18_l19" },
	{ "l19", QCOM_SMD_RPM_LDOA, 19, &pm8941_pldo, "vdd_l8_l16_l18_l19" },
	{ "l20", QCOM_SMD_RPM_LDOA, 20, &pm8941_pldo, "vdd_l13_l20_l23_l24" },
	{ "l21", QCOM_SMD_RPM_LDOA, 21, &pm8941_pldo, "vdd_l21" },
	{ "l22", QCOM_SMD_RPM_LDOA, 22, &pm8941_pldo, "vdd_l9_l10_l17_l22" },
	{ "l23", QCOM_SMD_RPM_LDOA, 23, &pm8941_pldo, "vdd_l13_l20_l23_l24" },
	{ "l24", QCOM_SMD_RPM_LDOA, 24, &pm8941_pldo, "vdd_l13_l20_l23_l24" },

	{ "lvs1", QCOM_SMD_RPM_VSA, 1, &pm8941_switch, "vdd_l2_lvs1_2_3" },
	{ "lvs2", QCOM_SMD_RPM_VSA, 2, &pm8941_switch, "vdd_l2_lvs1_2_3" },
	{ "lvs3", QCOM_SMD_RPM_VSA, 3, &pm8941_switch, "vdd_l2_lvs1_2_3" },

	{ "5vs1", QCOM_SMD_RPM_VSA, 4, &pm8941_switch, "vin_5vs" },
	{ "5vs2", QCOM_SMD_RPM_VSA, 5, &pm8941_switch, "vin_5vs" },

	{}
};

static const struct rpm_regulator_data rpm_pma8084_regulators[] = {
	{ "s1", QCOM_SMD_RPM_SMPA, 1, &pma8084_ftsmps, "vdd_s1" },
	{ "s2", QCOM_SMD_RPM_SMPA, 2, &pma8084_ftsmps, "vdd_s2" },
	{ "s3", QCOM_SMD_RPM_SMPA, 3, &pma8084_hfsmps, "vdd_s3" },
	{ "s4", QCOM_SMD_RPM_SMPA, 4, &pma8084_hfsmps, "vdd_s4" },
	{ "s5", QCOM_SMD_RPM_SMPA, 5, &pma8084_hfsmps, "vdd_s5" },
	{ "s6", QCOM_SMD_RPM_SMPA, 6, &pma8084_ftsmps, "vdd_s6" },
	{ "s7", QCOM_SMD_RPM_SMPA, 7, &pma8084_ftsmps, "vdd_s7" },
	{ "s8", QCOM_SMD_RPM_SMPA, 8, &pma8084_ftsmps, "vdd_s8" },
	{ "s9", QCOM_SMD_RPM_SMPA, 9, &pma8084_ftsmps, "vdd_s9" },
	{ "s10", QCOM_SMD_RPM_SMPA, 10, &pma8084_ftsmps, "vdd_s10" },
	{ "s11", QCOM_SMD_RPM_SMPA, 11, &pma8084_ftsmps, "vdd_s11" },
	{ "s12", QCOM_SMD_RPM_SMPA, 12, &pma8084_ftsmps, "vdd_s12" },

	{ "l1", QCOM_SMD_RPM_LDOA, 1, &pma8084_nldo, "vdd_l1_l11" },
	{ "l2", QCOM_SMD_RPM_LDOA, 2, &pma8084_nldo, "vdd_l2_l3_l4_l27" },
	{ "l3", QCOM_SMD_RPM_LDOA, 3, &pma8084_nldo, "vdd_l2_l3_l4_l27" },
	{ "l4", QCOM_SMD_RPM_LDOA, 4, &pma8084_nldo, "vdd_l2_l3_l4_l27" },
	{ "l5", QCOM_SMD_RPM_LDOA, 5, &pma8084_pldo, "vdd_l5_l7" },
	{ "l6", QCOM_SMD_RPM_LDOA, 6, &pma8084_pldo, "vdd_l6_l12_l14_l15_l26" },
	{ "l7", QCOM_SMD_RPM_LDOA, 7, &pma8084_pldo, "vdd_l5_l7" },
	{ "l8", QCOM_SMD_RPM_LDOA, 8, &pma8084_pldo, "vdd_l8" },
	{ "l9", QCOM_SMD_RPM_LDOA, 9, &pma8084_pldo, "vdd_l9_l10_l13_l20_l23_l24" },
	{ "l10", QCOM_SMD_RPM_LDOA, 10, &pma8084_pldo, "vdd_l9_l10_l13_l20_l23_l24" },
	{ "l11", QCOM_SMD_RPM_LDOA, 11, &pma8084_nldo, "vdd_l1_l11" },
	{ "l12", QCOM_SMD_RPM_LDOA, 12, &pma8084_pldo, "vdd_l6_l12_l14_l15_l26" },
	{ "l13", QCOM_SMD_RPM_LDOA, 13, &pma8084_pldo, "vdd_l9_l10_l13_l20_l23_l24" },
	{ "l14", QCOM_SMD_RPM_LDOA, 14, &pma8084_pldo, "vdd_l6_l12_l14_l15_l26" },
	{ "l15", QCOM_SMD_RPM_LDOA, 15, &pma8084_pldo, "vdd_l6_l12_l14_l15_l26" },
	{ "l16", QCOM_SMD_RPM_LDOA, 16, &pma8084_pldo, "vdd_l16_l25" },
	{ "l17", QCOM_SMD_RPM_LDOA, 17, &pma8084_pldo, "vdd_l17" },
	{ "l18", QCOM_SMD_RPM_LDOA, 18, &pma8084_pldo, "vdd_l18" },
	{ "l19", QCOM_SMD_RPM_LDOA, 19, &pma8084_pldo, "vdd_l19" },
	{ "l20", QCOM_SMD_RPM_LDOA, 20, &pma8084_pldo, "vdd_l9_l10_l13_l20_l23_l24" },
	{ "l21", QCOM_SMD_RPM_LDOA, 21, &pma8084_pldo, "vdd_l21" },
	{ "l22", QCOM_SMD_RPM_LDOA, 22, &pma8084_pldo, "vdd_l22" },
	{ "l23", QCOM_SMD_RPM_LDOA, 23, &pma8084_pldo, "vdd_l9_l10_l13_l20_l23_l24" },
	{ "l24", QCOM_SMD_RPM_LDOA, 24, &pma8084_pldo, "vdd_l9_l10_l13_l20_l23_l24" },
	{ "l25", QCOM_SMD_RPM_LDOA, 25, &pma8084_pldo, "vdd_l16_l25" },
	{ "l26", QCOM_SMD_RPM_LDOA, 26, &pma8084_pldo, "vdd_l6_l12_l14_l15_l26" },
	{ "l27", QCOM_SMD_RPM_LDOA, 27, &pma8084_nldo, "vdd_l2_l3_l4_l27" },

	{ "lvs1", QCOM_SMD_RPM_VSA, 1, &pma8084_switch },
	{ "lvs2", QCOM_SMD_RPM_VSA, 2, &pma8084_switch },
	{ "lvs3", QCOM_SMD_RPM_VSA, 3, &pma8084_switch },
	{ "lvs4", QCOM_SMD_RPM_VSA, 4, &pma8084_switch },
	{ "5vs1", QCOM_SMD_RPM_VSA, 5, &pma8084_switch },

	{}
};

static const struct rpm_regulator_data rpm_pm8994_regulators[] = {
	{ "s1", QCOM_SMD_RPM_SMPA, 1, &pm8994_ftsmps, "vdd_s1" },
	{ "s2", QCOM_SMD_RPM_SMPA, 2, &pm8994_ftsmps, "vdd_s2" },
	{ "s3", QCOM_SMD_RPM_SMPA, 3, &pm8994_hfsmps, "vdd_s3" },
	{ "s4", QCOM_SMD_RPM_SMPA, 4, &pm8994_hfsmps, "vdd_s4" },
	{ "s5", QCOM_SMD_RPM_SMPA, 5, &pm8994_hfsmps, "vdd_s5" },
	{ "s6", QCOM_SMD_RPM_SMPA, 6, &pm8994_ftsmps, "vdd_s6" },
	{ "s7", QCOM_SMD_RPM_SMPA, 7, &pm8994_hfsmps, "vdd_s7" },
	{ "s8", QCOM_SMD_RPM_SMPA, 8, &pm8994_ftsmps, "vdd_s8" },
	{ "s9", QCOM_SMD_RPM_SMPA, 9, &pm8994_ftsmps, "vdd_s9" },
	{ "s10", QCOM_SMD_RPM_SMPA, 10, &pm8994_ftsmps, "vdd_s10" },
	{ "s11", QCOM_SMD_RPM_SMPA, 11, &pm8994_ftsmps, "vdd_s11" },
	{ "s12", QCOM_SMD_RPM_SMPA, 12, &pm8994_ftsmps, "vdd_s12" },
	{ "l1", QCOM_SMD_RPM_LDOA, 1, &pm8994_nldo, "vdd_l1" },
	{ "l2", QCOM_SMD_RPM_LDOA, 2, &pm8994_nldo, "vdd_l2_l26_l28" },
	{ "l3", QCOM_SMD_RPM_LDOA, 3, &pm8994_nldo, "vdd_l3_l11" },
	{ "l4", QCOM_SMD_RPM_LDOA, 4, &pm8994_nldo, "vdd_l4_l27_l31" },
	{ "l5", QCOM_SMD_RPM_LDOA, 5, &pm8994_lnldo, "vdd_l5_l7" },
	{ "l6", QCOM_SMD_RPM_LDOA, 6, &pm8994_pldo, "vdd_l6_l12_l32" },
	{ "l7", QCOM_SMD_RPM_LDOA, 7, &pm8994_lnldo, "vdd_l5_l7" },
	{ "l8", QCOM_SMD_RPM_LDOA, 8, &pm8994_pldo, "vdd_l8_l16_l30" },
	{ "l9", QCOM_SMD_RPM_LDOA, 9, &pm8994_pldo, "vdd_l9_l10_l18_l22" },
	{ "l10", QCOM_SMD_RPM_LDOA, 10, &pm8994_pldo, "vdd_l9_l10_l18_l22" },
	{ "l11", QCOM_SMD_RPM_LDOA, 11, &pm8994_nldo, "vdd_l3_l11" },
	{ "l12", QCOM_SMD_RPM_LDOA, 12, &pm8994_pldo, "vdd_l6_l12_l32" },
	{ "l13", QCOM_SMD_RPM_LDOA, 13, &pm8994_pldo, "vdd_l13_l19_l23_l24" },
	{ "l14", QCOM_SMD_RPM_LDOA, 14, &pm8994_pldo, "vdd_l14_l15" },
	{ "l15", QCOM_SMD_RPM_LDOA, 15, &pm8994_pldo, "vdd_l14_l15" },
	{ "l16", QCOM_SMD_RPM_LDOA, 16, &pm8994_pldo, "vdd_l8_l16_l30" },
	{ "l17", QCOM_SMD_RPM_LDOA, 17, &pm8994_pldo, "vdd_l17_l29" },
	{ "l18", QCOM_SMD_RPM_LDOA, 18, &pm8994_pldo, "vdd_l9_l10_l18_l22" },
	{ "l19", QCOM_SMD_RPM_LDOA, 19, &pm8994_pldo, "vdd_l13_l19_l23_l24" },
	{ "l20", QCOM_SMD_RPM_LDOA, 20, &pm8994_pldo, "vdd_l20_l21" },
	{ "l21", QCOM_SMD_RPM_LDOA, 21, &pm8994_pldo, "vdd_l20_l21" },
	{ "l22", QCOM_SMD_RPM_LDOA, 22, &pm8994_pldo, "vdd_l9_l10_l18_l22" },
	{ "l23", QCOM_SMD_RPM_LDOA, 23, &pm8994_pldo, "vdd_l13_l19_l23_l24" },
	{ "l24", QCOM_SMD_RPM_LDOA, 24, &pm8994_pldo, "vdd_l13_l19_l23_l24" },
	{ "l25", QCOM_SMD_RPM_LDOA, 25, &pm8994_pldo, "vdd_l25" },
	{ "l26", QCOM_SMD_RPM_LDOA, 26, &pm8994_nldo, "vdd_l2_l26_l28" },
	{ "l27", QCOM_SMD_RPM_LDOA, 27, &pm8994_nldo, "vdd_l4_l27_l31" },
	{ "l28", QCOM_SMD_RPM_LDOA, 28, &pm8994_nldo, "vdd_l2_l26_l28" },
	{ "l29", QCOM_SMD_RPM_LDOA, 29, &pm8994_pldo, "vdd_l17_l29" },
	{ "l30", QCOM_SMD_RPM_LDOA, 30, &pm8994_pldo, "vdd_l8_l16_l30" },
	{ "l31", QCOM_SMD_RPM_LDOA, 31, &pm8994_nldo, "vdd_l4_l27_l31" },
	{ "l32", QCOM_SMD_RPM_LDOA, 32, &pm8994_pldo, "vdd_l6_l12_l32" },
	{ "lvs1", QCOM_SMD_RPM_VSA, 1, &pm8994_switch, "vdd_lvs1_2" },
	{ "lvs2", QCOM_SMD_RPM_VSA, 2, &pm8994_switch, "vdd_lvs1_2" },

	{}
};

static const struct rpm_regulator_data rpm_pm8998_regulators[] = {
	{ "s1", QCOM_SMD_RPM_SMPA, 1, &pm8998_ftsmps, "vdd_s1" },
	{ "s2", QCOM_SMD_RPM_SMPA, 2, &pm8998_ftsmps, "vdd_s2" },
	{ "s3", QCOM_SMD_RPM_SMPA, 3, &pm8998_hfsmps, "vdd_s3" },
	{ "s4", QCOM_SMD_RPM_SMPA, 4, &pm8998_hfsmps, "vdd_s4" },
	{ "s5", QCOM_SMD_RPM_SMPA, 5, &pm8998_hfsmps, "vdd_s5" },
	{ "s6", QCOM_SMD_RPM_SMPA, 6, &pm8998_ftsmps, "vdd_s6" },
	{ "s7", QCOM_SMD_RPM_SMPA, 7, &pm8998_ftsmps, "vdd_s7" },
	{ "s8", QCOM_SMD_RPM_SMPA, 8, &pm8998_ftsmps, "vdd_s8" },
	{ "s9", QCOM_SMD_RPM_SMPA, 9, &pm8998_ftsmps, "vdd_s9" },
	{ "s10", QCOM_SMD_RPM_SMPA, 10, &pm8998_ftsmps, "vdd_s10" },
	{ "s11", QCOM_SMD_RPM_SMPA, 11, &pm8998_ftsmps, "vdd_s11" },
	{ "s12", QCOM_SMD_RPM_SMPA, 12, &pm8998_ftsmps, "vdd_s12" },
	{ "s13", QCOM_SMD_RPM_SMPA, 13, &pm8998_ftsmps, "vdd_s13" },
	{ "l1", QCOM_SMD_RPM_LDOA, 1, &pm8998_nldo, "vdd_l1_l27" },
	{ "l2", QCOM_SMD_RPM_LDOA, 2, &pm8998_nldo, "vdd_l2_l8_l17" },
	{ "l3", QCOM_SMD_RPM_LDOA, 3, &pm8998_nldo, "vdd_l3_l11" },
	{ "l4", QCOM_SMD_RPM_LDOA, 4, &pm8998_nldo, "vdd_l4_l5" },
	{ "l5", QCOM_SMD_RPM_LDOA, 5, &pm8998_nldo, "vdd_l4_l5" },
	{ "l6", QCOM_SMD_RPM_LDOA, 6, &pm8998_pldo, "vdd_l6" },
	{ "l7", QCOM_SMD_RPM_LDOA, 7, &pm8998_pldo_lv, "vdd_l7_l12_l14_l15" },
	{ "l8", QCOM_SMD_RPM_LDOA, 8, &pm8998_nldo, "vdd_l2_l8_l17" },
	{ "l9", QCOM_SMD_RPM_LDOA, 9, &pm8998_pldo, "vdd_l9" },
	{ "l10", QCOM_SMD_RPM_LDOA, 10, &pm8998_pldo, "vdd_l10_l23_l25" },
	{ "l11", QCOM_SMD_RPM_LDOA, 11, &pm8998_nldo, "vdd_l3_l11" },
	{ "l12", QCOM_SMD_RPM_LDOA, 12, &pm8998_pldo_lv, "vdd_l7_l12_l14_l15" },
	{ "l13", QCOM_SMD_RPM_LDOA, 13, &pm8998_pldo, "vdd_l13_l19_l21" },
	{ "l14", QCOM_SMD_RPM_LDOA, 14, &pm8998_pldo_lv, "vdd_l7_l12_l14_l15" },
	{ "l15", QCOM_SMD_RPM_LDOA, 15, &pm8998_pldo_lv, "vdd_l7_l12_l14_l15" },
	{ "l16", QCOM_SMD_RPM_LDOA, 16, &pm8998_pldo, "vdd_l16_l28" },
	{ "l17", QCOM_SMD_RPM_LDOA, 17, &pm8998_nldo, "vdd_l2_l8_l17" },
	{ "l18", QCOM_SMD_RPM_LDOA, 18, &pm8998_pldo, "vdd_l18_l22" },
	{ "l19", QCOM_SMD_RPM_LDOA, 19, &pm8998_pldo, "vdd_l13_l19_l21" },
	{ "l20", QCOM_SMD_RPM_LDOA, 20, &pm8998_pldo, "vdd_l20_l24" },
	{ "l21", QCOM_SMD_RPM_LDOA, 21, &pm8998_pldo, "vdd_l13_l19_l21" },
	{ "l22", QCOM_SMD_RPM_LDOA, 22, &pm8998_pldo, "vdd_l18_l22" },
	{ "l23", QCOM_SMD_RPM_LDOA, 23, &pm8998_pldo, "vdd_l10_l23_l25" },
	{ "l24", QCOM_SMD_RPM_LDOA, 24, &pm8998_pldo, "vdd_l20_l24" },
	{ "l25", QCOM_SMD_RPM_LDOA, 25, &pm8998_pldo, "vdd_l10_l23_l25" },
	{ "l26", QCOM_SMD_RPM_LDOA, 26, &pm8998_nldo, "vdd_l26" },
	{ "l27", QCOM_SMD_RPM_LDOA, 27, &pm8998_nldo, "vdd_l1_l27" },
	{ "l28", QCOM_SMD_RPM_LDOA, 28, &pm8998_pldo, "vdd_l16_l28" },
	{ "lvs1", QCOM_SMD_RPM_VSA, 1, &pm8998_switch, "vdd_lvs1_lvs2" },
	{ "lvs2", QCOM_SMD_RPM_VSA, 2, &pm8998_switch, "vdd_lvs1_lvs2" },
	{}
};

static const struct rpm_regulator_data rpm_pmi8998_regulators[] = {
	{ "bob", QCOM_SMD_RPM_BOBB, 1, &pmi8998_bob, "vdd_bob" },
	{}
};

static const struct of_device_id rpm_of_match[] = {
	{ .compatible = "qcom,rpm-pm8841-regulators", .data = &rpm_pm8841_regulators },
	{ .compatible = "qcom,rpm-pm8916-regulators", .data = &rpm_pm8916_regulators },
	{ .compatible = "qcom,rpm-pm8941-regulators", .data = &rpm_pm8941_regulators },
	{ .compatible = "qcom,rpm-pm8994-regulators", .data = &rpm_pm8994_regulators },
	{ .compatible = "qcom,rpm-pm8998-regulators", .data = &rpm_pm8998_regulators },
	{ .compatible = "qcom,rpm-pma8084-regulators", .data = &rpm_pma8084_regulators },
	{ .compatible = "qcom,rpm-pmi8998-regulators", .data = &rpm_pmi8998_regulators },
	{}
};
MODULE_DEVICE_TABLE(of, rpm_of_match);

/* Callback storage outlives bank devres whenever a consumer still holds rdev. */
static void rpm_reg_release(struct kref *ref)
{
	struct qcom_rpm_reg *vreg = container_of(ref, struct qcom_rpm_reg, refs);

	kfree(vreg);
}

static void rpm_reg_put(void *data)
{
	struct qcom_rpm_reg *vreg = data;

	kref_put(&vreg->refs, rpm_reg_release);
}

static void rpm_reg_rdev_release(struct device *dev, void *data)
{
	struct qcom_rpm_reg **vreg = data;

	rpm_reg_put(*vreg);
}

static void rpm_reg_unregister(void *data)
{
	struct qcom_rpm_reg *vreg = data;
	bool retain_parent;

	/* No consumer can race another transport call after this lock barrier. */
	mutex_lock(&vreg->lock);
	vreg->online = false;
	/* A surviving consumer enters core supply code before our callback.
	 * A declared supply can resolve concurrently, so use its immutable
	 * declaration rather than a racy supply/use_count snapshot. Retain the
	 * built-in registration even when this child has not sent a request.
	 */
	retain_parent = IS_BUILTIN(CONFIG_REGULATOR_QCOM_SMD_RPM) &&
		vreg->rdev && vreg->desc.supply_name;
	if (vreg->claimed) {
		qcom_rpm_smd_release(vreg->rpm, vreg->type, vreg->id,
				     vreg, vreg->attempted || retain_parent);
		vreg->claimed = false;
	}
	/* Core unregister withdraws upstream enable references. For a built-in
	 * legacy provider whose hardware may still require its Linux parent,
	 * retain one bounded inert registration instead of dropping that lease.
	 * Transactional Dora rails use the separately audited firmware-input
	 * contract and have no invented Linux supply edge.
	 */
	mutex_unlock(&vreg->lock);
	if (vreg->rdev && !retain_parent) {
		regulator_unregister(vreg->rdev);
		vreg->rdev = NULL;
	}
	rpm_reg_put(vreg);
}

static int rpm_regulator_init_vreg(struct device *dev, struct device_node *node,
		struct qcom_smd_rpm *rpm, const struct rpm_regulator_data *pmic_data)
{
	const struct rpm_regulator_data *data;
	struct regulator_config config = { };
	struct qcom_rpm_reg *vreg, **ref;
	struct regulator_dev *rdev;
	int ret;

	for (data = pmic_data; data->name; data++)
		if (of_node_name_eq(node, data->name))
			break;
	if (!data->name)
		return -EINVAL;
	if (of_property_read_bool(node, "qcom,active-sleep-votes")) {
		if (!IS_BUILTIN(CONFIG_REGULATOR_QCOM_SMD_RPM) ||
		    !of_device_is_compatible(dev->of_node,
					     "qcom,rpm-pm8994-regulators") ||
		    !of_find_property(dev->of_node, "qcom,rpm", NULL))
			return -EINVAL;
		return qcom_rpm_physical_register(dev, node, rpm, data->type,
				data->id, data->desc, data->supply);
	}

	vreg = kzalloc(sizeof(*vreg), GFP_KERNEL);
	if (!vreg)
		return -ENOMEM;
	mutex_init(&vreg->lock);
	kref_init(&vreg->refs);
	vreg->dev = dev;
	vreg->rpm = rpm;
	vreg->type = data->type;
	vreg->id = data->id;
	vreg->desc = *data->desc;
	vreg->desc.name = data->name;
	vreg->desc.supply_name = data->supply;
	vreg->desc.owner = THIS_MODULE;
	vreg->desc.type = REGULATOR_VOLTAGE;
	vreg->desc.of_match = data->name;
	ret = qcom_rpm_smd_claim(rpm, data->type, data->id, vreg);
	if (ret)
		goto put;
	vreg->claimed = true;
	/* Preallocate final-rdev ownership before publication. Unlike the
	 * transactional mode, legacy machine constraints may send requests.
	 */
	ref = devres_alloc(rpm_reg_rdev_release, sizeof(*ref), GFP_KERNEL);
	if (!ref) {
		ret = -ENOMEM;
		goto retire;
	}
	vreg->online = true;
	config.dev = dev;
	config.of_node = node;
	config.driver_data = vreg;
	rdev = regulator_register(&vreg->desc, &config);
	if (IS_ERR(rdev)) {
		ret = PTR_ERR(rdev);
		devres_free(ref);
		goto retire;
	}
	kref_get(&vreg->refs);
	*ref = vreg;
	devres_add(&rdev->dev, ref);
	vreg->rdev = rdev;
	return devm_add_action_or_reset(dev, rpm_reg_unregister, vreg);
retire:
	rpm_reg_unregister(vreg);
	return ret;
put:
	rpm_reg_put(vreg);
	return ret;
}

static int rpm_reg_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	const struct rpm_regulator_data *vreg_data;
	struct device_node *node;
	struct qcom_smd_rpm *rpm;
	int ret;

	/* A module cannot host transactional votes. Reject the whole bank
	 * before legacy siblings can publish and apply machine constraints.
	 */
	if (!IS_BUILTIN(CONFIG_REGULATOR_QCOM_SMD_RPM)) {
		for_each_available_child_of_node(dev->of_node, node) {
			if (of_property_read_bool(node, "qcom,active-sleep-votes")) {
				of_node_put(node);
				return -EINVAL;
			}
		}
	}

	if (of_find_property(dev->of_node, "qcom,rpm", NULL)) {
		rpm = qcom_smd_rpm_get(dev);
		if (IS_ERR(rpm))
			return PTR_ERR(rpm);
	} else {
		rpm = dev_get_drvdata(pdev->dev.parent);
		if (!rpm)
			return -EPROBE_DEFER;
	}

	vreg_data = of_device_get_match_data(dev);
	if (!vreg_data)
		return -ENODEV;

	for_each_available_child_of_node(dev->of_node, node) {
		ret = rpm_regulator_init_vreg(dev, node, rpm, vreg_data);

		if (ret < 0) {
			of_node_put(node);
			return ret;
		}
	}

	return qcom_rpm_physical_activate(dev);
}

static int __maybe_unused rpm_reg_suspend(struct device *dev)
{
	return qcom_rpm_physical_suspend(dev);
}

static int __maybe_unused rpm_reg_resume(struct device *dev)
{
	return qcom_rpm_physical_resume(dev);
}

static const struct dev_pm_ops rpm_reg_pm_ops = {
	SET_LATE_SYSTEM_SLEEP_PM_OPS(rpm_reg_suspend, rpm_reg_resume)
};

static struct platform_driver rpm_reg_driver = {
	.probe = rpm_reg_probe,
	.driver = {
		.name  = "qcom_rpm_smd_regulator",
		.of_match_table = rpm_of_match,
		.pm = &rpm_reg_pm_ops,
		.suppress_bind_attrs = true,
	},
};

static int __init rpm_reg_init(void)
{
	return platform_driver_register(&rpm_reg_driver);
}
subsys_initcall(rpm_reg_init);

static void __exit rpm_reg_exit(void)
{
	platform_driver_unregister(&rpm_reg_driver);
}
module_exit(rpm_reg_exit)

MODULE_DESCRIPTION("Qualcomm RPM regulator driver");
MODULE_LICENSE("GPL v2");
