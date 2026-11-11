// SPDX-License-Identifier: GPL-2.0-only
/*
 * Status-only regulator API for legacy Qualcomm consumers of native GDSCs.
 * Physical sequencing and register ownership remain in the genpd provider.
 */
#include <linux/err.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/slab.h>

struct qcom_genpd_regulator {
	struct device *dev;
	struct regulator_desc desc;
	bool enabled;
};

static int qcom_genpd_regulator_enable(struct regulator_dev *rdev)
{
	struct qcom_genpd_regulator *proxy = rdev_get_drvdata(rdev);
	int ret;

	if (proxy->enabled)
		return 0;
	ret = pm_runtime_get_sync(proxy->dev);
	if (ret < 0) {
		pm_runtime_put_noidle(proxy->dev);
		return ret;
	}
	proxy->enabled = true;
	return 0;
}

static int qcom_genpd_regulator_disable(struct regulator_dev *rdev)
{
	struct qcom_genpd_regulator *proxy = rdev_get_drvdata(rdev);
	int ret;

	if (!proxy->enabled)
		return -EINVAL;
	ret = pm_runtime_put_sync(proxy->dev);
	if (ret < 0) {
		/* Keep the logical enable vote if the domain could not suspend. */
		pm_runtime_get_noresume(proxy->dev);
		return ret;
	}
	proxy->enabled = false;
	return 0;
}

static int qcom_genpd_regulator_is_enabled(struct regulator_dev *rdev)
{
	struct qcom_genpd_regulator *proxy = rdev_get_drvdata(rdev);

	return proxy->enabled;
}

static const struct regulator_ops qcom_genpd_regulator_ops = {
	.enable = qcom_genpd_regulator_enable,
	.disable = qcom_genpd_regulator_disable,
	.is_enabled = qcom_genpd_regulator_is_enabled,
};

static void qcom_genpd_regulator_cleanup(void *data)
{
	struct qcom_genpd_regulator *proxy = data;

	if (proxy->enabled)
		pm_runtime_put_sync(proxy->dev);
	pm_runtime_disable(proxy->dev);
}

static int qcom_genpd_regulator_runtime(struct device *dev)
{
	/* genpd performs the physical transition around this device callback. */
	return 0;
}

static const struct dev_pm_ops qcom_genpd_regulator_pm_ops = {
	SET_RUNTIME_PM_OPS(qcom_genpd_regulator_runtime,
			   qcom_genpd_regulator_runtime, NULL)
};

static int qcom_genpd_regulator_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct qcom_genpd_regulator *proxy;
	struct regulator_config config = {};
	struct regulator_init_data *init_data;
	struct regulator_dev *rdev;
	int ret;

	ret = of_count_phandle_with_args(dev->of_node, "power-domains",
					"#power-domain-cells");
	if (ret != 1)
		return ret < 0 ? ret : -EINVAL;
	/* A missing domain must never become an always-successful regulator. */
	if (!dev->pm_domain)
		return -EPROBE_DEFER;
	proxy = devm_kzalloc(dev, sizeof(*proxy), GFP_KERNEL);
	if (!proxy)
		return -ENOMEM;
	proxy->dev = dev;
	proxy->desc.type = REGULATOR_VOLTAGE;
	proxy->desc.owner = THIS_MODULE;
	proxy->desc.ops = &qcom_genpd_regulator_ops;
	init_data = of_get_regulator_init_data(dev, dev->of_node, &proxy->desc);
	if (!init_data || !init_data->constraints.name)
		return -EINVAL;
	/* This API switches a domain; it does not synthesize voltage/mode control. */
	if (init_data->constraints.min_uV || init_data->constraints.max_uV ||
	    init_data->constraints.valid_modes_mask)
		return -EINVAL;
	proxy->desc.name = init_data->constraints.name;
	init_data->constraints.valid_ops_mask = REGULATOR_CHANGE_STATUS;
	platform_set_drvdata(pdev, proxy);
	pm_runtime_enable(dev);
	ret = devm_add_action_or_reset(dev, qcom_genpd_regulator_cleanup, proxy);
	if (ret)
		return ret;
	config.dev = dev;
	config.of_node = dev->of_node;
	config.init_data = init_data;
	config.driver_data = proxy;
	rdev = devm_regulator_register(dev, &proxy->desc, &config);
	return PTR_ERR_OR_ZERO(rdev);
}

static const struct of_device_id qcom_genpd_regulator_match[] = {
	{ .compatible = "qcom,genpd-regulator" },
	{}
};
MODULE_DEVICE_TABLE(of, qcom_genpd_regulator_match);

static struct platform_driver qcom_genpd_regulator_driver = {
	.probe = qcom_genpd_regulator_probe,
	.driver = {
		.name = "qcom-genpd-regulator",
		.of_match_table = qcom_genpd_regulator_match,
		.pm = &qcom_genpd_regulator_pm_ops,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(qcom_genpd_regulator_driver);

MODULE_DESCRIPTION("Regulator API for native Qualcomm generic power domains");
MODULE_LICENSE("GPL v2");
