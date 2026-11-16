// SPDX-License-Identifier: GPL-2.0-only
/*
 * Volatile milestones in the kernel's own Image allocation. No fixed physical
 * address, reserved-memory allocation, device MMIO or firmware call is used.
 * The existing head.S boot_args are sampled only after the MMU and BSS setup.
 * These records cannot survive a reload and do not solve TWRP persistence.
 */
#include <linux/crc32.h>
#include <linux/dora-boot-trace.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/sched.h>
#include <linux/sched/task.h>
#include <linux/string.h>

#define DORA_BOOT_MAGIC 0x31425244 /* DRB1, little endian */

struct dora_boot_record {
	__le32 magic;
	__le16 version;
	__le16 bytes;
	__le64 sequence;
	__le32 stage;
	__le32 checksum;
	__le64 args[4];
	__le64 committed;
};

/* An initializer puts the records in file-backed .data, owned by the Image.
 * The symbol can also be located in System.map for an authorized live dump.
 * A new kernel load replaces this data; it is never a reboot breadcrumb.
 */
struct dora_boot_record dora_boot_records[DORA_BOOT_SLOTS] __aligned(64) = {
	[0] = { .magic = cpu_to_le32(DORA_BOOT_MAGIC) },
};
static u64 dora_boot_sequence;
extern u64 boot_args[4];

void notrace dora_boot_mark(enum dora_boot_stage stage, u64 a, u64 b, u64 c, u64 d)
{
	struct dora_boot_record record = { };
	struct dora_boot_record *target;
	u64 sequence;

	/* One boot-task writer, then PID 1. Concurrent module/workqueue calls
	 * cannot overwrite initcall evidence. No lock/percpu/timer is needed.
	 */
	if (stage <= 0 || stage >= DORA_BOOT_SLOTS ||
	    (system_state >= SYSTEM_RUNNING && stage != DORA_BOOT_EXEC_INIT &&
	     stage != DORA_BOOT_EXEC_RESULT) ||
	    (current != &init_task && current->pid != 1))
		return;
	BUILD_BUG_ON(sizeof(record) != 64);
	sequence = ++dora_boot_sequence;
	record.magic = cpu_to_le32(DORA_BOOT_MAGIC);
	record.version = cpu_to_le16(1);
	record.bytes = cpu_to_le16(sizeof(record));
	record.sequence = cpu_to_le64(sequence);
	record.stage = cpu_to_le32(stage);
	record.args[0] = cpu_to_le64(a);
	record.args[1] = cpu_to_le64(b);
	record.args[2] = cpu_to_le64(c);
	record.args[3] = cpu_to_le64(d);
	record.checksum = cpu_to_le32(crc32_le(~0U, (const u8 *)&record,
					 offsetof(struct dora_boot_record, committed)) ^ ~0U);
	target = &dora_boot_records[stage];
	WRITE_ONCE(target->committed, 0);
	smp_wmb();
	memcpy(target, &record, offsetof(struct dora_boot_record, committed));
	smp_wmb();
	WRITE_ONCE(target->committed, cpu_to_le64(~sequence));
}

void notrace dora_boot_start(void)
{
	dora_boot_mark(DORA_BOOT_START, boot_args[0], boot_args[1],
		       boot_args[2], boot_args[3]);
}

static ssize_t dora_boot_read(struct file *file, char __user *buf,
			      size_t bytes, loff_t *offset)
{
	return simple_read_from_buffer(buf, bytes, offset, dora_boot_records,
				       sizeof(dora_boot_records));
}

static const struct file_operations dora_boot_fops = {
	.read = dora_boot_read,
	.llseek = default_llseek,
};

static int __init dora_boot_trace_init(void)
{
	/* Raw register/PC values are restricted to root. A reader must verify
	 * each checksum/commit: an initcall may advance during a live read.
	 */
	return proc_create("dora_boot_trace", 0400, NULL, &dora_boot_fops) ? 0 : -ENOMEM;
}
late_initcall(dora_boot_trace_init);
