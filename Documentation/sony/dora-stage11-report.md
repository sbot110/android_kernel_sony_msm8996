# Stage 11: Linux 4.19.318 / Sony dora

Собран полный offline `boot-dora-lineage21-kernel419-stage11.img` с исправленным
Linux 4.19.318 и неизменённым рабочим LOS 21 ramdisk. Дополнительно собран
initrd822 differential image. Аппаратная загрузка нового ядра не подтверждена.
Единственный аппаратный результат —
сообщённое пользователем зависание Stage 10 image на Sony logo с пустым pstore.
В этом сеансе телефон не подключался, его разделы не читались и не изменялись.
Найденные ниже программные дефекты не объявляются установленной причиной этого
аппаратного зависания. **BOOTED: NO EVIDENCE; BOOT TEST READY: NO; DAILY USE READY: NO.**

## A. Дефекты и границы доказательства

| Узел | Классификация | Файл, функция, изменение | Проверка и предел вывода |
| --- | --- | --- | --- |
| Обход GICR за пределами mapping | CONFIRMED FIXED | `drivers/irqchip/irq-gic-v3.c`: `gic_iterate_rdists()`, `redist_region`; сохранён фактический размер DT/ACPI mapping, проверены длина RD/SGI, stride, остаток mapping и overflow до следующего MMIO | Выполняются полные production walk/init functions; missing LAST, 4 ядра, VLPIS, несколько mapping, короткий region, огромный/малый/невыровненный stride, ASan/UBSan. Отсутствие LAST на телефоне не наблюдалось |
| NULL IRQ domain до проверки allocation | CONFIRMED FIXED | `gic_init_bases()`: `irq_domain_update_bus_token()` перенесён после проверки domain/percpu allocation | Реальные allocation/cleanup branches с отказом каждого allocator. Нет доказательства такого allocation failure на телефоне |
| Продолжение GIC writes после RWP timeout | CONFIRMED FIXED (Dora failure path) | `gic_do_wait_for_rwp()`, `gicv3_of_init()`: для Sony/somc dora-row missing ACK приводит к panic до последующих GIC writes; policy остальных плат сохранена | Production polling: clear после трёх read, lost ACK после 1,000,000 read, fail-stop и unsafe mutation. Это не исправляет физический lost ACK и не доказывает, что он был на телефоне |
| Неверный UART и отсутствие stdout-path | CONFIRMED FIXED (source contract) | `msm8996-sony-xperia-tone.dtsi`: serial0/stdout → BLSP2 DM1 `0x075b0000`, IRQ114; ошибочно унаследованный `0x075b1000` отключён | Сверены Sony 4.4 `msm8996.dtsi` и compiled DT; отрицательные DT mutations. Физическая доступность UART/pinmux/baud/secure handoff остаётся HARDWARE UNVERIFIED; ранний MMIO earlycon не навязывается |
| Пропущенная частота architectural timer | IMPLEMENTED / HARDWARE UNVERIFIED | В Tone `/timer` восстановлено `clock-frequency=19200000` из Sony 4.4; PPI и регистры не менялись | Проверяется compiled DT; 4.19 `arch_timer_of_configure_rate()` использует property вместо ненаблюдённого CNTFRQ_EL0. Неверный CNTFRQ на телефоне не доказан |
| Проверяемые milestones после MMU | IMPLEMENTED / HARDWARE UNVERIFIED | `arch/arm64/kernel/dora-boot-trace.c`, `include/linux/dora-boot-trace.h`, `setup.c`, `init/main.c`, Kconfig/Makefile/config | Production serializer + независимый CRC32 decoder, torn/corrupt records, writer isolation, ASan/UBSan. Не persistent и не доступный сейчас pre-MMU transport |
| Sony S1 → decompression → Image entry | BLOCKED BY HARDWARE EVIDENCE | `head.S` проверен; Image header/полный BSS extent/4 DTB проверяются инструментами упаковки | Нет фактического entry PC, x0/FDT, loader relocation trace. `head.S` не патчится предположительными MMIO/physical writes |
| Initrd/header relocation | IMPLEMENTED / HARDWARE UNVERIFIED | `build-boot-image.py`, `check-boot-handoff.py`, `dora-stage65b-initrd-experiment.json` | Дополнительный строго pinned профиль меняет только declared initrd address; проверяются Image+BSS, DT growth, fixed reservations, scratch. Исходный packer сохраняет прежний профиль и все прежние negative tests |
| Реальная RAM map и dynamic reservations | BLOCKED BY HARDWARE EVIDENCE | Compiled `/memory/reg` остаётся firmware-filled zero placeholder; исходные fixed reservations учитываются | Нужен runtime FDT/RAM map от загрузчика. Проверка статических пересечений не доказывает владение физической памятью |
| CPU read-only handoff | IMPLEMENTED / HARDWARE UNVERIFIED | Существующие `clk-cpu-8996.c`, `clk-msm8996-cpu-power.c`: один shared owner, parent leases, fuse plan, admission/freeze/readback/quarantine | Повторены все production ownership/PM/fault tests. Нет нового writable CPU adapter или независимого voltage writer |
| CPR/SPM/APM/LMHv1 и шесть voltage leases | UNRESOLVED; BLOCKED BY HARDWARE EVIDENCE | Существующие transaction/native/state engines сохранены; `voltage_leases=0`, writable callbacks не подключены | Нет подтверждённого PMIC baseline и полного transferable stop/drain/ownership договора. Host ACK не используется как разрешение физической записи |
| PSCI, secondary CPU и secure firmware | IMPLEMENTED / HARDWARE UNVERIFIED | Sony 4.4 и 4.19 имеют `arm,psci-1.0`, SMC; CPU0/1/0x100/0x101 и GICR stride сохранены; `maxcpus=2` не менялся | Добавлены SMP begin/done и PSCI milestone. Вызов secure firmware не может быть квалифицирован software timeout |
| APSS watchdog | BLOCKED BY HARDWARE EVIDENCE | Существующие driver/activation gates сохранены; no-watchdog обходов не добавлено | Production probe/IRQ/PM tests проходят. Кто обслуживает firmware watchdog и допустим ли takeover — неизвестно |
| RPM→GCC/GPLL→SDHC1→eMMC | IMPLEMENTED / HARDWARE UNVERIFIED | L20/S4, CX votes, typed BOTH ACK, source clocks/NoC, PWRCTL, tuning/fault quarantine сохранены; 4 boot DTB активируют только SDHC1 | Повторены прежние provider/clock/power/IRQ/PM tests. Physical ACK, CID, IRQ/DMA, tuning/HS200/HS400 не наблюдались |
| GPT/by-name/system/oem/first-stage | IMPLEMENTED / HARDWARE UNVERIFIED | Stage 10 GPT range fix и boot-only metadata `boot_devices=any`, system/ext4, oem/squashfs сохранены | Production GPT/uevent и compiled 4-tree delta tests; реальный GPT/mount не читался. CQE не включён Dora compatible `qcom,sdhci-msm` |
| Android 14 ABI, SELinux и userspace | UNRESOLVED | Binder/ION/SELinux/BPF/config gates сохранены; kernel не подменяется Sony 4.4 | Нет `/init` runtime, policy/vendor modules/firmware/mount logs. `/init` exec result сам по себе не доказывает boot_completed |
| USB/charging/display | BLOCKED BY HARDWARE EVIDENCE | Существующие CPU-independent safety gates сохранены | АDB, экран и вибромотор не используются как выдуманный канал ранних маркеров; full GUI/daily-use поддержка не заявлена |
| Полный Android boot image | IMPLEMENTED / HARDWARE UNVERIFIED | Из предоставленного рабочего boot 4.4 взят неизменённый ramdisk; упакованы настоящее новое ядро 4.19.318 и четыре boot DTB | Два реальных образа независимо распакованы AOSP; ramdisk и kernel payload совпадают побайтно; legacy QCOM ID, границы и tuple order проверены. Аппаратная загрузка не заявлена |

