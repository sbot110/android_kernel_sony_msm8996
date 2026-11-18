# Stage 12: hardware validation остановлена на preflight

**Preflight: STOP. Hardware Test A: NOT EXECUTED. Hardware Test B: NOT EXECUTED.**
Ни прошивка, ни временный запуск, ни перезагрузка, ни восстановление телефона
в этом сеансе не выполнялись. Нового аппаратного результата Linux 4.19 нет.
Код ядра, конфигурация, DTB, cmdline и оба Stage 11 image не изменялись;
пересборка и масштабный повторный kernel audit не выполнялись.

Пользователь подтвердил текущее состояние: «Сейчас загружается LOS 21 / 4.4;
TWRP недавно проверен». Это принято как актуальное пользовательское evidence,
не выдано за выполненные Codex команды uname/getprop или новый restore test.
Доступность failure-time UART/USB/reader по-прежнему не подтверждена.

## A. Preflight и доказательства

Начальный local/remote HEAD после git fetch:
`30102a011d83b4dd0d30f3d47add10fba5fe45bb`; дерево было чистым.
Разрешённая ветка: `kernel-4.19-701905467803253630`.
Проверенный preflight source commit: `fb4a5613c054a632da3c3c2d1adcd365a057c9b4`.
Kernel source Stage 11: `1b8871be2d2ce04f7361816fa90aa686e5ece1a3`.
HEAD стабильной локальной ветки work сохранён:
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`.

Реальные A/B файлы найдены и заново проверены независимым parser и официальным
AOSP14 unpacker. Оба имеют 11051008 bytes, release 4.19.318-dora-4.19+,
одинаковые kernel payload/ramdisk/cmdline, четыре корректных Sony DTB с SDHC1
и QCOM ID 35afd6abb3972411a1bbe916a87557342ef46380. Отличается только byte 22
header ramdisk_addr: A 0x82700000, B 0x82200000. Это не hardware qualification.

| Проверка | Результат | Причина/доказательство |
| --- | --- | --- |
| image_A | PASS | real file: SHA/size, independent legacy QCOM/gzip/XZ/CPIO/FDT checks |
| image_B | PASS | real file: SHA/size, independent legacy QCOM/gzip/XZ/CPIO/FDT checks |
| A_B_differential | PASS | only header ramdisk_addr may differ; does not establish Sony relocation |
| AOSP_independent_unpack | PASS | both actual images unpacked; kernel and ramdisk byte equality; temporary host files removed |
| working_backup | STOP | [Errno 2] No such file or directory: '/mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img' |
| reference_file_tail | WARN | reference FILE illustration only; not a current partition measurement |
| current_4_4_user_confirmation | PASS | user statement only; runtime capture checked separately |
| recovery_access | PASS | current TWRP access must be confirmed; historical Stage 65 recovery alone is insufficient |
| boot_target_and_capacity | STOP | no live target capture |
| current_reference_runtime | STOP | no successful current runtime capture; user statement is preserved separately |
| current_partition_tail | STOP | no verified full CURRENT boot read; bytes after11051008 remain unknown |
| trace_symbol_map | PASS | exact Stage 11 System.map SHA256; not a persistent capture channel |
| S1_memory_handoff | STOP | reviewed Sony S1 load/decompression contract, actual RAM bounds and safe Image/initrd/FDT placement are missing; reaching entry remains a test objective |
| CPU_handoff | STOP | bootloader CPU/MX/APCC/CBF/CPR/SAW/LMH baseline and preservation/secondary-bringup contract are unqualified; native six-lease DVFS is also incomplete |
| watchdog_handoff | STOP | firmware watchdog owner, initial state, servicing and legal Linux takeover are unqualified |
| write_extent_and_tail_policy | STOP | Sony partition-read/write extent and old nonzero tail policy are unqualified; neither short write nor automatic zero-fill is approved |
| diagnostic_transport | STOP | no reviewed failure-time UART/live reader/pstore preservation before TWRP; stable4.4 ADB alone is insufficient |
| temporary_fastboot_boot | WARN | not qualified on this Sony S1; not executed and not assumed equivalent to partition boot |

Полный backup 64 MiB по `/mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img`
здесь отсутствует: его SHA 07200b77... не выдаётся за заново измеренный.
В окружении нет adb/fastboot client и прямого доступа к пользовательскому USB.
Физическая boot partition, её номер/размер и текущие bytes не определялись.
Пути `/dev/block/mmcblk0p42` в host tests — только фикстура, не адрес телефона.

Конкретный риск хвоста: в предоставленном regular reference boot 4.4 после
новой границы 11051008 остаётся 2232320 bytes, из них 1723124 ненулевых.
SHA256 этого file tail:
`1405dee14b4c5cd913b220f9b4d92c52a6b2247c19195717e719d95262d720c9`.
Это пример потенциально оставшихся старых данных при короткой записи, а не
прочитанный хвост текущего boot partition. Ни short write, ни автоматическое
обнуление оставшихся bytes, ни изготовление padded partition image не одобрены.

`fastboot boot` не выполнялся и не квалифицирован для данного Sony S1.
Его путь загрузки/relocation не приравнивается к physical boot partition.
Квалификация готовности касается безопасности ограниченного эксперимента;
успешное выполнение нового ядра остаётся целью будущего Test A.
Для read-only CPU handoff нужно проверить физический baseline/сохранение
параметров; завершение всей native DVFS системы не подменяется user permission.

## B. Hardware Test A

**NOT EXECUTED.** Каталог и заполненный offline manifest:
`/workspace/stage12-artifacts/stage12-test-A/manifest.json`.
Сохранены SHA/размер/полный decoded header без private cmdline, kernel release,
source SHA, четыре DTB identity/hash, Sony S1 reference, пользовательское
подтверждение рабочего 4.4/TWRP и требуемый способ восстановления.
Device serial/actual partition/current boot readback не выдуманы: значения null.
A1–A5 не назначались, наблюдений нового поведения Sony logo/black screen/reboot
нет. Команды запуска/прошивки не выдавались: критические gates не пройдены.

## C. Hardware Test B

**NOT EXECUTED.** Manifest:
`/workspace/stage12-artifacts/stage12-test-B/manifest.json`.
B сохраняет kernel/DTB/cmdline/console/watchdog/tags/CPU config A и меняет
только declared initrd address. Его hardware test требует сначала реального
результата A и анализа, затем собственных gates и НОВОГО явного разрешения.
Он не назначен автоматическим повтором после будущего отказа A.

## D. Diagnostics, recovery и следующий шаг

Получены только реальные host artifact данные и текущее пользовательское
подтверждение. Новые phone dmesg/panic/RPM/MMC/Android logs, runtime x0/FDT,
watchdog records и /proc/dora_boot_trace не получены. Причина Stage 10 hang
не установлена; пустой pstore не доказывает отсутствие kernel execution.
Stage 11 trace volatile. TWRP исторически перезаписывал 0xa7fff000 внутри
ramoops 0xa7f00000–0xa8000000; обычный вход в TWRP не считается способом
сохранить доказательства. Новая arbitrary persistent page не используется.

Нового восстановления не требовалось: устройство уже работает на 4.4 по
подтверждению пользователя и не изменялось. Будущий restore требует заново
проверенных full backup SHA, exact boot target/extent и отдельного разрешения;
после него обязательны full readback SHA и фактический Android 4.4 boot.
Не выдаются команды изменения boot/FOTAKernel/userdata/persist/modem/TA/GPT/
bootloader в обход нынешнего STOP.

Подготовлен переносимый script:
`scripts/dora/dora-stage12-hardware-preflight.sh`, SHA256
`8f6e66a8572aa5820ba4f1f464233f4f2085657465f8a5230175292dbeea8295`.
Default — read-only host checks и JSON stdout; device commands только с
явным --collect-adb SERIAL. Нет root/remount/boot/reboot/flash операций.
--read-boot выполняет только полное чтение уже проверенного boot target
при УЖЕ root ADB; несовпадение PARTNAME/размера/UID или short read не создаёт
usable dump. Импортированные JSON/user flags не могут повысить hardware gates.
Нормальный exit 2 означает STOP, а не сбой parser.

[Read-only WSL guide](dora-stage12-readonly-guide.md).
В kit включены reviewed AOSP unpacker, исходный decoder, exact System.map и
текущая пользовательская confirmation. Kit SHA256:
`148a1f7c0acb888766140cc26a354b934ffa6a1409558ea358f57fe661939042`.
Его путь: `/workspace/stage12-artifacts/dora-stage12-preflight-kit.zip`.

Следующий допустимый шаг — run read-only preflight/capture на подключённом
WSL-хосте, пока телефон работает на 4.4, и вернуть JSON для анализа.
Backup/default path указан из задания; image directory и exact SERIAL
нужно взять с реального хоста. Raw dumps/FDT/cmdline/device-state хранить privately.
Перезагрузка/recovery для этого шага не требуются и script их не выполняет.
После read-only данных нужно рассмотреть оставшиеся S1/CPU/watchdog/tail/
transport contracts. Принятие риска их не квалифицирует.

## Проверка инструмента и публикация

33 host parser/admission/no-write проверки прошли. Проверены corrupt header/ID,
неверный target/FOTAKernel/userdata, wrong phone/capacity, не-root/short read,
отсутствие raw read без opt-in, serial injection, risk overrides, malformed JSON,
сохранность existing output и отказ запускать непроверенный AOSP script.
ADB API boundaries моделировались; host PASS не означает hardware PASS.
Проверки source/bash syntax/git diff выполнены. Код ядра не изменён.

Public evidence: [preflight](validation-stage12/preflight.json),
[host tests](validation-stage12/preflight-host-tests.json),
[Test A manifest](validation-stage12/stage12-test-A-manifest.json),
[Test B manifest](validation-stage12/stage12-test-B-manifest.json).
В Git публикуются только code/docs и безопасные metadata. Private boot/ramdisk/
partition dumps и персональные Android данные не публикуются.
Final remote HEAD/clean tree verification — в
`/workspace/stage12-artifacts/publication.json` и финальном сообщении.
