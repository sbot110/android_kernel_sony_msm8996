# Dora Stage 10 — offline boot image, P0 fixes и аппаратные ограничения

Создан фактический `boot-dora-lineage21-kernel419.img` с Linux4.19.318,
четырьмя отдельными SDHC1/LOS21 boot DTB и неизменённым предоставленным
Android14 XZ ramdisk. Выполнена независимая распаковка официальным AOSP14
unpacker с побайтным сравнением компонентов. **Это офлайн-кандидат, а не
подтверждённый рабочий boot телефона.** Полная CPU/LMH/CPR/SPM/thermal
интеграция не завершена; modifying native callbacks не включены.

**Artifact: OFFLINE BUILT / HARDWARE UNVERIFIED. Kernel: BUILDABLE.**
**BOOT TEST READY: NO; BOOTED: NO; DAILY USE READY: NO.**

## Артефакт и ревизии

- Файл: `/workspace/stage10-artifacts/boot-dora-lineage21-kernel419.img`.
- Размер: **10,985,472 bytes**; padding до64MiB не добавлялся.
- SHA256: `772f3cbf74580b5b9e6935d3f88635342acbd26d4b70b39ab2336864f138eeda`.
- QCOM legacy v0 header ID: `bebb6e2828beb58baeefb1b5fd16742f8bbeb75d`.
- Initial SHA: `1bd88af779cff6ec677282d150b338e90e897576`.
- Validated source SHA: `41d70cb58dd60a38b98b7ee1cb6264d86dace8b2`.
- Final published SHA: итоговое сообщение и `/workspace/stage10-publication-audit.json`;
  это documentation/evidence commit, содержащий данный документ, поэтому его
  собственный hash не встраивается в него же.
- Коммитов: **7** — шесть source/test и один documentation/evidence.
- Изменённых файлов: **122**; [список](validation-stage10/changed-files.txt).
- Build invocation HEAD: initial SHA + frozen рабочие изменения. Все32
  source/test/profile hashes совпадают с validated source commit. Исходные
  dirty/version strings в артефактах не переписывались.

User ZIP/boot/ramdisk и готовый образ не публикуются в Git. В репозитории
только исходники, тесты, source provenance и безопасные metadata/evidence.
Оригиналы byte-for-byte сохранены. [SHA256](validation-stage10/boot-dora-419-SHA256SUMS.txt),
[результат упаковки](validation-stage10/boot-image-offline-result.json),
[независимая проверка](validation-stage10/aosp-independent-roundtrip.json).

## Реальные исправления

| Компонент | Результат | Предел готовности |
| --- | --- | --- |
| SPM/SAW | Failed PMIC ACK не восстанавливает AVS; sticky error блокирует повторные операции; реальные stop/limits/readback; active unsupported AVS возвращает ошибку до VCTL | Autonomous firmware/CPR/collapse exclusion не доказан |
| CPU owner/provider | Admission до MMIO, bootstrap NULL pins, shared freeze/drain, late probe defer, сохранение parent references и quarantine | Read-only; six voltage leases и transferable power owner не реализованы |
| LMHv1 | Реальный S11 notifier, DPM/ODCM850mV, service0x13 SCM discovery/intensity/profile, IRQ polling, PM/noirq, lifetime и ошибки; stale sensor values не выдаются за свежие | Compiled built-in; DT binding не активирован. Legacy custom thermal trip/cooling ABI и full gang arbitration не завершены |
| GPT | Reversed start/end LBA отвергается до unsigned underflow размера раздела | Это software fix, не обнаружение eMMC |
| LOS21 first stage | Четыре isolated boot DTB с SDHC1, `boot_devices="any"`, generic system/oem by-name и исходными read-only mount flags | eMMC/CID/GPT/mount/runtime logs отсутствуют |
| Boot packaging | Реальный gzip ARM64Image, actual-reference tuple order, исходный XZ ramdisk, legacyQCOM SHA1, memory bounds, unchanged components и enforcing cmdline | Loader/DDR/partition/hardware acceptance не подтверждены |

