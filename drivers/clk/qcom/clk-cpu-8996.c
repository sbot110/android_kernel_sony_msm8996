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
#include "clk-msm8996-cpu-hw.h"

struct msm8996_boot_clk {
	struct clk_hw hw;
	void __iomem *base;
	struct clk *aux;
	bool cbf;
};

struct msm8996_boot_snapshot {
	u32 mux, mode, l, alpha, alpha_hi, user;
	unsigned long aux;
};

static bool msm8996_boot_uses_aux(struct msm8996_boot_clk *clk, u32 mux)
{
	return clk->cbf ? (mux & 3) == 3 :
		!(mux & 3) && ((mux >> 2) & 3) == 3;
}

static int msm8996_boot_snapshot(struct msm8996_boot_clk *clk,
				 struct msm8996_boot_snapshot *state)
{
	void __iomem *base = clk->base;
	u32 mux_offset = clk->cbf ? 0x18 : 0x40;

	state->mux = readl_relaxed(base + mux_offset);
	if (msm8996_boot_uses_aux(clk, state->mux)) {
		/* Sony sys_apcsaux_clk is GPLL0 main output / 2. GCC validates
		 * the fixed root at boot; this CCF rate may remain cached and
		 * cannot detect a later firmware root change or lock loss.
		 */
		state->aux = clk_get_rate(clk->aux);
	} else if (clk->cbf ? state->mux & 3 : state->mux & 0xf) {
		if (clk->cbf)
			base += 0xf000;
		else if ((state->mux & 3) == 3)
			base += 0x100;
		state->mode = readl_relaxed(base);
		state->l = readl_relaxed(base + (clk->cbf ? 0x08 : 0x04));
		state->alpha = readl_relaxed(base + (clk->cbf ? 0x10 : 0x08));
		/* The primary Huayra fraction occupies only the low 16 bits. */
		if (clk->cbf || (state->mux & 3) == 3)
			state->alpha_hi = readl_relaxed(base +
						(clk->cbf ? 0x14 : 0x0c));
		state->user = readl_relaxed(base + (clk->cbf ? 0x18 : 0x10));
	}
	/* Order the complete sample before rechecking its selected source. */
	rmb();
	return state->mux == readl_relaxed(clk->base + mux_offset) ? 0 : -EAGAIN;
}

static unsigned long msm8996_boot_recalc(struct clk_hw *hw,
				       unsigned long xo)
{
	struct msm8996_boot_clk *clk = container_of(hw,
					struct msm8996_boot_clk, hw);
	struct msm8996_boot_snapshot a = { }, b = { };
	u32 mux, user, alpha;
	u64 rate;
	bool main;
	int div;

	if (!xo || msm8996_boot_snapshot(clk, &a) ||
	    msm8996_boot_snapshot(clk, &b))
		return 0;
	/* A read-only observer cannot exclude firmware/power-controller writes.
	 * Reject inconsistent selected APCC/CBF PLL/mux samples. Auxiliary
	 * rates rely on GCC's fixed-root ownership and boot validation.
	 * This sampling is not a substitute for the transaction domain guard.
	 */
	if (a.mux != b.mux || a.mode != b.mode || a.l != b.l ||
	    a.alpha != b.alpha || a.alpha_hi != b.alpha_hi ||
	    a.user != b.user || a.aux != b.aux)
		return 0;
	mux = a.mux;
	if (msm8996_boot_uses_aux(clk, mux))
		return a.aux / 2;
	if (clk->cbf ? !(mux & 3) : !(mux & 0xf))
		return xo;
	if (!clk->cbf && !(mux & 3) && ((mux >> 2) & 3) != 1)
		return 0; /* Reserved SMUX input: never invent a frequency. */
	main = clk->cbf ? (mux & 3) == 2 : !(mux & 3);
	user = a.user;
	/* Original handoff requires OUTCTRL/BYPASSNL/RESET_N. Both PLL types
	 * use MODE[31] for lock and USER[0]/[3] for main/early output enable.
	 */
	if ((a.mode & 7) != 7 || !(a.mode & BIT(31)) ||
	    !(user & (main ? BIT(0) : BIT(3))) || (user & BIT(12)))
		return 0;
	alpha = a.alpha;
	if (clk->cbf || (mux & 3) == 3) {
		rate = (u64)xo * a.l;
		if (user & BIT(24)) {
			/* Native alpha PLL decoding keeps the high 32 fraction bits. */
			alpha >>= 8;
			alpha |= (a.alpha_hi & 0xff) << 24;
			rate += (u64)xo * alpha >> 32;
		}
	} else if (msm8996_boot_huayra_rate(xo, a.l, alpha, user, &rate))
		return 0;
	if (main) {
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
	struct msm8996_cpu_hw_plans plans;
	const char *parent;
	struct clk *xo, *aux;
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
	/* GCC owns the fixed critical GPLL0 root. This handle only measures it;
	 * the observer neither acquires a hardware/voltage lease nor programs it.
	 * Resolve it before registering any clocks, including for APCC SMUX3.
	 */
	aux = devm_clk_get(dev, "aux");
	if (IS_ERR(aux))
		return PTR_ERR(aux);
	/* Optional on other boards; Dora supplies the real QFPROM cell.
	 * Resolve and validate all three source plans before publishing clocks.
	 * A fuse/provider error cannot become a guessed bin0 or voltage vote.
	 */
	if (!cbf && of_property_read_bool(dev->of_node, "nvmem-cells")) {
		ret = msm8996_cpu_hw_read_plans(dev, &plans);
		if (ret)
			return ret;
		dev_info(dev, "QFPROM CPU speed bin %u; CPR/SPM ownership unavailable\n",
			 plans.speed_bin);
	}
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
		clks[i].aux = aux;
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
