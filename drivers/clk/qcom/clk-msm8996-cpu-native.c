// SPDX-License-Identifier: GPL-2.0-only
/* Native, gated MSM8996-v3 CPU clock transactions.
 * Register maps/sequences: Sony4.4 0c295b15 clock-cpu-8996.c, clock-pll.c,
 * clock-alpha-pll.c and kryo-l2-accessors.c, Copyright (c) 2014-2018,
 * The Linux Foundation. This is not a cold PLL initializer or a power owner.
 * No live driver binds it until CPR/SPM/APM/shared-rail exclusion is supplied.
 */
#include <linux/cpu.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/regmap.h>
#include <linux/smp.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <asm/cputype.h>
#include <asm/smp_plat.h>
#include <asm/sysreg.h>

#include "clk-msm8996-boot.h"
#include "clk-msm8996-cpu-native.h"

#define MAIN_MAX 595200000UL
#define LOCK BIT(31)
#define FSM BIT(20)
#define OFFLINE BIT(7)
#define OFFLINE_ACK BIT(28)
#define ACTIVE BIT(30)
#define PLL_WAIT 200
#define L2_SEL sys_reg(3, 3, 15, 0, 6)
#define L2_DATA sys_reg(3, 3, 15, 0, 7)

static DEFINE_MUTEX(native_owner_lock);
static struct msm8996_cpu_native_shared *native_owner;
static DEFINE_RAW_SPINLOCK(native_acd_lock);

struct native_sample {
	u32 mux, mode, l, alpha, alpha_hi, user;
	u32 gcc[5], aux;
};

static void __iomem *native_base(struct msm8996_cpu_native_shared *s,
				unsigned int domain)
{
	return domain == 2 ? s->maps.cbf : s->maps.apcc[domain];
}

static u32 native_mux_offset(unsigned int domain)
{
	return domain == 2 ? 0x18 : 0x40;
}

static int native_sample(struct msm8996_cpu_native_shared *s, unsigned int d,
			 struct native_sample *p)
{
	static const u32 gcc_offset[] = { 0, 4, 8, 0xc, 0x10 };
	void __iomem *base = native_base(s, d), *pll = base;
	bool aux;
	unsigned int i;
	int ret;

	memset(p, 0, sizeof(*p));
	p->mux = readl_relaxed(base + native_mux_offset(d));
	aux = d == 2 ? (p->mux & 3) == 3 : (p->mux & 0xf) == 0xc;
	if (aux) {
		for (i = 0; i < 5; i++) {
			ret = regmap_read(s->maps.gcc, gcc_offset[i], &p->gcc[i]);
			if (ret)
				return ret;
		}
		p->aux = readl_relaxed(s->maps.aux);
	} else if (d == 2 ? p->mux & 3 : p->mux & 0xf) {
		if (d == 2)
			pll += 0xf000;
		else if ((p->mux & 3) == 3)
			pll += 0x100;
		p->mode = readl_relaxed(pll);
		p->l = readl_relaxed(pll + (d == 2 ? 8 : 4));
		p->alpha = readl_relaxed(pll + (d == 2 ? 0x10 : 8));
		if (d == 2 || (p->mux & 3) == 3)
			p->alpha_hi = readl_relaxed(pll + (d == 2 ? 0x14 : 0xc));
		p->user = readl_relaxed(pll + (d == 2 ? 0x18 : 0x10));
	}
	rmb();
	return p->mux == readl_relaxed(base + native_mux_offset(d)) ? 0 : -EAGAIN;
}

