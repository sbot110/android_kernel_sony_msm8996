# 02. Архитектура переноса аппаратной базы Sony Dora (MSM8996) на Linux 5.15

## 1. Загрузчик Sony S1, `Image.gz-dtb` и варианты Device Tree

Загрузчик Sony S1 на Xperia X Performance (`F8131` и `F8132`) не использует отдельный раздел `dtbo` или `vendor_boot` GKI 2.0: он распаковывает `Image.gz` из классического 64 МБ раздела `boot` и выбирает приклеенный в конец `.dtb` по тройке идентификаторов `qcom,msm-id`, `qcom,pmic-id` и `qcom,board-id`.

В `arch/arm64/boot/dts/qcom/` подготовлены и включены в сборку все **4 аппаратные ревизии DTB**, полностью покрывающие серийные и ранние платы `F8131` / `F8132`:

1. `msm8996-sony-xperia-tone-dora.dtb` — MSM8996 v3.1 (`<246 0x30001>`), `PM8994 + PMI8994` (`<0x20009 0x2000a 0 0>`), `board-id = <8 0>`.
2. `msm8996-pmi8996-sony-xperia-tone-dora.dtb` — MSM8996 v3.1 (`<246 0x30001>`), `PM8994 + PMI8996` (`<0x20009 0x10013 0 0>`), `board-id = <8 0>`.
3. `msm8996-v3.0-sony-xperia-tone-dora.dtb` — MSM8996 v3.0 (`<246 0x30000>`), `PM8994 + PMI8994`, `board-id = <8 0>`.
4. `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` — MSM8996 v3.0 (`<246 0x30000>`), `PM8994 + PMI8996`, `board-id = <8 0>`.

### Критические ошибки в upstream `msm8996-sony-xperia-tone.dtsi` и `msm8996-v3.0.dtsi`, устранённые при переносе

1. **Отключённый контроллер eMMC (`&sdhc1`)**: в mainline `msm8996-sony-xperia-tone.dtsi` узел `&sdhc1` (`7464900.sdhci`) оставлен в состоянии `status = "disabled"`. На `dora` нет UFS (`ufshc` отключён), вся таблица разделов находится на eMMC 5.1 (`sdhc1`). В `msm8996-sony-xperia-tone-dora-common.dtsi` включён `&sdhc1` (`bus-width = <8>`, `non-removable`, `mmc-ddr-1_8v`, `mmc-hs200-1_8v`, `mmc-hs400-1_8v`) и сохранён канонический путь `/dev/block/platform/soc/7464900.sdhci/by-name/*`, а также узел `/firmware/android/fstab` для раннего монтирования `system` (`ext4`) и `oem -> /vendor` (`squashfs`).
2. **Искусственное ограничение `maxcpus=2`**: в upstream `msm8996-sony-xperia-tone.dtsi` в `chosen/bootargs` присутствовал параметр `maxcpus=2`, отключавший производительный кластер Kryo Gold (`CPU2`, `CPU3` до 2.15 ГГц). Ограничение удалено; задействованы оба кластера Kryo (`cluster0_opp` + `cluster1_opp` с чтением `speedbin_efuse` через `ARM_QCOM_CPUFREQ_NVMEM`) и состояния простоя `ARM_QCOM_SPM_CPUIDLE`.
3. **Синтаксическая ошибка `msm8996-v3.0.dtsi`**: узел `gpu_opp_table_3_0` в upstream располагался вне корневого блока `/ { ... }`, что приводило к отказу `dtc`. Обёрнут в корневой узел `/ { ... }`.
4. **Неверный пин Micro-USB OTG ID**: в базовом `tone.dtsi` (ориентированном на USB-C `kagura`/`keyaki`) стоял `GPIO 25` и контроллер USB-C `blsp2_i2c5`. Для `dora` (Micro-USB 2.0) `blsp2_i2c5` отключён, а `usb3_id` переключён на штатный `TLMM GPIO 24` (`GPIO_ACTIVE_LOW`).

---

## 2. Дисплей, автоопределение LCD ID (`lcdid_adc` / PM8994 MPP2) и блокировка AUO 1080p60

