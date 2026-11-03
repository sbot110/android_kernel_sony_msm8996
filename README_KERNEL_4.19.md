# Linux 4.19.318 для Sony Xperia X Performance

Рабочая ветка: `kernel-4.19-701905467803253630`. Цель — F8131 (Single SIM)
и F8132 (Dual SIM/DSDS), LineageOS 21 / Android 14 ARM64 userdebug.
Ни одна модель не загружалась с этим ядром. Точные проверенные SHA, результаты
чистой сборки и ограничения: [отчёт](Documentation/sony/dora-build-validation.txt).

## Изменения этапа 4

Внутренняя память Dora — **SDHC1 eMMC, `7464900.sdhci`**. Это подтверждают
все четыре итоговых Sony DTB, early-mount fstab, pinned tone-common init и
ueventd. SDHC2 `74a4900.sdhci` — съёмный MicroSD/hybrid UIM2. Прежний вывод
об UFS был ошибочным. UFS host и PHY отключены во всех Dora DTB; их generic
MSM8996 код и регрессионные тесты сохранены. Подтверждённых UFS-вариантов
F8131/F8132 нет. [Доказательства](Documentation/sony/dora-storage-audit.md).

Восстановлены исходные eMMC/SD registers, IRQ, native GCC clocks, реальные
PM8994 rails, pad drive/bias, HS200/HS400 plans и ненулевые NoC vectors.
MMC aliases резервируют mmc0/mmc1 независимо от probe order; UIM2 GPIO
управляется конкретным removable host. Исправлены SDHCI provider/defer,
clock/power errors и cleanup. Неопределённость после потерянного ACK
блокирует дальнейшие power/clock операции; cached success не считается
восстановлением физического питания. Контроллеры пока отключены.

Добавлен настоящий SMD RPM logical-corner frontend: logical 1..7 → wire
0..6, corn/vfc, ACTIVE/SLEEP, BOTH/AO/floor aggregation, correlated ACK,
rollback, quarantine и безопасный lifetime. GCC/MMCC подключены к typed
CX consumer API с обязательным forced ACK. Перенесены 56 GCC и 49 MMCC
frequency limits после конечных Sony v3 overrides; SDCC2 возвращены 40/80 MHz.
MMPLL4/9 используют реальный L12 1.8 V. Эти ресурсы остаются safety-gated.
[Контракты clocks](Documentation/sony/dora-clock-voltage.md).

CPU получил fault-tested движок ACD→voltage→PLL с двумя voltage leases,
readback, rollback и исходными fuse plans. **Аппаратный адаптер не подключён**:
активный APCC/CBF provider остаётся read-only. Устранены конкурирующие native
RPM owners четырёх фаз S8/S9/S10/S11. Сохранены `maxcpus=2`, CPU_FREQ=n и
CPU_IDLE=n. [Ограничения CPU](Documentation/sony/dora-cpu-transactions.txt).

Исправлены SquashFS xattrs для vendor=oem и VFAT CP437/ISO-8859-1 для
modem/bluetooth firmware mounts. Проверяются реальные Binder/legacy ION/
fscrypt headers для ARM64 и COMPAT и настоящий ION command dispatch.
Старые bpfloader/BoringSSL/vold failures не объявляются устранёнными.
[Android 14](Documentation/sony/dora-android14-audit.md).

Сохранены предыдущие реализации: 145-node MSM8996 NoC/BIMC, Sony BCMDHD WPA2,
QUSB2 tuning, ION, Tasha/QPNP исходники, пять LCD-ID панелей 1080p60 и
аппаратные различия четырёх SoC/PMIC вариантов. Не включены AUO120/Dynamic FPS,
GPU, незавершённые display/charging chains или DVFS. Наличие исходников не
является доказательством работы аппаратуры.

## Четыре аппаратных варианта

Порядок DTB в `Image.gz-dtb` повторяет оригинальный Sony Makefile:

| SoC / PMIC | DTB | msm-id revision | Первые слова pmic-id |
| --- | --- | --- | --- |
| v3.0 / PMI8994 | `msm8996-v3.0-sony-xperia-tone-dora.dtb` | `0x30000` | `0x20009 0x2000a` |
| v3.0 / PMI8996 | `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` | `0x30000` | `0x20009 0x10013` |
| v3.1 / PMI8994 | `msm8996-sony-xperia-tone-dora.dtb` | `0x30001` | `0x20009 0x2000a` |
| v3.1 / PMI8996 | `msm8996-v3.1-pmi8996-sony-xperia-tone-dora.dtb` | `0x30001` | `0x20009 0x10013` |

