// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2012-2020, The Linux Foundation. All rights reserved.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/io.h>
#include <linux/delay.h>
#include <linux/sort.h>
#include <linux/kernel_stat.h>
#include <linux/irq_cpustat.h>
#include <linux/slab.h>
#include <linux/jiffies.h>
#include <linux/kthread.h>
#include <linux/mutex.h>
#include <linux/sched.h>
#include <linux/sched/task.h>
#include <linux/interrupt.h>
#include <linux/irq.h>
#include <linux/percpu.h>
#include <linux/of.h>
#include <linux/cpu.h>
#include <linux/cpu_pm.h>
#include <linux/platform_device.h>
#include <linux/wait.h>
#include <soc/qcom/scm.h>
#include <soc/qcom/memory_dump.h>
#include <soc/qcom/minidump.h>
#include <soc/qcom/watchdog.h>
#include <linux/dma-mapping.h>
#include <linux/sched/clock.h>
#include <linux/math64.h>
#include <linux/cpumask.h>
#include <linux/rcupdate.h>
#include <uapi/linux/sched/types.h>
#ifdef CONFIG_QCOM_INITIAL_LOGBUF
#include <linux/kallsyms.h>
#endif

#define MODULE_NAME "msm_watchdog"
#define WDT0_ACCSCSSNBARK_INT 0
#define TCSR_WDT_CFG	0x30
#define WDT0_RST	0x04
#define WDT0_EN		0x08
#define WDT0_STS	0x0C
#define WDT0_BARK_TIME	0x10
#define WDT0_BITE_TIME	0x14

#define WDOG_ABSENT	0

#define EN		0
#define UNMASKED_INT_EN 1

#define MASK_SIZE		32
#define SCM_SET_REGSAVE_CMD	0x2
#define SCM_SVC_SEC_WDOG_DIS	0x7
#define MAX_CPU_CTX_SIZE	2048
#define NR_TOP_HITTERS		10
#define COMPARE_RET		-1

typedef int (*compare_t) (const void *lhs, const void *rhs);

#ifdef CONFIG_QCOM_INITIAL_LOGBUF
#define LOGBUF_TIMEOUT		100000U

static struct delayed_work log_buf_work;
static char *init_log_buf;
static unsigned int *log_buf_size;
static dma_addr_t log_buf_paddr;
static struct device *log_buf_dev;
static struct md_region log_buf_region;
static bool log_buf_registered;
#endif

static struct msm_watchdog_data __rcu *wdog_data;
static DEFINE_MUTEX(wdog_probe_lock);

static int cpu_idle_pc_state[NR_CPUS];

struct irq_info {
	unsigned int irq;
	unsigned int total_count;
	unsigned int irq_counter[NR_CPUS];
};

/*
 * user_pet_enable:
 *	Require userspace to write to a sysfs file every pet_time milliseconds.
 *	Disabled by default on boot.
 */
struct msm_watchdog_data {
	phys_addr_t phys_base;
	size_t size;
	void __iomem *base;
	void __iomem *wdog_absent_base;
	struct device *dev;
	unsigned int pet_time;
	unsigned int bark_time;
	int bark_irq;
	int bite_irq;
	bool do_ipi_ping;
	bool wakeup_irq_enable;
	unsigned long long last_pet;
	unsigned int min_slack_ticks;
	unsigned long long min_slack_ns;
	void *scm_regsave;
	cpumask_t alive_mask;
	struct mutex disable_lock;
	raw_spinlock_t hw_lock;
	bool irq_ppi;
	bool irq_requested;
	bool panic_registered;
	bool cpu_pm_registered;
	bool sysfs_created;
	bool userspace_sysfs;
	bool ready;
	bool stopping;
	bool suspended;
	bool minidump_registered;
	struct md_region minidump;
	u32 bark_ticks;
	u32 bite_ticks;
	struct msm_watchdog_data __percpu **wdog_cpu_dd;
	struct notifier_block panic_blk;

	bool enabled;
	bool user_pet_enabled;

	struct task_struct *watchdog_task;
	struct timer_list pet_timer;
	wait_queue_head_t pet_complete;

	bool timer_expired;
	bool user_pet_complete;
	unsigned long long timer_fired;
	unsigned long long thread_start;
	unsigned long long ping_start[NR_CPUS];
	unsigned long long ping_end[NR_CPUS];
	struct work_struct irq_counts_work;
	struct irq_info irq_counts[NR_TOP_HITTERS];
	struct irq_info ipi_counts[NR_IPI];
	unsigned int tot_irq_count[NR_CPUS];
	atomic_t irq_counts_running;
};

/*
 * On the kernel command line specify
 * watchdog_v2.enable=1 to enable the watchdog
 * By default watchdog is turned on
 */
static int enable = 1;
module_param(enable, int, 0000);

/*
 * On the kernel command line specify
 * watchdog_v2.WDT_HZ=<clock val in HZ> to set Watchdog
 * ticks. By default it is set to 32765.
 */
static long WDT_HZ = 32765;
module_param(WDT_HZ, long, 0000);

/*
 * Watchdog ipi optimization:
 * Does not ping cores in low power mode at pet time to save power.
 * This feature is enabled by default.
 *
 * On the kernel command line specify
 * watchdog_v2.ipi_en=1 to disable this optimization.
 * Or, can be turned off, by enabling CONFIG_QCOM_WDOG_IPI_ENABLE.
 */
#ifdef CONFIG_QCOM_WDOG_IPI_ENABLE
#define IPI_CORES_IN_LPM 1
#else
#define IPI_CORES_IN_LPM 0
#endif

