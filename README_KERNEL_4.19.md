# Sony Xperia X Performance: переход на Linux 4.19

Устройство: dora / tone, F8131/F8132, Qualcomm MSM8996.
Дата обновления: 2026-11-01.

**Текущий статус: BUILDABLE. Завершение аппаратного порта не достигнуто.**
В этом продолжении исправлены Sony QUSB2 tuning, ошибочный GPIO USB ID и
fallback выбора панели; усилены Android config и проверки
DT/ARM64/appended DTB. Подробный [аудит блокеров](Documentation/sony/dora-hardware-blockers.md)
объясняет, почему четыре аппаратных варианта и первая безопасная загрузка
ещё не готовы. [Аудит LOS21](Documentation/sony/dora-android14-audit.md)
разделяет проверенные kernel interfaces и непроверенные runtime ошибки.

**В корне теперь настоящие исходники Linux 4.19.318. Это начальный перенос,
а не готовое к загрузке ядро LineageOS 21.** Импорт базы, компиляция и
работоспособность оборудования — разные этапы. Загрузка телефона и
совместимость с vendor-компонентами Android 14 пока не подтверждены.

**Сборочная цель выполнена:** ARM64 vmlinux, Image.gz, DTB dora и
Image.gz-dtb успешно получены с MDSS, KGSL, BCMDHD, Sony Tasha audio,
Clearpad и реальными VADC/BCL/fuel-gauge/SMBcharger, код выхода сборки 0.
Ревизия, форматы, контрольные суммы, toolchain и пределы проверки
записаны в [протоколе сборки](Documentation/sony/dora-build-validation.txt).
Это подтверждение сборки на ПК, а не завершения аппаратного порта.

**F8131 и F8132 обязательны одновременно.** Подробная
[матрица совместимости](Documentation/sony/dora-compatibility.md) различает
проверку исходников, компиляцию и данные с физического устройства.
Логи AUO F8132 относятся к рабочему 4.4; они не являются тестом нового 4.19.
Ни модель SIM, ни один экземпляр панели не используются для сужения порта.

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
коммиту без переключения рабочей ветки. Если single-branch clone его не
содержит, `git fetch origin 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`
получает объект в FETCH_HEAD; затем его файлы можно читать через git show.
Рабочая ветка при этом остаётся прежней.

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
BPF_JIT_ALWAYS_ON включает настоящий ARM64 JIT с начала работы; PSI включён
по умолчанию, runtime disable SELinux запрещён. Проверяются 277 явных
назначений и 87 необходимых built-ins, включая memfd/ashmem и fscrypt.
Отдельно включены реальные DT providers RPM GLINK/APCS, PMIC GPIO/MPP,
simple-pm-bus, QUSB2 и MSM8996 UFS14nm; их наличие проверяется в System.map.

## Device Tree: начальный headless bring-up

Добавлено начальное описание dora на основе Linux v5.15 Sony Tone DT,
адаптированное к существующим драйверам и bindings этой базы 4.19.
Исходные BSD-3-Clause лицензии и авторство сохранены в DTS.

Текущая цель — qcom/msm8996-sony-xperia-tone-dora.dtb:

- Только сочетание PM8994 + PMI8994 и идентификатор MSM8996 v3.1.
  Четыре старых vendor DTB для разных SoC/PMIC здесь пока не восстановлены.
- Сохраняется upstream bring-up ограничение maxcpus=2. Наличие NR_CPUS=4
  в конфигурации не означает проверку всех четырёх ядер.
- Дисплей остаётся headless. Sony panel/incell, Clearpad, Tasha audio и
  батарейные драйверы компилируются; полный DT/provider/firmware контракт
  дисплея, touch, аудио, зарядки, камер и secure firmware ещё не завершён.
- Подключён vendor PCIe host для BCM4359 с native QMP PHY/GCC genpd;
  GPIO84 имеет одного владельца. INTx и link-down SPI254 сверены с Sony.
