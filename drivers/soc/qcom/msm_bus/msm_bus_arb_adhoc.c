// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2014-2016, 2018, The Linux Foundation. All rights reserved.
 */
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/rtmutex.h>
#include <linux/clk.h>
#include <linux/msm-bus.h>
#include "msm_bus_core.h"
#include "msm_bus_adhoc.h"
#include <trace/events/trace_msm_bus.h>

#define NUM_CL_HANDLES	50
#define NUM_LNODES	3
#define MAX_STR_CL	50

/* Search entries never reuse node->link: it may hold a failed hardware vote. */
struct bus_search_type {
	struct list_head link;
	struct msm_bus_node_device_type *node;
	struct bus_search_type *parent;
	bool processed;
};

static bool route_seen(struct list_head *search,
		       struct msm_bus_node_device_type *node)
{
	struct bus_search_type *entry;

	list_for_each_entry(entry, search, link)
		if (entry->node == node)
			return true;
	return false;
}

/* Retain the legacy cumulative blacklist policy during breadth-first search. */
static bool route_blocked(struct list_head *search, struct device *dev)
{
	struct bus_search_type *entry;
	unsigned int i;

	list_for_each_entry(entry, search, link) {
		if (!entry->processed)
			continue;
		for (i = 0; i < entry->node->node_info->num_blist; i++)
			if (entry->node->node_info->black_connections[i] == dev)
				return true;
	}
	return false;
}

struct handle_type {
	int num_entries;
	struct msm_bus_client **cl_list;
};

static struct handle_type handle_list;
static LIST_HEAD(input_list);
static LIST_HEAD(apply_list);
static LIST_HEAD(commit_list);

DEFINE_RT_MUTEX(msm_bus_adhoc_lock);
static bool adhoc_online;

void msm_bus_arb_lock_adhoc(void)
{
	rt_mutex_lock(&msm_bus_adhoc_lock);
}

void msm_bus_arb_unlock_adhoc(void)
{
	rt_mutex_unlock(&msm_bus_adhoc_lock);
}

/*
 * Duplicate instantiaion from msm_bus_arb.c. Todo there needs to be a
 * "util" file for these common func/macros.
 *
 */
uint64_t msm_bus_div64(uint64_t num, unsigned int base)
{
	uint64_t *n = &num;

	if ((num > 0) && (num < base))
		return 1;

	switch (base) {
	case 0:
		WARN(1, "AXI: Divide by 0 attempted\n");
	case 1: return num;
	case 2: return (num >> 1);
	case 4: return (num >> 2);
	case 8: return (num >> 3);
	case 16: return (num >> 4);
	case 32: return (num >> 5);
	}

	do_div(*n, base);
	return *n;
}

int msm_bus_device_match_adhoc(struct device *dev, void *id)
{
	int ret = 0;
	struct msm_bus_node_device_type *bnode = to_msm_bus_node(dev);

	if (bnode)
		ret = (bnode->node_info->id == *(unsigned int *)id);
	else
		ret = 0;

	return ret;
}

static int gen_lnode(struct device *dev, struct device *next_dev,
		     int next_idx, const char *cl_name)
{
	struct msm_bus_node_device_type *node;
	struct link_node *lnode, *new_list;
	int index, new_count;

	if (!dev || (next_dev && next_idx < 0))
		return -EINVAL;
	node = to_msm_bus_node(dev);
	for (index = 0; index < node->num_lnodes; index++)
		if (!node->lnode_list[index].in_use)
			break;
	if (index == node->num_lnodes) {
		new_count = node->num_lnodes + NUM_LNODES;
		new_list = msm_bus_realloc_devmem(dev, node->lnode_list,
				sizeof(*new_list) * node->num_lnodes,
				sizeof(*new_list) * new_count, GFP_KERNEL);
		if (!new_list)
			return -ENOMEM;
		node->lnode_list = new_list;
		node->num_lnodes = new_count;
	}
	lnode = &node->lnode_list[index];
	memset(lnode, 0, sizeof(*lnode));
	lnode->in_use = true;
	lnode->cl_name = cl_name;
	lnode->next = next_dev ? next_idx : -1;
	lnode->next_dev = get_device(next_dev);
	return index;
}

static int remove_lnode(struct msm_bus_node_device_type *node, int index)
{
	struct link_node *lnode;

	if (!node || index < 0 || index >= node->num_lnodes)
		return -EINVAL;
	lnode = &node->lnode_list[index];
	if (!lnode->in_use)
		return -EINVAL;
	put_device(lnode->next_dev);
	memset(lnode, 0, sizeof(*lnode));
	lnode->next = -1;
	return 0;
}

/* Release an unvoted/partially constructed chain without staging bus votes. */
static void free_unvoted_path(struct device *dev, int index)
{
	while (dev && index >= 0) {
		struct msm_bus_node_device_type *node = to_msm_bus_node(dev);
		struct device *next;
		int next_index;

		if (index >= node->num_lnodes || !node->lnode_list[index].in_use)
			break;
		next = node->lnode_list[index].next_dev;
		next_index = node->lnode_list[index].next;
		remove_lnode(node, index);
		dev = next;
		index = next_index;
	}
}

