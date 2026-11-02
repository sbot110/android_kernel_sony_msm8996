# Dora F8131/F8132 — матрица совместимости Linux 4.19

Обновлено: 2026-11-02. Источники этапа 3:
`799e29584b029fe34b8344a726989d4836d258d5`.
Итоговый статус: **BUILDABLE**; чистая сборка: **код завершения 0; vmlinux, Image/Image.gz, четыре DTB и Image.gz-dtb проверены**.
Это не BOOT TEST READY. Физической загрузки 4.19 и подтверждения LOS21 нет
ни для F8131, ни для F8132. [Протокол сборки](dora-build-validation.txt).

«Код/DT» ниже означает перенесённую и автоматически проверяемую реализацию
или набор hardware данных. Это не подтверждение probe или работы телефона.
«Отключено» означает явный final DT/Kconfig safety gate до завершения
конкретной реализации, а не поддержанную подсистему.

## Четыре SoC/PMIC варианта

Эталон — окончательно развёрнутые Sony DT из
[4.4.302 commit 0c295b15](https://github.com/sbot110/android_kernel_sony_msm8996/commit/0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1).
Все четыре набора перенесены с hardware различиями и проверяются по
`dora-variant-dt.json`, `dora-cpu-dt.json`, `dora-display-dt.json`.

| SoC / PMIC | msm-id revision | pmic-id, первые два слова | GPU CPR corners | BCL derating | Flash ramp |
| --- | --- | --- | --- | --- | --- |
| v3.0 / PMI8994 | `0x30000` | `0x20009 0x2000a` | 7 | 550000 uA | 6P7_US |
| v3.0 / PMI8996 | `0x30000` | `0x20009 0x10013` | 7 | 0 uA | 27_US |
| v3.1 / PMI8994 | `0x30001` | `0x20009 0x2000a` | 8 | 550000 uA | 6P7_US |
| v3.1 / PMI8996 | `0x30001` | `0x20009 0x10013` | 8 | 0 uA | 27_US |

Во всех случаях SoC ID 246, board-id `<8 0>`; последние слова pmic-id — 0.
IBB discharge в **итоговом** оригинальном DT равен 32 для обеих PMIC:
промежуточное PMI8996 значение 300 заменяется поздним Tone override.
Сохранены реальные GPU fmax/fuse/voltage/aging отличия v3.0/v3.1; конечные
CPU/HMSS datasets одинаковы во всех четырёх Sony деревьях. Искусственные
CPU различия не добавлялись. Flash и GPU voltage chains пока отключены.

Порядок appended DTB — v3.0/PMI8994, v3.0/PMI8996, v3.1/PMI8994,
v3.1/PMI8996, как в Sony Makefile. Формат/порядок проверяются офлайн;
правильный выбор загрузчиком ещё не доказан. **F8131/F8132 не определяют
PMIC, stepping или панель.** Подробнее: [GPU/варианты](dora-gfx-variants.txt).

## Состояние моделей отдельно

| Модель | Проверено в исходниках | Не подтверждено |
| --- | --- | --- |
| F8131 — Single SIM | Общая dora платформа; Android userspace выбирает `ss`; нет принудительного SIM режима в ядре | Холодная загрузка 4.19, выбранный DTB/LCD ID, весь hardware; SIM1, звонки/SMS/LTE/data и восстановление модема |
| F8132 — Dual SIM/DSDS | Общая dora платформа; Android userspace выбирает `dsds`; данные AUO под 4.4 сохранены отдельно | Тот же полный набор тестов 4.19; оба слота, DSDS standby, входящие на оба номера, смена data SIM, voice-call audio/recovery |

Ни модельные скрипты device/vendor, ни LTALabel, ни разделы телефона здесь
не изменялись. Общая source база не доказывает пригодность одного `boot.img`
для обоих аппаратов.

## Аппаратная матрица

| Подсистема | F8131 | F8132 | Подтверждённое состояние и следующий blocker |
| --- | --- | --- | --- |
| UFS / BIMC/NoC | Код/DT; UFS отключён | Код/DT; UFS отключён | Настоящая topology/RPM bandwidth и 12×2 vectors восстановлены. Нет GCC frequency→CX corner coupling; затем нужны link startup, ICE, suspend и проверка сохранности данных. |
| CPU / CBF / CPR / SAW | Наблюдение rates; voltage/DVFS отключены | То же | APCC match/decoder, HMSS/Kryo/APM frontends и bin data есть. Требуются ACD/CPR transaction, реальные RPM corners, SPM ownership, thermal/LPM. `maxcpus=2`, cpufreq/cpuidle выключены. |
| MDSS / DSI / все 5 панелей | Граф/данные; отключено | Граф/данные; отключено | Настоящие clocks/PLL/SMMU/bus/GDSC/LAB/IBB/WLED и GPIO подключены в DT. Требуется MMCC digital-corner voting до любой активации. |
| Adreno 530 / KGSL | Данные/child clocks; отключено | То же | Реальные variant CPR/fuse/frequency планы и исправлен provider cycle. Нет полного CX/MX/PMI S2/retention/SMMU/secure ABI контракта. |
| BCM4359 C0 | Код/PCIe DT; runtime нет | То же | Sony BCMDHD и WPA2/AKM/cipher fixes сохранены. Нужны link training, DMA/IOMMU, firmware/NVRAM, CCMP/reconnect/suspend/wakeup. |
| Clearpad / incell | Код; нет полной touch DT связи | То же | Sony MDSS/incell API есть; I2C12/controller/питание и согласование с panel sequencing ещё не завершены. |
| VADC / FG / BCL | Код/PMIC данные; runtime нет | То же | Оригинальные каналы/профиль/JEITA и PMIC/BCL overrides сохранены. Требуются измерения, calibration, IRQ и температурные тесты. |
| SMBcharger / BC1.2 / OTG | Код; зарядная цепь отключена | То же | Нет согласованного настоящего DPDM/USB PHY/VBUS/MicroUSB ID/ADC provider. Защиты не отключены, fixed DPDM отсутствует. |
| USB / ADB / MTP | Код/peripheral DT; runtime нет | То же | Sony QUSB2 tuning и удаление ложного ID сохранены. Реальные cable/role/VBUS/OTG и charging ownership не завершены. |
| Tasha / WCD9335 / audio | Драйверы; DT/firmware неполны | То же | Нужны LPASS/PIL/SSR/SLIMbus/clock маршруты и firmware, затем media, mic/headphone/speaker и voice calls. |
| Modem / радио | Полной цепочки нет | Полной цепочки/DSDS нет | MPSS firmware/PIL/SSR, SMD/GLINK, RMNET/IPA и RIL ABI требуют завершения. Ни calls, ни data не подтверждены. |
| Камеры | Не интегрированы полностью | То же | Sensor/CCI/ISP/EEPROM/actuator и vendor ABI. |
| Suspend / thermal / 4 CPU | Не подтверждены; P0 | То же | Source completion CPU/power domains и physical logs; наличие TSENS/SUSPEND не подтверждает throttling или power collapse. |

Bluetooth/FM, GNSS, NFC, fingerprint, датчики и вибромотор не прошли полную
отдельную ревизию driver/DT/firmware. Поддержка по одному наличию исходников
не заявляется. [CPU](dora-cpu-port.txt), [UFS/GCC](dora-gcc-corners.txt),
[дисплей](dora-display-port.txt), [USB/DPDM](dora-usb-dpdm-gap.txt).

## Панели и контрольные данные 4.4

| Панель | Panel Name | LCD ADC диапазон, включительно |
| --- | --- | --- |
| AUO Synaptics | 4 | 1087000–1231000 |
| JDI Synaptics | 6 | 353000–653000 |
| LGD Synaptics | 8 | 215000–256000 |
| Sharp ID3 | 3 | 1236000–1395000 |
| Sharp ID9 | 9 | 0–57000 |

Исходные panel файлы сохранены побайтово; финальный DT содержит все пять
панелей с оригинальными 1080p60 командами и PHY timings. Выбор идёт по
`lcdid_adc`; preferred node служит якорем перечисления, его wildcard ADC
range удалён. Missing/invalid/unknown/ambiguous ID прекращает выбор.
ADC=0 является штатным значением Sharp ID9 и не приравнивается к отсутствию
параметра. Режимы 120 Hz и dynamic FPS/clock/mode switching не включены.

Данные пользователя относятся **только к F8132 с рабочим Sony 4.4**:

```text
lcdid_adc=0x11FFEA = 1179626 -> AUO, Panel Name=4
1080x1920 @ 60 Hz
bitclk=897039360; byteclk=112129920
pclk=149506560; escclk=19200000
```

Соотношения `bitclk=8*byteclk=6*pclk` согласованы. Эти числа не назначены
остальным панелям и не измерены под 4.19. Автотесты выбора, данных и
скомпилированного графа не заменяют испытание каждой фактической панели.

## Android 14 и границы ABI

Проверка итоговой `.config` охватывает 285 явных назначений и 101
обязательный built-in, включая Binder/BinderFS, BPF/JIT/cgroup BPF,
PSI/cgroups, SELinux, seccomp, namespaces, memfd/ashmem, fscrypt,
ext4/F2FS, dm-verity/FEC, loop, OverlayFS, ION/dma-buf/fences и USB gadget.
Для обеих моделей runtime LOS21, vendor EGL/gralloc/ION/cache/secure heaps,
32-bit clients, firmware paths, SELinux policy и FCM остаются непроверенными.

BPF program-side map flags RDONLY_PROG/WRONLY_PROG не реализованы простым
добавлением UAPI констант. Старые bpfloader code 1, BoringSSL self-check и
vold-failed не объявлены устранёнными; для их диагноза нужны конкретные
binary/verifier/init/fs_mgr/crypto логи нового ядра. Механизмы безопасности
не отключаются для обхода ошибок. [Аудит LOS21](dora-android14-audit.md).

## Условия следующего этапа

Сначала завершить настоящую RPM corner aggregation и связь GCC/MMCC/CPU
clock transitions с питанием, затем последовательно снимать соответствующие
safety gates и повторять host/DT/component/full-build checks. Подтвердить
boot header/размер/cmdline/ramdisk и Sony appended-DTB selection по рабочему
образу; подготовить образ только с реальным LOS21 ramdisk и планом
восстановления. Пока source P0 остаются, `boot.img` не готовится.

Будущие испытания требуют отдельных F8131 и F8132: реальные IDs/выбранный DT,
pstore/early log, UFS/температуры, панели/touch/GPU, Wi-Fi/USB, audio/radio,
зарядные защиты и suspend. Нужны аппараты с каждой штатной панелью и
представленные SoC/PMIC варианты. Прошивки, форматирования, записи TA,
persist, modem или userdata на этом этапе не выполнялось.

SMBcharger не привязан в DT. Это не доказывает, что загрузчик остановил
физическую зарядку: исходное состояние PMIC и реальные токи не измерялись.
Безопасная зарядка и обычное использование не подтверждены.
