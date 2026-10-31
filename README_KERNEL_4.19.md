# Dora: аудит подготовки к Linux 4.19 / LineageOS 21

Дата проверки: 2026-10-31. Устройство: Sony Xperia X Performance,
dora / tone / Qualcomm MSM8996, F8131/F8132.

## Фактический результат

**Эта ветка всё ещё содержит Linux 4.4.302. Порт Linux 4.19 отсутствует.**
Название ветки и build.config не меняют версию или API ядра.
Совместимость с Android 14, успешная сборка образа и загрузка телефона
не подтверждены.

Все изменения этой проверки сохранены непосредственно в существующей ветке
kernel-4.19-701905467803253630. Другие ветки не изменялись,
новые ветки и Pull Request не создавались.

## История и доказательства

Исходная точка проверки: a27a0c849e2cf3f67fd25889cc621690153116c8.
Её родитель: 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1
("dora: synchronize LineageOS 20 working build").
Ранее: 0b21bd84, первоначальный импорт.

Сравнение a27a0c8 с 0c295b15 через GitHub compare показывает ровно один
коммит и три изменённых файла:
README_KERNEL_4.19.md, arch/arm64/configs/tone_dora_defconfig,
build.config.common. Переноса исходников ядра или драйверов не было.
Makefile в a27a0c8 содержит VERSION=4, PATCHLEVEL=4, SUBLEVEL=302.
Все остальные файлы совпадают с указанным эталоном 4.4.
Название коммита эталона не заменяет протокол реального тестирования.

Предыдущая редакция этого README ошибочно заявляла о готовности функций
4.19 и предлагала работу в других ветках. Эти указания заменены этим отчётом.

## Подтверждённые исправления

Коммит 2fa6b24ba5dc89ae49df550632a4ba44ea0bfb75:

- Удалены отсутствующие в Kconfig PSI, PSI_DEFAULT_DISABLED, FUSE_BPF,
  INCREMENTAL_FS, ANDROID_BINDERFS, ANDROID_BINDER_IPC_32BIT,
  NET_ACT_SAMPLE, UPROBE_EVENTS. Всего восемь отсутствующих имён.
  Исправить UPROBE_EVENTS на UPROBE_EVENT тоже недостаточно:
  UPROBE_EVENT существует глобально, но ARM64 здесь не выбирает
  ARCH_SUPPORTS_UPROBES.
- Удалены повторные назначения уже включённых параметров BPF, cgroups
  и traffic control. Запись BPF_EVENTS удалена: её зависимости в данной
  конфигурации не выполнены. TRACEPOINTS — вычисляемый параметр,
  его отдельная запись не обеспечивает поддержку BPF trace events.
- Явно включены NAMESPACES, UTS_NS, PID_NS, NET_NS.
  В эталонной разрешённой конфигурации NAMESPACES был выключен:
  EMBEDDED выбирает EXPERT, при котором значение по умолчанию отличается.
  USER_NS не включался. IPC_NS не доступен без SYSVIPC/POSIX_MQUEUE;
  его наличие не заявляется.
- Включён существующий MEMBARRIER. Это только имеющийся API 4.4:
  в UAPI есть QUERY и SHARED; современные private expedited команды
  этим изменением не добавляются.
- Включены DM_VERITY и DM_VERITY_FEC; явно указан CRYPTO_SHA256.
  Это поддержка target, а не готовая интеграция AVB/fs_mgr/fstab.
- Сохранены поддерживаемые MEMCG_SWAP и NET_ACT_POLICE из переходного
  коммита; их значения подтверждены Kconfig.
- Явно закреплено ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder".
  Это существующие символьные устройства, не binderfs.
- build.config.common возвращён к метке android-4.4-p, соответствующей
  исходникам. KERNEL_DIR=common и пути prebuilts относятся к Android
  build harness; этот файл не является самостоятельным WSL build script.

Никаких фиктивных драйверов, подмены номера версии, изменения ABI
или ослабления SELinux в ядре не внесено.

## Проверка подсистем

