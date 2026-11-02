// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2014-2016, 2018-2019, Linux Foundation. All rights reserved.
 */

#include <linux/clk.h>
#include <linux/device.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/slab.h>
#include <soc/qcom/rpm-smd.h>
#include <linux/soc/qcom/smd-rpm.h>
#include <trace/events/trace_msm_bus.h>
#include "msm_bus_core.h"
#include "msm_bus_adhoc.h"
#include "msm_bus_noc.h"
#include "msm_bus_bimc.h"

static int msm_bus_dev_init_qos(struct device *dev, void *data);

ssize_t bw_show(struct device *dev, struct device_attribute *attr,
			  char *buf)
{
	struct msm_bus_node_info_type *node_info = NULL;
	struct msm_bus_node_device_type *bus_node = NULL;
	int i;
	int off = 0;

	bus_node = to_msm_bus_node(dev);
	if (!bus_node)
		return -EINVAL;

	node_info = bus_node->node_info;

	msm_bus_arb_lock_adhoc();
	for (i = 0; i < bus_node->num_lnodes && off < PAGE_SIZE - 1; i++) {
		if (!bus_node->lnode_list[i].in_use)
			continue;
		off += scnprintf((buf + off), PAGE_SIZE - off,
		"[%d]:%s:Act_IB %llu Act_AB %llu Slp_IB %llu Slp_AB %llu\n",
			i, bus_node->lnode_list[i].cl_name,
			bus_node->lnode_list[i].lnode_ib[ACTIVE_CTX],
			bus_node->lnode_list[i].lnode_ab[ACTIVE_CTX],
			bus_node->lnode_list[i].lnode_ib[DUAL_CTX],
			bus_node->lnode_list[i].lnode_ab[DUAL_CTX]);
	}
	off += scnprintf((buf + off), PAGE_SIZE - off,
	"Max_Act_IB %llu Sum_Act_AB %llu Act_Util_fact %d Act_Vrail_comp %d\n",
		bus_node->node_bw[ACTIVE_CTX].max_ib,
		bus_node->node_bw[ACTIVE_CTX].sum_ab,
		bus_node->node_bw[ACTIVE_CTX].util_used,
		bus_node->node_bw[ACTIVE_CTX].vrail_used);
	off += scnprintf((buf + off), PAGE_SIZE - off,
	"Max_Slp_IB %llu Sum_Slp_AB %llu Slp_Util_fact %d Slp_Vrail_comp %d\n",
		bus_node->node_bw[DUAL_CTX].max_ib,
		bus_node->node_bw[DUAL_CTX].sum_ab,
		bus_node->node_bw[DUAL_CTX].util_used,
		bus_node->node_bw[DUAL_CTX].vrail_used);
	msm_bus_arb_unlock_adhoc();
	return off;
}

ssize_t bw_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	return count;
}

DEVICE_ATTR_RW(bw);

struct static_rules_type {
	int num_rules;
	struct bus_rule_type *rules;
};

static struct static_rules_type static_rules;

struct msm_bus_graph {
	struct list_head nodes;
	struct qcom_smd_rpm *rpm;
};

/* The legacy arb API exposes one topology, not interchangeable live graphs. */
static DEFINE_MUTEX(msm_bus_graph_lock);
static struct msm_bus_graph *msm_bus_graph_owner;


static int bus_get_reg(struct nodeclk *nclk, struct device *dev)
{
	int ret = 0;
	struct msm_bus_node_device_type *node_dev;

	if (!(dev && nclk))
		return -ENXIO;

	node_dev = to_msm_bus_node(dev);
	if (!strlen(nclk->reg_name)) {
		dev_dbg(dev, "No regulator exist for node %d\n",
						node_dev->node_info->id);
		goto exit_of_get_reg;
	} else {
		if (!(IS_ERR_OR_NULL(nclk->reg)))
			goto exit_of_get_reg;

		nclk->reg = devm_regulator_get(dev, nclk->reg_name);
		if (IS_ERR_OR_NULL(nclk->reg)) {
			ret =
			(IS_ERR(nclk->reg) ? PTR_ERR(nclk->reg) : -ENXIO);
			dev_err(dev, "Error: Failed to get regulator %s:%d\n",
							nclk->reg_name, ret);
		} else {
			dev_dbg(dev, "Successfully got regulator for %d\n",
				node_dev->node_info->id);
		}
	}

exit_of_get_reg:
	return ret;
}

static int bus_enable_reg(struct nodeclk *nclk)
{
	int ret;

	if (!nclk || IS_ERR_OR_NULL(nclk->reg))
		return -ENXIO;
	if (nclk->reg_enabled)
		return 0;
	ret = regulator_enable(nclk->reg);
	if (!ret)
		nclk->reg_enabled = true;
	return ret;
}

static int bus_disable_reg(struct nodeclk *nclk)
{
	int ret;

	if (!nclk)
		return -EINVAL;
	if (!nclk->reg_enabled)
		return 0;
	ret = regulator_disable(nclk->reg);
	if (!ret)
		nclk->reg_enabled = false;
	return ret;
}

static int enable_nodeclk(struct nodeclk *nclk, struct device *dev)
{
	int ret;

	if (nclk->enable || nclk->setrate_only_clk)
		return 0;
	if (dev && nclk->reg_name[0]) {
		ret = bus_get_reg(nclk, dev);
		if (ret)
			return ret;
		ret = bus_enable_reg(nclk);
		if (ret)
			return ret;
	}
	ret = clk_prepare_enable(nclk->clk);
	if (ret) {
		/* Keep regulator bookkeeping if its disable itself fails. */
		bus_disable_reg(nclk);
		return ret;
	}
	nclk->enable = true;
	return 0;
}

