/*
 * drivers/staging/android/ion/compat_ion.c
 *
 * Copyright (C) 2013 Google, Inc.
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 */

#include <linux/compat.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

#include "ion.h"
#include "compat_ion.h"
#include "ion_legacy.h"

/* Old allocation contains size_t, so compat differs from native ARM64.
 * Other supported legacy commands use fixed-width handles/fds.
 */
struct compat_ion_old_allocation_data {
	compat_size_t len;
	compat_size_t align;
	compat_uint_t heap_id_mask;
	compat_uint_t flags;
	compat_int_t handle;
};

#define COMPAT_ION_IOC_ALLOC _IOWR(ION_IOC_MAGIC, 0, \
				struct compat_ion_old_allocation_data)

long compat_ion_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
	if (cmd == COMPAT_ION_IOC_ALLOC)
		return ion_legacy_compat_alloc(filp, arg);
	return ion_ioctl(filp, cmd, (unsigned long)compat_ptr(arg));
}