| Подсистема | Подтверждено в текущих исходниках | Ограничение для LOS21 / 4.19 |
| --- | --- | --- |
| ARM64 / MSM8996 | ARCH_QCOM, ARCH_MSM8996, ARCH_SONY_TONE, MACH_SONY_DORA; COMPAT и compat vDSO | Требуются ARM64 и ARM32 toolchain; перенос arch/SoC API не выполнен |
| Device Tree | Четыре варианта dora для v3/v3.0 с PMI8996 и без него; model SoMC Dora-ROW, compatible somc,dora-row и qcom,msm8996 | DTB не скомпилированы; различия F8131/F8132 и modem/DSDS требуют проверки на устройствах |
| MDSS | drivers/video/fbdev/msm, исправления DSI/clock из эталона; dsi-panel-dora.dtsi содержит 1080p60, включая 1080x1920@60 | Это vendor fbdev MDSS, не завершённый перенос DRM/KMS; HWC/gralloc/ION ABI нужно сохранять |
| Adreno 530 | drivers/gpu/msm, KGSL и A5xx, QCOM_KGSL=y | Совпадение ioctl, memory mapping, IOMMU и sync с vendor GPU userspace не тестировалось |
| BCM4359 | BCMDHD PCIe, BCM4359=y, firmware/NVRAM пути совпадают с device tree проекта | Нужны реальные firmware/calibration; cfg80211 API при переносе на 4.19 отличается |
| UFS / SD | SCSI_UFS_QCOM, ICE, MMC_SDHCI_MSM | IOMMU, clocks, ICE и файловые системы требуют runtime-проверки |
| USB | DWC3, PHY, configfs, FunctionFS/MTP и Qualcomm gadget-функции | ADB/MTP, role switching, зарядка, host mode не проверены |
| Звук | MSM8996 ASoC, codec/slimbus-конфигурация | Проверить DSP firmware, audio HAL ABI, маршрутизацию и звонки |
| Питание | QPNP charger/FG, регуляторы, RPM/SMD, PM и thermal-драйверы | Suspend/resume, температура, зарядка и расход батареи не измерялись |
| Binder | IPC включён; binder/hwbinder/vndbinder явно указаны | BinderFS отсутствует; новые ioctl/features Android требуют анализа реального userspace |
| BPF | SYSCALL/JIT, cgroup, cls/act BPF разрешаются в y | Флаги не добавляют новые map/helper/API: например, LRU_HASH, LPM_TRIE и OBJ_GET_INFO_BY_FD отсутствуют в текущем UAPI; eBPF loader Android 14 не проверен |
| Cgroups | memcg, swap accounting, cpusets, cpuacct, schedtune, cgroup BPF | Нельзя утверждать совместимость task_profiles или cgroup v2 по этим флагам; нужны проверки mount/controller ABI |
| SELinux | SECURITY_SELINUX=y, checkreqprot=0 | Загрузка целевой политики не проверена. В tone-common main имеется cmdline androidboot.selinux=permissive; этот внешний репозиторий не изменялся |
| ext4 / F2FS | Обе ФС, security/encryption и vendor ICE | Современный fscrypt/key management и параметры mkfs надо сверять; нельзя форматировать разделы по предположению |
| dm-verity | target/FEC разрешаются в y | DM_VERITY_AVB не включён; цепочка доверия и Sony bootloader не проверены |
| Namespaces / seccomp | UTS/PID/NET включены, SECCOMP_FILTER разрешается в y | USER_NS выключен; IPC_NS отсутствует в текущей конфигурации |
| Сеть | IPv4/IPv6, netfilter, TUN, RMNET/IPA, BPF cls/act | Netd, VPN, tethering и мобильные данные не тестировались |
| PSI / IncFS / FUSE BPF | Реализаций нет | При необходимости нужен настоящий backport с зависимостями; не все функции универсально обязательны для каждого legacy-порта Android 14 |

### WPA2 и 1080p60

Исходники драйверов и Device Tree не менялись по сравнению с эталоном
0c295b15. Сохранены его изменения dhd_linux.c, dhd_custom_gpio.c,
wl_cfg80211.c, nl80211.c, sme.c, mdss_dsi.c, mdss_dsi_clk.c и панелей.

В wl_cfg80211.c эталон содержит fallback принудительного AES, помеченный
TEMP DIAG, при отсутствии данных cipher. Это диагностический обход,
а не доказанное универсальное исправление WPA2 и не свидетельство
устранения KRACK. На новой базе нужно проверить propagation crypto/AKM
данных, WPA2-PSK/CCMP, mixed WPA/WPA2, reconnect, suspend и AP mode.
Не переносить структуру cfg80211_connect_params механически между API.

