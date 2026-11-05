/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __QCOM_MSM8996_CPU_HW_H__
#define __QCOM_MSM8996_CPU_HW_H__

#include "clk-msm8996-cpu-transaction.h"

struct device;

struct msm8996_cpu_hw_plans {
	unsigned int speed_bin;
	struct msm8996_cpu_plan *domain[3]; /* PWR, PERF, CBF */
	unsigned int count[3];
};

/* Read-only QFPROM adapter. It does not establish CPR/SPM ownership. */
int msm8996_cpu_hw_read_plans(struct device *dev,
			    struct msm8996_cpu_hw_plans *plans);

#endif
