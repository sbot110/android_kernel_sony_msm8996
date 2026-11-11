# Dora Stage 6 — BUILDABLE, не BOOT TEST READY

Реальная запись CPU clocks/voltages **не восстановлена полностью**. Выполнены
подключение QFPROM/выбора CPU plans, ограниченный same-owner CCF reattach,
исправления и тесты, две чистые сборочные попытки, отдельная диагностическая
сборка DT и подготовка проверяемых early-boot условий. Телефон не загружался.

Начальный фактический HEAD: `961ff7bf2eb9ab1dc6d83be69972b06e7cede65b`.
Контрольный `03a3c224…` не использовался для reset. Скомпилированный и
проверенный kernel/config/check HEAD:
`886a55f70209438d4fc790f8013465cdd03f1433`.
После него добавлен только итоговый evidence/documentation commit.
Конечный SHA и post-push ref audit приведены в финальном сообщении: документ
не может содержать SHA собственного коммита. Всего **6 коммитов** этапа.

| Коммит | Изменение |
| --- | --- |
| `84325e204` | Подключён реальный QFPROM reader и строгие CPU plans; byte-relative NVMEM binding |
| `ad4544209` | Same-owner CCF bootstrap reattachment без снятия аппаратной quarantine |
| `060b253df` | Сохранён ACK errno; исправлен lifetime mutex в pthread harness |
| `ee6191038` | Проверка общего CPU/GPU fuse cell и отрицательные old-binding fixtures |
| `886a55f70` | Early-boot checker, linked-symbol guards и blocked diagnostic checklist |
| evidence commit | Итоговые отчёты, config, логи, hashes, warning и preservation evidence |

Полный список файлов: [validation-stage6/changed-files.txt](validation-stage6/changed-files.txt).
Машинный отчёт: [summary.json](validation-stage6/summary.json).

## Аппаратные статусы

| Подсистема | Подтверждённый результат | Открытая граница |
| --- | --- | --- |
| CPU hardware adapter | Настоящие NVMEM/QFPROM reads до APCC publication; строго выбран общий bin0..3/PVS0, проверены три source plans без fallback; исправлен dormant `bits29` binding | Modifying callbacks transaction engine не подключены. Нет qualified shared-rail guard, ACD/CBF/PLL programming, CPR/SPM/HMSS/Kryo/APM, thermal/hotplug/collapse/suspend управления |
| GCC/CCF recovery | Только same-owner реконструкция sole healthy HIGH handoff record с fresh typed ACK; counts/handles сохраняются, чужой owner отвергается | Lost per-clock votes, unknown parent/rate, failed ACK и hardware quarantine остаются заблокированными. Full MMCC probe/unwind не исполняется новым host harness |
| SDHC1 | Source-qualified экспериментальные DT, все четыре отдельно собраны; exact full-tree delta и неизменность normal hashes подтверждены | eMMC enumeration/CID/IRQ/GPT/by-name/HS200/HS400/data integrity на телефоне не подтверждены |
| SDHC2 | MicroSD/hybrid UIM2 metadata и host-local logic сохранены, controller выключен | Нет tray/SD/DSDS runtime проверки; включение не выполнялось |
| RPM/PMIC | Сохранены physical S4/L12/L13/L20/L21 UV/MA/SWEN BOTH requests, ACTIVE/SLEEP ACK/recovery/quarantine и logical corners; 56 GCC/49 MMCC contracts | Нет новых phone ACK/voltage/temperature измерений. Native S8–S11 выключены, SPM-controlled gang owner не активирован |
| Display | Пять LCD-ID datasets AUO/JDI/LGD/Sharp ID3/ID9 и 1080p60 сохранены; MDP/DSI/PHY/WLED activation gates сохранены | Неполны DSI analog/incell/handoff и clock recovery. AUO120/dynamic FPS выключены; модель телефона панель не выбирает |
| USB/charging safety | Прежние DWC3/QUSB2 high-speed peripheral и Sony tuning; SMBcharger не привязан, JEITA/thermal/current protections не ослаблялись | DPDM/ID/VBUS/BC1.2 и состояние зарядки firmware не согласованы; возможный конфликт PHY/charging — P0 первой загрузки, USB logging не подтверждён |
| Watchdog/console/pstore | UART/ramoops source/config проверены и ограничения записаны | Нет APSS watchdog node, vendor watchdog config выключен ещё в исходной конфигурации. Требуется исправление/qualification watchdog probe/handoff; физические serial/USB и pstore persistence неизвестны |
| F8131 | Общая Single SIM source/DT база, все software проверки | Нет boot/modem/voice/data/thermal runtime evidence |
| F8132 | Общая DSDS source/DT база, UIM2 ограничения сохранены | Нет boot/dual-SIM/tray/voice/data runtime evidence |
| LOS21/Android14 | Binder/BinderFS, BPF/JIT, SELinux/seccomp, cgroups/PSI, fscrypt/dm-verity, ext4/F2FS/SquashFS/VFAT, ION/dma-buf, gadget/MMC/firmware source/config/ABI проверки проходят | Нет реального userspace запуска. bpfloader exit1, BoringSSL self-check failure и vold failure не объявляются исправленными |
| Boot image | Проверены kernel image formats, ordered appended-DTB metadata, compiled boot dependencies; добавлен read-only inspector supplied header/ramdisk | Рабочий boot.img и LOS21 ramdisk не предоставлены. Реальные load addresses, GPT boot size, bootloader selection и merged cmdline неизвестны; boot.img не создавался |

