# Dora Stage 10 — анализ предоставленного boot image

Дата: 2026-11-16 UTC. Оригинальный ZIP, boot image, ramdisk и извлечённые
компоненты сохранены **вне Git**. Этот документ содержит только format,
size/hash, аппаратные metadata и ограничения; полный fingerprint, cmdline,
private payload и пользовательские бинарные компоненты не публикуются.
Оригинальный образ не изменяется и не прошивается.

Рабочая LOS21 на Sony4.4 — пользовательский runtime reference. Новый
Linux4.19 пока не имеет phone kernel-entry/init/Android logs. Формат и
self-reported Android metadata не аутентифицируют конкретный установленный
bootloader и сами по себе не подтверждают Linux4.19 boot.

## Входной артефакт и header

| Поле | Фактическое значение |
| --- | --- |
| ZIP | 11,704,070 bytes; один regular image entry |
| ZIP SHA256 | `13615e75cbbc456cc6f32d960a2d07cf0c0dbc8bf4b302edb331e64e4c8a053a` |
| Boot image | 13,283,328 bytes |
| Boot image SHA256 | `f923f9b99c468484b713529577b84e3a137d1b9cbc09393e5593258009983820` |
| Android magic / format | ANDROID!; legacy Qualcomm Android boot v0, `dt_size=0` |
| Page size | 4096 bytes |
| Kernel payload | 12,047,744 bytes at file offset4096; gzip + four appended DTB |
| Ramdisk payload | 1,224,724 bytes at file offset12,054,528 |
| Second stage / separate DT | size0 / legacy dt_size0 |
| Declared kernel address | `0x80008000` |
| Declared ramdisk address | `0x81000000` |
| Declared tags address | `0x80000100` |
| Header OS version word | raw469762474; это поле не доказывает installed firmware identity |
| Outer Sony signature / AVB footer | Parser не распознал такую оболочку; loader authentication policy не установлена |
| File extent | Завершается на ожидаемой page-padded boundary; дополнительных trailing bytes нет |
| Full boot partition dump | **Нет**: это не64MiB dump, реальный GPT partition size неизвестен |

Последняя страница содержит обычное section padding, а не доказательство
полного размера boot partition. Выделять из64MiB дампа полезную часть здесь
не требуется. Рабочий файл доказывает только нижнюю границу необходимой
ёмкости; его размер нельзя объявлять GPT capacity.

Полная исходная cmdline хранится privately. В ней обнаружен
`androidboot.selinux=permissive`; такой security bypass не наследуется
новым образом. Packer удаляет несовместимые SELinux/maxcpus tokens и
формирует однозначные `androidboot.selinux=enforcing maxcpus=2`, сохраняя
остальные проверяемые исходные tokens. Он не подставляет guessed console,
bootdevice, partition size или loader parameters.

### Реальный image ID, а не предположение о header version

Header ID: `26019fa18d9ecd502b29199901c11535675606d9`.
SHA1 расчёт для фактического legacy QCOM варианта совпадает; обычный AOSP v0
даёт `889c051b73d4f5cc6c04e1104f41b6155a6ea107` и здесь не совпадает.
Для QCOM variant после kernel/ramdisk/second bytes и каждого little-endian
u32 размера учитывается дополнительное little-endian u32 zero `dt_size`.
Даже пустой second section участвует своим zero size. Нельзя принимать
AOSP-only mismatch за повреждение этого валидного legacy image или
переименовывать ненулевой `dt_size` в современный header version.

Stage10 parser проверяет этот вариант явно. Новая упаковка выбирает
однозначно совпавший формат исходника и пересчитывает ID после изменения
kernel/cmdline/addresses; suffix ID padding не становится payload.

## Kernel и четыре appended DTB

Определён release `4.4.302+`. Gzip stream занимает10,158,160 bytes;
распакованный файл —26,853,396 bytes. ARM64 Image magic присутствует,
`text_offset=0x80000`, `image_size=30,318,592`, flags10. Runtime extent
учитывает `image_size`, а не только длину распакованного файла; BSS/zero-fill
нельзя исключить из overlap проверки.

`dt_size=0` не означает отсутствие DT: четыре FDT находятся после конца
первого gzip stream внутри kernel payload. Их фактический порядок:

| Index | Offset внутри kernel payload | DTB bytes | MSM revision | PMIC tuple, первые два слова |
| --- | --- | --- | --- | --- |
| 0 | 10158160 | 470605 | `0x30000` | `0x20009 0x2000a` |
| 1 | 10628765 | 471117 | `0x30001` | `0x20009 0x2000a` |
| 2 | 11099882 | 473675 | `0x30000` | `0x20009 0x10013` |
| 3 | 11573557 | 474187 | `0x30001` | `0x20009 0x10013` |

Во всех четырёх деревьях msm-id246, board-id8/0. Это v3.0/PMI8994,
v3.1/PMI8994, v3.0/PMI8996, v3.1/PMI8996 соответственно. Boot-only workflow
сохраняет именно этот **наблюдаемый порядок**, отдельно от исторического
normal build order. Packer сопоставляет полные msm/board/pmic tuples,
отвергает duplicates/missing/extra variants; не выбирает DTB по F8131/F8132.
Фактически выбранный bootloader DTB на данном телефоне неизвестен.