Для дисплея сохранять выбор панели, power sequencing, reset, DSI clocks,
PHY timings и 1080p60. Проверять холодную загрузку, гашение/пробуждение,
яркость и отсутствие артефактов на каждом реально доступном варианте панели.

## Sony boot image и внешние конфигурации

Прочитаны, но не изменялись:
sbot110/android_device_sony_dora/main/BoardConfig.mk и
sbot110/android_device_sony_tone-common/main/BoardConfigCommon.mk,
rootdir/etc/fstab.qcom.

dora выбирает tone_dora_defconfig и перечисляет dora,dora_dsds,F8131,F8132
в OTA assert. В tone-common заданы ARM64 + ARM userspace, Adreno 530,
64-bit Binder и следующие параметры:

- Image.gz-dtb, base 0x80000000, pagesize 4096.
- tags offset 0x01E00000, ramdisk offset 0x02000000.
- boot/recovery partition size 67108864 байта.
- TARGET_KERNEL_SOURCE=kernel/sony/msm8996.
- Firmware Wi-Fi: /vendor/etc/firmware/wlan/bcmdhd/fw_bcmdhd.bin,
  AP firmware fw_bcmdhd_apsta.bin; NVRAM bcmdhd.cal.

В arch/arm64/boot/dts/qcom находится ссылка на arch/arm/boot/dts/qcom;
расположение DTS в arch/arm само по себе не ошибка.
Image.gz-dtb создаётся конкатенацией с выбранными DTB.
**Image.gz-dtb не является готовым boot.img.** Версию заголовка,
cmdline, ramdisk, возможную подпись/упаковку и размер сверять с рабочим
образом Sony/LOS20. Указанные параметры прочитаны из проекта, но
bootloader-приёмка не проверена. Нет оснований переводить устройство
на GKI/vendor_boot только из-за Android 14.

## Выполненная проверка и сборка

Локальный репозиторий в облаке оставлен в неизменённой ветке work.
Для проверки создана временная копия исходников эталона без .git:
остальное дерево идентично целевой ветке по GitHub compare.
В копию подставлен исправленный defconfig целевой ветки.
Генерируемые файлы вынесены в отдельный O= каталог.

Проверены имена всех добавленных назначений по определениям Kconfig.
Повторный ARM64 tone_dora_defconfig завершился с кодом 0.
Проверены 21 выбранный параметр в разрешённой .config:
MEMCG_SWAP, MEMBARRIER, NAMESPACES, UTS_NS, PID_NS, NET_NS,
DM_VERITY, DM_VERITY_FEC, CRYPTO_SHA256, SECCOMP_FILTER, NET_ACT_POLICE,
CGROUP_BPF, BPF_JIT, EXT4_FS, SECURITY_SELINUX, QCOM_KGSL, BCM4359,
SND_SOC_MSM8996, SCSI_UFS_QCOM, FB_MSM_MDSS и имена Binder-устройств.
Это 20 булевых параметров и одна строка Binder.

Сравнение с разрешённой конфигурацией эталона подтвердило изменения
namespaces, membarrier, memcg swap, policing и dm-verity.
SHA256 и Binder-имена раньше уже выбирались значениями по умолчанию.

Дважды предпринята сборка Image.gz-dtb, в том числе после финальной
редакции defconfig. Итоговый код выхода 2, остановка до компиляции ядра:

~~~
arch/arm64/Makefile:79: *** arm-linux-gnueabi-gcc not found, check CROSS_COMPILE_ARM32.  Stop.
make: *** [Makefile:152: sub-make] Error 2
~~~

Также отсутствует aarch64-linux-gnu-gcc. Имеется только host GCC 14;
Clang/LLD, flex, bison, bc не найдены. Host-инструмент Kconfig собрался
из поставляемых generated parser sources. Предупреждение linker про
Cortex-A53 в такой среде не доказывает дефект целевого binutils.

Около 9.7 GiB RAM, swap нет, до временной копии около 7.5 GiB диска.
Git HTTPS из executor не работает: настроенный proxy:8080 недоступен.
GitHub connector работает для чтения и прямого сохранения коммитов.
Toolchain через сеть executor не установлен. Образ ядра, DTB и модули
не получены; компиляционные ошибки C/ASM пока не исследованы.
Полная сборка LineageOS не запускалась. Смартфон не тестировался.

