// SPDX-License-Identifier: GPL-2.0-only
/*
 * Coordinated MSM8996 CPU/CBF transaction, with no MMIO adapter installed.
 * Order and transition limits derive from Sony clock-cpu-8996.c,
 * Copyright (c) 2015-2018, The Linux Foundation, Sony4.4 reference
 * 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1.
 *
 * This engine does not initialize PLLs, choose a fuse bin, provide a regulator
 * or enable DVFS. It makes the mandatory ordering/error contract executable
 * while those hardware adapters and ownership dependencies remain gated.
 */
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/string.h>

#include "clk-msm8996-cpu-transaction.h"

#define MSM8996_ACD_MAIN_MAX	595200000UL
#define MSM8996_DIV2_BOUNDARY	600000000UL
#define MSM8996_ALT_THRESHOLD	1190400000UL
#define MSM8996_ALT_LOW		307200000UL
#define MSM8996_ALT_HIGH		556800000UL

int msm8996_cpu_plan_lookup(const struct msm8996_cpu_plan *plans,
			  unsigned int count, unsigned int speed_bin,
			  unsigned long rate, bool boot,
			  struct msm8996_cpu_plan *plan)
{
	unsigned int i;

	if (!plans || !plan || count < 2 || speed_bin > 3 || !rate ||
	    plans[0].rate || plans[0].level)
		return -EINVAL;
	for (i = 1; i < count; i++)
		if (plans[i].rate <= plans[i - 1].rate ||
		    plans[i].level <= plans[i - 1].level)
			return -EINVAL;
	for (i = 1; i < count; i++) {
		if (rate > plans[i].rate)
			continue;
		if (!boot && rate != plans[i].rate)
			return -EINVAL;
		plan->rate = rate;
		plan->level = plans[i].level;
		return 0;
	}
	return -ERANGE;
}
EXPORT_SYMBOL_GPL(msm8996_cpu_plan_lookup);

static int msm8996_cpu_safe_rate(struct msm8996_cpu_transaction *txn,
			       unsigned long rate, unsigned long *safe)
{
	if (rate > MSM8996_ACD_MAIN_MAX)
		*safe = rate <= MSM8996_ACD_MAIN_MAX * 2 ?
			rate / 2 : MSM8996_ACD_MAIN_MAX;
	else
		*safe = txn->policy.aux_rate;
	/* Do not raise an unowned/uncertain clock to a supposed safe rate. */
	return !*safe || *safe > rate ? -ERANGE : 0;
}

static int msm8996_cpu_vote(struct msm8996_cpu_transaction *txn,
			   unsigned int lease,
			   const struct msm8996_cpu_plan *plan, bool enable)
{
	int ret;

	if (enable)
		txn->lease_plan[lease] = *plan;
	txn->lease[lease] = MSM8996_CPU_LEASE_UNKNOWN;
	ret = txn->ops->vote(txn->data, lease, plan, enable);
	if (!ret)
		txn->lease[lease] = enable ? MSM8996_CPU_LEASE_HELD :
					 MSM8996_CPU_LEASE_EMPTY;
	return ret;
}

static int msm8996_cpu_lookup(struct msm8996_cpu_transaction *txn,
			     unsigned long rate, bool boot,
			     struct msm8996_cpu_plan *plan)
{
	int ret;

	if (!rate)
		return -EINVAL;
	ret = txn->ops->lookup(txn->data, rate, boot, plan);
	if (!ret && (plan->rate != rate || !plan->level))
		return -EINVAL;
	return ret;
}

static int msm8996_cpu_verify_rate(struct msm8996_cpu_transaction *txn,
				  unsigned long expected)
{
	unsigned long rate;
	int ret = txn->ops->read_rate(txn->data, &rate);

	return ret ? ret : rate == expected ? 0 : -EIO;
}

/*
 * Restore the old plan while retaining both leases until a non-ACD rate is
 * confirmed. Failed restoration leaves the guard and every uncertain lease
 * held; callers must not resume rate changes or free its hardware resources.
 */
