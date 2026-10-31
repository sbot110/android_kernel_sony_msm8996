# 01. Инвентаризация исходных репозиториев и происхождение ветки `kernel-5.5`

## 1. Целевое устройство и назначение ветки

- **Устройства:** Sony Xperia X Performance (`dora`, платформа Sony `tone`, SoC Qualcomm Snapdragon 820 / `MSM8996`):
  - **F8131** — Single SIM (`ss` по `LTALabel`, `SIM1_DET` на `TLMM GPIO 112`).
  - **F8132** — Dual SIM DSDS (`dsds` по `LTALabel`, `SIM1_DET` на `TLMM GPIO 112` + гибридный лоток `MicroSD / UIM2` с `CD` на `TLMM GPIO 40` и `UIM2_DET_EN` на `TLMM GPIO 29`).
- **Целевые ОС:** LineageOS 21 (Android 14 — основной приоритет) и LineageOS 22 (Android 15 — перспективная цель).
- **Целевая ветка Git:** `kernel-5.5` в репозитории `https://github.com/sbot110/android_kernel_sony_msm8996` (имя ветки задано техническим заданием; фактическая версия ядра внутри дерева — **Linux 5.15.220**).

---

## 2. Аудит существующих веток основного репозитория (`sbot110/android_kernel_sony_msm8996`)

Перед созданием ветки `kernel-5.5` проверены все локальные и удалённые ссылки основного репозитория и фактическое содержимое корневого `Makefile` каждой ветки:

| Ветка | Точный SHA-1 коммита | Версия в `Makefile` | Назначение и статус |
|---|---|---|---|
| `main` | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | **Linux 4.4.302** | **Выбранный аппаратный эталон** (`dora: synchronize LineageOS 20 working build`). Проверенное на реальном F8132 рабочее ядро LineageOS 20. |
| `Lineageos-21` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | Linux 4.4.302 | Экспериментальная адаптация 4.4.302 под Android 14 (частичные backport-патчи BPF/cgroup, early-mount fstab, dm-verity). |
| `dora-full-state-20261017` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | Linux 4.4.302 | Архивный снимок рабочего дерева 4.4.302. |
| `wpa2-dora-debug` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | Linux 4.4.302 | Отладочная ветка исследования потери CCMP cipher в `bcmdhd` / `cfg80211`. |
| `4.9-test-not-startable` | `d8587034624fdf13432814248e4d63fa2779c` (`d8587034624fdf13432814248e4d63fa2771879c`) | Linux 4.9.230 | Незапускаемый ранний тест 4.9 (не использован как база). |
| `kernel-4.19-701905467803253630` | `8fca411c60281990b223dbfa25436a138cf16e5e` | Linux 4.19.318 | Экспериментальная ветка 4.19 (сохранена без изменений). |

Подтверждено до начала работ: ветка `kernel-5.5` отсутствовала как в `refs/heads/*`, так и в `refs/remotes/origin/*`.

---

## 3. Выбор базы Linux 5.15 и фиксация отставания `aosp-mirror`

Проверены все указанные в задании внешние репозитории (строго в режиме чтения):

