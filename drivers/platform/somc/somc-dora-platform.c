// SPDX-License-Identifier: GPL-2.0-only
/*
 * Sony Xperia X Performance (dora: F8131 Single SIM / F8132 Dual SIM)
 * Platform, Remoteproc, Clearpad & FPC1145 TEE Fingerprint Driver for Linux 5.15
 *
 * Ported and modernized from Sony Linux 4.4.302 (0c295b15b6d0):
 *  - SIM1 (TLMM GPIO 112) & hybrid UIM2 tray (TLMM GPIO 29) presence/IRQ
 *  - FPC1145 / FPC1020 TrustZone fingerprint /dev/fingerprint miscdevice
 *    (poll + ioctls) & fpc1020 sysfs interface (power/reset/IRQ/wakeup)
 *  - Real Synaptics RMI4 wakeup control via /sys/devices/virtual/input/clearpad/<attr>
 *  - Real Qualcomm Q6V5 remoteproc control via /sys/kernel/boot_adsp/boot and
 *    /sys/kernel/boot_slpi/boot (rproc_get_by_phandle / rproc_boot / rproc_shutdown)
 *  - Native built-in /sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}
 *    via MODULE_PARAM_PREFIX (safe when built-in with CONFIG_SOMC_DORA_PLATFORM=y)
 */

#include <linux/atomic.h>
#include <linux/compat.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/init.h>
#include <linux/input.h>
#include <linux/interrupt.h>
#include <linux/ioctl.h>
#include <linux/kernel.h>
#include <linux/kobject.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeup.h>
#include <linux/poll.h>
#include <linux/regulator/consumer.h>
#include <linux/remoteproc.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#undef MODULE_PARAM_PREFIX
#define MODULE_PARAM_PREFIX "bcmdhd."

#define FPC_TTW_HOLD_MS			1500
#define FPC_RESET_LOW_US		1300
#define FPC_RESET_HIGH1_US		1250

/* FPC1145 / Sony TrustZone /dev/fingerprint ioctl interface */
#define FPC_IOC_MAGIC			'f'
#define FPC_IOC_HW_RESET		_IO(FPC_IOC_MAGIC, 0x01)
#define FPC_IOC_WAKELOCK		_IOW(FPC_IOC_MAGIC, 0x02, int)
#define FPC_IOC_ENABLE_IRQ		_IOW(FPC_IOC_MAGIC, 0x03, int)
#define FPC_IOC_ENABLE_POWER		_IOW(FPC_IOC_MAGIC, 0x04, int)
#define FPC_IOC_CLK_STATE		_IOW(FPC_IOC_MAGIC, 0x05, int)
#define FPC_IOC_GET_IRQ_STATE		_IOR(FPC_IOC_MAGIC, 0x06, int)

/*
 * BCMDHD userspace parameter compatibility bridge:
 * Exported cleanly through kernel/params.c at:
 *   /sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}
 * without dereferencing THIS_MODULE (which is NULL when built-in).
 */
static char bcmdhd_fw_path[256] = "/vendor/etc/firmware/wlan/bcmdhd/fw_bcmdhd.bin";
static char bcmdhd_nvram_path[256] = "/vendor/etc/wifi/bcmdhd.cal";
static int bcmdhd_op_mode;

module_param_string(firmware_path, bcmdhd_fw_path, sizeof(bcmdhd_fw_path), 0660);
module_param_string(nvram_path, bcmdhd_nvram_path, sizeof(bcmdhd_nvram_path), 0660);
module_param_named(op_mode, bcmdhd_op_mode, int, 0660);

struct somc_dora_plat {
	struct device *dev;
	struct gpio_desc *sim1_gpiod;
	struct gpio_desc *uim2_gpiod;
	int sim1_irq;
	int uim2_irq;

	struct device *clearpad_compat_dev;
	struct kobject *boot_adsp_kobj;
	struct kobject *boot_slpi_kobj;

	struct mutex lock;
	bool wakeup_gesture;
	bool post_probe_start;
	bool cover_mode_enabled;
	bool glove_mode;
	u32 cover_win[4]; /* left, top, right, bottom */
	bool boot_adsp_state;
	bool boot_slpi_state;
	bool adsp_ref_held;
	bool slpi_ref_held;
};

