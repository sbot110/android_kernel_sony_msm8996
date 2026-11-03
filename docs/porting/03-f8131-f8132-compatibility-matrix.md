# 03. Матрица совместимости F8131 / F8132 и анализ проблем Android 14 / 15

## 1. Сводная матрица подсистем для F8131 (Single SIM) и F8132 (Dual SIM)

> **Важное примечание о верификации:** в облачной среде скомпилированы реальные объектные файлы ARM64, модули, DTB и образ `Image.gz-dtb`, однако физический смартфон `F8131` / `F8132` не подключён. Поэтому для всех собранных ядерных подсистем действует строгий статус: **«компиляция подтверждена, аппаратная работоспособность на устройстве требует проверки»**.

| Подсистема | F8131 (Single SIM) | F8132 (Dual SIM) | Драйвер / узел в Linux 5.15.220 | Статус готовности |
|---|---|---|---|---|
| Загрузчик S1 и `Image.gz-dtb` | 4 DTB (`v3.0`/`v3.1`, `PMI8994`/`PMI8996`) | 4 DTB (`v3.0`/`v3.1`, `PMI8994`/`PMI8996`) | `arch/arm64/boot/dts/qcom/*tone-dora*.dts` | Скомпилировано, укладывается в 64 МБ `boot` |
| CPU Kryo 4 ядра + CPUFreq + SPM | 2×1.59 ГГц + 2×2.15 ГГц | 2×1.59 ГГц + 2×2.15 ГГц | `qcom-cpufreq-nvmem`, `qcom-spm`, `kryocc` | Скомпилировано (`maxcpus=2` убран) |
| Память eMMC 5.1 (`sdhc1`) | HS400 8-bit (`/dev/block/.../7464900.sdhci`) | HS400 8-bit (`/dev/block/.../7464900.sdhci`) | `sdhci-msm` + `/firmware/android/fstab` | Скомпилировано (`sdhc1` включён) |
| Лоток MicroSD и SIM (`sdhc2` + SIM) | MicroSD (`CD GPIO 40`) + `SIM1 GPIO 112` (`ss`) | Гибридный `MicroSD / UIM2` (`CD GPIO 40` + `UIM2 GPIO 29`, `dsds`) | `mmc/core/slot-gpio.c` + `somc-dora-platform.c` | Скомпилировано (перенесена логика UIM2 4.4) |
| Дисплей 1080p60 + Автовыбор LCD ID | Автовыбор Panel 3/4/6/8/9 по `lcdid_adc`/VADC | Автовыбор (подтверждён Panel 4 AUO `0x11FFEA`) | `drm/msm` + `panel-sony-dora-incell.c` + `labibb` + `wled` | Скомпилировано (AUO 120Hz заблокирован) |
| GPU Adreno 530 + SMMU | `gpu@b00000` + `a530_zap.mdt` | `gpu@b00000` + `a530_zap.mdt` | `drm/msm/adreno` + `arm-smmu-qcom` | Ядро собрано; нужен Freedreno/Mesa в ROM |
| Тачскрин Synaptics Clearpad + жесты | `blsp2_i2c6 @ 0x2c`, `IRQ 125`, `RST 89` | `blsp2_i2c6 @ 0x2c`, `IRQ 125`, `RST 89` | `rmi4-i2c` (`reset-gpios` + `irq_wake`) + `&input_class` `clearpad` | Скомпилировано |
| Кнопки и датчик чехла Hall | Громкость, Камера 2 ступени, `SW_LID GPIO 78` | Громкость, Камера 2 ступени, `SW_LID GPIO 78` | `gpio-keys` + `pm8941-pwrkey` | Скомпилировано |
| Вибромотор | `PMI8994 GPIO 1` | `PMI8994 GPIO 1` | `gpio-vibrator` (`INPUT_GPIO_VIBRA`) | Скомпилировано |
| Отпечаток пальца FPC1145 (TEE) | `RST GPIO 21`, `IRQ GPIO 132`, QSEE SPI0 | `RST GPIO 21`, `IRQ GPIO 132`, QSEE SPI0 | `somc-dora-platform.c` (`/dev/fingerprint` + `fpc1020`) | Скомпилировано |
| Wi-Fi Broadcom BCM4359 C0 | PCIe0 (`0x43ef`/`0x4359`, `WL_REG_ON 84`, `WAKE 79`) | PCIe0 (`0x43ef`/`0x4359`, `WL_REG_ON 84`, `WAKE 79`) | `brcmfmac` (`request_firmware_direct`) + `MODULE_PARAM_PREFIX` | Скомпилировано |
| Bluetooth BCM4359 | `blsp1_uart2` (`REG_ON 83`, `WAKE 81/82`) | `blsp1_uart2` (`REG_ON 83`, `WAKE 81/82`) | `hci_bcm` (`brcm,bcm4359-bt` serdev) | Скомпилировано |
| NFC NXP PN547 / PN548 | `blsp2_i2c2 @ 0x28` (`IRQ 9`, `FW 12`, `VEN PM7`) | `blsp2_i2c2 @ 0x28` (`IRQ 9`, `FW 12`, `VEN PM7`) | `nxp-nci-i2c` | Скомпилировано |
| USB 2.0 Micro-B OTG + Gadget | `usb3` HS-only (`ID GPIO 24`), ConfigFS | `usb3` HS-only (`ID GPIO 24`), ConfigFS | `dwc3-qcom` + `usb_f_fs` / `rndis` / `ncm` / `mtp` | Скомпилировано |
| Аудио WCD9335 + WSA881x + Q6DSP | SLIMbus + `div1_mclk` (`PM GPIO 15`) | SLIMbus + `div1_mclk` (`PM GPIO 15`) | `wcd9335`, `wsa881x`, `qdsp6`, `apq8096-sndcard` | Скомпилировано |
| Модем / ADSP / SLPI / Видео Venus | `mss_pil`, `adsp_pil`, `slpi_pil`, `venus` | `mss_pil`, `adsp_pil`, `slpi_pil`, `venus` | `qcom_q6v5_mss`, `qcom_q6v5_pas`, `rproc_boot(adsp/slpi)`, `venus` | Скомпилировано |
| Камеры IMX300 / IMX240 | Ограничение проприетарного `mm-camera2` | Ограничение проприетарного `mm-camera2` | Нет открытого драйвера IMX300 для `camss` | Заблокировано userspace/sensor blobs |
| Ramoops / pstore | `0xa7f00000` (1 МБ: console, pmsg, ftrace) | `0xa7f00000` (1 МБ: console, pmsg, ftrace) | `pstore` + `ramoops` | Скомпилировано |

