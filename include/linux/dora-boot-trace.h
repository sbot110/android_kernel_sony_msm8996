/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_DORA_BOOT_TRACE_H
#define _LINUX_DORA_BOOT_TRACE_H

#include <linux/types.h>

/* Stable, independently checksummed slots, not a persistent memory format. */
enum dora_boot_stage {
	DORA_BOOT_START = 1,
	DORA_BOOT_SETUP_ARCH,
	DORA_BOOT_FDT_VALID,
	DORA_BOOT_MEMBLOCK,
	DORA_BOOT_PAGING_BEGIN,
	DORA_BOOT_PAGING_DONE,
	DORA_BOOT_PSCI_DONE,
	DORA_BOOT_ARCH_DONE,
	DORA_BOOT_IRQ_BEGIN,
	DORA_BOOT_IRQ_DONE,
	DORA_BOOT_TIMER_BEGIN,
	DORA_BOOT_TIMER_DONE,
	DORA_BOOT_IRQS_ON,
	DORA_BOOT_KERNEL_INIT,
	DORA_BOOT_SMP_BEGIN,
	DORA_BOOT_SMP_DONE,
	DORA_BOOT_INITCALL_BEGIN,
	DORA_BOOT_INITCALL_DONE,
	DORA_BOOT_EXEC_INIT,
	DORA_BOOT_EXEC_RESULT,
	DORA_BOOT_FDT_REJECTED,
	DORA_BOOT_FDT_HEADER,
	DORA_BOOT_SLOTS = 32,
};

#ifdef CONFIG_DORA_BOOT_TRACE
void dora_boot_start(void);
void dora_boot_mark(enum dora_boot_stage stage, u64 a, u64 b, u64 c, u64 d);
#else
static inline void dora_boot_start(void) { }
static inline void dora_boot_mark(enum dora_boot_stage stage,
				  u64 a, u64 b, u64 c, u64 d) { }
#endif

#endif