static int msm8996_cpu_restore(struct msm8996_cpu_transaction *txn)
{
	unsigned long rate, safe, old_safe;
	unsigned int target = txn->active ^ 1;
	int ret;

	ret = txn->ops->read_rate(txn->data, &rate);
	if (ret)
		return ret;
	ret = msm8996_cpu_safe_rate(txn, rate, &safe);
	if (ret)
		return ret;
	ret = msm8996_cpu_safe_rate(txn, txn->active_plan.rate, &old_safe);
	if (ret)
		return ret;
	/* The old lease must cover the safe output after target-lease removal. */
	if (safe > old_safe)
		safe = old_safe;
	ret = txn->ops->safe(txn->data, safe);
	if (ret)
		return ret;
	ret = msm8996_cpu_verify_rate(txn, safe);
	if (ret)
		return ret;
	ret = msm8996_cpu_vote(txn, txn->active, &txn->active_plan, true);
	if (ret)
		return ret;
	ret = txn->ops->power_ready(txn->data);
	if (ret)
		return ret;
	if (txn->alt_touched) {
		ret = txn->ops->set_alt(txn->data, txn->saved_alt);
		if (ret)
			return ret;
		txn->alt_touched = false;
	}
	if (txn->lease[target] != MSM8996_CPU_LEASE_EMPTY) {
		ret = msm8996_cpu_vote(txn, target, NULL, false);
		if (ret)
			return ret;
	}
	ret = txn->ops->power_ready(txn->data);
	if (ret)
		return ret;
	ret = txn->ops->program(txn->data, &txn->active_plan);
	if (ret)
		return ret;
	return msm8996_cpu_verify_rate(txn, txn->active_plan.rate);
}

static int msm8996_cpu_abort(struct msm8996_cpu_transaction *txn, int error)
{
	txn->last_error = error;
	txn->recovery_error = msm8996_cpu_restore(txn);
	txn->quarantined = !!txn->recovery_error;
	if (!txn->quarantined) {
		txn->initialized = true;
		txn->ops->leave(txn->data);
		txn->guarded = false;
	}
	return error;
}

int msm8996_cpu_transaction_init(struct msm8996_cpu_transaction *txn,
		const struct msm8996_cpu_transaction_ops *ops, void *data,
		const struct msm8996_cpu_transaction_policy *policy)
{
	if (!txn || !ops || !policy || !ops->lookup || !ops->enter ||
	    !ops->leave || !ops->read_rate || !ops->safe || !ops->program ||
	    !ops->vote || !ops->power_ready || !policy->aux_rate ||
	    (policy->do_half_rate && !ops->div2) ||
	    (policy->has_alt_pll && (!ops->read_alt || !ops->set_alt)))
		return -EINVAL;
	memset(txn, 0, sizeof(*txn));
	mutex_init(&txn->lock);
	txn->ops = ops;
	txn->data = data;
	txn->policy = *policy;
	return 0;
}
EXPORT_SYMBOL_GPL(msm8996_cpu_transaction_init);

int msm8996_cpu_transaction_bootstrap(struct msm8996_cpu_transaction *txn)
{
	unsigned long rate, safe;
	int ret;

	mutex_lock(&txn->lock);
	if (txn->initialized || txn->guarded || txn->quarantined) {
		ret = -EBUSY;
		goto out;
	}
	ret = txn->ops->enter(txn->data);
	if (ret)
		goto out;
	txn->guarded = true;
	ret = txn->ops->read_rate(txn->data, &rate);
	if (!ret)
		ret = msm8996_cpu_lookup(txn, rate, true, &txn->active_plan);
	if (ret) {
		txn->ops->leave(txn->data);
		txn->guarded = false;
		goto out;
	}
	txn->have_plan = true;
	ret = msm8996_cpu_safe_rate(txn, rate, &safe);
	if (!ret)
		ret = txn->ops->safe(txn->data, safe);
	if (!ret)
		ret = msm8996_cpu_verify_rate(txn, safe);
	if (!ret)
		ret = msm8996_cpu_vote(txn, txn->active, &txn->active_plan, true);
	if (!ret)
		ret = txn->ops->power_ready(txn->data);
	if (!ret)
		ret = txn->ops->program(txn->data, &txn->active_plan);
	if (!ret)
		ret = msm8996_cpu_verify_rate(txn, txn->active_plan.rate);
	if (ret) {
		ret = msm8996_cpu_abort(txn, ret);
		goto out;
	}
	txn->initialized = true;
	txn->ops->leave(txn->data);
	txn->guarded = false;
out:
	mutex_unlock(&txn->lock);
	return ret;
}
EXPORT_SYMBOL_GPL(msm8996_cpu_transaction_bootstrap);