static struct somc_dora_plat *g_dora_plat;

/* --- SIM1 / Hybrid UIM2 (F8131 Single SIM vs F8132 Dual SIM) --- */

static ssize_t sim1_present_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = dev_get_drvdata(dev);
	int val = plat->sim1_gpiod ? gpiod_get_value_cansleep(plat->sim1_gpiod) : -ENODEV;

	if (val < 0)
		return sysfs_emit(buf, "unknown\n");
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(sim1_present);

static ssize_t uim2_present_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = dev_get_drvdata(dev);
	int val = plat->uim2_gpiod ? gpiod_get_value_cansleep(plat->uim2_gpiod) : -ENODEV;

	if (val < 0)
		return sysfs_emit(buf, "unknown\n");
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(uim2_present);

static ssize_t sim_slot_summary_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = dev_get_drvdata(dev);
	int s1 = plat->sim1_gpiod ? gpiod_get_value_cansleep(plat->sim1_gpiod) : -1;
	int s2 = plat->uim2_gpiod ? gpiod_get_value_cansleep(plat->uim2_gpiod) : -1;

	return sysfs_emit(buf, "sim1=%d uim2_hybrid=%d (F8131=SS, F8132=DSDS via LTALabel)\n",
			  s1, s2);
}
static DEVICE_ATTR_RO(sim_slot_summary);

static struct attribute *dora_sim_attrs[] = {
	&dev_attr_sim1_present.attr,
	&dev_attr_uim2_present.attr,
	&dev_attr_sim_slot_summary.attr,
	NULL,
};
ATTRIBUTE_GROUPS(dora_sim);

static irqreturn_t dora_sim_det_isr(int irq, void *data)
{
	struct somc_dora_plat *plat = data;

	pm_wakeup_dev_event(plat->dev, 1000, true);
	sysfs_notify(&plat->dev->kobj, NULL,
		     (irq == plat->sim1_irq) ? "sim1_present" : "uim2_present");
	return IRQ_HANDLED;
}

/* --- Clearpad userspace gesture & smart-cover sysfs (/sys/devices/virtual/input/clearpad) --- */

static void somc_dora_sync_touch_wakeup(struct somc_dora_plat *plat, bool enable)
{
#if IS_ENABLED(CONFIG_I2C)
	struct device_node *ts_np;
	struct i2c_client *ts_client;

	if (!plat || !plat->dev || !plat->dev->of_node)
		return;

	ts_np = of_parse_phandle(plat->dev->of_node, "somc,touch-i2c", 0);
	if (!ts_np)
		return;

	ts_client = of_find_i2c_device_by_node(ts_np);
	of_node_put(ts_np);
	if (!ts_client)
		return;

	if (device_can_wakeup(&ts_client->dev))
		device_set_wakeup_enable(&ts_client->dev, enable);

	put_device(&ts_client->dev);
#endif
}

static ssize_t wakeup_gesture_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *p = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%u\n", p->wakeup_gesture ? 1 : 0);
}

static ssize_t wakeup_gesture_store(struct device *dev,
				    struct device_attribute *attr,
				    const char *buf, size_t count)
{
	struct somc_dora_plat *p = dev_get_drvdata(dev);
	bool v;

	if (kstrtobool(buf, &v))
		return -EINVAL;

	mutex_lock(&p->lock);
	p->wakeup_gesture = v;
	mutex_unlock(&p->lock);

	somc_dora_sync_touch_wakeup(p, v);
	return count;
}
static DEVICE_ATTR_RW(wakeup_gesture);

#define CLEARPAD_BOOL_ATTR(_name, _field)					\
static ssize_t _name##_show(struct device *dev,					\
			    struct device_attribute *attr, char *buf)		\
{										\
	struct somc_dora_plat *p = dev_get_drvdata(dev);			\
	return sysfs_emit(buf, "%u\n", p->_field ? 1 : 0);			\
}										\
static ssize_t _name##_store(struct device *dev,				\
			     struct device_attribute *attr,			\
			     const char *buf, size_t count)			\
{										\
	struct somc_dora_plat *p = dev_get_drvdata(dev);			\
	bool v;									\
	if (kstrtobool(buf, &v))						\
		return -EINVAL;							\
	mutex_lock(&p->lock);							\
	p->_field = v;								\
	mutex_unlock(&p->lock);							\
	return count;								\
}										\
static DEVICE_ATTR_RW(_name)

