# Sony Xperia X Performance: переход на Linux 4.19

Устройство: dora / tone, F8131/F8132, Qualcomm MSM8996.
Дата обновления: 2026-10-31.

**В корне теперь настоящие исходники Linux 4.19.318. Это начальный перенос,
а не готовое к загрузке ядро LineageOS 21.** Импорт базы, компиляция и
работоспособность оборудования — разные этапы. Загрузка телефона и
совместимость с vendor-компонентами Android 14 пока не подтверждены.

**Сборочная цель выполнена:** ARM64 vmlinux, Image.gz, DTB dora и
Image.gz-dtb успешно получены с MDSS, KGSL и BCMDHD, код выхода сборки 0.
Проверенный код: 2b64053e1. Форматы, контрольные суммы, toolchain и пределы
проверки записаны в [протоколе сборки](Documentation/sony/dora-build-validation.txt).
Это подтверждение сборки на ПК, а не завершения аппаратного порта.

## Происхождение исходников и история

- База: [Tama-4-19/android_kernel_sony_sdm845-419](https://github.com/Tama-4-19/android_kernel_sony_sdm845-419),
  ветка lineage-20, коммит 51b2850a389ec9069d1d6f6ecd8fbf22f9c03f99.
- Дерево донора: 3feca968f677b76dbbdc0532b7d9a7b8463fb6d8.
- Импорт базы в эту ветку: 8426911b1. Подробности:
  [Documentation/sony/dora-source-base.txt](Documentation/sony/dora-source-base.txt).
- Эталон оборудования Sony 4.4.302 для LOS20:
  0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1.
- До импорта ветка содержала 4.4.302, несмотря на название 4.19.
  Аудит и исправления прежней конфигурации сохранены в истории:
  2fa6b24 и 43839fe.

Все изменения сохраняются только в существующей ветке
kernel-4.19-701905467803253630 репозитория sbot110/android_kernel_sony_msm8996.
Новые ветки и Pull Request не нужны. Эталон 4.4 доступен по указанному
коммиту без переключения рабочей ветки, например через git show.

## Что перенесено на этом этапе

Импортировано полное дерево 4.19 с исходными лицензиями, а не только изменён
номер версии. В базе есть generic MSM8996 clocks/pinctrl/UFS, vendor MDSS,
KGSL с таблицами Adreno 530 и Android common backports. Эти компоненты
не означают автоматической поддержки Sony dora.

Конфигурация arch/arm64/configs/tone_dora_defconfig составлена заново
против Kconfig 4.19. В ней запрошены ARM64 и COMPAT/vDSO, Binder/BinderFS,
BPF и cgroup BPF, memory/cpu/pids/device cgroups, PSI, membarrier,
namespaces, seccomp, SELinux, ext4/F2FS, dm-verity/FEC, сети и USB gadget.
Наличие функций проверять по разрешённой .config: запись в defconfig
может быть отброшена Kconfig из-за зависимостей. IncFS присутствует в базе;
FUSE BPF не следует считать обязательным или реализованным по одному имени.

## Device Tree: начальный headless bring-up

Добавлено начальное описание dora на основе Linux v5.15 Sony Tone DT,
адаптированное к существующим драйверам и bindings этой базы 4.19.
Исходные BSD-3-Clause лицензии и авторство сохранены в DTS.

Текущая цель — qcom/msm8996-sony-xperia-tone-dora.dtb:

- Только сочетание PM8994 + PMI8994 и идентификатор MSM8996 v3.1.
  Четыре старых vendor DTB для разных SoC/PMIC здесь пока не восстановлены.
- Сохраняется upstream bring-up ограничение maxcpus=2. Наличие NR_CPUS=4
  в конфигурации не означает проверку всех четырёх ядер.
- Это headless-описание. Панель, touch, камера, vendor DSP/audio,
  зарядка/fuel gauge и secure firmware ABI ещё требуют переноса.
- Отсутствующие в 4.19 bindings не заменяются придуманными узлами.
  Скомпилированный DTB сам по себе не доказывает работу устройств.

F8131 и F8132 требуют отдельных испытаний modem/SIM/DSDS. Совпадение
названия платы или OTA assert в device tree Android этого не подтверждает.

## Основные аппаратные блокеры

| Подсистема | Что имеется в 4.19 | Что ещё требуется |
| --- | --- | --- |
| CPU / clocks | Generic GCC/MMCC MSM8996 и pinctrl | Старые qcom,gcc-8996/mmsscc-8996/gpucc-8996 bindings и числовые clock IDs несовместимы с generic framework; проверить DVFS, RPM и binning |
| Дисплей | Vendor MDSS, включая ревизии MSM8996 | Перенести Sony panel selection, reset/power sequencing, PHY timings, подсветку и 1080p60 в рабочий DT |
| GPU | KGSL и Adreno 530 | Проверить IOMMU, firmware, secure buffers и ioctl ABI с реальными vendor библиотеками |
| Wi-Fi | Перенесены BCMDHD BCM4359, Sony controlfnc/preallocation и адаптация API 4.19 | Начальный DT использует upstream PCIe, а Sony glue требует vendor msm_pcie; связка DT/controller ещё не готова, runtime-успех не заявляется |
| UFS / SD | Qualcomm UFS/PHY и SDHCI | Проверить питание, clocks, IOMMU, ICE и сохранность данных на устройстве |
| USB | DWC3 и gadget/configfs | Проверить role switching, PHY, VBUS/OTG, ADB/MTP и зарядку |
| Звук | Общая ASoC и QCOM APR/QDSP6 инфраструктура | Старый qcom,msm8996-asoc-snd-tasha и Sony vendor QDSP6V2/APRV2 machine driver не перенесены |
| Питание | Generic PM8994 RPM regulators и отдельные PMIC drivers | Старые qcom,qpnp-smbcharger, qcom,qpnp-fg и Sony extensions отсутствуют; SMB2/5 и FG_GEN3/4 не являются их заменой |

Совпадающий символ SND_SOC_MSM8996 в новом дереве означает upstream
APQ8096/QDSP6 sound card, а не старый Sony Tasha/HAL контракт. Зарядка,
уровень батареи, JEITA, температурные ограничения и suspend current
остаются непроверенными до переноса подходящих драйверов и испытаний.

### ION и vendor ABI

Подтверждён существенный разрыв allocator ABI. Старый ion_allocation_data
содержал len/align и handle с отдельными share/map/free операциями.
В 4.19 allocation возвращает fd, а msm_ion.h задаёт TARGET_ION_ABI_VERSION 2.
Старые gralloc/camera/audio binaries нужно проверить на использование этого
API. Нужна реальная совместимость реализации или подходящий userspace;
замена деклараций UAPI не решает проблему. KGSL/MDSS ioctl, структуры,
32-битные клиенты и secure heaps также требуют отдельной проверки.

### WPA2 и панель 1080p60

Эталонные изменения доступны в коммите 0c295b15. Его BCMDHD-код содержит
WPA/WPA2 изменения и fallback AES с пометкой TEMP DIAG. Это не доказанное
универсальное исправление WPA2 или KRACK. При переносе нужны проверки
crypto/AKM propagation, WPA2-PSK/CCMP, mixed mode, reconnect и AP mode.

Три функции BCMDHD, устанавливающие WPA version/cipher/AKM, побайтово
сверены с эталоном. В новом cfg80211_connect cipher-поля уже не стираются
при очистке старого WEP key. Диагностический fallback из старого nl80211
не переносился вслепую. Подробности адаптации PM, таймеров, hotplug и
netdev lifetime: [dora-wifi-port.txt](Documentation/sony/dora-wifi-port.txt).

Изменения mdss_dsi.c и mdss_dsi_clk.c в том коммите добавляли диагностику,
а не меняли установку частот. Функциональное ограничение 60 Гц находится
в DT: удалены dynamic mode switching и режимы 1080p120. Итоговые панели
сохранены в истории как эталон; работа Sony 1080p60 в новом DT пока
не подтверждена. Одни диагностические сообщения не означают перенос панели.

## Android 14 и загрузочный образ

Наличие BinderFS, BPF или PSI не доказывает совместимость с точной ревизией
LOS21. Нужны проверки bpfloader/netd, task_profiles, init, SELinux policy,
vendor manifest/FCM и файловых систем. Legacy-порт и соответствие требованиям
сертификации Android — разные цели. SELinux не следует выключать для
получения формально успешной загрузки.

Старый tone-common задавал Image.gz-dtb, base 0x80000000, pagesize 4096,
tags offset 0x01E00000, ramdisk offset 0x02000000 и boot-раздел 64 MiB.
Это параметры эталона, а не проверенная упаковка нового образа.
Image.gz-dtb не является готовым boot.img: header, cmdline, ramdisk,
размер и возможную подпись сверять с рабочим Sony/LOS20 образом.
Не переводить устройство на GKI/vendor_boot только из-за Android 14.

## Локальная сборка: WSL Ubuntu 22.04

Работать в Linux filesystem WSL, а не в /mnt/c. Нужны оба cross toolchain:
новая база использует CROSS_COMPILE_COMPAT, не старый CROSS_COMPILE_ARM32.
В облаке используются GCC 12.2 и binutils 2.44, размещённые вне исходников;
версии Ubuntu могут отличаться, поэтому сохраняйте версии и полный журнал.

```bash
sudo apt update
sudo apt install git build-essential bc bison flex libssl-dev libelf-dev \
  libncurses-dev device-tree-compiler python3 gcc-aarch64-linux-gnu \
  gcc-arm-linux-gnueabi

git clone --single-branch \
  --branch kernel-4.19-701905467803253630 \
  https://github.com/sbot110/android_kernel_sony_msm8996.git
cd android_kernel_sony_msm8996
test "$(git branch --show-current)" = \
  kernel-4.19-701905467803253630 || exit 1
head -5 Makefile
git rev-parse HEAD
aarch64-linux-gnu-gcc --version
arm-linux-gnueabi-gcc --version

JOBS=4 OUT_DIR="$PWD/out/dora" scripts/dora/build.sh
```

Скрипт проверяет имя рабочей ветки, записывает версии toolchain, генерирует
конфигурацию и проверяет её через scripts/dora/check-config.py. Затем собирает
Image.gz и ровно qcom/msm8996-sony-xperia-tone-dora.dtb, соединяет их
в Image.gz-dtb и записывает SHA256SUMS. Явный список исключает попадание
посторонних или старых DTB из O= каталога. Существенные драйверы этой
конфигурации встроены в ядро; отдельная сборка modules не включена.

Для отдельной повторной проверки DT:

```bash
make O="$PWD/out/dora" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CROSS_COMPILE_COMPAT=arm-linux-gnueabi- \
  qcom/msm8996-sony-xperia-tone-dora.dtb
```

Переменные CROSS_COMPILE и CROSS_COMPILE_COMPAT можно задать для своих
toolchain. Проверки скриптов не заменяют загрузку и испытания смартфона.

Полную сборку LineageOS этот этап не запускает. До заявления о готовности
нужны реальные тесты загрузки, панели, UFS, зарядки/температуры, audio/calls,
Wi-Fi, modem/DSDS, USB и suspend/resume. Ни один из этих аппаратных тестов
в облачной среде не выполнялся.