## B. Stage 65 → Stage 11

| Полученные ранее сведения | Как использованы сейчас |
| --- | --- |
| Working Sony 4.4/LOS 21, `sys.boot_completed=1`, версия `21.0-20261030-UNOFFICIAL-dora` | Только пользовательский hardware reference; не результат испытания нового ядра |
| S1 `1299-4832_S1_Boot_MSM8996_LA2.0_O_204` | Идентификатор известен из задания; бинарное соответствие pinned CAF/lk2nd не доказано. Диагностика S1 заново не запускается |
| Initrd `[0x82200000,0x8232b014)`, 1,224,724 bytes | Отдельный ограниченный differential profile и расчёт с фактическим новым Image+BSS |
| Header ramdisk `0x81000000` | Нельзя больше интерпретировать как runtime initrd. Ранее корректное отклонение literal-overlap не доказывало ошибку работающего Sony loader |
| Неизвестные relocating component и x0/FDT | Сохранены UNKNOWN. Tags `0x82500000` не объявляется фактическим FDT |
| SDHC1 `7464900.sdhci`, SDHC2 MicroSD/UIM2 | Boot set использует SDHC1; SDHC2/UFS не активируются ради Android |
| Ramoops `[0xa7f00000,0xa8000000)` и ненадёжная страница `0xa7fff000` | Никаких новых persistent writes; trace находится внутри Image и теряется при reload. Старые повреждённые 5.15 markers не используются как доказательство |
| Четыре Sony DTB/разные PMIC/MSM tuples | Все четыре сохранены; порядок boot set соответствует working Stage 10 reference, не Single/Dual SIM догадке |
| Проверенный 64MiB backup, SHA256 `07200b77a75f989533a477da355544dd25f1be99ee8c763df7ca75e5108d3178` | Сведения пользователя; файл `/mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img` здесь отсутствует и не выдаётся за проверенный в этом сеансе |
| TWRP в отдельном FOTAKernel и прежний успешный restore | Принято как пользовательское recovery evidence; не разрешение выполнять flash/boot сейчас |

