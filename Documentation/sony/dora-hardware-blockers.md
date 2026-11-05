# Dora Linux 4.19: блокеры после этапа 7

Обновлено: 2026-11-05. Начальный HEAD этапа:
`b8a20dab81b8f891fdb6f9ec935c5183549b18c8`.
Эталон Sony 4.4.302: `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`.

Ни F8131, ни F8132 не загружались с этим ядром. Первая диагностическая
загрузка не подготовлена; boot.img на этом этапе не создаётся.
Фактическая сборка/проверенные SHA: [dora-build-validation.txt](dora-build-validation.txt).

## Реальные изменения

Подтверждён SDHC1 eMMC `7464900.sdhci`, восстановлены оба SDHCI hardware
описания и исходные clocks/rails/pads/HS400/NoC vectors. SDHC2 — MicroSD/
hybrid UIM2; aliases и GPIO ownership не зависят от probe order. UFS host
и PHY отключены: предыдущая гипотеза о загрузке Dora с UFS была ошибочной.
[Доказательства и пределы](dora-storage-audit.md).

Добавлены настоящий logical RPM corner frontend, 56 GCC/49 MMCC исходных
Sony fmax maps и forced ACTIVE/SLEEP ACK перед clock operations. Обработаны
ошибки CCF prepare/rate/parent, частичное применение, удержание voltage
leases, bootstrap/handoff и teardown. Host-тесты исполняют production code;
они не измеряют напряжение или работу firmware телефона.

CPU transaction engine проверяет ACD/lease/PLL/readback/rollback и исходные
12 fuse plans. Native MMIO callbacks теперь реализованы, но live adapter
не подключён без настоящего shared power owner. Native RPM S8–S11
отключены, чтобы не конкурировать с четырёхфазным SPM-controlled APCC.
maxcpus=2 и выключенные CPU_FREQ/CPU_IDLE сохранены.

Сохранены 145-node NoC/BIMC, 33 PMIC boot pin states, пять LCD-ID панелей
1080p60, GPU/PMIC variant data и предыдущие BCMDHD WPA2/ION/USB/UFS fixes.
Не включены AUO120, dynamic switching, GPU или незавершённая зарядка.

Этап 5 добавляет физические S4/L12/L13/L20/L21 UV/MA/SWEN requests,
полные ACTIVE/SLEEP ACK, typed readiness/sync, отдельное recovery известного
baseline и bank-wide PM freeze/unfreeze. Unknown initial state не заменяется
нулевым baseline. Общее владение tuple распространяется на native, corner и
physical frontends; reprobe не снимает tombstone после попытки записи.
Regulator core исполняет replay aggregate load под настоящим lock.

Все четыре DT восстанавливают исходное RPM-managed input delegation.
Неверная связь L20/L21 2,95 В с native S5 2,15 В удалена; другой VIN не
угадывается. Provider always-on S4 удалён; исходная eMMC IO retention
остаётся consumer policy. L12 явно получает 1,8 В / временные 10 мА и
свежий BOTH ACK перед clock operation. Удаление proxy load требует
завершённого CCF handoff и известного ownership, с восстановлением cached
10 мА и quarantine при ошибке. Это протокол запросов, не измерение voltage.

Read-only APCC теперь распознаёт исходный AUX mux 0x3c, ждёт GPLL0 provider
и отвергает нестабильные/незалоченные PLL snapshots. Частоты и напряжения
не программируются. Исправлены minidump count/aux offsets/cleanup, DHD VLA,
SDHCI debug input/PM handling и ARM32 GNU-stack/SLUB varargs.

Stage 6 подключает настоящее чтение QFPROM и строгий выбор трёх CPU plans
для source speed bin, исправляет недопустимый NVMEM offset29 на byte-relative
`0x133/1,bits5:3`. Частоты/напряжения не изменяются. Writable CPU adapter
по-прежнему отсутствует. CCF теперь допускает same-owner reattach только
единственного чистого HIGH bootstrap vote с новыми ACK; потерянные per-clock
votes и аппаратная quarantine не восстанавливаются этим API.
Подробности: [CPU](dora-stage6-cpu-hardware.txt),
[CCF](dora-stage6-clock-ownership.txt),
[early boot](dora-stage6-early-boot.md).

Stage 7 добавляет настоящие APCC/CBF mux/PLL и target-cluster ACD callbacks,
проверяет source warm-PLL handoff до записи, объединяет reservation трёх
domains и исполняет их с production transaction engine в host tests.
Полного CPR/SPM/APM shared-gang backend нет; live clock provider остаётся
read-only. SPM ошибки больше не вызывают direct-SPMI fallback, main/AVS
операции сериализованы, unknown state quarantined. Watchdog probe/IRQ/
worker lifetime исправлены, но activation/handoff остаются gated. У Dora
USB PHY init явно блокируется до опасных hardware операций, поэтому USB/
ADB не являются доступной консолью. Read-only boot artifact parser не
подменяет отсутствующий рабочий boot.img или наблюдения телефона.
Подробности: [CPU/shared rails](dora-stage7-cpu-hardware.md),
[watchdog/USB/boot](dora-stage7-early-boot.md), [отчёт](dora-stage7-report.md).

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

