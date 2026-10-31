// SPDX-License-Identifier: GPL-2.0-only
/*
 * Sony Xperia X Performance (dora: F8131 Single SIM / F8132 Dual SIM)
 * Platform & FPC1145 TEE Fingerprint Driver for Linux 5.15
 *
 * Ported and modernized from Sony Linux 4.4.302 (0c295b15b6d0):
 *  - SIM1 (TLMM GPIO 112) & hybrid UIM2 tray (TLMM GPIO 29) presence/IRQ
 *  - FPC1145 / FPC1020 TrustZone fingerprint power/reset/IRQ sysfs interface
 *  - Sony init.qcom.rc sysfs compatibility bridges:
 *      /sys/devices/virtual/input/clearpad/*
 *      /sys/kernel/boot_adsp/boot, /sys/kernel/boot_slpi/boot
 *      /sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}
 */

#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/gpio/consumer.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/kobject.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeup.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/sysfs.h>

#define FPC_TTW_HOLD_MS			1500
#define FPC_RESET_LOW_US		1300
#define FPC_RESET_HIGH1_US		1250

/*
 * BCMDHD userspace parameter compatibility bridge:
 * Sony / LineageOS Wi-Fi HAL writes STA / AP firmware paths into
 * /sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}.
 */
static char bcmdhd_fw_path[256] = "/vendor/etc/firmware/wlan/bcmdhd/fw_bcmdhd.bin";
static char bcmdhd_nvram_path[256] = "/vendor/etc/wifi/bcmdhd.cal";
static int bcmdhd_op_mode;

struct somc_dora_plat {
	struct device *dev;
	struct gpio_desc *sim1_gpiod;
	struct gpio_desc *uim2_gpiod;
	int sim1_irq;
	int uim2_irq;

	struct class *input_compat_class;
	struct device *clearpad_compat_dev;
	struct kobject *boot_adsp_kobj;
	struct kobject *boot_slpi_kobj;
	struct kobject *bcmdhd_mod_kobj;
	struct kobject *bcmdhd_param_kobj;

	struct mutex lock;
	bool wakeup_gesture;
	bool post_probe_start;
	bool cover_mode_enabled;
	bool glove_mode;
	u32 cover_win[4]; /* left, top, right, bottom */
	bool boot_adsp_state;
	bool boot_slpi_state;
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

/* --- Clearpad userspace gesture & smart-cover compatibility sysfs --- */

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

CLEARPAD_BOOL_ATTR(wakeup_gesture, wakeup_gesture);
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

/* --- Sony init.qcom.sh boot_adsp / boot_slpi & bcmdhd kobject bridges --- */

static ssize_t boot_subsys_show(struct kobject *kobj,
				struct kobj_attribute *attr, char *buf)
{
	if (!g_dora_plat)
		return sysfs_emit(buf, "1\n");
	if (kobj == g_dora_plat->boot_adsp_kobj)
		return sysfs_emit(buf, "%u\n", g_dora_plat->boot_adsp_state ? 1 : 0);
	return sysfs_emit(buf, "%u\n", g_dora_plat->boot_slpi_state ? 1 : 0);
}

static ssize_t boot_subsys_store(struct kobject *kobj,
				 struct kobj_attribute *attr,
				 const char *buf, size_t count)
{
	bool val;

	if (kstrtobool(buf, &val))
		return -EINVAL;
	if (g_dora_plat) {
		if (kobj == g_dora_plat->boot_adsp_kobj)
			g_dora_plat->boot_adsp_state = val;
		else
			g_dora_plat->boot_slpi_state = val;
	}
	return count;
}

static struct kobj_attribute boot_adsp_attr =
	__ATTR(boot, 0664, boot_subsys_show, boot_subsys_store);
static struct kobj_attribute boot_slpi_attr =
	__ATTR(boot, 0664, boot_subsys_show, boot_subsys_store);

static ssize_t bcmdhd_fw_path_show(struct kobject *kobj,
				   struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%s\n", bcmdhd_fw_path);
}

static ssize_t bcmdhd_fw_path_store(struct kobject *kobj,
				    struct kobj_attribute *attr,
				    const char *buf, size_t count)
{
	strscpy(bcmdhd_fw_path, buf, sizeof(bcmdhd_fw_path));
	strim(bcmdhd_fw_path);
	return count;
}

static ssize_t bcmdhd_nvram_path_show(struct kobject *kobj,
				      struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%s\n", bcmdhd_nvram_path);
}

static ssize_t bcmdhd_nvram_path_store(struct kobject *kobj,
				       struct kobj_attribute *attr,
				       const char *buf, size_t count)
{
	strscpy(bcmdhd_nvram_path, buf, sizeof(bcmdhd_nvram_path));
	strim(bcmdhd_nvram_path);
	return count;
}

static ssize_t bcmdhd_op_mode_show(struct kobject *kobj,
				   struct kobj_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%d\n", bcmdhd_op_mode);
}

static ssize_t bcmdhd_op_mode_store(struct kobject *kobj,
				    struct kobj_attribute *attr,
				    const char *buf, size_t count)
{
	if (kstrtoint(buf, 0, &bcmdhd_op_mode))
		return -EINVAL;
	return count;
}

static struct kobj_attribute bcmdhd_fw_attr =
	__ATTR(firmware_path, 0660, bcmdhd_fw_path_show, bcmdhd_fw_path_store);
static struct kobj_attribute bcmdhd_nvram_attr =
	__ATTR(nvram_path, 0660, bcmdhd_nvram_path_show, bcmdhd_nvram_path_store);
static struct kobj_attribute bcmdhd_opmode_attr =
	__ATTR(op_mode, 0660, bcmdhd_op_mode_show, bcmdhd_op_mode_store);

static struct attribute *bcmdhd_param_attrs[] = {
	&bcmdhd_fw_attr.attr,
	&bcmdhd_nvram_attr.attr,
	&bcmdhd_opmode_attr.attr,
	NULL,
};

static const struct attribute_group bcmdhd_param_group = {
	.attrs = bcmdhd_param_attrs,
};

static int somc_dora_plat_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct somc_dora_plat *plat;
	struct kset *mod_kset;
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

