# Dora 4.19: аппаратный аудит и условия первого теста

Дата: 2026-11-01. Исходный HEAD этой работы:
`c06cad9740c8cfc590807c256b45af4f6485008c`.
Эталон Sony 4.4: `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`.
Статус — **BUILDABLE**, не BOOT TEST READY. Завершение аппаратного порта
не достигнуто. F8131 и F8132 не загружались с этим ядром.

## Четыре аппаратных варианта

Все четыре оригинальных DTS заново препроцессированы GCC и скомпилированы
dtc 1.6.1 с кодом 0 из неизменённого эталона 4.4. Для сравнения использован
итоговый flattened DT, поскольку последующие Tone overrides меняют часть
свойств из общих PMIC includes.

| SoC / PMIC | msm-id revision | pmic-id (первые два слова) | GPU CPR corners | BCL inhibit-derating-ua | Flash ramp up/down |
| --- | --- | --- | --- | --- | --- |
| v3.0 / PMI8994 | 0x30000 | 0x20009 0x2000a | 7 | 550000 | 6P7_US |
| v3.1 / PMI8994 | 0x30001 | 0x20009 0x2000a | 8 | 550000 | 6P7_US |
| v3.0 / PMI8996 | 0x30000 | 0x20009 0x10013 | 7 | 0 | 27_US |
| v3.1 / PMI8996 | 0x30001 | 0x20009 0x10013 | 8 | 0 | 27_US |

У всех SoC ID 246, board-id `<8 0>`, последние два слова pmic-id равны 0.
**IBB discharge в конечных четырёх Sony DT равен 32.** Включаемый файл
msm-pmi8996.dtsi задаёт 300, но более поздний Tone override заменяет его
на 32. Перенос 300 по одному include не соответствовал бы эталону Dora.
v3.0 также меняет GPU clock corner mapping, CPR floor/ceiling, fuse/RO
quotients и убирает aging properties; это не просто новая строка ID.

В 4.19 остаётся один DTB v3.1 + PM8994/PMI8994. Для остальных трёх
не восстановлен проверенный контракт GPUCC/CPR/PMIC/flash с драйверами этой
базы. Отсутствие связанного provider — задача реализации, а не отсутствие
Sony исходников. Фиктивные варианты с изменёнными ID не добавлены.
SIM-модель не определяет эти четыре сочетания.

## CPU, питание и thermal

В текущем DT четыре Kryo CPU с MPIDR 0, 1, 0x100, 0x101 и PSCI. Сохранён
`maxcpus=2`. Разрешённая конфигурация имеет `NR_CPUS=4`, но CPU_FREQ и
CPU_IDLE выключены. Нельзя заявлять DVFS, schedutil, power collapse или
thermal throttling по одному наличию TSENS и общего suspend-кода.

Узел `qcom,apcc-msm8996` присутствует в SoC DTS, но соответствующего
clock-driver match в drivers/clk/qcom этой базы нет. CPU не имеют связей
clocks/OPP/regulator/power-domain. Generic GCC/MMCC и RPM-регуляторы
не заменяют CPU/CBF clocks, SAW и CPR3 с fuse/binning.

В итоговом Sony v3 DT cpufreq-table-0 содержит 16 частот 307200–1593600 kHz,
table-2 — 25 частот 307200–2150400 kHz. Эти диапазоны не разрешают выставлять
максимум каждому кристаллу: соответствующие speedbin1/2/3 plans имеют другие
пределы и voltage corners. В 4.19 эти частоты или напряжения не назначены.
Для снятия лимита CPU нужны полноценные providers, проверка fuse/binning,
порядка voltage/clock переходов, CPU bring-up и thermal/suspend на устройстве.

## Состояние provider-цепочек

| Подсистема | Подтверждено в исходниках / сборке | Блокирует завершение |
| --- | --- | --- |
| IRQ, clocks, PMIC | Скомпилированные GCC/MMCC/RPM/SPMI; структурная проверка ссылок | Структура phandle не подтверждает семантику clock ID, probe или электрическое питание |
| UFS / SD | Восстановлен настоящий Sony UFS QMP14nm PHY, Native UFS/SDHCI, supplies и clocks | Отсутствуют обязательные UFS bus vectors и полная BIMC/NoC topology; probe блокируется. ICE, DMA/IOMMU, PMIC/stepping, suspend и сохранность данных не проверены |
| PCIe / BCM4359 | Реальный vendor host, QMP/GCC, Sony platform glue и INTx | ANOC SMMU/bandwidth topology, link training, firmware/NVRAM, WPA2 и wake требуют проверки |
| Дисплей / touch | MDSS и Sony panel/incell/Clearpad скомпилированы; все 5 LCD ID диапазонов сохранены | Нет активной MDSS→DSI→panel цепочки с bus/SMMU/clocks/power/reset/backlight; I2C12 touch ещё не подключён |
| Adreno 530 | KGSL содержит A530 v2/v3 firmware names и hardware tables | Нет активного GPU node/provider-цепочки; нужны MSM8996 GPUCC/CPR, IOMMU, secure firmware, heap/cache/ioctl/fence ABI с vendor EGL/gralloc |
| Аудио | Sony MSM8996/Tasha machine, WCD9335 и APRv2/Q6 собраны | Нет завершённых sound/SLIMbus/LPASS/PIL/SSR/firmware DT-связей; наличие upstream adsp-pil node без QCOM_ADSP_PIL в .config не запускает DSP |
| Modem | Частичные transport/SMP2P исходники | Нет полной MPSS firmware/PIL/SSR/SMD/GLINK/IPA/RMNET/RIL цепочки; звонки и радио не подтверждены |
| Батарея / зарядка | Реальные VADC/BCL/FG/SMBcharger собраны, 32 PMIC/profile узла сверены | SMBcharger DT не активирован: нет согласованных реальных DPDM, QC power_supply, microUSB ADC/ID, VBUS/OTG и PHY ownership |

