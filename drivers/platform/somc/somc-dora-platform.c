// SPDX-License-Identifier: GPL-2.0-only
/*
 * Sony Xperia X Performance (dora: F8131 Single SIM / F8132 Dual SIM)
 * Platform, Remoteproc, Clearpad & FPC1145 TEE Fingerprint Driver for Linux 5.15
 *
 * Ported and audited against Sony Linux 4.4.302 (0c295b15b6d0):
 *  - SIM1 (TLMM GPIO 112) & hybrid UIM2 tray (TLMM GPIO 29) presence/IRQ
 *  - FPC1145 TrustZone /dev/fingerprint miscdevice implementing exact Sony 4.4
 *    0x1145 ioctl ABI (FPC_IOCWPREPARE, FPC_IOCWRESET, FPC_IOCWAWAKE,
 *    FPC_IOCRPREPARE, FPC_IOCRIRQ, FPC_IOCRIRQPOLL) + exclusive open & sysfs
 *  - Verified Synaptics RMI4 wakeup control via /sys/devices/virtual/input/clearpad/<attr>
 *    (returns real error if RMI4 controller is absent or not wakeup-capable)
 *  - Strict refcounted Qualcomm Q6V5 remoteproc control via /sys/kernel/boot_adsp/boot
 *    and /sys/kernel/boot_slpi/boot (rproc_get_by_phandle / rproc_boot / rproc_shutdown)
 *  - Thread-safe built-in /sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}
 *    via MODULE_PARAM_PREFIX + module_param_cb consumed by brcmfmac firmware loader
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
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/kobject.h>
#include <linux/miscdevice.h>
#include <linux/mmc/slot-gpio.h>
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/pci.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeup.h>
#include <linux/poll.h>
#include <linux/regulator/consumer.h>
#include <linux/remoteproc.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/sysfs.h>
#include <linux/uaccess.h>
#include <linux/wait.h>
#include <linux/workqueue.h>

#undef MODULE_PARAM_PREFIX
#define MODULE_PARAM_PREFIX "bcmdhd."

#define FPC_TTW_HOLD_MS			1500
#define FPC_MAX_HAL_PROCESSING_TIME	400
#define FPC_IRQPOLL_TIMEOUT_MS		500
#define FPC_RESET_LOW_US		1300
#define FPC_RESET_HIGH1_US		1250

/*
 * Exact Sony Linux 4.4 (0c295b15b6d0) drivers/input/misc/fpc1145_platform.c
 * ioctl ABI expected by Sony TrustZone fingerprint.msm8996.so HAL.
 * Note: _IOW/_IOR masks magic to 8 bits (0x1145 & 0xff == 0x45).
 */
#define FPC_SONY_IOC_MAGIC		0x45
#define FPC_IOCWPREPARE			_IOW(FPC_SONY_IOC_MAGIC, 0x01, int)
#define FPC_IOCWDEVWAKE			_IOW(FPC_SONY_IOC_MAGIC, 0x02, int)
#define FPC_IOCWRESET			_IOW(FPC_SONY_IOC_MAGIC, 0x03, int)
#define FPC_IOCWAWAKE			_IOW(FPC_SONY_IOC_MAGIC, 0x04, int)
#define FPC_IOCRPREPARE			_IOR(FPC_SONY_IOC_MAGIC, 0x81, int)
#define FPC_IOCRDEVWAKE			_IOR(FPC_SONY_IOC_MAGIC, 0x82, int)
#define FPC_IOCRIRQ			_IOR(FPC_SONY_IOC_MAGIC, 0x83, int)
#define FPC_IOCRIRQPOLL			_IOR(FPC_SONY_IOC_MAGIC, 0x84, int)

struct fpc1145_awake_args {
	int awake;
	unsigned int timeout;
};

/* Secondary generic FPC ioctl block ('f' = 0x66, non-overlapping magic) */
#define FPC_ALT_IOC_MAGIC		'f'
#define FPC_IOC_HW_RESET		_IO(FPC_ALT_IOC_MAGIC, 0x01)
#define FPC_IOC_WAKELOCK		_IOW(FPC_ALT_IOC_MAGIC, 0x02, int)
#define FPC_IOC_ENABLE_IRQ		_IOW(FPC_ALT_IOC_MAGIC, 0x03, int)
#define FPC_IOC_ENABLE_POWER		_IOW(FPC_ALT_IOC_MAGIC, 0x04, int)
#define FPC_IOC_CLK_STATE		_IOW(FPC_ALT_IOC_MAGIC, 0x05, int)
#define FPC_IOC_GET_IRQ_STATE		_IOR(FPC_ALT_IOC_MAGIC, 0x06, int)