В Sony Xperia X Performance (`F8131` и `F8132`) на конвейере устанавливались **5 различных In-Cell DSI Command-Mode панелей** с общим разрешением `1080×1920`, различающихся по напряжению делителя на линии `LCD_ID` (`PM8994 MPP2 -> VADC_P_MUX6_1_1`, а также аргумент командной строки загрузчика S1 `lcdid_adc=0x...`).

Создан специализированный драйвер `drivers/gpu/drm/panel/panel-sony-dora-incell.c` (`CONFIG_DRM_PANEL_SONY_DORA_INCELL`), реализующий трёхуровневое определение установленной панели без жёсткой привязки к одному производителю:

1. Разбор параметра S1 `lcdid_adc=` (`__setup("lcdid_adc=", ...)` и fallback-парсинг `/chosen/bootargs`).
2. При отсутствии параметра — живое чтение физического напряжения `io-channels = <&pm8994_vadc VADC_P_MUX6_1_1>` (`lcd-id`) через IIO.
3. Сопоставление со штатными окнами напряжения из `dsi-panel-dora.dtsi` ядра 4.4.302:

| Panel ID | Compatible | Производитель | Окно ADC (мкВ) | Режим в Linux 5.15 |
|---|---|---|---|---|
| `9` | `somc,sharp_synaptics_cmd_9_panel` | Sharp + Synaptics Full In-Cell | `0 .. 57 000` | `1080×1920 @ 60Hz` (LAB/IBB ±5.6 В) |
| `8` | `somc,lgd_synaptics_cmd_8_panel` | LG Display + Synaptics Full In-Cell | `215 000 .. 256 000` | `1080×1920 @ 60Hz` (LAB/IBB ±5.5 В) |
| `6` | `somc,jdi_synaptics_cmd_6_panel` | JDI + Synaptics Hybrid In-Cell | `353 000 .. 653 000` | `1080×1920 @ 60Hz` (LAB/IBB ±5.6 В + `touch-avdd`) |
| **`4`** | **`somc,auo_synaptics_cmd_4_panel`** | **AUO + Synaptics Full In-Cell** | **`1 087 000 .. 1 231 000`** | **`1080×1920 @ 60Hz` строго (120 Гц исключён)** |
| `3` | `somc,sharp_synaptics_cmd_3_panel` | Sharp + Synaptics Full In-Cell | `1 236 000 .. 1 395 000` | `1080×1920 @ 60Hz` (LAB/IBB ±5.6 В) |
| Fallback | `somc,default_cmd_panel` | Резервный безопасный профиль Sony | вне диапазонов | `1080×1920 @ 60Hz` |

### Подтверждение математики таймингов и ADC на эталонном F8132

- Значение загрузчика `lcdid_adc=0x11FFEA` в десятичном представлении равно **`1 179 626` мкВ**; живые замеры VADC на устройстве (`1 179 234` мкВ и `1 180 606` мкВ) также лежат точно в середине окна `1 087 000 .. 1 231 000` мкВ, однозначно выбирая **Panel 4 (`somc,auo_synaptics_cmd_4_panel`)**.
- Проблемный экспериментальный режим `1080p120` для AUO Panel 4, вызывавший в ранних сборках срыв синхронизации DSI / чёрный экран, **полностью исключён** (аналогично нашему рабочему фиксу в `0c295b15b6d0`).
- Расчёт частот для `1080×1920 @ 60Hz` (`htotal = 1080 + 56 + 8 + 8 = 1152`, `vtotal = 1920 + 233 + 2 + 8 = 2163`):
  - `pclk = 1152 × 2163 × 60 = 149 506 560 Гц` (`clock = 149507 кГц`).
  - `bitclk (4 DSI lanes, RGB888 24 bpp) = 149 506 560 × 24 / 4 = 897 039 360 бит/с`.
  - `byteclk = 897 039 360 / 8 = 112 129 920 Гц`.
- В `drivers/regulator/qcom-labibb-regulator.c` и `pmi8994.dtsi` добавлена поддержка регуляторов питания матрицы `qcom,pmi8994-lab-ibb` / `qcom,pmi8996-lab-ibb` (`pmi8994_lab` + `pmi8994_ibb`), а для подсветки активирован `pmi8994_wled` (3 цепочки светодиодов, лимит 20 мА).