- Native QUSB2 использует точные Sony board TUNE1/2/3/4 d0/a3/83/c7
  с сохранением nvmem trim. Неверный GPIO24 ID extcon удалён; остаётся
  фиксированный high-speed peripheral mode без реального VBUS/OTG provider.
- PMIC supplement описывает реальные REVID/VADC/FG/BCL и профиль Dora
  1300-3513 (330 кОм, 17%) для PM8994/PMI8994. Это не заменяет зарядку.
- Отсутствующие в 4.19 bindings не заменяются придуманными узлами.
  Скомпилированный DTB сам по себе не доказывает работу устройств.

F8131 и F8132 требуют отдельных испытаний modem/SIM/DSDS. Совпадение
названия платы или OTA assert в device tree Android этого не подтверждает.

## Основные аппаратные блокеры

| Подсистема | Что имеется в 4.19 | Что ещё требуется |
| --- | --- | --- |
| CPU / clocks | Generic GCC/MMCC MSM8996 и pinctrl | Старые qcom,gcc-8996/mmsscc-8996/gpucc-8996 bindings и числовые clock IDs несовместимы с generic framework; проверить DVFS, RPM и binning |
| Дисплей | Vendor MDSS; перенесены исходники Sony panel/incell, LAB/IBB API и MSM8996 PLL bindings | Завершить реальную цепочку DT: MDSS/bus/SMMU/clock/power/reset/подсветка для всех вариантов |
| GPU | KGSL и Adreno 530 | Проверить IOMMU, firmware, secure buffers и ioctl ABI с реальными vendor библиотеками |
| Wi-Fi | BCMDHD BCM4359 и Sony glue; DT теперь использует реальный vendor msm_pcie с QMP PHY | Проверить link training, DMA/SMMU, firmware/calibration, WPA2, IRQ, suspend/wakeup на обоих аппаратах |
| UFS / SD | Восстановлен настоящий Sony UFS QMP14nm, native UFS/SDHCI | Требуются UFS bus vectors и BIMC/NoC topology; без них probe блокируется. ICE и сохранность данных не проверены |
| USB | DWC3 и gadget/configfs | Проверить role switching, PHY, VBUS/OTG, ADB/MTP и зарядку |
| Звук | Перенесены Sony MSM8996/Tasha machine, vendor APRv2/Q6, WCD9335/CPE/WSA и switch; полная линковка проходит | Реальные LPASS/PIL/SSR, SLIMbus, clocks/IOMMU/firmware и sound DT; Sony DSP-effects extensions остаются неполными |
| Питание | Перенесены настоящие Sony QPNP VADC, BCL, FG и SMBcharger с расширениями; native PM/IRQ/work lifetime | Зарядка требует настоящего DPDM/PHY provider, Sony microUSB ID/MPP и физической проверки safety paths; другие PMIC/SoC DT варианты ещё не перенесены |

Sony sound card выбирается отдельным SND_SOC_SONY_MSM8996; upstream
SND_SOC_MSM8996/APQ8096 не используется как её замена. Зарядка,
уровень батареи, JEITA, температурные ограничения и suspend current
остаются непроверенными до переноса подходящих драйверов и испытаний.

### Новые аппаратные исходники и проверка API

В основном defconfig встроены реальные legacy-драйверы питания и аудио,
чтобы обычная локальная сборка проверяла их вместе. Их включение в vmlinux
не означает, что отсутствующие DT/firmware связи уже работают.

- [PCIe/WLAN](Documentation/sony/dora-pcie-port.txt): GPIO/clock/PHY,
  INTx, исправление SPI254 и безопасное освобождение host resources.
- [VADC](Documentation/sony/dora-adc-port.txt): private namespace старого
  API, калибровка, настоящие измерения, threaded IRQ и ordered callbacks.
- [BCL](Documentation/sony/dora-bcl-port.md): исходные PMIC registers,
  scaling/calibration, экспорт msm_bcl_read и обработка ошибок.