/*
 * Thread-safe BCMDHD userspace parameter compatibility bridge:
 * Exposed at /sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}
 * via kernel/params.c without touching THIS_MODULE, and actively queried by
 * brcmfmac firmware loader so Wi-Fi HAL STA/SoftAP firmware switching works.
 */
static DEFINE_SPINLOCK(bcmdhd_param_lock);
static char bcmdhd_fw_path[256] = "/vendor/etc/firmware/wlan/bcmdhd/fw_bcmdhd.bin";
static char bcmdhd_nvram_path[256] = "/vendor/etc/wifi/bcmdhd.cal";
static int bcmdhd_op_mode;
static atomic_t bcmdhd_force_rebind = ATOMIC_INIT(0);

static bool somc_dora_is_bcm4359_pci(struct pci_dev *pdev)
{
	if (pdev->vendor != 0x14e4)
		return false;

	switch (pdev->device) {
	case 0x4415:
	case 0x4416:
	case 0x4417:
	case 0x43ef:
	case 4359:
		return true;
	default:
		return false;
	}
}

static int somc_dora_wifi_pci_iter(struct device *dev, void *data)
{
	struct pci_dev *pdev = to_pci_dev(dev);
	bool force_rebind = *(bool *)data;
	int ret;

	if (!somc_dora_is_bcm4359_pci(pdev))
		return 0;

	if (!dev->driver) {
		ret = device_attach(dev);
		if (ret < 0)
			dev_dbg(dev, "deferred brcmfmac attach failed: %d\n", ret);
	} else if (force_rebind && !strcmp(dev->driver->name, "brcmfmac")) {
		ret = device_reprobe(dev);
		if (ret < 0)
			dev_warn(dev, "brcmfmac op_mode reprobe failed: %d\n", ret);
	}

	return 0;
}

static void somc_dora_wifi_reprobe_work_fn(struct work_struct *work)
{
	bool force_rebind = atomic_xchg(&bcmdhd_force_rebind, 0) != 0;

#if IS_ENABLED(CONFIG_PCI)
	bus_for_each_dev(&pci_bus_type, NULL, &force_rebind,
			 somc_dora_wifi_pci_iter);
#endif
}

static DECLARE_DELAYED_WORK(somc_dora_wifi_reprobe_work,
			    somc_dora_wifi_reprobe_work_fn);

static int bcmdhd_path_param_set(const char *val, const struct kernel_param *kp)
{
	char *dst = kp->arg;
	unsigned long flags;
	size_t len;
	bool changed;

	if (!val)
		return -EINVAL;

	len = strcspn(val, "\r\n");
	if (len >= 256)
		return -ENOSPC;

	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	changed = (strncmp(dst, val, len) != 0 || dst[len] != '\0');
	memcpy(dst, val, len);
	dst[len] = '\0';
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);

	if (dst == bcmdhd_fw_path) {
		if (changed)
			atomic_set(&bcmdhd_force_rebind, 1);
		mod_delayed_work(system_wq, &somc_dora_wifi_reprobe_work,
				 msecs_to_jiffies(50));
	}

	return 0;
}

static int bcmdhd_path_param_get(char *buffer, const struct kernel_param *kp)
{
	const char *src = kp->arg;
	unsigned long flags;
	int ret;

	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	ret = scnprintf(buffer, PAGE_SIZE, "%s\n", src);
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);
	return ret;
}

static const struct kernel_param_ops bcmdhd_path_param_ops = {
	.set = bcmdhd_path_param_set,
	.get = bcmdhd_path_param_get,
};

static int bcmdhd_op_mode_param_set(const char *val,
				    const struct kernel_param *kp)
{
	int new_mode, old_mode;
	unsigned long flags;
	int ret;

	ret = kstrtoint(val, 0, &new_mode);
	if (ret)
		return ret;

	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	old_mode = bcmdhd_op_mode;
	bcmdhd_op_mode = new_mode;
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);

	if (old_mode != new_mode)
		atomic_set(&bcmdhd_force_rebind, 1);
	mod_delayed_work(system_wq, &somc_dora_wifi_reprobe_work,
			 msecs_to_jiffies(50));
	return 0;
}

static int bcmdhd_op_mode_param_get(char *buffer,
				    const struct kernel_param *kp)
{
	unsigned long flags;
	int mode;

	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	mode = bcmdhd_op_mode;
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);
	return scnprintf(buffer, PAGE_SIZE, "%d\n", mode);
}

static const struct kernel_param_ops bcmdhd_op_mode_param_ops = {
	.set = bcmdhd_op_mode_param_set,
	.get = bcmdhd_op_mode_param_get,
};

module_param_cb(firmware_path, &bcmdhd_path_param_ops, bcmdhd_fw_path, 0660);
module_param_cb(nvram_path, &bcmdhd_path_param_ops, bcmdhd_nvram_path, 0660);
module_param_cb(op_mode, &bcmdhd_op_mode_param_ops, &bcmdhd_op_mode, 0660);

