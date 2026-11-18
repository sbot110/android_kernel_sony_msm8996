# Dora Stage 7 — BUILDABLE, не BOOT TEST READY

Реализованы native CPU register callbacks и исправления SPM, APSS watchdog
и QUSB2. Полный CPU power adapter **не завершён**: live APCC provider остаётся
read-only без настоящего shared CPR/SPM/APM owner. Watchdog и USB/charging
handoff не квалифицированы. Телефон не загружался; runtime не подтверждён.

Начальный фактический local/remote HEAD:
`b8a20dab81b8f891fdb6f9ec935c5183549b18c8`. Работа продолжена от него,
без reset к Stage5 или более старой ревизии. Изменения опубликованы только
в существующую `kernel-4.19-701905467803253630`, без новой ветки/PR,
force push, переписывания истории и изменений Sony4.4/device/vendor.
До нашего push удалённая `kernel-5.5` изменилась с `470fdeeb...` на
`030f0b02...`; это зафиксировано повторным read-only ls-remote до source
commits и публикации. В рамках этой работы эта ветка не записывалась и
не восстанавливалась. Поэтому абсолютная неизменность всех refs от начала
сеанса не заявляется; post-push audit сравнивает также свежий pre-push
снимок. Служебные GitHub pull refs учитываются отдельно.
Конечный SHA и post-publication ref audit находятся в финальном сообщении
и внешнем `stage7-publication-audit.json`: SHA собственного evidence commit
не может быть включён в его содержимое.

Точные counters, build/source hashes и результаты:
[summary.json](validation-stage7/summary.json),
[список файлов](validation-stage7/changed-files.txt).
Source snapshot финальной чистой сборки зафиксирован в
[built-source-sha256.json](validation-stage7/built-source-sha256.json).
Коммиты созданы после проверки; `toolchain.txt` сохраняет исходный HEAD
на момент начала сборки рабочего дерева. Все перечисленные source files
побайтно сверены после сборки и после source commits; последующий commit
меняет только documentation/evidence. Git version string может содержать
`dirty`, это не сборка другого исходного дерева.

Проверенный source/config/check commit:
`5bcf038a27bd5334cdaec7ecd0f28d989f028ae1`. Всего **7 коммитов**, из них
6 тематических source/test commits и последний documentation/evidence.
Изменено **68 файлов**; полный список приведён выше.

| Коммит | Содержание |
| --- | --- |
| `42bccb16f` | Native CPU callbacks и production engine harness |
| `b3ca5b8a9` | SPM main/AVS ownership, errors/readback/quarantine |
| `997f09530` | Watchdog probe/IRQ/worker/resource lifetime |
| `22965e192` | MSM8996 USB IRQ layout и явный Dora handoff gate |
| `c60e1d75c` | Read-only boot artifact parser и qualification boundaries |
| `5bcf038a2` | Все 52 checker в default build и linked native symbols |
| evidence commit | Итоговый отчёт, config, SHA256, логи и preservation records |

## Реальные изменения и аппаратные границы

