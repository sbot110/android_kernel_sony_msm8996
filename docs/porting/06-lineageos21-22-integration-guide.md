# 06. Интеграция ядра `kernel-5.5` (Linux 5.15.220) в деревья LineageOS 21 / 22 (`dora` / `tone-common`)

Настоящая памятка описывает минимальные изменения в деревьях устройства (`android_device_sony_tone-common` и `android_device_sony_dora`, которые в рамках подготовки ядра использовались исключительно в режиме чтения), необходимые при переключении сборки LineageOS 21 (Android 14) или LineageOS 22 (Android 15) с ядра 4.4.302 на ветку `kernel-5.5`.

---

## 1. Манифест `local_manifests/dora_kernel_515.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <remove-project name="LineageOS/android_kernel_sony_msm8996" />
  <project path="kernel/sony/msm8996"
           name="sbot110/android_kernel_sony_msm8996"
           remote="github"
           revision="kernel-5.5" />
</manifest>
```

---

## 2. Настройки `BoardConfigCommon.mk` (`device/sony/tone-common`)

```makefile
# Архитектура CPU: строго ARMv8.0-A Kryo (предотвращает boringssl-self-check-failed)
TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_VARIANT := kryo

# Параметры Sony S1 Bootloader (сохранены неизменными от 4.4.302)
BOARD_KERNEL_BASE := 0x80000000
BOARD_KERNEL_PAGESIZE := 4096
BOARD_KERNEL_TAGS_OFFSET := 0x01E00000
BOARD_RAMDISK_OFFSET := 0x02000000
BOARD_RAMDISK_USE_XZ := true
BOARD_BOOTIMAGE_PARTITION_SIZE := 67108864

# Ядро Linux 5.15 (ветка kernel-5.5)
TARGET_KERNEL_SOURCE := kernel/sony/msm8996
TARGET_KERNEL_CONFIG := dora_defconfig
TARGET_KERNEL_CLANG_COMPILE := true
BOARD_KERNEL_IMAGE_NAME := Image.gz-dtb

# Явный список 4 DTB для склейки в Image.gz-dtb при сборке внутри AOSP/LineageOS
TARGET_KERNEL_APPEND_DTB := true
BOARD_KERNEL_CMDLINE := androidboot.hardware=qcom user_debug=31 msm_rtb.filter=0x237 ehci-hcd.park=3 cma=64M@0-0xffffffff coherent_pool=2M zram.backend=z3fold loop.max_part=16

# Переход графического стека с legacy KGSL 4.4 на DRM/MSM + Freedreno/Turnip
BOARD_USES_DRM_HWCOMPOSER := true
TARGET_USES_GRALLOC4 := true
BOARD_GPU_DRIVERS := freedreno
```

---

## 3. Проверка `rootdir/etc/fstab.qcom` (предотвращение `vold-failed` в Android 14/15)

1. Разделы `system` (`ext4`) и `/oem` -> `/vendor` (`squashfs`, сжатие LZ4) поддерживаются `dora_defconfig` напрямую.
2. Раздел `/cache` (`f2fs`) поддерживается напрямую (`CONFIG_F2FS_FS=y`, `CONFIG_F2FS_FS_LZ4=y`).
3. Для раздела `/data` (`userdata`) в Android 14 / 15 **нельзя оставлять устаревший флаг `forceencrypt=footer`** (FDE удалён из `vold` в Android 13/14). Рекомендуемая строка для чистой инициализации FBEv2 (программный fallback `BLK_INLINE_ENCRYPTION_FALLBACK=y` включён в `dora_defconfig`):

```text
/dev/block/bootdevice/by-name/userdata  /data  f2fs  noatime,nosuid,nodev,discard,inlinecrypt,fsync_mode=nobarrier  wait,check,quota,reservedsize=128M,fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized,keydirectory=/metadata/vold/metadata_encryption
```

*(Примечание: для первого диагностического запуска без форматирования `/metadata` допускается временно убрать `fileencryption=...`, проверив монтирование чистой ФС, после чего включить FBEv2).*

---

## 4. Символьные ссылки прошивок Wi-Fi для `brcmfmac` (опционально)

В `drivers/net/wireless/broadcom/brcm80211/brcmfmac/firmware.c` уже встроен автоматический ядерный fallback на заводские файлы Sony `/vendor/etc/firmware/wlan/bcmdhd/fw_bcmdhd.bin` и `bcmdhd.cal`. Тем не менее, при пересборке `vendor` рекомендуется добавить стандартные симлинки Linux:

```text
/vendor/firmware/brcm/brcmfmac4359-pcie.bin -> /vendor/etc/firmware/wlan/bcmdhd/fw_bcmdhd.bin
/vendor/firmware/brcm/brcmfmac4359-pcie.txt -> /vendor/etc/wifi/bcmdhd.cal
```