`DORA_LOS21_KERNEL515_PROGRESS_2026-11-14.txt` и оригинальные Stage 65B/C/D reports
не найдены в доступных workspace/tmp/shared roots; `/mnt/d` отсутствует.
Read-only fetch `kernel-5.15` дал SHA `0f955fbca80d3ccc7834f5524b1a5dc25a7ad6c9`.
В этом tree также нет именованных Stage 65 reports. Прочитан существующий
`scripts/dora_decode_early_markers.py`: он использует старую страницу/зеркало;
его checksum/выбор mirror не превращается в новое hardware evidence.

## C. CPU/power и ранняя инициализация

HMSS CPR3 и Kryo fuse/voltage plans имеются, но CPU CPR/SAW/APM/MEM-ACC endpoints
остаются gated. Один APCC/CBF owner удерживает counted XO/GPLL0 preparations и
обслуживает read-only clock snapshots; `recalc_rate` не программирует CPU PLL.
QFPROM speed-bin не заменяется guessed bin0. Не вводились SDM845 profiles.

CPU MX — **S2 ACTIVE-only smpa2**, LOW logical4 / wire3; GCC/MMCC CX smpa1 BOTH
не используется как его замена. RPM S8/S9/S10/S11 не получают независимого
владельца. LMHv1 compiled/state backend остаётся gated до подтверждения Sony
service 0x13/profile/sensor/IRQ/S11 DPM/ODCM handoff. Autonomous CPR/SAW/LMH и
collapse исключение не выдаётся одним Linux mutex или наблюдённым BUSY bit.
Существующие lost/partial ACK, rollback, sticky quarantine и readback tests
сохранены; безопасные физические ACK в новом Linux не измерены.

`CPU_FREQ=n`, `CPU_IDLE=n`, `maxcpus=2` сохранены. Нельзя обещать power collapse,
retention/hotplug/suspend и native DVFS с шестью voltage leases: их нет.
`maxcpus=2` ограничивает initial SMP, но не является hardware exclusion и
не запрещает последующее userspace hotplug остальных CPU.
До device initcalls отсутствуют новые CPR/SPM/APCC voltage writes, поэтому
этими отключёнными probe нельзя объяснять предположительный отказ до entry.
Это не исключает ошибку firmware power state или PSCI secondary bring-up.

GICD/GICR topology и 4 architectural PPI совпадают с Sony 4.4 source contract.
RWP wait ограничен; новый Dora fail-stop запрещает продолжение без ACK.
RPM request timeout 5s возвращает ETIMEDOUT; matching ACK/msg-id и errors не
обходятся. Повторный probe defer не объявляется успехом. Новый trace даёт
до/после IRQ/timer/SMP и текущий initcall, если доступна память живого ядра.

## D. Финальная чистая сборка и результаты

Validated source SHA: `1b8871be2d2ce04f7361816fa90aa686e5ece1a3`.
Kernel release: `4.19.318-dora-4.19+`.
Cross GCC: Debian12.4.0-5; ARM32 compat GCC12.4.0; host GCC14.2.0;
external DTC1.6.1, bundled DTC1.4.6. Кросс-инструменты извлечены в
`/workspace/stage11-tools`, системные пакеты не менялись.