static int getpath(struct device *src_dev, unsigned int dest, const char *cl_name)
{
	struct bus_search_type *entry, *child, *tmp, *found = NULL;
	struct device *next_dev = NULL;
	LIST_HEAD(search);
	int first_hop = -ENODEV, next_idx = -1;
	unsigned int i;

	if (!src_dev)
		return -EINVAL;
	entry = kzalloc(sizeof(*entry), GFP_KERNEL);
	if (!entry)
		return -ENOMEM;
	entry->node = to_msm_bus_node(src_dev);
	list_add_tail(&entry->link, &search);

	/* Appended entries form a FIFO; parents retain the actual chosen edge. */
	list_for_each_entry(entry, &search, link) {
		if (entry->node->node_info->id == dest) {
			found = entry;
			break;
		}
		entry->processed = true;
		for (i = 0; i < entry->node->node_info->num_connections; i++) {
			struct device *dev = entry->node->node_info->dev_connections[i];
			struct msm_bus_node_device_type *node;

			if (!dev) {
				first_hop = -ENODEV;
				goto out;
			}
			node = to_msm_bus_node(dev);
			if (route_seen(&search, node) || route_blocked(&search, dev))
				continue;
			child = kzalloc(sizeof(*child), GFP_KERNEL);
			if (!child) {
				first_hop = -ENOMEM;
				goto out;
			}
			child->node = node;
			child->parent = entry;
			list_add_tail(&child->link, &search);
		}
	}
	if (!found)
		goto out;

	for (entry = found; entry; entry = entry->parent) {
		first_hop = gen_lnode(&entry->node->dev, next_dev, next_idx, cl_name);
		if (first_hop < 0) {
			free_unvoted_path(next_dev, next_idx);
			goto out;
		}
		next_dev = &entry->node->dev;
		next_idx = first_hop;
	}
out:
	list_for_each_entry_safe(entry, tmp, &search, link) {
		list_del(&entry->link);
		kfree(entry);
	}
	return first_hop;
}

static uint64_t scheme1_agg_scheme(struct msm_bus_node_device_type *bus_dev,
			struct msm_bus_node_device_type *fab_dev, int ctx)
{
	uint64_t max_ib;
	uint64_t sum_ab;
	uint64_t bw_max_hz;
	uint32_t util_fact = 0;
	uint32_t vrail_comp = 0;
	struct node_util_levels_type *utils;
	int i;
	int num_util_levels;

	/*
	 *  Account for Util factor and vrail comp.
	 *  Util factor is picked according to the current sum(AB) for this
	 *  node and for this context.
	 *  Vrail comp is fixed for the entire performance range.
	 *  They default to 100 if absent.
	 *
	 *  The aggregated clock is computed as:
	 *  Freq_hz = max((sum(ab) * util_fact)/num_chan, max(ib)/vrail_comp)
	 *				/ bus-width
	 */
	if (bus_dev->node_info->agg_params.num_util_levels) {
		utils = bus_dev->node_info->agg_params.util_levels;
		num_util_levels =
			bus_dev->node_info->agg_params.num_util_levels;
	} else {
		utils = fab_dev->node_info->agg_params.util_levels;
		num_util_levels =
			fab_dev->node_info->agg_params.num_util_levels;
	}

	sum_ab = bus_dev->node_bw[ctx].sum_ab;
	max_ib = bus_dev->node_bw[ctx].max_ib;

	for (i = 0; i < num_util_levels; i++) {
		if (sum_ab < utils[i].threshold) {
			util_fact = utils[i].util_fact;
			break;
		}
	}
	if (i == num_util_levels)
		util_fact = utils[(num_util_levels - 1)].util_fact;

	vrail_comp = bus_dev->node_info->agg_params.vrail_comp ?
			bus_dev->node_info->agg_params.vrail_comp :
			fab_dev->node_info->agg_params.vrail_comp;

	bus_dev->node_bw[ctx].vrail_used = vrail_comp;
	bus_dev->node_bw[ctx].util_used = util_fact;

	if (util_fact && (util_fact != 100)) {
		sum_ab *= util_fact;
		sum_ab = msm_bus_div64(sum_ab, 100);
	}

	if (vrail_comp && (vrail_comp != 100)) {
		max_ib *= 100;
		max_ib = msm_bus_div64(max_ib, vrail_comp);
	}

	/* Account for multiple channels if any */
	if (bus_dev->node_info->agg_params.num_aggports > 1)
		sum_ab = msm_bus_div64(sum_ab,
				bus_dev->node_info->agg_params.num_aggports);

	if (!bus_dev->node_info->agg_params.buswidth) {
		MSM_BUS_WARN("No bus width found for %d. Using default\n",
					bus_dev->node_info->id);
		bus_dev->node_info->agg_params.buswidth = 8;
	}

	bw_max_hz = max(max_ib, sum_ab);
	bw_max_hz = msm_bus_div64(bw_max_hz,
				bus_dev->node_info->agg_params.buswidth);

	return bw_max_hz;
}

static uint64_t legacy_agg_scheme(struct msm_bus_node_device_type *bus_dev,
			struct msm_bus_node_device_type *fab_dev, int ctx)
{
	uint64_t max_ib;
	uint64_t sum_ab;
	uint64_t bw_max_hz;
	uint32_t util_fact = 0;
	uint32_t vrail_comp = 0;

	/*
	 *  Util_fact and vrail comp are obtained from fabric/Node's dts
	 *  properties and are fixed for the entire performance range.
	 *  They default to 100 if absent.
	 *
	 *  The clock frequency is computed as:
	 *  Freq_hz = max((sum(ab) * util_fact)/num_chan, max(ib)/vrail_comp)
	 *				/ bus-width
	 */
	util_fact = fab_dev->node_info->agg_params.util_levels[0].util_fact;
	vrail_comp = fab_dev->node_info->agg_params.vrail_comp;

