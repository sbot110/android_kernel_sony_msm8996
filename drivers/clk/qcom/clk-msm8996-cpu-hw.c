// SPDX-License-Identifier: GPL-2.0-only
/* Sony 4.4 clock-cpu-8996.c: EFUSE[31:29], common PWR/PERF/CBF bin,
 * pvs_ver=0. Unlike the old driver, never fall back to bin zero.
 * This completes only fuse/plan acquisition, not the writable CPU adapter.
 */
#include <linux/device.h>
#include <linux/err.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/slab.h>

#include "clk-msm8996-cpu-hw.h"

int msm8996_cpu_hw_read_plans(struct device *dev,
			    struct msm8996_cpu_hw_plans *plans)
{
	static const char * const domain[] = { "pwrcl", "perfcl", "cbf" };
	struct msm8996_cpu_hw_plans selected = { };
	struct msm8996_cpu_plan check;
	struct nvmem_cell *cell;
	char name[32];
	void *value;
	size_t length = 0;
	unsigned int i, j;
	u32 rate, level;
	int ret, cells;

	if (!dev || !plans)
		return -EINVAL;
	cell = nvmem_cell_get(dev, "speedbin");
	if (IS_ERR(cell))
		return PTR_ERR(cell);
	value = nvmem_cell_read(cell, &length);
	nvmem_cell_put(cell);
	if (IS_ERR(value))
		return PTR_ERR(value);
	/* The cell covers byte0x133 bits5:7, i.e. word0x130 bits29:31.
	 * NVMEM has already extracted the bits; never shift them twice.
	 */
	ret = -EINVAL;
	if (length == 1) {
		selected.speed_bin = *(u8 *)value;
		if (selected.speed_bin <= 3)
			ret = 0;
	}
	kfree(value);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(domain); i++) {
		snprintf(name, sizeof(name), "qcom,%s-speedbin%u-v0",
			 domain[i], selected.speed_bin);
		cells = of_property_count_u32_elems(dev->of_node, name);
		/* Bound allocation independently of an untrusted/malformed DT.
		 * All twelve original plans fit within 32 pairs.
		 */
		if (cells < 4 || cells > 64 || cells % 2) {
			ret = cells < 0 ? cells : -EINVAL;
			goto free_plans;
		}
		selected.count[i] = cells / 2;
		selected.domain[i] = devm_kcalloc(dev, selected.count[i],
					 sizeof(*selected.domain[i]), GFP_KERNEL);
		if (!selected.domain[i]) {
			ret = -ENOMEM;
			goto free_plans;
		}
		for (j = 0; j < selected.count[i]; j++) {
			ret = of_property_read_u32_index(dev->of_node, name, 2 * j,
						 &rate);
			if (!ret)
				ret = of_property_read_u32_index(dev->of_node, name,
							 2 * j + 1, &level);
			if (ret)
				goto free_plans;
			selected.domain[i][j].rate = rate;
			selected.domain[i][j].level = level;
		}
		/* Execute the transaction engine's complete plan validator. */
		ret = msm8996_cpu_plan_lookup(selected.domain[i], selected.count[i],
			 selected.speed_bin, selected.domain[i][1].rate, false, &check);
		if (ret)
			goto free_plans;
	}
	*plans = selected;
	return 0;

free_plans:
	for (i = 0; i < ARRAY_SIZE(domain); i++)
		if (selected.domain[i])
			devm_kfree(dev, selected.domain[i]);
	return ret;
}
