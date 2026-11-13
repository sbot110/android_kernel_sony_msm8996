# Stage 9: рабочий LOS21/Sony4.4 reference и boot qualification Linux4.19

По сообщению пользователя, **LineageOS21 успешно работает с исходным Sony
Linux4.4.302+**. Это стабильный runtime reference. Исторические сообщения
`bpfloader exit1`, `BoringSSL self-check failure`, `vold failure` не следует
называть текущими ошибками этой работающей системы. Совместимость нового
Linux4.19 с тем же LOS21 userspace остаётся **не проверенной на телефоне**.
Host ABI/config tests и запуск reference4.4 не доказывают запуск4.19.

## Что реально доступно

Read-only поиск Stage9 в доступных `/workspace` и `/tmp` не нашёл boot.img,
Android distribution/target-files archive или ramdisk, идентифицированных
как артефакты работающей LOS21/Sony4.4 сборки. Подробный record roots,
exclusions, ошибок обхода и найденных kernel-only файлов:
`validation-stage9/boot-artifact-search.json`. Git/credential/provider
metadata directories явно исключены; symlink directories не обходились,
block/character devices не читались. Read-only `git ls-tree` всех четырёх
доступных local heads/remote-tracking refs также не обнаружил boot-image
или distribution blobs; checkout/fetch/изменение refs не выполнялись.

| Артефакт | Фактический статус | Чего он не подтверждает |
| --- | --- | --- |
| Официальный Sony `kernel-dtb-dora` | Сохранён kernel-only prebuilt из Sony revision `8176b089...`, SHA256 `75a8dc67020fb672251a9c737ba14e1082348cd06fde8e159174ad4595ab614c`; прежний parser/provenance record Stage7/8 доступен | Это не boot.img и не идентифицированный kernel/ramdisk работающей LOS21 сборки пользователя |
| Stage8 `Image.gz-dtb` | Kernel4.19 + четыре ordered DTB, прежняя проверенная сборка | Нет Android boot header, LOS21 ramdisk, actual loader acceptance или phone runtime |
| `usr/initramfs_data.cpio` внутри `O=` | Пять kernel-generated initramfs файлов найдены в build outputs | Это не предоставленный Android14/LOS21 ramdisk |
| Рабочий LOS21/Sony4.4 boot.img и его ramdisk | Не предоставлены и не найдены | Header/page size/load addresses/partition capacity/actual DTB choice пока неизвестны |

Старые source/prebuilt checks не считаются новыми аппаратными испытаниями.
Новый анализ реального boot header/ramdisk сейчас невозможен из-за отсутствия
нужного файла; parser не изменён и предполагаемый boot.img не создавался.

## Как предоставить артефакты существующей LOS21 сборки

Нужен output **той самой сборки**, для которой подтверждена успешная загрузка
с Sony4.4, либо уже имеющаяся host backup копия её установленного boot.img.
Произвольный новый `boot.img` из другого build output не становится working
reference по имени файла. Зафиксировать model F8131/F8132, build fingerprint,
Sony kernel release/commit, источник файла и связь с успешным runtime.

Сначала найти существующие файлы, ничего не собирая и не меняя source repos:

```sh
rg --files --hidden --no-ignore /absolute/path/to/existing/LOS21/out/target/product \
  -g 'boot.img' -g 'ramdisk.img' -g '*target_files*.zip' \
  -g 'build.prop' -g '*fingerprint*' -g 'misc_info.txt'
```

Путь `ACTUAL_PRODUCT` ниже заменить найденным product output. Он не
выбирается автоматически по Single/Dual SIM. Команды создают только новую
host-копию в workspace, не изменяют LOS21/device/vendor/Sony4.4 sources и
не выполняют операций на телефоне:

```sh
dora_los21_out='/absolute/path/to/existing/LOS21/out/target/product/ACTUAL_PRODUCT'
test -f "$dora_los21_out/boot.img"
dora_reference_dir=$(mktemp -d /workspace/los21-sony44-reference.XXXXXX)
cp -- "$dora_los21_out/boot.img" "$dora_reference_dir/los21-sony44-working-boot.img"
cmp -- "$dora_los21_out/boot.img" "$dora_reference_dir/los21-sony44-working-boot.img"
sha256sum -- "$dora_los21_out/boot.img" "$dora_reference_dir/los21-sony44-working-boot.img"
stat --format='%n %s bytes' "$dora_reference_dir/los21-sony44-working-boot.img"
printf '%s\n' "$dora_reference_dir"
```

