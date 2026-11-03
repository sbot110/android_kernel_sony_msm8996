# 07. Критический построчный аудит исправлений и устранение P0/P1-дефектов ядра Linux 5.15.220 (`dora`)

Настоящий отчёт документирует построчную ревизию (Этапы B и C) всех ранее внесённых изменений в ветке `kernel-5.5` (**Linux 5.15.220**, SoC Qualcomm MSM8996, **Sony Xperia X Performance `F8131` / `F8132`**) против эталонного дерева Sony 4.4.302 (`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`) и исходных кодов подсистем Linux 5.15.

Ни одна ошибка предыдущей итерации не скрыта: ниже явно зафиксированы как исправления коммита `c47e99b8d6a6`, так и дефекты первого приближения, выявленные при повторном аудите и устранённые в текущем этапе.

---

## 1. Аудит и доработка исправлений предыдущего этапа (Этап B)

### 1.1. `THIS_MODULE`, `/sys/module/bcmdhd/parameters/*` и lifetime в `somc-dora-platform.c`
- **Статус опасного кода:** Вызов `THIS_MODULE->mkobj.kobj.kset` полностью отсутствует в `drivers/platform/somc/somc-dora-platform.c`. При `CONFIG_SOMC_DORA_PLATFORM=y` макрос `THIS_MODULE` равен `NULL`, поэтому прежний код гарантированно вызывал разыменование нулевого указателя на ранней загрузке.
- **Найденный при аудите недочёт первой ревизии:** Использование пассивного `module_param_string()` создавало узлы `/sys/module/bcmdhd/parameters/{firmware_path,nvram_path}`, но никак не уведомляло `brcmfmac` об изменении пути, когда Android Wi-Fi HAL переключает прошивку между STA (`fw_bcmdhd.bin`) и SoftAP (`fw_bcmdhd_apsta.bin`). Кроме того, в `somc_dora_plat_remove()` не сбрасывался `disable_irq_wake()` и `device_init_wakeup(dev, false)`.
- **Реализованное решение:**
  - Сохранён штатный ядерный механизм `#undef MODULE_PARAM_PREFIX` + `#define MODULE_PARAM_PREFIX "bcmdhd."` без создания поддельных `kobject` в `/sys/module`.
  - Вместо `module_param_string` реализованы `module_param_cb(firmware_path, ...)` и `module_param_cb(nvram_path, ...)` под защитой `DEFINE_SPINLOCK(somc_bcmdhd_param_lock)` с обрезкой `\n`, проверкой длины и экспортом потокобезопасных функций `somc_dora_get_bcmdhd_fw_path()` / `somc_dora_get_bcmdhd_nvram_path()`.
  - Проверена корректность сборки как в режиме `=y` (через таблицу `__param` в `kernel/params.c`), так и в режиме `=m`.
  - В `remove()` добавлено симметричное освобождение `disable_irq_wake()` и `device_init_wakeup(dev, false)`.

### 1.2. Ramdisk (`XZ`/`LZ4`/`GZIP`), `PSI`, `CGROUPS` и техническое уточнение по `KASAN`
- **Фактическое состояние `.config` после `make ARCH=arm64 dora_defconfig`:**
  - `CONFIG_BLK_DEV_INITRD=y`
  - `CONFIG_RD_XZ=y` (критично для Sony S1 `ramdisk.cpio.xz` при `BOARD_RAMDISK_USE_XZ := true`)
  - `CONFIG_RD_GZIP=y`
  - `CONFIG_RD_LZ4=y`
  - `CONFIG_PSI=y`, `CONFIG_CGROUPS=y`, `CONFIG_MEMCG=y`
- **Командная строка ядра (`CONFIG_CMDLINE`):**
  - `CONFIG_CMDLINE="console=ttynull stack_depot_disable=on ioremap_guard"`, `CONFIG_CMDLINE_EXTEND=y`.
  - Флаг `cgroup_disable=pressure` полностью отсутствует как в `dora_defconfig`, так и в DTS `chosen/bootargs`.