int msm8996_cpu_transaction_set_rate(struct msm8996_cpu_transaction *txn,
				   unsigned long rate)
{
	struct msm8996_cpu_plan target;
	unsigned int next;
	unsigned long safe;
	bool restore_acd = false;
	int ret;

	mutex_lock(&txn->lock);
	if (!txn->initialized || txn->quarantined) {
		ret = -EAGAIN;
		goto out;
	}
	ret = msm8996_cpu_lookup(txn, rate, false, &target);
	if (ret)
		goto out;
	ret = txn->ops->enter(txn->data);
	if (ret)
		goto out;
	txn->guarded = true;
	ret = msm8996_cpu_verify_rate(txn, txn->active_plan.rate);
	if (!ret)
		ret = txn->ops->power_ready(txn->data);
	if (ret)
		goto abort;
	if (rate == txn->active_plan.rate) {
		txn->ops->leave(txn->data);
		txn->guarded = false;
		goto out;
	}
	if (txn->policy.has_alt_pll) {
		ret = txn->ops->read_alt(txn->data, &txn->saved_alt);
		if (ret)
			goto abort;
	}
	if (txn->policy.has_acd && rate > txn->active_plan.rate &&
	    txn->active_plan.rate > MSM8996_ACD_MAIN_MAX) {
		ret = msm8996_cpu_safe_rate(txn, txn->active_plan.rate, &safe);
		if (!ret)
			ret = txn->ops->safe(txn->data, safe);
		if (!ret)
			ret = msm8996_cpu_verify_rate(txn, safe);
		if (ret)
			goto abort;
	}
	next = txn->active ^ 1;
	ret = msm8996_cpu_vote(txn, next, &target, true);
	if (!ret)
		ret = txn->ops->power_ready(txn->data);
	if (ret)
		goto abort;
	if (txn->policy.has_alt_pll) {
		txn->alt_touched = true;
		ret = txn->ops->set_alt(txn->data, rate > MSM8996_ALT_THRESHOLD ?
					MSM8996_ALT_HIGH : MSM8996_ALT_LOW);
		if (ret)
			goto abort;
	}
	if (txn->policy.do_half_rate &&
	    txn->active_plan.rate > MSM8996_DIV2_BOUNDARY &&
	    rate < MSM8996_DIV2_BOUNDARY) {
		ret = txn->ops->div2(txn->data, txn->active_plan.rate / 2);
		if (ret)
			goto abort;
	}
	ret = txn->ops->program(txn->data, &target);
	if (!ret)
		ret = msm8996_cpu_verify_rate(txn, rate);
	if (ret)
		goto abort;
	if (txn->policy.has_acd && rate < txn->active_plan.rate &&
	    rate > MSM8996_ACD_MAIN_MAX) {
		ret = msm8996_cpu_safe_rate(txn, txn->active_plan.rate, &safe);
		if (!ret)
			ret = txn->ops->safe(txn->data, safe);
		if (!ret)
			ret = msm8996_cpu_verify_rate(txn, safe);
		if (ret)
			goto abort;
		restore_acd = true;
	}
	ret = msm8996_cpu_vote(txn, txn->active, NULL, false);
	if (!ret)
		ret = txn->ops->power_ready(txn->data);
	if (ret)
		goto abort;
	/* Sony post_set_rate restores the ACD leg after the voltage decrease. */
	if (restore_acd) {
		ret = txn->ops->program(txn->data, &target);
		if (!ret)
			ret = msm8996_cpu_verify_rate(txn, rate);
		if (ret)
			goto abort;
	}
	txn->active_plan = target;
	txn->active = next;
	txn->alt_touched = false;
	txn->ops->leave(txn->data);
	txn->guarded = false;
	goto out;
abort:
	ret = msm8996_cpu_abort(txn, ret);
out:
	mutex_unlock(&txn->lock);
	return ret;
}
EXPORT_SYMBOL_GPL(msm8996_cpu_transaction_set_rate);

int msm8996_cpu_transaction_recover(struct msm8996_cpu_transaction *txn)
{
	int ret;

	mutex_lock(&txn->lock);
	if (!txn->quarantined || !txn->guarded || !txn->have_plan) {
		ret = -EINVAL;
		goto out;
	}
	ret = msm8996_cpu_restore(txn);
	txn->recovery_error = ret;
	if (!ret) {
		txn->quarantined = false;
		txn->initialized = true;
		txn->ops->leave(txn->data);
		txn->guarded = false;
	}
out:
	mutex_unlock(&txn->lock);
	return ret;
}
EXPORT_SYMBOL_GPL(msm8996_cpu_transaction_recover);
