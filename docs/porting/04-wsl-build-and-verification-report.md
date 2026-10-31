# 04. Отчёт о сборке ARM64, исправленных ошибках и инструкция по локальной сборке в WSL Ubuntu 22.04

## 1. Реальная компиляция и исправления ошибок в облачной среде (Этап 7)

В ходе этапа 7 в изолированном каталоге `/tmp/dora-build` выполнена реальная кросс-компиляция под архитектуру `ARCH=arm64` (`aarch64-linux-gnu-gcc 12.3.0`, `binutils 2.40`, `dtc 1.6.1`, флаг строгости ядра `-Werror`):

### Выявленные и исправленные при сборке замечания

1. **`arch/arm64/boot/dts/qcom/msm8996-v3.0.dtsi` (ошибка `dtc`):**
   - *Симптом:* синтаксический отказ компилятора Device Tree из-за объявления `gpu_opp_table_3_0` вне корневого блока `/ { ... }` в upstream-файле.
   - *Исправление:* таблица частот обёрнута в корневой узел `/ { ... }`; все 4 варианта `msm8996-*tone-dora.dtb` успешно компилируются (по ~71 КБ каждый).
2. **`drivers/platform/somc/somc-dora-platform.c` (`-Werror=comment`):**
   - *Симптом:* последовательности `/clearpad/*` и `/parameters/*` внутри блочных комментариев `/* ... */` вызвали срабатывание `-Werror=comment`.
   - *Исправление:* комментарии приведены к виду `/clearpad/<attr>` и `/parameters/<attr>`, после чего драйвер скомпилирован с нулём предупреждений.
3. **Монолитная компоновка беспроводного стека и NFC (`CONFIG_RFKILL=y`):**
   - *Симптом:* в базовом `gki_defconfig` опция `CONFIG_RFKILL=m` принудительно переключала `CFG80211`, `BRCMFMAC`, `BT_HCIUART` и `NFC_NXP_NCI_I2C` в модули `.ko`, что на Xperia X Performance с read-only SquashFS разделом `/oem` (`/vendor`) создаёт риск несовпадения модулей при прошивке только `boot.img`.
   - *Исправление:* в `dora_defconfig` включены `CONFIG_RFKILL=y` и `CONFIG_VIDEO_QCOM_VENUS=y`, благодаря чему Wi-Fi (`brcmfmac`), Bluetooth (`hci_bcm`), NFC (`nxp-nci-i2c`) и все платформенные драйверы `dora` собираются встроенно (`=y`) непосредственно внутрь `Image.gz-dtb`.

### Подтверждённые скомпилированные целевые объекты ARM64 (`O=/tmp/dora-build`)

- **Device Tree Blobs (4 варианта для Sony S1 Bootloader):**
  - `arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-dora.dtb`
  - `arch/arm64/boot/dts/qcom/msm8996-pmi8996-sony-xperia-tone-dora.dtb`
  - `arch/arm64/boot/dts/qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb`
  - `arch/arm64/boot/dts/qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb`
- **Портированные и платформенные драйверы MSM8996 / Dora / Android 14:**
  - `drivers/gpu/drm/panel/panel-sony-dora-incell.o` (автовыбор LCD ID Panel 3/4/6/8/9, AUO 1080p60)
  - `drivers/platform/somc/somc-dora-platform.o` (SIM1, FPC1145 TEE, Clearpad/ADSP/SLPI/bcmdhd sysfs)
  - `drivers/regulator/qcom-labibb-regulator.o` (`pmi8994-lab-ibb` / `pmi8996-lab-ibb`)
  - `drivers/video/backlight/qcom-wled.o` (`pmi8994_wled`)
  - `drivers/mmc/core/slot-gpio.o`, `drivers/mmc/core/host.o`, `drivers/mmc/core/core.o`, `drivers/mmc/host/sdhci-msm.o` (eMMC HS400 + F8132 гибридный лоток `uim2-gpios`)
  - `drivers/bluetooth/hci_bcm.o` (`brcm,bcm4359-bt` serdev)
  - `drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.o`, `pcie.o` (BCM4359 PCIe + Sony `/oem` fallback)
  - `drivers/nfc/nxp-nci/i2c.o` (PN547/PN548 NFC)
  - `drivers/remoteproc/qcom_q6v5_mss.o`, `qcom_q6v5_pas.o` (Hexagon Modem, ADSP, SLPI)
  - `drivers/clk/qcom/gcc-msm8996.o`, `mmcc-msm8996.o`, `apcc-msm8996.o`
  - `drivers/pinctrl/qcom/pinctrl-msm8996.o`, `drivers/cpufreq/qcom-cpufreq-nvmem.o`
  - `sound/soc/codecs/wcd9335.o`, `wsa881x.o`, `sound/soc/qcom/apq8096.o`
  - `drivers/android/binder.o`, `drivers/android/binderfs.o`