Чистая сборка: Mon Nov 16 15:16:45 UTC 2026 → 2026-11-16T15:25:37.573205+00:00, exit 0.
Использован свежий `/workspace/stage11-artifacts/clean-out`; сборка до последнего
RWP fix сохранена отдельно в `/workspace/stage11-pre-rwp-artifacts` и не выдаётся
за финальную. `scripts/dora/build.sh`, isolated diagnostic и boot workflows
завершились с exit 0. Дополнительно скомпилирован GIC object с ACPI=y, включая
изменённый ACPI mapping-size path.

- Все 62 check scripts выполнены: 61 охвачены штатными build/DT workflows,
  `check-boot-handoff.py` выполнен отдельно с реальными Image и четырьмя boot DTB.
- ASan/UBSan: 23 script runs на финальном SHA, все exit 0.
  `ASAN_OPTIONS=detect_leaks=0`, `UBSAN_OPTIONS=halt_on_error=1`; LSan не заявлен.
- 4 normal +4 diagnostic +4 SDHC1 boot DTB собраны отдельно; isolated workflows
  подтвердили сохранность normal artifacts.
- Compiler warnings 90; external DTC 29/DTB; bundled DTC 15/DTB во всех 12 деревьях.
  Normalized added/removed0/0 относительно Stage 10. Проверки не удалялись.
- Config delta: только DORA_BOOT_TRACE=y и compiler identity/version12.2→12.4.
- Проверка исходников, `git diff --check`, source-file hashes и все 19 kernel/
  config/map/DTB artifact SHA256 выполнены.
- Packaging selftests: 2 positive/69 negative/3 unsafe mutations; parser 96
  checks. После получения reference дополнительно проверены два реальных
  boot image: AOSP unpack, независимый QCOM ID, kernel/ramdisk byte equality
  и минимальный header diff.
- Повторная компиляция не требовалась: все 19 kernel/config/DTB artifact
  hashes и 16 source-file hashes совпали с уже проверенной чистой сборкой.
  Состояние до получения private input сохранено в историческом
  `validation-stage11`; актуальная упаковка — в `validation-stage11-boot`.

| Реальный артефакт относительно `/workspace/stage11-artifacts` | Bytes | SHA256 |
| --- | --- | --- |
| `clean-out/.config` | 137490 | `40179cb0f4f02fe0105a167bd55a675a004b4c6a9b9dffc6369540019f90751c` |
| `clean-out/arch/arm64/boot/Image` | 19792384 | `df80903f8168bd233559836d283e409266344faad1bdc627cc57afa1e8de15df` |
| `clean-out/arch/arm64/boot/Image.gz` | 9051760 | `3a5418ab714e7f8cd349afd88faaadd847b01a3a1bd47f237e697a57934acf5c` |
| `Image.gz-dtb-stage11-boot` | 9815824 | `725a60106736e6eeabc45f93a0178e6a57d7f5958679d44c1414038252a146d2` |

Все 12 DTB, vmlinux, System.map и прочие hashes:
[SHA256SUMS](validation-stage11/SHA256SUMS).
[Актуальный summary](validation-stage11-boot/summary.json),
[config delta](validation-stage11/config-delta.json),
[compiler comparison](validation-stage11/warning-comparison.json),
[DTC comparison](validation-stage11/dtc-comparison.json),
[source hashes](validation-stage11/built-source-sha256.json),
[sanitizers](validation-stage11/sanitizers/results.json).

ARM64 Image: magic `0x644d5241` (`ARM\x64`), text_offset=`0x80000`,
image_size=22147072 bytes (`0x151f000`), flags=`0xa`,
raw file=19792384 bytes, BSS/zero-fill=2354688 bytes.
При candidate load 0x80080000 runtime заканчивается в `0x8159f000`;
до reported initrd 0x82200000 остаётся `0xc61000` bytes. Обе candidate layouts
прошли real packer bounds и 6 дополнительных negative cases. RAM ownership,
actual kernel load и x0 по-прежнему UNVERIFIED.

## E. Boot format, адреса и приватные входы

Stage 10 physical attempt: `boot-dora-lineage21-kernel419.img`,10,985,472 bytes,
SHA256 `772f3cbf74580b5b9e6935d3f88635342acbd26d4b70b39ab2336864f138eeda`.
HARDWARE BOOT FAILED/HANG AT SONY LOGO; PSTORE EMPTY; Android completion absent.
Сам Stage 10 image в новом окружении отсутствует; его hash/размер известны из
задания и versioned Stage 10 evidence, не из повторного хэширования payload.