---

## 3. Периферия, связь и особенности F8131 (Single SIM) / F8132 (Dual SIM)

### 3.1. Гибридный лоток SIM2 / MicroSD на F8132 и `SIM1_DET`

- На обоих вариантах (`F8131` и `F8132`) основной слот SIM1 подключён к `TLMM GPIO 112` (`sim1-det-gpios`), а программный режим модема (`persist.radio.multisim.config=ss` или `dsds`) выставляется скриптом `init.qcom.msim.sh` на основе чтения раздела `/dev/block/bootdevice/by-name/LTALabel`.
- На **F8132 (Dual SIM)** второй слот является **электрически гибридным** (`MicroSD` либо `Nano-SIM2`):
  - `TLMM GPIO 40` (`cd-gpios`, `GPIO_ACTIVE_HIGH`) сигнализирует о закрытии лотка.
  - `TLMM GPIO 29` (`uim2-gpios`, `GPIO_ACTIVE_HIGH`) управляет линией `UIM2_DET_EN`.
  - Из нашего рабочего ядра 4.4.302 (`drivers/mmc/core/slot-gpio.c` и `core.c`) в Linux 5.15 перенесена точная логика коммутации `mmc_gpiod_request_uim2()` / `mmc_gpio_tray_close_set_uim2()`: при извлечении лотка `GPIO 29` принудительно сбрасывается в `0`; при закрытии лотка сначала выполняется опрос SD-карты (`mmc_attach_sd`), и **только если SD-карта не ответила** при закрытом лотке, ядро выставляет `GPIO 29 = 1`, передавая вторую SIM-карту модему.

### 3.2. Wi-Fi Broadcom BCM4359 C0 (PCIe0) и устранение проблемы WPA1/WPA2 CCMP

- Чип BCM4359 подключён к шине `&pcie0` (`WL_REG_ON` = `TLMM GPIO 84`, `WL_HOST_WAKE` = `TLMM GPIO 79`).
- В старом ядре 4.4 (`bcmdhd` + старый `net/wireless/sme.c`) при соединении происходило обнуление `cipher_group` / `n_ciphers_pairwise`, из-за чего требовались ручные исправления WPA1/WPA2 и подгрузки NVRAM. В Linux 5.15.220 стек `cfg80211`/`nl80211` (`net/wireless/sme.c`) передаёт параметры шифрования RSN/WPA без потери `WLAN_CIPHER_SUITE_CCMP`/`TKIP`.
- Драйвер `brcmfmac` (`CONFIG_BRCMFMAC_PCIE=y`) поддерживает как штатный `BRCM_PCIE_4359_DEVICE_ID` (`0x43ef`), так и немаркированный OTP `BRCM_CC_4359_CHIP_ID` (`0x4359`). В `drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.c` реализован безопасный fallback через `request_firmware_direct()` на относительные пути read-only раздела Sony `/oem` (`wlan/bcmdhd/fw_bcmdhd.bin`, `wlan/bcmdhd/bcmdhd.cal`, `wifi/bcmdhd.cal`, `bcmdhd.cal`) без `..` path traversal и без 60-секундных блокировок `CONFIG_FW_LOADER_USER_HELPER`.
- В `somc-dora-platform.c` через штатный механизм `MODULE_PARAM_PREFIX = "bcmdhd."` (`kernel/params.c`) экспортируются `/sys/module/bcmdhd/parameters/{firmware_path,nvram_path,op_mode}` (`0660`) без небезопасного разыменования `THIS_MODULE` при монолитной сборке (`CONFIG_SOMC_DORA_PLATFORM=y`).

### 3.3. Bluetooth, NFC, Clearpad, FPC1145, Аудио и Remoteproc