| Подсистема | Результат Stage7 | Открытая зависимость |
| --- | --- | --- |
| CPU hardware adapter | Native APCC/CBF mux, AUX, warm primary/alternate PLL, bounded lock/readback и Kryo ACD sysreg/IPI callbacks соединены с production transaction engine через bind API; linked-symbol проверка | API не инстанцируется live. Нужен настоящий общий CPR/SPM/APM guard, prepared parents и acknowledged voltage leases; cold/pro/fractional/unknown handoff отвергается |
| Shared S8–S11 ownership | Singleton reservation обоих clusters/CBF; SPM main/AVS instance lock, no fallback SPMI после SPM failure, sticky instance quarantine, UNKNOWN SAW cache и signed endpoint errors | Нет полного физического gang arbitration, autonomous CPR exclusion, HMSS/Kryo/LDO/retention/APM/collapse/thermal и permanent quarantine/lifetime через reprobe |
| GCC/CCF | Stage6 same-owner sole healthy HIGH reattach, 56 GCC/49 MMCC contracts и fault tests сохранены и проходят с новыми исходниками | Lost per-clock owners и unknown parent/rate не восстанавливаются; HIGH/quarantine автоматически не снимаются |
| APSS watchdog | Реальные probe/IRQ/resource errors, init/unwind, thread/timer/notifier/RCU lifetime и PM races исправлены; оба initial-logbuf object variants собраны | Source final Tone и official DTB bark12s/pet10s подтверждены; bootloader/secure handoff неизвестен, DT/config activation не менялась |
| USB/charging | MSM8996 IRQ/line-state bits исправлены, mutex/clock/reset/PLL/error handling; Dora public init возвращает EOPNOTSUPP до PHY rail/clock/reset/MMIO | USB/ADB недоступны. DPDM/VBUS/ID/BC1.2/FG/BCL/JEITA/firmware owner, parent qscratch override и teardown/reprobe не квалифицированы; charger disabled не доказывает safety |
| RPM/PMIC | Physical S4/L12/L13/L20/L21, BOTH ACK/rollback/quarantine/recovery и logical corners сохранены | Нет phone ACK/voltage/current/temperature measurements; native S8–S11 не включены |
| SDHC1 | Четыре отдельные experimental DTB, L20/S4/GCC/GPLL4/CX/NoC/IRQ/DMA/CQE/PM invariants проходят после интеграции | Нет фактических eMMC/CID/GPT/by-name/HS200/HS400/data-integrity/resume logs; normal DT не активированы |
| SDHC2 | Выключен, hybrid-UIM2/DSDS metadata сохранены | Нет microSD/tray/DSDS runtime |
| Display | AUO/JDI/LGD/Sharp ID3/ID9 datasets и 1080p60 сохранены, gates не сняты | MDSS/MDP/MMCC/L12/DSI PHY/incell/reset/backlight handoff не завершён; AUO120/dynamic FPS выключены |
| Boot image | Новый bounded read-only parser Android v0/ARM64/gzip/XZ/FDT/newc, runtime Image extent/reservation/metadata/security checks и comparison mode | Нет рабочего boot.img, LOS21 ramdisk, observed partition size/cmdline/selection/load data. Boot.img не создавался |

Подробности: [CPU/shared rails](dora-stage7-cpu-hardware.md),
[watchdog/USB/boot qualification](dora-stage7-early-boot.md).
Подготовленный backend API не выдаётся за завершённое аппаратное управление.
Никаких фиктивных success/ACK/readback/regulators в production не добавлено.

## Проверки и сборка

Чистая сборка выполнялась в новом пустом каталоге:

```sh
OUT_DIR=/workspace/stage7-final-clean-out JOBS=3 scripts/dora/build.sh
OUT_DIR=/workspace/stage7-final-clean-out \
  DIAGNOSTIC_OUT_DIR=/workspace/stage7-diagnostic-out JOBS=3 \
  scripts/dora/build-sdhc1-diagnostic.sh
```

Проверены `vmlinux`, `System.map`, `Image`, `Image.gz`, четыре normal DTB,
`Image.gz-dtb`, ELF/ARM64/gzip/append layout и linked production symbols.
Отдельный SDHC1 workflow проверяет exact full-tree delta и неизменность
normal config/kernel/DTB/image hashes, не создаёт diagnostic kernel/boot.img.
Final config побайтно сохранён относительно Stage6; `maxcpus=2`,
`CONFIG_CPU_FREQ=n`, `CONFIG_CPU_IDLE=n`. Compiler/toolchain и SHA256
сохранены в `validation-stage7/`, включая размер каждого artifact.

Финальная чистая сборка: **2026-11-05T20:07:34Z..20:17:23Z, exit0**.
Diagnostic workflow: exit0. Все четыре normal DTB побайтно совпадают со
Stage6. `Image.gz-dtb`: **9731579 bytes**, SHA256
`f64fee87380efa84c1f7dac67a0c75e15c2fa7ebe4a986dfd149febf495bd421`.

