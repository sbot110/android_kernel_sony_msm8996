# Dora Stage 8 — BUILDABLE, не BOOT TEST READY

Главная цель полной live CPU power интеграции **не завершена**. Исправлены
реальные ошибки APM/CPR/SPM/native binding/CCF и раннего USB parent probe.
Но transferable CPR/SPM/APM/LMH shared-gang owner отсутствует; native
callbacks не подключены к writable live provider. Телефон не загружался.
**BOOT TEST READY: NO; BOOTED: NO; DAILY USE READY: NO.**

Начальный local/remote HEAD, опубликованный Stage7:
`6dfd2314c958294d13291885e3ce463e2d7a9257`. Рабочее дерево было чистым;
Stage7 publication и remote refs проверены до изменений. Только существующая
`kernel-4.19-701905467803253630`, без новой ветки/PR/force push/reset или
переписывания истории. Sony4.4, device/vendor и разделы телефона не менялись.

Исходники сборки зафиксированы hashes в
[built-source-sha256.json](validation-stage8/built-source-sha256.json).
Source commits создаются после успешных проверок; invocation HEAD в
`toolchain.txt` -- initial HEAD с frozen working-tree changes. Source hashes
после commit и возобновления работы совпали; version string может содержать `dirty`.
Последний commit содержит только documentation/evidence. Его собственный
SHA находится в финальном ответе и внешнем `stage8-publication-audit.json`.

После паузы повторно проверены все 15 implementation/check files, config,
архивные логи и SHA256 normal/diagnostic artifacts; исходники сборки не
менялись, сохранены фактические UTC timestamps сборки 5 ноября. Публикация
завершается 11 ноября 2026 после проверки remote refs. До нашего первого
Stage8 push внешне обновилась `kernel-5.5` (`030f0b02...` → `c10cec83...`)
и появилась `kernel-5.15` (`ad3dcab5...`). Наша работа эти refs не создавала,
не записывала и не восстанавливала. Начальный и новый snapshots сохранены.

Проверенный source/config/check commit: `ecedc54e29a31665a761085b33adeeed280dab61`.
Всего **6 коммитов**: пять тематических source/test commits и последний
documentation/evidence.
Изменено **76 файлов**, включая полный evidence bundle.

- `96cc943e6` — clk: qcom: keep MSM8996 native CPU bindings and boot limits exclusive
- `d942cc188` — soc: qcom: retain confirmed APM CPR and SAW state on failure
- `2c3656505` — clk: preserve recovery floors and reject faulted handoff completion
- `2745b1bd3` — usb: dwc3: gate Dora parent before firmware charging state changes
- `ecedc54e2` — dora: qualify shared power and USB parent code in every build

Полный список: [changed-files.txt](validation-stage8/changed-files.txt).
Машинный итог: [summary.json](validation-stage8/summary.json).

## Аппаратный результат

| Подсистема | Реальная работа Stage8 | Остаточная граница |
| --- | --- | --- |
| CPU shared power | APM consumer serialization, unique endpoint/range owner, fresh readback, sticky error/retirement; CPR crossover fault closure и SAW sequence bounds | Не полный владелец S8--S11/CPR/SPM/APM/LMH; нет общего exclusion autonomous AVS, hotplug/collapse/thermal/retention |
| Live CPU adapter | Unique native+transaction object bindings; peer fuse-plan проверки до MMIO, unknown CFA reject | Live instance не создан, active provider read-only; prepared parent/real six leases/shared guard отсутствуют |
| CPR/SPM/APM | Нет voltage rollback после uncertain APM switch; signed LDO error handling, PM errors/freeze, bounded sequence API/cleanup/publication | SAW raw-handle retirement/reprobe, full HMSS/Kryo retention и firmware ownership не завершены; activation gates сохранены |
| GCC/CCF | Recovery floor монотонный; faulted/uncertain handoff не разрешает bootstrap release; existing reattach/PLL/ACK/parent fault tests проходят | Lost per-clock identity/unknown parent/rate не восстанавливаются по предположению. HIGH/quarantine не снимаются; 56 GCC/49 MMCC contracts сохранены |
| Watchdog | Source final Tone/official DTB: base0x9830000/0x1000, SPI28/29, bark12s/pet10s; все Stage7 tests сохранены | Bootloader/secure state, access, IRQ routing и reset/panic/suspend на телефоне неизвестны; activation не включалась |
| USB/charging | DWC3 parent reject до allocation/reset/clocks/UTMI/VBUS; прежний PHY gate остаётся | USB/ADB/MTP недоступны. DPDM/ID/VBUS/BC1.2/FG/BCL/JEITA/thermal/current/firmware handoff -- P0. Отказ parent не измеряет зарядку |
| RPM/PMIC | Physical S4/L12/L13/L20/L21 и logical corners/BOTH ACK/recovery/quarantine сохранены | Нет новых phone ACK/voltage/current/temperature данных. Native S8--S11 выключены |
| SDHC1 | Source rails/clocks/NoC/IRQ/CQE/PM invariants и четыре experimental variants сохранены и проверены после CCF/power изменений | Нет phone CID/eMMC/GPT/by-name/HS200/HS400/runtime-PM/data-integrity evidence. CPU power backend не активирован, его runtime взаимодействие не заявляется |
| SDHC2/UFS | Выключены | Нет activation или eMMC write обхода |
| Display/GPU | Все четыре SoC/PMIC variants и пять LCD-ID panels 1080p60 сохранены; no AUO120/dynamic FPS | Display/incell/backlight/DSI PHY и Adreno530/KGSL/MX/CX/CPR/SMMU/secure ABI остаются gated |
| Boot image | Read-only поиск доступных roots и анализ финального kernel через Stage7 parser | Нет рабочего boot.img LOS20/LOS21, LOS21 ramdisk, observed partition/cmdline/loader data. Boot.img не создавался |