static int native_decode(struct msm8996_cpu_native_shared *s, unsigned int d,
			 const struct native_sample *p, unsigned long *rate)
{
	u32 mux = p->mux, alpha;
	u64 value;
	bool main;
	int div, ret;

	if (d == 2 ? (mux & 3) == 3 : (mux & 0xf) == 0xc) {
		/* Live GPLL0 + APCS divider readback, never a cached CCF rate. */
		if ((p->gcc[0] & (LOCK | 7)) != (LOCK | 7) ||
		    !(p->gcc[4] & BIT(0)) || p->gcc[4] & BIT(12) ||
		    ((p->aux >> 16) & 3) != 1)
			return -EIO;
		value = (u64)s->maps.xo * p->gcc[1];
		if (p->gcc[4] & BIT(24)) {
			alpha = p->gcc[2] >> 8 | (p->gcc[3] & 0xff) << 24;
			value += (u64)s->maps.xo * alpha >> 32;
		}
		div = msm8996_boot_main_div(p->gcc[4]);
		if (div < 0 || value / div != 600000000)
			return -EIO;
		*rate = value / div / 2;
		return 0;
	}
	if (d == 2 ? !(mux & 3) : !(mux & 0xf)) {
		*rate = s->maps.xo;
		return 0;
	}
	if (d != 2 && !(mux & 3) && ((mux >> 2) & 3) != 1)
		return -EINVAL;
	main = d == 2 ? (mux & 3) == 2 : !(mux & 3);
	if ((p->mode & (LOCK | 7)) != (LOCK | 7) ||
	    !(p->user & (main ? BIT(0) : BIT(3))) || p->user & BIT(12))
		return -EIO;
	if (d == 2 || (mux & 3) == 3) {
		value = (u64)s->maps.xo * p->l;
		if (p->user & BIT(24)) {
			alpha = p->alpha >> 8 | (p->alpha_hi & 0xff) << 24;
			value += (u64)s->maps.xo * alpha >> 32;
		}
	} else {
		ret = msm8996_boot_huayra_rate(s->maps.xo, p->l, p->alpha,
					    p->user, &value);
		if (ret)
			return ret;
	}
	if (main) {
		div = msm8996_boot_main_div(p->user);
		if (div < 0)
			return div;
		value /= div;
	}
	if (!value || value > ULONG_MAX)
		return -ERANGE;
	*rate = value;
	return 0;
}

static int native_read(struct msm8996_cpu_native_shared *s, unsigned int d,
			unsigned long *rate)
{
	struct native_sample a, b;
	int ret;

	ret = native_sample(s, d, &a);
	if (!ret)
		ret = native_sample(s, d, &b);
	if (ret)
		return ret;
	if (memcmp(&a, &b, sizeof(a)))
		return -EAGAIN;
	return native_decode(s, d, &a, rate);
}

static int native_guard(struct msm8996_cpu_native *n)
{
	return READ_ONCE(n->shared->owner) == n ? 0 : -EPERM;
}

static int native_peers(struct msm8996_cpu_native *n)
{
	unsigned long rate;
	unsigned int i;
	int ret;

	for (i = 0; i < 3; i++) {
		if (i == n->domain)
			continue;
		ret = native_read(n->shared, i, &rate);
		if (ret || rate != n->peer_rate[i])
			return ret ? ret : -EIO;
	}
	return 0;
}

static int native_lookup(void *data, unsigned long rate, bool boot,
			 struct msm8996_cpu_plan *plan)
{
	struct msm8996_cpu_native *n = data;
	struct msm8996_cpu_hw_plans *p = &n->shared->plans;

	return msm8996_cpu_plan_lookup(p->domain[n->domain], p->count[n->domain],
				      p->speed_bin, rate, boot, plan);
}

static int native_primary_valid(struct msm8996_cpu_native *n);