CLEARPAD_BOOL_ATTR(post_probe_start, post_probe_start);
CLEARPAD_BOOL_ATTR(cover_mode_enabled, cover_mode_enabled);
CLEARPAD_BOOL_ATTR(glove, glove_mode);

#define CLEARPAD_WIN_ATTR(_name, _idx)						\
static ssize_t _name##_show(struct device *dev,					\
			    struct device_attribute *attr, char *buf)		\
{										\
	struct somc_dora_plat *p = dev_get_drvdata(dev);			\
	return sysfs_emit(buf, "%u\n", p->cover_win[_idx]);			\
}										\
static ssize_t _name##_store(struct device *dev,				\
			     struct device_attribute *attr,			\
			     const char *buf, size_t count)			\
{										\
	struct somc_dora_plat *p = dev_get_drvdata(dev);			\
	u32 v;									\
	if (kstrtou32(buf, 0, &v))						\
		return -EINVAL;							\
	mutex_lock(&p->lock);							\
	p->cover_win[_idx] = v;							\
	mutex_unlock(&p->lock);							\
	return count;								\
}										\
static DEVICE_ATTR_RW(_name)

CLEARPAD_WIN_ATTR(cover_win_left, 0);
CLEARPAD_WIN_ATTR(cover_win_top, 1);
CLEARPAD_WIN_ATTR(cover_win_right, 2);
CLEARPAD_WIN_ATTR(cover_win_bottom, 3);

static struct attribute *clearpad_compat_attrs[] = {
	&dev_attr_wakeup_gesture.attr,
	&dev_attr_post_probe_start.attr,
	&dev_attr_cover_mode_enabled.attr,
	&dev_attr_glove.attr,
	&dev_attr_cover_win_left.attr,
	&dev_attr_cover_win_top.attr,
	&dev_attr_cover_win_right.attr,
	&dev_attr_cover_win_bottom.attr,
	NULL,
};
ATTRIBUTE_GROUPS(clearpad_compat);

/* --- Sony init.qcom.sh /sys/kernel/boot_adsp/boot & boot_slpi/boot -> real rproc --- */

static int somc_dora_rproc_control(struct somc_dora_plat *plat,
				   const char *prop_name,
				   bool enable,
				   bool *ref_held)
{
#if IS_ENABLED(CONFIG_REMOTEPROC)
	phandle ph;
	struct rproc *rproc;
	int ret = 0;

	if (!plat || !plat->dev || !plat->dev->of_node)
		return 0;

	if (of_property_read_u32(plat->dev->of_node, prop_name, &ph))
		return 0;

	rproc = rproc_get_by_phandle(ph);
	if (!rproc)
		return 0;

	if (enable) {
		if (!*ref_held && rproc->state != RPROC_RUNNING) {
			ret = rproc_boot(rproc);
			if (!ret)
				*ref_held = true;
		}
	} else {
		if (*ref_held) {
			rproc_shutdown(rproc);
			*ref_held = false;
		}
	}

	rproc_put(rproc);
	return ret;
#else
	return 0;
#endif
}

static bool somc_dora_rproc_is_running(struct somc_dora_plat *plat,
				       const char *prop_name,
				       bool fallback_state)
{
#if IS_ENABLED(CONFIG_REMOTEPROC)
	phandle ph;
	struct rproc *rproc;
	bool running;

	if (!plat || !plat->dev || !plat->dev->of_node)
		return fallback_state;

	if (of_property_read_u32(plat->dev->of_node, prop_name, &ph))
		return fallback_state;

	rproc = rproc_get_by_phandle(ph);
	if (!rproc)
		return fallback_state;

	running = (rproc->state == RPROC_RUNNING);
	rproc_put(rproc);
	return running || fallback_state;
#else
	return fallback_state;
#endif
}

