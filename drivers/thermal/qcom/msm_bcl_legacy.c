/* Copyright (c) 2014, 2017, 2020, The Linux Foundation. All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 and
 * only version 2 as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 */

#define pr_fmt(fmt) "%s:%s " fmt, KBUILD_MODNAME, __func__

#include <linux/module.h>
#include <linux/device.h>
#include <linux/kernel.h>
#include <linux/mutex.h>
#include <linux/msm_bcl.h>
#include <linux/slab.h>

/* Static API objects outlive the built-in provider and its sysfs callbacks. */
static struct bcl_param_data bcl[BCL_PARAM_MAX];
static DEFINE_MUTEX(bcl_api_lock);

static int bcl_thresh_notify(struct bcl_param_data *data, int val,
			    enum bcl_trip_type trip)
{
	struct bcl_threshold *threshold;

	if (!data || trip < 0 || trip >= BCL_TRIP_MAX ||
	    !READ_ONCE(data->registered))
		return -EINVAL;
	threshold = READ_ONCE(data->thresh[trip]);
	if (!threshold || !threshold->trip_notify)
		return -ENODATA;
	threshold->trip_notify(trip, val, threshold->trip_data);
	return 0;
}

struct bcl_param_data *msm_bcl_register_param(enum bcl_param param,
		struct bcl_driver_ops *ops, char *name)
{
	struct bcl_param_data *data;

	if (param < 0 || param >= BCL_PARAM_MAX || !name || !ops ||
	    !ops->read || !ops->set_high_trip || !ops->get_high_trip ||
	    !ops->set_low_trip || !ops->get_low_trip || !ops->enable ||
	    !ops->disable)
		return NULL;
	mutex_lock(&bcl_api_lock);
	data = &bcl[param];
	if (data->registered) {
		mutex_unlock(&bcl_api_lock);
		return NULL;
	}
	data->ops = ops;
	strscpy(data->name, name, sizeof(data->name));
	ops->notify = bcl_thresh_notify;
	WRITE_ONCE(data->registered, true);
	mutex_unlock(&bcl_api_lock);
	return data;
}
EXPORT_SYMBOL_GPL(msm_bcl_register_param);