---

## 2. Инженерный разбор сбоев LineageOS 21 (Android 14) и их решение в Linux 5.15

В техническом задании отмечены три характерные аварии при попытке запуска LineageOS 21 (Android 14) на старом ядре: сбой `bpfloader`, `boringssl-self-check-failed` и `vold-failed`. Ниже приведено разделение ядерных и userspace-причин и то, что сделано в `dora_defconfig`.

### 2.1. Сбой `bpfloader` -> решается переходом на ACK 5.15.220

- **Причина на ядре 4.4:** Android 14 (`system/bpf/bpfloader` и `netd`) требует нативной поддержки современных карт eBPF (`BPF_MAP_TYPE_RINGBUF`, `BPF_MAP_TYPE_PERCPU_HASH`, `BPF_MAP_TYPE_DEVMAP_HASH`), флагов `BPF_F_RDONLY_PROG`, привязки к cgroup v2 (`BPF_CGROUP_INET_INGRESS`, `EGRESS`, `SOCK_CREATE`, `UDP4_SENDMSG`) и ядерных tracepoints (`power/cpu_frequency` для `time_in_state.o`). Ручной backport eBPF в 4.4 неизбежно расходится с верификатором Android 14 и Android 15.
- **Решение в `kernel-5.5` (Linux 5.15.220):** база `android14-5.15` изначально спроектирована Google под `bpfloader` Android 14 и обратно совместима с Android 15. В `dora_defconfig` включены `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y`, `CONFIG_BPF_JIT_ALWAYS_ON=y`, `CONFIG_CGROUP_BPF=y`, `CONFIG_BPF_EVENTS=y`, `CONFIG_TRACING=y`, `CONFIG_NET_CLS_BPF=y`, `CONFIG_NET_ACT_BPF=y`, `CONFIG_NETFILTER_XT_MATCH_BPF=y`. При сборке с `pahole` в WSL скрипт поддерживает также `ENABLE_BTF=1` (`CONFIG_DEBUG_INFO_BTF=y`).

### 2.2. Сбой `vold-failed` -> устранён на уровне ФС, квот и криптографии ядра + рекомендация для `fstab.qcom`