### Fixed reserved-memory metadata исходных деревьев

Следующие explicit ranges одинаковы во всех четырёх parsed reference DTB.
Это DT declarations, а не полученная от firmware runtime RAM map.

| Reserved node | Start | Size | End exclusive |
| --- | --- | --- | --- |
| `/reserved-memory/removed_regions@85800000` | `0x85800000` | `0x3000000` | `0x88800000` |
| `/reserved-memory/peripheral_region@8ea00000` | `0x8ea00000` | `0x2d00000` | `0x91700000` |
| `/reserved-memory/modem_region@88800000` | `0x88800000` | `0x6200000` | `0x8ea00000` |
| `/reserved-memory/dfps_data_mem@83400000` | `0x83400000` | `0x1000` | `0x83401000` |
| `/reserved-memory/cont_splash_mem@83401000` | `0x83401000` | `0x23ff000` | `0x85800000` |
| `/reserved-memory/cont_splash_mem_hdmi@bdd00000` | `0xb1c00000` | `0x23ff000` | `0xb3fff000` |
| `/reserved-memory/pstore_reserve_mem_region@a7f00000` | `0xa7f00000` | `0x100000` | `0xa8000000` |

Fixed ranges FDT source не заменяют dynamic reserved allocations, memory
banks, secure firmware ownership или выбранный DT, модифицированный loader.
Новый packer проверяет **свои** четыре compiled boot trees против всех их
fixed reservations; соответствующий report обязателен при final packaging.

## Ramdisk и Android first-stage init

Ramdisk: XZ-compressed newc CPIO,1,224,724 compressed bytes,
3,589,632 expanded bytes. SHA256 исходных compressed bytes:
`9a532793e779b33bad98df1576ec9b661bc04457f4f3f9148b31ba8d42d4f41e`.

`/init` — static little-endian ELF64 AArch64 (machine183),3,587,128 bytes,
без dynamic interpreter. SHA256 `/init`:
`74f3afc4adce6e0f0ad02993e7701e353c0d7777c935e417fca8b4c19edbb5c5`.
Признаки first-stage implementation есть, Magisk markers не обнаружены.
Metadata указывают Android14 / SDK34, dora / lineage_dora, userdebug/test-keys.
Это согласуется с сообщением пользователя о working LOS21/Sony4.4;
полный fingerprint и raw payload намеренно не публикуются.

Standalone fstab, policy files и kernel modules в этом ramdisk не найдены.
Это не повод придумывать отсутствующий init.rc или ослаблять SELinux:
первый stage читает embedded DT fstab, затем нуждается в actual system/oem
и их second-stage init, policy, firmware и возможных modules. Отсутствие
4.4 .ko в ramdisk не доказывает их отсутствие во внешнем vendor/oem.

Working DT содержит system ext4 и vendor, отображённый на `oem` squashfs:
`/dev/block/by-name/system` и `/dev/block/by-name/oem`. Source LineageOS21
`GetBootDevices()` и `DeviceHandler::GetBlockDeviceSymlinks()` подтверждает
literal `boot_devices=any` policy для generic by-name links; это source
контракт, не лог обнаруженного GPT. Boot-only overlay переносит эти metadata
без изменения filesystem/ro flags; normal/diagnostic DT policy сохраняется.
Проверенный source pin: LineageOS `android_system_core` commit
`ec299bf6da55702b7b9675d60a0faf2815f0dc6d`, `init/devices.cpp`,
`fs_mgr/libfstab/fstab.cpp`, `init/first_stage_mount.cpp`; эти внешние
репозитории использованы read-only, без device/vendor/userspace изменений.

Packer требует built-in RD_XZ, initrd, MMC block, GPT, ext4, SquashFS,
SELinux и необходимых Android ABI/config options. Ramdisk сохраняется
byte-for-byte. Проверка ELF/newc/metadata и даже независимое извлечение
после упаковки не исполняют Android init и не доказывают vendor kernel ABI.
SELinux enforcing с external policy должен быть проверен отдельно; рабочий
reference с permissive token не является такой проверкой.

## Почему literal исходные addresses не принимаются

| Проверяемый диапазон | Declared range / результат |
| --- | --- |
| Original compressed kernel + DTB payload | `0x80008000..0x80b85580` |
| Original expanded Image runtime extent | `0x80008000..0x81cf2000` |
| Original compressed ramdisk | `0x81000000..0x8112b014` |
| ARM64 text_offset candidate from2MiB base | `0x80080000..0x81d6a000` |
| Expanded kernel / initrd overlap | **Да** при literal original header interpretation |
| Tags candidate / expanded kernel | Также пересекаются, если DTB действительно копируется по header tags адресу |
| In-place decompression input/output risk | **Да**; compressed bytes находятся внутри предполагаемого output extent |
| Actual loader decompression/DTB/ramdisk relocation | **Неизвестны** |