- **Исправление неточности предыдущего отчёта по `KASAN` и `UBSAN`:**
  - *Корректировка формулировки:* Не все режимы `KASAN` требуют ARMv8.5-A. Аппаратного расширения ARMv8.5-A MTE требует **только** `CONFIG_KASAN_HW_TAGS`. Программные режимы `CONFIG_KASAN_GENERIC` и `CONFIG_KASAN_SW_TAGS` работают на ядрах Qualcomm Kryo (`ARMv8.0-A`, MSM8996) за счёт инструментации компилятора (`-fsanitize=kernel-address` / `kernel-hwaddress`), однако увеличивают размер образа `Image.gz` и расход ОЗУ, создавая риск выхода за бюджет 64 МБ раздела `boot` загрузчика Sony S1. Поэтому `# CONFIG_KASAN is not set` оставлен отключённым по соображениям бюджета S1 и производительности.
  - Дополнительно устранён реальный P0-риск `CONFIG_UBSAN_TRAP=y`: при включённом `UBSAN_TRAP` любое предупреждение UBSAN генерирует инструкцию `brk` (фатальный kernel oops при `CONFIG_PANIC_ON_OOPS=y`). Установлено `# CONFIG_UBSAN_TRAP is not set`.

### 1.3. Тачскрин Synaptics Clearpad S3320 (`rmi_i2c.c` + DTSI + `wakeup_gesture`)
- **Выявленные при построчном аудите дефекты первой ревизии:**
  1. В `somc-dora-platform.c` запись `1` в `/sys/devices/virtual/input/clearpad/wakeup_gesture` возвращала успех даже тогда, когда контроллер `rmi4_i2c` ещё не был привязан к I2C-клиенту (`fake success`).
  2. В `rmi_i2c.c` вызов `device_init_wakeup(&client->dev, true)` на `probe` включал `device_may_wakeup()` по умолчанию со старта системы, из-за чего в обычном `suspend` (при выключенном Tap-to-Wake) регуляторы `vdd` (3.0 В) и `vio` (1.8 В) никогда не отключались.
  3. В `rmi_i2c_suspend()` при ошибке `rmi_driver_suspend()` вызов `enable_irq_wake()` не откатывался, нарушая баланс wake IRQ.
  4. В `msm8996-sony-xperia-tone-dora-common.dtsi` узел `synaptics@2c` наследовал из базового `msm8996-sony-xperia-tone.dtsi` дочерний узел `rmi4-f11@11` одновременно с `rmi4-f12@12`, а линия `vio-supply` была подключена напрямую к `pm8994_s4` в обход ключа питания `panel_tvdd` (`tp_vddio_en`, GPIO 50 active-low).
- **Реализованное решение:**
  - В `somc_dora_sync_touch_wakeup()` добавлена строгая проверка наличия узла I2C, привязанного драйвера (`touch->dev.driver`) и `device_can_wakeup(&touch->dev)`. При отсутствии контроллера возвращаются честные коды `-ENODEV` / `-EAGAIN` / `-EOPNOTSUPP` без изменения `touch_wakeup_gesture`.
  - В `rmi_i2c_probe()` `device_init_wakeup(..., true)` заменён на `device_set_wakeup_capable(&client->dev, true)`: по умолчанию после загрузки wakeup выключен, регуляторы штатно гасятся в `suspend`, а включение `wakeup_gesture=1` активирует `device_wakeup_enable()` только по запросу userspace.
  - В `rmi_i2c_suspend()` проверка ошибки `rmi_driver_suspend()` перенесена **до** `enable_irq_wake()` и `regulator_bulk_disable()`; при полном отключении питания линия `reset_gpio` (GPIO 89) переводится в активный уровень удержания сброса (`1`), а на `resume` выполняется чистый аппаратный импульс сброса.
  - В `dora-common.dtsi` добавлен `/delete-node/ rmi4-f11@11;`, заданы `#address-cells = <1>; #size-cells = <0>;`, а `vio-supply` подключён через `&panel_tvdd` (`vin-supply = <&pm8994_s4>`).