	if (bus_dev->node_info->agg_params.num_util_levels)
		util_fact =
		bus_dev->node_info->agg_params.util_levels[0].util_fact ?
		bus_dev->node_info->agg_params.util_levels[0].util_fact :
		util_fact;

	vrail_comp = bus_dev->node_info->agg_params.vrail_comp ?
			bus_dev->node_info->agg_params.vrail_comp :
			vrail_comp;

	bus_dev->node_bw[ctx].vrail_used = vrail_comp;
	bus_dev->node_bw[ctx].util_used = util_fact;
	sum_ab = bus_dev->node_bw[ctx].sum_ab;
	max_ib = bus_dev->node_bw[ctx].max_ib;

	if (util_fact && (util_fact != 100)) {
		sum_ab *= util_fact;
		sum_ab = msm_bus_div64(sum_ab, 100);
	}

	if (vrail_comp && (vrail_comp != 100)) {
		max_ib *= 100;
		max_ib = msm_bus_div64(max_ib, vrail_comp);
	}

	/* Account for multiple channels if any */
	if (bus_dev->node_info->agg_params.num_aggports > 1)
		sum_ab = msm_bus_div64(sum_ab,
				bus_dev->node_info->agg_params.num_aggports);

	if (!bus_dev->node_info->agg_params.buswidth) {
		MSM_BUS_WARN("No bus width found for %d. Using default\n",
					bus_dev->node_info->id);
		bus_dev->node_info->agg_params.buswidth = 8;
	}

	bw_max_hz = max(max_ib, sum_ab);
	bw_max_hz = msm_bus_div64(bw_max_hz,
				bus_dev->node_info->agg_params.buswidth);

	return bw_max_hz;
}

static uint64_t aggregate_bus_req(struct msm_bus_node_device_type *bus_dev,
									int ctx)
{
	uint64_t bw_hz = 0;
	int i;
	struct msm_bus_node_device_type *fab_dev = NULL;
	uint32_t agg_scheme;
	uint64_t max_ib = 0;
	uint64_t sum_ab = 0;

	if (!bus_dev || !to_msm_bus_node(bus_dev->node_info->bus_device)) {
		MSM_BUS_ERR("Bus node pointer is Invalid");
		goto exit_agg_bus_req;
	}

	fab_dev = to_msm_bus_node(bus_dev->node_info->bus_device);
	for (i = 0; i < bus_dev->num_lnodes; i++) {
		max_ib = max(max_ib, bus_dev->lnode_list[i].lnode_ib[ctx]);
		sum_ab += bus_dev->lnode_list[i].lnode_ab[ctx];
	}

	bus_dev->node_bw[ctx].sum_ab = sum_ab;
	bus_dev->node_bw[ctx].max_ib = max_ib;

	if (bus_dev->node_info->agg_params.agg_scheme != AGG_SCHEME_NONE)
		agg_scheme = bus_dev->node_info->agg_params.agg_scheme;
	else
		agg_scheme = fab_dev->node_info->agg_params.agg_scheme;

	switch (agg_scheme) {
	case AGG_SCHEME_1:
		bw_hz = scheme1_agg_scheme(bus_dev, fab_dev, ctx);
		break;
	case AGG_SCHEME_LEG:
		bw_hz = legacy_agg_scheme(bus_dev, fab_dev, ctx);
		break;
	}

exit_agg_bus_req:
	return bw_hz;
}


static void del_inp_list(struct list_head *list)
{
	struct rule_update_path_info *rule_node;
	struct rule_update_path_info *rule_node_tmp;

	list_for_each_entry_safe(rule_node, rule_node_tmp, list, link) {
		list_del(&rule_node->link);
		rule_node->added = false;
	}
}

static void del_op_list(struct list_head *list)
{
	struct rule_apply_rcm_info *rule;
	struct rule_apply_rcm_info *rule_tmp;

	list_for_each_entry_safe(rule, rule_tmp, list, link)
		list_del(&rule->link);
}

static int msm_bus_apply_rules(struct list_head *list, bool after_clk_commit)
{
	struct rule_apply_rcm_info *rule;
	struct device *dev = NULL;
	struct msm_bus_node_device_type *dev_info = NULL;
	int ret = 0;

	list_for_each_entry(rule, list, link) {
		if (rule->after_clk_commit != after_clk_commit)
			continue;

		dev = bus_find_device(&msm_bus_type, NULL,
				(void *) &rule->id,
				msm_bus_device_match_adhoc);

		if (!dev) {
			MSM_BUS_ERR("Can't find dev node for %d", rule->id);
			continue;
		}
		dev_info = to_msm_bus_node(dev);

		ret = msm_bus_enable_limiter(dev_info, rule->throttle,
							rule->lim_bw);
		if (ret)
			MSM_BUS_ERR("Failed to set limiter for %d", rule->id);
	}

	return ret;
}

static int commit_data(void)
{
	bool rules_registered = msm_rule_are_rules_registered();
	int ret;

	if (rules_registered) {
		ret = msm_rules_update_path(&input_list, &apply_list);
		if (ret)
			goto out_rules;
		ret = msm_bus_apply_rules(&apply_list, false);
		if (ret)
			goto out_rules;
	}
	ret = msm_bus_commit_data(&commit_list);
	if (!ret && rules_registered)
		ret = msm_bus_apply_rules(&apply_list, true);
out_rules:
	if (rules_registered) {
		del_inp_list(&input_list);
		del_op_list(&apply_list);
	}
	INIT_LIST_HEAD(&input_list);
	INIT_LIST_HEAD(&apply_list);
	/* Failed nodes stay dirty/on commit_list until a successful retry. */
	return ret;
}