static int native_enter(void *data)
{
	struct msm8996_cpu_native *n = data;
	struct msm8996_cpu_native_shared *s = n->shared;
	unsigned int i;
	int ret;

	mutex_lock(&s->lock);
	ret = -EBUSY;
	if (s->owner || s->frozen)
		goto out;
	ret = s->power->enter(s->power_data);
	if (ret)
		goto out;
	for (i = 0; i < 3; i++) {
		ret = native_read(s, i, &n->peer_rate[i]);
		if (ret)
			goto leave;
	}
	/* Only warm source-v3 primary handoff is qualified here. Reject an
	 * unsupported initial parent/config BEFORE safe() can alter a mux.
	 */
	ret = native_primary_valid(n);
	if (!ret && n->domain != 2 &&
	    (readl_relaxed(native_base(s, n->domain) + 0x40) & 3) == 3)
		ret = -EOPNOTSUPP;
	if (ret)
		goto leave;
	WRITE_ONCE(s->owner, n);
	goto out;
leave:
	s->power->leave(s->power_data);
out:
	mutex_unlock(&s->lock);
	return ret;
}

static void native_leave(void *data)
{
	struct msm8996_cpu_native *n = data;
	struct msm8996_cpu_native_shared *s = n->shared;

	mutex_lock(&s->lock);
	if (!WARN_ON(s->owner != n)) {
		s->power->leave(s->power_data);
		WRITE_ONCE(s->owner, NULL);
	}
	mutex_unlock(&s->lock);
}

static int native_read_rate(void *data, unsigned long *rate)
{
	struct msm8996_cpu_native *n = data;
	int ret = native_guard(n);

	return ret ? ret : native_read(n->shared, n->domain, rate);
}

static int native_mux(struct msm8996_cpu_native *n, u32 mask, u32 select)
{
	void __iomem *reg = native_base(n->shared, n->domain) +
			   native_mux_offset(n->domain);
	u32 old = readl_relaxed(reg), value = (old & ~mask) | select;

	writel_relaxed(value, reg);
	mb();
	udelay(5); /* Sony hardware-mandated mux settling delay. */
	return readl_relaxed(reg) == value ? 0 : -EIO;
}

static int native_poll(void __iomem *reg, u32 mask, u32 wanted)
{
	unsigned int i;

	for (i = 0; i < PLL_WAIT; i++) {
		if ((readl_relaxed(reg) & mask) == wanted) {
			udelay(1);
			if ((readl_relaxed(reg) & mask) == wanted)
				return 0;
		}
		udelay(1);
	}
	return -ETIMEDOUT;
}

static int native_primary_valid(struct msm8996_cpu_native *n)
{
	bool cbf = n->domain == 2;
	void __iomem *base = native_base(n->shared, n->domain) + (cbf ? 0xf000 : 0);
	u32 user = readl_relaxed(base + (cbf ? 0x18 : 0x10));
	u32 test = readl_relaxed(base + (cbf ? 0x30 : 0x20));

	/* Preserve the source v3 boot configuration; no guessed cold init,
	 * fraction clearing, pro (/4) divider, or unrelated PLL reconfiguration.
	 */
	if ((user & (BIT(12) | BIT(24) | BIT(25) | GENMASK(9, 8))) != BIT(8) ||
	    (user & (BIT(0) | BIT(3))) != (BIT(0) | BIT(3)) ||
	    readl_relaxed(base + (cbf ? 0x20 : 0x18)) != 0x200d4828 ||
	    readl_relaxed(base + (cbf ? 0x24 : 0x1c)) != 6 ||
	    (test & ~BIT(16)) != 0x1c000000 ||
	    (readl_relaxed(base) & (FSM | LOCK | 7)) != (FSM | LOCK | 7))
		return -EOPNOTSUPP;
	return 0;
}