[Подробный P0 closure](dora-stage10-p0-closure.md) различает FIXED,
IMPLEMENTED / HARDWARE UNVERIFIED, BLOCKED BY HARDWARE EVIDENCE и UNRESOLVED.
[Анализ input/final boot images](dora-stage10-boot-image-analysis.md) содержит
размеры, адреса, ID/hash convention, DTB order и memory risks.

CPU digital остаётся MX S2 ACTIVE-only smpa2, LOW logicalSVS_SOC4/wire3;
не GCC/MMCC CX smpa1 и не CPU CPR plan index. Native RPM S8–S11 отключены,
независимый владелец S11 gang не добавлен. 56GCC/49MMCC contracts,
S4/L12/L13/L20/L21 ACTIVE/SLEEP BOTH ACK, bootstrap HIGH и quarantine сохранены.
`maxcpus=2`, CPU_FREQ=n, CPU_IDLE=n сохранены.

## Память, ramdisk и безопасность

В предоставленном working4.4 image literal kernel0x80008000/ramdisk0x81000000/
tags0x80000100 дают пересечения expanded kernel. Его DT memory/reg нулевой,
actual initrd/DTB/load relocation в файле не зафиксирован. Нельзя выдавать
эти header fields за доказанные runtime addresses.

Кандидат использует отдельный pinned Qualcomm CAF source profile:
kernel0x80080000, tags0x82500000, ramdisk0x82700000; scratch0x92300000 с
source-derived staged decompression. Проверены Image.text_offset0x80000,
image_size22,077,440 (включая BSS), disjoint declared destinations/scratch и
fixed reserved regions всех четырёх boot DTB. Этот профиль не является
идентифицированной Sony firmware. Actual DDR/RAM table, dynamic reservations,
firmware scratch/relocation и bootloader acceptance **не доказаны**.

Input — полезный13,283,328-byte image, не64MiB дамп. Реальная GPT capacity
не предоставлена. Новый image меньше исходного; это conservative relative
size bound при заявленном working reference, а не измерение partition size.

Ramdisk содержит настоящий static AArch64 Android14 first-stage init, SDK34,
Dora/Lineage metadata. Его SHA256 сохранён:
`9a532793e779b33bad98df1576ec9b661bc04457f4f3f9148b31ba8d42d4f41e`.
Policy/init.rc/firmware/vendor modules и system/oem payload не предоставлены;
их ABI/работа с4.19 не подтверждены. Reference cmdline permissive **не перенесён**:
новый header задаёт enforcing и maxcpus2. Security checks, BPF/JIT, SELinux,
BoringSSL tests и vold failure handling не отключались. Working4.4 runtime
не доказывает, что тот же userspace будет работать с4.19 в enforcing mode.

## Чистая сборка и проверки

`OUT_DIR=/workspace/stage10-clean-out JOBS=3 scripts/dora/build.sh`:
**exit0**, 2026-11-16T11:58:17.999832+00:00 → 2026-11-16T12:08:35.909329+00:00. Проверены vmlinux/System.map/Image/Image.gz/
4normal DTB/ordered Image.gz-dtb/final.config/SHA256. Отдельные свежие workflows
создали4diagnostic и4boot DTB; обычные артефакты не менялись. Все4normal DTB
byte-identical Stage9; единственный config delta — SONY_MSM8996_LMH_V1=y.

| Метрика | Stage9 | Stage10 |
| --- | --- | --- |
| Check scripts | 56 | 59 (все56 сохранены) |
| Default top-level calls в successful clean workflow | 83 | 86 |
| Compiler warnings | 90 | 90; normalized added/removed0/0 |
| External dtc1.6.1 / DTB | 29 | 29 для normal/diagnostic/boot; added/removed0/0 |
| Bundled dtc1.4.6 / DTB | 15 | 15 для normal/diagnostic/boot; added/removed0/0 |
| ASan/UBSan script runs | 8 | 10 |

Дополнительно: parser96selftests; packaging2positive/69negative/3unsafe
mutations; CPU20unsafe mutations; LMHv1 7state+22backend productionfunctions/
16unsafe mutations; SAW4production harnesses/5unsafe mutations; shared-power12
mutations; first-stage3production functions/52compiled-workflow negatives.
Actual private ramdisk checks и official AOSP round-trip прошли отдельно.
Host boundaries не доказывают реальные ACK/voltage/PLL lock или boot.
LSan не запускался из-за ptrace среды (`detect_leaks=0`), ASan/UBSan активны.