### 1.4. Сканер отпечатков пальцев FPC1145 (`/dev/fingerprint` ABI Sony 4.4)
- **Выявленный критический дефект первой ревизии:** В коммите `c47e99b8d6a6` для `/dev/fingerprint` были объявлены произвольные команды `_IO('f', 1..4)`, тогда как проприетарный бинарный HAL Sony `fingerprint.msm8996.so` скомпилирован под контракт `drivers/input/misc/fpc1145_platform.c` из дерева Sony 4.4 (`0c295b15b6d0`), использующий магическое число `0x45` (`0x1145 & 0xff`)!
- **Реализованное решение:**
  - По эталонному файлу `drivers/input/misc/fpc1145_platform.c` (Sony 4.4) полностью воспроизведён оригинальный двоичный контракт `ioctl`:
    - `FPC_IOCWPREPARE` (`_IOW(0x45, 0x01, int)`) — управление питанием (`vdd_ana`, `vdd_io`, `vcc_spi`) и GPIO;
    - `FPC_IOCWDEVWAKE` (`_IOW(0x45, 0x02, int)`) — `dev_wakeup`;
    - `FPC_IOCWRESET` (`_IOW(0x45, 0x03, int)`) — аппаратный импульс `reset_gpio` (GPIO 21);
    - `FPC_IOCWAWAKE` (`_IOW(0x45, 0x04, struct fpc1145_awake_args)`) — удержание `pm_wakeup_ws_event()` с таймаутом;
    - `FPC_IOCRPREPARE`, `FPC_IOCRDEVWAKE`, `FPC_IOCRIRQ` (`_IOR(0x45, 0x83, int)` — чтение уровня GPIO 132), `FPC_IOCRIRQPOLL` (`_IOR(0x45, 0x84, int)` — блокирующее ожидание фронта IRQ с таймаутом 500 мс).
  - Добавлена защита от одновременного открытия (`open_excl`, возврат `-EBUSY`), очистка состояния в `.release` и симметричное управление wake IRQ в `PM_SLEEP`.
  - **Важное разграничение:** Наличие `/dev/fingerprint` и совпадение `ioctl`-контракта обеспечивают только сторону Linux-ядра (питание, сброс, IRQ, wakeup). Обмен по SPI0 выполняется внутри TrustZone QSEE (`tzbsp` / апплет отпечатка) через `qseecom` и **не может считаться подтверждённым без проверки на реальном устройстве**.

### 1.5. ADSP и SLPI `remoteproc` (`somc-dora-platform.c`)
- **Выявленные дефекты первой ревизии:**
  1. Запись `1` в `/sys/kernel/boot_adsp/boot` или `/sys/kernel/boot_slpi/boot` могла сохранять `adsp_booted = true` даже при ошибке или отсутствии `rproc`, а `show()` возвращал `rproc->state == RPROC_RUNNING || fallback_state`, маскируя падение DSP.
  2. Повторные записи `1` или `0` без строгого учёта владения ссылкой могли разбалансировать внутренний `atomic_t power` в `rproc_boot()` / `rproc_shutdown()`.
- **Реализованное решение:**
  - Введён строгий учёт удержания ссылки `*ref_held` под `rproc_lock`.
  - При `state == RPROC_CRASHED` перед повторным запуском при необходимости сбрасывается старая ссылка `rproc_shutdown()`, после чего вызывается `rproc_boot()`.
  - Любая ошибка `rproc_boot()` или отсутствие привязанного драйвера `qcom_q6v5_pas` (`-EAGAIN` / `-ENODEV`) немедленно возвращается в userspace (без возврата фиктивного `count`), а `show()` отражает исключительно реальное состояние `rproc->state == RPROC_RUNNING`.

### 1.6. Wi-Fi Broadcom BCM4359 (`brcmfmac` + Android Wi-Fi HAL)
- **Реализованное решение:**
  - В `drivers/net/wireless/broadcom/brcm80211/brcmfmac/pcie.c` присутствуют оба идентификатора PCIe: `BRCM_PCIE_4359_DEVICE_ID` (`0x43ef`) и `0x4359`.
  - В `drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.c` функции `brcmf_fw_request_sony_binary()` и `brcmf_fw_request_sony_nvram()` теперь считывают актуальные пути из `somc_dora_get_bcmdhd_fw_path()` / `somc_dora_get_bcmdhd_nvram_path()`, безопасно отрезают префиксы `/vendor/firmware/`, `/vendor/etc/firmware/`, `/oem/firmware/` (отклоняя любые строки с `..`) и загружают выбранный HAL-ом образ (`fw_bcmdhd.bin` для STA или `fw_bcmdhd_apsta.bin` для точки доступа SoftAP, а также `bcmdhd.cal`) через `request_firmware_direct()` без 60-секундных задержек usermode-helper.