Успешная работа4.4 с таким header не доказывает literal расположение.
Loader может игнорировать/переопределять поля и использовать отдельный
scratch buffer; это требуется установить по actual installed firmware или
логам. Только исправить `text_offset` недостаточно: даже соответствующий
ARM64 candidate range всё ещё пересекает исходный initrd.

Образ с новыми4.19 bytes по этим unsafe literal addresses не создаётся.
BoardConfig не используется как доказательство реального loader behavior.

## Явный source-based кандидат layout

[Профиль](dora-msm8996-lk-source-layout.json) pin-ит публичные source/blob/hash
Qualcomm CAF MSM8996 LK данные из `msm8916-mainline/lk2nd` commit
`e8af1985b6b42f490abcdf9a86dd4855f3511326`. Это не идентифицированная
установленная Sony прошивка и не разрешение считать relocation безопасной.
Profile status: **SOURCE_BASED_CANDIDATE / FIRMWARE_MATCH_UNVERIFIED**.

| Поле кандидата | Значение / условие |
| --- | --- |
| Base | `0x80000000` |
| Kernel | `0x80080000`; ARM64 text_offset0x80000 от2MiB-aligned base |
| Tags / selected DTB workspace | `0x82500000`; окно2MiB до initrd |
| Ramdisk | `0x82700000` |
| Page size |4096, совпадает с reference |
| Source scratch | `0x92300000`, source capacity0x20000000 |
| Source loader region | `0x91d00000`, size0x400000 |
| Compressed staging | scratch + page; actual extent берётся из final kernel payload |
| Expanded staging | scratch + padded new image bytes + page; extent учитывает file и BSS |
| Selected DTB padding | Source1024-byte pad и дополнительная проверяемая allowance; exact size проверяет packer |

Under this source model final kernel/initrd/DT workspace и compressed/expanded
scratch ranges должны быть попарно disjoint и не пересекать fixed DT
reservations. Packer проверяет overflow, alignment, every-tree reservations,
DTB tuple/order и actual final4.19 extent. Он не утверждает physical RAM
bounds, dynamic reservations, secure ownership или соответствие этому
профилю actual Sony loader. Эти booleans остаются false в результате.

Новый файл должен помещаться в **консервативный cap13,283,328 bytes**, то
есть быть не больше supplied working file. Это относительная проверка, а не
измеренный partition size. Если реальная GPT capacity предоставлена позже,
она проверяется отдельно;64MiB значение не угадывается.

## Итоговые Stage10 артефакты

| Поле | Фактический результат |
| --- | --- |
| Target artifact | `boot-dora-lineage21-kernel419.img` |
| Final image bytes / SHA256 | `boot-dora-lineage21-kernel419.img`: 10,985,472 bytes; SHA256 `772f3cbf74580b5b9e6935d3f88635342acbd26d4b70b39ab2336864f138eeda` |
| Final kernel/ramdisk/DTB component hashes | Image SHA256 `8cec234b28e667ab4c0c85879086c5f2bfe151cff302269fc94107cc2cbb4f21`; kernel+4DTB SHA256 `c57fc7ca5fe554970b465bf10038997e75c3c887c658884072b13bdb8f4cd88e`; unchanged XZ ramdisk SHA256 `9a532793e779b33bad98df1576ec9b661bc04457f4f3f9148b31ba8d42d4f41e`; DTB hashes: `validation-stage10/boot-SHA256SUMS` |
| Independent unpack / payload comparison | PASS: unchanged official AOSP14 unpacker + independent verifier; kernel/ramdisk byte equality; QCOM legacy v0 ID `bebb6e2828beb58baeefb1b5fd16742f8bbeb75d`; `validation-stage10/aosp-independent-roundtrip.json` |
| Exact final memory-layout report | kernel runtime (file + BSS) `0x80080000..0x8158e000`; compressed initrd `0x82700000..0x8282b014`; selected DTB workspace `0x82500000..0x82700000`; all four fixed reservations checked; actual Sony firmware/DDR/relocation unverified |
| Actual partition capacity | Не предоставлена; не выводится из размера файла |
| Artifact readiness | Не выше OFFLINE BUILT / HARDWARE UNVERIFIED; статус упаковки заполняется только по фактическому результату |
| Hardware boot / sys.boot_completed / uname4.19 | Не наблюдались |

Итоговые поля заполняются фактическими packaging/unpack reports после новой
чистой сборки. Исходный reference image и private ramdisk в публичный Git не
попадают; новый образ выдаётся отдельным host artifact. Полное состояние P0:
[P0 closure](dora-stage10-p0-closure.md), общий результат:
[Stage10 report](dora-stage10-report.md).

Неподтверждённые Sony loader/RAM/relocation, CPU CPR/SPM/APM/LMHv1 ownership,
six leases, watchdog, USB/charging и физические eMMC/GPT/init/SELinux logs
препятствуют BOOT TEST READY. Наличие итогового файла не означает закрытие
этих условий или рекомендацию к прошивке.