Full logs/config/manifests: [validation-stage10](validation-stage10/summary.json).
Generic normal build без privateinput по-прежнему пишет «artifact unavailable»
для своего отдельного parser invocation; Stage10 supplied-image/round-trip
reports фактически проверяют предоставленный reference и новый candidate.
Эти generic сообщения не являются утверждением, что input не найден.

## Совместимость и оставшиеся зависимости

F8131/F8132: все четыре SoC/PMIC variants и five LCD-ID panels сохранены;
Single-SIM/DSDS source restrictions не менялись, LCD не выбирается по SIM.
Phone runtime Linux4.19 для обеих моделей не проверен. SDHC1 включён только
в isolated boot/diagnostic DTB. SDHC2/UFS выключены. Display/GPU остаются gated,
1080p60/AUO/JDI/LGD/Sharp ID3/Sharp ID9 сохранены; AUO120/dynamic FPS не включены.

P0: complete CPU CPR/SPM/APM/LMHv1 ownership, six real leases и автономный
stop/drain/resume; legacy thermal trip/cooling integration; hotplug/retention/
collapse и electrical baseline; secure watchdog handoff; USB/charging DPDM/
VBUS/ID/BC1.2/FG/BCL/JEITA/current protections; actual Sony loader/RAM/GPT/logging/
recovery evidence; phone RPM/eMMC/first-stage/SELinux/Android logs. Native
modifying callbacks остаются unbound. Watchdog/USB activation gates не сняты,
отключённый charger node не объявляется доказательством безопасной зарядки.
P1: display/backlight/incell, Adreno530/secure ABI, modem/DSDS/audio/camera,
полный suspend. Исторические bpfloader/BoringSSL/vold сообщения не считаются
текущими ошибками workingLOS21/Sony4.4;4.19 runtime неизвестен.

## Условия последующего аппаратного испытания

До запуска нужны: точная модель/SoC/PMIC/LCD identity; verified original boot
backup и proven recovery/TWRP; реальная boot capacity и loader/RAM/initrd/DTB
trace; подтверждённый serial/pstore/recovery logging channel; закрытые CPU,
watchdog и charging P0. Не использовать произвольный devmem/SMC и не менять
защиты для получения логов. На working4.4 доступны только уже существующие
read-only dmesg/pstore/sysfs records при наличии доступа. Никаких flash,
fastboot boot, partition reads/writes или запуска init на телефоне не было.
Нет boot/`sys.boot_completed=1`/`uname -r` phone evidence Linux4.19.

## Публикация

Обычный fast-forward push только в `kernel-4.19-701905467803253630`.
Pre-push refs сохранены; final remote HEAD, остальные branches/tags и clean
worktree проверяются после публикации в `/workspace/stage10-publication-audit.json`.
Другие refs не входят в push refspec; GitHub может обновить существующий PR head
вслед за этой веткой. Новый branch/PR, force push, reset/rebase не используются.

Source commits:

- `33048f80b354c0a7af772b3c8fe56e09857cef4e` — soc: qcom: quarantine failed SAW voltage acknowledgements before AVS recovery.
- `699b707ce2d4fa2f51ca0c3fced80d2a185d75ed` — clk: qcom: freeze and drain Dora CPU observations before MMIO.
- `9b4b4757233e4ddfe61b993f76a9b959a77f18e3` — thermal: qcom: port gated MSM8996 LMHv1 S11 and secure monitoring backend.
- `1b1c634498624dc0a8c57a711f8f839ab82da5a5` — dora: add isolated LOS21 first-stage DTs and reject reversed GPT extents.
- `4b8e8cf1d543dfb5f5ee283f6ed19ca07b66776f` — dora: package offline boot candidates with verified payloads and bounded source layouts.
- `41d70cb58dd60a38b98b7ee1cb6264d86dace8b2` — dora: compile LMHv1 and run first-stage and packaging checks in clean builds.