static int ipi_en = IPI_CORES_IN_LPM;
module_param(ipi_en, int, 0444);

static void dump_cpu_alive_mask(struct msm_watchdog_data *wdog_dd)
{
	static char alive_mask_buf[MASK_SIZE];

	scnprintf(alive_mask_buf, MASK_SIZE, "%*pb1", cpumask_pr_args(
				&wdog_dd->alive_mask));
	dev_info(wdog_dd->dev, "cpu alive mask from last pet %s\n",
				alive_mask_buf);
}

static int msm_watchdog_suspend(struct device *dev)
{
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);
	unsigned long flags;

	raw_spin_lock_irqsave(&wdog_dd->hw_lock, flags);
	if (!wdog_dd->ready || wdog_dd->stopping || !enable)
		goto out;
	__raw_writel(1, wdog_dd->base + WDT0_RST);
	if (!wdog_dd->wakeup_irq_enable) {
		__raw_writel(0, wdog_dd->base + WDT0_EN);
		wdog_dd->enabled = false;
	}
	mb();
	wdog_dd->suspended = true;
	wdog_dd->last_pet = sched_clock();
out:
	raw_spin_unlock_irqrestore(&wdog_dd->hw_lock, flags);
	return 0;
}

static int msm_watchdog_resume(struct device *dev)
{
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);
	unsigned long flags;

	raw_spin_lock_irqsave(&wdog_dd->hw_lock, flags);
	/* A concurrent disable must never be undone by resume. */
	if (!wdog_dd->ready || wdog_dd->stopping || !enable)
		goto out;
	if (!wdog_dd->wakeup_irq_enable)
		__raw_writel(BIT(EN), wdog_dd->base + WDT0_EN);
	__raw_writel(1, wdog_dd->base + WDT0_RST);
	mb();
	wdog_dd->enabled = true;
	wdog_dd->suspended = false;
	wdog_dd->last_pet = sched_clock();
out:
	raw_spin_unlock_irqrestore(&wdog_dd->hw_lock, flags);
	return 0;
}

static int panic_wdog_handler(struct notifier_block *this,
			      unsigned long event, void *ptr)
{
	struct msm_watchdog_data *wdog_dd = container_of(this,
				struct msm_watchdog_data, panic_blk);
	if (!READ_ONCE(wdog_dd->ready) || READ_ONCE(wdog_dd->stopping))
		return NOTIFY_DONE;
	if (panic_timeout == 0) {
		__raw_writel(0, wdog_dd->base + WDT0_EN);
		/* Make sure watchdog is enabled before notifying the caller */
		mb();
	} else {
		__raw_writel(WDT_HZ * (panic_timeout + 10),
				wdog_dd->base + WDT0_BARK_TIME);
		__raw_writel(WDT_HZ * (panic_timeout + 10),
				wdog_dd->base + WDT0_BITE_TIME);
		__raw_writel(1, wdog_dd->base + WDT0_RST);
	}
	return NOTIFY_DONE;
}

static void wdog_disable(struct msm_watchdog_data *wdog_dd)
{
	unsigned long flags;

	/* disable_lock excludes the worker's pet/rearm transaction. The worker
	 * is stopped outside that mutex, so kthread_stop cannot deadlock on it.
	 */
	WRITE_ONCE(wdog_dd->stopping, true);
	wake_up(&wdog_dd->pet_complete);
	raw_spin_lock_irqsave(&wdog_dd->hw_lock, flags);
	__raw_writel(0, wdog_dd->base + WDT0_EN);
	mb();
	wdog_dd->enabled = false;
	enable = 0;
	raw_spin_unlock_irqrestore(&wdog_dd->hw_lock, flags);
	del_timer_sync(&wdog_dd->pet_timer);
	pr_info("MSM Apps Watchdog deactivated.\n");
}

static ssize_t wdog_disable_get(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	int ret;
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);

	mutex_lock(&wdog_dd->disable_lock);
	ret = snprintf(buf, PAGE_SIZE, "%d\n", enable == 0 ? 1 : 0);
	mutex_unlock(&wdog_dd->disable_lock);
	return ret;
}

static ssize_t wdog_disable_set(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	int ret;
	u8 disable;
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);
	struct scm_desc desc = {0};

	ret = kstrtou8(buf, 10, &disable);
	if (ret) {
		dev_err(wdog_dd->dev, "invalid user input\n");
		return ret;
	}
	if (disable == 1) {
		mutex_lock(&wdog_dd->disable_lock);
		if (!wdog_dd->ready) {
			mutex_unlock(&wdog_dd->disable_lock);
			return -ENODEV;
		}
		if (enable == 0) {
			pr_info("MSM Apps Watchdog already disabled\n");
			mutex_unlock(&wdog_dd->disable_lock);
			return count;
		}
		disable = 1;

		desc.args[0] = 1;
		desc.arginfo = SCM_ARGS(1);
		ret = scm_call2(SCM_SIP_FNID(SCM_SVC_BOOT,
						SCM_SVC_SEC_WDOG_DIS), &desc);
		if (ret) {
			dev_err(wdog_dd->dev,
					"Failed to deactivate secure wdog\n");
			mutex_unlock(&wdog_dd->disable_lock);
			return -EIO;
		}
		wdog_disable(wdog_dd);
		mutex_unlock(&wdog_dd->disable_lock);
	} else {
		pr_err("invalid operation, only disable = 1 supported\n");
		return -EINVAL;
	}
	return count;
}

static DEVICE_ATTR(disable, 0600, wdog_disable_get, wdog_disable_set);