| Репозиторий | Ветка / Тег | Точный SHA-1 | Версия ядра | Решение |
|---|---|---|---|---|
| `https://android.googlesource.com/kernel/common` | `android14-5.15` | `8b71bff53163de44c0ab719eb30bc3913635557f` | **5.15.220** | **Выбрана как основная база `kernel-5.5`**. Официальный актуальный релиз Android Common Kernel для Android 14. |
| `https://android.googlesource.com/kernel/common` | `android14-5.15-lts` | `100b5cf1a70581f110187de55a36794eb79cfced` | 5.15.223 | Промежуточная LTS-ветка слияния; основная ветка Android 14 — `android14-5.15` (`5.15.220`). |
| `https://github.com/aosp-mirror/kernel_common` | `android14-5.15` | `a9ee5509a0c552cb7bd7846b03b538dc2cba7585` | **5.15.194** | **Зафиксировано отставание зеркала GitHub на 26 LTS-подверсий** (5.15.194 против официального 5.15.220). В соответствии с п. 3 задания использован официальный источник Google `android.googlesource.com/kernel/common`. |
| `https://github.com/sonyxperiadev/kernel` | `aosp/LA.UM.6.4.r1` | `696bf91693d3d8b03dd8a4afa1b12a78b33b6d73` | 4.4.x | Справочный оригинал Sony Open Devices MSM8996 Tone. |
| `https://github.com/sonyxperiadev/kernel-sony-msm-4.4-common` | `aosp/LA.UM.6.4.r1` | `8176b0892ba13a0401a22d7322bc109f473a24b9` | 4.4.x | Справочный пакет общих файлов Sony 4.4. |
| `https://github.com/sonyxperiadev/kernel` | `aosp/K.P.2.0.r1` | `c13c2d70f7d33208aa8cb8146091b40141a728c0` | 5.15.94 | Изучен как архитектурный ориентир Sony 5.15; содержит только платформы `nagara`/`yodo` (SM8450/SM8475), поддержка `MSM8996`/`tone`/`dora` в нём полностью отсутствует. |
| `https://github.com/sonyxperiadev/kernel-sony-msm-5.15-common` | `aosp/K.P.2.0.r1` | `ba576bd1b47edf8e0a5912b0c236a09514a08cce` | 5.15 build | Изучена организация сборочных скриптов Clang/LLVM Sony 5.15. |
| `https://github.com/torvalds/linux` | `refs/tags/v5.15` | `8bb7eca972ad531c9b149c0a51ab43a417385813` (tag `dc7089468610`) | 5.15.0 | Эталон mainline-подсистем DRM/MSM, `rmi4`, `brcmfmac`, `qcom_q6v5`, `labibb`. |

### Смежные репозитории устройства (использованы только для чтения)

- `sbot110/android_device_sony_dora` (`main` @ `5e9385a65257a28f9a3974342331644a3bbcdd49`) — проверены `BoardConfig.mk`, asserts для `F8131`/`F8132`, тепловые профили `thermanager.xml`.
- `sbot110/android_device_sony_tone-common` (`main` @ `2701d2aecab52b30c3672ecebbad6ae1910cc212`) — проверены параметры загрузчика S1 (`BOARD_KERNEL_BASE := 0x80000000`, `BOARD_KERNEL_PAGESIZE := 4096`, `BOARD_RAMDISK_OFFSET := 0x02000000`, `BOARD_KERNEL_TAGS_OFFSET := 0x01E00000`, `BOARD_RAMDISK_USE_XZ := true`, `BOARD_KERNEL_IMAGE_NAME := Image.gz-dtb`), `fstab.qcom` (`/system` ext4, `/oem` -> `/vendor` squashfs, `/cache` f2fs, `/data` ext4/f2fs + шифрование), `init.qcom.rc` и `init.qcom.msim.sh` (`LTALabel` -> `F8131`=`ss`, `F8132`=`dsds`).
- `sbot110/android_vendor_sony` (`main` @ `bb8c352c8da4942f6189fd08d0c1ec947f25e8ef`) — проверены пути прошивок (`a530_zap.mdt`, `adsp.mdt`, `mba.mbn`, `modem.mdt`, `slpi.mdt`, `venus.mdt`, `wlan/bcmdhd/fw_bcmdhd.bin`, `wifi/bcmdhd.cal`).
- `sbot110/android_hardware_sony_thermanager` (`main` @ `78b0fbf2f8e9c5f3e7b8e8e3fb27469360e522d1`) — проверены имена sysfs-зон `thermal_zone*` и каналов PM8994 VADC.
- `sjllls/android_kernel_sony_msm8996` (`lineage-18.1-LA.UM.8.4` @ `9ad723b248a08ae8e7c89140cb0242e25c6b0115`) — проверен как дополнительный справочник миграции MSM8996.