static int native_primary(struct msm8996_cpu_native *n, unsigned long rate)
{
	bool cbf = n->domain == 2;
	void __iomem *base = native_base(n->shared, n->domain) + (cbf ? 0xf000 : 0);
	u32 user, test;
	int ret = native_primary_valid(n);

	if (ret)
		return ret;
	if (rate < 600000000 || rate > 3000000000UL || rate % n->shared->maps.xo)
		return -EINVAL;
	user = readl_relaxed(base + (cbf ? 0x18 : 0x10));
	test = readl_relaxed(base + (cbf ? 0x30 : 0x20));
	if (cbf) {
		writel_relaxed(test | BIT(16), base + 0x30);
		mb();
		udelay(1);
	}
	writel_relaxed(rate / n->shared->maps.xo, base + (cbf ? 8 : 4));
	mb();
	if (cbf)
		writel_relaxed(test & ~BIT(16), base + 0x30);
	mb();
	udelay(50); /* Source's explicit transient-lock avoidance interval. */
	ret = native_poll(base, LOCK, LOCK);
	if (!ret && readl_relaxed(base + (cbf ? 8 : 4)) != rate / n->shared->maps.xo)
		ret = -EIO;
	if (!ret && (readl_relaxed(base + (cbf ? 0x18 : 0x10)) != user ||
		     readl_relaxed(base + (cbf ? 0x20 : 0x18)) != 0x200d4828 ||
		     readl_relaxed(base + (cbf ? 0x24 : 0x1c)) != 6 ||
		     readl_relaxed(base + (cbf ? 0x30 : 0x20)) != (test & ~BIT(16))))
		ret = -EIO;
	return ret;
}

struct native_acd_call {
	struct msm8996_cpu_native *native;
	int result;
};

static u64 native_l2_read(u64 reg)
{
	write_sysreg_s(reg, L2_SEL);
	isb();
	return read_sysreg_s(L2_DATA);
}

static void native_l2_write(u64 reg, u64 value)
{
	mb();
	write_sysreg_s(reg, L2_SEL);
	isb();
	write_sysreg_s(value, L2_DATA);
	isb();
}

static void native_acd_cpu(void *data)
{
	struct native_acd_call *call = data;
	struct msm8996_cpu_native *n = call->native;
	void __iomem *base = native_base(n->shared, n->domain);
	unsigned long flags;
	u64 mpidr = read_cpuid_mpidr() & 0xfff;

	call->result = -EXDEV;
	if ((mpidr >> 8) != n->domain || (mpidr & 0xff) > 1)
		return;
	raw_spin_lock_irqsave(&native_acd_lock, flags);
	native_l2_write(0x581, 0x6a11);
	native_l2_write(0x584, 0x000e0f0f);
	native_l2_write(0x589, 0x601);
	if (!n->domain) {
		writel_relaxed(0xf, base + 0x160);
		mb();
	}
	native_l2_write(0x580, 0x002c5ffd);
	if (n->domain) {
		writel_relaxed(0xf, base + 0x160);
		mb();
	}
	writel_relaxed(0x00030005, base + 0x164);
	mb();
	call->result = native_l2_read(0x581) == 0x6a11 &&
		native_l2_read(0x584) == 0x000e0f0f &&
		native_l2_read(0x589) == 0x601 &&
		native_l2_read(0x580) == 0x002c5ffd &&
		readl_relaxed(base + 0x160) == 0xf &&
		readl_relaxed(base + 0x164) == 0x00030005 ? 0 : -EIO;
	raw_spin_unlock_irqrestore(&native_acd_lock, flags);
}

static int native_acd(struct msm8996_cpu_native *n)
{
	struct native_acd_call call = { .native = n, .result = -ENODEV };
	cpumask_t mask;
	unsigned int cpu;
	int ret;

	cpumask_clear(&mask);
	cpus_read_lock();
	for_each_online_cpu(cpu)
		if ((cpu_logical_map(cpu) & 0xfff) >> 8 == n->domain &&
		    (cpu_logical_map(cpu) & 0xff) <= 1)
			cpumask_set_cpu(cpu, &mask);
	ret = cpumask_empty(&mask) ? -ENODEV :
		smp_call_function_any(&mask, native_acd_cpu, &call, 1);
	cpus_read_unlock();
	return ret ? ret : call.result;
}