static int disable_nodeclk(struct nodeclk *nclk)
{
	if (nclk->enable && !nclk->setrate_only_clk) {
		clk_disable_unprepare(nclk->clk);
		nclk->enable = false;
	}
	return bus_disable_reg(nclk);
}

static int setrate_nodeclk(struct nodeclk *nclk, long rate)
{
	int ret = 0;

	if (!nclk->enable_only_clk)
		ret = clk_set_rate(nclk->clk, rate);

	if (ret)
		MSM_BUS_ERR("%s: failed to setrate clk", __func__);
	return ret;
}

static int msm_bus_rpm_vote(struct msm_bus_node_device_type *node, int ctx,
			    u32 type, int id)
{
	if (id < 0)
		return -EINVAL;
	if (node->rpm) {
#if IS_REACHABLE(CONFIG_QCOM_SMD_RPM)
		struct {
			__le32 key;
			__le32 length;
			__le64 bandwidth;
		} request = {
			.key = cpu_to_le32(RPM_MASTER_FIELD_BW),
			.length = cpu_to_le32(sizeof(u64)),
			.bandwidth = cpu_to_le64(node->node_bw[ctx].sum_ab),
		};

		BUILD_BUG_ON(sizeof(request) != 16);
		return qcom_rpm_smd_write(node->rpm,
			ctx == DUAL_CTX ? QCOM_SMD_RPM_SLEEP_STATE :
					  QCOM_SMD_RPM_ACTIVE_STATE,
			type, id, &request, sizeof(request));
#else
		return -EOPNOTSUPP;
#endif
	}
#if IS_REACHABLE(CONFIG_MSM_RPM_SMD)
	{
		struct msm_rpm_kvp kvp = {
			.key = RPM_MASTER_FIELD_BW,
			.length = sizeof(u64),
			.data = (u8 *)&node->node_bw[ctx].sum_ab,
		};

		return msm_rpm_send_message(ctx == DUAL_CTX ?
			MSM_RPM_CTX_SLEEP_SET : MSM_RPM_CTX_ACTIVE_SET,
			type, id, &kvp, 1);
	}
#else
	return -ENODEV;
#endif
}

static int send_rpm_msg(struct msm_bus_node_device_type *ndev, int ctx)
{
	int ret, rpm_ctx;

	if (!ndev || ctx < 0 || ctx >= NUM_CTX)
		return -EINVAL;
	rpm_ctx = ctx == DUAL_CTX ? MSM_RPM_CTX_SLEEP_SET :
				    MSM_RPM_CTX_ACTIVE_SET;
	if (ndev->node_info->mas_rpm_id != -1) {
		ret = msm_bus_rpm_vote(ndev, ctx, RPM_BUS_MASTER_REQ,
				       ndev->node_info->mas_rpm_id);
		if (ret)
			return ret;
		trace_bus_agg_bw(ndev->node_info->id,
			ndev->node_info->mas_rpm_id, rpm_ctx,
			ndev->node_bw[ctx].sum_ab);
	}
	if (ndev->node_info->slv_rpm_id != -1) {
		ret = msm_bus_rpm_vote(ndev, ctx, RPM_BUS_SLAVE_REQ,
				       ndev->node_info->slv_rpm_id);
		if (ret)
			return ret;
		trace_bus_agg_bw(ndev->node_info->id,
			ndev->node_info->slv_rpm_id, rpm_ctx,
			ndev->node_bw[ctx].sum_ab);
	}
	return 0;
}

static int flush_bw_data(struct msm_bus_node_device_type *node_info, int ctx)
{
	int ret = 0;

	if (!node_info) {
		MSM_BUS_ERR("%s: Unable to find bus device for device",
			__func__);
		ret = -ENODEV;
		goto exit_flush_bw_data;
	}

	if (node_info->node_bw[ctx].last_sum_ab !=
				node_info->node_bw[ctx].sum_ab) {
		if (node_info->ap_owned) {
			struct msm_bus_node_device_type *bus_device =
			to_msm_bus_node(node_info->node_info->bus_device);
			struct msm_bus_fab_device_type *fabdev =
							bus_device->fabdev;

			/*
			 * For AP owned ports, only care about the Active
			 * context bandwidth.
			 */
			if (fabdev && (ctx == ACTIVE_CTX) &&
				fabdev->noc_ops.update_bw_reg &&
				fabdev->noc_ops.update_bw_reg
					(node_info->node_info->qos_params.mode))
				ret = fabdev->noc_ops.set_bw(node_info,
							fabdev->qos_base,
							fabdev->base_offset,
							fabdev->qos_off,
							fabdev->qos_freq);
		} else {
			ret = send_rpm_msg(node_info, ctx);

			if (ret)
				MSM_BUS_ERR("%s: Failed to send RPM msg for%d",
				__func__, node_info->node_info->id);
		}
		if (!ret)
			node_info->node_bw[ctx].last_sum_ab =
						node_info->node_bw[ctx].sum_ab;
	}

exit_flush_bw_data:
	return ret;

}

