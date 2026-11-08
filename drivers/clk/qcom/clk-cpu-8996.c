// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (c) 2020, The Linux Foundation. All rights reserved.
 *
 * MSM8996 Kryo APCC CPU Clock Driver with HWFSM Alpha PLLs,
 * cluster-accurate L2 ACD initialization, AUTO_CLK_SEL / PSCTL setup,
 * and glitch-free SMUX/ALT_PLL transitions across 600 MHz.
 */

#include <linux/clk.h>
#include <linux/cpuhotplug.h>
#include <linux/clk-provider.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <asm/cputype.h>
#include <soc/qcom/kryo-l2-accessors.h>

#include "clk-alpha-pll.h"
#include "clk-regmap.h"

enum _pmux_input {
	DIV_2_INDEX = 0,
	PLL_INDEX,
	ACD_INDEX,
	ALT_INDEX,
	NUM_OF_PMUX_INPUTS
};

enum _smux_input {
	SMUX_XO_INDEX = 0,
	SMUX_PLL_DIV2_INDEX,
	NUM_OF_SMUX_INPUTS
};

#define DIV_2_THRESHOLD		600000000
#define PWRCL_REG_OFFSET	0x0
#define PERFCL_REG_OFFSET	0x80000
#define MUX_OFFSET		0x40
#define CLK_CTL_OFFSET		0x44
#define ALT_PLL_OFFSET		0x100
#define SSSCTL_OFFSET		0x160
#define PSCTL_OFFSET		0x164

#define MUX_AUTO_CLK_SEL_ALWAYS_ON_MASK		GENMASK(5, 4)
#define MUX_AUTO_CLK_SEL_ALWAYS_ON_GPLL0_SEL	(0x3 << 4)
#define CLK_CTL_AUTO_CLK_SEL			BIT(8)
#define PSCTL_DEFAULT_VAL			0x00030005

static const u8 prim_pll_regs[PLL_OFF_MAX_REGS] = {
	[PLL_OFF_L_VAL] = 0x04,
	[PLL_OFF_ALPHA_VAL] = 0x08,
	[PLL_OFF_USER_CTL] = 0x10,
	[PLL_OFF_CONFIG_CTL] = 0x18,
	[PLL_OFF_CONFIG_CTL_U] = 0x1c,
	[PLL_OFF_TEST_CTL] = 0x20,
	[PLL_OFF_TEST_CTL_U] = 0x24,
	[PLL_OFF_STATUS] = 0x28,
};

static const u8 alt_pll_regs[PLL_OFF_MAX_REGS] = {
	[PLL_OFF_L_VAL] = 0x04,
	[PLL_OFF_ALPHA_VAL] = 0x08,
	[PLL_OFF_ALPHA_VAL_U] = 0x0c,
	[PLL_OFF_USER_CTL] = 0x10,
	[PLL_OFF_USER_CTL_U] = 0x14,
	[PLL_OFF_CONFIG_CTL] = 0x18,
	[PLL_OFF_TEST_CTL] = 0x20,
	[PLL_OFF_TEST_CTL_U] = 0x24,
	[PLL_OFF_STATUS] = 0x28,
};

/* Primary Kryo HFPLLs (MSM8996 v3 silicon settings, safe 1036.8 MHz L=54 init) */
static const struct alpha_pll_config hfpll_config = {
	.l = 54,
	.config_ctl_val = 0x200d4828,
	.config_ctl_hi_val = 0x006,
	.test_ctl_val = 0x1c000000,
	.pre_div_mask = BIT(12),
	.post_div_mask = 0x3 << 8,
	.post_div_val = 0x1 << 8,
	.main_output_mask = BIT(0),
	.early_output_mask = BIT(3),
};

static struct clk_alpha_pll perfcl_pll = {
	.offset = PERFCL_REG_OFFSET,
	.regs = prim_pll_regs,
	.flags = SUPPORTS_DYNAMIC_UPDATE | SUPPORTS_FSM_MODE,
	.clkr.hw.init = &(struct clk_init_data){
		.name = "perfcl_pll",
		.parent_names = (const char *[]){ "xo" },
		.num_parents = 1,
		.ops = &clk_alpha_pll_hwfsm_ops,
	},
};