---

## 2. Пошаговая инструкция по локальной полной сборке `Image.gz-dtb` в WSL2 Ubuntu 22.04

### Шаг 1. Установка инструментов сборки в Ubuntu 22.04 (WSL2)

```bash
sudo apt-get update
sudo apt-get install -y \
  git build-essential bc bison flex libssl-dev libelf-dev dwarves \
  device-tree-compiler cpio lz4 xz-utils python3 rsync curl \
  clang lld llvm gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu
```

*(Либо используйте предсобранный компилятор AOSP/LineageOS Clang из `prebuilts/clang/host/linux-x86/clang-r487747c/bin`, добавив его в начало `PATH`).*

### Шаг 2. Получение ветки `kernel-5.5` (без затрагивания других веток)

Если ветка уже опубликована в GitHub:

```bash
git clone --branch kernel-5.5 --single-branch \
  https://github.com/sbot110/android_kernel_sony_msm8996.git dora-kernel-5.15
cd dora-kernel-5.15
```

Либо импорт напрямую из подготовленного пакета `dora-kernel-5.5.bundle` и публикация в `origin`:

```bash
git clone https://github.com/sbot110/android_kernel_sony_msm8996.git
cd android_kernel_sony_msm8996
git fetch /path/to/dora-kernel-5.5.bundle kernel-5.5:kernel-5.5
git push origin kernel-5.5
git worktree add ../dora-kernel-5.15 kernel-5.5
cd ../dora-kernel-5.15
```

### Шаг 3. Сборка `Image.gz-dtb` и модулей одной командой

Рекомендуемая сборка через **Clang/LLVM** (`LLVM=1 LLVM_IAS=1`):

```bash
OUT_DIR=out JOBS=$(nproc) ./scripts/dora/build-dora-5.15.sh
```

Опционально — сборка с генерацией ядерной таблицы `BTF` (`CONFIG_DEBUG_INFO_BTF=y`, при наличии `pahole` из пакета `dwarves` и >= 8 ГБ ОЗУ в WSL2):

```bash
ENABLE_BTF=1 OUT_DIR=out JOBS=$(nproc) ./scripts/dora/build-dora-5.15.sh
```

Резервная сборка через **GNU GCC** (`aarch64-linux-gnu-gcc`):

```bash
USE_GCC=1 OUT_DIR=out JOBS=$(nproc) ./scripts/dora/build-dora-5.15.sh
```

По завершении скрипт автоматически:
1. формирует `out/arch/arm64/boot/Image.gz`;
2. компилирует все 4 `.dtb` ревизий `dora`;
3. склеивает финальный образ `out/arch/arm64/boot/Image.gz-dtb`;
4. проверяет запас размера под 64 МБ раздел `boot` загрузчика Sony S1;
5. выводит контрольные суммы `SHA-256`.

### Шаг 4. Упаковка тестового `boot.img` для Sony S1 Bootloader (`F8131` / `F8132`)

При локальной проверке с существующим `ramdisk.cpio.xz` от вашей сборки LineageOS 20 / 21:

```bash
mkbootimg \
  --kernel out/arch/arm64/boot/Image.gz-dtb \
  --ramdisk ramdisk.cpio.xz \
  --cmdline "androidboot.hardware=qcom user_debug=31 msm_rtb.filter=0x237 ehci-hcd.park=3 cma=64M@0-0xffffffff coherent_pool=2M zram.backend=z3fold loop.max_part=16" \
  --base 0x80000000 \
  --pagesize 4096 \
  --kernel_offset 0x00008000 \
  --ramdisk_offset 0x02000000 \
  --tags_offset 0x01E00000 \
  --output boot-dora-5.15.img
```

---

## 3. Протокол первичной проверки на реальном устройстве (`F8131` / `F8132`)

Поскольку физическое тестирование в облачной среде невозможно, при первом запуске на смартфоне выполните проверку в следующем порядке:

1. **Безопасная загрузка без перезаписи флеш-памяти (если S1 поддерживает `fastboot boot`) или прошивка `boot` с возможностью отката на `0c295b15b6d0`:**
   - При сбое ранней загрузки перезагрузитесь обратно в рабочее ядро 4.4.302 и считайте `/sys/fs/pstore/console-ramoops-0` (область `0xa7f00000` полностью совпадает между 4.4.302 и 5.15.220!).
2. **Проверка выбора панели LCD ID:**
   - `adb shell dmesg | grep -i dora-panel` — убедиться, что на эталонном `F8132` (`lcdid_adc=0x11FFEA` / `1179626 uV`) выбран профиль `AUO ID4 (somc,auo_synaptics_cmd_4_panel)` в режиме `1080x1920@60`.
3. **Проверка eMMC, `/vendor` (`SquashFS`) и гибридного лотка `F8132`:**
   - `adb shell ls -l /dev/block/platform/soc/7464900.sdhci/by-name/`
   - `adb shell cat /sys/devices/platform/somc-dora-platform/sim1_present`
   - На `F8132`: проверить работу как MicroSD во втором слоте, так и второй Nano-SIM (`dsds`).
4. **Проверка периферии и `bpfloader`:**
   - `adb shell getprop bpf.progs_loaded` (должно вернуть `1`).
   - Проверить Wi-Fi WPA2/WPA3 (`wlan0`), Bluetooth (`hci0`), NFC, звук (`Sony-Xperia-Dora`), сканер отпечатка FPC1145 и датчик Холла чехла.

---

## 4. Контрольные суммы SHA-256 скомпилированных артефактов ARM64 (Linux 5.15.220)

```text
fb9ff2c771bdb55b5e37f090b2c7b21f5381e9b87102768323d08dc6b9d8e3b4  arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-dora.dtb
a6ca32cdfb073d4ac217fa4a06ce0893a20993857fe762cb8f169f18d8b21be9  arch/arm64/boot/dts/qcom/msm8996-pmi8996-sony-xperia-tone-dora.dtb
4cdca676669641fd0ae9745e80aa4ae4fb7675ad914fb68d1fd60b027b2cc88d  arch/arm64/boot/dts/qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb
9c91336e38897d6e4b550d5dc06396aaca2994ce45605768f8c9d5b53930c5ff  arch/arm64/boot/dts/qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb
6e3c5d673ebf620d12d033652f8516a605c009a72a04d636e6e4112b3fb77e08  drivers/gpu/drm/panel/panel-sony-dora-incell.o
eaaca8fb16b1ce7baa859ebefae5fd5c03caf76c3efbc76be591ccc6c2b25b82  drivers/platform/somc/somc-dora-platform.o
d1fe4bc6e3496959e338eb4a403c1d8de393dd04e688f6032378e1b14f2591c0  drivers/regulator/qcom-labibb-regulator.o
a168fbf83355cd4f261dd93023720d0cef673caefa8fc1288bab5d8e90c68ef3  drivers/mmc/core/slot-gpio.o
7601b51df876494f9de34e7a9c174eea688ca47bdae693cdb0a780ddd0ff9dcc  drivers/bluetooth/hci_bcm.o
c3a1ec0fcd186575226516c3f543236e2ce15645654163ac7ff2de7717c805f5  drivers/nfc/nxp-nci/i2c.o
9868e3b2ae487229fe35471ecb7b99811b58abaf1f40a18b9ee918ff8a9872ed  drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.o
```