Проверка итоговой конфигурации дополнительно выявила выключенные реальные
providers: QUSB2, PMIC GPIO/MPP, RPM GLINK, APCS mailbox и simple-pm-bus.
Они включены, поскольку соответствуют уже активному DT. simple-pm-bus
необходим для создания дочернего PCIe platform device, а RPM GLINK/APCS
для RPM clock/regulator transport. Теперь проверяется и наличие их настоящих
реализаций в System.map. Это не устраняет отсутствующую NoC topology.
Подробности восстановленного UFS14nm и ошибок provider/init/unwind:
[dora-ufs-port.txt](dora-ufs-port.txt).

В этом изменении исправлены два USB несоответствия: native QUSB2 использует
точные Sony TUNE1/2/3/4 `d0/a3/83/c7`; GPIO24 больше не объявлен цифровым
ID extcon. Sony USB_DETECT — output в схеме ADC/pullup. DWC3 остаётся
high-speed peripheral с VBUS override; это не обнаружение реального кабеля
и не готовая зарядка. Ненулевой nvmem trim сохраняет приоритет над TUNE2.

Дополнительная сверка `sjllls/android_kernel_sony_msm8996`, ветка
lineage-18.1-LA.UM.8.4, blob `834e111e654d4277f64a35512ea597ddd56ae482`
msm8996-tone-common.dtsi, подтверждает те же десять QUSB2 register/value
пар. Код другого SoC из дополнительных 4.9 деревьев не использован как
замена отсутствующим MSM8996 providers.

`sbot110/android_device_sony_dora` main/BoardConfig.mk (blob
`d6e729b2a4190dd509bc69b8264220cbb1f4ea1e`) подтверждает F8131/F8132
и tone_dora_defconfig. `android_vendor_sony` main/tone-common/
tone-common-vendor.mk (blob `7d49072866453b21d229016959649eb2fe0b2b32`)
перечисляет fw_bcmdhd.bin/bcmdhd.cal по путям defconfig и A530 v2/v3 firmware
names из KGSL. Dora vendor makefile содержит BCM4359C0.hcd и touch images
0x90–0x94. Makefile записи не проверяют содержимое, подлинность, DSP/modem
firmware из отдельных разделов, фактическое монтирование firmware paths
или ABI бинарных библиотек. Ни device, ни vendor исходники не изменены.

Выбор панели теперь прекращается при отсутствующем/некорректном/неизвестном
LCD ID, malformed или перекрывающихся диапазонах. Предпочтительная панель
не остаётся неявным fallback. OF references сбалансированы. Все пять
штатных диапазонов, включая Sharp ID9 с ADC 0, работают в host-модели
реальной функции; это не электрический тест DSI. Данные 1080p60 сохранены,
AUO 120 Hz и dynamic switching не активированы.

## F8131 и F8132

F8131: Single SIM определяется Android userspace; ядро/DT не получило
принудительный `ss`. Требуются реальные IDs, загрузка, SIM1, calls/SMS/data
и полный общий аппаратный набор тестов.

F8132: Dual SIM/DSDS определяется отдельно Android userspace; ядро не
получило принудительный `dsds`. Дополнительно нужны оба слота, DSDS standby,
смена data SIM, входящие на оба номера и voice-call recovery. Данные AUO
с F8132 под 4.4 не подтверждают поведение 4.19.

## Диагностика до первого разрешённого теста

1. На работающем 4.4 собрать read-only снимок cmdline, корневых msm/board/PMIC
   IDs, LCD ID, DT, firmware paths, regulator/clock summaries и pstore.
   Не очищать разделы или логи для этой проверки.
2. Офлайн разобрать известный рабочий Sony boot.img: header/signature,
   offsets, cmdline, ramdisk, 64-MiB лимит раздела и настоящий механизм
   выбора appended DTB. Один корректный FDT в Image.gz-dtb этого не доказывает.
3. Закрыть перечисленные критические provider-связи и проверить реальные
   firmware/vendor ABI. Подготовить boot.img только с настоящим LOS21
   ramdisk; полный checkout LOS21 и ramdisk в окружении отсутствуют.
4. После отдельной готовности тестового образа использовать согласованный
   способ временной загрузки и доступный путь восстановления/сбора pstore.
   Здесь никаких команд прошивки или загрузки на телефон не выполнялось.
5. Первые тесты раздельны для каждого реального SoC/PMIC и каждой модели:
   boot/storage/temperatures, затем панели/USB/WLAN/audio/radio и suspend.
   Зарядка и особенно QC/OTG требуют завершённой safety-цепочки до включения.

Проверки SELinux, dm-verity и BoringSSL не отключаются. Патч удаления
внешнего принудительного permissive параметра подготовлен, проверен на
точной версии BoardConfigCommon.mk и не применён к device/vendor репозиториям.
Разбор kernel/userspace причин LOS21 находится в dora-android14-audit.md.
