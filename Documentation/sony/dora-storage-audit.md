# Dora: аудит внутреннего накопителя, eMMC и UFS

Обновлено: 2026-11-04. Начальный HEAD предыдущего этапа:
`8d7ab8a3baeb73cfaea264f3f7758e32cffce097`.
Рабочий аппаратный эталон Sony 4.4:
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`.

**Вывод: подтверждённый внутренний накопитель dora F8131/F8132 подключён
через SDHC1 eMMC (`7464900.sdhci`), а не через UFS.** SDHC2
(`74a4900.sdhci`) обслуживает съёмный MicroSD/hybrid UIM2. Предыдущий этап
ошибочно считал UFS основным загрузочным накопителем. Компиляция UFS и
`status = "ok"` общего SoC узла этого не доказывали.

## Доказательства и границы вывода

| Источник | Наблюдение | Что это доказывает |
| --- | --- | --- |
| Все четыре итоговых Sony 4.4 Dora DTB | SDHC1: 8-bit, non-removable, HS400/HS200/DDR 1.8 V; регистры 0x7464900/0x7464000 | Реальная аппаратная конфигурация внутренней eMMC; она одинакова по storage во всех четырёх SoC/PMIC вариантах |
| Те же итоговые DTB, `/firmware/android/fstab` | system → `/dev/block/platform/soc/7464900.sdhci/by-name/system`; vendor → `.../by-name/oem`, SquashFS | Android early mount ожидает именно SDHC1, включая OEM как vendor |
| Tone-common `init.qcom.rc:44–45,60,81–83` | bootdevice symlink из ro.boot.bootdevice; SD host mmc1; прямое ожидание mmcblk0p1 | Нужны стабильные имена platform controller и индексы mmc0/mmc1 |
| Tone-common `fstab.qcom` | `/dev/block/bootdevice/by-name/...`; MicroSD `/devices/soc/74a4900.sdhci/mmc_host*` | Связь system/oem/userdata/cache/persist/dsp/modem/bluetooth с bootdevice и отдельный съёмный слот |
| Tone-common `ueventd.qcom.rc:272–273` | Оба FRP пути: bootdevice и platform/soc/7464900.sdhci | Независимое подтверждение ожидаемого controller path |
| Предоставленные пользователем сведения о старых логах | androidboot.bootdevice=7464900.sdhci и mmcblk0 | Runtime-сведения пользователя согласуются с исходниками; сами сырые логи в текущем workspace отсутствуют и заново не прочитаны |
| Общее Sony SoC DTS | И SDHC1, и UFS включены | Это общий набор контроллеров, не доказательство установки обоих накопителей |

В доступных источниках нет подтверждённого UFS-equipped варианта F8131/F8132.
Такая поддержка не объявляется. UFS controller **и PHY отключены для Dora**,
включая все четыре DTB; это ограничение не снимается после реализации GCC
corner votes. Сам UFS код и прежние регрессионные проверки сохраняются для
общей MSM8996 базы и не выдаются за проверку внутренней памяти Dora.

Внешние источники прочитаны без изменений:

- [tone-common, 2701d2aecab52b30c3672ecebbad6ae1910cc212](https://github.com/sbot110/android_device_sony_tone-common/tree/2701d2aecab52b30c3672ecebbad6ae1910cc212): rootdir/etc/fstab.qcom, init.qcom.rc, ueventd.qcom.rc, BoardConfigCommon.mk.
- [dora, 5e9385a65257a28f9a3974342331644a3bbcdd49](https://github.com/sbot110/android_device_sony_dora/tree/5e9385a65257a28f9a3974342331644a3bbcdd49).
- [vendor, bb8c352c8da4942f6189fd08d0c1ec947f25e8ef](https://github.com/sbot110/android_vendor_sony/tree/bb8c352c8da4942f6189fd08d0c1ec947f25e8ef).

## Восстановленные аппаратные данные

| Свойство | Внутренний SDHC1 | Съёмный SDHC2 |
| --- | --- | --- |
| HC/core registers | 0x7464900/0x500; 0x7464000/0x800 | 0x74a4900/0x314; 0x74a4000/0x800 |
| CQ resource | 0x7464e00/0x19c | Нет |
| GIC SPI | 141, 134 | 125, 221 |
| Шина | 8-bit non-removable | 4-bit removable |
| Native GCC | SDCC1_AHB, APPS, ICE_CORE | SDCC2_AHB, APPS |
| Driver clock names | iface_clk, core_clk, ice_core_clk | iface_clk, core_clk |
| Card/IO supply | PM8994 L20 2.95 V; S4 1.8 V | L21 2.95 V; L13 1.8–2.95 V |
| Clock plan, MHz | 0.4/20/25/50/96/192/384 | 0.4/25/40/50/80/100/200 |
| Режимы | HS400_1p8v, HS200_1p8v, DDR_1p8v | SDR12/25/50/104, DDR50 |
| NoC bandwidth path | master 78 → EBI 512, 9 cases | master 81 → EBI 512, 10 cases |
| Tray/UIM2 | Не относится к внутреннему накопителю | CD GPIO40; UIM2 GPIO29, исходная Sony логика |

384 MHz — исходная внутренняя DDR clock rate, не выдуманная частота карты.
NOMINAL fmax ceiling 400 MHz не означает назначения 400 MHz вместо 384 MHz.
Отдельного generic MMC PHY у этой реализации нет: DLL, phase selection,
HS200 tuning и HS400 calibration находятся в `drivers/mmc/host/sdhci-msm.c`.
Реальные tuning/CRC/data integrity на телефоне ещё не проверены.

Исходные pad drive/bias и active/sleep состояния сохранены. Устаревший
`num-grp-pins` заменён естественной шириной native pin group; clock hashes
переведены на реальные GCC IDs, supplies — на настоящие PM8994 providers.
Неподходящие upstream `vmmc/vqmmc` свойства удалены при переходе к vendor
`vdd/vdd-io`, чтобы не создавать двух владельцев одного rail.

Legacy `sdhc-msm-crypto` phandle не скопирован: текущий CQHCI crypto API
не использует его. Настоящий ICE clock сохранён. Совместимость существующего
зашифрованного userdata с ICE/QSEE/Keymaster не доказана; отключение проверок
шифрования или форматирование не применяются как обход.

## Изменения и автоматическая проверка

- `msm8996-sony-xperia-tone-storage.dtsi`: полноценные исходные hardware
  данные обоих контроллеров, реальные clocks/rails/pads/vectors, firmware
  system/vendor/vbmeta metadata; сохраняются имена `sdhci@...`.
- `/aliases`: sdhc1/sdhc2 нужны vendor SDHCI; mmc0/mmc1 резервируют
  userspace индексы независимо от deferred/probe порядка.
- `check-storage-dt.py` и `dora-storage-dt.json`: точные итоговые Sony данные,
  provider references, firmware paths, UIM2/CD и отрицательные fixtures.
- `check-android-abi.py`: реальные Binder/ION/fscrypt layouts и dispatch,
  SquashFS/LZ4/xattrs и обязательные VFAT NLS dependencies.
- MMC core/driver: host-local UIM2, alias reservation, настоящий deferred
  probe и provider acquisition до clock MMIO, точный bootdevice token,
  IRQ/work cleanup и публикация слота только после успешного probe.
- `check-mmc-alias.py`, `check-mmc-uim2.py`, `check-mmc-providers.py`,
  `check-mmc-power.py`: actual allocation/scan/stop/helpers, concurrent bus
  votes, ошибки clocks/rails, применённая запись с потерянным ACK и cache
  divergence. Все четыре host tests и финальная ARM64 объектная сборка
  core/host/slot-gpio/sdhci-msm завершились кодом 0. Полная сборка записывается
  отдельно в `dora-build-validation.txt`.

Physical power_error и bus quarantine сохраняют первый errno. После любой
неопределённой physical regulator transaction повторные power/clock enables
блокируются; best-effort shutdown не снимает latch. Generic cached rollback
не объявляется восстановлением реального PMIC. В этапе 5 добавлен настоящий
provider-specific UV/SWEN/load replay/ACK для BOTH sets. Explicit recovery
разрешён только при наличии ранее подтверждённого полного baseline; неизвестная
первая транзакция не восстанавливается предположением. Наличие этого API не
снимает host-local quarantine автоматически и не доказывает напряжение на PMIC.
SDHC1/2 пока safety-gated до проверки настоящего GCC CX coupling, включая
родительский GPLL4, подготовку clocks, ACK и ошибки voltage transactions.
UFS остаётся отключённым независимо от этого. Не включается DVFS, четыре
CPU или незавершённая зарядная цепочка ради обнаружения block device.

## Разделы и модели

Ожидаются исходные GPT labels: system, oem, userdata, cache, persist, dsp,
modem, bluetooth, boot/recovery/misc и прочие существующие разделы. Их
таблица, содержимое и имена не изменяются. Driver registration и aliases
готовят ожидаемое обнаружение; без телефона это не является доказательством
существования, успешного монтирования или расшифровки разделов.

F8131: Single SIM userspace режим сохранён, встроенная eMMC и MicroSD
требуют отдельного runtime теста. F8132: дополнительно нужны оба сценария
hybrid tray — MicroSD и UIM2/DSDS, смена карты, suspend/wakeup и оба SIM.
Ни одна модель не привязана к конкретному stepping, PMIC или панели.
Нет нового boot.img, команд прошивки, операций с userdata/modem/TA/persist
или форматирования. Статусы сборки, boot readiness и runtime подтверждаются
раздельно в итоговом отчёте этого этапа.

## Этап 5: физические RPM зависимости

Начальный HEAD этапа 5:
`03a3c224b84079db06a424e387e220b6eafc6cfa`.
Во всех четырёх итоговых DTB пять ресурсов S4/L12/L13/L20/L21 используют
согласованный backend с `qcom,active-sleep-votes`, `qcom,set = <3>` и
`qcom,rpm-managed-input`. Сохраняются существующие phandles и реальные
PM8994 voltage descriptors. Оба SDHC требуют
`sony,require-rpm-physical-acks`; compiled-DT проверки отклоняют отсутствие
этого флага, физического opt-in, load permission или BOTH request contract.

Устранена ошибочная входная связь общего upstream Tone DT: L20/L21 с
выходом 2,95 В ссылались на S5, ограниченный 2,15 В. Итоговые Sony 4.4 DTB
всех четырёх вариантов не задают электрических `*-supply`/`parent-supply`
у этих пяти RPM ресурсов или их предков. Исходный Sony driver устанавливает
Linux parent только при явном `parent-supply`; здесь такого свойства нет.
Поэтому backend сохраняет делегирование входных зависимостей firmware RPM,
а не назначает другой предполагаемый источник и не создаёт dummy VIN.
Остальные legacy native rails сохраняют существующие input metadata.

В исходных Sony данных S4 не является provider `regulator-always-on`;
этот унаследованный upstream флаг удалён. Retention eMMC IO остаётся
политикой исходного SDHC1 consumer (`qcom,vdd-io-always-on`), с отдельными
voltage/load/enable requests. Static system loads, дублирующие client loads,
не возвращены. L12 boot proxy 10 мА является временным clock consumer,
а не постоянным provider load или разрешением полагаться на bootloader.

Точные source hashes, voltage constraints, sets и отсутствие Linux input
сохранены в `dora-rpm-physical-dt.json`; `check-rpm-physical-dt.py`
сверяет данные с четырьмя reference DTB и исходным Sony driver при передаче
`--reference-dir` и `--reference-commit`. Эти проверки не измеряют voltage,
не подтверждают RPM firmware input handling и не выдают успешную компиляцию
за обнаружение mmcblk0. Основные SDHC1/SDHC2 остаются `disabled`; UFS host
и PHY также остаются отключёнными. Отдельный активный diagnostic DT требует
дополнительной квалификации полного power/clock handoff и не создаётся
только на основании наличия новых providers.