static int flush_clk_data(struct msm_bus_node_device_type *node, int ctx)
{
	struct nodeclk *clock;
	u64 rate;
	long rounded;
	int ret;

	if (!node || ctx < 0 || ctx >= NUM_CTX)
		return -EINVAL;
	clock = &node->clk[ctx];
	if (IS_ERR_OR_NULL(clock->clk))
		return 0;
	rate = node->node_bw[ctx].cur_clk_hz;
	if (rate > LONG_MAX)
		return -ERANGE;
	if (clock->rate != rate || clock->dirty) {
		clock->dirty = true;
		if (rate) {
			rounded = clk_round_rate(clock->clk, rate);
			if (rounded < 0)
				return rounded;
			ret = setrate_nodeclk(clock, rounded);
			if (ret)
				return ret;
			ret = enable_nodeclk(clock, &node->dev);
			if (ret)
				return ret;
			if (node->node_info->is_fab_dev &&
			    !IS_ERR_OR_NULL(node->bus_qos_clk.clk)) {
				ret = enable_nodeclk(&node->bus_qos_clk, &node->dev);
				if (ret)
					return ret;
			}
		} else {
			if (node->node_info->is_fab_dev &&
			    !IS_ERR_OR_NULL(node->bus_qos_clk.clk)) {
				ret = disable_nodeclk(&node->bus_qos_clk);
				if (ret)
					return ret;
			}
			ret = disable_nodeclk(clock);
			if (ret)
				return ret;
		}
		/* Desired-rate cache is valid only after every operation succeeded. */
		clock->rate = rate;
		clock->dirty = false;
	}
	if (node->node_info->is_fab_dev)
		node->node_bw[ctx].cur_clk_hz = 0;
	return 0;
}

static int msm_bus_agg_fab_clks(struct msm_bus_node_device_type *bus_dev)
{
	int ret = 0;
	struct msm_bus_node_device_type *node;
	int ctx;

	list_for_each_entry(node, &bus_dev->devlist, dev_link) {
		for (ctx = 0; ctx < NUM_CTX; ctx++) {
			if (node->node_bw[ctx].cur_clk_hz >=
					bus_dev->node_bw[ctx].cur_clk_hz)
				bus_dev->node_bw[ctx].cur_clk_hz =
						node->node_bw[ctx].cur_clk_hz;
		}
	}
	return ret;
}

int msm_bus_commit_data(struct list_head *clist)
{
	int ret, first_error = 0, node_error, ctx;
	struct msm_bus_node_device_type *node, *tmp;

	list_for_each_entry(node, clist, link)
		if (node->node_info->is_fab_dev)
			msm_bus_agg_fab_clks(node);
	list_for_each_entry_safe(node, tmp, clist, link) {
		node_error = 0;
		if (unlikely(node->node_info->defer_qos))
			node_error = msm_bus_dev_init_qos(&node->dev, NULL);
		if (!node_error) {
			for (ctx = 0; ctx < NUM_CTX; ctx++) {
				ret = flush_clk_data(node, ctx);
				if (!ret)
					ret = flush_bw_data(node, ctx);
				if (ret && !node_error)
					node_error = ret;
			}
		}
		if (node_error) {
			node->dirty = true;
			if (!first_error)
				first_error = node_error;
		} else {
			node->dirty = false;
			list_del_init(&node->link);
		}
	}
	return first_error;
}

void *msm_bus_realloc_devmem(struct device *dev, void *p, size_t old_size,
					size_t new_size, gfp_t flags)
{
	void *ret;
	size_t copy_size = old_size;

	if (!new_size) {
		devm_kfree(dev, p);
		return ZERO_SIZE_PTR;
	}

	if (new_size < old_size)
		copy_size = new_size;

	ret = devm_kzalloc(dev, new_size, flags);
	if (!ret)
		goto exit_realloc_devmem;

	memcpy(ret, p, copy_size);
	devm_kfree(dev, p);
exit_realloc_devmem:
	return ret;
}

static int msm_bus_fab_init_noc_ops(struct msm_bus_node_device_type *bus_dev)
{
	switch (bus_dev->fabdev->bus_type) {
	case MSM_BUS_NOC:
		return msm_bus_noc_set_ops(bus_dev);
	case MSM_BUS_BIMC:
		return msm_bus_bimc_set_ops(bus_dev);
	case MSM_BUS_QNOC:
		return msm_bus_qnoc_set_ops(bus_dev);
	default:
		return -EINVAL;
	}
}

static int msm_bus_disable_node_qos_clk(struct msm_bus_node_device_type *node)
{
	struct msm_bus_node_device_type *bus;
	int i, ret, first_error = 0;

	if (!node || IS_ERR_OR_NULL(node->node_info->bus_device))
		return -ENODEV;
	bus = to_msm_bus_node(node->node_info->bus_device);
	for (i = 0; i < node->num_node_qos_clks; i++) {
		ret = disable_nodeclk(&node->node_qos_clks[i]);
		if (ret && !first_error)
			first_error = ret;
	}
	for (i = 0; i < bus->num_node_qos_clks; i++) {
		ret = disable_nodeclk(&bus->node_qos_clks[i]);
		if (ret && !first_error)
			first_error = ret;
	}
	return first_error;
}