static struct clk_alpha_pll pwrcl_pll = {
	.offset = PWRCL_REG_OFFSET,
	.regs = prim_pll_regs,
	.flags = SUPPORTS_DYNAMIC_UPDATE | SUPPORTS_FSM_MODE,
	.clkr.hw.init = &(struct clk_init_data){
		.name = "pwrcl_pll",
		.parent_names = (const char *[]){ "xo" },
		.num_parents = 1,
		.ops = &clk_alpha_pll_hwfsm_ops,
	},
};

static const struct pll_vco alt_pll_vco_modes[] = {
	VCO(3,  250000000,  500000000),
	VCO(2,  500000000,  750000000),
	VCO(1,  750000000, 1000000000),
	VCO(0, 1000000000, 2150400000),
};

static const struct alpha_pll_config altpll_config = {
	.l = 16,
	.vco_val = 0x3 << 20,
	.vco_mask = 0x3 << 20,
	.config_ctl_val = 0x4001051b,
	.post_div_mask = 0x3 << 8,
	.post_div_val = 0x1 << 8,
	.main_output_mask = BIT(0),
	.early_output_mask = BIT(3),
};

static struct clk_alpha_pll perfcl_alt_pll = {
	.offset = PERFCL_REG_OFFSET + ALT_PLL_OFFSET,
	.regs = alt_pll_regs,
	.vco_table = alt_pll_vco_modes,
	.num_vco = ARRAY_SIZE(alt_pll_vco_modes),
	.flags = SUPPORTS_OFFLINE_REQ | SUPPORTS_FSM_MODE,
	.clkr.hw.init = &(struct clk_init_data) {
		.name = "perfcl_alt_pll",
		.parent_names = (const char *[]){ "xo" },
		.num_parents = 1,
		.ops = &clk_alpha_pll_hwfsm_ops,
	},
};

static struct clk_alpha_pll pwrcl_alt_pll = {
	.offset = PWRCL_REG_OFFSET + ALT_PLL_OFFSET,
	.regs = alt_pll_regs,
	.vco_table = alt_pll_vco_modes,
	.num_vco = ARRAY_SIZE(alt_pll_vco_modes),
	.flags = SUPPORTS_OFFLINE_REQ | SUPPORTS_FSM_MODE,
	.clkr.hw.init = &(struct clk_init_data) {
		.name = "pwrcl_alt_pll",
		.parent_names = (const char *[]){ "xo" },
		.num_parents = 1,
		.ops = &clk_alpha_pll_hwfsm_ops,
	},
};

struct clk_cpu_8996_mux {
	u32	reg;
	u8	shift;
	u8	width;
	struct notifier_block nb;
	struct clk_hw	*pll;
	struct clk_hw	*pll_acd;
	struct clk_hw	*pll_div_2;
	struct clk_regmap clkr;
};

static int cpu_clk_notifier_cb(struct notifier_block *nb, unsigned long event,
			       void *data);

#define to_clk_cpu_8996_mux_nb(_nb) \
	container_of(_nb, struct clk_cpu_8996_mux, nb)

static inline struct clk_cpu_8996_mux *to_clk_cpu_8996_mux_hw(struct clk_hw *hw)
{
	return container_of(to_clk_regmap(hw), struct clk_cpu_8996_mux, clkr);
}

static u8 clk_cpu_8996_mux_get_parent(struct clk_hw *hw)
{
	struct clk_regmap *clkr = to_clk_regmap(hw);
	struct clk_cpu_8996_mux *cpuclk = to_clk_cpu_8996_mux_hw(hw);
	u32 mask = GENMASK(cpuclk->width - 1, 0);
	u32 val = 0;

	regmap_read(clkr->regmap, cpuclk->reg, &val);
	val >>= cpuclk->shift;

	return val & mask;
}