static void add_node_to_clist(struct msm_bus_node_device_type *node)
{
	struct msm_bus_node_device_type *node_parent =
			to_msm_bus_node(node->node_info->bus_device);

	if (!node->dirty) {
		list_add_tail(&node->link, &commit_list);
		node->dirty = true;
	}

	if (!node_parent->dirty) {
		list_add_tail(&node_parent->link, &commit_list);
		node_parent->dirty = true;
	}
}

static int update_path(struct device *src_dev, int dest, uint64_t act_req_ib,
			uint64_t act_req_bw, uint64_t slp_req_ib,
			uint64_t slp_req_bw, uint64_t cur_ib, uint64_t cur_bw,
			int src_idx, int ctx)
{
	struct device *next_dev = NULL;
	struct link_node *lnode = NULL;
	struct msm_bus_node_device_type *dev_info = NULL;
	int curr_idx;
	int ret = 0;
	struct rule_update_path_info *rule_node;
	bool rules_registered = msm_rule_are_rules_registered();

	if (IS_ERR_OR_NULL(src_dev)) {
		MSM_BUS_ERR("%s: No source device", __func__);
		ret = -ENODEV;
		goto exit_update_path;
	}

	next_dev = src_dev;

	if (src_idx < 0) {
		MSM_BUS_ERR("%s: Invalid lnode idx %d", __func__, src_idx);
		ret = -ENXIO;
		goto exit_update_path;
	}
	curr_idx = src_idx;

	while (next_dev) {
		int i;

		dev_info = to_msm_bus_node(next_dev);

		if (curr_idx >= dev_info->num_lnodes) {
			MSM_BUS_ERR("%s: Invalid lnode Idx %d num lnodes %d",
			 __func__, curr_idx, dev_info->num_lnodes);
			ret = -ENXIO;
			goto exit_update_path;
		}

		lnode = &dev_info->lnode_list[curr_idx];
		if (!lnode) {
			MSM_BUS_ERR("%s: Invalid lnode ptr lnode %d",
				 __func__, curr_idx);
			ret = -ENXIO;
			goto exit_update_path;
		}
		lnode->lnode_ib[ACTIVE_CTX] = act_req_ib;
		lnode->lnode_ab[ACTIVE_CTX] = act_req_bw;
		lnode->lnode_ib[DUAL_CTX] = slp_req_ib;
		lnode->lnode_ab[DUAL_CTX] = slp_req_bw;

		for (i = 0; i < NUM_CTX; i++)
			dev_info->node_bw[i].cur_clk_hz =
					aggregate_bus_req(dev_info, i);

		add_node_to_clist(dev_info);

		if (rules_registered) {
			rule_node = &dev_info->node_info->rule;
			rule_node->id = dev_info->node_info->id;
			rule_node->ib = dev_info->node_bw[ACTIVE_CTX].max_ib;
			rule_node->ab = dev_info->node_bw[ACTIVE_CTX].sum_ab;
			rule_node->clk =
				dev_info->node_bw[ACTIVE_CTX].cur_clk_hz;
			if (!rule_node->added) {
				list_add_tail(&rule_node->link, &input_list);
				rule_node->added = true;
			}
		}

		next_dev = lnode->next_dev;
		curr_idx = lnode->next;
	}

exit_update_path:
	return ret;
}

static int remove_path(struct device *src_dev, int dst, uint64_t cur_ib,
			uint64_t cur_ab, int src_idx, int active_only)
{
	struct device *next_dev = NULL;
	struct link_node *lnode = NULL;
	struct msm_bus_node_device_type *dev_info = NULL;
	int ret = 0;
	int cur_idx = src_idx;
	int next_idx;

	/* Update the current path to zero out all request from
	 * this cient on all paths
	 */
	if (!src_dev) {
		MSM_BUS_ERR("%s: Can't find source device", __func__);
		ret = -ENODEV;
		goto exit_remove_path;
	}

	ret = update_path(src_dev, dst, 0, 0, 0, 0, cur_ib, cur_ab, src_idx,
							active_only);
	if (ret) {
		MSM_BUS_ERR("%s: Error zeroing out path ctx %d",
					__func__, ACTIVE_CTX);
		goto exit_remove_path;
	}

	next_dev = src_dev;

	while (next_dev) {
		dev_info = to_msm_bus_node(next_dev);
		lnode = &dev_info->lnode_list[cur_idx];
		next_idx = lnode->next;
		next_dev = lnode->next_dev;
		remove_lnode(dev_info, cur_idx);
		cur_idx = next_idx;
	}

exit_remove_path:
	return ret;
}

