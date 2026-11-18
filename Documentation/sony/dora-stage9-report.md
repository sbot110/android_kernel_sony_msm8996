# Dora Stage 9 — CPU shared resources и read-only live integration

Полная writable CPU power интеграция **не завершена**. Выполнены реальные
изменения CPU provider/resource ownership, CPR/RPM observational API и
SPM/SAW lifetime/readback/error handling. APCC/CBF подключены к одному
resource owner, но modifying native callbacks остаются unbound: отсутствуют
совместимый LMHv1 S11 DPM/ODCM backend и подтверждённый transferable guard
автономных CPR/SPM/thermal/collapse изменений. Телефон с Linux4.19 не запускался.

**Финальный build status: BUILDABLE. BOOT TEST READY: NO;
BOOTED: NO; DAILY USE READY: NO; RUNTIME VERIFIED: NO.**

## Ревизии и сохранение работы

Начальный published Stage8 SHA:
`e9f1a7be0c13e1447a0cf398af31a9efe5868ad1`.
Работа продолжается только в `kernel-4.19-701905467803253630`. При resume
незакоммиченные изменения проверены через status/stat/diff-check и полный
source audit, сохранены в отдельном host snapshot; они не удалялись и не
откатывались. До новых исправлений существовало 19 изменённых/untracked
файлов, включая результаты прежних параллельных задач.

- Validated source SHA: `87a4a52a50177dfafeec780f3f3c9316621d7c20`.
- Final published SHA: полный SHA публикационного commit указан в итоговом
  сообщении и `/workspace/stage9-publication-audit.json`; это commit, содержащий
  данный документ. Его собственный hash не встраивается в этот же commit.
- Коммитов: 6 (пять source/test и один documentation/evidence).
- Изменённых файлов: 104 (список в evidence).
- Полный список файлов: [changed-files.txt](validation-stage9/changed-files.txt);
  результаты: [summary.json](validation-stage9/summary.json), SHA256 и архивы
  логов в `validation-stage9/`.

Sony4.4 reference `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`, device/vendor
и остальные ветки не изменяются. No reset/rebase/force push/new branch/PR.
Remote snapshots фиксируют внешнее обновление `kernel-5.15` до нашего push;
это изменение не отменяется и не приписывается Stage9.

## Подтверждённый инженерный результат

Подробная dependency graph и source-to-implementation table:
[CPU power integration](dora-stage9-cpu-power.md).

1. Восстановлен Sony CPU digital supply **MX S2 ACTIVE-only**, отдельно от
   GCC/MMCC CX. PLL LOW = logical SVS_SOC4 (wire corn3), а не CPU CPR index.
   Добавлены четыре APCC supply links; voltage nodes не активированы.
2. APCC/CBF read-only providers используют один постоянный resource owner,
   counted XO/GPLL0 references, supplier identity, неизменяемые fuse plans,
   common rate validation и persistent fault/retirement. Нет нового
   transaction engine, CPU PLL writes или фиктивных voltage leases.
3. Typed CPR/RPM snapshots получают scalar state опубликованного exact
   provider без regulator consumer acquisition и без ACK/votes/recovery.
   Это исключает возможный upstream-enable побочный эффект regulator lookup
   при чтении диагностического статуса.
4. CPR ошибки после uncertain rail/APM transitions сохраняют quarantine;
   новые IRQ guards не продолжают voltage completion через unknown state.
   Logical/error bounds и snapshot/PM/error propagation усилены.
5. SPM/SAW active operations удерживают lifetime до retirement/drain перед
   devres unmap. Физический range/CPU/name owner и tombstones препятствуют
   повторному захвату; fresh register readback и bounds закрывают fail-open
   состояния. Это software lifetime, а не autonomous firmware exclusion.
6. Sony LMHv1 S11 notifier восстановлен как точный обязательный контракт:
   DPM/ODCM до повышения и после понижения, abort restoration; 850mV по
   исходному driver, несмотря на DT property suffix mA. Более новый DCVSH
   Linux4.19 не является совместимой заменой. Неподтверждённые MMIO writes
   для обхода отсутствующего LMHv1 не добавлены.

