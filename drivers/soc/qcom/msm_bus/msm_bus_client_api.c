// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2014-2017, The Linux Foundation. All rights reserved.
 */

#define pr_fmt(fmt) "AXI: %s(): " fmt, __func__

#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/mutex.h>
#include <linux/radix-tree.h>
#include <linux/clk.h>
#include <linux/of_platform.h>
#include <linux/msm-bus.h>
#include "msm_bus_core.h"

struct msm_bus_arb_ops arb_ops;

/*
 * Native board descriptions name the real fabric supplier. Legacy callers
 * without the property retain their original singleton registration path.
 */
int msm_bus_scale_client_init(struct device *dev)
{
	struct device_node *np;
	struct platform_device *supplier;
	struct device_link *link;
	int ret = 0;

	if (!dev || !dev->of_node)
		return 0;
	if (!of_find_property(dev->of_node, "qcom,msm-bus-provider", NULL))
		return 0;
	np = of_parse_phandle(dev->of_node, "qcom,msm-bus-provider", 0);
	if (!np)
		return -EINVAL;
	if (!of_device_is_compatible(np, "qcom,msm-bus-device")) {
		of_node_put(np);
		return -EINVAL;
	}
	supplier = of_find_device_by_node(np);
	of_node_put(np);
	if (!supplier)
		return -EPROBE_DEFER;

	device_lock(&supplier->dev);
	if (!supplier->dev.driver ||
	    !smp_load_acquire(&arb_ops.register_client)) {
		ret = -EPROBE_DEFER;
		goto out;
	}
	link = device_link_add(dev, &supplier->dev, DL_FLAG_AUTOREMOVE_CONSUMER);
	if (!link)
		ret = -EINVAL;
out:
	device_unlock(&supplier->dev);
	put_device(&supplier->dev);
	return ret;
}
EXPORT_SYMBOL_GPL(msm_bus_scale_client_init);

/**
 * msm_bus_scale_register_client() - Register the clients with the msm bus
 * driver
 * @pdata: Platform data of the client, containing src, dest, ab, ib.
 * Return non-zero value in case of success, 0 in case of failure.
 *
 * Client data contains the vectors specifying arbitrated bandwidth (ab)
 * and instantaneous bandwidth (ib) requested between a particular
 * src and dest.
 */
uint32_t msm_bus_scale_register_client(struct msm_bus_scale_pdata *pdata)
{
	typeof(arb_ops.register_client) callback = smp_load_acquire(&arb_ops.register_client);

	if (callback)
		return callback(pdata);
	pr_err("%s: Bus driver not ready.\n",
			__func__);
	return 0;
}
EXPORT_SYMBOL(msm_bus_scale_register_client);

/**
 * msm_bus_scale_client_update_request() - Update the request for bandwidth
 * from a particular client
 *
 * cl: Handle to the client
 * index: Index into the vector, to which the bw and clock values need to be
 * updated
 */
int msm_bus_scale_client_update_request(uint32_t cl, unsigned int index)
{
	typeof(arb_ops.update_request) callback = smp_load_acquire(&arb_ops.update_request);

	if (callback)
		return callback(cl, index);
	pr_err("%s: Bus driver not ready.\n",
			__func__);
	return -EPROBE_DEFER;
}
EXPORT_SYMBOL(msm_bus_scale_client_update_request);

/**
 * msm_bus_scale_client_update_context() - Update the context for a client
 * cl: Handle to the client
 * active_only: Bool to indicate dual context or active-only context.
 * ctx_idx: Voting index to be used when switching contexts.
 */
int msm_bus_scale_client_update_context(uint32_t cl, bool active_only,
							unsigned int ctx_idx)
{
	typeof(arb_ops.update_context) callback = smp_load_acquire(&arb_ops.update_context);

	if (callback)
		return callback(cl, active_only, ctx_idx);

	return -EPROBE_DEFER;
}
EXPORT_SYMBOL(msm_bus_scale_client_update_context);

/**
 * msm_bus_scale_unregister_client() - Unregister the client from the bus driver
 * @cl: Handle to the client
 */
void msm_bus_scale_unregister_client(uint32_t cl)
{
	typeof(arb_ops.unregister_client) callback = smp_load_acquire(&arb_ops.unregister_client);

	if (callback)
		callback(cl);
	else {
		pr_err("%s: Bus driver not ready.\n",
				__func__);
	}
}
EXPORT_SYMBOL(msm_bus_scale_unregister_client);