static void getpath_debug(int src, int curr, int active_only)
{
	struct device *dev_node;
	struct device *dev_it;
	unsigned int hop = 1;
	int idx;
	struct msm_bus_node_device_type *devinfo;
	int i;

	dev_node = bus_find_device(&msm_bus_type, NULL,
				(void *) &src,
				msm_bus_device_match_adhoc);

	if (!dev_node) {
		MSM_BUS_ERR("SRC NOT FOUND %d", src);
		return;
	}

	idx = curr;
	devinfo = to_msm_bus_node(dev_node);
	dev_it = dev_node;

	MSM_BUS_ERR("Route list Src %d", src);
	while (dev_it) {
		struct msm_bus_node_device_type *busdev =
			to_msm_bus_node(devinfo->node_info->bus_device);

		MSM_BUS_ERR("Hop[%d] at Device %d ctx %d", hop,
					devinfo->node_info->id, active_only);

		for (i = 0; i < NUM_CTX; i++) {
			MSM_BUS_ERR("dev info sel ib %llu",
						devinfo->node_bw[i].cur_clk_hz);
			MSM_BUS_ERR("dev info sel ab %llu",
						devinfo->node_bw[i].sum_ab);
		}

		dev_it = devinfo->lnode_list[idx].next_dev;
		idx = devinfo->lnode_list[idx].next;
		if (dev_it)
			devinfo = to_msm_bus_node(dev_it);

		MSM_BUS_ERR("Bus Device %d", busdev->node_info->id);
		MSM_BUS_ERR("Bus Clock %llu", busdev->clk[active_only].rate);

		if (idx < 0)
			break;
		hop++;
	}
}

static void unregister_client_adhoc(uint32_t cl)
{
	int i;
	struct msm_bus_scale_pdata *pdata;
	int lnode, curr, dest;
	uint64_t  cur_clk, cur_bw;
	struct msm_bus_client *client;
	struct device *src_dev;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		goto exit_unregister_client;
	}

	if (!cl || cl >= (unsigned int)handle_list.num_entries || !handle_list.cl_list[cl]) {
		MSM_BUS_ERR("%s: Invalid cl handle passed unregister\n",
				__func__);
		goto exit_unregister_client;
	}
	client = handle_list.cl_list[cl];
	pdata = client->pdata;
	if (!pdata) {
		MSM_BUS_ERR("%s: Null pdata passed to unregister\n",
				__func__);
		goto exit_unregister_client;
	}

	curr = client->curr;
	if ((curr < 0) || (curr >= pdata->num_usecases)) {
		MSM_BUS_ERR("Invalid index Defaulting curr to 0");
		curr = 0;
	}

	MSM_BUS_DBG("%s: Unregistering client %p", __func__, client);

	for (i = 0; i < pdata->usecase->num_paths; i++) {
		dest = client->pdata->usecase[curr].vectors[i].dst;

		lnode = client->src_pnode[i];
		src_dev = client->src_devs[i];
		cur_clk = client->pdata->usecase[curr].vectors[i].ib;
		cur_bw = client->pdata->usecase[curr].vectors[i].ab;
		remove_path(src_dev, dest, cur_clk, cur_bw, lnode,
						pdata->active_only);
		put_device(src_dev);
	}
	commit_data();
	msm_bus_dbg_client_data(client->pdata, MSM_BUS_DBG_UNREGISTER, cl);
	kfree(client->src_pnode);
	kfree(client->src_devs);
	kfree(client);
	handle_list.cl_list[cl] = NULL;
exit_unregister_client:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
}

static int alloc_handle_lst(int size)
{
	int ret = 0;
	struct msm_bus_client **t_cl_list;

	if (!handle_list.num_entries) {
		t_cl_list = kzalloc(sizeof(struct msm_bus_client *)
			* NUM_CL_HANDLES, GFP_KERNEL);
		if (ZERO_OR_NULL_PTR(t_cl_list)) {
			ret = -ENOMEM;
			MSM_BUS_ERR("%s: Failed to allocate handles list",
								__func__);
			goto exit_alloc_handle_lst;
		}
		handle_list.cl_list = t_cl_list;
		handle_list.num_entries += NUM_CL_HANDLES;
	} else {
		t_cl_list = krealloc(handle_list.cl_list,
				sizeof(struct msm_bus_client *) *
				(handle_list.num_entries + NUM_CL_HANDLES),
				GFP_KERNEL);
		if (ZERO_OR_NULL_PTR(t_cl_list)) {
			ret = -ENOMEM;
			MSM_BUS_ERR("%s: Failed to allocate handles list",
								__func__);
			goto exit_alloc_handle_lst;
		}

		handle_list.cl_list = t_cl_list;
		memset(&handle_list.cl_list[handle_list.num_entries], 0,
			NUM_CL_HANDLES * sizeof(struct msm_bus_client *));
		handle_list.num_entries += NUM_CL_HANDLES;
	}
exit_alloc_handle_lst:
	return ret;
}

static uint32_t gen_handle(struct msm_bus_client *client)
{
	uint32_t handle = 0;
	int i;
	int ret = 0;

	for (i = 0; i < handle_list.num_entries; i++) {
		if (i && !handle_list.cl_list[i]) {
			handle = i;
			break;
		}
	}

	if (!handle) {
		ret = alloc_handle_lst(NUM_CL_HANDLES);

		if (ret) {
			MSM_BUS_ERR("%s: Failed to allocate handle list",
							__func__);
			goto exit_gen_handle;
		}
		handle = i + 1;
	}
	handle_list.cl_list[handle] = client;
exit_gen_handle:
	return handle;
}