static ssize_t boot_subsys_show(struct kobject *kobj,
				struct kobj_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = g_dora_plat;
	bool running;

	if (!plat)
		return sysfs_emit(buf, "1\n");

	if (kobj == plat->boot_adsp_kobj)
		running = somc_dora_rproc_is_running(plat, "somc,adsp-rproc",
						     plat->boot_adsp_state);
	else
		running = somc_dora_rproc_is_running(plat, "somc,slpi-rproc",
						     plat->boot_slpi_state);

	return sysfs_emit(buf, "%u\n", running ? 1 : 0);
}

static ssize_t boot_subsys_store(struct kobject *kobj,
				 struct kobj_attribute *attr,
				 const char *buf, size_t count)
{
	struct somc_dora_plat *plat = g_dora_plat;
	bool val;
	int ret = 0;

	if (kstrtobool(buf, &val))
		return -EINVAL;

	if (!plat)
		return count;

	mutex_lock(&plat->lock);
	if (kobj == plat->boot_adsp_kobj) {
		ret = somc_dora_rproc_control(plat, "somc,adsp-rproc", val,
					      &plat->adsp_ref_held);
		if (!ret)
			plat->boot_adsp_state = val;
	} else {
		ret = somc_dora_rproc_control(plat, "somc,slpi-rproc", val,
					      &plat->slpi_ref_held);
		if (!ret)
			plat->boot_slpi_state = val;
	}
	mutex_unlock(&plat->lock);

	if (ret)
		dev_warn(plat->dev, "remoteproc transition (%s=%d) returned %d\n",
			 kobj == plat->boot_adsp_kobj ? "adsp" : "slpi", val, ret);

	return count;
}

static struct kobj_attribute boot_adsp_attr =
	__ATTR(boot, 0664, boot_subsys_show, boot_subsys_store);
static struct kobj_attribute boot_slpi_attr =
	__ATTR(boot, 0664, boot_subsys_show, boot_subsys_store);

static int somc_dora_plat_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct somc_dora_plat *plat;
	int ret;

	plat = devm_kzalloc(dev, sizeof(*plat), GFP_KERNEL);
	if (!plat)
		return -ENOMEM;

	plat->dev = dev;
	mutex_init(&plat->lock);
	plat->boot_adsp_state = true;
	plat->boot_slpi_state = true;
	plat->cover_win[2] = 1080;
	plat->cover_win[3] = 1920;
	platform_set_drvdata(pdev, plat);
	g_dora_plat = plat;

	plat->sim1_gpiod = devm_gpiod_get_optional(dev, "sim1-det", GPIOD_IN);
	if (IS_ERR(plat->sim1_gpiod))
		return dev_err_probe(dev, PTR_ERR(plat->sim1_gpiod),
				     "failed to get sim1-det gpio\n");

	plat->uim2_gpiod = devm_gpiod_get_optional(dev, "uim2-det", GPIOD_IN);
	if (IS_ERR(plat->uim2_gpiod))
		return dev_err_probe(dev, PTR_ERR(plat->uim2_gpiod),
				     "failed to get uim2-det gpio\n");

	device_init_wakeup(dev, true);

	if (plat->sim1_gpiod) {
		plat->sim1_irq = gpiod_to_irq(plat->sim1_gpiod);
		if (plat->sim1_irq > 0) {
			ret = devm_request_threaded_irq(dev, plat->sim1_irq,
				NULL, dora_sim_det_isr,
				IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
				"dora-sim1-det", plat);
			if (!ret)
				enable_irq_wake(plat->sim1_irq);
		}
	}

	if (plat->uim2_gpiod) {
		plat->uim2_irq = gpiod_to_irq(plat->uim2_gpiod);
		if (plat->uim2_irq > 0) {
			ret = devm_request_threaded_irq(dev, plat->uim2_irq,
				NULL, dora_sim_det_isr,
				IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
				"dora-uim2-det", plat);
			if (!ret)
				enable_irq_wake(plat->uim2_irq);
		}
	}

	/*
	 * Register under &input_class so sysfs exposes the exact path
	 * /sys/devices/virtual/input/clearpad/<attr> expected by Sony init.qcom.rc
	 * and PowerHAL (TARGET_TAP_TO_WAKE_NODE).
	 */
	plat->clearpad_compat_dev = device_create_with_groups(
		&input_class, NULL, MKDEV(0, 0), plat,
		clearpad_compat_groups, "clearpad");
	if (IS_ERR(plat->clearpad_compat_dev)) {
		dev_warn(dev, "clearpad sysfs registration returned %ld\n",
			 PTR_ERR(plat->clearpad_compat_dev));
		plat->clearpad_compat_dev = NULL;
	}

	/* Create /sys/kernel/boot_adsp/boot and /sys/kernel/boot_slpi/boot */
	if (kernel_kobj) {
		plat->boot_adsp_kobj = kobject_create_and_add("boot_adsp", kernel_kobj);
		if (plat->boot_adsp_kobj)
			ret = sysfs_create_file(plat->boot_adsp_kobj, &boot_adsp_attr.attr);

		plat->boot_slpi_kobj = kobject_create_and_add("boot_slpi", kernel_kobj);
		if (plat->boot_slpi_kobj)
			ret = sysfs_create_file(plat->boot_slpi_kobj, &boot_slpi_attr.attr);
	}

	dev_info(dev, "Sony Dora platform ready (sim1=%d, uim2_hybrid=%d)\n",
		 plat->sim1_gpiod ? gpiod_get_value_cansleep(plat->sim1_gpiod) : -1,
		 plat->uim2_gpiod ? gpiod_get_value_cansleep(plat->uim2_gpiod) : -1);
	return 0;
}