/*
 * Userspace Watchdog Support:
 * Write 1 to the "user_pet_enabled" file to enable hw support for a
 * userspace watchdog.
 * Userspace is required to pet the watchdog by continuing to write 1
 * to this file in the expected interval.
 * Userspace may disable this requirement by writing 0 to this same
 * file.
 */
static void __wdog_user_pet(struct msm_watchdog_data *wdog_dd)
{
	WRITE_ONCE(wdog_dd->user_pet_complete, true);
	wake_up(&wdog_dd->pet_complete);
}

static ssize_t wdog_user_pet_enabled_get(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	int ret;
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);

	ret = snprintf(buf, PAGE_SIZE, "%d\n",
			wdog_dd->user_pet_enabled);
	return ret;
}

static ssize_t wdog_user_pet_enabled_set(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	int ret;
	bool requested;
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);

	ret = strtobool(buf, &requested);
	if (ret) {
		dev_err(wdog_dd->dev, "invalid user input\n");
		return ret;
	}

	mutex_lock(&wdog_dd->disable_lock);
	if (!wdog_dd->ready || wdog_dd->stopping) {
		mutex_unlock(&wdog_dd->disable_lock);
		return -ESHUTDOWN;
	}
	WRITE_ONCE(wdog_dd->user_pet_enabled, requested);
	__wdog_user_pet(wdog_dd);
	mutex_unlock(&wdog_dd->disable_lock);

	return count;
}

static DEVICE_ATTR(user_pet_enabled, 0600, wdog_user_pet_enabled_get,
						wdog_user_pet_enabled_set);

static ssize_t wdog_pet_time_get(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	int ret;
	struct msm_watchdog_data *wdog_dd = dev_get_drvdata(dev);

	ret = snprintf(buf, PAGE_SIZE, "%d\n", wdog_dd->pet_time);
	return ret;
}

static DEVICE_ATTR(pet_time, 0400, wdog_pet_time_get, NULL);

static void pet_watchdog(struct msm_watchdog_data *wdog_dd)
{
	u32 count = 0, previous = U32_MAX;
	unsigned int i;
	unsigned long flags;
	u64 now, bark_ns = (u64)wdog_dd->bark_time * NSEC_PER_MSEC;
	u64 slack_ns;
	u32 slack;

	raw_spin_lock_irqsave(&wdog_dd->hw_lock, flags);
	if (!wdog_dd->enabled || wdog_dd->stopping || wdog_dd->suspended)
		goto out;
	/* The counter may keep changing. Diagnostic sampling must not prevent
	 * the actual pet indefinitely; it is not a hardware lock poll.
	 */
	for (i = 0; i < 8; i++) {
		count = (__raw_readl(wdog_dd->base + WDT0_STS) >> 1) & 0xFFFFF;
		if (count == previous)
			break;
		previous = count;
	}
	slack = count < wdog_dd->bark_ticks ? wdog_dd->bark_ticks - count : 0;
	wdog_dd->min_slack_ticks = min(wdog_dd->min_slack_ticks, slack);
	__raw_writel(1, wdog_dd->base + WDT0_RST);
	now = sched_clock();
	slack_ns = now < wdog_dd->last_pet + bark_ns ?
		wdog_dd->last_pet + bark_ns - now : 0;
	wdog_dd->min_slack_ns = min(wdog_dd->min_slack_ns, slack_ns);
	wdog_dd->last_pet = now;
out:
	raw_spin_unlock_irqrestore(&wdog_dd->hw_lock, flags);
}

static void keep_alive_response(void *info)
{
	int cpu = smp_processor_id();
	struct msm_watchdog_data *wdog_dd = (struct msm_watchdog_data *)info;

	cpumask_set_cpu(cpu, &wdog_dd->alive_mask);
	wdog_dd->ping_end[cpu] = sched_clock();
	/* Make sure alive mask is cleared and set in order */
	smp_mb();
}

/*
 * If this function does not return, it implies one of the
 * other cpu's is not responsive.
 */
static void ping_other_cpus(struct msm_watchdog_data *wdog_dd)
{
	int cpu;

	cpumask_clear(&wdog_dd->alive_mask);
	/* Make sure alive mask is cleared and set in order */
	smp_mb();
	for_each_cpu(cpu, cpu_online_mask) {
		if (!cpu_idle_pc_state[cpu] && !cpu_isolated(cpu)) {
			wdog_dd->ping_start[cpu] = sched_clock();
			smp_call_function_single(cpu, keep_alive_response,
						 wdog_dd, 1);
		}
	}
}

static void pet_task_wakeup(struct timer_list *t)
{
	struct msm_watchdog_data *wdog_dd =
		from_timer(wdog_dd, t, pet_timer);
	if (READ_ONCE(wdog_dd->stopping))
		return;
	WRITE_ONCE(wdog_dd->timer_expired, true);
	wdog_dd->timer_fired = sched_clock();
	wake_up(&wdog_dd->pet_complete);
}

static int cmp_irq_info_fn(const void *a, const void *b)
{
	struct irq_info *lhs = (struct irq_info *)a;
	struct irq_info *rhs = (struct irq_info *)b;

	if (lhs->total_count < rhs->total_count)
		return 1;

	if (lhs->total_count > rhs->total_count)
		return COMPARE_RET;

	return 0;
}

static void swap_irq_info_fn(void *a, void *b, int size)
{
	struct irq_info temp;
	struct irq_info *lhs = (struct irq_info *)a;
	struct irq_info *rhs = (struct irq_info *)b;

	temp = *lhs;
	*lhs = *rhs;
	*rhs = temp;
}

