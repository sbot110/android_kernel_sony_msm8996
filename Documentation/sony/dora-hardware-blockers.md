# Dora Linux 4.19: блокеры после этапа 4

Обновлено: 2026-11-03. Начальный HEAD этапа:
`8d7ab8a3baeb73cfaea264f3f7758e32cffce097`.
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
12 fuse plans, но hardware adapter не подключён. Native RPM S8–S11
отключены, чтобы не конкурировать с четырёхфазным SPM-controlled APCC.
maxcpus=2 и выключенные CPU_FREQ/CPU_IDLE сохранены.

Сохранены 145-node NoC/BIMC, 33 PMIC boot pin states, пять LCD-ID панелей
1080p60, GPU/PMIC variant data и предыдущие BCMDHD WPA2/ION/USB/UFS fixes.
Не включены AUO120, dynamic switching, GPU или незавершённая зарядка.

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

## Нерешённые P0

| Зависимость | Точная граница и следующее действие |
| --- | --- |
| eMMC / physical rails | `sdhci-msm.c` power_error и bus vote quarantine запрещают продолжение после uncertain ACK. `qcom_smd-regulator.c:rpm_reg_write_active` не даёт consumer API для полного UV/SWEN/load replay и SLEEP. Нужен настоящий provider contract, затем проверка интегрированной цепочки и снятие CX/SDHC gates. Cached rollback не является восстановлением PMIC. |
| GCC/MMCC bootstrap | `clk-msm8996-voltage.h:msm8996_voltage_ownership` сохраняет HIGH, если работающий PLL/parent не имеет доказанного counted owner/rate. Нужно подтвердить реальный boot handoff и поведение late/deferred probe; не угадывать idle состояние. Typed CX frontend включён: активный GCC не должен ссылаться на disabled supply. |
| CPU DVFS / четыре CPU | `clk-cpu-8996.c` остаётся observational provider. Новый `clk-msm8996-cpu-transaction.c` не имеет MMIO/CPR/SPM adapter. Нужны native ACD/CBF/PLL callbacks, fuse/binning, S8–S11/SPM ownership, HMSS/Kryo/APM и thermal/hotplug/LPM. |
| MDSS / MMCC analog | `msm8996_voltage_apply` permanently quarantines uncertain L12 operation: digital ACK не подтверждает physical SWEN. Нужны L12/SLEEP replay, DSI PHY analog/panel/incell ownership, затем display activation и реальные измерения. |
| GPU Adreno 530 | Gated child provider/MMSS CPR/PMI S2, независимый MX и CX, retention/SMMU/secure firmware и userspace ABI остаются неполными. Нельзя применять SDM845 планы. |
| USB / зарядка | Не завершены DPDM/PHY ownership, BC1.2/VBUS, MicroUSB ID/ADC и SMBcharger safety paths. Нет fake fixed DPDM; JEITA/thermal/current protections не отключены. |
| Android boot | Не предоставлены фактический рабочий boot.img, LOS21 ramdisk и новые runtime логи. Требуются boot header/partition size/cmdline/DTB-selection/vendor checks. Сохранённая DT bootargs сама не доказывает итоговую cmdline загрузчика. |

Generic UFS host/PHY recovery остаётся отдельной задачей общей MSM8996 базы,
но UFS не является установленным Dora storage и не используется для обхода
неполной eMMC. Обе MMC цепочки, MX, GPU/CPR/PMI S2 и display power chain
остаются явно gated. Это не BOOT TEST READY.

## Независимые проверки и физические пределы

32 check-*.py включены в build.sh; сохранены исходные 20 проверок. Новые
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
