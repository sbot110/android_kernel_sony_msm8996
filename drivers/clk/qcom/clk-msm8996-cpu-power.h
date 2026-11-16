/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __QCOM_MSM8996_CPU_POWER_H__
#define __QCOM_MSM8996_CPU_POWER_H__

#include <linux/types.h>
#include "clk-msm8996-cpu-hw.h"

struct device;
struct clk;
struct msm8996_cpu_power_port;

/* Shared, real resource ownership used by both live read-only providers.
 * This interface does not install native modifying callbacks or claim that
 * a software lock excludes firmware/SAW/LMH. There is no write-enable knob.
 */
struct msm8996_cpu_power_port *msm8996_cpu_power_attach(struct device *dev,
		bool cbf, struct clk *xo, struct clk *aux,
		const struct msm8996_cpu_hw_plans *plans);
/* Admission metadata only, without MMIO: transient shared freeze defers a
 * late probe; a permanent fault retains its original error.
 */
int msm8996_cpu_power_probe_status(void);
/* Pin an observer before any APCC/CBF MMIO, then release on every result.
 * A shared PM freeze returns -EBUSY without changing quarantine. NULL is
 * permitted for initial probe reads; on Dora these receive counted bootstrap
 * pins and obey the same shared freeze/fault. Non-Dora NULL calls bypass it.
 * This is MMIO lifetime/PM serialization, not autonomous voltage exclusion.
 */
int msm8996_cpu_power_read_begin(struct msm8996_cpu_power_port *port,
		unsigned int domain);
unsigned long msm8996_cpu_power_read_end(struct msm8996_cpu_power_port *port,
		unsigned int domain, unsigned long rate);
unsigned long msm8996_cpu_power_observe(struct msm8996_cpu_power_port *port,
		unsigned int domain, unsigned long rate);
void msm8996_cpu_power_retire(void *data);
int msm8996_cpu_power_suspend(struct msm8996_cpu_power_port *port);
int msm8996_cpu_power_resume(struct msm8996_cpu_power_port *port);
ssize_t msm8996_cpu_power_format(struct msm8996_cpu_power_port *port,
		char *buf, size_t size);

#endif