static int somc_dora_plat_remove(struct platform_device *pdev)
{
	struct somc_dora_plat *plat = platform_get_drvdata(pdev);

	mutex_lock(&plat->lock);
	if (plat->slpi_ref_held)
		somc_dora_rproc_control(plat, "somc,slpi-rproc", false,
					&plat->slpi_ref_held);
	if (plat->adsp_ref_held)
		somc_dora_rproc_control(plat, "somc,adsp-rproc", false,
					&plat->adsp_ref_held);
	mutex_unlock(&plat->lock);

	if (plat->boot_slpi_kobj) {
		sysfs_remove_file(plat->boot_slpi_kobj, &boot_slpi_attr.attr);
		kobject_put(plat->boot_slpi_kobj);
	}
	if (plat->boot_adsp_kobj) {
		sysfs_remove_file(plat->boot_adsp_kobj, &boot_adsp_attr.attr);
		kobject_put(plat->boot_adsp_kobj);
	}
	if (plat->clearpad_compat_dev)
		device_unregister(plat->clearpad_compat_dev);

	g_dora_plat = NULL;
	return 0;
}

static const struct of_device_id somc_dora_plat_of_match[] = {
	{ .compatible = "somc,dora-platform" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, somc_dora_plat_of_match);

static struct platform_driver somc_dora_plat_driver = {
	.probe = somc_dora_plat_probe,
	.remove = somc_dora_plat_remove,
	.driver = {
		.name = "somc-dora-platform",
		.of_match_table = somc_dora_plat_of_match,
		.dev_groups = dora_sim_groups,
	},
};

/* --- FPC1145 / FPC1020 TrustZone Fingerprint Platform + /dev/fingerprint Driver --- */

struct fpc1145_tee_data {
	struct device *dev;
	struct miscdevice miscdev;
	struct gpio_desc *rst_gpiod;
	struct gpio_desc *irq_gpiod;
	struct regulator_bulk_data supplies[3];
	int irq;
	wait_queue_head_t irq_wq;
	atomic_t irq_pending;
	struct mutex lock;
	bool wakeup_enabled;
	bool clocks_enabled;
	bool power_enabled;
	bool irq_enabled;
	bool misc_registered;
};

static int fpc1145_power_set(struct fpc1145_tee_data *fpc, bool enable)
{
	int ret = 0;

	if (enable == fpc->power_enabled)
		return 0;

	if (enable) {
		ret = regulator_bulk_enable(ARRAY_SIZE(fpc->supplies), fpc->supplies);
		if (!ret)
			fpc->power_enabled = true;
	} else {
		ret = regulator_bulk_disable(ARRAY_SIZE(fpc->supplies), fpc->supplies);
		fpc->power_enabled = false;
	}
	return ret;
}

static int fpc1145_irq_set(struct fpc1145_tee_data *fpc, bool enable)
{
	if (fpc->irq <= 0 || enable == fpc->irq_enabled)
		return 0;

	if (enable) {
		enable_irq(fpc->irq);
		fpc->irq_enabled = true;
	} else {
		disable_irq(fpc->irq);
		fpc->irq_enabled = false;
	}
	return 0;
}

static int fpc1145_hw_reset(struct fpc1145_tee_data *fpc)
{
	if (!fpc->rst_gpiod)
		return -ENODEV;

	/* Assert active-low reset, hold, then release and settle */
	gpiod_set_value_cansleep(fpc->rst_gpiod, 1);
	usleep_range(FPC_RESET_LOW_US, FPC_RESET_LOW_US + 100);
	gpiod_set_value_cansleep(fpc->rst_gpiod, 0);
	usleep_range(FPC_RESET_HIGH1_US, FPC_RESET_HIGH1_US + 100);
	atomic_set(&fpc->irq_pending, 0);
	return 0;
}

static __poll_t fpc1145_dev_poll(struct file *filp, poll_table *wait)
{
	struct fpc1145_tee_data *fpc = container_of(filp->private_data,
						    struct fpc1145_tee_data,
						    miscdev);

	poll_wait(filp, &fpc->irq_wq, wait);
	if (atomic_xchg(&fpc->irq_pending, 0) > 0)
		return EPOLLIN | EPOLLRDNORM;

	return 0;
}

static ssize_t fpc1145_dev_read(struct file *filp, char __user *buf,
				size_t count, loff_t *ppos)
{
	struct fpc1145_tee_data *fpc = container_of(filp->private_data,
						    struct fpc1145_tee_data,
						    miscdev);
	char val;

	if (count < 1)
		return -EINVAL;

	if (filp->f_flags & O_NONBLOCK) {
		if (atomic_read(&fpc->irq_pending) == 0 &&
		    gpiod_get_value_cansleep(fpc->irq_gpiod) <= 0)
			return -EAGAIN;
	} else {
		if (wait_event_interruptible(fpc->irq_wq,
					     atomic_read(&fpc->irq_pending) > 0))
			return -ERESTARTSYS;
	}

	atomic_set(&fpc->irq_pending, 0);
	val = gpiod_get_value_cansleep(fpc->irq_gpiod) ? '1' : '0';
	if (copy_to_user(buf, &val, 1))
		return -EFAULT;

	return 1;
}

static long fpc1145_dev_ioctl(struct file *filp, unsigned int cmd,
			      unsigned long arg)
{
	struct fpc1145_tee_data *fpc = container_of(filp->private_data,
						    struct fpc1145_tee_data,
						    miscdev);
	int state = 0;
	int ret = 0;

	switch (cmd) {
	case FPC_IOC_HW_RESET:
		mutex_lock(&fpc->lock);
		ret = fpc1145_hw_reset(fpc);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOC_WAKELOCK:
		if (get_user(state, (int __user *)arg))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		fpc->wakeup_enabled = !!state;
		mutex_unlock(&fpc->lock);
		if (state)
			pm_wakeup_dev_event(fpc->dev, FPC_TTW_HOLD_MS, true);
		break;

	case FPC_IOC_ENABLE_IRQ:
		if (get_user(state, (int __user *)arg))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		ret = fpc1145_irq_set(fpc, !!state);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOC_ENABLE_POWER:
		if (get_user(state, (int __user *)arg))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		ret = fpc1145_power_set(fpc, !!state);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOC_CLK_STATE:
		if (get_user(state, (int __user *)arg))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		fpc->clocks_enabled = !!state;
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOC_GET_IRQ_STATE:
		state = gpiod_get_value_cansleep(fpc->irq_gpiod);
		if (state < 0)
			return state;
		if (put_user(state, (int __user *)arg))
			return -EFAULT;
		break;

	default:
		return -ENOTTY;
	}

	return ret;
}

static const struct file_operations fpc1145_fops = {
	.owner = THIS_MODULE,
	.poll = fpc1145_dev_poll,
	.read = fpc1145_dev_read,
	.unlocked_ioctl = fpc1145_dev_ioctl,
	.compat_ioctl = compat_ptr_ioctl,
	.llseek = no_llseek,
};

static ssize_t hw_reset_store(struct device *dev,
			      struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);
	int ret;

	if (!sysfs_streq(buf, "reset"))
		return -EINVAL;

	mutex_lock(&fpc->lock);
	ret = fpc1145_hw_reset(fpc);
	mutex_unlock(&fpc->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(hw_reset);

static ssize_t irq_show(struct device *dev,
			struct device_attribute *attr, char *buf)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%d\n", gpiod_get_value_cansleep(fpc->irq_gpiod));
}

static ssize_t irq_store(struct device *dev,
			 struct device_attribute *attr,
			 const char *buf, size_t count)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);

	atomic_set(&fpc->irq_pending, 0);
	return count;
}
static DEVICE_ATTR_RW(irq);