- [Fuel gauge](Documentation/sony/dora-fg-port.txt): SRAM/profile/JEITA,
  alarmtimer work, wakeup_sources и баланс supply references.
- [SMBcharger](Documentation/sony/dora-charger-port.txt): Sony charging
  policy и исходная battery implementation, native PM, строгие зависимости.
- [Аудио](Documentation/sony/dora-audio-port.txt): Sony Tasha routes,
  APR/Q6/WCD9335 и проверенные IRQ/GPIO/switch/SSR lifetime.
- [Clearpad](Documentation/sony/dora-touch-port.txt): MDSS framebuffer
  notifications и Tone incell reset без назначения одной панели.
- [PMIC DT](Documentation/sony/dora-power-dt.txt): сравнение с итоговым
  Sony DT и исходный профиль батареи без подмены данными MTP.
- [UFS14nm](Documentation/sony/dora-ufs-port.txt): исходные Sony calibration
  tables, native PHY API, реальные provider/init errors и безопасный unwind;
  UFS bus topology всё ещё отсутствует, это не доказательство работы накопителя.

DPDM regulator не заменён fixed-regulator: он должен управлять настоящим
PHY и высокоимпедансным состоянием D+/D-. Зарядный DT пока не активирован.
Dora использует microUSB; TypeC-настройки Kagura/Keyaki не переносятся на него.
Наличие отдельного vendor QUSB PHY не позволяет включить два драйвера одного
PHY одновременно; [разбор DPDM/USB](Documentation/sony/dora-usb-dpdm-gap.txt)
описывает реальные последовательности и незавершённые связи.

### ION и vendor ABI

Разрыв allocator ABI частично закрыт настоящими per-open IDR handles:
alloc/free/share/map/import и compat32 используют реальные dma-buf references,
а не fd как фиктивный handle. Исправлены unwind при copy-out failure и lifetime
независимых share fd. Native fd ABI сохранён. Добавлен device-side smoke test
tools/testing/selftests/ion/dora-legacy-handles.c; в облаке он пропускается
с кодом 77, поскольку /dev/ion отсутствует. Runtime успех не заявляется.

Исходный старый ion_allocation_data
содержал len/align и handle с отдельными share/map/free операциями.
В 4.19 allocation возвращает fd, а msm_ion.h задаёт TARGET_ION_ABI_VERSION 2.
Старые gralloc/camera/audio binaries нужно проверить на использование этого
API. CUSTOM/cache-range ioctls, over-page alignment и secure heap interoperability
пока не покрыты. Нужна полная совместимость реализации или подходящий userspace;
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
Выбор по LCD ID теперь прекращается при отсутствующем, некорректном,
неизвестном или неоднозначном значении. Предпочтительная панель не остаётся
неявным fallback; отдельный host-тест реальной функции проверяет все пять
диапазонов и баланс OF references. Это не физическое испытание дисплея.

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
В текущей облачной проверке используются GCC 12.2 и binutils 2.40 вне исходников;
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
конфигурацию и проверяет её через scripts/dora/check-config.py, сверяет все
исходные панели и ADC-диапазоны, выполняет host-тест выбора панели. Затем
проверяет исходные UFS14nm таблицы и реальный calibration selector в host-модели,
собирает Image.gz и ровно qcom/msm8996-sony-xperia-tone-dora.dtb, соединяет их
в Image.gz-dtb и записывает SHA256SUMS для vmlinux, .config и образов.
check-dt.py проверяет текущий hardware tuple и структурные provider/IRQ связи.
check-artifacts.py проверяет AArch64 ELF, ARM64 header, gzip round-trip и
точный порядок/состав appended DTB, а также System.map реализации активных
providers; пишет artifact-validation.json.
Это не доказывает выбор DTB загрузчиком Sony. Скомпилированные 32 узла PMIC/profile
проверяются scripts/dora/check-power-dt.py по итоговым данным Sony 4.4.
Явный список исключает попадание
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