static int clk_cpu_8996_mux_set_parent(struct clk_hw *hw, u8 index)
{
	struct clk_regmap *clkr = to_clk_regmap(hw);
	struct clk_cpu_8996_mux *cpuclk = to_clk_cpu_8996_mux_hw(hw);
	u32 mask = GENMASK(cpuclk->width + cpuclk->shift - 1, cpuclk->shift);
	u32 val;

	val = index;
	val <<= cpuclk->shift;

	return regmap_update_bits(clkr->regmap, cpuclk->reg, mask, val);
}

static int clk_cpu_8996_mux_determine_rate(struct clk_hw *hw,
					   struct clk_rate_request *req)
{
	struct clk_cpu_8996_mux *cpuclk = to_clk_cpu_8996_mux_hw(hw);
	struct clk_hw *parent = cpuclk->pll;

	if (cpuclk->pll_div_2 && req->rate < DIV_2_THRESHOLD) {
		if (req->rate < (DIV_2_THRESHOLD / 2))
			return -EINVAL;

		parent = cpuclk->pll_div_2;
	} else if (cpuclk->pll_acd) {
		parent = cpuclk->pll_acd;
	}

	req->best_parent_rate = clk_hw_round_rate(parent, req->rate);
	req->best_parent_hw = parent;

	return 0;
}

static const struct clk_ops clk_cpu_8996_mux_ops = {
	.set_parent = clk_cpu_8996_mux_set_parent,
	.get_parent = clk_cpu_8996_mux_get_parent,
	.determine_rate = clk_cpu_8996_mux_determine_rate,
};

static struct clk_cpu_8996_mux pwrcl_smux = {
	.reg = PWRCL_REG_OFFSET + MUX_OFFSET,
	.shift = 2,
	.width = 2,
	.clkr.hw.init = &(struct clk_init_data) {
		.name = "pwrcl_smux",
		.parent_names = (const char *[]){
			"xo",
			"pwrcl_pll_main",
		},
		.num_parents = 2,
		.ops = &clk_cpu_8996_mux_ops,
		.flags = CLK_SET_RATE_PARENT,
	},
};

static struct clk_cpu_8996_mux perfcl_smux = {
	.reg = PERFCL_REG_OFFSET + MUX_OFFSET,
	.shift = 2,
	.width = 2,
	.clkr.hw.init = &(struct clk_init_data) {
		.name = "perfcl_smux",
		.parent_names = (const char *[]){
			"xo",
			"perfcl_pll_main",
		},
		.num_parents = 2,
		.ops = &clk_cpu_8996_mux_ops,
		.flags = CLK_SET_RATE_PARENT,
	},
};

static struct clk_cpu_8996_mux pwrcl_pmux = {
	.reg = PWRCL_REG_OFFSET + MUX_OFFSET,
	.shift = 0,
	.width = 2,
	.pll = &pwrcl_pll.clkr.hw,
	.pll_div_2 = &pwrcl_smux.clkr.hw,
	.nb.notifier_call = cpu_clk_notifier_cb,
	.clkr.hw.init = &(struct clk_init_data) {
		.name = "pwrcl_pmux",
		.parent_names = (const char *[]){
			"pwrcl_smux",
			"pwrcl_pll",
			"pwrcl_pll_acd",
			"pwrcl_alt_pll",
		},
		.num_parents = 4,
		.ops = &clk_cpu_8996_mux_ops,
		/* CPU clock is critical and should never be gated */
		.flags = CLK_SET_RATE_PARENT | CLK_IS_CRITICAL,
	},
};

static struct clk_cpu_8996_mux perfcl_pmux = {
	.reg = PERFCL_REG_OFFSET + MUX_OFFSET,
	.shift = 0,
	.width = 2,
	.pll = &perfcl_pll.clkr.hw,
	.pll_div_2 = &perfcl_smux.clkr.hw,
	.nb.notifier_call = cpu_clk_notifier_cb,
	.clkr.hw.init = &(struct clk_init_data) {
		.name = "perfcl_pmux",
		.parent_names = (const char *[]){
			"perfcl_smux",
			"perfcl_pll",
			"perfcl_pll_acd",
			"perfcl_alt_pll",
		},
		.num_parents = 4,
		.ops = &clk_cpu_8996_mux_ops,
		/* CPU clock is critical and should never be gated */
		.flags = CLK_SET_RATE_PARENT | CLK_IS_CRITICAL,
	},
};