Подробные source sequences и недостающий guard:
[power integration](dora-stage8-power-integration.md).
USB/watchdog/boot artifacts и команды безопасного предоставления:
[boot qualification](dora-stage8-boot-qualification.md).

## Сборка и тестирование

Чистая сборка в новом пустом OUT_DIR: **exit0**,
2026-11-05T21:17:40Z..21:27:19Z. Проверены vmlinux/System.map/Image/Image.gz,
четыре normal DTB и exact ordered Image.gz-dtb, ARM64/ELF/gzip/linked symbols.
Отдельный fresh SDHC1 workflow: **exit0**; exact full-tree delta и unchanged
normal hashes подтверждены. Все normal DTB и final.config побайтно совпадают
со Stage7; maxcpus=2, CPU_FREQ=n, CPU_IDLE=n.

Image.gz-dtb: **9734047 bytes**, SHA256
`d5b5aa6bdb4cdf594424ad5a23f8033da1e7816865cd21192cff8bb1fdf087e7`.
Compiler **90 warnings**, external dtc1.6.1 **29/normal DTB**, как Stage7;
normalized added/removed **0/0**. Bundled dtc1.4.6 отдельно: **15/DTB**,
без изменений. Warnings не подавлялись. Toolchain/config/logs/JSON/SHA256
сохранены в validation-stage8; git diff --check проходит.

```sh
OUT_DIR=/workspace/stage8-final-clean-out JOBS=3 scripts/dora/build.sh
OUT_DIR=/workspace/stage8-final-clean-out \
  DIAGNOSTIC_OUT_DIR=/workspace/stage8-diagnostic-out JOBS=3 \
  scripts/dora/build-sdhc1-diagnostic.sh
```

Все **52 прежних checker** сохранены; добавлены два, всего **54 scripts /
81 default executions** против Stage7 52/79. Старые native и CCF harnesses
только усилены negative invariants; удалённых assertions нет. Дополнительно
два diagnostic checker runs, eight-tree early-boot check, eight-tree USB
parent check и **пять ASan/UBSan прогонов** (shared power/SAW, parent USB,
native CPU, CCF, прежние SPM transactions). `detect_leaks=0` из-за ptrace
ограничений среды; явные allocation/ref/count assertions сохранены.

Shared-power harness исполняет **23 production functions** APM/CPR/SAW:
readback/SMC/timeout/known crossover request, partial faults/quarantine,
defer/resource/probe failures, competing owner/overlapping range,
concurrent consumers, retired raw handles/reprobe и PM error/freeze.
Отвергаются **10 unsafe mutations**. USB parent harness исполняет весь
production probe для обоих root aliases и compiled trees, concurrent rejects;
отвергает **3 source mutations** и invalid root identity. Все старые CPU PLL/
ACD/CBF, RPM lost-ACK, CCF reattach/concurrency, MMC error/PM и DT activation
negative tests остаются в pipeline.

Map/SMC/regulator/kernel API boundaries в host tests -- модели. SAW sequence
tests проверяют память и код, APM tests не подтверждают фактическое питание
arrays, а PM boundary faults не являются phone suspend. Ни один результат
не заменяет hardware verification или firmware handoff.

## Совместимость, P0/P1 и публикация

F8131: Single-SIM source/config/DT база сохранена, boot/modem/voice/data/
thermal runtime не подтверждены. F8132: DSDS/UIM2 restrictions сохранены,
dual-SIM/tray/modem/runtime не проверены. Нет привязки PMIC/stepping/панели
к модели F8131/F8132. Все четыре DT variants проверяются отдельно.

LOS21/Android14: Binder/BinderFS, BPF/JIT, SELinux/seccomp, cgroups/PSI,
fscrypt/dm-verity, ext4/F2FS/SquashFS/VFAT, ION/dma-buf, MMC и firmware
source/config/ABI проверки сохранены. Gadget config не означает USB transport.
bpfloader exit1, BoringSSL self-check failure и vold failure остаются runtime
unresolved; новых логов нет. Security/self-tests/failure handling не отключены.
Userspace repositories не изменялись; прежний enforcing-cmdline patch не применён.

P0: общий CPU transferable guard/real leases/parents, SAW lifetime/reprobe,
CPR/APM/LMH/autonomous AVS и thermal/collapse/retention integration;
потерянное clock ownership; watchdog и charging firmware handoff; реальные
boot.img/ramdisk/GPT/loader/cmdline/recovery и serial/pstore evidence;
phone RPM/eMMC/early-init logs. P1: display/incell/backlight, GPU/secure ABI,
modem/DSDS/audio/camera и полный suspend. No BOOT TEST READY до P0 closure.

Публикация -- обычный fast-forward только разрешённой ветки. Начальный и
pre-push snapshots сохранены; внешние изменения других refs, если появятся,
учитываются отдельно от нашего push, как и служебные GitHub pull refs.
Remote HEAD, остальные heads/tags и clean working tree проверены после push
во внешнем audit. Ни flash/fastboot boot, ни запись eMMC не выполнялись.
