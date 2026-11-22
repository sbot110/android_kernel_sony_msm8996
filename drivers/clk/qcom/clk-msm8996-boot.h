/* SPDX-License-Identifier: GPL-2.0 */
#ifndef __QCOM_CLK_MSM8996_BOOT_H__
#define __QCOM_CLK_MSM8996_BOOT_H__

/* Register formats shared with Sony clock-cpu-8996.c and clk-alpha-pll.c. */
static inline int msm8996_boot_huayra_rate(u64 xo, u32 l, u32 alpha,
					 u32 user, u64 *rate)
{
	s64 fraction = 0;

	/* Sony uses the undivided XO input; reject an unhandled prescaler. */
	if (user & (1U << 12))
		return -EINVAL;

	if (user & (1U << 24)) {
		if (user & (1U << 25)) {
			u32 denominator = alpha & 0xff;
			s32 numerator = (s8)(alpha >> 8);

			if (!denominator)
				return -EINVAL;
			fraction = (s64)xo * numerator / denominator;
		} else {
			s32 signed_alpha = (s16)alpha;

			/* Match the hardware/native driver floor for negative alpha. */
			if (signed_alpha < 0)
				fraction = -((s64)xo * -signed_alpha + 65535) / 65536;
			else
				fraction = (s64)xo * signed_alpha / 65536;
		}
	}
	if ((s64)(xo * l) + fraction <= 0)
		return -EINVAL;
	*rate = xo * l + fraction;
	return 0;
}

/* Main output divider encoding: 0 = /1, 1 = /2, 3 = /4. */
static inline int msm8996_boot_main_div(u32 user)
{
	switch ((user >> 8) & 3) {
	case 0: return 1;
	case 1: return 2;
	case 3: return 4;
	default: return -EINVAL;
	}
}

#endif
