# Dora Linux 4.19: блокеры после этапа 3

Дата: 2026-11-02. Начальный HEAD этапа:
`1b2d146ae22ee973750a2ca3151f1496badc4867`.
Аппаратный эталон Sony 4.4.302:
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`.

Первая безопасная диагностическая загрузка пока не подготовлена.
F8131 и F8132 не загружались с этим ядром. Текущие результаты сборки,
точный проверенный SHA и артефакты перечислены в
[dora-build-validation.txt](dora-build-validation.txt).

## Реальные исправления

Восстановлены все 145 fabric/master/slave узлов исходного MSM8996 NoC/BIMC,
12 UFS usecases и два настоящих маршрута. Старые hashed clock IDs переведены
на реально заполненные native GCC/MMCC/RPM clock IDs. Возвращён физический
AGGRE0 MPU configuration gate. Сопоставление с четырьмя итоговыми Sony DT
сохраняет QoS, MMIO, ширины шин, RPM IDs, aggregation и ненулевые рабочие votes.
Исключены только CoreSight trace links и отдельные SPDM clients вне графа.

Bus backend теперь отправляет настоящие 64-bit active/sleep requests через
native SMD RPM: отключённый legacy RPM больше не может имитировать успех.
Исправлены ACK bounds/correlation, deferred probe, lifetime, маршрутизация,
ошибки clocks/regulators, кеширование и повторные commits. Host-тесты
проверяют реальные функции, включая отказы выделения памяти и cleanup.
Они не являются измерением bandwidth или ответов firmware телефона.

Восстановлены 33 исходных PMIC GPIO/MPP состояния; аналоговые inputs больше
не наследуют выходной драйвер. Исправлены UFS host registration/power errors,
reset-before-collapse при ошибке power-on и always-on pad load при повторе.

APCC/CBF теперь имеют реальный read-only provider и читают PLL/mux/divider
firmware без изменения частот. HMSS/MMSS CPR3, Kryo LDO/retention и APM
перенесены с сохранением исходных fuse/binning tables и лицензий.

MDSS/DSI/PLL/SMMU/LAB/IBB/WLED граф и пять LCD-ID панелей описаны полностью
в проверяемых supplements. Использован настоящий Sony WLED, а не WLED4
другого PMIC. Итоговые Sony overrides дают WLED 660 mA, 20 mA и три strings;
промежуточные 980 mA/25 mA/четыре strings не использованы. Сохранены
1080p60, автоматический LCD-ID выбор и fail-closed поведение для неизвестного
ID. AUO120 и dynamic switching отключены.

## Четыре варианта данных

| SoC | PMIC | msm revision | pmic-id | GPU CPR corners | BCL derating | Flash ramp |
| --- | --- | --- | --- | --- | --- | --- |
| v3.0 | PMI8994 | 0x30000 | 0x20009 / 0x2000a | 7 | 550000 | 6P7_US |
| v3.0 | PMI8996 | 0x30000 | 0x20009 / 0x10013 | 7 | 0 | 27_US |
| v3.1 | PMI8994 | 0x30001 | 0x20009 / 0x2000a | 8 | 550000 | 6P7_US |
| v3.1 | PMI8996 | 0x30001 | 0x20009 / 0x10013 | 8 | 0 | 27_US |

Все имеют SoC ID 246, board-id `<8 0>` и конечный IBB discharge 32.
GPU floor/ceiling/fuse/aging/corner mapping и PMIC overrides сверены после
всех исходных includes. CPU datasets у четырёх вариантов совпадают после
нормализации phandle; искусственных CPU отличий нет. Порядок DTB совпадает
с Sony Makefile. Это четыре восстановленных аппаратных описания с явными
activation guards, не подтверждение runtime поддержки или выбора загрузчиком.
F8131/F8132 не связаны жёстко с stepping, PMIC или панелью.

## Нерешённые P0 и следующие реализации

| Блокер | Точная причина и необходимое действие |
| --- | --- |
| RPM logical corners | Native `qcom_smd-regulator.c` передаёт µV и ACTIVE requests; Sony нужны logical 1..7 → wire 0..6, corn/vfc, BOTH/AO/floor max aggregation и ACK/rollback. Нельзя направить corner consumers на обычные s1/s2 µV providers. Требуется полноценный общий frontend; исходные запросы сохранены disabled. |
| UFS GCC/CX | Sony UFS AXI 200 MHz и ICE source 300 MHz требуют CX NOMINAL. Native GCC имеет rates, но не voltage-corner coupling. UFS host поэтому disabled. Восстановленная NoC цепочка не заменяет CX vote. См. [точный аудит GCC](dora-gcc-corners.txt). |
| CPU DVFS / четыре CPU | CCF поднимает напряжение раньше PRE notifier, тогда как Sony сначала переводит ACD на safe source. Нужна согласованная ACD→CPR/RPM→PLL транзакция с rollback и fuse plans. Read-only APCC не является DVFS. CPU_FREQ/CPU_IDLE выключены, maxcpus=2 сохранён; SPM и native S8..S11 ownership ещё нужно согласовать. См. [CPU port](dora-cpu-port.txt). |
| Дисплей | Native MMCC не голосует Sony vdd_dig corners для MDP/DSI. MDP probe может запросить 412.5 MHz/HIGH. MDP/DSI/PLL/SMMU/LAB/IBB/WLED поэтому disabled до корректного coupling. См. [display port](dora-display-port.txt). |
| GPU | Не завершены общий corner frontend, оригинальный physical S2 regulator backend, независимый MX vote, KGSL retention/SMMU/secure-firmware контракт. GFX child/CPR/buck остаются disabled, unregulated fallback исключён. См. [GPU/variants](dora-gfx-variants.txt). |
| UFS power-off recovery | Common PHY игнорирует часть ошибок отключения ресурсов. Возврат ошибки после частичного collapse оставляет generic PHY power_count=1; простой rollback может потерять calibration/PCS state. Требуется совместная host/PHY recovery state machine и fault tests; это не исправлено предположением. |
| Зарядка / USB | Реальные DPDM/BC1.2/ID-ADC/VBUS/OTG и ownership PHY не завершены. SMBcharger DT не активирован, fake fixed DPDM отсутствует. JEITA/temperature/current protections не отключались. См. [DPDM gap](dora-usb-dpdm-gap.txt). |
| Bootloader / Android image | Нет проверенного рабочего boot.img и настоящего LOS21 ramdisk для сравнения Sony header/signature/offsets/DT selection. Наличие четырёх FDT в Image.gz-dtb этого не доказывает. boot.img не создавался. |

Audio/DSP/modem/firmware/vendor ABI, Wi-Fi/PCIe DMA и остальные аппаратные
подсистемы также требуют дальнейшей интеграции и физической проверки.
Их прежние исходники и WPA2 исправления сохранены; завершение этих цепочек
на этапе 3 не заявляется.

## Условия дальнейшего теста

Сначала реализовать общий RPM corner frontend и coupling GCC/MMCC, затем
согласовать CPU ACD/CPR и остальные указанные контракты. Не снимать guards
ради успешного probe. Повторить clean build и fault/provider checks.

Для каждой модели отдельно нужны read-only снимки рабочего 4.4: реальные
msm/board/PMIC/LCD IDs, выбранный DT, cmdline, clock/regulator summaries и
pstore. Требуется офлайн разобранный рабочий boot.img и реальный LOS21
ramdisk, проверка размера/подписи/header и наличие проверенного пути TWRP
recovery с копией рабочего boot. Только после закрытия P0 обсуждать отдельный
диагностический boot test и способ сбора логов. При восстановлении затрагивать
только заранее согласованный boot; userdata, modem, TA, persist не менять.
Никаких операций прошивки, загрузки на телефон или форматирования не выполнено.

Runtime минимум: UFS link/I/O/integrity и suspend, реальные RPM ACK/rails,
все четыре CPU/thermal, пять панелей/touch/ESD, GPU firmware/ABI, Wi-Fi WPA2,
зарядные отсечки и firmware/DSP/modem. Для F8131 проверить Single SIM, для
F8132 оба слота и DSDS; принудительные ss/dsds в ядро не добавлены.

SMBcharger не привязан в DT. Это не доказывает, что загрузчик остановил
физическую зарядку: исходное состояние PMIC и реальные токи не измерялись.
Безопасная зарядка и обычное использование не подтверждены.
