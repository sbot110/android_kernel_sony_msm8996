// SPDX-License-Identifier: GPL-2.0
/*
 * Legacy ION userspace handles backed by real ION dma-bufs.
 *
 * A handle belongs to the /dev/ion file, not to the process fd table.
 * Keep this translation private: the native fd-based UAPI remains intact.
 */
#ifdef CONFIG_COMPAT
#include <linux/compat.h>
#endif
#include <linux/dma-buf.h>
#include <linux/file.h>
#include <linux/idr.h>
#include <linux/mm.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#include "ion.h"
#include "ion_legacy.h"

struct ion_legacy_handle {
	struct dma_buf *buffer;
	unsigned int refs;
};

struct ion_legacy_client {
	struct mutex lock;
	struct idr handles;
};

/* The old 32-bit ABI uses five 32-bit fields, even on an ARM64 kernel. */
struct ion_legacy_allocation32 {
	__u32 len;
	__u32 align;
	__u32 heap_id_mask;
	__u32 flags;
	__s32 handle;
};

int ion_legacy_open(struct inode *inode, struct file *file)
{
	struct ion_legacy_client *client;

	client = kzalloc(sizeof(*client), GFP_KERNEL);
	if (!client)
		return -ENOMEM;
	mutex_init(&client->lock);
	idr_init(&client->handles);
	file->private_data = client;
	return nonseekable_open(inode, file);
}

int ion_legacy_release(struct inode *inode, struct file *file)
{
	struct ion_legacy_client *client = file->private_data;
	struct ion_legacy_handle *handle;
	int id;

	/* VFS has released all active file references before ->release. */
	idr_for_each_entry(&client->handles, handle, id) {
		dma_buf_put(handle->buffer);
		kfree(handle);
	}
	idr_destroy(&client->handles);
	mutex_destroy(&client->lock);
	kfree(client);
	return 0;
}

static int ion_legacy_add(struct ion_legacy_client *client,
			  struct dma_buf *buffer)
{
	struct ion_legacy_handle *handle;
	int id;

	handle = kzalloc(sizeof(*handle), GFP_KERNEL);
	if (!handle)
		return -ENOMEM;
	handle->buffer = buffer;
	handle->refs = 1;
	id = idr_alloc(&client->handles, handle, 1, 0, GFP_KERNEL);
	if (id < 0)
		kfree(handle);
	return id;
}

static int ion_legacy_free(struct ion_legacy_client *client, int id)
{
	struct ion_legacy_handle *handle;

	if (id <= 0)
		return -EINVAL;
	handle = idr_find(&client->handles, id);
	if (!handle)
		return -EINVAL;
	if (!--handle->refs) {
		idr_remove(&client->handles, id);
		dma_buf_put(handle->buffer);
		kfree(handle);
	}
	return 0;
}

static long ion_legacy_alloc(struct file *file, void __user *arg, bool compat)
{
	struct ion_legacy_client *client = file->private_data;
	struct ion_old_allocation_data data;
	struct ion_legacy_allocation32 data32;
	struct dma_buf *buffer;
	int id, ret;

	if (compat) {
		if (copy_from_user(&data32, arg, sizeof(data32)))
			return -EFAULT;
		memset(&data, 0, sizeof(data));
		data.len = data32.len;
		data.align = data32.align;
		data.heap_id_mask = data32.heap_id_mask;
		data.flags = data32.flags;
	} else if (copy_from_user(&data, arg, sizeof(data))) {
		return -EFAULT;
	}

	/* This allocator guarantees PAGE_SIZE alignment, not larger requests. */
	if (data.align > PAGE_SIZE ||
	    (data.align && !is_power_of_2(data.align)))
		return -EINVAL;
	if (!data.len || data.len > SIZE_MAX - (PAGE_SIZE - 1))
		return -EINVAL;

	buffer = ion_alloc_dmabuf(data.len, data.heap_id_mask, data.flags);
	if (IS_ERR(buffer))
		return PTR_ERR(buffer);

	mutex_lock(&client->lock);
	id = ion_legacy_add(client, buffer);
	if (id < 0) {
		mutex_unlock(&client->lock);
		dma_buf_put(buffer);
		return id;
	}
	if (compat) {
		data32.handle = id;
		ret = copy_to_user(arg, &data32, sizeof(data32));
	} else {
		data.handle = id;
		ret = copy_to_user(arg, &data, sizeof(data));
	}
	if (ret)
		ion_legacy_free(client, id);
	mutex_unlock(&client->lock);
	return ret ? -EFAULT : 0;
}