static uint32_t register_client_adhoc(struct msm_bus_scale_pdata *pdata)
{
	struct msm_bus_client *client = NULL;
	uint32_t handle = 0;
	int i, j, paths;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online || !pdata || !pdata->name ||
	    pdata->num_usecases <= 0 || !pdata->usecase)
		goto out;
	paths = pdata->usecase[0].num_paths;
	if (paths <= 0 || !pdata->usecase[0].vectors)
		goto out;
	/* A handle owns one fixed set of routes, shared by all vote levels. */
	for (i = 0; i < pdata->num_usecases; i++) {
		if (pdata->usecase[i].num_paths != paths || !pdata->usecase[i].vectors)
			goto out;
		for (j = 0; j < paths; j++)
			if (pdata->usecase[i].vectors[j].src != pdata->usecase[0].vectors[j].src ||
			    pdata->usecase[i].vectors[j].dst != pdata->usecase[0].vectors[j].dst)
				goto out;
	}
	client = kzalloc(sizeof(*client), GFP_KERNEL);
	if (!client)
		goto out;
	client->pdata = pdata;
	client->curr = -1;
	client->src_pnode = kmalloc_array(paths, sizeof(*client->src_pnode), GFP_KERNEL);
	client->src_devs = kcalloc(paths, sizeof(*client->src_devs), GFP_KERNEL);
	if (!client->src_pnode || !client->src_devs)
		goto free_client;
	for (i = 0; i < paths; i++)
		client->src_pnode[i] = -1;
	for (i = 0; i < paths; i++) {
		unsigned int src = pdata->usecase[0].vectors[i].src;
		int dest = pdata->usecase[0].vectors[i].dst;

		client->src_devs[i] = bus_find_device(&msm_bus_type, NULL, &src,
						   msm_bus_device_match_adhoc);
		if (!client->src_devs[i])
			goto free_paths;
		client->src_pnode[i] = getpath(client->src_devs[i], dest, pdata->name);
		if (client->src_pnode[i] < 0)
			goto free_paths;
	}
	handle = gen_handle(client);
	if (!handle)
		goto free_paths;
	msm_bus_dbg_client_data(pdata, MSM_BUS_DBG_REGISTER, handle);
	goto out;

free_paths:
	for (i = 0; i < paths; i++) {
		free_unvoted_path(client->src_devs[i], client->src_pnode[i]);
		put_device(client->src_devs[i]);
	}
free_client:
	kfree(client->src_devs);
	kfree(client->src_pnode);
	kfree(client);
out:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
	return handle;
}

static int update_client_paths(struct msm_bus_client *client, bool log_trns,
							unsigned int idx)
{
	int lnode, src, dest, cur_idx;
	uint64_t req_clk, req_bw, curr_clk, curr_bw, slp_clk, slp_bw;
	int i, ret = 0;
	struct msm_bus_scale_pdata *pdata;
	struct device *src_dev;

	if (!client) {
		MSM_BUS_ERR("Client handle  Null");
		ret = -ENXIO;
		goto exit_update_client_paths;
	}

	pdata = client->pdata;
	if (!pdata) {
		MSM_BUS_ERR("Client pdata Null");
		ret = -ENXIO;
		goto exit_update_client_paths;
	}

	cur_idx = client->curr;
	for (i = 0; i < pdata->usecase->num_paths; i++) {
		src = pdata->usecase[idx].vectors[i].src;
		dest = pdata->usecase[idx].vectors[i].dst;

		lnode = client->src_pnode[i];
		src_dev = client->src_devs[i];
		req_clk = client->pdata->usecase[idx].vectors[i].ib;
		req_bw = client->pdata->usecase[idx].vectors[i].ab;
		if (cur_idx < 0) {
			curr_clk = 0;
			curr_bw = 0;
		} else {
			curr_clk =
				client->pdata->usecase[cur_idx].vectors[i].ib;
			curr_bw = client->pdata->usecase[cur_idx].vectors[i].ab;
			MSM_BUS_DBG("%s:ab: %llu ib: %llu\n", __func__,
					curr_bw, curr_clk);
		}

		if (pdata->active_only) {
			slp_clk = 0;
			slp_bw = 0;
		} else {
			slp_clk = req_clk;
			slp_bw = req_bw;
		}

		ret = update_path(src_dev, dest, req_clk, req_bw, slp_clk,
			slp_bw, curr_clk, curr_bw, lnode, pdata->active_only);

		if (ret) {
			MSM_BUS_ERR("%s: Update path failed! %d ctx %d\n",
					__func__, ret, pdata->active_only);
			goto exit_update_client_paths;
		}

		if (log_trns)
			getpath_debug(src, lnode, pdata->active_only);
	}
	client->curr = idx;
	ret = commit_data();
exit_update_client_paths:
	return ret;
}

static int update_context(uint32_t cl, bool active_only,
					unsigned int ctx_idx)
{
	int ret = 0;
	struct msm_bus_scale_pdata *pdata;
	struct msm_bus_client *client;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		ret = -ENODEV;
		goto exit_update_context;
	}

	if (!cl || cl >= handle_list.num_entries) {
		MSM_BUS_ERR("%s: Invalid client handle %d", __func__, cl);
		ret = -ENXIO;
		goto exit_update_context;
	}

	client = handle_list.cl_list[cl];
	if (!client) {
		ret = -ENXIO;
		goto exit_update_context;
	}

	pdata = client->pdata;
	if (!pdata) {
		ret = -ENXIO;
		goto exit_update_context;
	}
	if (pdata->active_only == active_only) {
		MSM_BUS_ERR("No change in context(%d==%d), skip\n",
					pdata->active_only, active_only);
		ret = ctx_idx == client->curr ? commit_data() : -EINVAL;
		goto exit_update_context;
	}

	if (ctx_idx >= pdata->num_usecases) {
		MSM_BUS_ERR("Client %u passed invalid index: %d\n",
			cl, ctx_idx);
		ret = -ENXIO;
		goto exit_update_context;
	}

	pdata->active_only = active_only;

	msm_bus_dbg_client_data(client->pdata, ctx_idx, cl);
	ret = update_client_paths(client, false, ctx_idx);
	if (ret) {
		pr_err("%s: Err updating path\n", __func__);
		goto exit_update_context;
	}

	trace_bus_update_request_end(pdata->name);