Если найден соответствующий отдельный `ramdisk.img`, скопировать и
захэшировать его так же. Standalone ramdisk необязателен для parsing
поддерживаемого boot.img: embedded section уже проверяется и хэшируется
parser. При наличии обоих их SHA256 должны соответствовать одному payload;
несовпадение требует выяснения provenance. Existing `build.prop`/fingerprint
и pinned `repo manifest -r` полезны как build identity, но `misc_info.txt` и
BoardConfig дают только build-time параметры, не фактическое поведение loader.

Дополнительно нужны уже полученные фактические сведения:

* Размер boot partition в bytes и provenance этого измерения. Размер файла,
  BoardConfig `BOARD_BOOTIMAGE_PARTITION_SIZE` или размер ZIP entry не заменяет
  размер GPT partition; здесь partition access не выполняется.
* Captured effective `/proc/cmdline`/bootloader cmdline работающей системы,
  actual selected `qcom,msm-id`/board-id/pmic-id, LCD ID и bootloader/kernel logs.
* Подтверждение Sony wrapper/header/signature и loader relocation/decompression
  behavior для этого bootloader. Header fields и порядок appended DTB сами
  по себе не доказывают загрузку по этим адресам и выбор конкретного дерева.

После предоставления реальных файлов и размера -- read-only inspection:

```sh
python3 scripts/dora/check-boot-image.py --self-test \
  --boot-image /actual/reference/los21-sony44-working-boot.img \
  --partition-size ACTUALLY_OBSERVED_BYTES \
  --bootloader-cmdline /actual/reference/observed-effective-cmdline.txt \
  --kernel /actual/stage9-out/arch/arm64/boot/Image.gz-dtb \
  --config /actual/stage9-out/.config \
  --report /tmp/dora-working44-and-kernel419.json
```

Этот отчёт проверит source header/page layout, compression, embedded ramdisk,
declared offsets/reservations reference image и отдельный4.19 kernel extent/
DTB metadata. `--kernel` не пересчитывает автоматически память нового kernel
под адреса старого header и не разрешает перепаковку. Если позже появятся
два **реальных**, независимо qualified boot.img, `--compare SECOND_IMAGE`
с отдельным `--compare-partition-size` сравнит header/compression/sections/
ramdisk/DTB layout. Неизвестный Sony wrapper/header version должен остаться
явной ошибкой; его нельзя обходить guessed offsets. Boot image generation,
flash/format/fastboot boot/dd и block-device reads этим workflow не выполняются.

## Конкретные P0 measurements/logs для остающегося handoff

| P0 | Уже подтверждённый source контракт | Недостающее фактическое evidence |
| --- | --- | --- |
| APSS watchdog | Final Tone и официальный Sony DTB: base0x9830000/size0x1000, SPI28/29, bark12s/pet10s; source default32765Hz | Captured WDT0_EN/STS/BARK_TIME/BITE_TIME сразу после loader и после первого service с timestamps/clock provenance; secure/non-secure ownership и разрешённость доступа, реальная bark/bite IRQ routing и reset/panic/suspend logs. Не читать write-only/reset registers и не предлагать произвольный devmem: capture требует проверенного диагностического пути и known ownership |
| USB/charging | Dora DWC3 parent и QUSB2 init возвращают EOPNOTSUPP до своих reset/clocks/UTMI/VBUS/PHY writes | Working4.4 logs для cable absent/present и insertion/removal, фактический DPDM/ID/VBUS/BC1.2 owner; initial charger/PHY reset/qscratch/PMIC state, calibrated battery current/voltage/temperature и JEITA/BCL/current-limit status, firmware→kernel и shutdown ownership. Source gate не доказывает прекращение firmware charging; не снимать его ради ADB |
| Logging/recovery | UART/ramoops source description и pstore config сохранены; USB/ADB недоступны в4.19 | Физически доступный serial transport с baud/pin/access provenance; настоящий ramoops persistence/reboot record, выбранный DT/reservation, подтверждённая доступность recovery/TWRP. Отсутствие изображения при gated display не доказывает отсутствие kernel entry |
| SDHC1 boot chain | Source RPM→banks/ACK→GCC/CX→L20/S4→clocks/NoC→controller contracts сохранены; четыре отдельные diagnostic DTB | Actual RPM ACK/CID/IRQ/DMA/CQE/GPT/partition-name/HS200/HS400/early-init logs. Device enumeration и integrity не подтверждаются host tests; запись в eMMC не разрешена |

Новые фиктивные ACK, PHY/charger providers или механизмы отключения защиты
для получения этих данных не добавляются. F8131/F8132, четыре SoC/PMIC
variants и пять LCD-ID panels требуют идентификации по фактическому DT/LCD ID,
а не по SIM-модели. До закрытия CPU/shared-power и перечисленных handoff
зависимостей: **BOOT TEST READY: NO; BOOTED: NO; DAILY USE READY: NO** для4.19.