static struct irq_info *search(struct irq_info *key, struct irq_info *base,
			       size_t num, compare_t cmp)
{
	struct irq_info *pivot = NULL;
	int result;

	while (num > 0) {
		pivot = base + (num >> 1);
		result = cmp(key, pivot);

		if (result == 0)
			goto out;

		if (result > 0) {
			base = pivot + 1;
			num--;
		}

		if (num)
			num >>= 1;
	}

out:
	return pivot;
}

static void print_irq_stat(struct msm_watchdog_data *wdog_dd)
{
	int index;
	int cpu;
	struct irq_info *info;


	pr_debug("(virq:irq_count)-\n");
	for (index = 0; index < NR_TOP_HITTERS; index++) {
		info = &wdog_dd->irq_counts[index];
		pr_debug("%u:%u\n", info->irq, info->total_count);
	}
	pr_debug("\n");

	pr_debug("(cpu:irq_count)-\n");
	for_each_possible_cpu(cpu)
		pr_debug("%u:%u\n", cpu, wdog_dd->tot_irq_count[cpu]);
	pr_debug("\n");

	pr_debug("(ipi:irq_count)-\n");
	for (index = 0; index < NR_IPI; index++) {
		info = &wdog_dd->ipi_counts[index];
		pr_debug("%u:%u\n", info->irq, info->total_count);
	}
	pr_debug("\n");
}

static void compute_irq_stat(struct work_struct *work)
{
	unsigned int count;
	int index = 0, cpu, irq;
	struct irq_desc *desc;
	struct irq_info *pos;
	struct irq_info *start;
	struct irq_info key = {0};
	unsigned int running;
	struct msm_watchdog_data *wdog_dd = container_of(work,
					    struct msm_watchdog_data,
					    irq_counts_work);

	size_t arr_size = ARRAY_SIZE(wdog_dd->irq_counts);

	/* avoid parallel execution from bark handler and queued
	 * irq_counts_work.
	 */
	running = atomic_xchg(&wdog_dd->irq_counts_running, 1);
	if (running)
		return;

	/* per irq counts */
	rcu_read_lock();
	for_each_irq_nr(irq) {
		desc = irq_to_desc(irq);
		if (!desc)
			continue;

		count = kstat_irqs_usr(irq);
		if (!count)
			continue;

		if (index < arr_size) {
			wdog_dd->irq_counts[index].irq = irq;
			wdog_dd->irq_counts[index].total_count = count;
			for_each_possible_cpu(cpu)
				wdog_dd->irq_counts[index].irq_counter[cpu] =
					*per_cpu_ptr(desc->kstat_irqs, cpu);

			index++;
			if (index == arr_size)
				sort(wdog_dd->irq_counts, arr_size,
				     sizeof(*pos), cmp_irq_info_fn,
				     swap_irq_info_fn);

			continue;
		}

		key.total_count = count;
		start = wdog_dd->irq_counts + (arr_size - 1);
		pos = search(&key, wdog_dd->irq_counts,
			     arr_size, cmp_irq_info_fn);
		pr_debug("*pos:%u key:%u\n",
				pos->total_count, key.total_count);
		if (pos && (pos->total_count >= key.total_count)) {
			if (pos < start)
				pos++;
			else
				pos = NULL;
		}

		pr_debug("count :%u irq:%u\n", count, irq);
		if (pos && pos < start) {
			start--;
			for (; start >= pos ; start--)
				*(start + 1) = *start;
		}

		if (pos) {
			pos->irq = irq;
			pos->total_count = count;
			for_each_possible_cpu(cpu)
				pos->irq_counter[cpu] =
					*per_cpu_ptr(desc->kstat_irqs, cpu);
		}
	}
	rcu_read_unlock();

	/* per cpu total irq counts */
	for_each_possible_cpu(cpu)
		wdog_dd->tot_irq_count[cpu] = kstat_cpu_irqs_sum(cpu);

	/* per IPI counts */
	for (index = 0; index < NR_IPI; index++) {
		wdog_dd->ipi_counts[index].total_count = 0;
		wdog_dd->ipi_counts[index].irq = index;
		for_each_possible_cpu(cpu) {
			wdog_dd->ipi_counts[index].irq_counter[cpu] =
				__IRQ_STAT(cpu, ipi_irqs[index]);
			wdog_dd->ipi_counts[index].total_count +=
				wdog_dd->ipi_counts[index].irq_counter[cpu];
		}
	}

	print_irq_stat(wdog_dd);
	atomic_xchg(&wdog_dd->irq_counts_running, 0);
}

static __ref int watchdog_kthread(void *arg)
{
	struct msm_watchdog_data *wdog_dd = arg;
	struct sched_param param = { .sched_priority = MAX_RT_PRIO - 1 };
	int ret, cpu;

	sched_setscheduler(current, SCHED_FIFO, &param);
	while (!kthread_should_stop()) {
		ret = wait_event_interruptible(wdog_dd->pet_complete,
			READ_ONCE(wdog_dd->timer_expired) ||
			READ_ONCE(wdog_dd->stopping) || kthread_should_stop());
		if (kthread_should_stop() || READ_ONCE(wdog_dd->stopping))
			break;
		if (ret)
			continue;
		wdog_dd->thread_start = sched_clock();
		for_each_cpu(cpu, cpu_present_mask)
			wdog_dd->ping_start[cpu] = wdog_dd->ping_end[cpu] = 0;
		if (wdog_dd->do_ipi_ping)
			ping_other_cpus(wdog_dd);
		ret = wait_event_interruptible(wdog_dd->pet_complete,
			READ_ONCE(wdog_dd->user_pet_complete) ||
			READ_ONCE(wdog_dd->stopping) || kthread_should_stop());
		if (kthread_should_stop() || READ_ONCE(wdog_dd->stopping))
			break;
		if (ret)
			continue;

		mutex_lock(&wdog_dd->disable_lock);
		if (!wdog_dd->stopping && enable) {
			WRITE_ONCE(wdog_dd->timer_expired, false);
			WRITE_ONCE(wdog_dd->user_pet_complete,
				   !READ_ONCE(wdog_dd->user_pet_enabled));
			pet_watchdog(wdog_dd);
			mod_timer(&wdog_dd->pet_timer,
				  jiffies + msecs_to_jiffies(wdog_dd->pet_time));
			queue_work(system_unbound_wq, &wdog_dd->irq_counts_work);
		}
		mutex_unlock(&wdog_dd->disable_lock);
	}
	return 0;
}