exit_update_context:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
	return ret;
}

static int update_request_adhoc(uint32_t cl, unsigned int index)
{
	int ret = 0;
	struct msm_bus_scale_pdata *pdata;
	struct msm_bus_client *client;
	const char *test_cl = "Null";
	bool log_transaction = false;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		ret = -ENODEV;
		goto exit_update_request;
	}


	if (!cl || cl >= handle_list.num_entries) {
		MSM_BUS_ERR("%s: Invalid client handle %d", __func__, cl);
		ret = -ENXIO;
		goto exit_update_request;
	}

	client = handle_list.cl_list[cl];
	if (!client) {
		MSM_BUS_ERR("%s: Invalid client pointer ", __func__);
		ret = -ENXIO;
		goto exit_update_request;
	}

	pdata = client->pdata;
	if (!pdata) {
		MSM_BUS_ERR("%s: Client data Null.[client didn't register]",
				__func__);
		ret = -ENXIO;
		goto exit_update_request;
	}

	if (index >= pdata->num_usecases) {
		MSM_BUS_ERR("Client %u passed invalid index: %d\n",
			cl, index);
		ret = -ENXIO;
		goto exit_update_request;
	}

	if (client->curr == index) {
		ret = commit_data();
		MSM_BUS_DBG("%s: Not updating client request idx %d unchanged",
				__func__, index);
		goto exit_update_request;
	}

	if (!strcmp(test_cl, pdata->name))
		log_transaction = true;

	MSM_BUS_DBG("%s: cl: %u index: %d curr: %d num_paths: %d\n", __func__,
		cl, index, client->curr, client->pdata->usecase->num_paths);
	msm_bus_dbg_client_data(client->pdata, index, cl);
	ret = update_client_paths(client, log_transaction, index);
	if (ret) {
		pr_err("%s: Err updating path\n", __func__);
		goto exit_update_request;
	}

	trace_bus_update_request_end(pdata->name);

exit_update_request:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
	return ret;
}

static void free_cl_mem(struct msm_bus_client_handle *cl)
{
	if (cl) {
		put_device(cl->mas_dev);
		kfree(cl->name);
		kfree(cl);
		cl = NULL;
	}
}

static int update_bw_adhoc(struct msm_bus_client_handle *cl, u64 ab, u64 ib)
{
	int ret = 0;
	char *test_cl = "test-client";
	bool log_transaction = false;
	u64 slp_ib, slp_ab;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		ret = -ENODEV;
		goto exit_update_request;
	}


	if (!cl) {
		MSM_BUS_ERR("%s: Invalid client handle %p", __func__, cl);
		ret = -ENXIO;
		goto exit_update_request;
	}

	if (!strcmp(test_cl, cl->name))
		log_transaction = true;

	msm_bus_dbg_rec_transaction(cl, ab, ib);

	if ((cl->cur_act_ib == ib) && (cl->cur_act_ab == ab)) {
		ret = commit_data();
		MSM_BUS_DBG("%s:no change in request", cl->name);
		goto exit_update_request;
	}

	if (cl->active_only) {
		slp_ib = 0;
		slp_ab = 0;
	} else {
		slp_ib = ib;
		slp_ab = ab;
	}

	ret = update_path(cl->mas_dev, cl->slv, ib, ab, slp_ib, slp_ab,
		cl->cur_act_ib, cl->cur_act_ab, cl->first_hop, cl->active_only);

	if (ret) {
		MSM_BUS_ERR("%s: Update path failed! %d active_only %d\n",
				__func__, ret, cl->active_only);
		goto exit_update_request;
	}

	ret = commit_data();
	cl->cur_act_ib = ib;
	cl->cur_act_ab = ab;
	cl->cur_dual_ib = slp_ib;
	cl->cur_dual_ab = slp_ab;

	if (log_transaction)
		getpath_debug(cl->mas, cl->first_hop, cl->active_only);
	trace_bus_update_request_end(cl->name);
exit_update_request:
	rt_mutex_unlock(&msm_bus_adhoc_lock);

	return ret;
}

static int update_bw_context(struct msm_bus_client_handle *cl, u64 act_ab,
				u64 act_ib, u64 slp_ib, u64 slp_ab)
{
	int ret = 0;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		ret = -ENODEV;
		goto exit_change_context;
	}

	if (!cl) {
		MSM_BUS_ERR("Invalid client handle %p", cl);
		ret = -ENXIO;
		goto exit_change_context;
	}

	if ((cl->cur_act_ib == act_ib) &&
		(cl->cur_act_ab == act_ab) &&
		(cl->cur_dual_ib == slp_ib) &&
		(cl->cur_dual_ab == slp_ab)) {
		MSM_BUS_ERR("No change in vote");
		ret = commit_data();
		goto exit_change_context;
	}

	cl->active_only = !slp_ab && !slp_ib;
	msm_bus_dbg_rec_transaction(cl, cl->cur_act_ab, cl->cur_dual_ib);
	ret = update_path(cl->mas_dev, cl->slv, act_ib, act_ab, slp_ib,
				slp_ab, cl->cur_act_ib, cl->cur_act_ab,
				cl->first_hop, cl->active_only);
	if (ret) {
		MSM_BUS_ERR("%s: Update path failed! %d active_only %d\n",
				__func__, ret, cl->active_only);
		goto exit_change_context;
	}
	ret = commit_data();
	cl->cur_act_ib = act_ib;
	cl->cur_act_ab = act_ab;
	cl->cur_dual_ib = slp_ib;
	cl->cur_dual_ab = slp_ab;
	trace_bus_update_request_end(cl->name);
