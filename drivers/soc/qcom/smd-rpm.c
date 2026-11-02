/*
 * Copyright (c) 2015, Sony Mobile Communications AB.
 * Copyright (c) 2012-2013, The Linux Foundation. All rights reserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 and
 * only version 2 as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/of_platform.h>
#include <linux/io.h>
#include <linux/interrupt.h>
#include <linux/slab.h>
#include <linux/kref.h>
#include <linux/rpmsg.h>
#include <linux/soc/qcom/smd-rpm.h>
#include <asm/unaligned.h>

#define RPM_REQUEST_TIMEOUT (5 * HZ)
#define RPM_SERVICE_TYPE_REQUEST 0x00716572
#define RPM_MSG_TYPE_ERR 0x00727265
#define RPM_MSG_TYPE_MSG_ID 0x2367736d

struct qcom_smd_rpm {
	struct rpmsg_endpoint *rpm_channel;
	struct device *dev;
	struct kref refs;
	struct completion ack;
	struct mutex lock;
	spinlock_t ack_lock;
	u32 next_msg_id;
	u32 waiting_msg_id;
	int ack_status;
	bool online;
};
struct qcom_rpm_header {
	__le32 service_type;
	__le32 length;
};
struct qcom_rpm_request {
	__le32 msg_id;
	__le32 flags;
	__le32 type;
	__le32 id;
	__le32 data_len;
};
static struct rpmsg_driver qcom_smd_rpm_driver;

static void qcom_smd_rpm_release(struct kref *ref)
{
	struct qcom_smd_rpm *rpm = container_of(ref, struct qcom_smd_rpm, refs);

	put_device(rpm->dev);
	kfree(rpm);
}

static void qcom_smd_rpm_put(void *data)
{
	struct qcom_smd_rpm *rpm = data;

	kref_put(&rpm->refs, qcom_smd_rpm_release);
}

/* Runs after the RPMSG core has destroyed/drained the endpoint. */
static void qcom_smd_rpm_put_driver(void *data)
{
	struct qcom_smd_rpm *rpm = data;

	dev_set_drvdata(rpm->dev, NULL);
	qcom_smd_rpm_put(rpm);
}

static int qcom_smd_rpm_match_node(struct device *dev, void *data)
{
	return dev->of_node == data;
}

struct qcom_smd_rpm *qcom_smd_rpm_get(struct device *consumer)
{
	struct qcom_smd_rpm *rpm;
	struct device_node *np;
	struct device_link *link;
	struct device *supplier;
	int ret;

	if (!consumer || !consumer->of_node)
		return ERR_PTR(-EINVAL);
	np = of_parse_phandle(consumer->of_node, "qcom,rpm", 0);
	if (!np)
		return ERR_PTR(-ENODEV);
	supplier = driver_find_device(&qcom_smd_rpm_driver.drv, NULL, np,
				     qcom_smd_rpm_match_node);
	of_node_put(np);
	if (!supplier)
		return ERR_PTR(-EPROBE_DEFER);
	device_lock(supplier);
	rpm = dev_get_drvdata(supplier);
	if (!device_is_bound(supplier) || !rpm || !READ_ONCE(rpm->online)) {
		ret = -EPROBE_DEFER;
		goto unlock;
	}
	kref_get(&rpm->refs);
	link = device_link_add(consumer, supplier, DL_FLAG_AUTOREMOVE_CONSUMER);
	if (!link) {
		qcom_smd_rpm_put(rpm);
		ret = -ENOMEM;
		goto unlock;
	}
	ret = devm_add_action_or_reset(consumer, qcom_smd_rpm_put, rpm);
	if (ret)
		goto unlock;
	device_unlock(supplier);
	put_device(supplier);
	return rpm;
unlock:
	device_unlock(supplier);
	put_device(supplier);
	return ERR_PTR(ret);
}
EXPORT_SYMBOL_GPL(qcom_smd_rpm_get);