static const struct regmap_config cpu_msm8996_regmap_config = {
	.reg_bits		= 32,
	.reg_stride		= 4,
	.val_bits		= 32,
	.max_register		= 0x80210,
	.fast_io		= true,
	.val_format_endian	= REGMAP_ENDIAN_LITTLE,
};

static struct clk_regmap *cpu_msm8996_clks[] = {
	&perfcl_pll.clkr,
	&pwrcl_pll.clkr,
	&perfcl_alt_pll.clkr,
	&pwrcl_alt_pll.clkr,
	&perfcl_smux.clkr,
	&pwrcl_smux.clkr,
	&perfcl_pmux.clkr,
	&pwrcl_pmux.clkr,
};

#define CPU_CLUSTER_AFFINITY_MASK	0xf00
#define PWRCL_CLUSTER_ID		0x000
#define PERFCL_CLUSTER_ID		0x100

#define L2ACDCR_REG			0x580ULL
#define L2ACDTD_REG			0x581ULL
#define L2ACDDVMRC_REG			0x584ULL
#define L2ACDSSCR_REG			0x589ULL

#define L2ACDTD_INIT_VAL		0x00006a11ULL
#define L2ACDDVMRC_INIT_VAL		0x000e0f0fULL
#define L2ACDSSCR_INIT_VAL		0x00000601ULL
#define L2ACDCR_INIT_VAL		0x002c5ffdULL

static DEFINE_SPINLOCK(qcom_clk_acd_lock);
static void __iomem *base;
static enum cpuhp_state qcom_cpu_clk_cpuhp_state;

void qcom_cpu_clk_msm8996_acd_init(void __iomem *reg_base)
{
	u64 l2acdtd;
	u64 mpidr;
	u32 cluster;
	unsigned long flags;

	if (!reg_base)
		return;

	mpidr = read_cpuid_mpidr();
	cluster = mpidr & CPU_CLUSTER_AFFINITY_MASK;

	spin_lock_irqsave(&qcom_clk_acd_lock, flags);

	l2acdtd = kryo_l2_get_indirect_reg(L2ACDTD_REG);
	if (l2acdtd != L2ACDTD_INIT_VAL) {
		kryo_l2_set_indirect_reg(L2ACDTD_REG, L2ACDTD_INIT_VAL);
		kryo_l2_set_indirect_reg(L2ACDDVMRC_REG, L2ACDDVMRC_INIT_VAL);
		kryo_l2_set_indirect_reg(L2ACDSSCR_REG, L2ACDSSCR_INIT_VAL);
		kryo_l2_set_indirect_reg(L2ACDCR_REG, L2ACDCR_INIT_VAL);
	}

	/*
	 * Hardware safety rule: SSSCTL must only be written AFTER L2ACDCR
	 * is programmed in the cluster's local L2 controller, and only for
	 * the currently executing cluster.
	 */
	if (cluster == PERFCL_CLUSTER_ID)
		writel(0xf, reg_base + PERFCL_REG_OFFSET + SSSCTL_OFFSET);
	else
		writel(0xf, reg_base + PWRCL_REG_OFFSET + SSSCTL_OFFSET);

	spin_unlock_irqrestore(&qcom_clk_acd_lock, flags);
}

static int qcom_cpu_clk_msm8996_cpu_online(unsigned int cpu)
{
	if (base)
		qcom_cpu_clk_msm8996_acd_init(base);
	return 0;
}