Подробности: [CPU](dora-stage6-cpu-hardware.txt),
[ownership](dora-stage6-clock-ownership.txt),
[early boot и условия будущего запуска](dora-stage6-early-boot.md).
Отсутствие изображения не является доказательством отсутствия kernel entry.

## Сборка и проверки

Финальная чистая команда:

```sh
OUT_DIR=/workspace/stage6-final-clean-out JOBS=3 scripts/dora/build.sh
```

2026-11-05 10:22:57–10:33:01 UTC, **exit0**. GCC12.2.0-14 ARM64/ARM32,
binutils2.40, host GCC14; инструменты установлены отдельно от репозитория.
Проверены `vmlinux`, `System.map`, `Image`, `Image.gz`, четыре штатных DTB,
`Image.gz-dtb`, ELF/ARM64 headers, gzip round trip, exact append order и
наличие production symbols, включая новый QFPROM reader и CCF API.
Итоговая `.config` побайтно совпадает со Stage5: 291 explicit assignments,
116 required built-ins, CPU_FREQ=n, CPU_IDLE=n, `maxcpus=2`.

Image.gz-dtb: **9723110 bytes**,
SHA256 `cfb6aceb00f22b732a687c62515ca308c1f99242339e08d9d9194a70da4e5bf5`.
Все hashes: [SHA256SUMS](validation-stage6/SHA256SUMS).
Четыре experimental DTB собраны отдельным `build-sdhc1-diagnostic.sh`
workflow в `/workspace/stage6-diagnostic-out`, exit0. Normal config/kernel/
DTB/image hashes после этого неизменны; diagnostic kernel image не создавался.

**47 скриптов / 74 запуска в финальном default pipeline**, против44/71
Stage5. Все44 сохранены; три новых checker также включены в build.sh.
Дополнительно зафиксированы два checker запуска diagnostic workflow,
один early-boot run для всех8деревьев, три ASan/UBSan прогона и два
source-reference прогона. Development retries не суммируются с этими числами.
ASan/UBSan: CPU fuse/plans, CCF reattach/GCC probe и CCF core — exit0;
`detect_leaks=0`, как на Stage5, с отдельными count/ref/cleanup assertions.
Host tests исполняют production code с явными API/MMIO/transport границами;
они не заменяют аппаратные измерения или firmware ACK телефона.

Compiler warnings **90**, как на Stage5; normalized added/removed — **0/0**.
Независимый dtc1.6.1 — **29 на каждый штатный DTB**, как на Stage5,
added/removed — **0/0**. Встроенный dtc1.4.6 отдельно выдаёт15надерево;
его результат не подменяет сравнение одинаковых версий. Старые предупреждения
не объявляются безвредными и не подавлялись новыми compiler/dtc options.
Логи, toolchain versions, final.config, warning comparison, negative tests,
hash rechecks и preservation records находятся в `validation-stage6/`.
`git diff --check` проходит.

Первая чистая попытка `/workspace/stage6-clean-out` скомпилировала и
слинковала ядро, но завершила pipeline с **exit1**: GPU DT checker требовал
старый общий fuse path `@130`. Ошибка исправлена строгой проверкой `@133/1,
bits5:3`, без ослабления golden frequency/voltage или activation checks.
Логи и причина сохранены в `failed-first-*`; финальный результат получен
повторной чистой сборкой в другом новом каталоге.

## Оставшиеся приоритеты и публикация

P0: writable CPU shared-rail/CPR/SPM/APM/ACD/CBF/PLL adapter; потерянное
per-clock ownership; watchdog handoff; безопасное сосуществование USB PHY и
зарядки firmware; реальные boot.img/ramdisk/GPT/selection/logging данные.
Нужны phone RPM/eMMC/early-init logs до утверждения готовности диагностики.

P1: DSI/panel/incell/touch/display activation, GPU independent rails/CPR/
secure ABI, modem/audio/DSDS, suspend и завершение USB/charging функциональности.
Реальные userspace ошибки требуют binary/verifier/init/fs_mgr/crypto logs.
Существующий точный enforcing-cmdline patch проверен на pinned reference,
но остаётся UNAPPLIED; device/vendor репозитории не изменялись.

Публикация предназначена только для существующей
`kernel-4.19-701905467803253630`, обычным fast-forward push. Перед ней
удалённые refs совпали с начальным снимком; dry-run прошёл. Конечный remote
HEAD, прочие heads/tags и локальная чистота проверяются после push и
фиксируются в финальном ответе/post-publication audit. Служебные pull refs
GitHub, если они обновятся автоматически, учитываются отдельно.
Другие ветки, Sony4.4, device/vendor, телефон и содержимое eMMC не изменялись.