static ssize_t wakeup_enable_store(struct device *dev,
				   struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);

	mutex_lock(&fpc->lock);
	if (sysfs_streq(buf, "enable") || sysfs_streq(buf, "1"))
		fpc->wakeup_enabled = true;
	else if (sysfs_streq(buf, "disable") || sysfs_streq(buf, "0"))
		fpc->wakeup_enabled = false;
	else {
		mutex_unlock(&fpc->lock);
		return -EINVAL;
	}
	mutex_unlock(&fpc->lock);
	return count;
}
static DEVICE_ATTR_WO(wakeup_enable);

static ssize_t clk_enable_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);
	int v;

	/* SPI0 clocks on MSM8996 Dora are owned by QSEE TrustZone */
	if (kstrtoint(buf, 0, &v))
		return -EINVAL;
	mutex_lock(&fpc->lock);
	fpc->clocks_enabled = !!v;
	mutex_unlock(&fpc->lock);
	return count;
}
static DEVICE_ATTR_WO(clk_enable);

static ssize_t regulator_enable_store(struct device *dev,
				      struct device_attribute *attr,
				      const char *buf, size_t count)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);
	bool en;
	int ret;

	if (sysfs_streq(buf, "enable") || sysfs_streq(buf, "1"))
		en = true;
	else if (sysfs_streq(buf, "disable") || sysfs_streq(buf, "0"))
		en = false;
	else
		return -EINVAL;

	mutex_lock(&fpc->lock);
	ret = fpc1145_power_set(fpc, en);
	mutex_unlock(&fpc->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(regulator_enable);

static struct attribute *fpc1145_attrs[] = {
	&dev_attr_hw_reset.attr,
	&dev_attr_irq.attr,
	&dev_attr_wakeup_enable.attr,
	&dev_attr_clk_enable.attr,
	&dev_attr_regulator_enable.attr,
	NULL,
};
ATTRIBUTE_GROUPS(fpc1145);

static irqreturn_t fpc1145_irq_handler(int irq, void *handle)
{
	struct fpc1145_tee_data *fpc = handle;

	if (READ_ONCE(fpc->wakeup_enabled))
		pm_wakeup_dev_event(fpc->dev, FPC_TTW_HOLD_MS, true);

	atomic_inc(&fpc->irq_pending);
	wake_up_interruptible(&fpc->irq_wq);
	sysfs_notify(&fpc->dev->kobj, NULL, dev_attr_irq.attr.name);
	return IRQ_HANDLED;
}

static int fpc1145_tee_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct fpc1145_tee_data *fpc;
	int ret;

	fpc = devm_kzalloc(dev, sizeof(*fpc), GFP_KERNEL);
	if (!fpc)
		return -ENOMEM;

	fpc->dev = dev;
	mutex_init(&fpc->lock);
	init_waitqueue_head(&fpc->irq_wq);
	atomic_set(&fpc->irq_pending, 0);
	fpc->wakeup_enabled = true;
	platform_set_drvdata(pdev, fpc);

	fpc->supplies[0].supply = "vcc_spi";
	fpc->supplies[1].supply = "vdd_ana";
	fpc->supplies[2].supply = "vdd_io";
	ret = devm_regulator_bulk_get(dev, ARRAY_SIZE(fpc->supplies), fpc->supplies);
	if (ret)
		return dev_err_probe(dev, ret, "failed to get FPC1145 regulators\n");

	fpc->rst_gpiod = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(fpc->rst_gpiod))
		return dev_err_probe(dev, PTR_ERR(fpc->rst_gpiod),
				     "failed to get FPC1145 reset gpio\n");

	fpc->irq_gpiod = devm_gpiod_get(dev, "irq", GPIOD_IN);
	if (IS_ERR(fpc->irq_gpiod))
		return dev_err_probe(dev, PTR_ERR(fpc->irq_gpiod),
				     "failed to get FPC1145 irq gpio\n");

	fpc->irq = gpiod_to_irq(fpc->irq_gpiod);
	if (fpc->irq < 0)
		return dev_err_probe(dev, fpc->irq, "failed to map FPC1145 irq\n");

	device_init_wakeup(dev, true);

	ret = fpc1145_power_set(fpc, true);
	if (ret)
		return ret;

	fpc1145_hw_reset(fpc);

	ret = devm_request_threaded_irq(dev, fpc->irq, NULL, fpc1145_irq_handler,
					IRQF_TRIGGER_RISING | IRQF_ONESHOT,
					dev_name(dev), fpc);
	if (ret) {
		fpc1145_power_set(fpc, false);
		return dev_err_probe(dev, ret, "failed to request FPC1145 irq\n");
	}
	fpc->irq_enabled = true;
	enable_irq_wake(fpc->irq);

	fpc->miscdev.minor = MISC_DYNAMIC_MINOR;
	fpc->miscdev.name = "fingerprint";
	fpc->miscdev.fops = &fpc1145_fops;
	fpc->miscdev.parent = dev;
	ret = misc_register(&fpc->miscdev);
	if (ret) {
		dev_warn(dev, "failed to register /dev/fingerprint (%d), continuing with sysfs\n",
			 ret);
	} else {
		fpc->misc_registered = true;
	}

	dev_info(dev, "Sony Dora FPC1145 TEE fingerprint sensor ready (irq=%d, misc=%d)\n",
		 fpc->irq, fpc->misc_registered);
	return 0;
}