static void qcom_cpu_clk_msm8996_unregister_fixed(void)
{
	if (qcom_cpu_clk_cpuhp_state > 0) {
		cpuhp_remove_state(qcom_cpu_clk_cpuhp_state);
		qcom_cpu_clk_cpuhp_state = 0;
	}
	if (!IS_ERR_OR_NULL(perfcl_pmux.pll_acd)) {
		clk_hw_unregister(perfcl_pmux.pll_acd);
		perfcl_pmux.pll_acd = NULL;
	}
	if (!IS_ERR_OR_NULL(pwrcl_pmux.pll_acd)) {
		clk_hw_unregister(pwrcl_pmux.pll_acd);
		pwrcl_pmux.pll_acd = NULL;
	}
	if (!IS_ERR_OR_NULL(perfcl_smux.pll)) {
		clk_hw_unregister(perfcl_smux.pll);
		perfcl_smux.pll = NULL;
	}
	if (!IS_ERR_OR_NULL(pwrcl_smux.pll)) {
		clk_hw_unregister(pwrcl_smux.pll);
		pwrcl_smux.pll = NULL;
	}
}

static int qcom_cpu_clk_msm8996_register_clks(struct device *dev,
					      struct regmap *regmap)
{
	int i, ret;

	perfcl_smux.pll = clk_hw_register_fixed_factor(dev, "perfcl_pll_main",
						       "perfcl_pll",
						       CLK_SET_RATE_PARENT,
						       1, 2);
	if (IS_ERR(perfcl_smux.pll)) {
		dev_err(dev, "Failed to initialize perfcl_pll_main\n");
		return PTR_ERR(perfcl_smux.pll);
	}

	pwrcl_smux.pll = clk_hw_register_fixed_factor(dev, "pwrcl_pll_main",
						      "pwrcl_pll",
						      CLK_SET_RATE_PARENT,
						      1, 2);
	if (IS_ERR(pwrcl_smux.pll)) {
		dev_err(dev, "Failed to initialize pwrcl_pll_main\n");
		ret = PTR_ERR(pwrcl_smux.pll);
		pwrcl_smux.pll = NULL;
		goto err_unreg;
	}

	perfcl_pmux.pll_acd = clk_hw_register_fixed_factor(dev,
							   "perfcl_pll_acd",
							   "perfcl_pll",
							   CLK_SET_RATE_PARENT,
							   1, 1);
	if (IS_ERR(perfcl_pmux.pll_acd)) {
		dev_err(dev, "Failed to initialize perfcl_pll_acd\n");
		ret = PTR_ERR(perfcl_pmux.pll_acd);
		perfcl_pmux.pll_acd = NULL;
		goto err_unreg;
	}

	pwrcl_pmux.pll_acd = clk_hw_register_fixed_factor(dev,
							  "pwrcl_pll_acd",
							  "pwrcl_pll",
							  CLK_SET_RATE_PARENT,
							  1, 1);
	if (IS_ERR(pwrcl_pmux.pll_acd)) {
		dev_err(dev, "Failed to initialize pwrcl_pll_acd\n");
		ret = PTR_ERR(pwrcl_pmux.pll_acd);
		pwrcl_pmux.pll_acd = NULL;
		goto err_unreg;
	}

	for (i = 0; i < ARRAY_SIZE(cpu_msm8996_clks); i++) {
		ret = devm_clk_register_regmap(dev, cpu_msm8996_clks[i]);
		if (ret)
			goto err_unreg;
	}

	/* Ensure always-on GPLL0/2 fallback source is selected for AUTO_CLK_SEL */
	ret = regmap_update_bits(regmap, PWRCL_REG_OFFSET + MUX_OFFSET,
				 MUX_AUTO_CLK_SEL_ALWAYS_ON_MASK,
				 MUX_AUTO_CLK_SEL_ALWAYS_ON_GPLL0_SEL);
	if (ret)
		goto err_unreg;

	ret = regmap_update_bits(regmap, PERFCL_REG_OFFSET + MUX_OFFSET,
				 MUX_AUTO_CLK_SEL_ALWAYS_ON_MASK,
				 MUX_AUTO_CLK_SEL_ALWAYS_ON_GPLL0_SEL);
	if (ret)
		goto err_unreg;

