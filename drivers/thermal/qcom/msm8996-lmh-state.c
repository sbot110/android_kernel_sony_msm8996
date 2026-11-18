// SPDX-License-Identifier: GPL-2.0-only
/* LMHv1 S11 ordering from Sony4.4 0c295b15 drivers/thermal/lmh_lite.c.
 * Unlike its file-static pending flags, state is per exclusive endpoint.
 */
#include <linux/errno.h>
#include <linux/limits.h>
#include "msm8996-lmh-state.h"

int msm8996_lmh_fail(struct msm8996_lmh_power *power, int error)
{
	if (!power->error)
		power->error = error < 0 ? error : -EIO;
	return power->error;
}

static int msm8996_lmh_ready(struct msm8996_lmh_power *power)
{
	if (power->retired)
		return -ESHUTDOWN;
	if (power->error)
		return power->error;
	if (power->suspended)
		return -EBUSY;
	return 0;
}

static int msm8996_lmh_sample(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data,
		unsigned int expected_mv, bool expected_odcm)
{
	unsigned int mv;
	bool odcm;
	int ret;

	ret = io->read_dpm(data, &mv);
	if (!ret)
		ret = io->read_odcm(data, &odcm);
	if (!ret && (mv != expected_mv || odcm != expected_odcm))
		ret = -EIO;
	return ret ? msm8996_lmh_fail(power, ret) : 0;
}

int msm8996_lmh_baseline(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data, unsigned int uv)
{
	unsigned int mv;
	bool odcm;
	int ret = msm8996_lmh_ready(power);

	if (ret)
		return ret;
	if (!io || !io->read_dpm || !io->read_odcm || !io->write_dpm ||
	    !io->write_odcm || !power->min_uv || power->max_uv < power->min_uv ||
	    uv < power->min_uv || uv > power->max_uv)
		return -EINVAL;
	/* Baseline attachment is observational: never repair a boot state by
	 * writing guessed scale/ODCM values or discard existing quarantine.
	 */
	if (power->pending || power->baseline_valid)
		return -EBUSY;
	ret = io->read_dpm(data, &mv);
	if (!ret)
		ret = io->read_odcm(data, &odcm);
	if (ret)
		return msm8996_lmh_fail(power, ret);
	if (mv != uv / 1000 ||
	    (uv / 1000 > MSM8996_LMH_ODCM_THRESHOLD_MV && !odcm))
		return msm8996_lmh_fail(power, -ENODATA);
	power->baseline_uv = uv;
	power->dpm_mv = mv;
	power->odcm_enabled = odcm;
	power->baseline_valid = true;
	return 0;
}

int msm8996_lmh_pre(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data,
		unsigned long old_uv, unsigned long min_uv, unsigned long max_uv)
{
	unsigned int target_mv;
	int ret = msm8996_lmh_ready(power);

	if (ret)
		return ret;
	if (!power->baseline_valid)
		return -ENODATA;
	if (power->pending)
		return msm8996_lmh_fail(power, -EBUSY);
	if (old_uv > UINT_MAX || min_uv > UINT_MAX || max_uv > UINT_MAX ||
	    min_uv < power->min_uv || max_uv > power->max_uv || min_uv > max_uv)
		return -EINVAL;
	/* set_voltage(min,max) alone does not identify the actual PMIC step.
	 * No writable CPU binding may convert an unqualified range into an
	 * assumed exact LMH scale. The future owner must select a real step.
	 */
	if (min_uv != max_uv)
		return -EOPNOTSUPP;
	if (old_uv != power->baseline_uv)
		return msm8996_lmh_fail(power, -ESTALE);
	ret = msm8996_lmh_sample(power, io, data, power->dpm_mv,
				power->odcm_enabled);
	if (ret)
		return ret;
	power->old_uv = old_uv;
	power->target_uv = min_uv;
	power->pending = true;
	target_mv = min_uv / 1000;
	/* Original Sony ordering: DPM before rise, ODCM above850mV before
	 * rise. On a drop retain both until a successful POST notification.
	 */
	if (min_uv > old_uv) {
		ret = io->write_dpm(data, target_mv);
		if (ret)
			return msm8996_lmh_fail(power, ret);
		power->dpm_mv = target_mv;
	}
	if (target_mv > MSM8996_LMH_ODCM_THRESHOLD_MV && !power->odcm_enabled) {
		ret = io->write_odcm(data, true);
		if (ret)
			return msm8996_lmh_fail(power, ret);
		power->odcm_enabled = true;
	}
	return 0;
}

int msm8996_lmh_post(struct msm8996_lmh_power *power,
		const struct msm8996_lmh_io *io, void *data, unsigned long uv)
{
	unsigned int mv;
	int ret = msm8996_lmh_ready(power);

	if (ret)
		return ret;
	if (!power->pending || uv > UINT_MAX || uv != power->target_uv)
		return msm8996_lmh_fail(power, -EPROTO);
	ret = msm8996_lmh_sample(power, io, data, power->dpm_mv,
				power->odcm_enabled);
	if (ret)
		return ret;
	mv = uv / 1000;
	if (uv <= power->old_uv) {
		ret = io->write_dpm(data, mv);
		if (ret)
			return msm8996_lmh_fail(power, ret);
		power->dpm_mv = mv;
	}
	if (mv <= MSM8996_LMH_ODCM_THRESHOLD_MV && power->odcm_enabled) {
		ret = io->write_odcm(data, false);
		if (ret)
			return msm8996_lmh_fail(power, ret);
		power->odcm_enabled = false;
	}
	power->baseline_uv = uv;
	power->pending = false;
	return 0;
}

int msm8996_lmh_abort(struct msm8996_lmh_power *power, unsigned long old_uv)
{
	int ret = msm8996_lmh_ready(power);

	if (ret)
		return ret;
	if (!power->pending || old_uv != power->old_uv)
		return msm8996_lmh_fail(power, -EPROTO);
	/* 4.19 ABORT carries cached old_uV, not confirmed voltage or a PMIC
	 * ACK. After a lost ACK the rail can already be at the new value.
	 * Blind source-style ODCM restoration would remove mitigation from
	 * an unknown supply. Retain all raised settings and sticky quarantine.
	 */
	return msm8996_lmh_fail(power, -EIO);
}