void somc_dora_get_bcmdhd_fw_path(char *buf, size_t size)
{
	unsigned long flags;

	if (!buf || !size)
		return;
	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	strscpy(buf, bcmdhd_fw_path, size);
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);
}
EXPORT_SYMBOL_GPL(somc_dora_get_bcmdhd_fw_path);

void somc_dora_get_bcmdhd_nvram_path(char *buf, size_t size)
{
	unsigned long flags;

	if (!buf || !size)
		return;
	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	strscpy(buf, bcmdhd_nvram_path, size);
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);
}
EXPORT_SYMBOL_GPL(somc_dora_get_bcmdhd_nvram_path);

int somc_dora_get_bcmdhd_op_mode(void)
{
	unsigned long flags;
	int mode;

	spin_lock_irqsave(&bcmdhd_param_lock, flags);
	mode = bcmdhd_op_mode;
	spin_unlock_irqrestore(&bcmdhd_param_lock, flags);
	return mode;
}
EXPORT_SYMBOL_GPL(somc_dora_get_bcmdhd_op_mode);

struct somc_dora_plat {
	struct device *dev;
	struct gpio_desc *sim1_gpiod;
	int sim1_irq;
	bool sim1_wake_enabled;
	struct device *clearpad_compat_dev;
	struct kobject *boot_adsp_kobj;
	struct kobject *boot_slpi_kobj;
	bool boot_adsp_attr_created;
	bool boot_slpi_attr_created;

	struct mutex lock;
	bool wakeup_gesture;
	u32 cover_win[4]; /* left, top, right, bottom */
	bool adsp_ref_held;
	bool slpi_ref_held;
};

static struct somc_dora_plat *g_dora_plat;

/* --- SIM1 + MMC-owned Hybrid UIM2/SD state (F8131 SS vs F8132 DSDS) --- */

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

static ssize_t tray_closed_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	int tray_closed = -1;

	if (mmc_uim2_slot_get_state(&tray_closed, NULL) || tray_closed < 0)
		return sysfs_emit(buf, "unknown\n");
	return sysfs_emit(buf, "%d\n", tray_closed);
}
static DEVICE_ATTR_RO(tray_closed);

static ssize_t uim2_present_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	int uim2_en = -1;

	if (mmc_uim2_slot_get_state(NULL, &uim2_en) || uim2_en < 0)
		return sysfs_emit(buf, "unknown\n");
	return sysfs_emit(buf, "%d\n", uim2_en);
}
static DEVICE_ATTR_RO(uim2_present);

static ssize_t sim_slot_summary_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = dev_get_drvdata(dev);
	int s1 = plat->sim1_gpiod ? gpiod_get_value_cansleep(plat->sim1_gpiod) : -1;
	int tray = -1, uim2 = -1;

	mmc_uim2_slot_get_state(&tray, &uim2);
	return sysfs_emit(buf,
			  "sim1=%d tray_closed=%d uim2_mux_en=%d (F8131=SS, F8132=DSDS via LTALabel)\n",
			  s1, tray, uim2);
}
static DEVICE_ATTR_RO(sim_slot_summary);

static struct attribute *dora_sim_attrs[] = {
	&dev_attr_sim1_present.attr,
	&dev_attr_tray_closed.attr,
	&dev_attr_uim2_present.attr,
	&dev_attr_sim_slot_summary.attr,
	NULL,
};
ATTRIBUTE_GROUPS(dora_sim);

static irqreturn_t dora_sim_det_isr(int irq, void *data)
{
	struct somc_dora_plat *plat = data;

	pm_wakeup_dev_event(plat->dev, 1000, true);
	sysfs_notify(&plat->dev->kobj, NULL, "sim1_present");
	return IRQ_HANDLED;
}

/* --- Clearpad userspace gesture & smart-cover sysfs (/sys/devices/virtual/input/clearpad) --- */

static int somc_dora_sync_touch_wakeup(struct somc_dora_plat *plat, bool enable)
{
#if IS_ENABLED(CONFIG_I2C)
	struct device_node *ts_np;
	struct i2c_client *ts_client;
	int ret;

	if (!plat || !plat->dev || !plat->dev->of_node)
		return -ENODEV;

	ts_np = of_parse_phandle(plat->dev->of_node, "somc,touch-i2c", 0);
	if (!ts_np)
		return -ENODEV;

	ts_client = of_find_i2c_device_by_node(ts_np);
	of_node_put(ts_np);
	if (!ts_client)
		return -EAGAIN;

	device_lock(&ts_client->dev);
	if (!ts_client->dev.driver) {
		ret = -EAGAIN;
	} else if (!device_can_wakeup(&ts_client->dev)) {
		ret = -EOPNOTSUPP;
	} else {
		ret = device_set_wakeup_enable(&ts_client->dev, enable);
	}
	device_unlock(&ts_client->dev);

	put_device(&ts_client->dev);
	return ret;
#else
	return -ENODEV;
#endif
}

