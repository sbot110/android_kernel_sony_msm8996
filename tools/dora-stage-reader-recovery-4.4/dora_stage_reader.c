// SPDX-License-Identifier: GPL-2.0
/*
 * Read-only snapshot of the fixed 4 KiB Sony Dora early marker page.
 * Intended ONLY for an exact kernel-ABI-matched TWRP Linux 4.4 build.
 * The recovery ramoops backend may have overwritten the markers already.
 */
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#define DORA_PHYS ((phys_addr_t)0xa7fff000ULL)
#define DORA_BYTES 4096
static u8 *dora_snapshot;
static struct proc_dir_entry *dora_proc;

static ssize_t dora_read(struct file *file, char __user *buf,
			 size_t count, loff_t *pos)
{
	return simple_read_from_buffer(buf, count, pos, dora_snapshot, DORA_BYTES);
}

static const struct file_operations dora_fops = {
	.owner = THIS_MODULE,
	.read = dora_read,
	.llseek = default_llseek,
};

static int __init dora_init(void)
{
	void __iomem *mapped;

	BUILD_BUG_ON(PAGE_SIZE != DORA_BYTES);
	dora_snapshot = kmalloc(DORA_BYTES, GFP_KERNEL);
	if (!dora_snapshot)
		return -ENOMEM;
	mapped = ioremap_wc(DORA_PHYS, DORA_BYTES);
	if (!mapped) {
		kfree(dora_snapshot);
		dora_snapshot = NULL;
		return -ENOMEM;
	}
	memcpy_fromio(dora_snapshot, mapped, DORA_BYTES);
	iounmap(mapped);
	dora_proc = proc_create("dora_stage_raw", 0400, NULL, &dora_fops);
	if (!dora_proc) {
		kfree(dora_snapshot);
		dora_snapshot = NULL;
		return -ENOMEM;
	}
	pr_info("dora_stage_reader: snapshot captured from 0x%llx\n",
		(unsigned long long)DORA_PHYS);
	return 0;
}

static void __exit dora_exit(void)
{
	if (dora_proc)
		remove_proc_entry("dora_stage_raw", NULL);
	kfree(dora_snapshot);
}

module_init(dora_init);
module_exit(dora_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Sony Dora fixed 4KiB read-only boot stage snapshot (4.4 recovery)");