static int wdog_cpu_pm_notify(struct notifier_block *self,
			      unsigned long action, void *v)
{
	int cpu;

	cpu = raw_smp_processor_id();

	switch (action) {
	case CPU_PM_ENTER:
		cpu_idle_pc_state[cpu] = 1;
		break;
	case CPU_PM_ENTER_FAILED:
	case CPU_PM_EXIT:
		cpu_idle_pc_state[cpu] = 0;
		break;
	}

	return NOTIFY_OK;
}

static struct notifier_block wdog_cpu_pm_nb = {
	.notifier_call = wdog_cpu_pm_notify,
};

#ifdef CONFIG_QCOM_INITIAL_LOGBUF
static void log_buf_remove(void)
{
	cancel_delayed_work_sync(&log_buf_work);
	if (!init_log_buf)
		return;
	if (log_buf_registered &&
	    msm_minidump_remove_region(&log_buf_region) < 0) {
		/* Do not free memory that firmware can still dump. */
		dev_warn(log_buf_dev, "retaining initial log minidump memory\n");
		return;
	}
	dma_free_coherent(log_buf_dev, *log_buf_size, init_log_buf, log_buf_paddr);
	put_device(log_buf_dev);
	init_log_buf = NULL;
	log_buf_registered = false;
}
#else
static void log_buf_remove(void) { }
#endif


static void msm_watchdog_bite(struct msm_watchdog_data *wdog_dd)
{
	compute_irq_stat(&wdog_dd->irq_counts_work);
	pr_info("Causing a watchdog bite!");
	__raw_writel(1, wdog_dd->base + WDT0_BITE_TIME);
	mb();
	__raw_writel(1, wdog_dd->base + WDT0_RST);
	mb();
	mdelay(10000);
	pr_err("Wdog - STS: 0x%x, CTL: 0x%x, BARK TIME: 0x%x, BITE TIME: 0x%x",
		__raw_readl(wdog_dd->base + WDT0_STS),
		__raw_readl(wdog_dd->base + WDT0_EN),
		__raw_readl(wdog_dd->base + WDT0_BARK_TIME),
		__raw_readl(wdog_dd->base + WDT0_BITE_TIME));
	/* Non-secure bite must reset the subsystem; never return success. */
	while (1)
		udelay(1);
}

void msm_trigger_wdog_bite(void)
{
	struct msm_watchdog_data *wdog_dd;

	rcu_read_lock();
	wdog_dd = rcu_dereference(wdog_data);
	if (wdog_dd && READ_ONCE(wdog_dd->ready) &&
	    !READ_ONCE(wdog_dd->stopping) && READ_ONCE(wdog_dd->enabled))
		msm_watchdog_bite(wdog_dd);
	rcu_read_unlock();
}

static irqreturn_t wdog_bark_handler(int irq, void *dev_id)
{
	struct msm_watchdog_data *wdog_dd = (struct msm_watchdog_data *)dev_id;
	unsigned long nanosec_rem;
	unsigned long long t = sched_clock();

	if (!READ_ONCE(wdog_dd->ready) || READ_ONCE(wdog_dd->stopping))
		return IRQ_NONE;
	nanosec_rem = do_div(t, 1000000000);
	dev_info(wdog_dd->dev, "Watchdog bark! Now = %lu.%06lu\n",
			(unsigned long) t, nanosec_rem / 1000);

	t = READ_ONCE(wdog_dd->last_pet);
	nanosec_rem = do_div(t, 1000000000);
	dev_info(wdog_dd->dev, "Watchdog last pet at %lu.%06lu\n",
			(unsigned long)t, nanosec_rem / 1000);
	if (wdog_dd->do_ipi_ping)
		dump_cpu_alive_mask(wdog_dd);

	msm_watchdog_bite(wdog_dd);
	return IRQ_HANDLED;
}

static irqreturn_t wdog_ppi_bark(int irq, void *dev_id)
{
	struct msm_watchdog_data *wdog_dd =
			*(struct msm_watchdog_data **)(dev_id);
	return wdog_bark_handler(irq, wdog_dd);
}

static void remove_watchdog_sysfs(struct msm_watchdog_data *wdog_dd)
{
	if (!wdog_dd->sysfs_created)
		return;
	if (wdog_dd->userspace_sysfs) {
		device_remove_file(wdog_dd->dev, &dev_attr_user_pet_enabled);
		device_remove_file(wdog_dd->dev, &dev_attr_pet_time);
	}
	device_remove_file(wdog_dd->dev, &dev_attr_disable);
	wdog_dd->sysfs_created = false;
}

