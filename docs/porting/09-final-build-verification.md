# 09. Итоговый отчёт верификации полной сборки Linux 5.15.220 (`dora`: `F8131` / `F8132`)

Настоящий документ фиксирует независимые, проверяемые доказательства успешного выполнения **полной сборки ядра Linux 5.15.220** (ветка `kernel-5.5`, `dora_defconfig`) для смартфонов **Sony Xperia X Performance (`F8131` Single-SIM и `F8132` Dual-SIM, Qualcomm MSM8996)** в текущем контейнере, а также воспроизводимую инструкцию для WSL2 Ubuntu 22.04 LTS.

---

## 1. Среда сборки и компилятор

- **Исходное дерево:** `/tmp/dora-src` (синхронизировано 1-в-1 с веткой `kernel-5.5`)
- **Каталог сборки (`O=`):** `/tmp/dora-clean-out`
- **Скрипт сборки и упаковки S1:** `scripts/dora/build-dora-5.15.sh` (`DEFCONFIG=dora_defconfig`)
- **Кросс-тулчейн ARM64:** `aarch64-linux-gnu-gcc (Ubuntu 12.3.0-1ubuntu1~22.04.3) 12.3.0`, `GNU ld (GNU Binutils for Debian) 2.40`
- **Строка версии внутри слинкованного `vmlinux`:**
  ```text
  Linux version 5.15.220-dora-los21 (root@localhost) (aarch64-linux-gnu-gcc (Ubuntu 12.3.0-1ubuntu1~22.04.3) 12.3.0, GNU ld (GNU Binutils for Debian) 2.40) #2 SMP PREEMPT Tue Nov 3 11:27:29 UTC 2026
  ```
- **Статистика лога сборки (`build.log`, 5383 строки):**
  - Количество ошибок компиляции/линковки (`: error:`): **`0`**
  - Количество предупреждений компилятора (`: warning:`): **`0`**
  - Целевые драйверы порта (`somc-dora-platform.o`, `panel-sony-dora-incell.o`, `rmi_i2c.o`, `brcmfmac/{pcie,firmware}.o`, `mmc/core/{core,host,slot-gpio}.o`) дополнительно проверены с жёстким флагом `KCFLAGS="-Werror"`: **`0` предупреждений**.

---

## 2. Сводная таблица собранных артефактов, размеров и SHA-256

| Артефакт | Точный размер (байт) | Размер | SHA-256 | Проверка заголовка / формата |
|---|---:|---:|---|---|
| `vmlinux` | `43 324 152` | 41.32 МиБ | `5a0f3061ba67373feebfc0c8f9193fbe14f75cfb24bdc48ffb968026bfe8ffa4` | `ELF64`, `AArch64`, Entry `0xffffffc008000000` |
| `System.map` | `6 796 460` | 6.48 МиБ | `e04ecf5d240f667ade2d0550c8c954e703ced905ee461de62f9d3c82ad492745` | Полная таблица символов ядра |
| `arch/arm64/boot/Image` | `34 677 248` | 33.07 МиБ | `c6bafd92336eec4505e3706c782deba517214583b252ab790c1aa6c12fbf9186` | Magic @ `0x38` = `0x41524d64` (`ARM\x64`) |
| `arch/arm64/boot/Image.gz` | `13 038 821` | 12.43 МиБ | `69ce87a47e5c2ad3b25ed1b3e7a412e93287f35ef8e139f2ffbd797a81a41698` | GZIP stream |
| `arch/arm64/boot/Image.gz-dtb` | `13 328 377` | **12.71 МиБ** | `26635b772eda4a039636938368940ef6436ba2634222e48dbfc770f748d3a713` | `Image.gz` + 4 `.dtb` Sony S1 (запас > 51 МиБ в 64 МиБ `boot`) |
| `msm8996-sony-xperia-tone-dora.dtb` | `72 139` | 70.45 КиБ | `7700e0b5c2adbadebe4a1a53df2ebcb7f84958ab5b1f5fa5ced03b4817b256df` | FDT magic `0xd00dfeed` (MSM8996 v2/v3.1 + PMI8994) |
| `msm8996-pmi8996-sony-xperia-tone-dora.dtb` | `72 147` | 70.46 КиБ | `860ee441f344733f57ffa077c42b88a7560668a949a34147a6f3c5003816ce8d` | FDT magic `0xd00dfeed` (MSM8996 v2/v3.1 + PMI8996) |
| `msm8996-v3.0-sony-xperia-tone-dora.dtb` | `72 631` | 70.93 КиБ | `a676c934ff95d65b269402f11972f0f9fbb6d295c48b28e6be0fdaaa1ee95293` | FDT magic `0xd00dfeed` (MSM8996 v3.0 + PMI8994) |
| `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` | `72 639` | 70.94 КиБ | `d8be560422e93d06514e8c821e7aaea0314cddfe900f50783f162b842f7094c6` | FDT magic `0xd00dfeed` (MSM8996 v3.0 + PMI8996) |
| `modules_staging/lib/modules/5.15.220-dora-los21/` | 46 модулей `.ko` | Stripped + `depmod` | Прошли `INSTALL_MOD_STRIP=1` + `DEPMOD` | Все критические драйверы SoC/Storage/Display/Wi-Fi/BT/NFC встроены (`=y`), периферийные USB/сетевые — `.ko` |

