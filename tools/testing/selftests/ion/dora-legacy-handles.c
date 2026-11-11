// SPDX-License-Identifier: GPL-2.0
/* Device-side smoke test of the public pre-4.12 ION handle ABI. */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

struct old_alloc {
	size_t len, align;
	unsigned int heap_mask, flags;
	int handle;
};
struct handle_arg { int handle; };
struct fd_arg { int handle, fd; };
#define OLD_ALLOC _IOWR('I', 0, struct old_alloc)
#define OLD_FREE _IOWR('I', 1, struct handle_arg)
#define OLD_SHARE _IOWR('I', 4, struct fd_arg)
#define OLD_IMPORT _IOWR('I', 5, struct fd_arg)
#define OLD_SYNC _IOWR('I', 7, struct fd_arg)

_Static_assert(sizeof(struct old_alloc) == (sizeof(size_t) == 8 ? 32 : 20),
	       "legacy allocation ABI size");
_Static_assert(sizeof(struct fd_arg) == 8, "legacy fd ABI size");

int main(int argc, char **argv)
{
	int ion = -1, second = -1, shared = -1, rc = 1;
	long page = sysconf(_SC_PAGESIZE);
	unsigned char *map = MAP_FAILED;
	struct old_alloc alloc = { .len = page, .align = page,
		.heap_mask = argc > 1 ? strtoul(argv[1], NULL, 0) : 1U << 25 };
	struct fd_arg share = { .fd = -1 }, first, again, other;
	struct handle_arg free_arg;
	void *readonly = MAP_FAILED;

	ion = open("/dev/ion", O_RDWR | O_CLOEXEC);
	if (ion < 0 && (errno == ENOENT || errno == ENODEV)) {
		puts("SKIP: no /dev/ion; no runtime handle test was performed");
		return 77;
	}
#define CHECK(condition, label) do { \
	if (!(condition)) { perror(label); goto out; } \
} while (0)
	CHECK(ion >= 0, "open ion");
	CHECK(ioctl(ion, OLD_ALLOC, &alloc) == 0, "legacy alloc / heap DT");
	share.handle = alloc.handle;
	CHECK(ioctl(ion, OLD_SHARE, &share) == 0, "share");
	shared = share.fd;
	CHECK(shared >= 0 && shared != ion, "independent shared fd");
	map = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_SHARED, shared, 0);
	CHECK(map != MAP_FAILED, "mmap dma-buf");
	map[0] = 0x5a;
	first.fd = again.fd = shared;
	CHECK(ioctl(ion, OLD_IMPORT, &first) == 0, "first import");
	CHECK(ioctl(ion, OLD_IMPORT, &again) == 0, "repeated import");
	CHECK(first.handle == alloc.handle && again.handle == alloc.handle,
	      "imports must reference the existing per-open handle");
	second = open("/dev/ion", O_RDWR | O_CLOEXEC);
	CHECK(second >= 0, "second client");
	other.fd = shared;
	CHECK(ioctl(second, OLD_IMPORT, &other) == 0, "second-client import");
	close(second);
	second = -1;
	free_arg.handle = alloc.handle;
	CHECK(ioctl(ion, OLD_FREE, &free_arg) == 0, "free alloc ref");
	CHECK(ioctl(ion, OLD_FREE, &free_arg) == 0, "free import ref");
	CHECK(ioctl(ion, OLD_FREE, &free_arg) == 0, "free last handle ref");
	errno = 0;
	CHECK(ioctl(ion, OLD_FREE, &free_arg) == -1 && errno == EINVAL,
	      "freed handle must be rejected");
	CHECK(map[0] == 0x5a, "share fd must outlive all handles");
	CHECK(ioctl(ion, OLD_SYNC, &share) == 0, "real dma-buf CPU sync");

	/* Readable input, unwritable output: allocation must roll back. */
	readonly = mmap(NULL, page, PROT_READ | PROT_WRITE,
			MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	CHECK(readonly != MAP_FAILED, "readonly argument allocation");
	*(struct old_alloc *)readonly = alloc;
	CHECK(mprotect(readonly, page, PROT_READ) == 0, "mprotect");
	errno = 0;
	CHECK(ioctl(ion, OLD_ALLOC, readonly) == -1 && errno == EFAULT,
	      "allocation copy-out failure");
	CHECK(ioctl(ion, OLD_ALLOC, &alloc) == 0, "allocation after EFAULT");
	CHECK(alloc.handle == free_arg.handle, "EFAULT must not leak IDR handle");
	free_arg.handle = alloc.handle;
	CHECK(ioctl(ion, OLD_FREE, &free_arg) == 0, "final free");
	puts("PASS: alloc/free/share/import, per-open lifetime and EFAULT rollback");
	rc = 0;
out:
	if (readonly != MAP_FAILED) munmap(readonly, page);
	if (map != MAP_FAILED) munmap(map, page);
	if (shared >= 0) close(shared);
	if (second >= 0) close(second);
	if (ion >= 0) close(ion);
	return rc;
}