static int native_rate(struct msm8996_cpu_native *n, unsigned long rate, bool safe)
{
	unsigned long old, observed;
	unsigned long pll_rate = rate <= MAIN_MAX ? rate * 2 : rate;
	bool cbf = n->domain == 2;
	int ret = native_guard(n);

	if (ret)
		return ret;
	ret = native_read(n->shared, n->domain, &old);
	if (ret)
		return ret;
	/* Source AUX is 300MHz. Never raise an unowned/uncertain output. */
	if (old < 300000000 || (safe && (rate > old || rate > MAIN_MAX)))
		return -ERANGE;
	if (!safe) {
		ret = n->shared->power->ready(n->shared->power_data);
		if (ret)
			return ret;
	}
	if (rate != 300000000 &&
	    (pll_rate < 600000000 || pll_rate > 3000000000UL ||
	     pll_rate % n->shared->maps.xo))
		return -EINVAL;
	/* Park on confirmed AUX before changing L. LF and HF are separate
	 * source RMW operations; automatic-selection fields are preserved.
	 */
	ret = cbf ? native_mux(n, 3, 3) : native_mux(n, 0xc, 0xc);
	if (!ret && !cbf)
		ret = native_mux(n, 3, 0);
	if (!ret)
		ret = native_read(n->shared, n->domain, &observed);
	if (!ret && observed != 300000000)
		ret = -EIO;
	if (ret || rate == 300000000)
		return ret ? ret : native_peers(n);
	ret = native_primary(n, pll_rate);
	if (ret)
		return ret;
	if (!cbf && rate > MAIN_MAX) {
		ret = native_acd(n);
		if (!ret)
			ret = native_mux(n, 3, 2);
	} else if (cbf) {
		ret = native_mux(n, 3, rate <= MAIN_MAX ? 2 : 1);
	} else {
		ret = native_mux(n, 0xc, 4);
	}
	if (!ret)
		ret = native_read(n->shared, n->domain, &observed);
	if (!ret && observed != rate)
		ret = -EIO;
	return ret ? ret : native_peers(n);
}

static int native_safe(void *data, unsigned long rate)
{
	return native_rate(data, rate, true);
}

static int native_program(void *data, const struct msm8996_cpu_plan *plan)
{
	struct msm8996_cpu_plan selected;
	int ret;

	if (!plan)
		return -EINVAL;
	ret = native_lookup(data, plan->rate, true, &selected);
	if (!ret && selected.level != plan->level)
		ret = -EINVAL;
	return ret ? ret : native_rate(data, plan->rate, false);
}

static int native_div2(void *data, unsigned long rate)
{
	struct msm8996_cpu_native *n = data;
	unsigned long old;
	u32 user;
	void __iomem *base = native_base(n->shared, n->domain);
	int ret = native_read_rate(data, &old);

	if (ret || old != rate * 2)
		return ret ? ret : -EINVAL;
	user = readl_relaxed(base + (n->domain == 2 ? 0xf018 : 0x10));
	if (msm8996_boot_main_div(user) != 2)
		return -EOPNOTSUPP;
	/* The source div2 transition starts on primary early/ACD. A main or
	 * alternate boot leg cannot be interpreted as the primary early rate.
	 */
	user = readl_relaxed(base + native_mux_offset(n->domain)) & 3;
	if (n->domain == 2 ? user != 1 : (user != 1 && user != 2))
		return -EOPNOTSUPP;
	ret = n->domain == 2 ? native_mux(n, 3, 2) : native_mux(n, 0xc, 4);
	if (!ret && n->domain != 2)
		ret = native_mux(n, 3, 0);
	if (!ret)
		ret = native_read_rate(n, &old);
	return ret ? ret : old == rate ? native_peers(n) : -EIO;
}