| Поле | Working header | Stage 10/source baseline | Stage 65B differential |
| --- | --- | --- | --- |
| kernel | `0x80008000` declared | `0x80080000` candidate | тот же candidate |
| tags/FDT workspace | `0x80000100` declared | `0x82500000`, 2MiB | тот же candidate; actual x0 UNKNOWN |
| initrd | `0x81000000` declared | `0x82700000` candidate | `0x82200000` proposed header, согласован с reported runtime4.4 |
| scratch | не установлен | `0x92300000` pinned CAF source | тот же candidate; Sony match UNKNOWN |
| page size |4096 | 4096 |4096 |

Ни один header field не подменяет фактический runtime FDT. Только initrd4.4
имеет сообщённое физическое наблюдение; размещение нового ядра и его scratch
остаётся UNVERIFIED. Проверка включает effective Image.image_size/BSS,
text_offset/2MiB base, initrd,2MiB FDT growth workspace, compressed/expanded
scratch и все fixed DT reservations. Dynamic/secure ownership неизвестен.

Предоставленный `boot.zip` теперь фактически прочитан и проверен: 11,704,070 bytes,
SHA256 `13615e75cbbc456cc6f32d960a2d07cf0c0dbc8bf4b302edb331e64e4c8a053a`.
Единственный regular image entry — `boot.img`, 13,283,328 bytes, SHA256
`f923f9b99c468484b713529577b84e3a137d1b9cbc09393e5593258009983820`.
Это побайтно тот же working reference, который анализировался в Stage 10;
распакованное ядро содержит release `4.4.302+`. Исходный ZIP и image не менялись.
Это полезный boot image, а не 64 MiB partition dump и не измерение GPT capacity.

| Полный offline image | Bytes | SHA256 | Declared initrd |
| --- | --- | --- | --- |
| `/workspace/stage11-artifacts/boot-dora-lineage21-kernel419-stage11.img` | 11051008 | `c7a248e58f04fc72c4640d144f5e6f1a47dc32c85c50f020cefd3cae2ea9dfd2` | `0x82700000` |
| `/workspace/stage11-artifacts/boot-dora-lineage21-kernel419-stage11-initrd822-experiment.img` | 11051008 | `3c33a57da1c473cf239dbd9fdc0b3cad26206461b4ba8151c371c86d60941c18` | `0x82200000` |

Основной образ сохраняет Stage 10 candidate addresses, меняя ядро и boot DTB.
Отдельный эксперимент использует то же новое ядро/DTB/ramdisk/cmdline и меняет
только declared initrd address. Между image files отличается единственный
byte по offset 22 — внутри ramdisk_addr header field 20..23. Это не доказательство,
что Sony S1 применит данное поле; actual x0 и relocating component неизвестны.

Оба образа содержат реальный Linux `4.19.318-dora-4.19+`, четыре Sony boot DTB
в порядке v3.0/PMI8994, v3.1/PMI8994, v3.0/PMI8996, v3.1/PMI8996, и неизменённый
XZ ramdisk 1,224,724 bytes, SHA256
`9a532793e779b33bad98df1576ec9b661bc04457f4f3f9148b31ba8d42d4f41e`.
Независимый AOSP14 unpacker распаковал reference и оба новых boot image.
Compressed ramdisk совпадает побайтно с оригиналом; kernel payload совпадает
с `Image.gz-dtb-stage11-boot`; распакованный Image совпадает с clean build.

Legacy Qualcomm v0 header: page 4096, second_size 0, dt_size 0;
kernel_addr=`0x80080000`, tags_addr=`0x82500000`.
QCOM SHA1 ID обоих images — `35afd6abb3972411a1bbe916a87557342ef46380`;
он независимо пересчитан из extracted payload и little-endian sizes с
дополнительным zero dt_size. ID совпадает у A/B, поскольку address words не
входят в его область хэширования. Это не криптографическая подпись Sony.
Enforcing/maxcpus2 policy сохранена, security/thermal/watchdog bypass не добавлен;
полная исходная cmdline хранится privately, её hardware effective вид неизвестен.

