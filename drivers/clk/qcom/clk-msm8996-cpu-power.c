// SPDX-License-Identifier: GPL-2.0-only
/* Sony4.4 0c295b15 MSM8996-v3 CPU resource ownership, observational phase.
 * The original S11 CPR gang and MX S2 AO contracts are NOT the GCC CX vote.
 * Native PLL/voltage transactions remain unbound until LMHv1, autonomous
 * SAW/CPR and collapse/retention exclusion are implemented and qualified.
 */
#include <linux/atomic.h>
#include <linux/clk.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regulator/qcom-cpr3-cpu.h>
#include <linux/regulator/qcom-rpm-corner.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <dt-bindings/clock/qcom,gcc-msm8996.h>

#include "clk-msm8996-cpu-power.h"

#define CPU_POWER_DOMAINS 3
#define CPU_POWER_PLANS 32
#define CPU_POWER_ALL 7
#define CPU_POWER_CPR BIT(0)
#define CPU_POWER_MX BIT(1)
#define CPU_POWER_LMH BIT(2)
#define CPU_POWER_AUTONOMOUS BIT(3)
#define CPU_POWER_LEASES BIT(4)
#define CPU_POWER_PEERS BIT(5)
#define CPU_POWER_FAULT BIT(6)
#define CPU_POWER_SUSPENDED BIT(7)

struct msm8996_cpu_power_port {
	struct device *dev;
	unsigned int mask;
	bool claimed, retired, suspended;
};

/* Permanently bounded SoC owner/ports. A port leaving does not release the
 * parents of running CPUs or let a new probe discard their unknown state.
 * CCF recalc never takes owner_lock: attach must call clock APIs. The short
 * observation lock covers only immutable plan publication and rate validation;
 * no CCF, regulator, OF or sleeping call is made while it is held.
 */
static DEFINE_MUTEX(cpu_power_lock);
static DEFINE_RAW_SPINLOCK(cpu_power_observation_lock);
static struct msm8996_cpu_power_port cpu_power_port[2];
static struct clk *cpu_power_parent[2];
static bool cpu_power_prepared[2];
static struct msm8996_cpu_plan cpu_power_plan[CPU_POWER_DOMAINS][CPU_POWER_PLANS];
static struct msm8996_cpu_hw_plans cpu_power_plans;
static bool cpu_power_plans_valid;
static unsigned long cpu_power_rate[CPU_POWER_DOMAINS];
static atomic_t cpu_power_fault = ATOMIC_INIT(0);
static int cpu_power_reg_error[4] = {
	-EPROBE_DEFER, -EPROBE_DEFER, -EPROBE_DEFER, -EPROBE_DEFER,
};
static const char * const cpu_power_supply[] = {
	"vdd-pwrcl", "vdd-perfcl", "vdd-cbf", "vdd-dig",
};

static int cpu_power_fail(int error)
{
	if (error >= 0)
		error = -EIO;
	atomic_cmpxchg(&cpu_power_fault, 0, error);
	return atomic_read(&cpu_power_fault);
}

static bool cpu_power_dora(void)
{
	return of_machine_is_compatible("sony,dora-row") ||
		of_machine_is_compatible("somc,dora-row");
}

static int cpu_power_supplies(struct device *dev)
{
	static const char * const expected[] = {
		"apc0_pwrcl_corner", "apc1_corner", "apc0_cbf_corner",
		"pm8994_s2_corner_ao",
	};
	struct device_node *node, *parent;
	const char *name;
	char property[32];
	u32 value;
	unsigned int i;
	int ret;

	for (i = 0; i < ARRAY_SIZE(cpu_power_supply); i++) {
		snprintf(property, sizeof(property), "%s-supply", cpu_power_supply[i]);
		node = of_parse_phandle(dev->of_node, property, 0);
		if (!node)
			return -EINVAL;
		ret = of_property_read_string(node, "regulator-name", &name);
		if (!ret && strcmp(name, expected[i]))
			ret = -EINVAL;
		if (!ret && i == 3) {
			/* CPU PLL digital LOW is logical SVS_SOC=4 (wire3),
			 * ACTIVE-only MX S2. Never substitute CX or CPR plan.level.
			 */
			ret = of_property_read_u32(node, "qcom,set", &value);
			if (!ret && (value != 1 || !of_property_read_bool(node,
							"qcom,use-voltage-corner")))
				ret = -EINVAL;
			parent = of_get_parent(node);
			if (!parent) {
				ret = -EINVAL;
			} else {
				if (!ret)
					ret = of_property_read_u32(parent, "qcom,resource-id", &value);
				if (!ret && value != 2)
					ret = -EINVAL;
				if (!ret)
					ret = of_property_read_string(parent, "qcom,resource-name", &name);
				if (!ret && strcmp(name, "smpa"))
					ret = -EINVAL;
				of_node_put(parent);
			}
		}
		of_node_put(node);
		if (ret)
			return ret;
	}
	return 0;
}