### 1.7. Дисплей In-Cell 1080p60 и детекция `LCD_ID` (`panel-sony-dora-incell.c`)
- **Выявленные критические дефекты первой ревизии:**
  1. В таблицах команд инициализации `sharp_id3_on_cmds` и `sharp_id9_on_cmds` команда `0x29` (`MIPI_DCS_SET_DISPLAY_ON`) ошибочно отправлялась **до** `0x11` (`MIPI_DCS_EXIT_SLEEP_MODE` + 120 мс), что на панелях Sharp R63350 нарушает спецификацию MIPI DCS и оставляет дисплей чёрным.
  2. В `somc_dora_panel_prepare()` выполнялся одиночный импульс сброса вместо штатной двойной последовательности Sony 4.4 `somc,pw-on-rst-seq = <0 10>, <1 16>, <0 7>, <1 16>` из `dsi-panel-dora.dtsi`.
  3. В `msm8996-sony-xperia-tone-dora-common.dtsi` узел `&pmi8994_wled` наследовал из `pmi8994.dtsi` булево свойство `qcom,cabc;`. В драйвере `qcom-wled.c` это переключает все 3 гирлянды подсветки в режим внешнего ШИМ-стробирования CABC от панели, тогда как панели Dora отключают CABC (`0x55 0x00`), что приводило бы к **нулевой яркости подсветки (чёрному экрану)**.
- **Реализованное решение:**
  - Для Sharp ID3 и Sharp ID9 исправлен порядок команд: сначала `0x11` (`SLEEP_OUT` + 120 мс), затем `0x29` (`DISPLAY_ON` + 20 мс) — идентично AUO ID4, JDI ID6 и LGD ID8.
  - Воспроизведена точная двойная последовательность сброса GPIO 8 (`LOW 10ms -> HIGH 16ms -> LOW 7ms -> HIGH 16ms`) из Sony 4.4.
  - Проверены единицы измерения `qcom-spmi-vadc`: `IIO_CHAN_INFO_PROCESSED` в `qcom-vadc-common.c` возвращает напряжение в **микровольтах (`uV`)**, что строго совпадает с окнами `somc,panel-adc-windows-uv` (`[0..57000]`, `[215000..256000]`, `[353000..653000]`, `[1087000..1231000]`, `[1236000..1395000]`).
  - Добавлена проброска `-EPROBE_DEFER` как при получении канала `devm_iio_channel_get()`, так и при чтении `iio_read_channel_processed()`. Принудительный выбор AUO отсутствует: при физическом отказе ADC выводится явный `dev_warn` и выбирается нейтральный профиль `Default Panel` без искажения диагностики.
  - В `&pmi8994_wled` добавлена директива `/delete-property/ qcom,cabc;`.

---

## 2. Доработка аппаратных подсистем P0 / P1 (Этап C)

### 2.1. P0 Storage — устранение первопричины отказа eMMC `sdhc1` (`7464900.sdhci`) и `sdhc2` (`74a4900.sdhci`)
При сверке `arch/arm64/boot/dts/qcom/msm8996.dtsi` и `msm8996-sony-xperia-tone.dtsi` (где в апстриме оставлен комментарий `/* eMMC doesn't seem to cooperate even in PIO mode.. */`) с рабочим деревом Sony 4.4 (`msm8996.dtsi` + `drivers/mmc/host/sdhci-msm.c`) обнаружены три аппаратные причины неработоспособности `sdhc1` в базовом 5.15:
1. **Урезанное окно MMIO `hc_mem`:** В `msm8996.dtsi` для `sdhc1` было задано `reg = <0x07464900 0x11c>`, тогда как структура `sdhci_msm_v4` (`sdhci_msm_mci_offset`) обращается к регистрам `CORE_VENDOR_SPEC_CAPABILITIES0` (`0x11c`) .. `CORE_DDR_CONFIG` (`0x1b8`). На `sdhc2` размер окна составлял `0x314`, а в Sony 4.4 — `0x500`.
2. **Отсутствие аппаратного сброса `GCC_SDCC1_BCR` / `GCC_SDCC2_BCR`:** Без свойства `resets = <&gcc GCC_SDCC1_BCR>;` функция `sdhci_msm_gcc_reset()` в Linux 5.15 пропускает сброс блока SDCC1, оставляя контроллер в состоянии PIO/FIFO после загрузчика Sony S1 и вызывая таймауты ADMA/HS400.
3. **Тактирование XO:** Переключено с фиксированного `&xo_board` на управляемый `<&rpmcc RPM_SMD_XO_CLK_SRC>`.

Все три исправления внесены в `&sdhc1` и `&sdhc2` в `msm8996-sony-xperia-tone-dora-common.dtsi`.