| Component | Implemented | Live connected | Host tested | Hardware verified | Blocker |
| --- | --- | --- | --- | --- | --- |
| CPU Shared Owner | Singleton ports, supplier identity, counted XO/GPLL0, immutable plans, fault/retirement | Да, APCC/CBF observer lifecycle; voltage_leases=0 | PASS (software) | Нет | Полный CPR/SPM/APM/LMH guard и реальные leases отсутствуют |
| CPR3/HMSS | Typed snapshots, sticky error/IRQ closure, source fuse/corner data | Snapshot API доступен; HMSS voltage nodes disabled | PASS (software) | Нет | Autonomous stop/readback, IRQ drain, calibration/runtime state |
| SPM/SAW | Permanent endpoint ownership, users drain, AVS/PMIC readback, bounds | API исправлен; CPU SAW/S11 activation gated | PASS (software) | Нет | Bootloader/firmware, hotplug/collapse/retention ownership |
| APM | Stage8 MODE/STATUS/SMC/retirement/crossover safeguards сохранены | CPU APM nodes disabled | PASS (software) | Нет | Общий gang guard и supply baseline |
| APCC/CBF/ACD/PLL | Existing native callbacks и transaction engine сохранены; owner-bound observer | Только recalc/readback; modifying callbacks unbound | PASS (software) | Нет | LMHv1, autonomous exclusion, leases, hotplug/PM |
| GCC/CCF/RPM | 56 GCC/49 MMCC, bootstrap HIGH, BOTH ACK/quarantine, reattach сохранены | Существующие providers; CPU parents counted | PASS (software) | Нет | Unknown owner/parent/rate не разрешает сброс HIGH или quarantine |
| Watchdog | Stage7 probe/IRQ fixes и Stage8 provenance сохранены | Activation blocked | PASS (software) | Нет | Loader/secure ownership, register access, clock/IRQ/reset/panic/PM evidence |
| USB/Charging | Parent DWC3 и QUSB2 gates сохранены | Dora USB/ADB недоступны | PASS (software) | Нет | DPDM/VBUS/ID/BC1.2/FG/BCL/JEITA/current/thermal/firmware handoff |
| SDHC1/eMMC | Source RPM→GCC/CX→L20/S4→clocks/NoC→IRQ/CQE contracts | Только четыре отдельные diagnostic DTB | PASS (software) | Нет | Actual RPM/CID/IRQ/DMA/GPT/partition/HS200/HS400/init logs |
| Boot image | Read-only parser и artifact search; kernel metadata checks | Реальный working boot.img/ramdisk отсутствует | PASS (software) | Нет | Header/loader/partition/cmdline/DTB-selection/recovery evidence |

PM flags общего owner — диагностическое bookkeeping; они не подтверждают
hardware suspend/resume. Parent retention после partial failure преднамерен:
неизвестное питание работающих CPU не превращается в guessed OFF baseline.
Не создаются четыре независимых native RPM owners S8–S11.
Унаследованный `msm_spm_drv_set_vdd()` восстанавливает AVS после failed
PMIC ACK до wrapper quarantine. Полный autonomous exclusion этой
последовательности не подтверждён и остаётся частью P0 CPU voltage gate.

## Сборка и проверки

Чистый новый OUT_DIR: `/workspace/stage9-qualified-clean-out`, JOBS=3.
Полный build exit0: `2026-11-13T21:19:46.579564+00:00` → `2026-11-13T21:29:34.109772+00:00`.
Отдельный новый diagnostic OUT_DIR: `/workspace/stage9-diagnostic-out`, exit0.

Проверены vmlinux, System.map, Image, Image.gz, четыре
штатных DTB, exact ordered Image.gz-dtb, четыре отдельные SDHC1 DTB,
final .config, SHA256, compiler/dtc warnings и git diff --check. Штатная
политика hardware activation не меняется. Единственный преднамеренный CPU
DT delta — supply metadata; полное binary equality со Stage8 не заявляется.

| Метрика | Stage8 | Stage9 |
| --- | --- | --- |
| Checker scripts | 54 | 56 |
| Default executions | 81 | 83 |
| Compiler warnings | 90 | 90 |
| External dtc1.6.1 warnings / normal DTB | 29 | 29 |
| Bundled dtc1.4.6 warnings / normal DTB | 15 | 15 |
| ASan/UBSan runs | 5 | 8 |

Существующие checkers и security checks сохраняются. Новые tests исполняют
production code на моделируемых kernel/MMIO/firmware boundaries и проверяют
partial failures, ownership, concurrent access, retirement, PM и quarantine.
Они не подтверждают физическую безопасность rails, actual PLL lock, boot,
firmware handshake или eMMC detection. Новые suites: owner — 7 unsafe source + 3 DT mutations; CPR — 24 production
functions и 16 mutations; generic DT — 17 mutations на каждое дерево; SAW —
три production harness. Все прошли. ASan/UBSan: 8 прогонов, `detect_leaks=0`;
LSan недоступен под ptrace. Compiler и оба dtc: normalized added/removed0/0
для четырёх normal и четырёх diagnostic DTB. Final config byte-identical
Stage8; normal/diagnostic hashes проверены, обычные артефакты при diagnostic
workflow не менялись. Дополнительно: 2 diagnostic workflow calls, 1 early-boot
и 1 USB-parent call по восьми деревьям, 4 diagnostic CPU-DT calls. Число83
означает calls в успешном штатном workflow, а не сумму всех повторов этапа.