static int cpu_power_link_aux(struct device *dev)
{
	struct of_phandle_args spec;
	struct platform_device *supplier;
	struct device_link *link;
	int ret;

	ret = of_parse_phandle_with_args(dev->of_node, "clocks", "#clock-cells", 1, &spec);
	if (ret)
		return ret;
	if (spec.args_count != 1 || spec.args[0] != GPLL0 ||
	    !of_device_is_compatible(spec.np, "qcom,gcc-msm8996")) {
		of_node_put(spec.np);
		return -EINVAL;
	}
	supplier = of_find_device_by_node(spec.np);
	of_node_put(spec.np);
	if (!supplier)
		return -EPROBE_DEFER;
	device_lock(&supplier->dev);
	if (!supplier->dev.driver) {
		ret = -EPROBE_DEFER;
	} else {
		link = device_link_add(dev, &supplier->dev, DL_FLAG_AUTOREMOVE_CONSUMER);
		ret = link ? 0 : -ENOMEM;
	}
	device_unlock(&supplier->dev);
	put_device(&supplier->dev);
	return ret;
}

static int cpu_power_copy_plans(const struct msm8996_cpu_hw_plans *plans)
{
	struct msm8996_cpu_plan selected;
	unsigned long flags;
	unsigned int i;
	int ret;

	if (!plans)
		return -EINVAL;
	for (i = 0; i < CPU_POWER_DOMAINS; i++) {
		if (plans->count[i] > CPU_POWER_PLANS)
			return -EINVAL;
		ret = msm8996_cpu_plan_lookup(plans->domain[i], plans->count[i],
			plans->speed_bin, 1, true, &selected);
		if (ret)
			return ret;
	}
	raw_spin_lock_irqsave(&cpu_power_observation_lock, flags);
	if (cpu_power_plans_valid) {
		ret = -EBUSY;
		goto out;
	}
	/* CBF may have published a rate before APCC selected the common plans.
	 * Publication and observation share this lock, so neither can miss the
	 * other's rate/plan update. Published arrays are never rewritten.
	 */
	for (i = 0; i < CPU_POWER_DOMAINS; i++) {
		if (!cpu_power_rate[i])
			continue;
		ret = msm8996_cpu_plan_lookup(plans->domain[i], plans->count[i],
			plans->speed_bin, cpu_power_rate[i], true, &selected);
		if (ret) {
			ret = cpu_power_fail(ret);
			goto out;
		}
	}
	for (i = 0; i < CPU_POWER_DOMAINS; i++) {
		memcpy(cpu_power_plan[i], plans->domain[i],
		       plans->count[i] * sizeof(*plans->domain[i]));
		cpu_power_plans.domain[i] = cpu_power_plan[i];
		cpu_power_plans.count[i] = plans->count[i];
	}
	cpu_power_plans.speed_bin = plans->speed_bin;
	smp_store_release(&cpu_power_plans_valid, true);
	ret = 0;
out:
	raw_spin_unlock_irqrestore(&cpu_power_observation_lock, flags);
	return ret;
}