	clk_alpha_pll_configure(&perfcl_pll, regmap, &hfpll_config);
	clk_alpha_pll_configure(&pwrcl_pll, regmap, &hfpll_config);
	clk_alpha_pll_configure(&perfcl_alt_pll, regmap, &altpll_config);
	clk_alpha_pll_configure(&pwrcl_alt_pll, regmap, &altpll_config);

	/* Wait for HWFSM PLLs to settle, then enable AUTO_CLK_SEL and PSCTL */
	udelay(50);
	ret = regmap_write(regmap, PWRCL_REG_OFFSET + PSCTL_OFFSET, PSCTL_DEFAULT_VAL);
	if (ret)
		goto err_unreg;

	ret = regmap_write(regmap, PERFCL_REG_OFFSET + PSCTL_OFFSET, PSCTL_DEFAULT_VAL);
	if (ret)
		goto err_unreg;

	ret = regmap_update_bits(regmap, PWRCL_REG_OFFSET + CLK_CTL_OFFSET,
				 CLK_CTL_AUTO_CLK_SEL, CLK_CTL_AUTO_CLK_SEL);
	if (ret)
		goto err_unreg;

	ret = regmap_update_bits(regmap, PERFCL_REG_OFFSET + CLK_CTL_OFFSET,
				 CLK_CTL_AUTO_CLK_SEL, CLK_CTL_AUTO_CLK_SEL);
	if (ret)
		goto err_unreg;

	qcom_cpu_clk_msm8996_acd_init(base);

	ret = cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "clk/qcom/cpu8996:online",
				qcom_cpu_clk_msm8996_cpu_online, NULL);
	if (ret < 0) {
		dev_err(dev, "Failed to register cpuhp state: %d\n", ret);
		goto err_unreg;
	}
	qcom_cpu_clk_cpuhp_state = ret;

	/* Park secondary muxes on PLL/2 so sub-600 MHz OPPs are ready */
	ret = clk_cpu_8996_mux_set_parent(&pwrcl_smux.clkr.hw, SMUX_PLL_DIV2_INDEX);
	if (ret)
		goto err_unreg;

	ret = clk_cpu_8996_mux_set_parent(&perfcl_smux.clkr.hw, SMUX_PLL_DIV2_INDEX);
	if (ret)
		goto err_unreg;

	ret = clk_notifier_register(pwrcl_pmux.clkr.hw.clk, &pwrcl_pmux.nb);
	if (ret)
		goto err_unreg;

	ret = clk_notifier_register(perfcl_pmux.clkr.hw.clk, &perfcl_pmux.nb);
	if (ret) {
		clk_notifier_unregister(pwrcl_pmux.clkr.hw.clk, &pwrcl_pmux.nb);
		goto err_unreg;
	}

	return 0;

err_unreg:
	qcom_cpu_clk_msm8996_unregister_fixed();
	return ret;
}

static int qcom_cpu_clk_msm8996_unregister_clks(void)
{
	int ret, ret_perf;

	/*
	 * Unregister both notifiers before releasing their clock parents.
	 * A failure for one notifier must not skip the other cleanup.
	 */
	ret = clk_notifier_unregister(pwrcl_pmux.clkr.hw.clk, &pwrcl_pmux.nb);
	ret_perf = clk_notifier_unregister(perfcl_pmux.clkr.hw.clk,
					 &perfcl_pmux.nb);
	if (ret || ret_perf)
		return ret ? ret : ret_perf;

	qcom_cpu_clk_msm8996_unregister_fixed();

	return 0;
}