### 2.2. P0 Power, Regulators, Clocks и Kryo CPUFreq (`dora_defconfig` + DTSI)
- **Критическая находка в Kconfig CPUFreq:** В Linux 5.15 опция `CONFIG_ARM_QCOM_CPUFREQ_NVMEM=y` создаёт только платформенное устройство `cpufreq-dt`, но **не выбирает (`select`) сам драйвер `CONFIG_CPUFREQ_DT`**! Без `CONFIG_CPUFREQ_DT=y` масштабирование частот кластеров Kryo Silver/Gold на MSM8996 не активировалось. В `dora_defconfig` добавлены:
  - `CONFIG_CPUFREQ_DT=y`
  - `CONFIG_QCOM_SPM=y` (управление питанием подсистем SAW2/SPM)
  - `CONFIG_MSM_GCC_8996=y` (явная фиксация главного тактового контроллера встроенным драйвером)
- **Ограничения напряжений регуляторов Dora из Sony 4.4:** В `msm8996-tone-dora-common.dtsi` (4.4) относительно базовой платформы `tone` жёстко ограничены верхние пороги двух LDO камеры/периферии во избежание перенапряжения:
  - `pm8994_l17`: `2.2 В` (`<2200000>`, вместо `2.5 В` в `tone.dtsi`);
  - `pm8994_l27`: `1.0 В` (`<1000000>`, вместо `1.2 В` в `tone.dtsi`);
  - `pm8994_l18`: добавлен `regulator-allow-set-load;` для LPM/HPM переключения FPC1145/RF.

### 2.3. P1 Android 14 / 15 Userspace Contract (`dora_defconfig`)
Проверены и включены недостававшие системные вызовы и пространства имён современного Android (`init`, `zygote`, `artd`, `vold`, `netd`):
- `CONFIG_PID_NS=y` (песочницы процессов и контейнеризация Android 14/15);
- `CONFIG_RSEQ=y` (Restartable Sequences для аллокатора Scudo / Bionic в Android 14/15);
- `CONFIG_FHANDLE=y` (`name_to_handle_at` / `open_by_handle_at`);
- `CONFIG_CRYPTO_AES=y`, `CONFIG_CRYPTO_SHA256=y`, `CONFIG_CRYPTO_SHA512=y` наряду с ARM64 CE ускорителями, `CONFIG_FS_ENCRYPTION=y`, `CONFIG_DM_VERITY=y`, `CONFIG_ANDROID_BINDERFS=y`, `CONFIG_CGROUP_BPF=y`, `CONFIG_DMABUF_HEAPS_SYSTEM=y`, `CONFIG_DMABUF_HEAPS_CMA=y`.

---

## 3. Дополнительный глубокий аудит скомпилированных `.dtb` и драйверов (Этапы B и F — вторая волна верификации)

При декомпиляции собранных `.dtb` (`dtc -I dtb -O dts`) и перекрёстной сверке исходников ядра 5.15 с эталоном Sony 4.4 (`0c295b15b6d0`) выявлены и устранены ещё 4 реальных аппаратных дефекта:

### 3.1. P0 Early Boot — устранение немого `SError` на серийных `F8131` / `F8132` из-за `earlycon=msm_serial_dm,0x75b0000`
- **Проблема:** Базовый `msm8996-sony-xperia-tone.dtsi` задаёт в `/chosen/bootargs` параметр `earlycon=msm_serial_dm,0x75b0000`. На розничных (retail) аппаратах `F8131` и `F8132` без сервисного флага UART в разделе Sony TA загрузчик S1 оставляет тактирование блока `BLSP2 UART2` (`0x075b0000`) выключенным. Попытка ранней консоли `earlycon` записать байт в нетактированный MMIO-регистр происходит **до** инициализации `gcc-msm8996` и **до** регистрации `ramoops` (`0xa7f00000`), вызывая мгновенный аппаратный Synchronous External Abort (`SError`) и циклическую перезагрузку без следов в `pstore`.
- **Исправление:** В `msm8996-sony-xperia-tone-dora-common.dtsi` свойство `/chosen/bootargs` явно переопределено без `earlycon=msm_serial_dm,0x75b0000` (при сохранении `stdout-path = "serial0:115200n8"`, который активируется штатным драйвером `msm_serial` только после включения тактовых линий `GCC_BLSP2_UART2_APPS_CLK` и `GCC_BLSP2_AHB_CLK`).