static int init_watchdog_sysfs(struct msm_watchdog_data *wdog_dd)
{
	int ret;

	ret = device_create_file(wdog_dd->dev, &dev_attr_disable);
	if (ret)
		return ret;
	wdog_dd->sysfs_created = true;
	if (!of_property_read_bool(wdog_dd->dev->of_node,
				   "qcom,userspace-watchdog"))
		return 0;
	ret = device_create_file(wdog_dd->dev, &dev_attr_pet_time);
	if (ret)
		goto remove_disable;
	ret = device_create_file(wdog_dd->dev, &dev_attr_user_pet_enabled);
	if (ret)
		goto remove_pet;
	wdog_dd->userspace_sysfs = true;
	return 0;
remove_pet:
	device_remove_file(wdog_dd->dev, &dev_attr_pet_time);
remove_disable:
	device_remove_file(wdog_dd->dev, &dev_attr_disable);
	wdog_dd->sysfs_created = false;
	return ret;
}

#ifdef CONFIG_QCOM_INITIAL_LOGBUF
static void log_buf_work_fn(struct work_struct *work)
{
	char **addr;
	int ret;

	addr = (char **)kallsyms_lookup_name("log_buf");
	log_buf_size = (unsigned int *)kallsyms_lookup_name("log_buf_len");
	if (!addr || !log_buf_size || !*log_buf_size) {
		dev_err(log_buf_dev, "initial log buffer symbols unavailable\n");
		return;
	}
	init_log_buf = dma_alloc_coherent(log_buf_dev, *log_buf_size,
					 &log_buf_paddr, GFP_KERNEL);
	if (!init_log_buf) {
		dev_err(log_buf_dev, "initial log allocation failed\n");
		return;
	}
	get_device(log_buf_dev);
	strlcpy(log_buf_region.name, "KBOOT_LOG", sizeof(log_buf_region.name));
	log_buf_region.virt_addr = (uintptr_t)init_log_buf;
	log_buf_region.phys_addr = log_buf_paddr;
	log_buf_region.size = *log_buf_size;
	log_buf_region.id = MINIDUMP_DEFAULT_ID;
	ret = msm_minidump_add_region(&log_buf_region);
	if (ret < 0)
		dev_warn(log_buf_dev, "initial log minidump registration failed: %d\n", ret);
	else
		log_buf_registered = true;
	memcpy(init_log_buf, *addr, *log_buf_size);
}

static void log_buf_init(struct device *dev)
{
	u64 elapsed = div_u64(sched_clock(), NSEC_PER_MSEC);
	unsigned int timeout = elapsed < LOGBUF_TIMEOUT ? LOGBUF_TIMEOUT - elapsed : 0;

	/* A failed firmware retirement retains this bounded original buffer. */
	if (init_log_buf)
		return;
	log_buf_dev = dev;
	INIT_DELAYED_WORK(&log_buf_work, log_buf_work_fn);
	queue_delayed_work(system_unbound_wq, &log_buf_work, msecs_to_jiffies(timeout));
}
#else
static void log_buf_init(struct device *dev) { }
#endif


static void wdog_disable_percpu_irq(void *data)
{
	struct msm_watchdog_data *wdog_dd = data;

	disable_percpu_irq(wdog_dd->bark_irq);
}

static void release_watchdog_irq(struct msm_watchdog_data *wdog_dd)
{
	if (wdog_dd->irq_requested) {
		if (wdog_dd->irq_ppi) {
			/* A process-context cleanup can run on another CPU. */
			on_each_cpu(wdog_disable_percpu_irq, wdog_dd, 1);
			free_percpu_irq(wdog_dd->bark_irq, wdog_dd->wdog_cpu_dd);
		} else {
			devm_free_irq(wdog_dd->dev, wdog_dd->bark_irq, wdog_dd);
		}
		wdog_dd->irq_requested = false;
	}
	if (wdog_dd->wdog_cpu_dd) {
		free_percpu(wdog_dd->wdog_cpu_dd);
		wdog_dd->wdog_cpu_dd = NULL;
	}
}

static void release_watchdog_notifiers(struct msm_watchdog_data *wdog_dd)
{
	if (wdog_dd->cpu_pm_registered) {
		cpu_pm_unregister_notifier(&wdog_cpu_pm_nb);
		wdog_dd->cpu_pm_registered = false;
	}
	if (wdog_dd->panic_registered) {
		atomic_notifier_chain_unregister(&panic_notifier_list,
						 &wdog_dd->panic_blk);
		wdog_dd->panic_registered = false;
	}
}