static int fpc1145_tee_remove(struct platform_device *pdev)
{
	struct fpc1145_tee_data *fpc = platform_get_drvdata(pdev);

	if (fpc->misc_registered)
		misc_deregister(&fpc->miscdev);

	disable_irq_wake(fpc->irq);
	fpc1145_power_set(fpc, false);
	return 0;
}

static const struct of_device_id fpc1145_tee_of_match[] = {
	{ .compatible = "fpc,fpc1145" },
	{ .compatible = "fpc,fpc1020" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, fpc1145_tee_of_match);

static struct platform_driver fpc1145_tee_driver = {
	.probe = fpc1145_tee_probe,
	.remove = fpc1145_tee_remove,
	.driver = {
		.name = "fpc1020",
		.of_match_table = fpc1145_tee_of_match,
		.dev_groups = fpc1145_groups,
	},
};

static struct platform_driver * const somc_dora_drivers[] = {
	&somc_dora_plat_driver,
	&fpc1145_tee_driver,
};

static int __init somc_dora_init(void)
{
	return platform_register_drivers(somc_dora_drivers,
					 ARRAY_SIZE(somc_dora_drivers));
}
module_init(somc_dora_init);

static void __exit somc_dora_exit(void)
{
	platform_unregister_drivers(somc_dora_drivers,
				    ARRAY_SIZE(somc_dora_drivers));
}
module_exit(somc_dora_exit);

MODULE_DESCRIPTION("Sony Xperia X Performance (dora) Platform, Remoteproc & FPC1145 Driver");
MODULE_LICENSE("GPL v2");