exit_change_context:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
	return ret;
}

static void unregister_adhoc(struct msm_bus_client_handle *cl)
{
	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		goto exit_unregister_client;
	}

	if (!cl) {
		MSM_BUS_ERR("%s: Null cl handle passed unregister\n",
				__func__);
		goto exit_unregister_client;
	}

	MSM_BUS_DBG("%s: Unregistering client %p", __func__, cl);

	remove_path(cl->mas_dev, cl->slv, cl->cur_act_ib, cl->cur_act_ab,
				cl->first_hop, cl->active_only);
	commit_data();
	msm_bus_dbg_remove_client(cl);
	put_device(cl->mas_dev);
	kfree(cl->name);
	kfree(cl);
exit_unregister_client:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
}

static struct msm_bus_client_handle*
register_adhoc(uint32_t mas, uint32_t slv, char *name, bool active_only)
{
	struct msm_bus_client_handle *client = NULL;
	int len = 0;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	if (!adhoc_online) {
		goto exit_register;
	}


	if (!(mas && slv && name)) {
		pr_err("%s: Error: src dst name num_paths are required\n",
								 __func__);
		goto exit_register;
	}

	client = kzalloc(sizeof(struct msm_bus_client_handle), GFP_KERNEL);
	if (!client) {
		MSM_BUS_ERR("%s: Error allocating client data", __func__);
		goto exit_register;
	}

	len = strnlen(name, MAX_STR_CL);
	client->name = kzalloc((len + 1), GFP_KERNEL);
	if (!client->name) {
		MSM_BUS_ERR("%s: Error allocating client name buf", __func__);
		free_cl_mem(client);
		client = NULL;
		goto exit_register;
	}
	strlcpy(client->name, name, len + 1);
	client->active_only = active_only;

	client->mas = mas;
	client->slv = slv;

	client->mas_dev = bus_find_device(&msm_bus_type, NULL,
					(void *) &mas,
					msm_bus_device_match_adhoc);
	if (IS_ERR_OR_NULL(client->mas_dev)) {
		MSM_BUS_ERR("%s:Failed to find path.src %d dest %d",
			__func__, client->mas, client->slv);
		free_cl_mem(client);
		client = NULL;
		goto exit_register;
	}

	client->first_hop = getpath(client->mas_dev, client->slv, client->name);
	if (client->first_hop < 0) {
		MSM_BUS_ERR("%s:Failed to find path.src %d dest %d",
			__func__, client->mas, client->slv);
		free_cl_mem(client);
		client = NULL;
		goto exit_register;
	}

	MSM_BUS_DBG("%s:Client handle %p %s", __func__, client,
						client->name);
	msm_bus_dbg_add_client(client);
exit_register:
	rt_mutex_unlock(&msm_bus_adhoc_lock);
	return client;
}
/**
 *  msm_bus_arb_setops_adhoc() : Setup the bus arbitration ops
 *  @ arb_ops: pointer to the arb ops.
 */
void msm_bus_arb_setops_adhoc(struct msm_bus_arb_ops *arb_ops)
{
	rt_mutex_lock(&msm_bus_adhoc_lock);
	adhoc_online = true;
	arb_ops->update_request = update_request_adhoc;
	arb_ops->unregister_client = unregister_client_adhoc;
	arb_ops->update_context = update_context;

	arb_ops->register_cl = register_adhoc;
	arb_ops->unregister = unregister_adhoc;
	arb_ops->update_bw = update_bw_adhoc;
	arb_ops->update_bw_context = update_bw_context;
	smp_store_release(&arb_ops->register_client, register_client_adhoc);
	rt_mutex_unlock(&msm_bus_adhoc_lock);
}

/* Drain callbacks before the fabric releases any graph devices. */
void msm_bus_arb_clearops_adhoc(struct msm_bus_arb_ops *ops)
{
	struct msm_bus_node_device_type *node, *tmp;

	rt_mutex_lock(&msm_bus_adhoc_lock);
	adhoc_online = false;
	smp_store_release(&ops->register_client, NULL);
	WRITE_ONCE(ops->update_request, NULL);
	WRITE_ONCE(ops->unregister_client, NULL);
	WRITE_ONCE(ops->update_context, NULL);
	WRITE_ONCE(ops->register_cl, NULL);
	WRITE_ONCE(ops->unregister, NULL);
	WRITE_ONCE(ops->update_bw, NULL);
	WRITE_ONCE(ops->update_bw_context, NULL);
	list_for_each_entry_safe(node, tmp, &commit_list, link) {
		list_del_init(&node->link);
		node->dirty = false;
	}
	rt_mutex_unlock(&msm_bus_adhoc_lock);
}
