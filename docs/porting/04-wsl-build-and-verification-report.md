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

4. **Распаковка S1 `ramdisk.cpio.xz`, `PSI` для `lmkd` и отключение `KASAN_HW_TAGS` на ARMv8.0-A Kryo (`dora_defconfig`):**
   - *Симптом:* при `CONFIG_EMBEDDED=y` отсутствовала явная строка `CONFIG_BLK_DEV_INITRD=y`, из-за чего отключались `CONFIG_RD_XZ` / `CONFIG_RD_LZ4`; в `CONFIG_CMDLINE` присутствовали `cgroup_disable=pressure` (блокирующий `lmkd` Android 14), `bootconfig` и `kvm-arm.mode=protected`; был активен `CONFIG_KASAN_HW_TAGS=y` (требующий ARMv8.5-A MTE, отсутствующего на ядрах Qualcomm Kryo ARMv8.0-A).
   - *Исправление:* включены `CONFIG_BLK_DEV_INITRD=y`, `CONFIG_RD_XZ=y`, `CONFIG_RD_LZ4=y`, `CONFIG_RD_GZIP=y`; очищен `CONFIG_CMDLINE` с сохранением активного `CONFIG_PSI=y`; отключён `CONFIG_KASAN`.
5. **Устранение NULL-dereference `THIS_MODULE` при `=y`, `/dev/fingerprint`, `rproc_boot` и `&input_class` (`somc-dora-platform.c`, `rmi_i2c.c`, `brcmfmac`):**
   - *Исправление:* удалено обращение к `THIS_MODULE->mkobj.kobj.kset` (дававшее `NULL` при встроенной сборке `=y`), параметры `/sys/module/bcmdhd/parameters/*` переведены на штатный `MODULE_PARAM_PREFIX = "bcmdhd."`; узел `/sys/devices/virtual/input/clearpad/*` переведён на `&input_class` с прямой привязкой `wakeup_gesture` к `rmi_i2c` (`reset-gpios` GPIO 89 + `enable_irq_wake`); добавлен символьный узел `/dev/fingerprint` (`poll` + `FPC_IOC_*`); `boot_adsp`/`boot_slpi` подключены к `rproc_boot()`/`rproc_shutdown()`; в `brcmfmac/firmware.c` убран `..` traversal и применён `request_firmware_direct()`.

### Подтверждённые скомпилированные целевые объекты ARM64 (`O=/tmp/dora-build`, `KCFLAGS=-Werror`)

- **Device Tree Blobs (4 варианта для Sony S1 Bootloader):**
  - `arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-dora.dtb`
  - `arch/arm64/boot/dts/qcom/msm8996-pmi8996-sony-xperia-tone-dora.dtb`
  - `arch/arm64/boot/dts/qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb`
  - `arch/arm64/boot/dts/qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb`
- **Портированные и платформенные драйверы MSM8996 / Dora / Android 14 (26 объектов ARM64):**
  - `drivers/gpu/drm/panel/panel-sony-dora-incell.o` (автовыбор LCD ID Panel 3/4/6/8/9, AUO 1080p60, `-EPROBE_DEFER` VADC)
  - `drivers/platform/somc/somc-dora-platform.o` (`/dev/fingerprint` + `fpc1020`, `rproc_boot`, `&input_class` clearpad, `MODULE_PARAM_PREFIX` bcmdhd)
  - `drivers/input/rmi4/rmi_i2c.o`, `drivers/input/rmi4/rmi_f12.o` (Synaptics Clearpad `reset-gpios` GPIO 89 + `irq_wake`)
  - `drivers/regulator/qcom-labibb-regulator.o` (`pmi8994-lab-ibb` / `pmi8996-lab-ibb`)
  - `drivers/video/backlight/qcom-wled.o` (`pmi8994_wled`)
  - `drivers/mmc/core/slot-gpio.o`, `drivers/mmc/core/host.o`, `drivers/mmc/core/core.o`, `drivers/mmc/host/sdhci-msm.o` (eMMC HS400 + F8132 гибридный лоток `uim2-gpios`)
  - `drivers/bluetooth/hci_bcm.o` (`brcm,bcm4359-bt` serdev)
  - `drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.o`, `pcie.o` (BCM4359 `0x43ef`/`0x4359` + `request_firmware_direct`)
  - `drivers/nfc/nxp-nci/i2c.o` (PN547/PN548 NFC)
  - `drivers/remoteproc/qcom_q6v5_mss.o`, `qcom_q6v5_pas.o` (Hexagon Modem, ADSP, SLPI)
  - `drivers/clk/qcom/gcc-msm8996.o`, `mmcc-msm8996.o`, `clk-cpu-8996.o`
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
ac9ba7f8eafa9e43bab9ebe9b9d00f6ed07f44341f5203bf0bf5640e48a8380d  arch/arm64/boot/dts/qcom/msm8996-sony-xperia-tone-dora.dtb
d494fba2e417079addb475f83b0778bfe4e917f0a881c9b61514a0721a2c55c6  arch/arm64/boot/dts/qcom/msm8996-pmi8996-sony-xperia-tone-dora.dtb
6d2108e36a648045355c928c512c80c5304c8b3016a50babd842bcd837bf39eb  arch/arm64/boot/dts/qcom/msm8996-v3.0-sony-xperia-tone-dora.dtb
ad5e41d24dcde839ade68ec3dc70e27ec4475a118e92b652f123a34cda304b0d  arch/arm64/boot/dts/qcom/msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb
1b1e2428af55547da5822ffe41be69bddcb2da9e696b2dc023419cbcaffcfeaf  drivers/gpu/drm/panel/panel-sony-dora-incell.o
6d5b6a9632c91eea69baa87ee80acb2340dafeed236dbaf55937a0a825202ac2  drivers/platform/somc/somc-dora-platform.o
4f909b718c71941000fa314b538f2cbe976ba36d861196e3b0d352bc0b48f4bd  drivers/input/rmi4/rmi_i2c.o
63817ceacaa80ffd0b2fce16e88e70d38848c1f53cf4c845adc9c2f6b28f0599  drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.o
ea5fc75f83f04f394cf1643fed42deb32a8ab15226940b5737e65ede53b73320  drivers/net/wireless/broadcom/brcm80211/brcmfmac/pcie.o
d13ea4e1fa97fd5f9a603f904c49197dd47174f9319306949d5971ce0f69b80d  drivers/mmc/core/slot-gpio.o
a042edd4a62fd0cd734b112cefe24c3f28cbd4bb188c8adc97e6330798a99a05  drivers/bluetooth/hci_bcm.o
9c8e790b002e4cdb24efdd11d0817bc39acd618261b95e48b3f045f68ae0e9a0  drivers/nfc/nxp-nci/i2c.o
725a0e9c623bce3b538c54c4ac9b32cb4c1b95d8b65d5f326d482b70ff31cce1  drivers/regulator/qcom-labibb-regulator.o
76c592c4d606d56cd972941974677b4704808578a5cd7331584a106a3bcbdc96  drivers/clk/qcom/clk-cpu-8996.o
```
