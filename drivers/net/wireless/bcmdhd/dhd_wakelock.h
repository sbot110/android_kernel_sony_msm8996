/* SPDX-License-Identifier: GPL-2.0 */
#ifndef DHD_WAKELOCK_H
#define DHD_WAKELOCK_H

#include <linux/device.h>
#include <linux/errno.h>
#include <linux/jiffies.h>
#include <linux/pm_wakeup.h>

/* Driver-private translation of the retired Android wakelock interface.
 * Registration can fail; callers must unwind before exposing the device.
 */
enum { WAKE_LOCK_SUSPEND };

struct wake_lock {
	struct wakeup_source *ws;
};

static inline int wake_lock_init(struct wake_lock *lock, int type,
				 const char *name)
{
	lock->ws = wakeup_source_register(NULL, name);
	return lock->ws ? 0 : -ENOMEM;
}

static inline void wake_lock_destroy(struct wake_lock *lock)
{
	wakeup_source_unregister(lock->ws);
	lock->ws = NULL;
}

static inline void wake_lock(struct wake_lock *lock)
{
	__pm_stay_awake(lock->ws);
}

static inline void wake_unlock(struct wake_lock *lock)
{
	__pm_relax(lock->ws);
}

static inline void wake_lock_timeout(struct wake_lock *lock, long timeout)
{
	__pm_wakeup_event(lock->ws, jiffies_to_msecs(timeout));
}

static inline int wake_lock_active(struct wake_lock *lock)
{
	unsigned long flags;
	bool active;

	if (!lock->ws)
		return 0;
	spin_lock_irqsave(&lock->ws->lock, flags);
	active = lock->ws->active;
	spin_unlock_irqrestore(&lock->ws->lock, flags);
	return active;
}

#endif