Полный Image runtime `[0x80080000,0x8159f000)` с BSS, FDT workspace
`[0x82500000,0x82700000)`, initrd A `[0x82700000,0x8282b014)` или B
`[0x82200000,0x8232b014)` не пересекаются друг с другом и fixed reservations
всех четырёх DTB. Source-derived compressed scratch
`[0x92301000,0x92c5d710)` и expanded scratch `[0x92d8b000,0x942aa000)` также
проверены. Actual firmware RAM/relocation и dynamic/secure ownership остаются
UNVERIFIED. File size 11051008 меньше проверенного reference 13283328;
реальная ёмкость boot partition из этих размеров не выводится.

AOSP unpacker: официальный `android-14.0.0_r1/system/tools/mkbootimg/unpack_bootimg.py`,
SHA256 `a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308`.
[Независимый round-trip](validation-stage11-boot/aosp-independent-roundtrip-stage11.json),
[основной image](validation-stage11-boot/boot-image-offline-result.json),
[initrd822](validation-stage11-boot/boot-image-initrd822-result.json),
[входной reference](validation-stage11-boot/working-reference-provenance.json),
[все 21 SHA256](validation-stage11-boot/SHA256SUMS).
Никакие private ramdisk bytes, исходный boot dump, исходная cmdline или
персональные Android данные не добавлялись в Git. Образы остаются вне Git.
**OFFLINE BUILT: YES; HARDWARE BOOT: UNVERIFIED; BOOT TEST READY: NO.**

## F. Git и публикация

Исходный remote Stage 10 HEAD: `61d3e0663162c6cf2507e6653d8ac2e0d2ce9803`.
Начальное дерево было чистым на локальной `work` (`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`);
fetch подтвердил точный Stage 10 SHA. До source edits выполнен переход на
существующую разрешённую ветку `kernel-4.19-701905467803253630`. Stage 10
сохранён предком; HEAD `work` не менялся; kernel-5.15 читался без checkout
или изменения его ветки. Новые удалённые ветки не создавались.

| Source commit | Изменение |
| --- | --- |
| `e1efc59591b4fad695dddbcfd87fe8652fbfa5b5` | irqchip: bound GICv3 redistributor walks and restore Sony early DT contracts |
| `eb1b60493922656a8c67c5720ca3319bcbae6988` | arm64: record checksummed volatile Dora boot milestones without fixed physical writes |
| `e9d2281a07f7150c343a2c651d0b38cf522f87f2` | sony: add bounded Stage 65B initrd differential without asserting S1 relocation |
| `1b8871be2d2ce04f7361816fa90aa686e5ece1a3` | irqchip: fail stop Dora on missing GIC RWP acknowledgement |

Первый report/evidence опубликован как `42523a582c69eda88653b502a8cbf182039023da`.
После предоставления рабочего boot.zip добавляется только новый
documentation/evidence commit; kernel/config/DTB source не изменён. Точный
final SHA и remote verification находятся в
`/workspace/stage11-artifacts/publication.json` и финальном сообщении сеанса.
Validated compiled source остаётся `1b8871be2d2ce04f7361816fa90aa686e5ece1a3`.
Используется обычный fast-forward push с явным refspec только разрешённой
ветки. Исходный/private boot, ramdisk и Android personal payload не публикуются.

## G. Минимальная процедура следующего аппаратного исследования

Это план сбора доказательств для отдельно разрешённого испытания, а не
рекомендация прошить текущий неквалифицированный образ. Здесь команды на
телефоне не выполнялись. **Private input получен и offline images проверены,
но без квалифицированного транспорта/reader и CPU/watchdog handoff
аппаратное испытание нельзя считать подготовленным.**

1. На хосте сохранить проверенный working backup и повторно проверить его
   SHA256. Предоставленный working boot.zip уже прочитан, сохранён privately
   и использован без изменения ramdisk. Это не разрешение трогать boot partition.
2. На уже работающем Sony 4.4 получить read-only snapshot `uname -r`,
   `getprop sys.boot_completed`, `/proc/cmdline`, `/sys/firmware/fdt`, `dmesg`,
   доступные pstore records. Raw FDT/cmdline хранить privately. Из FDT нужны
   `/memory/reg`, `/chosen`, selected tuples и reserves; из logs — PSCI,
   timer/IRQ и watchdog first state. Не читать регистры через guessed devmem/SMC.