bool ion_legacy_cmd(unsigned int cmd)
{
	switch (cmd) {
	case ION_OLD_IOC_ALLOC:
	case ION_IOC_FREE:
	case ION_IOC_SHARE:
	case ION_IOC_MAP:
	case ION_IOC_IMPORT:
	case ION_IOC_SYNC:
		return true;
	default:
		return false;
	}
}

#ifdef CONFIG_COMPAT
long ion_legacy_compat_alloc(struct file *file, unsigned long arg)
{
	BUILD_BUG_ON(sizeof(struct ion_legacy_allocation32) != 20);
	return ion_legacy_alloc(file, compat_ptr(arg), true);
}
#endif

long ion_legacy_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct ion_legacy_client *client = file->private_data;
	void __user *user = (void __user *)arg;
	struct ion_legacy_handle *handle;
	struct ion_handle_data free_data;
	struct ion_fd_data data;
	struct dma_buf *buffer;
	int id, fd, ret = 0;

	if (cmd == ION_OLD_IOC_ALLOC)
		return ion_legacy_alloc(file, user, false);
	if (cmd == ION_IOC_FREE) {
		if (copy_from_user(&free_data, user, sizeof(free_data)))
			return -EFAULT;
		mutex_lock(&client->lock);
		ret = ion_legacy_free(client, free_data.handle);
		mutex_unlock(&client->lock);
		return ret;
	}
	if (copy_from_user(&data, user, sizeof(data)))
		return -EFAULT;
	if (cmd == ION_IOC_SYNC) {
		buffer = dma_buf_get(data.fd);
		if (IS_ERR(buffer))
			return PTR_ERR(buffer);
		if (!ion_is_ion_dma_buf(buffer))
			ret = -EINVAL;
		else
			ret = dma_buf_end_cpu_access(buffer, DMA_BIDIRECTIONAL);
		dma_buf_put(buffer);
		return ret;
	}

	mutex_lock(&client->lock);
	switch (cmd) {
	case ION_IOC_SHARE:
	case ION_IOC_MAP:
		handle = data.handle > 0 ?
			idr_find(&client->handles, data.handle) : NULL;
		if (!handle) {
			ret = -EINVAL;
			break;
		}
		fd = get_unused_fd_flags(O_CLOEXEC);
		if (fd < 0) {
			ret = fd;
			break;
		}
		buffer = handle->buffer;
		get_dma_buf(buffer);
		data.fd = fd;
		if (copy_to_user(user, &data, sizeof(data))) {
			put_unused_fd(fd);
			dma_buf_put(buffer);
			ret = -EFAULT;
		} else {
			/* Publish only after copy succeeds: no hidden fd on EFAULT. */
			fd_install(fd, buffer->file);
		}
		break;
	case ION_IOC_IMPORT:
		buffer = dma_buf_get(data.fd);
		if (IS_ERR(buffer)) {
			ret = PTR_ERR(buffer);
			break;
		}
		if (!ion_is_ion_dma_buf(buffer)) {
			dma_buf_put(buffer);
			ret = -EINVAL;
			break;
		}
		/* As in old ION, repeated imports share a refcounted handle. */
		idr_for_each_entry(&client->handles, handle, id) {
			if (handle->buffer == buffer)
				goto existing;
		}
		id = ion_legacy_add(client, buffer);
		if (id < 0) {
			dma_buf_put(buffer);
			ret = id;
			break;
		}
		goto copy_import;
existing:
		if (handle->refs == UINT_MAX) {
			dma_buf_put(buffer);
			ret = -EOVERFLOW;
			break;
		}
		handle->refs++;
		dma_buf_put(buffer);
copy_import:
		data.handle = id;
		if (copy_to_user(user, &data, sizeof(data))) {
			ion_legacy_free(client, id);
			ret = -EFAULT;
		}
		break;
	default:
		ret = -ENOTTY;
	}
	mutex_unlock(&client->lock);
	return ret;
}