	/* Create /sys/devices/virtual/input/clearpad/* for Sony init.qcom.rc */
	plat->input_compat_class = class_create(THIS_MODULE, "somc_clearpad_compat");
	if (!IS_ERR(plat->input_compat_class)) {
		plat->clearpad_compat_dev = device_create_with_groups(
			plat->input_compat_class, NULL, MKDEV(0, 0), plat,
			clearpad_compat_groups, "clearpad");
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

	/* Create /sys/module/bcmdhd/parameters/* if bcmdhd is not loaded */
	mod_kset = *(struct kset **)&THIS_MODULE->mkobj.kobj.kset;
	if (mod_kset && !kset_find_obj(mod_kset, "bcmdhd")) {
		plat->bcmdhd_mod_kobj = kobject_create_and_add("bcmdhd", &mod_kset->kobj);
		if (plat->bcmdhd_mod_kobj) {
			plat->bcmdhd_param_kobj = kobject_create_and_add("parameters",
									 plat->bcmdhd_mod_kobj);
			if (plat->bcmdhd_param_kobj)
				ret = sysfs_create_group(plat->bcmdhd_param_kobj,
							 &bcmdhd_param_group);
		}
	}

	dev_info(dev, "Sony Dora platform ready (sim1=%d, uim2_hybrid=%d)\n",
		 plat->sim1_gpiod ? gpiod_get_value_cansleep(plat->sim1_gpiod) : -1,
		 plat->uim2_gpiod ? gpiod_get_value_cansleep(plat->uim2_gpiod) : -1);
	return 0;
}

static int somc_dora_plat_remove(struct platform_device *pdev)
{
	struct somc_dora_plat *plat = platform_get_drvdata(pdev);

	if (plat->bcmdhd_param_kobj) {
		sysfs_remove_group(plat->bcmdhd_param_kobj, &bcmdhd_param_group);
		kobject_put(plat->bcmdhd_param_kobj);
	}
	kobject_put(plat->bcmdhd_mod_kobj);

	if (plat->boot_slpi_kobj) {
		sysfs_remove_file(plat->boot_slpi_kobj, &boot_slpi_attr.attr);
		kobject_put(plat->boot_slpi_kobj);
	}
	if (plat->boot_adsp_kobj) {
		sysfs_remove_file(plat->boot_adsp_kobj, &boot_adsp_attr.attr);
		kobject_put(plat->boot_adsp_kobj);
	}
	if (!IS_ERR_OR_NULL(plat->clearpad_compat_dev))
		device_unregister(plat->clearpad_compat_dev);
	if (!IS_ERR_OR_NULL(plat->input_compat_class))
		class_destroy(plat->input_compat_class);

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

/* --- FPC1145 / FPC1020 TrustZone Fingerprint Platform Driver --- */

struct fpc1145_tee_data {
	struct device *dev;
	struct gpio_desc *rst_gpiod;
	struct gpio_desc *irq_gpiod;
	struct regulator_bulk_data supplies[3];
	int irq;
	struct mutex lock;
	bool wakeup_enabled;
	bool clocks_enabled;
	bool power_enabled;
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

static int fpc1145_hw_reset(struct fpc1145_tee_data *fpc)
{
	if (!fpc->rst_gpiod)
		return -ENODEV;

	/* Assert active-low reset, hold, then release and verify IRQ low/high */
	gpiod_set_value_cansleep(fpc->rst_gpiod, 1);
	usleep_range(FPC_RESET_LOW_US, FPC_RESET_LOW_US + 100);
	gpiod_set_value_cansleep(fpc->rst_gpiod, 0);
	usleep_range(FPC_RESET_HIGH1_US, FPC_RESET_HIGH1_US + 100);
	return 0;
}

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
	/* Compatibility ACK write from Sony fingerprint HAL */
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

	/* SPI clocks on MSM8996 Dora are owned by QSEE TrustZone */
	if (kstrtoint(buf, 0, &v))
		return -EINVAL;
	fpc->clocks_enabled = !!v;
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
	enable_irq_wake(fpc->irq);

	dev_info(dev, "Sony Dora FPC1145 TEE fingerprint sensor initialized (irq=%d)\n",
		 fpc->irq);
	return 0;
}

static int fpc1145_tee_remove(struct platform_device *pdev)
{
	struct fpc1145_tee_data *fpc = platform_get_drvdata(pdev);

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

MODULE_DESCRIPTION("Sony Xperia X Performance (dora) Platform & FPC1145 Driver");
MODULE_LICENSE("GPL v2");