int qcom_rpm_smd_write(struct qcom_smd_rpm *rpm, int state,
		       u32 type, u32 id, void *buf, size_t count)
{
	struct {
		struct qcom_rpm_header hdr;
		struct qcom_rpm_request req;
		u8 payload[];
	} *pkt;
	unsigned long flags, left;
	size_t size;
	u32 msg_id;
	int ret;

	if (!rpm)
		return -ENODEV;
	if ((state != QCOM_SMD_RPM_ACTIVE_STATE &&
	     state != QCOM_SMD_RPM_SLEEP_STATE) ||
	    count >= 256 - sizeof(*pkt) || (count && !buf))
		return -EINVAL;
	size = sizeof(*pkt) + count;
	pkt = kmalloc(size, GFP_KERNEL);
	if (!pkt)
		return -ENOMEM;
	mutex_lock(&rpm->lock);
	if (!READ_ONCE(rpm->online)) {
		ret = -ENODEV;
		goto out;
	}
	msg_id = ++rpm->next_msg_id;
	if (!msg_id)
		msg_id = ++rpm->next_msg_id;
	pkt->hdr.service_type = cpu_to_le32(RPM_SERVICE_TYPE_REQUEST);
	pkt->hdr.length = cpu_to_le32(sizeof(pkt->req) + count);
	pkt->req.msg_id = cpu_to_le32(msg_id);
	pkt->req.flags = cpu_to_le32(state);
	pkt->req.type = cpu_to_le32(type);
	pkt->req.id = cpu_to_le32(id);
	pkt->req.data_len = cpu_to_le32(count);
	if (count)
		if (count)
		memcpy(pkt->payload, buf, count);
	spin_lock_irqsave(&rpm->ack_lock, flags);
	if (!rpm->online) {
		spin_unlock_irqrestore(&rpm->ack_lock, flags);
		ret = -ENODEV;
		goto out;
	}
	reinit_completion(&rpm->ack);
	rpm->ack_status = 0;
	rpm->waiting_msg_id = msg_id;
	spin_unlock_irqrestore(&rpm->ack_lock, flags);
	ret = rpmsg_send(rpm->rpm_channel, pkt, size);
	if (!ret) {
		left = wait_for_completion_timeout(&rpm->ack, RPM_REQUEST_TIMEOUT);
		ret = left ? rpm->ack_status : -ETIMEDOUT;
	}
	spin_lock_irqsave(&rpm->ack_lock, flags);
	rpm->waiting_msg_id = 0;
	spin_unlock_irqrestore(&rpm->ack_lock, flags);
out:
	mutex_unlock(&rpm->lock);
	kfree(pkt);
	return ret;
}
EXPORT_SYMBOL(qcom_rpm_smd_write);

/* Parse only within the received packet, including each padded KVP length. */
static int qcom_rpm_parse_ack(const void *data, int count, u32 *msg_id,
			      int *ack_status)
{
	const u8 *buf = data;
	size_t remaining, length, padded;
	u32 type;
	bool have_id = false;
	char error[32];

	if (count < (int)sizeof(struct qcom_rpm_header))
		return -EINVAL;
	if (get_unaligned_le32(buf) != RPM_SERVICE_TYPE_REQUEST)
		return -EINVAL;
	remaining = get_unaligned_le32(buf + sizeof(u32));
	if (remaining > count - sizeof(struct qcom_rpm_header))
		return -EINVAL;
	buf += sizeof(struct qcom_rpm_header);
	*ack_status = 0;
	while (remaining) {
		if (remaining < 2 * sizeof(u32))
			return -EINVAL;
		type = get_unaligned_le32(buf);
		length = get_unaligned_le32(buf + sizeof(u32));
		if (length > remaining - 2 * sizeof(u32))
			return -EINVAL;
		padded = ALIGN(length, sizeof(u32));
		if (padded > remaining - 2 * sizeof(u32))
			return -EINVAL;
		if (type == RPM_MSG_TYPE_MSG_ID) {
			if (length != sizeof(u32) || have_id)
				return -EINVAL;
			*msg_id = get_unaligned_le32(buf + 2 * sizeof(u32));
			have_id = true;
		} else if (type == RPM_MSG_TYPE_ERR) {
			if (!length)
				return -EINVAL;
			length = min_t(size_t, length, sizeof(error) - 1);
			memcpy(error, buf + 2 * sizeof(u32), length);
			error[length] = '\0';
			*ack_status = !strcmp(error, "resource does not exist") ?
				      -ENXIO : -EINVAL;
		}
		buf += 2 * sizeof(u32) + padded;
		remaining -= 2 * sizeof(u32) + padded;
	}
	return have_id ? 0 : -EINVAL;
}