- **Ядерные причины:** `vold` в Android 14 падает на старте (`vold-failed` -> перезагрузка в recovery), если ядро не может смонтировать хотя бы один обязательный раздел из `fstab.qcom` или не поддерживает требуемый режим шифрования/квот:
  1. На Sony `dora` раздел `/oem` (монтируемый как `/vendor`) отформатирован в **SquashFS со сжатием LZ4** (`BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE := squashfs`), а `/cache` — в **F2FS** (`BOARD_CACHEIMAGE_FILE_SYSTEM_TYPE := f2fs`). В стандартном `gki_defconfig` `CONFIG_SQUASHFS_LZ4` выключен! В нашем `dora_defconfig` жёстко включены `CONFIG_SQUASHFS=y`, `CONFIG_SQUASHFS_LZ4=y`, `CONFIG_SQUASHFS_ZLIB=y`, `CONFIG_SQUASHFS_XZ=y`, `CONFIG_F2FS_FS=y`, `CONFIG_F2FS_FS_LZ4=y`, `CONFIG_EXT4_FS=y`.
  2. Для FBE (`fileencryption=aes-256-xts:aes-256-cts:v2` / `adiantum`) и проектных квот `installd`/`vold` включены `CONFIG_FS_ENCRYPTION=y`, `CONFIG_FS_ENCRYPTION_INLINE_CRYPT=y`, `CONFIG_BLK_INLINE_ENCRYPTION=y`, `CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK=y`, `CONFIG_FS_VERITY=y`, `CONFIG_DM_CRYPT=y`, `CONFIG_DM_DEFAULT_KEY=y`, `CONFIG_DM_VERITY=y`, `CONFIG_DM_BOW=y`, `CONFIG_DM_USER=y`, `CONFIG_QUOTA=y`, `CONFIG_QFMT_V2=y`, `CONFIG_CRYPTO_XTS=y`, `CONFIG_CRYPTO_CTS=y`, `CONFIG_CRYPTO_ADIANTUM=y`, `CONFIG_CRYPTO_HCTR2=y`, `CONFIG_CRYPTO_ESSIV=y`.
- **Userspace-фактор в LineageOS 21 (проверить в `android_device_sony_tone-common`):** Android 14 полностью удалил поддержку старого Full-Disk Encryption (`forceencrypt=footer` / Qualcomm HW FDE `qseecom` v1). Если в `fstab.qcom` для `/data` остался флаг `forceencrypt=/dev/block/bootdevice/by-name/userdata` от Android 10/13, `vold` в Android 14 завершится с `vold-failed` независимо от версии ядра — его необходимо заменить на `fileencryption=aes-256-xts:aes-256-cts:v2` (либо временно `encryptable=footer` / без FDE при первом тестовом запуске с чистым `userdata`).

### 2.3. Сбой `boringssl-self-check-failed` -> ядерные условия соблюдены + диагностика userspace

- **Ядерная сторона:** при старте `init` библиотека `libcrypto.so` (BoringSSL) выполняет известный ответный тест (`BORINGSSL_self_test()`) с использованием инструкций ARMv8 Crypto Extensions (`AES`, `PMULL`, `SHA1`, `SHA256`) и векторных регистров NEON/FPSIMD. Если ядро некорректно сохраняет контекст FPSIMD или активирует `CONFIG_CRYPTO_FIPS=y` без подписанного модуля `fips140.ko`, происходит ранний крах криптотестов. В `dora_defconfig`:
  - включены `CONFIG_KERNEL_MODE_NEON=y`, `CONFIG_ARM64_CRYPTO=y`, все аппаратные модули `CONFIG_CRYPTO_*_ARM64_CE=y` и 32-битная совместимость `CONFIG_COMPAT=y`;
  - явно отключён `CONFIG_CRYPTO_FIPS` (`# CONFIG_CRYPTO_FIPS is not set`).
- **Userspace-сторона (LineageOS 21 на Kryo MSM8996):** `boringssl-self-check-failed` в раннем `init` на кастомных сборках Android 14 также возникает, если в `BoardConfig.mk` задан несовместимый `TARGET_CPU_VARIANT` (например `cortex-a76`/`armv8.2-a` с инструкциями DotProd/SVE/SHA3, отсутствующими в ядрах Qualcomm Kryo ARMv8.0-A на Snapdragon 820) или повреждена секция `.rodata`/`bcd` `libcrypto.so` при strip/relocation. Для MSM8996 обязательно использовать `TARGET_ARCH_VARIANT := armv8-a` и `TARGET_CPU_VARIANT := kryo` (или `generic`).