3. До нового boot подтвердить доступный transport на рабочем ядре: реальный
   UART либо уже разрешённый debug/trace интерфейс. Source UART address сам
   по себе не подтверждает электрическое подключение/baud. USB4.19 gated,
   поэтому нельзя предполагать наличие ADB после hang.
4. A (Stage 10 candidate addresses) и B (только header initrd→0x82200000)
   уже собраны и независимо распакованы AOSP. Подтверждён единственный
   изменённый byte 22; kernel/DTB/ramdisk/cmdline одинаковы. Перед отдельно
   разрешённым испытанием проверять SHA256 из этого отчёта и сохранить
   read-only capture вне TWRP, как описано ниже.
5. Разделение стадий требует положительных наблюдений:

| Наблюдение | Что оно различает | Чего не доказывает |
| --- | --- | --- |
| Loader trace либо остановленный PC на Image entry, реальные x0–x3 | Sony S1 acceptance/entry и адрес FDT | Один Sony logo/пустой pstore ничего из этого не доказывает |
| Valid DRB1 `START` с CRC/sequence | MMU/BSS/C entry уже достигнуты; x0–x3 из существующего head.S boot_args | Отсутствие записи без live capture не доказывает отказ entry |
| SETUP_ARCH, FDT_REJECTED либо FDT_VALID/FDT_HEADER | FDT validation и выбранные initrd/memory data | Молчание UART до setup не локализует отказ |
| MEMBLOCK/PAGING_DONE | Пройдена соответствующая memory/MMU C initialization | Не квалифицирует всю RAM/secure ownership |
| IRQ_BEGIN/DONE, TIMER_BEGIN/DONE, IRQS_ON | Локализует GIC/timer enable interval | Не подтверждает реальное IRQ delivery одним source test |
| SMP_BEGIN без SMP_DONE и последний secure trace | Secondary PSCI/firmware interval | CPU voltage/power причину нужно подтвердить physical state, не угадать |
| INITCALL_BEGIN новее INITCALL_DONE | Адрес последнего выполняющегося initcall, сопоставляемый с тем же System.map/vmlinux | Общий hung initcall не является доказанным RPM/CPU/eMMC диагнозом |
| RPM ACK/error, MMC CID/IRQ, GPT PARTNAME, mount logs | RPM→eMMC→GPT→system/oem interval | Host GPT/ACK tests не заменяют эти records |
| EXEC_INIT/EXEC_RESULT | Попытка `/init` и kernel exec result | Требуются Android first-stage/SELinux logs и boot_completed |

6. `/proc/dora_boot_trace` — root-only binary 2048bytes; декодировать
   `python3 scripts/dora/decode-boot-trace.py capture.bin`. Проверять CRC,
   magic/version/size, commit complement и sequence; сопоставлять только с
   hash-совпадающими vmlinux/System.map. Trace volatile и не заменяет UART.
   START содержит x0–x3; SETUP_ARCH — FDT physical pointer; FDT_VALID — FDT
   pointer, mapped size, initrd start/end сразу после DT scan; FDT_HEADER —
   проверенные magic/totalsize. MEMBLOCK содержит DRAM start/end и текущие
   initrd values после `arm64_memblock_init()` (они уже могут быть virtual,
   их нельзя автоматически называть physical). PSCI_DONE содержит лишь
   адрес обнаруженного `cpu_on` callback. IRQ/TIMER/SMP DONE обозначают
   возврат функции, а не physical qualification. INITCALL slots содержат
   function address/return code; async worker/probe вне initcall hook может
   потребовать отдельного transport trace.
7. **Сохранение при TWRP:** не считать обычную загрузку существующего TWRP
   способом сохранить доказательства. Он уже повреждал ramoops page.
   До испытания нужен подтверждённый live capture либо отдельно проверенный
   reader kernel, резервирующий тот же ramoops range и снимающий raw bytes
   до любого ramoops/pmsg/ftrace write. Сейчас такого reader/transport нет;
   рабочее Sony 4.4 и FOTAKernel не изменялись. Новая arbitrary persistent
   page не выбрана; отсутствие общей проверенной области оставляет этот
   метод отключённым. После непредохранённого перехода в TWRP отрицательный
   результат помечать EVIDENCE MAY HAVE BEEN OVERWRITTEN, не «kernel never ran».
8. BOOTED можно подтвердить только физическим runtime нового образа:
   `uname -r` содержит4.19 и `getprop sys.boot_completed` возвращает1.
   Никакие host PASS, AOSP unpack, exec milestone или заставка не заменяют это.