struct msm8996_cpu_power_port *msm8996_cpu_power_attach(struct device *dev,
		bool cbf, struct clk *xo, struct clk *aux,
		const struct msm8996_cpu_hw_plans *plans)
{
	struct msm8996_cpu_power_port *port = &cpu_power_port[cbf];
	struct clk *parent[2];
	unsigned int i;
	int ret;

	if (!cpu_power_dora())
		return NULL;
	if (!dev || !dev->of_node || IS_ERR_OR_NULL(xo) || IS_ERR_OR_NULL(aux))
		return ERR_PTR(-EINVAL);
	if (!cbf) {
		ret = cpu_power_supplies(dev);
		if (ret)
			return ERR_PTR(ret);
	}
	ret = cpu_power_link_aux(dev);
	if (ret)
		return ERR_PTR(ret);
	mutex_lock(&cpu_power_lock);
	ret = atomic_read(&cpu_power_fault);
	if (ret)
		goto error;
	if (port->claimed) {
		ret = port->retired ? -ESHUTDOWN : -EBUSY;
		goto error;
	}
	parent[0] = clk_get(dev, "xo");
	if (IS_ERR(parent[0])) {
		ret = PTR_ERR(parent[0]);
		goto error;
	}
	parent[1] = clk_get(dev, "aux");
	if (IS_ERR(parent[1])) {
		ret = PTR_ERR(parent[1]);
		clk_put(parent[0]);
		goto error;
	}
	if (!clk_is_match(xo, parent[0]) || !clk_is_match(aux, parent[1]) ||
	    clk_get_rate(parent[0]) != 19200000 || clk_get_rate(parent[1]) != 600000000 ||
	    (cpu_power_parent[0] &&
	     (!clk_is_match(parent[0], cpu_power_parent[0]) ||
	      !clk_is_match(parent[1], cpu_power_parent[1])))) {
		clk_put(parent[1]);
		clk_put(parent[0]);
		ret = -EINVAL;
		goto error;
	}
	if (!cbf) {
		ret = cpu_power_copy_plans(plans);
		if (ret) {
			clk_put(parent[1]);
			clk_put(parent[0]);
			goto error;
		}
	}
	/* No hardware operation before identity/plans/supplier validation. */
	get_device(dev);
	__module_get(THIS_MODULE);
	port->dev = dev;
	port->mask = cbf ? BIT(2) : BIT(0) | BIT(1);
	WRITE_ONCE(port->claimed, true);
	if (cpu_power_parent[0]) {
		clk_put(parent[1]);
		clk_put(parent[0]);
	} else {
		cpu_power_parent[0] = parent[0];
		cpu_power_parent[1] = parent[1];
		for (i = 0; i < ARRAY_SIZE(parent); i++) {
			ret = clk_prepare_enable(parent[i]);
			if (ret) {
				ret = cpu_power_fail(ret);
				goto error;
			}
			cpu_power_prepared[i] = true;
		}
	}
	mutex_unlock(&cpu_power_lock);
	return port;
error:
	mutex_unlock(&cpu_power_lock);
	return ERR_PTR(ret);
}

unsigned long msm8996_cpu_power_observe(struct msm8996_cpu_power_port *port,
		unsigned int domain, unsigned long rate)
{
	struct msm8996_cpu_plan selected;
	unsigned long flags;
	int ret;

	if (!port)
		return rate;
	raw_spin_lock_irqsave(&cpu_power_observation_lock, flags);
	if (atomic_read(&cpu_power_fault))
		goto invalid;
	if (domain >= CPU_POWER_DOMAINS || !(port->mask & BIT(domain)) ||
	    !READ_ONCE(port->claimed) || READ_ONCE(port->retired) || !rate) {
		cpu_power_fail(-EIO);
		goto invalid;
	}
	if (smp_load_acquire(&cpu_power_plans_valid)) {
		ret = msm8996_cpu_plan_lookup(cpu_power_plans.domain[domain],
			cpu_power_plans.count[domain], cpu_power_plans.speed_bin,
			rate, true, &selected);
		if (ret) {
			cpu_power_fail(ret);
			goto invalid;
		}
	}
	WRITE_ONCE(cpu_power_rate[domain], rate);
	raw_spin_unlock_irqrestore(&cpu_power_observation_lock, flags);
	return rate;
invalid:
	raw_spin_unlock_irqrestore(&cpu_power_observation_lock, flags);
	return 0;
}

void msm8996_cpu_power_retire(void *data)
{
	struct msm8996_cpu_power_port *port = data;

	if (!port)
		return;
	mutex_lock(&cpu_power_lock);
	WRITE_ONCE(port->retired, true);
	cpu_power_fail(-ESHUTDOWN);
	/* No parent unprepare/put: running CPUs and uncertain firmware state
	 * have no qualified handoff/release protocol. The static SoC tombstone
	 * retains the exact counted references, never a guessed OFF baseline.
	 */
	mutex_unlock(&cpu_power_lock);
}

int msm8996_cpu_power_suspend(struct msm8996_cpu_power_port *port)
{
	int ret;

	if (!port)
		return 0;
	mutex_lock(&cpu_power_lock);
	ret = atomic_read(&cpu_power_fault);
	if (!ret)
		port->suspended = true;
	mutex_unlock(&cpu_power_lock);
	return ret;
}