/* Provider must first stop interrupts/work, then withdraw its API objects. */
int msm_bcl_unregister_param(struct bcl_param_data *data)
{
	int i, ret = -EINVAL;

	mutex_lock(&bcl_api_lock);
	for (i = 0; i < BCL_PARAM_MAX; i++) {
		if (data != &bcl[i])
			continue;
		WRITE_ONCE(data->registered, false);
		data->ops = NULL;
		memset(data->thresh, 0, sizeof(data->thresh));
		ret = 0;
		break;
	}
	mutex_unlock(&bcl_api_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_bcl_unregister_param);

int msm_bcl_read(enum bcl_param param, int *value)
{
	int ret;

	if (!value || param < 0 || param >= BCL_PARAM_MAX)
		return -EINVAL;
	mutex_lock(&bcl_api_lock);
	if (!bcl[param].registered) {
		ret = -ENODEV;
		goto out;
	}
	ret = bcl[param].ops->read(value);
	if (!ret)
		bcl[param].last_read_val = *value;
 out:
	mutex_unlock(&bcl_api_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_bcl_read);

int msm_bcl_set_threshold(enum bcl_param param, enum bcl_trip_type trip,
			 struct bcl_threshold *threshold)
{
	struct bcl_param_data *data;
	int ret;

	if (param < 0 || param >= BCL_PARAM_MAX || trip < 0 ||
	    trip >= BCL_TRIP_MAX || !threshold || threshold->trip_value < 0 ||
	    !threshold->trip_notify)
		return -EINVAL;
	mutex_lock(&bcl_api_lock);
	data = &bcl[param];
	if (!data->registered) {
		ret = -ENODEV;
		goto out;
	}
	if (trip == BCL_HIGH_TRIP)
		ret = data->ops->set_high_trip(threshold->trip_value);
	else
		ret = data->ops->set_low_trip(threshold->trip_value);
	if (!ret) {
		if (trip == BCL_HIGH_TRIP)
			data->high_trip = threshold->trip_value;
		else
			data->low_trip = threshold->trip_value;
		WRITE_ONCE(data->thresh[trip], threshold);
	}
 out:
	mutex_unlock(&bcl_api_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_bcl_set_threshold);

/* Backend is built-in and cannot be manually unbound. Do not hold the API
 * mutex across disable: it drains callbacks which may update thresholds.
 */
int msm_bcl_enable(void)
{
	struct bcl_driver_ops *ops = NULL;
	int i, ret = -ENODATA;

	mutex_lock(&bcl_api_lock);
	for (i = 0; i < BCL_PARAM_MAX; i++) {
		struct bcl_param_data *data = &bcl[i];

		if (!data->registered || !data->thresh[0] || !data->thresh[1]) {
			ret = -ENODATA;
			goto out;
		}
		ret = data->ops->set_high_trip(data->high_trip);
		if (ret)
			goto out;
		ret = data->ops->set_low_trip(data->low_trip);
		if (ret)
			goto out;
	}
	ops = bcl[0].ops;
 out:
	mutex_unlock(&bcl_api_lock);
	return ops ? ops->enable() : ret;
}
EXPORT_SYMBOL_GPL(msm_bcl_enable);

int msm_bcl_disable(void)
{
	struct bcl_driver_ops *ops = READ_ONCE(bcl[0].ops);

	return ops ? ops->disable() : -ENODEV;
}
EXPORT_SYMBOL_GPL(msm_bcl_disable);

static ssize_t value_show(struct kobject *kobj, struct kobj_attribute *attr,
			  char *buf)
{
	struct bcl_param_data *data = container_of(attr,
			struct bcl_param_data, val_attr);
	int value, ret = msm_bcl_read(data - bcl, &value);

	return ret ? ret : scnprintf(buf, PAGE_SIZE, "%d\n", value);
}

static ssize_t trip_show(struct bcl_param_data *data, bool high, char *buf)
{
	int value, ret;

	mutex_lock(&bcl_api_lock);
	if (!data->registered) {
		ret = -ENODEV;
		goto out;
	}
	ret = high ? data->ops->get_high_trip(&value) :
		     data->ops->get_low_trip(&value);
	if (!ret)
		ret = scnprintf(buf, PAGE_SIZE, "%d\n", value);
 out:
	mutex_unlock(&bcl_api_lock);
	return ret;
}

static ssize_t high_trip_show(struct kobject *kobj, struct kobj_attribute *attr,
			      char *buf)
{
	return trip_show(container_of(attr, struct bcl_param_data,
				      high_trip_attr), true, buf);
}

static ssize_t low_trip_show(struct kobject *kobj, struct kobj_attribute *attr,
			     char *buf)
{
	return trip_show(container_of(attr, struct bcl_param_data,
				      low_trip_attr), false, buf);
}

static struct class msm_bcl_class = { .name = "msm_bcl" };

static void bcl_device_release(struct device *dev)
{
	/* The enclosing bcl[] objects have static storage. */
}

static void bcl_remove_sysfs(int i)
{
	sysfs_remove_group(&bcl[i].device.kobj, &bcl[i].bcl_attr_gp);
	kfree(bcl[i].bcl_attr_gp.attrs);
	device_unregister(&bcl[i].device);
}

static int __init msm_bcl_init_driver(void)
{
	static const char * const names[] = { "voltage", "current" };
	int i, ret;

	ret = class_register(&msm_bcl_class);
	if (ret)
		return ret;
	for (i = 0; i < BCL_PARAM_MAX; i++) {
		struct bcl_param_data *data = &bcl[i];
		struct attribute **attrs;

		attrs = kcalloc(4, sizeof(*attrs), GFP_KERNEL);
		if (!attrs) {
			ret = -ENOMEM;
			goto unwind;
		}
		data->val_attr = (struct kobj_attribute)__ATTR(value, 0444,
							      value_show, NULL);
		data->high_trip_attr = (struct kobj_attribute)__ATTR(high_trip,
						   0444, high_trip_show, NULL);
		data->low_trip_attr = (struct kobj_attribute)__ATTR(low_trip,
						   0444, low_trip_show, NULL);
		attrs[0] = &data->val_attr.attr;
		attrs[1] = &data->high_trip_attr.attr;
		attrs[2] = &data->low_trip_attr.attr;
		data->bcl_attr_gp.attrs = attrs;
		data->device.class = &msm_bcl_class;
		data->device.release = bcl_device_release;
		ret = dev_set_name(&data->device, "%s", names[i]);
		if (ret) {
			kfree(attrs);
			goto unwind;
		}
		ret = device_register(&data->device);
		if (ret) {
			put_device(&data->device);
			kfree(attrs);
			goto unwind;
		}
		ret = sysfs_create_group(&data->device.kobj, &data->bcl_attr_gp);
		if (ret) {
			device_unregister(&data->device);
			kfree(attrs);
			goto unwind;
		}
	}
	return 0;
 unwind:
	while (--i >= 0)
		bcl_remove_sysfs(i);
	class_unregister(&msm_bcl_class);
	return ret;
}
fs_initcall(msm_bcl_init_driver);
