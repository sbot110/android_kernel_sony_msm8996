# Dora: проверка интерфейсов LineageOS 21 / Android 14

Дата: 2026-11-01. Это аудит исходников и разрешённой конфигурации, а не
подтверждение запуска Android. F8131 и F8132 требуют отдельных тестов.

## Исправления конфигурации

`tone_dora_defconfig` теперь явно включает `BPF_JIT_ALWAYS_ON`: ARM64 JIT
присутствует в `arch/arm64/net/bpf_jit_comp.c`, а `HAVE_EBPF_JIT=y` разрешён
Kconfig. Ранее `BPF_JIT=y` только компилировал JIT, но начальное значение
`bpf_jit_enable` в `kernel/bpf/core.c` оставалось 0 до записи userspace.
Теперь JIT включён с начала работы; interpreter fallback отключён штатным
кодом ядра. Это не изменение verifier и не разрешение запрещённых программ.

Явно сохранены `PSI_DEFAULT_DISABLED=n` (lmkd получает PSI без обязательного
добавления `psi=1`) и `SECURITY_SELINUX_DISABLE=n`. SELinux остаётся default
LSM. Возможность permissive policy в userdebug сама по себе не является
подтверждением enforcing: окончательные cmdline/init/policy проверяются
отдельно в Android.

`scripts/dora/check-config.py` проверяет 87 обязательных built-ins по итоговой
`.config`, включая скрытые зависимости, а также стандартный ARM64 Binder
ABI, default SELinux и отсутствие отключения PSI. Наличие имён в defconfig
больше не является единственным свидетельством для memfd/ashmem, cgroup BPF,
fscrypt, loop, OverlayFS, ION/fences и configfs USB.

Команда генерации `make O=/workspace/work/dora-verified-out ARCH=arm64
CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_COMPAT=arm-linux-gnueabi-
tone_dora_defconfig` завершилась с кодом 0. Проверка разрешённой `.config`
завершилась с кодом 0: 277 explicit assignments, 87 required built-ins.
Полный результат компиляции записывается отдельно в dora-build-validation.txt.

## Реализация интерфейсов

| Интерфейс | Проверенная реализация / зависимость | Предел проверки |
| --- | --- | --- |
| Binder / BinderFS | `drivers/android/binder.c`, `binderfs.c`; обе цели встроены, устройства binder/hwbinder/vndbinder | Запуск servicemanager/hwservicemanager и старых 32-bit vendor клиентов не выполнялся |
| BPF syscall / JIT / cgroup | `kernel/bpf/syscall.c`, ARM64 JIT, `kernel/bpf/cgroup.c`; CGROUP_BPF выбирает SOCK_CGROUP_DATA | Нужны реальные ELF из установленного Tethering APEX и verifier logs |
| bpffs | `kernel/bpf/Makefile` включает `inode.o` при BPF_SYSCALL; отдельного CONFIG_BPF_FS в этой базе нет | Нужны mount, SELinux labels и pinning в `/sys/fs/bpf` |
| PSI / cgroups | `kernel/sched/psi.c`; cpu/memory/pids/device/freezer/cpuset/accounting controllers встроены | Нужны lmkd, task_profiles и проверка конкретного cgroups.json |
| seccomp / namespaces | SECCOMP_FILTER разрешён ARM64+SECCOMP+NET; UTS/IPC/PID/NET namespaces встроены | Это не утверждение о наличии всех необязательных namespace типов |
| memfd / ashmem | `mm/memfd.c`, `drivers/staging/android/ashmem.c`; MEMFD_CREATE выбирается TMPFS | Нужны bionic/shared-memory и vendor ashmem ioctl smoke tests |
| fscrypt | `fs/crypto/keyring.c` и `policy.c`; v2, ADD_ENCRYPTION_KEY и GET_ENCRYPTION_POLICY_EX подключены в ext4/f2fs ioctl handlers | Keymaster/TEE, существующий формат userdata и конкретная схема vold не проверены |
| ФС / device mapper | ext4/F2FS security labels и encryption, quota, dm-crypt, dm-verity/FEC, loop, OverlayFS встроены | Mount/verity/FEC, APEX и сохранность данных требуют Android и устройства |
| ION / dma-buf / fences | Реальные legacy IDR handles, dma-buf и SYNC_FILE | CUSTOM/cache-range, secure heaps, vendor EGL/gralloc ещё не подтверждены |
| USB / Android PM | gadget/configfs/FunctionFS/MTP/PTP/accessory/RNDIS, autosleep/wakelocks | PHY/DPDM/role switching и ADB на устройстве ещё не подтверждены |

## bpfloader и NetBpfLoad: точные границы вывода

Прочитаны снимки LineageOS 21:

- `android_system_bpf`:
  [d5e515c0085b354fb8f00ef3feb09ec0f0b1f60b](https://github.com/LineageOS/android_system_bpf/tree/d5e515c0085b354fb8f00ef3feb09ec0f0b1f60b),
  `bpfloader/BpfLoader.cpp`, `libbpf_android/Loader.cpp`.
- `android_packages_modules_Connectivity`:
  [965d387f90f22d20c3cba4dc90fd900f2e1b7ed0](https://github.com/LineageOS/android_packages_modules_Connectivity/tree/965d387f90f22d20c3cba4dc90fd900f2e1b7ed0),
  `netbpfload/NetBpfLoad.cpp`, `netbpfload/loader.cpp`, `bpf_progs/`.

NetBpfLoad при Android U записывает 1 в `net/core/bpf_jit_enable` и
`net/core/bpf_jit_kallsyms`. На 4.14+ ошибка этих записей приводит к коду 1.
В данной базе оба sysctl имеют настоящие handlers; для ALWAYS_ON допустимое
значение jit_enable равно 1. Но SELinux, procfs/sysctl mount и полномочия
процесса всё ещё могут запретить запись. Включение Kconfig этого не доказывает.
Запись `kernel/unprivileged_bpf_disabled=0` проверяется загрузчиком иначе:
её ошибка считается fatal только начиная с 5.13. Политика этого sysctl
в данном изменении не ослабляется и не подменяется.

В platform BpfLoader код 1 также возвращается при ошибке создания pin
directories или установки `bpf.progs_loaded`; критическая ошибка загрузки
ELF в прочитанной версии возвращает 2. Поэтому старое сообщение
«bpfloader exited with status 1» без версии binary и предшествующих логов
не позволяет назначить причину. У NetBpfLoad есть дополнительные ошибки
выбора platform/APEX и init scripts, относящиеся к userspace.

У обязательных Connectivity programs проверены используемые API:
`bpf_skb_change_head` доступен TC programs в `net/core/filter.c`,
`bpf_ktime_get_boot_ns` реализован, cgroup bind4/bind6 attach types существуют.
Наличие helper не доказывает принятие всего ELF verifier: нужны реальные
объекты с соответствующими min/max kernel version и tests на ARM64.

Остаётся конкретный разрыв: ядро не реализует program-side map flags
`BPF_F_RDONLY_PROG`/`BPF_F_WRONLY_PROG`; Loader ожидает implicit RDONLY_PROG
для DEVMAP. Однако stock `offload.c` использует DEVMAP_HASH, который на
4.19 загрузчик преобразует в HASH и проверяет как HASH. Поэтому этот разрыв
не объявлен причиной старой ошибки загрузки и не исправляется добавлением
неподдержанных флагов только в UAPI. Для реального DEVMAP клиента потребуется
полный корректный backport семантики и syscall/verifier тесты.

## boringssl-self-check-failed и vold-failed

`boringssl-self-check-failed` относится к проверке userspace crypto. Для
диагноза нужны конкретный failing executable, tombstone, dynamic linker
errors, результат getrandom и auxv/HWCAP. Наличие CONFIG_CRYPTO_AES/SHA256
не исправляет userspace self-test BoringSSL. Проверка подлинности executable,
согласованности APEX/system/vendor и CPU instruction ABI обязательна;
отключение self-test не является исправлением.

`vold-failed` требует первого сообщения об ошибке vold/fs_mgr и errno.
Проверены реальные fscrypt v2/ioctl implementations, встроенные ФС, quota,
loop и device mapper. Но причиной всё ещё могут быть неподдерживаемые
fstab options, старый disk layout, недоступный UFS, TEE/keymaster ABI,
SELinux policy или несовместимая версия userspace. Внешний tone-common
fstab не задаёт fileencryption/metadata_encryption для userdata, поэтому
его нельзя считать проверенной конфигурацией современного LOS21 FBE.
Никакая миграция или форматирование userdata этим аудитом не разрешается.

## Внешняя Android-конфигурация: изменения не применены

Прочитан `android_device_sony_tone-common` main,
[2701d2aecab52b30c3672ecebbad6ae1910cc212](https://github.com/sbot110/android_device_sony_tone-common/tree/2701d2aecab52b30c3672ecebbad6ae1910cc212).
BoardConfigCommon.mk принудительно добавляет `androidboot.selinux=permissive`.
Приложен точный патч
`patches/tone-common-enforcing-cmdline.patch`; он **не применён** к device tree
и требует отдельного разрешения на изменение внешнего репозитория.
Удаление параметра не доказывает полноту policy и не заменяет сборку LOS21.
В том же cmdline остаётся `lpm_levels.sleep_disabled=1`. Наличие SUSPEND=y
не доказывает deep sleep с такой внешней конфигурацией. Параметр не удалён
вслепую: сначала нужны реальные CPU/CPR/SAW/power-domain связи и испытания.

Там же указан XZ ramdisk. Поэтому проверка требует итоговый `RD_XZ=y`,
а не только BLK_DEV_INITRD. Сохранены исходные boot parameters из документации;
boot.img без настоящего Android ramdisk и проверки Sony header/DT selection
не создаётся. Полного исходного checkout LineageOS 21 в окружении нет.

Для каждого аппарата при разрешённом будущем тесте загрузки нужны kernel
log/pstore, init/SELinux AVC, bpfloader/NetBpfLoad verifier logs, mountinfo,
vold/fs_mgr и BoringSSL tombstones. SELinux, dm-verity и crypto checks
не отключаются для сокрытия ошибки.