int msm8996_cpu_power_resume(struct msm8996_cpu_power_port *port)
{
	int ret;

	if (!port)
		return 0;
	mutex_lock(&cpu_power_lock);
	ret = atomic_read(&cpu_power_fault);
	if (!ret)
		port->suspended = false;
	mutex_unlock(&cpu_power_lock);
	return ret;
}

static bool cpu_power_available(struct device_node *node)
{
	struct device_node *parent;
	bool available = true;

	while (node) {
		available &= of_device_is_available(node);
		parent = of_get_parent(node);
		of_node_put(node);
		node = parent;
	}
	return available;
}

static void cpu_power_resources(void)
{
	struct device *dev = cpu_power_port[0].dev;
	struct qcom_cpr3_cpu_state cpr;
	struct qcom_rpm_corner_state mx;
	struct device_node *node;
	char property[32];
	unsigned int i;
	int ret;

	if (!dev || cpu_power_port[0].retired)
		return;
	for (i = 0; i < ARRAY_SIZE(cpu_power_supply); i++) {
		snprintf(property, sizeof(property), "%s-supply", cpu_power_supply[i]);
		node = of_parse_phandle(dev->of_node, property, 0);
		if (!node || !cpu_power_available(of_node_get(node))) {
			of_node_put(node);
			cpu_power_reg_error[i] = -EPROBE_DEFER;
			continue;
		}
		/* A regulator consumer lookup can resolve/enable an upstream supply.
		 * Published-provider snapshots avoid that side effect entirely.
		 */
		if (i < 3) {
			ret = qcom_cpr3_cpu_get_state_by_node(node, &cpr);
			if (!ret && (!cpr.raw_valid || !cpr.enabled))
				ret = -ENODATA;
		} else {
			ret = qcom_rpm_corner_get_state_by_node(node, &mx);
			if (!ret && (mx.resource_type != 0x61706d73 || mx.resource_id != 2 ||
				     mx.key != 0x6e726f63 || mx.sets != 1))
				ret = -EINVAL;
		}
		of_node_put(node);
		cpu_power_reg_error[i] = ret;
	}
}

ssize_t msm8996_cpu_power_format(struct msm8996_cpu_power_port *port,
		char *buf, size_t size)
{
	unsigned int online = 0, suspended = 0, blockers;
	unsigned int i;
	ssize_t length;
	int fault;

	if (!port)
		return -EOPNOTSUPP;
	mutex_lock(&cpu_power_lock);
	fault = atomic_read(&cpu_power_fault);
	if (!fault)
		cpu_power_resources();
	for (i = 0; i < ARRAY_SIZE(cpu_power_port); i++) {
		if (cpu_power_port[i].claimed && !cpu_power_port[i].retired)
			online |= cpu_power_port[i].mask;
		if (cpu_power_port[i].suspended)
			suspended |= cpu_power_port[i].mask;
	}
	/* These are missing real implementations, not switchable DT grants.
	 * Observed endpoint state never stands in for six owned voltage leases.
	 */
	blockers = CPU_POWER_LMH | CPU_POWER_AUTONOMOUS | CPU_POWER_LEASES;
	if (cpu_power_reg_error[0] || cpu_power_reg_error[1] || cpu_power_reg_error[2])
		blockers |= CPU_POWER_CPR;
	if (cpu_power_reg_error[3])
		blockers |= CPU_POWER_MX;
	if (online != CPU_POWER_ALL || !smp_load_acquire(&cpu_power_plans_valid))
		blockers |= CPU_POWER_PEERS;
	if (fault)
		blockers |= CPU_POWER_FAULT;
	if (suspended)
		blockers |= CPU_POWER_SUSPENDED;
	length = scnprintf(buf, size,
		"readonly=1 domains=0x%x suspended=0x%x parents=%u/%u voltage_leases=0 "
		"blockers=0x%x error=%d cpr=%d/%d/%d mx_ao=%d mx_logical_required=4 "
		"rates=%lu/%lu/%lu\n", online, suspended,
		cpu_power_prepared[0], cpu_power_prepared[1], blockers, fault,
		cpu_power_reg_error[0], cpu_power_reg_error[1], cpu_power_reg_error[2],
		cpu_power_reg_error[3], READ_ONCE(cpu_power_rate[0]),
		READ_ONCE(cpu_power_rate[1]), READ_ONCE(cpu_power_rate[2]));
	mutex_unlock(&cpu_power_lock);
	return length;
}