static int native_read_alt(void *data, unsigned long *rate)
{
	struct msm8996_cpu_native *n = data;
	void __iomem *base = native_base(n->shared, n->domain) + 0x100;
	u32 mode, user, l;
	int ret = native_guard(n);

	if (ret || n->domain != 1)
		return ret ? ret : -EINVAL;
	mode = readl_relaxed(base);
	user = readl_relaxed(base + 0x10);
	l = readl_relaxed(base + 4);
	if ((mode & (LOCK | FSM | ACTIVE | OFFLINE | OFFLINE_ACK)) != (LOCK | FSM | ACTIVE) ||
	    (user & 0xf) != 9 || msm8996_boot_main_div(user) != 2 ||
	    readl_relaxed(base + 8) || readl_relaxed(base + 0xc))
		return -EOPNOTSUPP;
	rmb();
	if (mode != readl_relaxed(base) || user != readl_relaxed(base + 0x10) ||
	    l != readl_relaxed(base + 4))
		return -EAGAIN;
	*rate = l * n->shared->maps.xo;
	return 0;
}

static int native_set_alt(void *data, unsigned long rate)
{
	struct msm8996_cpu_native *n = data;
	void __iomem *base = native_base(n->shared, n->domain) + 0x100;
	u32 mode, user, vco, target_user;
	unsigned long observed;
	int ret = native_guard(n);

	if (ret || n->domain != 1)
		return ret ? ret : -EINVAL;
	if (rate < 250000000 || rate > 2150400000UL || rate % n->shared->maps.xo)
		return -ERANGE;
	if ((readl_relaxed(native_base(n->shared, 1) + 0x40) & 3) == 3)
		return -EBUSY;
	user = readl_relaxed(base + 0x10);
	if ((user & 0xf) != 9 || msm8996_boot_main_div(user) != 2 ||
	    readl_relaxed(base + 0x18) != 0x4001051b)
		return -EOPNOTSUPP;
	ret = n->shared->power->ready(n->shared->power_data);
	if (ret)
		return ret;
	mode = readl_relaxed(base);
	writel_relaxed(mode | OFFLINE, base);
	mb();
	ret = native_poll(base, OFFLINE_ACK, OFFLINE_ACK);
	if (ret)
		return ret;
	mode = readl_relaxed(base) & ~FSM; /* v3 retains OFFLINE until enable */
	writel_relaxed(mode, base);
	mb();
	ret = native_poll(base, ACTIVE, 0);
	if (ret)
		return ret;
	vco = rate < 500000000 ? 3 : rate < 750000000 ? 2 : rate < 1000000000 ? 1 : 0;
	writel_relaxed(rate / n->shared->maps.xo, base + 4);
	writel_relaxed(0, base + 8);
	writel_relaxed(0, base + 0xc);
	target_user = (user & ~GENMASK(21, 20)) | vco << 20 | BIT(24);
	writel_relaxed(target_user, base + 0x10);
	mb();
	writel_relaxed((readl_relaxed(base) | FSM) & ~OFFLINE, base);
	mb();
	ret = native_poll(base, LOCK | ACTIVE | OFFLINE_ACK, LOCK | ACTIVE);
	if (!ret && readl_relaxed(base + 0x10) != target_user)
		ret = -EIO;
	if (!ret)
		ret = native_read_alt(n, &observed);
	if (!ret && observed != rate)
		ret = -EIO;
	return ret ? ret : native_peers(n);
}

static int native_vote(void *data, unsigned int lease,
		       const struct msm8996_cpu_plan *plan, bool enable)
{
	struct msm8996_cpu_native *n = data;
	int ret = native_guard(n);

	if (lease > 1)
		return -EINVAL;
	return ret ? ret : n->shared->power->vote(n->shared->power_data,
					       n->domain, lease, plan, enable);
}

static int native_ready(void *data)
{
	struct msm8996_cpu_native *n = data;
	int ret = native_guard(n);

	if (!ret)
		ret = n->shared->power->ready(n->shared->power_data);
	return ret ? ret : native_peers(n);
}