static int msm_bus_enable_node_qos_clk(struct msm_bus_node_device_type *node)
{
	struct msm_bus_node_device_type *bus, *owner;
	struct nodeclk *clock;
	long rounded;
	int set, i, ret;

	if (!node || IS_ERR_OR_NULL(node->node_info->bus_device))
		return -ENODEV;
	bus = to_msm_bus_node(node->node_info->bus_device);
	for (set = 0; set < 2; set++) {
		owner = set ? bus : node;
		for (i = 0; i < owner->num_node_qos_clks; i++) {
			clock = &owner->node_qos_clks[i];
			if (!clock->enable_only_clk) {
				rounded = clk_round_rate(clock->clk, 1);
				if (rounded < 0) {
					ret = rounded;
					goto disable;
				}
				ret = setrate_nodeclk(clock, rounded);
				if (ret)
					goto disable;
			}
			ret = enable_nodeclk(clock, &owner->dev);
			if (ret)
				goto disable;
		}
	}
	return 0;
disable:
	msm_bus_disable_node_qos_clk(node);
	return ret;
}

int msm_bus_enable_limiter(struct msm_bus_node_device_type *node_dev,
				int enable, uint64_t lim_bw)
{
	int ret = 0;
	struct msm_bus_node_device_type *bus_node_dev;

	if (!node_dev) {
		MSM_BUS_ERR("No device specified");
		ret = -ENXIO;
		goto exit_enable_limiter;
	}

	if (!node_dev->ap_owned) {
		MSM_BUS_ERR("Device is not AP owned %d",
						node_dev->node_info->id);
		ret = -ENXIO;
		goto exit_enable_limiter;
	}

	bus_node_dev = to_msm_bus_node(node_dev->node_info->bus_device);
	if (!bus_node_dev) {
		MSM_BUS_ERR("Unable to get bus device infofor %d",
			node_dev->node_info->id);
		ret = -ENXIO;
		goto exit_enable_limiter;
	}
	if (bus_node_dev->fabdev &&
		bus_node_dev->fabdev->noc_ops.limit_mport) {
		if (ret < 0) {
			MSM_BUS_ERR("Can't Enable QoS clk %d",
				node_dev->node_info->id);
			goto exit_enable_limiter;
		}
		bus_node_dev->fabdev->noc_ops.limit_mport(
				node_dev,
				bus_node_dev->fabdev->qos_base,
				bus_node_dev->fabdev->base_offset,
				bus_node_dev->fabdev->qos_off,
				bus_node_dev->fabdev->qos_freq,
				enable, lim_bw);
	}

exit_enable_limiter:
	return ret;
}

static int msm_bus_dev_init_qos(struct device *dev, void *data)
{
	struct msm_bus_node_device_type *node = to_msm_bus_node(dev), *bus;
	struct msm_bus_fab_device_type *fabric;
	int ret, disable_ret;

	if (!node->ap_owned || node->node_info->qos_params.mode == -1)
		return 0;
	if (IS_ERR_OR_NULL(node->node_info->bus_device))
		return -ENODEV;
	bus = to_msm_bus_node(node->node_info->bus_device);
	fabric = bus->fabdev;
	if (!fabric || !fabric->noc_ops.qos_init)
		return -ENODEV;
	if (fabric->bypass_qos_prg)
		return 0;
	ret = msm_bus_enable_node_qos_clk(node);
	if (ret)
		goto out;
	ret = fabric->noc_ops.qos_init(node, fabric->qos_base,
				       fabric->base_offset, fabric->qos_off,
				       fabric->qos_freq);
	disable_ret = msm_bus_disable_node_qos_clk(node);
	if (!ret)
		ret = disable_ret;
out:
	node->node_info->defer_qos = !!ret;
	return ret;
}

static int msm_bus_fabric_init(struct device *dev,
				struct msm_bus_node_device_type *pdata)
{
	struct msm_bus_node_device_type *node = to_msm_bus_node(dev);
	struct msm_bus_fab_device_type *fabric;
	int ret;

	if (node->node_info->virt_dev)
		return 0;
	if (IS_ERR_OR_NULL(pdata->fabdev) || !pdata->fabdev->qos_range)
		return -EINVAL;
	fabric = devm_kzalloc(dev, sizeof(*fabric), GFP_KERNEL);
	if (!fabric)
		return -ENOMEM;
	*fabric = *pdata->fabdev;
	node->fabdev = fabric;
	ret = msm_bus_fab_init_noc_ops(node);
	if (ret)
		return ret;
	fabric->qos_base = devm_ioremap(dev, fabric->pqos_base, fabric->qos_range);
	if (!fabric->qos_base)
		return -ENOMEM;
	return 0;
}

static int msm_bus_init_clk(struct device *bus_dev,
				struct msm_bus_node_device_type *pdata)
{
	unsigned int ctx;
	struct msm_bus_node_device_type *node_dev = to_msm_bus_node(bus_dev);
	int i, ret;

	for (ctx = 0; ctx < NUM_CTX; ctx++) {
		if (!IS_ERR_OR_NULL(pdata->clk[ctx].clk)) {
			node_dev->clk[ctx].clk = pdata->clk[ctx].clk;
			node_dev->clk[ctx].enable_only_clk =
					pdata->clk[ctx].enable_only_clk;
			node_dev->clk[ctx].setrate_only_clk =
					pdata->clk[ctx].setrate_only_clk;
			node_dev->clk[ctx].enable = false;
			node_dev->clk[ctx].dirty = false;
			strlcpy(node_dev->clk[ctx].reg_name,
				pdata->clk[ctx].reg_name, MAX_REG_NAME);
			node_dev->clk[ctx].reg = NULL;
			ret = bus_get_reg(&node_dev->clk[ctx], bus_dev);
			if (ret)
				return ret;
			MSM_BUS_DBG("%s: Valid node clk node %d ctx %d\n",
				__func__, node_dev->node_info->id, ctx);
		}
	}

