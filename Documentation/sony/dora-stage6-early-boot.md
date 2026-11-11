# Stage 6: границы ранней загрузки

Статус подготовки: **BLOCKED, не BOOT TEST READY**. Телефон не подключался,
boot.img не создавался. Изменения device/vendor и запись разделов не выполнялись.
Четыре штатных DTB сохраняют выключенные SDHC1/SDHC2. Четыре отдельные
экспериментальные DTB включают только SDHC1; их сборка не разрешает загрузку.

## Проверенная программная цепочка

| Участок | Доказательство и предел |
| --- | --- |
| kernel entry | ARM64 Image header, ELF AArch64, gzip round trip и порядок четырёх appended DTB проверяет `check-artifacts.py`. Это проверка формата сборки, не фактического входа из Sony bootloader. |
| DTB selection | SoC246/board8, v3.0/v3.1 и PMI8994/PMI8996 tuples сохранены; offsets и hashes записаны в artifact-validation.json. Какой tuple выбирает конкретный загрузчик F8131/F8132, неизвестно. Модель телефона не определяет PMIC или панель. |
| early clocks/CPU | Реальное чтение APCC/CBF и QFPROM. Нет writable CPU adapter, общего CPR/SPM guard, подтверждённого CBF/ACD/PLL управления. `maxcpus=2`, CPU_FREQ/CPU_IDLE выключены. |
| GCC/RPM | Source HIGH bootstrap и same-owner reattach ограничены чистым handoff. Typed corner/physical BOTH ACK и sticky quarantine сохранены. ACK от реального RPM телефона не получен. |
| SDHC1/eMMC | Исходная цепочка GCC/NoC/pads/L20/S4, IRQ/CQE/error/PM проверена программно. Экспериментальные DTB сохраняют её. CID, interrupts, GPT, partition labels и tuning на телефоне не наблюдались. |
| early mount | DT system → `7464900.sdhci/by-name/system` ext4, vendor → `.../by-name/oem` SquashFS. `mmc0`/`sdhc1` и `mmc1`/`sdhc2` стабильны. Наличие GPT labels на конкретном устройстве не подтверждено. |
| initramfs/init | BLK_DEV_INITRD, RD_GZIP/RD_XZ и нужные ФС built-in. Реальные ramdisk/first-stage init/fstab/APEX/SELinux policy LOS21 не предоставлены. |
| pstore | Исходный Sony reserved range `0xa7f00000/0x100000` и record/console sizes сохранены. PSTORE/RAM/CONSOLE встроены. Persistence после reboot, отсутствие очистки загрузчиком/TWRP и сохранение самой ранней panic не доказаны. |
| serial | SERIAL_MSM_CONSOLE/EARLYCON встроены, `serial@75b1000` доступен в DT, но stdout-path отсутствует. Разводка, pinmux, baud rate и безопасный доступ к UART не подтверждены. Адрес/параметры earlycon не назначаются по предположению. |
| watchdog | В DT нет APSS watchdog node, QCOM_WATCHDOG_V2 выключен. Это унаследованный пробел, не выполненное в Stage6 отключение защиты. Sony4.4 описывает `0x9830000/0x1000`, SPI28/29, bark11000ms/pet10000ms. Текущий vendor driver имеет void `init_watchdog_data()` с ранними IRQ failures, после которых probe продолжает успешный путь; требуется отдельное исправление/qualification и bootloader handoff. Нельзя считать reset отключённым или безопасно обслуживаемым. |

## Фактический boot image пока недоступен