static const struct msm8996_cpu_transaction_ops native_ops = {
	.lookup = native_lookup, .enter = native_enter, .leave = native_leave,
	.read_rate = native_read_rate, .safe = native_safe, .program = native_program,
	.div2 = native_div2, .read_alt = native_read_alt, .set_alt = native_set_alt,
	.vote = native_vote, .power_ready = native_ready,
};

int msm8996_cpu_native_shared_init(struct msm8996_cpu_native_shared *s,
		const struct msm8996_cpu_native_maps *maps,
		const struct msm8996_cpu_hw_plans *plans,
		const struct msm8996_cpu_native_power_ops *power, void *data)
{
	struct msm8996_cpu_plan check;
	unsigned int i;
	int ret = 0;

	if (!s || !maps || !plans || !power || !power->enter || !power->leave ||
	    !power->vote || !power->ready)
		return -EPROBE_DEFER;
	if (maps->xo != 19200000 || IS_ERR_OR_NULL(maps->apcc[0]) ||
	    IS_ERR_OR_NULL(maps->apcc[1]) || IS_ERR_OR_NULL(maps->cbf) ||
	    IS_ERR_OR_NULL(maps->aux) || IS_ERR_OR_NULL(maps->gcc) ||
	    maps->apcc[0] == maps->apcc[1] || maps->apcc[0] == maps->cbf ||
	    maps->apcc[1] == maps->cbf)
		return -EINVAL;
	for (i = 0; i < 3; i++) {
		ret = msm8996_cpu_plan_lookup(plans->domain[i], plans->count[i],
			plans->speed_bin, 1, true, &check);
		if (ret)
			return ret;
	}
	mutex_lock(&native_owner_lock);
	if (native_owner) {
		ret = -EBUSY;
		goto out;
	}
	mutex_init(&s->lock);
	s->maps = *maps;
	s->plans = *plans;
	s->power = power;
	s->power_data = data;
	s->initialized = true;
	native_owner = s;
out:
	mutex_unlock(&native_owner_lock);
	return ret;
}

int msm8996_cpu_native_bind(struct msm8996_cpu_native *n,
		struct msm8996_cpu_native_shared *s, unsigned int d,
		struct msm8996_cpu_transaction *txn)
{
	struct msm8996_cpu_transaction_policy policy = {
		.aux_rate = 300000000, .do_half_rate = true,
	};
	int ret;

	if (!n || !s || !txn || !s->initialized || s != native_owner || d > 2)
		return -EINVAL;
	mutex_lock(&s->lock);
	if (s->bound[d]) {
		ret = -EBUSY;
		goto out;
	}
	n->shared = s;
	n->domain = d;
	policy.has_acd = d != 2;
	policy.has_alt_pll = d == 1;
	ret = msm8996_cpu_transaction_init(txn, &native_ops, n, &policy);
	if (!ret)
		s->bound[d] = true;
out:
	mutex_unlock(&s->lock);
	return ret;
}

static int native_pm(struct msm8996_cpu_native_shared *s, bool resume)
{
	unsigned long rates[3];
	unsigned int i;
	int ret = -EBUSY;

	if (!s || !s->initialized)
		return -EINVAL;
	mutex_lock(&s->lock);
	if (s->owner || s->frozen != resume)
		goto out;
	ret = s->power->enter(s->power_data);
	if (ret)
		goto out;
	ret = s->power->ready(s->power_data);
	for (i = 0; !ret && i < 3; i++) {
		ret = native_read(s, i, &rates[i]);
		if (!ret && resume && rates[i] != s->suspend_rate[i])
			ret = -EIO;
	}
	if (!ret) {
		memcpy(s->suspend_rate, rates, sizeof(rates));
		s->frozen = !resume;
	}
	s->power->leave(s->power_data);
out:
	mutex_unlock(&s->lock);
	return ret;
}

int msm8996_cpu_native_suspend(struct msm8996_cpu_native_shared *s)
{
	return native_pm(s, false);
}

int msm8996_cpu_native_resume(struct msm8996_cpu_native_shared *s)
{
	return native_pm(s, true);
}