	if (!IS_ERR_OR_NULL(pdata->bus_qos_clk.clk)) {
		node_dev->bus_qos_clk.clk = pdata->bus_qos_clk.clk;
		node_dev->bus_qos_clk.enable_only_clk =
					pdata->bus_qos_clk.enable_only_clk;
		node_dev->bus_qos_clk.setrate_only_clk =
					pdata->bus_qos_clk.setrate_only_clk;
		node_dev->bus_qos_clk.enable = false;
		strlcpy(node_dev->bus_qos_clk.reg_name,
			pdata->bus_qos_clk.reg_name, MAX_REG_NAME);
		node_dev->bus_qos_clk.reg = NULL;
		MSM_BUS_DBG("%s: Valid bus qos clk node %d\n", __func__,
						node_dev->node_info->id);
	}

	if (pdata->num_node_qos_clks) {
		node_dev->node_qos_clks = devm_kcalloc(bus_dev,
			pdata->num_node_qos_clks, sizeof(struct nodeclk), GFP_KERNEL);
		if (!node_dev->node_qos_clks)
			return -ENOMEM;
		node_dev->num_node_qos_clks = pdata->num_node_qos_clks;

		for (i = 0; i < pdata->num_node_qos_clks; i++) {
			node_dev->node_qos_clks[i].clk =
					pdata->node_qos_clks[i].clk;
			node_dev->node_qos_clks[i].enable_only_clk =
					pdata->node_qos_clks[i].enable_only_clk;
			node_dev->node_qos_clks[i].setrate_only_clk =
				pdata->node_qos_clks[i].setrate_only_clk;
			node_dev->node_qos_clks[i].enable = false;
			strlcpy(node_dev->node_qos_clks[i].reg_name,
				pdata->node_qos_clks[i].reg_name, MAX_REG_NAME);
			node_dev->node_qos_clks[i].reg = NULL;
			MSM_BUS_DBG("%s: Valid qos clk[%d] node %d %d Reg%s\n",
					__func__, i,
					node_dev->node_info->id,
					node_dev->num_node_qos_clks,
					node_dev->node_qos_clks[i].reg_name);
		}
	}

	return 0;
}

static int msm_bus_copy_node_info(struct msm_bus_node_device_type *pdata,
				struct device *bus_dev)
{
	int ret = 0;
	struct msm_bus_node_info_type *node_info = NULL;
	struct msm_bus_node_info_type *pdata_node_info = NULL;
	struct msm_bus_node_device_type *bus_node = NULL;

	bus_node = to_msm_bus_node(bus_dev);

	if (!bus_node || !pdata) {
		ret = -ENXIO;
		MSM_BUS_ERR("%s: NULL pointers for pdata or bus_node",
			__func__);
		goto exit_copy_node_info;
	}

	node_info = bus_node->node_info;
	pdata_node_info = pdata->node_info;

	node_info->name = pdata_node_info->name;
	node_info->id =  pdata_node_info->id;
	node_info->bus_device_id = pdata_node_info->bus_device_id;
	node_info->mas_rpm_id = pdata_node_info->mas_rpm_id;
	node_info->slv_rpm_id = pdata_node_info->slv_rpm_id;
	node_info->num_connections = pdata_node_info->num_connections;
	node_info->num_blist = pdata_node_info->num_blist;
	node_info->num_qports = pdata_node_info->num_qports;
	node_info->virt_dev = pdata_node_info->virt_dev;
	node_info->is_fab_dev = pdata_node_info->is_fab_dev;
	node_info->qos_params.mode = pdata_node_info->qos_params.mode;
	node_info->qos_params.prio1 = pdata_node_info->qos_params.prio1;
	node_info->qos_params.prio0 = pdata_node_info->qos_params.prio0;
	node_info->qos_params.prio_dflt = pdata_node_info->qos_params.prio_dflt;
	node_info->qos_params.urg_fwd_en =
				pdata_node_info->qos_params.urg_fwd_en;
	node_info->qos_params.reg_prio1 = pdata_node_info->qos_params.reg_prio1;
	node_info->qos_params.reg_prio0 = pdata_node_info->qos_params.reg_prio0;
	node_info->qos_params.prio_lvl = pdata_node_info->qos_params.prio_lvl;
	node_info->qos_params.prio_rd = pdata_node_info->qos_params.prio_rd;
	node_info->qos_params.prio_wr = pdata_node_info->qos_params.prio_wr;
	node_info->qos_params.gp = pdata_node_info->qos_params.gp;
	node_info->qos_params.thmp = pdata_node_info->qos_params.thmp;
	node_info->qos_params.ws = pdata_node_info->qos_params.ws;
	node_info->qos_params.bw_buffer = pdata_node_info->qos_params.bw_buffer;
	node_info->agg_params.buswidth = pdata_node_info->agg_params.buswidth;
	node_info->agg_params.agg_scheme =
					pdata_node_info->agg_params.agg_scheme;
	node_info->agg_params.vrail_comp =
					pdata_node_info->agg_params.vrail_comp;
	node_info->agg_params.num_aggports =
				pdata_node_info->agg_params.num_aggports;
	node_info->agg_params.num_util_levels =
				pdata_node_info->agg_params.num_util_levels;
	node_info->agg_params.util_levels = devm_kzalloc(bus_dev,
			sizeof(struct node_util_levels_type) *
			node_info->agg_params.num_util_levels,
			GFP_KERNEL);
	if (!node_info->agg_params.util_levels) {
		MSM_BUS_ERR("%s: Agg util level alloc failed\n", __func__);
		ret = -ENOMEM;
		goto exit_copy_node_info;
	}
	memcpy(node_info->agg_params.util_levels,
		pdata_node_info->agg_params.util_levels,
		sizeof(struct node_util_levels_type) *
			pdata_node_info->agg_params.num_util_levels);

