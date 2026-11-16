/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __MSM8996_LMH_STATE_H
#define __MSM8996_LMH_STATE_H

#include <linux/types.h>

#define MSM8996_LMH_ODCM_COUNT 6
#define MSM8996_LMH_ODCM_THRESHOLD_MV 850

/* The lock belongs to the driver. This is only the LMH voltage notification
 * protocol, not a second CPU clock/voltage transaction engine or an
 * exclusion token for autonomous CPR, SAW, secure firmware or CPU collapse.
 */
struct msm8996_lmh_power {
	unsigned int min_uv, max_uv;
	unsigned int baseline_uv, dpm_mv;
	unsigned int old_uv, target_uv;
	bool odcm_enabled;
	bool baseline_valid, pending, suspended, retired;
	int error;
};

struct msm8996_lmh_io {
	int (*read_dpm)(void *data, unsigned int *mv);
	int (*read_odcm)(void *data, bool *enabled);
	int (*write_dpm)(void *data, unsigned int mv);
	int (*write_odcm)(void *data, bool enabled);
};

int msm8996_lmh_fail(struct msm8996_lmh_power *power, int error);
int msm8996_lmh_baseline(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data, unsigned int uv);
int msm8996_lmh_pre(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data,
		unsigned long old_uv, unsigned long min_uv, unsigned long max_uv);
int msm8996_lmh_post(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data, unsigned long uv);
int msm8996_lmh_abort(struct msm8996_lmh_power *power, unsigned long old_uv);

#endif