static ssize_t wakeup_gesture_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *p = dev_get_drvdata(dev);
	bool enabled;

	mutex_lock(&p->lock);
	enabled = p->wakeup_gesture;
	mutex_unlock(&p->lock);

	return sysfs_emit(buf, "%u\n", enabled ? 1 : 0);
}

static ssize_t wakeup_gesture_store(struct device *dev,
				    struct device_attribute *attr,
				    const char *buf, size_t count)
{
	struct somc_dora_plat *p = dev_get_drvdata(dev);
	bool v;
	int ret;

	if (kstrtobool(buf, &v))
		return -EINVAL;

	ret = somc_dora_sync_touch_wakeup(p, v);
	if (ret)
		return ret;

	mutex_lock(&p->lock);
	p->wakeup_gesture = v;
	mutex_unlock(&p->lock);

	return count;
}
static DEVICE_ATTR_RW(wakeup_gesture);

static ssize_t post_probe_start_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = dev_get_drvdata(dev);
	bool bound = false;
#if IS_ENABLED(CONFIG_I2C)
	struct device_node *ts_np;
	struct i2c_client *ts_client;

	if (plat && plat->dev && plat->dev->of_node) {
		ts_np = of_parse_phandle(plat->dev->of_node, "somc,touch-i2c", 0);
		if (ts_np) {
			ts_client = of_find_i2c_device_by_node(ts_np);
			of_node_put(ts_np);
			if (ts_client) {
				bound = (ts_client->dev.driver != NULL);
				put_device(&ts_client->dev);
			}
		}
	}
#endif
	return sysfs_emit(buf, "%u\n", bound ? 1 : 0);
}

static ssize_t post_probe_start_store(struct device *dev,
				      struct device_attribute *attr,
				      const char *buf, size_t count)
{
	struct somc_dora_plat *plat = dev_get_drvdata(dev);
	bool v;
#if IS_ENABLED(CONFIG_I2C)
	struct device_node *ts_np;
	struct i2c_client *ts_client;
	int ret;
#endif

	if (kstrtobool(buf, &v))
		return -EINVAL;
	if (!v)
		return count;

#if IS_ENABLED(CONFIG_I2C)
	if (!plat || !plat->dev || !plat->dev->of_node)
		return -ENODEV;

	ts_np = of_parse_phandle(plat->dev->of_node, "somc,touch-i2c", 0);
	if (!ts_np)
		return -ENODEV;

	ts_client = of_find_i2c_device_by_node(ts_np);
	of_node_put(ts_np);
	if (!ts_client)
		return -ENODEV;

	if (!ts_client->dev.driver) {
		ret = device_attach(&ts_client->dev);
		if (ret < 0) {
			put_device(&ts_client->dev);
			return ret;
		}
		if (ret == 0 || !ts_client->dev.driver) {
			put_device(&ts_client->dev);
			return -ENODEV;
		}
	}

	put_device(&ts_client->dev);
	return count;
#else
	return -ENODEV;
#endif
}
static DEVICE_ATTR_RW(post_probe_start);

static ssize_t clearpad_unimplemented_show(struct device *dev,
					   struct device_attribute *attr,
					   char *buf)
{
	return sysfs_emit(buf, "0\n");
}

static ssize_t clearpad_unimplemented_store(struct device *dev,
					    struct device_attribute *attr,
					    const char *buf, size_t count)
{
	bool v;

	if (kstrtobool(buf, &v))
		return -EINVAL;
	/* RMI4 F12 register-level glove/cover mode is not supported in mainline rmi4-f12 */
	if (v)
		return -EOPNOTSUPP;
	return count;
}

static struct device_attribute dev_attr_cover_mode_enabled =
	__ATTR(cover_mode_enabled, 0664, clearpad_unimplemented_show,
	       clearpad_unimplemented_store);
static struct device_attribute dev_attr_glove =
	__ATTR(glove, 0664, clearpad_unimplemented_show,
	       clearpad_unimplemented_store);

#define CLEARPAD_WIN_ATTR(_name, _idx)						\
static ssize_t _name##_show(struct device *dev,					\
			    struct device_attribute *attr, char *buf)		\
{										\
	struct somc_dora_plat *p = dev_get_drvdata(dev);			\
	u32 val;								\
	mutex_lock(&p->lock);							\
	val = p->cover_win[_idx];						\
	mutex_unlock(&p->lock);							\
	return sysfs_emit(buf, "%u\n", val);					\
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
		return -ENODEV;

	if (of_property_read_u32(plat->dev->of_node, prop_name, &ph))
		return -ENOENT;