	node_info->dev_connections = devm_kzalloc(bus_dev,
			sizeof(struct device *) *
				pdata_node_info->num_connections,
			GFP_KERNEL);
	if (!node_info->dev_connections) {
		MSM_BUS_ERR("%s:Bus dev connections alloc failed\n", __func__);
		ret = -ENOMEM;
		goto exit_copy_node_info;
	}

	node_info->connections = devm_kzalloc(bus_dev,
			sizeof(int) * pdata_node_info->num_connections,
			GFP_KERNEL);
	if (!node_info->connections) {
		MSM_BUS_ERR("%s:Bus connections alloc failed\n", __func__);
		devm_kfree(bus_dev, node_info->dev_connections);
		ret = -ENOMEM;
		goto exit_copy_node_info;
	}

	memcpy(node_info->connections,
		pdata_node_info->connections,
		sizeof(int) * pdata_node_info->num_connections);

	node_info->black_connections = devm_kzalloc(bus_dev,
			sizeof(struct device *) *
				pdata_node_info->num_blist,
			GFP_KERNEL);
	if (!node_info->black_connections) {
		MSM_BUS_ERR("%s: Bus black connections alloc failed\n",
			__func__);
		devm_kfree(bus_dev, node_info->dev_connections);
		devm_kfree(bus_dev, node_info->connections);
		ret = -ENOMEM;
		goto exit_copy_node_info;
	}

	node_info->bl_cons = devm_kzalloc(bus_dev,
			pdata_node_info->num_blist * sizeof(int),
			GFP_KERNEL);
	if (!node_info->bl_cons) {
		MSM_BUS_ERR("%s:Bus black list connections alloc failed\n",
					__func__);
		devm_kfree(bus_dev, node_info->black_connections);
		devm_kfree(bus_dev, node_info->dev_connections);
		devm_kfree(bus_dev, node_info->connections);
		ret = -ENOMEM;
		goto exit_copy_node_info;
	}

	memcpy(node_info->bl_cons,
		pdata_node_info->bl_cons,
		sizeof(int) * pdata_node_info->num_blist);

	node_info->qport = devm_kzalloc(bus_dev,
			sizeof(int) * pdata_node_info->num_qports,
			GFP_KERNEL);
	if (!node_info->qport) {
		MSM_BUS_ERR("%s:Bus qport allocation failed\n", __func__);
		devm_kfree(bus_dev, node_info->dev_connections);
		devm_kfree(bus_dev, node_info->connections);
		devm_kfree(bus_dev, node_info->bl_cons);
		ret = -ENOMEM;
		goto exit_copy_node_info;
	}

	memcpy(node_info->qport,
		pdata_node_info->qport,
		sizeof(int) * pdata_node_info->num_qports);

exit_copy_node_info:
	return ret;
}

static void msm_bus_node_release(struct device *dev)
{
	of_node_put(dev->of_node);
	kfree(to_msm_bus_node(dev));
}

static struct device *msm_bus_device_init(
		struct msm_bus_node_device_type *pdata, struct device *parent,
		struct qcom_smd_rpm *rpm)
{
	struct msm_bus_node_device_type *node;
	struct device *dev;
	int ret;

	node = kzalloc(sizeof(*node), GFP_KERNEL);
	if (!node)
		return ERR_PTR(-ENOMEM);
	dev = &node->dev;
	dev->release = msm_bus_node_release;
	device_initialize(dev);
	dev->parent = parent;
	dev->bus = &msm_bus_type;
	dev->of_node = of_node_get(pdata->of_node);
	node->of_node = dev->of_node;
	node->rpm = rpm;
	node->ap_owned = pdata->ap_owned;
	INIT_LIST_HEAD(&node->link);
	INIT_LIST_HEAD(&node->dev_link);
	INIT_LIST_HEAD(&node->devlist);
	INIT_LIST_HEAD(&node->owner_link);
	node->node_info = devm_kzalloc(dev, sizeof(*node->node_info), GFP_KERNEL);
	if (!node->node_info) {
		ret = -ENOMEM;
		goto put;
	}
	ret = msm_bus_copy_node_info(pdata, dev);
	if (ret)
		goto put;
	ret = dev_set_name(dev, "%s", node->node_info->name);
	if (ret)
		goto put;
	ret = device_add(dev);
	if (ret)
		goto put;
	ret = device_create_file(dev, &dev_attr_bw);
	if (ret) {
		device_unregister(dev);
		return ERR_PTR(ret);
	}
	return dev;
put:
	put_device(dev);
	return ERR_PTR(ret);
}