static int init_watchdog_data(struct msm_watchdog_data *wdog_dd)
{
	u32 val = BIT(EN);
	int ret, cpu;

	/* All worker/IRQ/notifier-visible state exists before requesting IRQs.
	 * No watchdog register is written on any fallible setup path.
	 */
	INIT_WORK(&wdog_dd->irq_counts_work, compute_irq_stat);
	atomic_set(&wdog_dd->irq_counts_running, 0);
	mutex_init(&wdog_dd->disable_lock);
	raw_spin_lock_init(&wdog_dd->hw_lock);
	init_waitqueue_head(&wdog_dd->pet_complete);
	timer_setup(&wdog_dd->pet_timer, pet_task_wakeup, 0);
	wdog_dd->min_slack_ticks = UINT_MAX;
	wdog_dd->min_slack_ns = ULLONG_MAX;
	wdog_dd->user_pet_complete = true;
	wdog_dd->watchdog_task = kthread_create(watchdog_kthread, wdog_dd,
					      "msm_watchdog");
	if (IS_ERR(wdog_dd->watchdog_task)) {
		ret = PTR_ERR(wdog_dd->watchdog_task);
		wdog_dd->watchdog_task = NULL;
		return ret;
	}
	/* The thread can exit after a user disable, before platform remove. */
	get_task_struct(wdog_dd->watchdog_task);
	if (wdog_dd->irq_ppi) {
		wdog_dd->wdog_cpu_dd = alloc_percpu(struct msm_watchdog_data *);
		if (!wdog_dd->wdog_cpu_dd) {
			ret = -ENOMEM;
			goto stop_thread;
		}
		for_each_possible_cpu(cpu)
			*per_cpu_ptr(wdog_dd->wdog_cpu_dd, cpu) = wdog_dd;
		ret = request_percpu_irq(wdog_dd->bark_irq, wdog_ppi_bark,
					"apps_wdog_bark", wdog_dd->wdog_cpu_dd);
	} else {
		ret = devm_request_irq(wdog_dd->dev, wdog_dd->bark_irq,
				       wdog_bark_handler, IRQF_TRIGGER_RISING,
				       "apps_wdog_bark", wdog_dd);
	}
	if (ret)
		goto free_irq;
	wdog_dd->irq_requested = true;
	wdog_dd->panic_blk.notifier_call = panic_wdog_handler;
	ret = atomic_notifier_chain_register(&panic_notifier_list,
					     &wdog_dd->panic_blk);
	if (ret)
		goto free_irq;
	wdog_dd->panic_registered = true;
	if (!ipi_en) {
		ret = cpu_pm_register_notifier(&wdog_cpu_pm_nb);
		if (ret)
			goto unregister_notifiers;
		wdog_dd->cpu_pm_registered = true;
	}
	ret = init_watchdog_sysfs(wdog_dd);
	if (ret)
		goto unregister_notifiers;

	/* Original register layout/sequence. Successful source setup does not
	 * qualify firmware ownership or hardware timing: Dora remains gated.
	 */
	if (wdog_dd->wdog_absent_base)
		__raw_writel(2, wdog_dd->wdog_absent_base + WDOG_ABSENT);
	__raw_writel(wdog_dd->bark_ticks, wdog_dd->base + WDT0_BARK_TIME);
	__raw_writel(wdog_dd->bite_ticks, wdog_dd->base + WDT0_BITE_TIME);
	if (wdog_dd->wakeup_irq_enable)
		val |= BIT(UNMASKED_INT_EN);
	__raw_writel(val, wdog_dd->base + WDT0_EN);
	__raw_writel(1, wdog_dd->base + WDT0_RST);
	mb();
	wdog_dd->last_pet = sched_clock();
	wdog_dd->enabled = true;
	WRITE_ONCE(wdog_dd->ready, true);
	if (wdog_dd->irq_ppi)
		enable_percpu_irq(wdog_dd->bark_irq, 0);
	/* Queue only after the timer, wait queue, work and hardware exist. */
	mod_timer(&wdog_dd->pet_timer,
		  jiffies + msecs_to_jiffies(wdog_dd->pet_time));
	wake_up_process(wdog_dd->watchdog_task);
	dev_info(wdog_dd->dev, "MSM Watchdog Initialized\n");
	return 0;
unregister_notifiers:
	release_watchdog_notifiers(wdog_dd);
free_irq:
	release_watchdog_irq(wdog_dd);
stop_thread:
	kthread_stop(wdog_dd->watchdog_task);
	put_task_struct(wdog_dd->watchdog_task);
	wdog_dd->watchdog_task = NULL;
	return ret;
}

static const struct of_device_id msm_wdog_match_table[] = {
	{ .compatible = "qcom,msm-watchdog" },
	{}
};

static void dump_pdata(struct msm_watchdog_data *pdata)
{
	dev_dbg(pdata->dev, "wdog bark_time %d", pdata->bark_time);
	dev_dbg(pdata->dev, "wdog pet_time %d", pdata->pet_time);
	dev_dbg(pdata->dev, "wdog perform ipi ping %d", pdata->do_ipi_ping);
	dev_dbg(pdata->dev, "wdog base address is 0x%lx\n", (unsigned long)
								pdata->base);
}

static int msm_wdog_dt_to_pdata(struct platform_device *pdev,
				 struct msm_watchdog_data *pdata)
{
	struct device_node *node = pdev->dev.of_node;
	struct resource *res;
	u64 bark, margin;
	int ret;

	/* Keep signed IRQ errors, including EPROBE_DEFER, before any mapping. */
	ret = platform_get_irq(pdev, 0);
	if (ret <= 0)
		return ret ? ret : -EINVAL;
	pdata->bark_irq = ret;
	ret = platform_get_irq(pdev, 1);
	if (ret <= 0)
		return ret ? ret : -EINVAL;
	pdata->bite_irq = ret;
	ret = of_property_read_u32(node, "qcom,bark-time", &pdata->bark_time);
	if (ret)
		return ret;
	ret = of_property_read_u32(node, "qcom,pet-time", &pdata->pet_time);
	if (ret)
		return ret;
	if (WDT_HZ <= 0 || WDT_HZ > U32_MAX || !pdata->pet_time ||
	    pdata->pet_time >= pdata->bark_time)
		return -EINVAL;
	bark = div_u64((u64)pdata->bark_time * WDT_HZ, 1000);
	margin = (u64)WDT_HZ * 3;
	if (!bark || bark > U32_MAX || margin > U32_MAX - bark)
		return -ERANGE;
	pdata->bark_ticks = bark;
	pdata->bite_ticks = bark + margin;
	pdata->do_ipi_ping = of_property_read_bool(node, "qcom,ipi-ping");
	pdata->wakeup_irq_enable = of_property_read_bool(node, "qcom,wakeup-enable");
	pdata->irq_ppi = irq_is_percpu(pdata->bark_irq);

	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "wdt-base");
	if (!res || resource_size(res) < WDT0_BITE_TIME + sizeof(u32))
		return -EINVAL;
	pdata->size = resource_size(res);
	pdata->phys_base = res->start;
	pdata->base = devm_ioremap_resource(&pdev->dev, res);
	if (IS_ERR(pdata->base))
		return PTR_ERR(pdata->base);
	res = platform_get_resource_byname(pdev, IORESOURCE_MEM,
					   "wdt-absent-base");
	if (res) {
		if (resource_size(res) < sizeof(u32))
			return -EINVAL;
		pdata->wdog_absent_base = devm_ioremap_resource(&pdev->dev, res);
		if (IS_ERR(pdata->wdog_absent_base))
			return PTR_ERR(pdata->wdog_absent_base);
	}
	dump_pdata(pdata);
	return 0;
}

