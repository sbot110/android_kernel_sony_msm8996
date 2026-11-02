/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __QCOM_MSM8996_CPU_TRANSACTION_H__
#define __QCOM_MSM8996_CPU_TRANSACTION_H__

#include <linux/mutex.h>
#include <linux/types.h>

struct msm8996_cpu_plan {
	unsigned long rate;
	unsigned int level;
};

enum msm8996_cpu_lease_state {
	MSM8996_CPU_LEASE_EMPTY,
	MSM8996_CPU_LEASE_HELD,
	MSM8996_CPU_LEASE_UNKNOWN,
};

/*
 * Callbacks are deliberately not a hardware implementation. The eventual
 * APCC/CBF adapter must own every affected clock/rail and validate the selected
 * Sony fuse plan. No current boot-clock provider binds these operations.
 *
 * enter() excludes power collapse and all concurrent owners of this domain;
 * an error must leave no guard. leave() releases a successfully held guard.
 * read_rate() returns a stable, measured mux/PLL rate, not a software cache.
 * safe() selects a non-ACD output without exceeding the requested safe rate.
 * program() implements the original PLL/mux order, including hardware lock
 * verification; success means that read_rate() will confirm the exact plan.
 *
 * vote() replaces one of TWO fixed consumer leases idempotently. Disabling a
 * lease must remove its voltage constraints as well as its enable request;
 * regulator_disable() alone is not sufficient for a stored corner request.
 * The plan argument is NULL on disable. An enable
 * success means a real acknowledged request, including every required supply
 * and RPM set. A failure may have applied: the lease becomes UNKNOWN.
 * power_ready() must reject any unacknowledged/uncertain supply state.
 * All callbacks may sleep. They must not re-enter this transaction object.
 */
struct msm8996_cpu_transaction_ops {
	int (*lookup)(void *data, unsigned long rate, bool boot,
		      struct msm8996_cpu_plan *plan);
	int (*enter)(void *data);
	void (*leave)(void *data);
	int (*read_rate)(void *data, unsigned long *rate);
	int (*safe)(void *data, unsigned long rate);
	int (*program)(void *data, const struct msm8996_cpu_plan *plan);
	int (*div2)(void *data, unsigned long rate);
	int (*read_alt)(void *data, unsigned long *rate);
	int (*set_alt)(void *data, unsigned long rate);
	int (*vote)(void *data, unsigned int lease,
		    const struct msm8996_cpu_plan *plan, bool enable);
	int (*power_ready)(void *data);
};

struct msm8996_cpu_transaction_policy {
	bool has_acd;
	bool do_half_rate;
	bool has_alt_pll;
	unsigned long aux_rate;
};

struct msm8996_cpu_transaction {
	struct mutex lock;
	const struct msm8996_cpu_transaction_ops *ops;
	void *data;
	struct msm8996_cpu_transaction_policy policy;
	struct msm8996_cpu_plan active_plan;
	enum msm8996_cpu_lease_state lease[2];
	struct msm8996_cpu_plan lease_plan[2];
	unsigned int active;
	unsigned long saved_alt;
	bool alt_touched;
	bool have_plan;
	bool initialized;
	bool guarded;
	bool quarantined;
	int last_error;
	int recovery_error;
};

/* Source-plan helper: no unknown-bin fallback and no invented OPP/voltage. */
int msm8996_cpu_plan_lookup(const struct msm8996_cpu_plan *plans,
			  unsigned int count, unsigned int speed_bin,
			  unsigned long rate, bool boot,
			  struct msm8996_cpu_plan *plan);
int msm8996_cpu_transaction_init(struct msm8996_cpu_transaction *txn,
		const struct msm8996_cpu_transaction_ops *ops, void *data,
		const struct msm8996_cpu_transaction_policy *policy);
/* Bootstrap takes a real lease for the measured firmware rate before use. */
int msm8996_cpu_transaction_bootstrap(struct msm8996_cpu_transaction *txn);
int msm8996_cpu_transaction_set_rate(struct msm8996_cpu_transaction *txn,
				   unsigned long rate);
/* An explicit retry restores the old plan only after voltage ACK/readback. */
int msm8996_cpu_transaction_recover(struct msm8996_cpu_transaction *txn);

#endif