static int qcom_smd_rpm_callback(struct rpmsg_device *rpdev, void *data,
				int count, void *priv, u32 addr)
{
	struct qcom_smd_rpm *rpm = dev_get_drvdata(&rpdev->dev);
	unsigned long flags;
	u32 msg_id;
	int ret, status;

	if (!rpm)
		return -ENODEV;
	ret = qcom_rpm_parse_ack(data, count, &msg_id, &status);
	if (ret)
		return ret;
	spin_lock_irqsave(&rpm->ack_lock, flags);
	if (rpm->waiting_msg_id && rpm->waiting_msg_id == msg_id) {
		rpm->ack_status = status;
		rpm->waiting_msg_id = 0;
		complete(&rpm->ack);
	}
	spin_unlock_irqrestore(&rpm->ack_lock, flags);
	return 0;
}

static void qcom_smd_rpm_offline(struct qcom_smd_rpm *rpm)
{
	unsigned long flags;

	spin_lock_irqsave(&rpm->ack_lock, flags);
	WRITE_ONCE(rpm->online, false);
	if (rpm->waiting_msg_id) {
		rpm->waiting_msg_id = 0;
		rpm->ack_status = -ENODEV;
		complete(&rpm->ack);
	}
	spin_unlock_irqrestore(&rpm->ack_lock, flags);
	/* Do not let the RPMSG core destroy an endpoint still used by a writer. */
	mutex_lock(&rpm->lock);
	rpm->rpm_channel = NULL;
	mutex_unlock(&rpm->lock);
}

static int qcom_smd_rpm_probe(struct rpmsg_device *rpdev)
{
	struct qcom_smd_rpm *rpm;
	int ret;

	rpm = kzalloc(sizeof(*rpm), GFP_KERNEL);
	if (!rpm)
		return -ENOMEM;
	kref_init(&rpm->refs);
	rpm->dev = get_device(&rpdev->dev);
	mutex_init(&rpm->lock);
	spin_lock_init(&rpm->ack_lock);
	init_completion(&rpm->ack);
	rpm->rpm_channel = rpdev->ept;
	rpm->online = true;
	ret = devm_add_action_or_reset(&rpdev->dev,
				      qcom_smd_rpm_put_driver, rpm);
	if (ret)
		return ret;
	/* Publish only after callback lifetime is protected by driver devres. */
	dev_set_drvdata(&rpdev->dev, rpm);
	ret = of_platform_populate(rpdev->dev.of_node, NULL, NULL, &rpdev->dev);
	if (ret) {
		of_platform_depopulate(&rpdev->dev);
		qcom_smd_rpm_offline(rpm);
	}
	return ret;
}

static void qcom_smd_rpm_remove(struct rpmsg_device *rpdev)
{
	struct qcom_smd_rpm *rpm = dev_get_drvdata(&rpdev->dev);

	of_platform_depopulate(&rpdev->dev);
	qcom_smd_rpm_offline(rpm);
}

static const struct of_device_id qcom_smd_rpm_of_match[] = {
	{ .compatible = "qcom,rpm-apq8084" },
	{ .compatible = "qcom,rpm-msm8916" },
	{ .compatible = "qcom,rpm-msm8974" },
	{ .compatible = "qcom,rpm-msm8996" },
	{ .compatible = "qcom,rpm-msm8998" },
	{}
};
MODULE_DEVICE_TABLE(of, qcom_smd_rpm_of_match);

static struct rpmsg_driver qcom_smd_rpm_driver = {
	.probe = qcom_smd_rpm_probe,
	.remove = qcom_smd_rpm_remove,
	.callback = qcom_smd_rpm_callback,
	.drv = {
		.name = "qcom_smd_rpm",
		.suppress_bind_attrs = true,
		.of_match_table = qcom_smd_rpm_of_match,
	},
};

static int __init qcom_smd_rpm_init(void)
{
	return register_rpmsg_driver(&qcom_smd_rpm_driver);
}
arch_initcall(qcom_smd_rpm_init);

static void __exit qcom_smd_rpm_exit(void)
{
	unregister_rpmsg_driver(&qcom_smd_rpm_driver);
}
module_exit(qcom_smd_rpm_exit);
MODULE_AUTHOR("Bjorn Andersson <bjorn.andersson@sonymobile.com>");
MODULE_DESCRIPTION("Qualcomm SMD backed RPM driver");
MODULE_LICENSE("GPL v2");
