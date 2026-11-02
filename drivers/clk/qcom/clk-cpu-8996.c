// SPDX-License-Identifier: GPL-2.0
/*
 * MSM8996 boot-clock observation for Sony Tone diagnostic bring-up.
 *
 * Register topology: Sony clock-cpu-8996.c, Copyright (c) 2015-2018,
 * The Linux Foundation, from 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1.
 * Huayra fractional decoding follows clk-alpha-pll.c in this kernel.
 *
 * This driver deliberately has no clock/voltage programming operations.
 * Sony's ACD must leave the primary PLL BEFORE a CPR voltage transition;
 * attaching its corner tables to a generic CCF rate notifier is unsafe.
 * A coordinated rate/voltage driver is required before enabling cpufreq.
 */
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/errno.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>

#include "clk-msm8996-boot.h"

struct msm8996_boot_clk {
	struct clk_hw hw;
	void __iomem *base;
	struct clk *aux;
	bool cbf;
};

static unsigned long msm8996_boot_recalc(struct clk_hw *hw,
				       unsigned long xo)
{
	struct msm8996_boot_clk *clk = container_of(hw,
					struct msm8996_boot_clk, hw);
	void __iomem *base = clk->base;
	u32 mux, user, alpha, l, offset;
	u64 rate;
	int div, ret;

	mux = readl_relaxed(base + (clk->cbf ? 0x18 : 0x40));
	if (clk->cbf) {
		switch (mux & 3) {
		case 0:
			return xo;
		case 3:
			/* Sony sys_apcsaux_clk is GPLL0 main output / 2. */
			return clk_get_rate(clk->aux) / 2;
		default:
			base += 0xf000;
			l = readl_relaxed(base + 0x08);
			user = readl_relaxed(base + 0x18);
			if (user & BIT(12))
				return 0;
			rate = (u64)xo * l;
			if (user & BIT(24)) {
				/* Alpha PLL has a 40-bit unsigned fraction. */
				alpha = readl_relaxed(base + 0x10) >> 8;
				alpha |= (readl_relaxed(base + 0x14) & 0xff) << 24;
				rate += (u64)xo * alpha >> 32;
			}
			if ((mux & 3) == 2) {
				div = msm8996_boot_main_div(user);
				if (div < 0)
					return 0;
				rate /= div;
			}
			return rate;
		}
	}

	/* PMUX: SMUX, primary early PLL, primary ACD (1:1), alt early PLL. */
	if (!(mux & 3) && !((mux >> 2) & 3))
		return xo;
	if (!(mux & 3) && ((mux >> 2) & 3) != 1)
		return 0; /* Reserved SMUX input: never invent a frequency. */
	offset = (mux & 3) == 3 ? 0x100 : 0;
	l = readl_relaxed(base + offset + 0x04);
	alpha = readl_relaxed(base + offset + 0x08);
	user = readl_relaxed(base + offset + 0x10);
	if (offset) {
		if (user & BIT(12))
			return 0;
		rate = (u64)xo * l;
		if (user & BIT(24)) {
			alpha >>= 8;
			alpha |= (readl_relaxed(base + offset + 0x0c) & 0xff) << 24;
			rate += (u64)xo * alpha >> 32;
		}
	} else {
		ret = msm8996_boot_huayra_rate(xo, l, alpha, user, &rate);
		if (ret)
			return 0;
	}
	if (!(mux & 3)) {
		div = msm8996_boot_main_div(user);
		if (div < 0)
			return 0;
		rate /= div;
	}
	return rate;
}

static const struct clk_ops msm8996_boot_ops = {
	.recalc_rate = msm8996_boot_recalc,
};

static int msm8996_boot_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct clk_hw_onecell_data *data;
	struct msm8996_boot_clk *clks;
	struct clk_init_data init = { };
	const char *parent;
	struct clk *xo;
	struct resource *res;
	void __iomem *base;
	unsigned int i, count;
	bool cbf = of_device_is_compatible(dev->of_node, "qcom,msm8996-cbf");
	int ret;

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res || resource_size(res) < (cbf ? 0xf038 : 0x80118))
		return -EINVAL;
	base = devm_ioremap_resource(dev, res);
	if (IS_ERR(base))
		return PTR_ERR(base);
	xo = devm_clk_get(dev, "xo");
	if (IS_ERR(xo))
		return PTR_ERR(xo);
	if (!clk_get_rate(xo))
		return -EINVAL;
	parent = __clk_get_name(xo);
	count = cbf ? 1 : 2;
	data = devm_kzalloc(dev, sizeof(*data) + count * sizeof(data->hws[0]),
			   GFP_KERNEL);
	clks = devm_kcalloc(dev, count, sizeof(*clks), GFP_KERNEL);
	if (!data || !clks)
		return -ENOMEM;
	data->num = count;
	for (i = 0; i < count; i++) {
		init.name = cbf ? "cbf_boot_clk" :
				 (i ? "perfcl_boot_clk" : "pwrcl_boot_clk");
		init.ops = &msm8996_boot_ops;
		init.flags = CLK_GET_RATE_NOCACHE;
		init.parent_names = &parent;
		init.num_parents = 1;
		clks[i].hw.init = &init;
		clks[i].base = base + i * 0x80000;
		clks[i].cbf = cbf;
		if (cbf) {
			clks[i].aux = devm_clk_get(dev, "aux");
			if (IS_ERR(clks[i].aux))
				return PTR_ERR(clks[i].aux);
		}
		ret = devm_clk_hw_register(dev, &clks[i].hw);
		if (ret)
			return ret;
		if (!clk_hw_get_rate(&clks[i].hw))
			return -EINVAL;
		data->hws[i] = &clks[i].hw;
	}
	ret = devm_of_clk_add_hw_provider(dev, of_clk_hw_onecell_get, data);
	if (!ret)
		dev_info(dev, "observing firmware clocks; DVFS is unavailable\n");
	return ret;
}

static const struct of_device_id msm8996_boot_match[] = {
	{ .compatible = "qcom,apcc-msm8996" },
	{ .compatible = "qcom,msm8996-apcc" },
	{ .compatible = "qcom,msm8996-cbf" },
	{ }
};
MODULE_DEVICE_TABLE(of, msm8996_boot_match);

static struct platform_driver msm8996_boot_driver = {
	.probe = msm8996_boot_probe,
	.driver = {
		.name = "msm8996-boot-clock",
		.of_match_table = msm8996_boot_match,
		.suppress_bind_attrs = true,
	},
};
builtin_platform_driver(msm8996_boot_driver);
MODULE_DESCRIPTION("MSM8996 read-only firmware APCC/CBF clocks");
MODULE_LICENSE("GPL v2");