static int msm_watchdog_probe(struct platform_device *pdev)
{
	struct msm_watchdog_data *wdog_dd;
	int ret;

	if (!pdev->dev.of_node || !enable)
		return -ENODEV;
	mutex_lock(&wdog_probe_lock);
	if (rcu_access_pointer(wdog_data)) {
		ret = -EBUSY;
		goto unlock;
	}
	wdog_dd = kzalloc(sizeof(*wdog_dd), GFP_KERNEL);
	if (!wdog_dd) {
		ret = -ENOMEM;
		goto unlock;
	}
	wdog_dd->dev = &pdev->dev;
	ret = msm_wdog_dt_to_pdata(pdev, wdog_dd);
	if (ret)
		goto free;
	cpumask_clear(&wdog_dd->alive_mask);
	platform_set_drvdata(pdev, wdog_dd);
	ret = init_watchdog_data(wdog_dd);
	if (ret) {
		platform_set_drvdata(pdev, NULL);
		goto free;
	}
	/* Do not expose a partially initialized object to fatal trigger users. */
	rcu_assign_pointer(wdog_data, wdog_dd);
	log_buf_init(wdog_dd->dev);
	strlcpy(wdog_dd->minidump.name, "KWDOGDATA", sizeof(wdog_dd->minidump.name));
	wdog_dd->minidump.virt_addr = (uintptr_t)wdog_dd;
	wdog_dd->minidump.phys_addr = virt_to_phys(wdog_dd);
	wdog_dd->minidump.size = sizeof(*wdog_dd);
	wdog_dd->minidump.id = MINIDUMP_DEFAULT_ID;
	ret = msm_minidump_add_region(&wdog_dd->minidump);
	if (ret < 0)
		dev_warn(wdog_dd->dev, "Watchdog minidump registration failed: %d\n", ret);
	else
		wdog_dd->minidump_registered = true;
	mutex_unlock(&wdog_probe_lock);
	return 0;
free:
	kfree(wdog_dd);
unlock:
	mutex_unlock(&wdog_probe_lock);
	return ret;
}

static int msm_watchdog_remove(struct platform_device *pdev)
{
	struct msm_watchdog_data *wdog_dd = platform_get_drvdata(pdev);
	bool retain = false;

	mutex_lock(&wdog_probe_lock);
	remove_watchdog_sysfs(wdog_dd);
	/* Complete an in-flight fatal trigger before releasing its resources. */
	rcu_assign_pointer(wdog_data, NULL);
	synchronize_rcu();
	mutex_lock(&wdog_dd->disable_lock);
	WRITE_ONCE(wdog_dd->ready, false);
	if (!wdog_dd->stopping)
		wdog_disable(wdog_dd);
	mutex_unlock(&wdog_dd->disable_lock);
	kthread_stop(wdog_dd->watchdog_task);
	put_task_struct(wdog_dd->watchdog_task);
	del_timer_sync(&wdog_dd->pet_timer);
	cancel_work_sync(&wdog_dd->irq_counts_work);
	release_watchdog_notifiers(wdog_dd);
	release_watchdog_irq(wdog_dd);
	log_buf_remove();
	if (wdog_dd->minidump_registered &&
	    msm_minidump_remove_region(&wdog_dd->minidump) < 0) {
		/* Firmware may still reference the descriptor's backing memory. */
		dev_warn(wdog_dd->dev, "retaining watchdog minidump backing memory\n");
		get_device(wdog_dd->dev);
		retain = true;
	}
	platform_set_drvdata(pdev, NULL);
	if (!retain)
		kfree(wdog_dd);
	mutex_unlock(&wdog_probe_lock);
	return 0;
}

static const struct dev_pm_ops msm_watchdog_dev_pm_ops = {
	.suspend_noirq = msm_watchdog_suspend,
	.resume_noirq = msm_watchdog_resume,
};

static struct platform_driver msm_watchdog_driver = {
	.probe = msm_watchdog_probe,
	.remove = msm_watchdog_remove,
	.driver = {
		.name = MODULE_NAME,
		.suppress_bind_attrs = true,
		.owner = THIS_MODULE,
		.pm = &msm_watchdog_dev_pm_ops,
		.of_match_table = msm_wdog_match_table,
	},
};

static int init_watchdog(void)
{
	return platform_driver_register(&msm_watchdog_driver);
}

pure_initcall(init_watchdog);
MODULE_DESCRIPTION("MSM Watchdog Driver");
MODULE_LICENSE("GPL v2");