---

## 3. Верификация символов `System.map`, таблицы `__param` и декомпилированного `.dtb`

### 3.1. Подтверждение линковки ключевых драйверов порта в `vmlinux` (`System.map`)
```text
ffffffc0088cea34 t somc_dora_panel_probe
ffffffc0089c0e04 t brcmf_fw_request_sony_binary
ffffffc008abbc30 t rmi_i2c_suspend
ffffffc008abbe10 t rmi_i2c_probe
ffffffc008bd27c0 t sdhci_msm_probe
ffffffc008c5da80 t somc_dora_plat_probe
ffffffc008c5dd64 T somc_dora_get_bcmdhd_fw_path
ffffffc008c5e5e0 t fpc1145_tee_probe
ffffffc009965a7c t dt_cpufreq_platdrv_init
ffffffc009965aa4 t cpufreq_dt_platdev_init
```

### 3.2. Подтверждение built-in параметров `/sys/module/bcmdhd/parameters/*` в секции `__param`
Поиск по `vmlinux` подтверждает регистрацию параметров через `MODULE_PARAM_PREFIX "bcmdhd."` без обращения к `THIS_MODULE`:
```text
0x1375e90 bcmdhd.op_mode
0x1375ea0 bcmdhd.nvram_path
0x1375eb8 bcmdhd.firmware_path
```

### 3.3. Инспекция декомпилированного `msm8996-sony-xperia-tone-dora.dtb` (`dtc -I dtb -O dts`)
- **`/chosen/bootargs`:** `earlycon=msm_serial_dm,0x75b0000` удалён (`bootargs = "androidboot.hardware=qcom user_debug=31 msm_rtb.filter=0x237 ehci-hcd.park=3 cma=64M@0-0xffffffff coherent_pool=2M loop.max_part=16"`), исключён ранний `SError` на серийных `F8131` / `F8132`.
- **`sdhci@7464900` (`sdhc1` eMMC 5.1):** `reg = <0x7464900 0x314 0x7464000 0x800>`, подключены `GCC_SDCC1_BCR` и `RPM_SMD_XO_CLK_SRC`.
- **`wled@d800` (`pmi8994_wled`):** свойство `qcom,cabc` удалено, включены 3 гирлянды (`0, 1, 2`), лимит `20000 uA` (`0x4e20`), `default-brightness = 512` (`0x200`).
- **`uim-detect-en` (`gpio29`) и `usb-detect` (`gpio24`):** конфликт с `output-high` устранён, оба узла содержат чистый `output-low;`, цифровой `usb3-id` удалён (`0` вхождений), канал `adc-chan@39` (`usb_id_lv`) присутствует в `pm8994_vadc`.

---

## 4. Воспроизводимая сборка в WSL2 Ubuntu 22.04 LTS (Clang/LLD или GCC)

```bash
# Установка зависимостей в WSL2 Ubuntu 22.04 LTS
sudo apt update
sudo apt install -y git build-essential bc bison flex libssl-dev libelf-dev \
  dwarves xz-utils lz4 device-tree-compiler python3 \
  clang lld llvm gcc-aarch64-linux-gnu

# Сборка для LineageOS 21 (Android 14)
./scripts/dora/build-dora-5.15.sh

# Сборка для LineageOS 22 (Android 15 — с включением CO-RE BTF через pahole)
ENABLE_BTF=1 ./scripts/dora/build-dora-5.15.sh
```