static int cpu_clk_notifier_cb(struct notifier_block *nb, unsigned long event,
			       void *data)
{
	struct clk_cpu_8996_mux *cpuclk = to_clk_cpu_8996_mux_nb(nb);
	struct clk_notifier_data *cnd = data;
	struct clk_cpu_8996_mux *smux =
		(cpuclk == &pwrcl_pmux) ? &pwrcl_smux : &perfcl_smux;
	int ret = 0;

	switch (event) {
	case PRE_RATE_CHANGE:
		qcom_cpu_clk_msm8996_acd_init(base);
		/*
		 * Avoid transient overclock on PLL/2 path when crossing 600 MHz
		 * while reprogramming the primary HFPLL: park PMUX on ALT_PLL
		 * and ensure SMUX stays on PLL/2 only after HFPLL settles.
		 */
		if (cnd->new_rate < DIV_2_THRESHOLD &&
		    cnd->old_rate >= DIV_2_THRESHOLD) {
			ret = clk_cpu_8996_mux_set_parent(&smux->clkr.hw,
							   SMUX_XO_INDEX);
			if (ret)
				break;
		}
		ret = clk_cpu_8996_mux_set_parent(&cpuclk->clkr.hw, ALT_INDEX);
		break;
	case POST_RATE_CHANGE:
		if (cnd->new_rate < DIV_2_THRESHOLD) {
			ret = clk_cpu_8996_mux_set_parent(&smux->clkr.hw,
							   SMUX_PLL_DIV2_INDEX);
			if (ret)
				break;
			ret = clk_cpu_8996_mux_set_parent(&cpuclk->clkr.hw,
							  DIV_2_INDEX);
		} else {
			ret = clk_cpu_8996_mux_set_parent(&cpuclk->clkr.hw,
							  ACD_INDEX);
		}
		break;
	case ABORT_RATE_CHANGE:
		ret = clk_cpu_8996_mux_set_parent(&smux->clkr.hw, SMUX_PLL_DIV2_INDEX);
		if (ret)
			break;
		ret = clk_cpu_8996_mux_set_parent(&cpuclk->clkr.hw,
						  cnd->old_rate < DIV_2_THRESHOLD ?
						  DIV_2_INDEX : ACD_INDEX);
		break;
	default:
		break;
	}

	return notifier_from_errno(ret);
}

static int qcom_cpu_clk_msm8996_driver_probe(struct platform_device *pdev)
{
	struct regmap *regmap;
	struct clk_hw_onecell_data *data;
	struct device *dev = &pdev->dev;
	int ret;

	data = devm_kzalloc(dev, struct_size(data, hws, 2), GFP_KERNEL);
	if (!data)
		return -ENOMEM;

	base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(base))
		return PTR_ERR(base);

	regmap = devm_regmap_init_mmio(dev, base, &cpu_msm8996_regmap_config);
	if (IS_ERR(regmap))
		return PTR_ERR(regmap);

	ret = qcom_cpu_clk_msm8996_register_clks(dev, regmap);
	if (ret)
		return ret;

	data->hws[0] = &pwrcl_pmux.clkr.hw;
	data->hws[1] = &perfcl_pmux.clkr.hw;
	data->num = 2;

	ret = devm_of_clk_add_hw_provider(dev, of_clk_hw_onecell_get, data);
	if (ret) {
		int cleanup_ret;

		cleanup_ret = qcom_cpu_clk_msm8996_unregister_clks();
		if (cleanup_ret)
			dev_err(dev, "Failed to clean up CPU clocks: %d\n",
				cleanup_ret);
	}

	return ret;
}

static int qcom_cpu_clk_msm8996_driver_remove(struct platform_device *pdev)
{
	return qcom_cpu_clk_msm8996_unregister_clks();
}

static const struct of_device_id qcom_cpu_clk_msm8996_match_table[] = {
	{ .compatible = "qcom,msm8996-apcc" },
	{}
};
MODULE_DEVICE_TABLE(of, qcom_cpu_clk_msm8996_match_table);

static struct platform_driver qcom_cpu_clk_msm8996_driver = {
	.probe = qcom_cpu_clk_msm8996_driver_probe,
	.remove = qcom_cpu_clk_msm8996_driver_remove,
	.driver = {
		.name = "qcom-msm8996-apcc",
		.of_match_table = qcom_cpu_clk_msm8996_match_table,
	},
};
module_platform_driver(qcom_cpu_clk_msm8996_driver);

MODULE_DESCRIPTION("QCOM MSM8996 CPU Clock Driver");
MODULE_LICENSE("GPL v2");