## Локальная проверка в WSL Ubuntu 22.04

Работать в Linux filesystem WSL, а не в /mnt/c.
Ниже команды для **фактически имеющегося 4.4**, не доказанная сборка 4.19.
Версии GCC/binutils Ubuntu 22.04 — отправная точка; их совместимость
с этим vendor-деревом ещё предстоит проверить. Для воспроизведения
эталона предпочтительно знать точные версии его рабочих toolchain.

~~~bash
sudo apt update
sudo apt install git build-essential bc bison flex libssl-dev libelf-dev \
  libncurses-dev device-tree-compiler python3 \
  gcc-aarch64-linux-gnu gcc-arm-linux-gnueabi

git clone --single-branch \
  --branch kernel-4.19-701905467803253630 \
  https://github.com/sbot110/android_kernel_sony_msm8996.git
cd android_kernel_sony_msm8996

test "$(git branch --show-current)" = \
  kernel-4.19-701905467803253630 || exit 1
git status --short
head -5 Makefile
aarch64-linux-gnu-gcc --version
arm-linux-gnueabi-gcc --version

# Эта проверка сейчас должна показать 4.4, а не 4.19.
python3 - <<'PY'
from pathlib import Path
import re
s = Path("Makefile").read_text()
v = [re.search(r"^" + k + r"\s*=\s*(\d+)", s, re.M).group(1)
     for k in ("VERSION", "PATCHLEVEL", "SUBLEVEL")]
print("Фактическая версия:", ".".join(v))
PY

mkdir -p out/dora
make O="$PWD/out/dora" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CROSS_COMPILE_ARM32=arm-linux-gnueabi- tone_dora_defconfig

# Проверять итоговую .config: запись в defconfig может быть отброшена.
rg 'CONFIG_(NAMESPACES|NET_NS|MEMBARRIER|DM_VERITY|SECCOMP_FILTER|ANDROID_BINDER_DEVICES)' \
  out/dora/.config

set -o pipefail
make O="$PWD/out/dora" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CROSS_COMPILE_ARM32=arm-linux-gnueabi- \
  -j4 Image.gz-dtb modules 2>&1 | tee out/dora/build.log
~~~

Если rg не установлен, использовать grep -E.
Для отдельной последующей проверки DT:

~~~bash
make O="$PWD/out/dora" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CROSS_COMPILE_ARM32=arm-linux-gnueabi- dtbs
~~~

Сохранить commit SHA, .config, версии toolchain и полный build.log.
Не применять LLVM=1 или современный Clang только по инструкции
для Android 14: сначала проверить поддержку этим старым Kbuild.
При ошибке исправлять конкретную причину; не выключать драйверы,
SELinux, compat vDSO или проверки для получения формально успешной сборки.

## Что необходимо для настоящего 4.19

1. Выбрать и зафиксировать полноценную совместимую базу Linux/Android 4.19,
   оценить её vendor MSM8996 поддержку. В разрешённой ветке сейчас такой базы нет.
2. Перенести SoC bring-up, clocks, regulators, IRQ, RPM/SMD/GLINK,
   IOMMU, DT и Sony board support. Проверять API, reserved-memory и firmware.
3. Перенести MDSS/KGSL/ION, BCMDHD, storage/ICE, USB, audio/DSP,
   питание и остальные аппаратные компоненты, сохраняя ABI vendor userspace.
   Не заменять отсутствующие реализации заглушками.
4. Составить defconfig уже против Kconfig этой 4.19 базы; применить подходящие
   Android common backports. Не считать PSI, BinderFS, IncFS и FUSE BPF
   существующими только по имени CONFIG_.
5. Сверить требования с точной ревизией LOS21, first shipping API level,
   target FCM, vendor manifest, init/task_profiles и bpfloader;
   различать возможность legacy-порта и соответствие Android certification.
6. Собрать Image.gz-dtb/DTB/modules, проверить упаковку рабочего Sony boot.img,
   затем выполнить реальные испытания загрузки и оборудования.
   Для F8131 и F8132 отдельно проверить modem/SIM/DSDS.

Подготовка полноценного 4.19 и подтверждение LOS21 остаются незавершёнными.
Этот аудит исправляет подтверждённые ошибки существующей ветки и сохраняет
проверяемую отправную точку, но не заменяет перенос ядра.