/**
 * msm_bus_scale_register() - Register the clients with the msm bus
 * driver
 *
 * @mas: Master ID
 * @slv: Slave ID
 * @name: descriptive name for this client
 * @active_only: Whether or not this bandwidth vote should only be
 *               effective while the application processor is active.
 *
 * Client data contains the vectors specifying arbitrated bandwidth (ab)
 * and instantaneous bandwidth (ib) requested between a particular
 * src and dest.
 */
struct msm_bus_client_handle*
msm_bus_scale_register(uint32_t mas, uint32_t slv, char *name, bool active_only)
{
	typeof(arb_ops.register_cl) callback = smp_load_acquire(&arb_ops.register_cl);

	if (callback)
		return callback(mas, slv, name, active_only);
	pr_err("%s: Bus driver not ready.\n",
			__func__);
	return ERR_PTR(-EPROBE_DEFER);
}
EXPORT_SYMBOL(msm_bus_scale_register);

/**
 * msm_bus_scale_client_update_bw() - Update the request for bandwidth
 * from a particular client
 *
 * @cl: Handle to the client
 * @ab: Arbitrated bandwidth being requested
 * @ib: Instantaneous bandwidth being requested
 */
int msm_bus_scale_update_bw(struct msm_bus_client_handle *cl, u64 ab, u64 ib)
{
	typeof(arb_ops.update_bw) callback = smp_load_acquire(&arb_ops.update_bw);

	if (callback)
		return callback(cl, ab, ib);
	pr_err("%s: Bus driver not ready.\n", __func__);
	return -EPROBE_DEFER;
}
EXPORT_SYMBOL(msm_bus_scale_update_bw);

/**
 * msm_bus_scale_change_context() - Update the context for a particular client
 * cl: Handle to the client
 * act_ab: The average bandwidth(AB) in Bytes/s to be used in active context.
 * act_ib: The instantaneous bandwidth(IB) in Bytes/s to be used in active
 *         context.
 * dual_ib: The average bandwidth(AB) in Bytes/s to be used in dual context.
 * dual_ab: The instantaneous bandwidth(IB) in Bytes/s to be used in dual
 *         context.
 */
int
msm_bus_scale_update_bw_context(struct msm_bus_client_handle *cl, u64 act_ab,
				u64 act_ib, u64 dual_ib, u64 dual_ab)
{
	typeof(arb_ops.update_bw_context) callback = smp_load_acquire(&arb_ops.update_bw_context);

	if (callback)
		return callback(cl, act_ab, act_ib,
							dual_ab, dual_ib);

	return -EPROBE_DEFER;
}
EXPORT_SYMBOL(msm_bus_scale_update_bw_context);

/**
 * msm_bus_scale_unregister() - Update the request for bandwidth
 * from a particular client
 *
 * cl: Handle to the client
 */
void msm_bus_scale_unregister(struct msm_bus_client_handle *cl)
{
	typeof(arb_ops.unregister) callback = smp_load_acquire(&arb_ops.unregister);

	if (callback)
		callback(cl);
	else
		pr_err("%s: Bus driver not ready.\n",
				__func__);
}
EXPORT_SYMBOL(msm_bus_scale_unregister);

/**
 * msm_bus_scale_query_tcs_cmd() - Query for a list of TCS commands for
 * an aggregated votes of paths from a single usecase.
 *
 * tcs_usecase: pointer to client allocated memory blob
 * cl: Handle to the client
 * index: Index into the vector, to which the bw and clock values need to be
 * updated
 */
int msm_bus_scale_query_tcs_cmd(struct msm_bus_tcs_usecase *tcs_usecase,
					uint32_t cl, unsigned int index)
{
	typeof(arb_ops.query_usecase) callback = smp_load_acquire(&arb_ops.query_usecase);

	if (callback)
		return callback(tcs_usecase, cl, index);
	pr_err("%s: Bus driver not ready.\n",
			__func__);
	return -EPROBE_DEFER;
}
EXPORT_SYMBOL(msm_bus_scale_query_tcs_cmd);

/**
 * msm_bus_scale_query_tcs_cmd_all() - Query for a list of TCS commands for
 * an aggregated vote of paths for all usecases registered by client
 *
 * tcs_handle: pointer to client allocated memory blob
 * cl: Handle to the client
 *
 */
int msm_bus_scale_query_tcs_cmd_all(struct msm_bus_tcs_handle *tcs_handle,
					uint32_t cl)
{
	typeof(arb_ops.query_usecase_all) callback = smp_load_acquire(&arb_ops.query_usecase_all);

	if (callback)
		return callback(tcs_handle, cl);
	pr_err("%s: Bus driver not ready.\n",
			__func__);
	return -EPROBE_DEFER;
}
EXPORT_SYMBOL(msm_bus_scale_query_tcs_cmd_all);