Повторно прочитан без изменений рабочий Tone reference:
[BoardConfigCommon.mk, 2701d2ae](https://github.com/sbot110/android_device_sony_tone-common/blob/2701d2aecab52b30c3672ecebbad6ae1910cc212/BoardConfigCommon.mk),
[Dora BoardConfig, 5e9385a6](https://github.com/sbot110/android_device_sony_dora/blob/5e9385a65257a28f9a3974342331644a3bbcdd49/BoardConfig.mk),
[fstab.qcom](https://github.com/sbot110/android_device_sony_tone-common/blob/2701d2aecab52b30c3672ecebbad6ae1910cc212/rootdir/etc/fstab.qcom),
[init.qcom.rc](https://github.com/sbot110/android_device_sony_tone-common/blob/2701d2aecab52b30c3672ecebbad6ae1910cc212/rootdir/etc/init.qcom.rc).
Это зафиксированные reference source files; они не заменяют boot.img рабочей
LOS20, не подтверждают состав установленного LOS21 и не изменялись.

| Reference setting | Значение в исходнике | Чего оно не подтверждает |
| --- | --- | --- |
| BOARD_KERNEL_BASE | `0x80000000` | Реальные kernel load/entry address и kernel offset конкретного boot.img |
| BOARD_KERNEL_PAGESIZE | `4096` | Формат Sony wrapper/header и его принятие bootloader |
| BOARD_KERNEL_TAGS_OFFSET | `0x01e00000` | Размещение DTB после всех bootloader преобразований |
| BOARD_RAMDISK_OFFSET | `0x02000000` | Реальный адрес и пересечения с распакованным ядром/резервами |
| BOARD_RAMDISK_USE_XZ | true | Совместимость конкретного init/ramdisk с LOS21 |
| BOARD_BOOTIMAGE_PARTITION_SIZE | `67108864` | Фактический размер boot GPT partition телефона |
| BOARD_KERNEL_IMAGE_NAME | Image.gz-dtb | Реальный выбор одного из четырёх appended DTB |

Reference cmdline принудительно включает `androidboot.selinux=permissive`.
Она не копируется в диагностический образ. Существующий точный
`patches/tone-common-enforcing-cmdline.patch` остаётся **UNAPPLIED**;
внешний репозиторий не менялся. Удаление override само по себе не доказывает
готовность enforcing policy. Итоговая bootloader cmdline неизвестна;
единственная DT bootarg `maxcpus=2` не исключает её замены загрузчиком.
Не добавляются выдуманные console/bootdevice/load parameters.

## Дисплей и USB/зарядка

MDP/DSI/PHY/panel/WLED цепочка остаётся отключённой. Сохранены source MMCC
56 GCC/49 MMCC contracts, физический L12 BOTH ACK, analog supplies,
reset/power sequence и LCD-ID данные AUO/JDI/LGD/Sharp ID3/Sharp ID9.
Ни F8131, ни F8132 не выбирают панель. Только исходный 1080p60; AUO120 и
dynamic FPS не активированы. Не завершены clock ownership recovery после
потери per-clock votes, DSI PHY analog/incell handoff и реальные измерения.
Отсутствие изображения не является доказательством отсутствия kernel entry.

DWC3/QUSB2 сохраняют прежнюю high-speed peripheral конфигурацию и Sony
tuning. CONFIGFS/FunctionFS не означают наличие работающего ADB: нужны
init/adbd, UDC enumeration и реальные логи. Generic PHY не предоставляет
DPDM regulator/QC power_supply API для SMBcharger. MicroUSB ID/VADC/VBUS/
BC1.2 routing и владение PHY относительно зарядки firmware не завершены.
SMBcharger не привязан в DT; FG и BCL source paths присутствуют. JEITA,
thermal/current limits не ослаблялись. Выключенный DT charger не доказывает
прекращение физической зарядки bootloader. Пока конфликт generic PHY с
оставленной firmware зарядкой не исключён, это **P0 безопасной первой
загрузки**, а не только P1 функциональности USB.

## Условия будущего диагностического запуска

Этот список нельзя использовать как разрешение на запуск до закрытия P0.

1. Получить исходный рабочий boot.img с provenance/SHA256 и сведения о
   загрузчике каждого устройства; разобрать фактический Sony/Android header.
   Не заменять неизвестный ELF/иной wrapper предположением Android v0.
2. Сверить фактический GPT размер boot/recovery, доступность рабочего TWRP и
   проверенного recovery image/backup. На этом этапе только чтение имеющихся
   артефактов/логов, никаких flash/format/partition writes.
3. Подтвердить фактические load/ramdisk/tags addresses, decompressed Image
   extent, отсутствие пересечений с DTB/initramfs/reserved memory и сохранение
   bootloader ownership. Значение BASE из Makefile недостаточно.
4. Подтвердить DTB-selection log для SoC/PMIC tuple телефона и проверить
   отдельный experimental SDHC1 DT с тем же tuple. Не подмешивать его в
   штатный Image.gz-dtb.
5. Проверить предоставленный LOS21 ramdisk, compression, /init, interpreter/
   linker, first-stage fstab, policy и итоговую cmdline. Для диагностики
   без записи eMMC требуется отдельный проверенный ramdisk/init policy:
   штатный Android init монтирует writable data/persist и запускает службы
   TA/charger. Просто повторно использовать его как read-only test нельзя.
6. Завершить CPU/CPR/SPM/shared-rail и watchdog handoff; проверить реальный
   RPM ACTIVE/SLEEP handshake до SDHC1 clocks. Не обходить quarantine.
7. Подтвердить хотя бы один реальный канал логов: UART с известной разводкой
   либо проверенный USB без конфликта зарядки; отдельно проверить сохранение
   pstore/ramoops в recovery. Не обещать USB/serial только по CONFIG.
8. Успех стадий фиксировать раздельно: kernel entry/banner; selected DTB и
   merged cmdline; RPM ACK; eMMC CID/IRQ; GPT/by-name; ramdisk PID1/early-init.
   Ни одна поздняя стадия не считается пройденной по host tests.
9. Признаки неуспеха: PLL unlock, CPU/RPM/CCF quarantine, voltage/IRQ timeout,
   watchdog reset, повторяемая panic, MMC CRC/tuning error или неизвестная
   температура/зарядный ток. Сохранять первый errno и полный log, не повторять
   попытки путём отключения защиты. Blank screen отдельно не диагностичен.
10. Для F8131 подтвердить Single SIM runtime; для F8132 дополнительно DSDS и
    отсутствие конфликтов MicroSD/UIM2. Оба требуют самостоятельных логов;
    четыре SoC/PMIC DTB общие, модель не фиксирует stepping/PMIC/panel.

## Автоматическая проверка

`check-early-boot.py OUT --diagnostic-out DIAG --self-test --report REPORT`
проверяет четыре штатных/четыре диагностических дерева, встроенные initramfs/
MMC/GPT/pstore зависимости, ранние mount paths, aliases, CPU/security gates
и отдельно сообщает watchdog/serial ограничения. Negative fixtures отвергают
опасные изменения. Опциональные `--boot-image PATH --partition-size BYTES`
и `--ramdisk PATH` только читают реальные предоставленные файлы. Parser
поддерживает Android v0 и raw/gzip/XZ newc; неизвестный wrapper/версия требуют
отдельного анализа. Unit fixtures находятся только в памяти, boot.img не
записывается. Даже успешный разбор не снимает аппаратные blockers.