static int msm_bus_setup_dev_conn(struct device *bus_dev, void *data)
{
	struct msm_bus_node_device_type *bus_node = NULL;
	int ret = 0;
	int j;
	struct msm_bus_node_device_type *fab;

	bus_node = to_msm_bus_node(bus_dev);
	if (!bus_node) {
		MSM_BUS_ERR("%s: Can't get device info", __func__);
		ret = -ENODEV;
		goto exit_setup_dev_conn;
	}

	/* Setup parent bus device for this node */
	if (!bus_node->node_info->is_fab_dev) {
		struct device *bus_parent_device =
			bus_find_device(&msm_bus_type, NULL,
				(void *)&bus_node->node_info->bus_device_id,
				msm_bus_device_match_adhoc);

		if (!bus_parent_device) {
			MSM_BUS_ERR("%s: Error finding parentdev %d parent %d",
				__func__,
				bus_node->node_info->id,
				bus_node->node_info->bus_device_id);
			ret = -ENXIO;
			goto exit_setup_dev_conn;
		}
		bus_node->node_info->bus_device = bus_parent_device;
		fab = to_msm_bus_node(bus_parent_device);
		list_add_tail(&bus_node->dev_link, &fab->devlist);
	}

	bus_node->node_info->is_traversed = false;

	for (j = 0; j < bus_node->node_info->num_connections; j++) {
		bus_node->node_info->dev_connections[j] =
			bus_find_device(&msm_bus_type, NULL,
				(void *)&bus_node->node_info->connections[j],
				msm_bus_device_match_adhoc);

		if (!bus_node->node_info->dev_connections[j]) {
			MSM_BUS_ERR("%s: Error finding conn %d for device %d",
				__func__, bus_node->node_info->connections[j],
				 bus_node->node_info->id);
			ret = -ENODEV;
			goto exit_setup_dev_conn;
		}
	}

	for (j = 0; j < bus_node->node_info->num_blist; j++) {
		bus_node->node_info->black_connections[j] =
			bus_find_device(&msm_bus_type, NULL,
			(void *)
			&bus_node->node_info->bl_cons[j],
				msm_bus_device_match_adhoc);

		if (!bus_node->node_info->black_connections[j]) {
			MSM_BUS_ERR("%s: Error finding conn %d for device %d\n",
			__func__,
			bus_node->node_info->bl_cons[j],
			bus_node->node_info->id);
			ret = -ENODEV;
			goto exit_setup_dev_conn;
		}
	}

exit_setup_dev_conn:
	return ret;
}

static int msm_bus_node_debug(struct device *bus_dev, void *data)
{
	int j;
	int ret = 0;
	struct msm_bus_node_device_type *bus_node = NULL;

	bus_node = to_msm_bus_node(bus_dev);
	if (!bus_node) {
		MSM_BUS_ERR("%s: Can't get device info", __func__);
		ret = -ENODEV;
		goto exit_node_debug;
	}

	MSM_BUS_DBG("Device = %d buswidth %u", bus_node->node_info->id,
				bus_node->node_info->agg_params.buswidth);
	for (j = 0; j < bus_node->node_info->num_connections; j++) {
		struct msm_bus_node_device_type *bdev =
		to_msm_bus_node(bus_node->node_info->dev_connections[j]);
		MSM_BUS_DBG("\n\t Connection[%d] %d", j, bdev->node_info->id);
	}

	if (bus_node->node_info->is_fab_dev)
		msm_bus_floor_init(bus_dev);

exit_node_debug:
	return ret;
}

/* Drop all graph-owned references before unregistering any node. */
static void msm_bus_disconnect_graph(struct msm_bus_graph *graph)
{
	struct msm_bus_node_device_type *node;
	struct msm_bus_node_info_type *info;
	unsigned int i;

	list_for_each_entry(node, &graph->nodes, owner_link) {
		info = node->node_info;
		for (i = 0; i < node->num_lnodes; i++) {
			put_device(node->lnode_list[i].next_dev);
			node->lnode_list[i].next_dev = NULL;
		}
		for (i = 0; i < info->num_connections; i++) {
			put_device(info->dev_connections[i]);
			info->dev_connections[i] = NULL;
		}
		for (i = 0; i < info->num_blist; i++) {
			put_device(info->black_connections[i]);
			info->black_connections[i] = NULL;
		}
		if (!list_empty(&node->dev_link))
			list_del_init(&node->dev_link);
		put_device(info->bus_device);
		info->bus_device = NULL;
	}
}

static void msm_bus_destroy_graph(struct msm_bus_graph *graph)
{
	struct msm_bus_node_device_type *node, *tmp;
	int i;

	list_for_each_entry(node, &graph->nodes, owner_link) {
		device_remove_file(&node->dev, &dev_attr_bw);
		for (i = 0; i < NUM_CTX; i++)
			disable_nodeclk(&node->clk[i]);
		disable_nodeclk(&node->bus_qos_clk);
		for (i = 0; i < node->num_node_qos_clks; i++)
			disable_nodeclk(&node->node_qos_clks[i]);
	}
	msm_bus_disconnect_graph(graph);
	list_for_each_entry_safe(node, tmp, &graph->nodes, owner_link) {
		list_del_init(&node->owner_link);
		device_unregister(&node->dev);
	}
}

int msm_bus_device_remove(struct platform_device *pdev)
{
	struct msm_bus_graph *graph = platform_get_drvdata(pdev);

	if (!graph)
		return 0;
	mutex_lock(&msm_bus_graph_lock);
	if (msm_bus_graph_owner == graph) {
		msm_bus_arb_clearops_adhoc(&arb_ops);
		msm_bus_graph_owner = NULL;
	}
	msm_bus_destroy_graph(graph);
	platform_set_drvdata(pdev, NULL);
	mutex_unlock(&msm_bus_graph_lock);
	return 0;
}