## Совместимость и boot qualification

**По сообщению пользователя LOS21 уже работает на исходном Sony4.4.302+.**
Это стабильный runtime reference. Исторические bpfloader exit1,
BoringSSL self-check failure и vold failure не объявляются текущими ошибками
работающего Sony4.4. Для нового Linux4.19 runtime compatibility пока неизвестна.
Binder/BinderFS, BPF/JIT, SELinux/seccomp, cgroups/PSI, fscrypt/dm-verity,
filesystems, ION/dma-buf, MMC/firmware и Android ABI source/config checks
сохраняются; security/self-tests/failure handling не отключались.

F8131: Single-SIM source/config/DT база сохранена; Linux4.19 phone runtime
не проверен. F8132: DSDS/UIM2 restrictions сохранены; modem/DSDS runtime
не проверен. Ни SoC/PMIC stepping, ни одна из пяти LCD-ID panels не
выбираются по SIM-модели. Все четыре normal и четыре diagnostic variants
проверяются отдельно; SDHC2/UFS остаются выключенными.

Display/GPU: MDSS/MMCC/DSI/L12/panel/backlight и Adreno530/KGSL/MX/CX/CPR/
SMMU/secure ABI не активированы. Сохранены AUO/JDI/LGD/Sharp ID3/Sharp ID9,
1080p60, no AUO120/dynamic FPS. Отсутствие изображения не доказывает
отсутствие kernel entry; USB gate также означает отсутствие доказанного ADB.

[Working reference и boot qualification](dora-stage9-boot-reference.md)
содержит фактический artifact search и точные host-only команды безопасного
предоставления working LOS21/Sony4.4 boot.img/ramdisk. Отсутствующие header,
load addresses, boot partition size и loader behavior не подменяются
BoardConfig или guessed parameters. Boot.img не создаётся; flash/fastboot boot,
partition reads/writes и eMMC writes не выполняются.

## Оставшиеся P0/P1

P0: transferable CPU CPR/SPM/APM/LMHv1 owner и шесть подтверждённых voltage
leases; MX AO и S11 firmware baseline; source LMHv1 DPM/ODCM integration;
revision-specific CPR/AVS stop/IRQ-drain protocol; thermal/hotplug/collapse/
retention/PM coordination; unknown clock owner/parent/rate recovery;
watchdog loader/secure handoff; USB/charging DPDM/VBUS/ID/BC1.2/JEITA/BCL/
thermal/current handoff; working boot.img/ramdisk/partition/cmdline/selected-DTB/
serial/pstore/recovery evidence; phone RPM/eMMC/GPT/early-init observations.

P1: display/incell/backlight, GPU/secure ABI, modem/DSDS/audio/camera и полный
suspend. До устранения P0 статус BOOT TEST READY не объявляется.

Публикация: только обычный fast-forward push разрешённой ветки. Снимок
`remote-refs-prepublication.txt` фиксирует target Stage8 до push и внешнее
обновление `kernel-5.15`. Итоговый remote HEAD, сравнение остальных refs и
clean worktree фиксируются после push в `/workspace/stage9-publication-audit.json`
и итоговом сообщении. Другие ветки/refs не входят в push refspec.

## Проверенный путь сборки и опубликованные исходники

Первая чистая попытка скомпилировала ядро (90 warnings), но workflow завершился
exit1: общий checker трактовал новые APCC supply metadata как активные
regulator dependencies. Исправлена модель проверки: только четыре точных
read-only links допускают gated endpoints при проверке полного CPU DT и
реального inert provider/owner source; прочие active consumers по-прежнему
требуют available provider. Добавлены17 negative fixtures. Проверочный
повтор на первом O= прошёл, затем выполнен новый полный clean build выше.
Первая неуспешная попытка и rehearsal сохранены отдельными логами/exit codes;
они не выдаются за успешную чистую сборку.

Сборка выполнялась с initial HEAD плюс frozen working changes. SHA256 всех22
изменённых source/test files совпадают с validated source commit; invocation
HEAD и dirty version strings сохранены в артефактах без переписывания.

- `a67210d3923d98180c92aab68541f09787da5818` — regulator: cpr3: expose HMSS observations and quarantine uncertain transitions.
- `9c29df6a097e13ffb0d281a8eae05d9e4acf5553` — regulator: qcom: observe RPM corners without acquiring supplies.
- `7acbd44eff1839d043d1b2e40782f0f01fb6bd45` — soc: qcom: retain SAW ownership and verify live AVS state.
- `6326cd1cf2116ff4ff02288649d47864f1b12520` — clk: qcom: bind Dora read-only providers to shared CPU resources.
- `87a4a52a50177dfafeec780f3f3c9316621d7c20` — dora: qualify inert CPU supply references and shared power code in builds.