Все **47 прежних скриптов** сохранены, добавлены пять: всего **52 скрипта**
и **79 default запусков** против Stage6 47/74. Единственное изменение
старого checker -- усиление проверки linked native CPU symbols. Полные
логи содержат результаты всех default проверок. Дополнительно выполняются
два diagnostic checker runs, eight-tree early-boot check, четыре новых
ASan/UBSan harnesses и две отдельные watchdog-enabled object builds.
LeakSanitizer отключён из-за ограничений ptrace среды (`detect_leaks=0`);
allocation/ref/count cleanup assertions остаются в harnesses.

CPU tests исполняют целиком native callbacks вместе с engine: source bin0
rates вверх/вниз, shared reservation, ACD order, alternate PLL, voltage
leases, readback drift, lock/ACK timeout, partial rollback, quarantine,
unsupported boot state, offline cluster, PM и concurrent consumers.
SPM tests исполняют main/AVS и SAW helpers. Watchdog: **69 cases, 18
production functions, 11 rejected unsafe mutations**. USB: **42 cases,
11 rejected mutations**, concurrent gated init и DWC3 error propagation.
Boot parser: **66 in-memory positive/negative checks**. Boundary models
не являются аппаратными измерениями; physical safety не сертифицирована.

Compiler warnings сравниваются со Stage6 тем же GCC; dtc -- отдельно
одинаковым external1.6.1 и bundled1.4.6, без смешивания версий. Точные
числа и normalized added/removed в
[warning-comparison.json](validation-stage7/warning-comparison.json).
Предупреждения не подавлялись, исходные проверки не ослаблялись.

Итог: **90 compiler warnings**, **29 external dtc1.6.1 warnings/normal
DTB**, как на Stage6; normalized added/removed **0/0** для обоих сравнений.
Bundled dtc1.4.6 отдельно: **15/DTB**, также без изменений. Новые native
CPU/SPM/QUSB2 и отдельный watchdog object builds предупреждений не добавили.
`git diff --check` проходит.

## Совместимость и оставшиеся P0/P1

F8131: общая source/config/DT Single-SIM база сохранена, runtime boot,
modem/voice/data/thermal не проверены. F8132: та же база, DSDS/UIM2
restrictions сохранены; dual-SIM/tray/modem runtime не проверены.
Модель не выбирает панель, SoC stepping или PMIC.

LOS21/Android14 source/config/ABI checks сохраняют Binder/BinderFS,
BPF/JIT, SELinux/seccomp, cgroups/PSI, fscrypt/dm-verity,
ext4/F2FS/SquashFS/VFAT, ION/dma-buf, MMC, gadget и firmware support.
Gadget config не означает доступный USB transport. `bpfloader exit1`,
`BoringSSL self-check failure`, `vold failure` остаются runtime unresolved;
новых phone logs нет. Security/self-tests/failure handling не отключались.
Ранее подготовленный точный enforcing-cmdline userspace patch остаётся
UNAPPLIED; другие repositories не изменялись.

P0: полный CPU shared-gang power owner/CPR/SPM/APM/HMSS/thermal/lifetime;
неизвестное clock ownership; watchdog secure/bootloader handoff;
USB/firmware charging interaction; реальные boot.img/LOS21 ramdisk/GPT/
DT selection/load/cmdline/recovery и подтверждённый serial/pstore transport;
phone RPM/eMMC/early-init evidence. До устранения этих зависимостей нет
разрешения считать сборку BOOT TEST READY.

P1: безопасная display/incell/backlight activation, GPU/secure ABI, modem/
DSDS/audio/camera и полноценный suspend. Phone flash, partition writes,
новые boot parameters, независимые native CPU RPM regulators и ослабление
thermal/JEITA/SELinux/watchdog protections не выполнялись.