- **Bluetooth BCM4359:** подключён через `serdev` на `&blsp1_uart2` (`0x7570000`, 3 Мбит/с) к `drivers/bluetooth/hci_bcm.c` (`brcm,bcm4359-bt`), `shutdown-gpios = <&tlmm 83>`, `device-wakeup-gpios = <&tlmm 81>`, `host-wakeup-gpios = <&tlmm 82>`, `vddio-supply = <&pm8994_s4>`.
- **NFC NXP PN547/PN548:** подключён на `&blsp2_i2c2` (`0x75b6000`, адрес `0x28`, драйвер `nxp-nci-i2c`), `IRQ` = `TLMM GPIO 9`, `firmware-gpios` = `TLMM GPIO 12`, `enable-gpios` = `PM8994 GPIO 7`.
- **Тачскрин Synaptics Clearpad S3320:** подключён на `&blsp2_i2c6` (`0x75ba000 @ 0x2c`) через доработанный `drivers/input/rmi4/rmi_i2c.c` (управление `reset-gpios = <&tlmm 89 GPIO_ACTIVE_LOW>`, сохранение питания регуляторов и `enable_irq_wake` на `TLMM GPIO 125` при активном `device_may_wakeup`). Узел `/sys/devices/virtual/input/clearpad/{wakeup_gesture,post_probe_start,cover_mode_enabled,glove,cover_win_*}` зарегистрирован через `&input_class` и при записи `wakeup_gesture` напрямую переключает `device_set_wakeup_enable()` на реальном I2C-контроллере `clearpad_ts`.
- **Сканер отпечатков FPC1145 (в кнопке питания):** шина `BLSP1 SPI1` (`GPIO 0..3`) на `dora` зарезервирована за TrustZone (`gpio-reserved-ranges = <0 4>`). Драйвер в `somc-dora-platform.c` регистрирует как символьное устройство **`/dev/fingerprint`** (`poll` + `FPC_IOC_HW_RESET` / `WAKELOCK` / `ENABLE_IRQ` / `ENABLE_POWER` / `CLK_STATE`), так и sysfs-атрибуты `fpc1020` (`hw_reset`, `irq`, `wakeup_enable`, `clk_enable`, `regulator_enable`).
- **Аудио и сопроцессоры:** настроены `WCD9335 (Tasha)` по `SLIMbus` (`slim@91c0000`, тактирование `div1_mclk` через `PM8994 GPIO 15`), усилители `WSA881x`, звуковая карта `qcom,apq8096-sndcard` (`Sony-Xperia-Dora`) и все 4 сопроцессора `remoteproc`: `mss_pil`, `adsp_pil`, `slpi_pil` и `venus`. Узлы `/sys/kernel/boot_adsp/boot` и `/sys/kernel/boot_slpi/boot` связаны через DT-phandle (`somc,adsp-rproc`, `somc,slpi-rproc`) с реальными вызовами `rproc_get_by_phandle()` / `rproc_boot()` / `rproc_shutdown()`.

---

## 4. Честная фиксация проприетарных ограничений userspace (GPU и Камера)

1. **Графический стек Adreno 530 (DRM/MSM vs Legacy KGSL):**
   - В Linux 5.15 используется открытый ядерный драйвер `drivers/gpu/drm/msm` (`adreno` A530 + `a530_zap.mdt` + `adreno_smmu`), а не устаревший downstream-драйвер Qualcomm `drivers/gpu/msm` (`kgsl-3d0`).
   - Проприетарные бинарные библиотеки Qualcomm OpenGL ES / Vulkan от Android 8/10 обращаются к `/dev/kgsl-3d0` и с ядром 5.15 работать **не будут**. При сборке LineageOS 21 / 22 с ядром 5.15 в дереве устройства необходимо переключить графический userspace на **Mesa3D Freedreno ( Gallium `freedreno` + Vulkan `turnip` )** и `drm_hwcomposer` / `minigbm_msm`.
2. **Камера Sony IMX300 (23 МП) / IMX240 (13 МП):**
   - Заводской 32-битный проприетарный стек Sony `mm-camera2` жёстко завязан на закрытые downstream V4L2/MSMB ioctl ядра Qualcomm 4.4 (`drivers/media/platform/msm/camera_v2`) и проприетарный демон `mm-qcamera-daemon`.
   - В Linux 5.15 присутствует mainline-подсистема `qcom-camss`, однако открытого mainline-драйвера сенсора **Sony IMX300** с таблицами фазового автофокуса и калибровками Sony ISP нет. Поддельная заглушка для `camera_v2` намеренно **не создавалась**.