	rproc = rproc_get_by_phandle(ph);
	if (!rproc)
		return -EAGAIN;

	if (enable) {
		if (!*ref_held) {
			ret = rproc_boot(rproc);
			if (!ret)
				*ref_held = true;
		} else if (rproc->state == RPROC_CRASHED ||
			   rproc->state == RPROC_OFFLINE) {
			/* Re-balance reference and attempt clean recovery boot */
			rproc_shutdown(rproc);
			*ref_held = false;
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
	return -ENODEV;
#endif
}

static bool somc_dora_rproc_is_running(struct somc_dora_plat *plat,
				       const char *prop_name)
{
#if IS_ENABLED(CONFIG_REMOTEPROC)
	phandle ph;
	struct rproc *rproc;
	bool running;

	if (!plat || !plat->dev || !plat->dev->of_node)
		return false;

	if (of_property_read_u32(plat->dev->of_node, prop_name, &ph))
		return false;

	rproc = rproc_get_by_phandle(ph);
	if (!rproc)
		return false;

	running = (rproc->state == RPROC_RUNNING);
	rproc_put(rproc);
	return running;
#else
	return false;
#endif
}

static ssize_t boot_subsys_show(struct kobject *kobj,
				struct kobj_attribute *attr, char *buf)
{
	struct somc_dora_plat *plat = READ_ONCE(g_dora_plat);
	bool running;

	if (!plat)
		return sysfs_emit(buf, "0\n");

	if (kobj == plat->boot_adsp_kobj)
		running = somc_dora_rproc_is_running(plat, "somc,adsp-rproc");
	else
		running = somc_dora_rproc_is_running(plat, "somc,slpi-rproc");

	return sysfs_emit(buf, "%u\n", running ? 1 : 0);
}

static ssize_t boot_subsys_store(struct kobject *kobj,
				 struct kobj_attribute *attr,
				 const char *buf, size_t count)
{
	struct somc_dora_plat *plat = READ_ONCE(g_dora_plat);
	bool val;
	int ret;

	if (kstrtobool(buf, &val))
		return -EINVAL;

	if (!plat)
		return -ENODEV;

	mutex_lock(&plat->lock);
	if (kobj == plat->boot_adsp_kobj)
		ret = somc_dora_rproc_control(plat, "somc,adsp-rproc", val,
					      &plat->adsp_ref_held);
	else
		ret = somc_dora_rproc_control(plat, "somc,slpi-rproc", val,
					      &plat->slpi_ref_held);
	mutex_unlock(&plat->lock);

	if (ret) {
		dev_err(plat->dev, "remoteproc transition (%s=%d) failed: %d\n",
			kobj == plat->boot_adsp_kobj ? "adsp" : "slpi", val, ret);
		return ret;
	}

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
	plat->cover_win[2] = 1080;
	plat->cover_win[3] = 1920;
	platform_set_drvdata(pdev, plat);

	plat->sim1_gpiod = devm_gpiod_get_optional(dev, "sim1-det", GPIOD_IN);
	if (IS_ERR(plat->sim1_gpiod))
		return dev_err_probe(dev, PTR_ERR(plat->sim1_gpiod),
				     "failed to get sim1-det gpio\n");

	device_init_wakeup(dev, true);
	WRITE_ONCE(g_dora_plat, plat);

	if (plat->sim1_gpiod) {
		plat->sim1_irq = gpiod_to_irq(plat->sim1_gpiod);
		if (plat->sim1_irq > 0) {
			ret = devm_request_threaded_irq(dev, plat->sim1_irq,
				NULL, dora_sim_det_isr,
				IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING | IRQF_ONESHOT,
				"dora-sim1-det", plat);
			if (!ret && !enable_irq_wake(plat->sim1_irq))
				plat->sim1_wake_enabled = true;
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
		if (plat->boot_adsp_kobj) {
			ret = sysfs_create_file(plat->boot_adsp_kobj, &boot_adsp_attr.attr);
			if (ret) {
				kobject_put(plat->boot_adsp_kobj);
				plat->boot_adsp_kobj = NULL;
			} else {
				plat->boot_adsp_attr_created = true;
			}
		}

		plat->boot_slpi_kobj = kobject_create_and_add("boot_slpi", kernel_kobj);
		if (plat->boot_slpi_kobj) {
			ret = sysfs_create_file(plat->boot_slpi_kobj, &boot_slpi_attr.attr);
			if (ret) {
				kobject_put(plat->boot_slpi_kobj);
				plat->boot_slpi_kobj = NULL;
			} else {
				plat->boot_slpi_attr_created = true;
			}
		}
	}

	dev_info(dev, "Sony Dora platform ready (sim1=%d, uim2=mmc-slot-owned)\n",
		 plat->sim1_gpiod ? gpiod_get_value_cansleep(plat->sim1_gpiod) : -1);
	return 0;
}

static int somc_dora_plat_remove(struct platform_device *pdev)
{
	struct somc_dora_plat *plat = platform_get_drvdata(pdev);

	if (plat->boot_slpi_kobj) {
		if (plat->boot_slpi_attr_created)
			sysfs_remove_file(plat->boot_slpi_kobj, &boot_slpi_attr.attr);
		kobject_put(plat->boot_slpi_kobj);
	}
	if (plat->boot_adsp_kobj) {
		if (plat->boot_adsp_attr_created)
			sysfs_remove_file(plat->boot_adsp_kobj, &boot_adsp_attr.attr);
		kobject_put(plat->boot_adsp_kobj);
	}
	if (plat->clearpad_compat_dev)
		device_unregister(plat->clearpad_compat_dev);

	mutex_lock(&plat->lock);
	if (plat->slpi_ref_held)
		somc_dora_rproc_control(plat, "somc,slpi-rproc", false,
					&plat->slpi_ref_held);
	if (plat->adsp_ref_held)
		somc_dora_rproc_control(plat, "somc,adsp-rproc", false,
					&plat->adsp_ref_held);
	mutex_unlock(&plat->lock);

	if (plat->sim1_wake_enabled)
		disable_irq_wake(plat->sim1_irq);

	WRITE_ONCE(g_dora_plat, NULL);
	device_init_wakeup(&pdev->dev, false);
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
	atomic_t open_excl;
	struct mutex lock;
	bool wakeup_enabled;
	bool clocks_enabled;
	bool power_enabled;
	bool irq_enabled;
	bool wake_irq_active;
	bool misc_registered;
	bool soc_link_created;
};

static int fpc1145_power_set(struct fpc1145_tee_data *fpc, bool enable)
{
	int ret = 0;

	if (enable == fpc->power_enabled)
		return 0;

	if (enable) {
		ret = regulator_bulk_enable(ARRAY_SIZE(fpc->supplies), fpc->supplies);
		if (!ret) {
			fpc->power_enabled = true;
			usleep_range(100, 900);
		}
	} else {
		if (fpc->rst_gpiod)
			gpiod_set_value_cansleep(fpc->rst_gpiod, 1);
		ret = regulator_bulk_disable(ARRAY_SIZE(fpc->supplies), fpc->supplies);
		if (!ret)
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

static int fpc1145_device_prepare(struct fpc1145_tee_data *fpc, bool enable)
{
	int ret;

	if (enable) {
		ret = fpc1145_power_set(fpc, true);
		if (ret)
			return ret;
		fpc->clocks_enabled = true;
		ret = fpc1145_hw_reset(fpc);
		if (ret) {
			fpc1145_power_set(fpc, false);
			return ret;
		}
		fpc1145_irq_set(fpc, true);
	} else {
		fpc1145_irq_set(fpc, false);
		fpc->clocks_enabled = false;
		ret = fpc1145_power_set(fpc, false);
	}
	return ret;
}

static int fpc1145_dev_open(struct inode *inode, struct file *filp)
{
	struct fpc1145_tee_data *fpc = container_of(filp->private_data,
						    struct fpc1145_tee_data,
						    miscdev);

	if (atomic_cmpxchg(&fpc->open_excl, 0, 1) != 0)
		return -EBUSY;

	atomic_set(&fpc->irq_pending, 0);
	return 0;
}

static int fpc1145_dev_release(struct inode *inode, struct file *filp)
{
	struct fpc1145_tee_data *fpc = container_of(filp->private_data,
						    struct fpc1145_tee_data,
						    miscdev);

	mutex_lock(&fpc->lock);
	if (fpc->power_enabled)
		fpc1145_irq_set(fpc, true);
	mutex_unlock(&fpc->lock);

	pm_relax(fpc->dev);
	atomic_set(&fpc->open_excl, 0);
	return 0;
}

static __poll_t fpc1145_dev_poll(struct file *filp, poll_table *wait)
{
	struct fpc1145_tee_data *fpc = container_of(filp->private_data,
						    struct fpc1145_tee_data,
						    miscdev);

	mutex_lock(&fpc->lock);
	if (fpc->power_enabled)
		fpc1145_irq_set(fpc, true);
	mutex_unlock(&fpc->lock);

	poll_wait(filp, &fpc->irq_wq, wait);
	if (atomic_xchg(&fpc->irq_pending, 0) > 0 ||
	    gpiod_get_value_cansleep(fpc->irq_gpiod) > 0)
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
		mutex_lock(&fpc->lock);
		if (fpc->power_enabled)
			fpc1145_irq_set(fpc, true);
		mutex_unlock(&fpc->lock);

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
	struct fpc1145_awake_args awake_args;
	void __user *usr = (void __user *)arg;
	int state = 0;
	long wait_rc;
	int ret = 0;

	switch (cmd) {
	/* --- Primary Sony 4.4 0x1145 (0x45) ioctl ABI --- */
	case FPC_IOCWPREPARE:
		if (arg <= 1)
			state = (int)arg;
		else if (get_user(state, (int __user *)usr))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		ret = fpc1145_device_prepare(fpc, !!state);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOCWDEVWAKE:
	case FPC_IOCRDEVWAKE:
		ret = 0;
		break;

	case FPC_IOCWRESET:
	case FPC_IOC_HW_RESET:
		mutex_lock(&fpc->lock);
		ret = fpc1145_hw_reset(fpc);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOCWAWAKE:
		if (copy_from_user(&awake_args, usr, sizeof(awake_args)))
			return -EFAULT;
		if (awake_args.awake) {
			unsigned int timeout = min_t(unsigned int,
						     awake_args.timeout,
						     FPC_MAX_HAL_PROCESSING_TIME);
			pm_wakeup_dev_event(fpc->dev, timeout, true);
		} else {
			pm_relax(fpc->dev);
		}
		break;

	case FPC_IOCRPREPARE:
		mutex_lock(&fpc->lock);
		state = fpc->power_enabled ? 1 : 0;
		mutex_unlock(&fpc->lock);
		if (put_user(state, (int __user *)usr))
			return -EFAULT;
		break;

	case FPC_IOCRIRQ:
	case FPC_IOC_GET_IRQ_STATE:
		state = gpiod_get_value_cansleep(fpc->irq_gpiod);
		if (state < 0)
			return state;
		if (put_user(state, (int __user *)usr))
			return -EFAULT;
		break;

	case FPC_IOCRIRQPOLL:
		state = gpiod_get_value_cansleep(fpc->irq_gpiod);
		if (state < 0)
			return state;
		if (state > 0) {
			if (put_user(state, (int __user *)usr))
				return -EFAULT;
			return 0;
		}
		mutex_lock(&fpc->lock);
		if (fpc->power_enabled)
			fpc1145_irq_set(fpc, true);
		mutex_unlock(&fpc->lock);

		wait_rc = wait_event_interruptible_timeout(
			fpc->irq_wq,
			atomic_read(&fpc->irq_pending) > 0,
			msecs_to_jiffies(FPC_IRQPOLL_TIMEOUT_MS));
		if (wait_rc == -ERESTARTSYS)
			return -ERESTARTSYS;

		atomic_set(&fpc->irq_pending, 0);
		state = gpiod_get_value_cansleep(fpc->irq_gpiod);
		if (state < 0)
			return state;
		if (state > 0)
			pm_wakeup_dev_event(fpc->dev, FPC_MAX_HAL_PROCESSING_TIME, true);
		if (put_user(state, (int __user *)usr))
			return -EFAULT;
		break;

	/* --- Secondary generic FPC ('f') ioctl fallback --- */
	case FPC_IOC_WAKELOCK:
		if (get_user(state, (int __user *)usr))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		fpc->wakeup_enabled = !!state;
		mutex_unlock(&fpc->lock);
		if (state)
			pm_wakeup_dev_event(fpc->dev, FPC_TTW_HOLD_MS, true);
		else
			pm_relax(fpc->dev);
		break;

	case FPC_IOC_ENABLE_IRQ:
		if (get_user(state, (int __user *)usr))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		ret = fpc1145_irq_set(fpc, !!state);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOC_ENABLE_POWER:
		if (get_user(state, (int __user *)usr))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		ret = fpc1145_power_set(fpc, !!state);
		mutex_unlock(&fpc->lock);
		break;

	case FPC_IOC_CLK_STATE:
		if (get_user(state, (int __user *)usr))
			return -EFAULT;
		mutex_lock(&fpc->lock);
		fpc->clocks_enabled = !!state;
		mutex_unlock(&fpc->lock);
		break;

	default:
		return -ENOTTY;
	}

	return ret;
}

static const struct file_operations fpc1145_fops = {
	.owner = THIS_MODULE,
	.open = fpc1145_dev_open,
	.release = fpc1145_dev_release,
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
	mutex_lock(&fpc->lock);
	if (fpc->power_enabled)
		fpc1145_irq_set(fpc, true);
	mutex_unlock(&fpc->lock);
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

static ssize_t spi_prepare_store(struct device *dev,
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
	ret = fpc1145_device_prepare(fpc, en);
	mutex_unlock(&fpc->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(spi_prepare);

static struct attribute *fpc1145_attrs[] = {
	&dev_attr_hw_reset.attr,
	&dev_attr_irq.attr,
	&dev_attr_wakeup_enable.attr,
	&dev_attr_clk_enable.attr,
	&dev_attr_regulator_enable.attr,
	&dev_attr_spi_prepare.attr,
	NULL,
};
ATTRIBUTE_GROUPS(fpc1145);

static irqreturn_t fpc1145_irq_handler(int irq, void *handle)
{
	struct fpc1145_tee_data *fpc = handle;

	if (READ_ONCE(fpc->wakeup_enabled))
		pm_wakeup_dev_event(fpc->dev, FPC_MAX_HAL_PROCESSING_TIME, true);

	mutex_lock(&fpc->lock);
	if (fpc->irq_enabled) {
		disable_irq_nosync(fpc->irq);
		fpc->irq_enabled = false;
	}
	mutex_unlock(&fpc->lock);

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
	atomic_set(&fpc->open_excl, 0);
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
	if (fpc->irq <= 0)
		return dev_err_probe(dev, fpc->irq ? fpc->irq : -EINVAL,
				     "failed to map FPC1145 irq\n");

	device_init_wakeup(dev, true);

	ret = devm_request_threaded_irq(dev, fpc->irq, NULL, fpc1145_irq_handler,
					IRQF_TRIGGER_HIGH | IRQF_ONESHOT |
					IRQF_NO_AUTOEN,
					dev_name(dev), fpc);
	if (ret) {
		device_init_wakeup(dev, false);
		return dev_err_probe(dev, ret, "failed to request FPC1145 irq\n");
	}
	fpc->irq_enabled = false;

	if (of_property_read_bool(dev->of_node, "fpc,enable-on-boot")) {
		ret = fpc1145_device_prepare(fpc, true);
		if (ret) {
			device_init_wakeup(dev, false);
			return ret;
		}
	}

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

	/*
	 * LineageOS 21 init.qcom.rc & fingerprint.msm8996.so access
	 * /sys/devices/platform/soc/fpc1145_device/<attr>. Expose the exact
	 * "fpc1145_device" symlink under /sys/devices/platform/soc/ when the
	 * OF-instantiated platform device is named "soc:fpc1145_device".
	 */
	if (dev->parent && strcmp(dev_name(dev), "fpc1145_device") != 0) {
		if (!sysfs_create_link(&dev->parent->kobj, &dev->kobj,
				       "fpc1145_device"))
			fpc->soc_link_created = true;
	}

	dev_info(dev, "Sony Dora FPC1145 TEE fingerprint interface ready (irq=%d, misc=%d)\n",
		 fpc->irq, fpc->misc_registered);
	return 0;
}

static int fpc1145_tee_remove(struct platform_device *pdev)
{
	struct fpc1145_tee_data *fpc = platform_get_drvdata(pdev);

	if (fpc->soc_link_created && pdev->dev.parent)
		sysfs_remove_link(&pdev->dev.parent->kobj, "fpc1145_device");

	if (fpc->misc_registered)
		misc_deregister(&fpc->miscdev);

	mutex_lock(&fpc->lock);
	if (fpc->wake_irq_active) {
		disable_irq_wake(fpc->irq);
		fpc->wake_irq_active = false;
	}
	if (fpc->irq_enabled)
		fpc1145_irq_set(fpc, false);
	fpc1145_power_set(fpc, false);
	mutex_unlock(&fpc->lock);

	device_init_wakeup(&pdev->dev, false);
	return 0;
}

#ifdef CONFIG_PM_SLEEP
static int fpc1145_tee_suspend(struct device *dev)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);

	mutex_lock(&fpc->lock);
	if (device_may_wakeup(dev) && fpc->wakeup_enabled &&
	    fpc->power_enabled && !fpc->wake_irq_active) {
		if (!enable_irq_wake(fpc->irq))
			fpc->wake_irq_active = true;
	}
	mutex_unlock(&fpc->lock);
	return 0;
}

static int fpc1145_tee_resume(struct device *dev)
{
	struct fpc1145_tee_data *fpc = dev_get_drvdata(dev);

	mutex_lock(&fpc->lock);
	if (fpc->wake_irq_active) {
		disable_irq_wake(fpc->irq);
		fpc->wake_irq_active = false;
	}
	mutex_unlock(&fpc->lock);
	return 0;
}
#endif

static SIMPLE_DEV_PM_OPS(fpc1145_tee_pm_ops, fpc1145_tee_suspend, fpc1145_tee_resume);

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
		.pm = &fpc1145_tee_pm_ops,
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
	cancel_delayed_work_sync(&somc_dora_wifi_reprobe_work);
	platform_unregister_drivers(somc_dora_drivers,
				    ARRAY_SIZE(somc_dora_drivers));
}
module_exit(somc_dora_exit);

MODULE_DESCRIPTION("Sony Xperia X Performance (dora) Platform, Remoteproc & FPC1145 Driver");
MODULE_LICENSE("GPL v2");
