// SPDX-License-Identifier: GPL-2.0
/*
 * Read-only early boot breadcrumb exporter for Sony Xperia X Performance.
 *
 * Build against the exact 3.18.71-sjll TWRP kernel + matching .config and
 * Module.symvers, not against the experimental Linux 5.15 kernel.
 *
 * The snapshot is taken ONCE when the module loads. Nothing is written
 * to ramoops or to the reserved physical RAM page.
 */
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/io.h>
#include <linux/slab.h>
#include <linux/fs.h>
#include <linux/proc_fs.h>

#define DORA_STAGE_PHYS  ((phys_addr_t)0xa7fff000ULL)
#define DORA_STAGE_BYTES 4096
#define DORA_PROC_NAME   "dora_stage_raw"

static u8 *dora_snapshot;
static struct proc_dir_entry *dora_proc;

static ssize_t dora_stage_read(struct file *file, char __user *buf,
			       size_t count, loff_t *pos)
{
	return simple_read_from_buffer(buf, count, pos, dora_snapshot,
				       DORA_STAGE_BYTES);
}

static const struct file_operations dora_stage_fops = {
	.owner = THIS_MODULE,
	.read = dora_stage_read,
	.llseek = default_llseek,
};

static int __init dora_stage_init(void)
{
	void __iomem *mapped;

	dora_snapshot = kmalloc(DORA_STAGE_BYTES, GFP_KERNEL);
	if (!dora_snapshot)
		return -ENOMEM;

	/*
	 * The recovery ramoops backend maps this physical region using
	 * ioremap_wc() (mem_type=0); use the same memory attribute for
	 * our read-only snapshot. No /dev/mem or generic physical-read API.
	 */
	mapped = ioremap_wc(DORA_STAGE_PHYS, DORA_STAGE_BYTES);
	if (!mapped) {
		kfree(dora_snapshot);
		dora_snapshot = NULL;
		return -ENOMEM;
	}
	memcpy_fromio(dora_snapshot, mapped, DORA_STAGE_BYTES);
	iounmap(mapped);

	dora_proc = proc_create(DORA_PROC_NAME, 0400, NULL,
				&dora_stage_fops);
	if (!dora_proc) {
		kfree(dora_snapshot);
		dora_snapshot = NULL;
		return -ENOMEM;
	}

	pr_info("dora_stage_reader: snapshot saved from 0x%llx (4096 bytes)\n",
		(unsigned long long)DORA_STAGE_PHYS);
	return 0;
}

static void __exit dora_stage_exit(void)
{
	if (dora_proc) {
		remove_proc_entry(DORA_PROC_NAME, NULL);
		dora_proc = NULL;
	}
	kfree(dora_snapshot);
	dora_snapshot = NULL;
}

module_init(dora_stage_init);
module_exit(dora_stage_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Dora bring-up diagnostics");
MODULE_DESCRIPTION("Read-only one-page snapshot of Sony dora early-boot markers");
