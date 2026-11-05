/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __QCOM_MSM8996_CPU_NATIVE_H__
#define __QCOM_MSM8996_CPU_NATIVE_H__

#include <linux/io.h>
#include "clk-msm8996-cpu-hw.h"

struct regmap;
struct msm8996_cpu_native;

/* A real backend must exclude autonomous CPR, SAW/APM, collapse and hotplug
 * for the entire shared S8-S11 gang, both clusters AND CBF, and retain
 * counted clock-parent preparations throughout every hardware operation.
 * The guard must
 * survive quarantine/recovery across callers; it cannot be a task-owned
 * mutex held across return. Each of six leases covers primary + digital
 * supplies with actual acknowledged state. There is NO in-tree backend yet.
 */
struct msm8996_cpu_native_power_ops {
	int (*enter)(void *data);
	void (*leave)(void *data);
	int (*vote)(void *data, unsigned int domain, unsigned int lease,
		    const struct msm8996_cpu_plan *plan, bool enable);
	int (*ready)(void *data);
};

struct msm8996_cpu_native_maps {
	void __iomem *apcc[2]; /* source APCC banks, each >=0x168 */
	void __iomem *cbf;     /* source CBF bank, >=0xf038 */
	void __iomem *aux;     /* source APCS_CSR+0x50 divider, >=4 */
	struct regmap *gcc;    /* existing GCC owner's map; READ ONLY here */
	unsigned long xo;      /* measured, pinned source XO parent */
};

struct msm8996_cpu_native_shared {
	struct mutex lock;
	struct msm8996_cpu_native_maps maps;
	struct msm8996_cpu_hw_plans plans;
	const struct msm8996_cpu_native_power_ops *power;
	void *power_data;
	struct msm8996_cpu_native *owner;
	bool initialized, frozen;
	bool bound[3];
	struct msm8996_cpu_native *binding[3];
	struct msm8996_cpu_transaction *transaction[3];
	unsigned long suspend_rate[3];
};

struct msm8996_cpu_native {
	struct msm8996_cpu_native_shared *shared;
	unsigned int domain; /* 0=PWR, 1=PERF, 2=CBF */
	unsigned long peer_rate[3];
};

/* Zero-initialized, permanently pinned owner/maps/plans required. Initialization
 * performs no MMIO writes. No DT driver, CCF write path or fake power provider
 * is installed by this API. Only Sony MSM8996-v3 integer PLL handoff supported.
 */
int msm8996_cpu_native_shared_init(struct msm8996_cpu_native_shared *shared,
		const struct msm8996_cpu_native_maps *maps,
		const struct msm8996_cpu_hw_plans *plans,
		const struct msm8996_cpu_native_power_ops *power, void *power_data);
int msm8996_cpu_native_bind(struct msm8996_cpu_native *native,
		struct msm8996_cpu_native_shared *shared, unsigned int domain,
		struct msm8996_cpu_transaction *txn);
int msm8996_cpu_native_suspend(struct msm8996_cpu_native_shared *shared);
int msm8996_cpu_native_resume(struct msm8996_cpu_native_shared *shared);

#endif