## Ограничения и нерешённые P0

| Зависимость | Точная граница и следующее действие |
| --- | --- |
| eMMC / physical rails | `qcom-rpm-physical.c` реализует UV/MA/SWEN BOTH replay. `sdhci-msm.c` сохраняет host quarantine после uncertain ACK. Source qualification clock/IRQ/PM/shutdown завершена; добавлены отдельные experimental SDHC1 DT. Нужны аппаратные CID/GPT/by-name/HS200/HS400/data-integrity тесты и проверка полного boot chain. Основные SDHC отключены; cached rollback не измеряет PMIC. |
| GCC/MMCC bootstrap | `clk-msm8996-voltage.h:msm8996_voltage_ownership` сохраняет HIGH, если работающий PLL/parent не имеет доказанного counted owner/rate. Нужно подтвердить реальный boot handoff; partial-registration unwind с sole healthy HIGH теперь допускает same-owner reattach после fresh typed ACK. Потерянные per-clock leases/parent ownership и failed ACK остаются заблокированными; автоматического снятия hardware quarantine нет. Typed CX frontend включён: активный GCC не должен ссылаться на disabled supply. |
| CPU DVFS / четыре CPU | `clk-cpu-8996.c` остаётся observational provider. Native ACD/CBF/PLL callbacks реализованы и gated. Нет настоящего transferable shared-gang CPR/SPM/APM guard, voltage leases/parents, HMSS/Kryo/retention и thermal/hotplug/LPM integration. SPM instance lock не заменяет gang ownership; quarantine/reprobe и SAW per-CPU lifetime требуют завершения до activation. |
| MDSS / MMCC analog | `msm8996_voltage_apply` получает typed L12 BOTH ACK и quarantines uncertain operation. Отдельный backend baseline recovery не восстанавливает clock ownership/counts. Нужны qualified clock-level recovery, DSI PHY analog/panel/incell ownership и реальные измерения до display activation. |
| GPU Adreno 530 | Gated child provider/MMSS CPR/PMI S2, независимый MX и CX, retention/SMMU/secure firmware и userspace ABI остаются неполными. Нельзя применять SDM845 планы. |
| USB / зарядка | Не завершены DPDM/PHY ownership, BC1.2/VBUS, MicroUSB ID/ADC и SMBcharger safety paths. Sony PHY init возвращает EOPNOTSUPP до rails/clocks/reset/MMIO; USB/ADB недоступны. Parent qscratch VBUS override и firmware charging не квалифицированы. Нет fake fixed DPDM; JEITA/thermal/current protections не отключены. |
| Watchdog / logging | Probe/IRQ/worker/resource errors исправлены и fault-tested. Source final Tone/official prebuilt: base 0x9830000/0x1000, SPI28/29, bark12s/pet10s (11s был generic intermediate). Нет APSS watchdog node, QCOM_WATCHDOG_V2 выключен в унаследованной конфигурации; bootloader/secure handoff остаётся P0. UART node/config и ramoops reservation не доказывают физический serial transport или persistence. |
| Android boot | Не предоставлены фактический рабочий boot.img, LOS21 ramdisk и новые runtime логи. Требуются boot header/partition size/cmdline/DTB-selection/vendor checks. Сохранённая DT bootargs сама не доказывает итоговую cmdline загрузчика. |

Generic UFS host/PHY recovery остаётся отдельной задачей общей MSM8996 базы,
но UFS не является установленным Dora storage и не используется для обхода
неполной eMMC. Обе MMC цепочки, MX, GPU/CPR/PMI S2 и display power chain
остаются явно gated. Это не BOOT TEST READY.

## Независимые проверки и физические пределы

Все 52 check-*.py включены в build.sh; все 47 проверок Stage6 сохранены.
Точное число и результаты — в протоколе сборки. Новые
тесты выполняют MMC alias/UIM2/provider/power helpers, physical/cache ACK
расхождения, RPM corner aggregation/rollback/lifetime/concurrency, CCF
transactions, CPU transaction engine и реальные Android ABI/filesystem
functions. Отрицательные DT fixtures отвергают снятые gates, duplicate
owners, неверные supplies/endpoint/corners и обход maxcpus.

Без отдельных F8131/F8132 нельзя подтвердить выбранный DTB, раннюю загрузку,
напряжение/температуры, eMMC/SD tuning и data integrity, encrypted userdata,
каждую панель/touch/GPU, WPA2/reconnect, USB/charging, suspend, audio/voice,
модем/DSDS или vendor runtime. Старые bpfloader/BoringSSL/vold ошибки не
объявлены устранёнными. Механизмы безопасности не отключались.

SMBcharger не привязан в DT. Это не доказывает, что загрузчик прекратил
физическую зарядку: исходное PMIC состояние и токи не измерены. Разделы
userdata/modem/TA/persist не менялись; прошивки и форматирования не было.

SDHC1 source qualification и точные error/PM зависимости: [протокол](dora-sdhc1-qualification.md). Экспериментальные DT не входят в основной Image.gz-dtb и не создают boot.img. При host fault SDCLK останавливается, неопределённые rail votes удерживаются; это не доказательство физического выключения питания.