### 3.2. P1 Input / PM — устранение двойного вызова `enable_irq_wake()` / `disable_irq_wake()` в `drivers/input/rmi4/rmi_i2c.c`
- **Проблема:** Аудит `drivers/input/rmi4/rmi_driver.c` (функции `rmi_disable_irq()` и `rmi_enable_irq()`, строки 888–935) показал, что ядро подсистемы RMI4 внутри `rmi_driver_suspend(rmi_dev, enable_wake)` и `rmi_driver_resume(rmi_dev, clear_wake)` **уже самостоятельно вызывает** `enable_irq_wake(irq)` и `disable_irq_wake(irq)`, если `enable_wake == true` и `device_may_wakeup(rmi_dev->xport->dev)`. Дополнительный вызов `enable_irq_wake(client->irq)` в `rmi_i2c_suspend()` приводил к двойному инкременту `wake_depth = 2`, а передача жёсткого `true` в `rmi_driver_suspend()` даже при выключенном wakeup нарушала семантику транспорта.
- **Исправление:** В `rmi_i2c.c` удалены дублирующие вызовы `enable_irq_wake()` / `disable_irq_wake()`, введено состояние `suspended_with_wakeup = device_may_wakeup(dev) && client->irq > 0`, которое симметрично передаётся в `rmi_driver_suspend(..., wakeup)` и `rmi_driver_resume(..., wakeup)`. Полное снятие питания `vdd`/`vio` и удержание `reset_gpio` выполняются строго при `!wakeup`.

### 3.3. P1 Fingerprint / PM — согласование `fpc1145_tee_probe()` с эталоном Sony 4.4 (`IRQF_TRIGGER_HIGH | IRQF_NO_AUTOEN` и отсутствие утечки `pm8994_l18`)
- **Проблема:** В эталонном драйвере Sony 4.4 (`drivers/input/misc/fpc1145_platform.c`, строки 614–647) и `msm8996-tone-common.dtsi` (строки 62–80) свойство `fpc,enable-on-boot` не задано: на этапе `probe` регуляторы `pm8994_l18` (2.85 В) и `pm8994_s4` остаются выключенными до вызова `ioctl(FPC_IOCWPREPARE, 1)` из `fingerprint.msm8996.so`, а прерывание GPIO 132 конфигурируется с уровнем `IRQF_TRIGGER_HIGH | IRQF_ONESHOT`. Ранее `fpc1145_tee_probe()` включал питание безусловно при старте ядра (оставляя `pm8994_l18` постоянно включённым даже в окружении без `/dev/qseecom`) и использовал `IRQF_TRIGGER_RISING`.
- **Исправление:** В `somc-dora-platform.c` прерывание переведено на `IRQF_TRIGGER_HIGH | IRQF_ONESHOT | IRQF_NO_AUTOEN`, питание при загрузке остаётся выключенным (`power_enabled = false`, `rst_gpiod` удерживается в активном сбросе) при отсутствии `fpc,enable-on-boot` и включается строго по команде `FPC_IOCWPREPARE(1)` или `regulator_enable`, а в `remove()` прерывание гарантированно отключается до снятия питания.

### 3.4. P1 Pinconf / USB — устранение конфликта `output-high` + `output-low` на `&uim_detect_en` (GPIO 29) и защита открытого Micro-USB (GPIO 24 + VADC `0x39`)
- **Проблема:**
  1. Декомпиляция `.dtb` показала, что узел `uim-detect-en` (`gpio29`) одновременно содержал `output-high;` (унаследованный из `tone.dtsi`) и `output-low;`.
  2. Базовый `tone.dtsi` (рассчитанный на модели с USB Type-C) задавал `output-high;` на `usb_detect` и привязывал цифровой драйвер `linux,extcon-usb-gpio` (`&usb3_id`). В `dora` используется открытый влагозащищённый разъём Micro-USB 2.0 (capless waterproof USB), где по схеме Sony 4.4 (`msm8996-tone-dora-common.dtsi`, строки 149–172) линия `tlmm 24` (`id_polling_pd_gpio`) должна оставаться в `output-low` в покое для предотвращения электрохимической коррозии влажных контактов, а измерение уровня ID заведено на аналоговый канал `0x39` (`VADC_LR_MUX10_USB_ID`, `usb_id_lv`).
- **Исправление:** В `&uim_detect_en` и `&usb_detect` (`gpio24`) добавлено `/delete-property/ output-high; output-low;`, удалён некорректный для `dora` цифровой узел `/delete-node/ &usb3_id;` (`/delete-property/ extcon;` в `&usb3_dwc3` при сохранении `dr_mode = "peripheral"`), а в `&pm8994_vadc` зарегистрирован штатный канал `adc-chan@39` (`usb_id_lv`).