Все используют SoC ID 246 и board-id `<8 0>`. F8131/F8132 не привязаны к
PMIC, stepping или одной панели. Наличие четырёх FDT в образе не доказывает
выбор правильного дерева загрузчиком Sony. Полная матрица:
[dora-compatibility.md](Documentation/sony/dora-compatibility.md).

## Оставшиеся P0

| Цепочка | Следующее обязательное действие |
| --- | --- |
| eMMC / physical RPM | Завершить подтверждённый replay UV/SWEN/load и SLEEP для физических rails; проверить integrated boot-clock handoff, затем отдельно снимать CX и MMC gates. Нужны HS200/HS400/data-integrity, GPT/by-name и encrypted userdata tests без форматирования. |
| CPU / CPR / SAW | Подключить qualified native APCC/CBF/ACD/CPR/SPM adapter к transaction engine, согласовать fuse/binning, S8–S11 ownership, thermal/hotplug/LPM; проверить на телефоне. |
| MMCC / MDSS | Подтвердить physical L12/SLEEP recovery, DSI PHY analog ownership, incell/touch sequencing, затем отдельно активировать дисплей. |
| Adreno 530 | Завершить независимые CX/MX/PMI S2/CPR, SMMU и secure-buffer/firmware/vendor ABI dependencies. GPU отключён. |
| Зарядка / USB | Настоящие DPDM/PHY ownership, BC1.2, VBUS, MicroUSB ID/ADC и safety paths. Фиктивного DPDM regulator нет. |
| Android boot | Проверить настоящий boot header/cmdline/DTB selection/ramdisk и device/vendor ABI; LOS21 runtime не запускался. |

Аудио/LPASS/PIL/SSR/SLIMbus, modem/RIL/DSDS, touch и камеры также требуют
аппаратной интеграции. F8131/F8132 не привязываются к одной панели, PMIC или
stepping; режим SIM в ядре не принуждается. [Матрица](Documentation/sony/dora-compatibility.md),
[точные блокеры](Documentation/sony/dora-hardware-blockers.md).

## Сборка

Нужны AArch64 и ARM32 COMPAT toolchains, GNU make, bc, bison, flex, OpenSSL/ELF
headers и Python 3. Указать новый пустой OUT_DIR:

```bash
OUT_DIR="$PWD/out/dora-clean" JOBS=4 scripts/dora/build.sh
```

Скрипт проверяет ветку, итоговую .config, выполняет все 32 check-*.py,
собирает vmlinux, System.map, Image, Image.gz и четыре DTB. Проверяются
ARM64/ELF/gzip, linked implementation symbols и точный порядок appended DTB;
создаются Image.gz-dtb, toolchain.txt, config.log, build.log,
artifact-validation.json и SHA256SUMS. Явная gated policy не позволяет
случайно активировать непроверенные power chains.

Ни boot.img, ни прошивка на этом этапе не создаются. Разделы userdata,
modem, persist и TA не изменяются. SELinux, dm-verity и BoringSSL self-test
не отключены. Без критических аппаратных проверок BOOT TEST READY и
RUNTIME VERIFIED не присваиваются.

## Происхождение

- База Linux 4.19.318:
  [Tama-4-19/android_kernel_sony_sdm845-419](https://github.com/Tama-4-19/android_kernel_sony_sdm845-419),
  `lineage-20`, commit `51b2850a389ec9069d1d6f6ecd8fbf22f9c03f99`,
  tree `3feca968f677b76dbbdc0532b7d9a7b8463fb6d8`.
  [Запись импорта](Documentation/sony/dora-source-base.txt).
- Аппаратный эталон Sony 4.4.302:
  [0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1](https://github.com/sbot110/android_kernel_sony_msm8996/commit/0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1).
  История и исходные лицензии сохранены; рабочее 4.4 не изменено.
- Оригинальные Sony:
  [4.4 common](https://github.com/sonyxperiadev/kernel-sony-msm-4.4-common),
  [4.9 common](https://github.com/sonyxperiadev/kernel-sony-msm-4.9-common).
- Контракт Android устройства:
  [dora](https://github.com/sbot110/android_device_sony_dora),
  [tone-common](https://github.com/sbot110/android_device_sony_tone-common),
  [vendor Sony](https://github.com/sbot110/android_vendor_sony).

Данные пользователя о F8132/AUO Panel Name=4, LCD ID `0x11FFEA` и
1080×1920@60 относятся только к рабочему **4.4**. Они сохранены как
контрольный эталон и не являются свидетельством загрузки или дисплея 4.19.

SMBcharger не привязан в DT. Это не доказывает, что загрузчик остановил
физическую зарядку: исходное состояние PMIC и реальные токи не измерялись.
Безопасная зарядка и обычное использование не подтверждены.