static int msm_bus_device_probe(struct platform_device *pdev)
{
	struct msm_bus_device_node_registration *pdata;
	struct msm_bus_graph *graph;
	struct msm_bus_node_device_type *node;
	struct device *dev;
	unsigned int i;
	int ret;

	pdata = pdev->dev.of_node ? msm_bus_of_to_pdata(pdev) :
		pdev->dev.platform_data;
	if (IS_ERR_OR_NULL(pdata))
		return pdata ? PTR_ERR(pdata) : -ENODATA;
	graph = devm_kzalloc(&pdev->dev, sizeof(*graph), GFP_KERNEL);
	if (!graph)
		return -ENOMEM;
	INIT_LIST_HEAD(&graph->nodes);
	if (of_find_property(pdev->dev.of_node, "qcom,rpm", NULL)) {
#if IS_REACHABLE(CONFIG_QCOM_SMD_RPM)
		graph->rpm = qcom_smd_rpm_get(&pdev->dev);
		if (IS_ERR(graph->rpm))
			return PTR_ERR(graph->rpm);
#else
		return -EOPNOTSUPP;
#endif
	} else if (!IS_REACHABLE(CONFIG_MSM_RPM_SMD)) {
		/* Never call the legacy disabled-config success stub. */
		for (i = 0; i < pdata->num_devices; i++)
			if (!pdata->info[i].ap_owned &&
			    (pdata->info[i].node_info->mas_rpm_id != -1 ||
			     pdata->info[i].node_info->slv_rpm_id != -1))
				return -ENODEV;
	}
	mutex_lock(&msm_bus_graph_lock);
	if (msm_bus_graph_owner) {
		ret = -EBUSY;
		goto unlock;
	}
	for (i = 0; i < pdata->num_devices; i++) {
		dev = msm_bus_device_init(&pdata->info[i], &pdev->dev, graph->rpm);
		if (IS_ERR(dev)) {
			ret = PTR_ERR(dev);
			goto destroy;
		}
		node = to_msm_bus_node(dev);
		list_add_tail(&node->owner_link, &graph->nodes);
		ret = msm_bus_init_clk(dev, &pdata->info[i]);
		if (ret)
			goto destroy;
		if (node->node_info->is_fab_dev) {
			ret = msm_bus_fabric_init(dev, &pdata->info[i]);
			if (ret)
				goto destroy;
		}
	}
	list_for_each_entry(node, &graph->nodes, owner_link) {
		ret = msm_bus_setup_dev_conn(&node->dev, NULL);
		if (ret)
			goto destroy;
	}
	list_for_each_entry(node, &graph->nodes, owner_link) {
		ret = msm_bus_dev_init_qos(&node->dev, NULL);
		if (ret)
			goto destroy;
	}
	platform_set_drvdata(pdev, graph);
	msm_bus_graph_owner = graph;
	/* Publish arb callbacks only after the complete graph/QoS is usable. */
	msm_bus_arb_setops_adhoc(&arb_ops);
	list_for_each_entry(node, &graph->nodes, owner_link)
		msm_bus_node_debug(&node->dev, NULL);
	ret = 0;
	dev_info(&pdev->dev, "Bus scaling graph and RPM transport ready\n");
	goto unlock;
destroy:
	msm_bus_destroy_graph(graph);
unlock:
	mutex_unlock(&msm_bus_graph_lock);
	return ret;
}

static int msm_bus_device_rules_probe(struct platform_device *pdev)
{
	struct bus_rule_type *rule_data = NULL;
	int num_rules = 0;

	num_rules = msm_bus_of_get_static_rules(pdev, &rule_data);

	if (!rule_data)
		goto exit_rules_probe;

	msm_rule_register(num_rules, rule_data, NULL);
	static_rules.num_rules = num_rules;
	static_rules.rules = rule_data;
	pdev->dev.platform_data = &static_rules;

exit_rules_probe:
	return 0;
}

int msm_bus_device_rules_remove(struct platform_device *pdev)
{
	struct static_rules_type *static_rules = NULL;

	static_rules = pdev->dev.platform_data;
	if (static_rules)
		msm_rule_unregister(static_rules->num_rules,
					static_rules->rules, NULL);
	return 0;
}


static const struct of_device_id rules_match[] = {
	{.compatible = "qcom,msm-bus-static-bw-rules"},
	{}
};

static struct platform_driver msm_bus_rules_driver = {
	.probe = msm_bus_device_rules_probe,
	.remove = msm_bus_device_rules_remove,
	.driver = {
		.name = "msm_bus_rules_device",
		.of_match_table = rules_match,
	},
};

static const struct of_device_id fabric_match[] = {
	{.compatible = "qcom,msm-bus-device"},
	{}
};

static struct platform_driver msm_bus_device_driver = {
	.probe = msm_bus_device_probe,
	.remove = msm_bus_device_remove,
	.driver = {
		.name = "msm_bus_device",
		.suppress_bind_attrs = true,
		.of_match_table = fabric_match,
	},
};

int __init msm_bus_device_init_driver(void)
{
	int rc;

	MSM_BUS_ERR("msm_bus_fabric_init_driver\n");
	rc =  platform_driver_register(&msm_bus_device_driver);

	if (rc) {
		MSM_BUS_ERR("Failed to register bus device driver");
		return rc;
	}
	return platform_driver_register(&msm_bus_rules_driver);
}
fs_initcall(msm_bus_device_init_driver);
