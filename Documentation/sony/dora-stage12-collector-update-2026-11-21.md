# Stage 12: подготовка сбора аппаратных данных, 2026-11-21

**Preflight: STOP. Test A/B: NOT EXECUTED. BOOT TEST READY: NO.**
Новых результатов телефона нет. Не выполнялись device commands, прошивка,
временная загрузка, перезагрузка или восстановление. Причина зависания Stage 10
остаётся UNKNOWN. В этом продолжении исправлены воспроизводимые дефекты
сборщика доказательств перед следующим аппаратным этапом.

После `git fetch` local/remote HEAD совпадали:
`7ca79547620ac740582f8b3fe9cafceed11bb943`; рабочее дерево было чистым.
Разрешённая ветка: `kernel-4.19-701905467803253630`.
Исправления инструмента: `d3bf66d5b380a19d5fc3152c01565030ddf409db`.
Рабочая ветка Sony 4.4 `work` сохранена на
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`; другие ветки не изменялись.

## Исправления инструмента

Все изменения находятся в
`scripts/dora/dora-stage12-hardware-preflight.sh`; проверки — в
`scripts/dora/check-stage12-preflight.py`.
Классификация CONFIRMED FIXED ниже относится к host-инструменту, а не к причине
Sony logo или аппаратному питанию CPU.

| Дефект | Статус | Функция и исправление | Проверка |
| --- | --- | --- | --- |
| stdout/stderr буферизовались без ограничения до возврата subprocess | CONFIRMED FIXED | `bounded_command`: независимые лимиты при чтении pipe, timeout, завершение и reap host-процесса | Реальные локальные процессы: переполнение stdout/stderr/обоих, точные границы, timeout, закрытие pipe |
| `exec-out` мог выдать текст ошибки с host exit 0 за бинарный файл | CONFIRMED FIXED | `validate_capture`, `artifact`: tar/FDT framing и DRB1 version/sequence/commit/CRC перед сохранением | rc0 с ошибками cat/tar отклонён; actual DTB и валидный tar/trace приняты; оригинальный Stage 11 decoder согласован |
| Обрыв между getprop и sysfs терял уже собранные поля | CONFIRMED FIXED | `persist`, `remember`: атомарные checkpoints; PARTIAL/REJECTED и ошибка сохраняются при отказе | Ошибка uname сохраняет identity, pstore и JSON; ошибочный target отклонён до raw read |
| Данные разных загрузок могли попасть в один capture | CONFIRMED FIXED | `boot_id`, `capture_provenance`: boot_id до/после; несовпадение отклоняется | Модель reconnect с разными UUID не принимается как полный capture |
| Старый импорт описывался как CURRENT, S1 не сверялся | CONFIRMED FIXED | `capture_provenance`, `evaluate`: исходная дата/возраст, historical WARN; отдельная проверка measured S1; дата user statement не обновляется | Импорт 2000 года остаётся историческим; future/partial/mixed-boot отклонены; другая S1 даёт STOP |
| CPIO parser использовал Python 3.9 `removeprefix` при общей инструкции Python3 | CONFIRMED FIXED | `check_ramdisk`: startswith/slice; инструкция Python 3.8+ | Реальный ramdisk принят, embedded syntax проверен по grammar Python 3.8; runtime тестов Python 3.12.14 |
| Явный относительный путь `./adb.exe` мог превратиться в поиск PATH | CONFIRMED FIXED | `collect_adb`: resolve явно переданного executable | Fake runner проверяет абсолютный путь |

Ни один флаг не снимает прежние S1/CPU/watchdog/tail/transport STOP gates.
Не добавлялись MMIO, `/dev/mem`, `/dev/watchdog`, debugfs mounts, измерители
clocks, изменения напряжений, root/remount/reboot или команды записи разделов.
Обычное чтение произвольных debugfs файлов не считается безопасным по умолчанию:
например, Sony 4.4 `drivers/clk/msm/clock-debug.c` содержит measurement getters,
которые переключают/включают clocks. Они в collector не включены.

## Реальные host-проверки

70 проверок PASS, включая все прежние 33. Дополнительные 37 проверяют реальные
pipe/timeout paths и модели ADB, а не физический телефон. `bash -n` и
`git diff --check` прошли. ASan/UBSan и новая kernel build не запускались:
production C/assembly/DT/config не менялись.

Оба фактических Stage 11 image заново проверены независимым parser и pinned
официальным AOSP14 unpacker. QCOM ID, неизменный ramdisk и четыре DTB совпали;
diff A/B — только byte 22, declared initrd 0x82700000 против 0x82200000.
Это не подтверждает Sony relocation. Kernel source остаётся
`1b8871be2d2ce04f7361816fa90aa686e5ece1a3`, release
`4.19.318-dora-4.19+`. APCC/CBF read-only handoff и maxcpus=2 не менялись;
native CPU voltage leases и watchdog takeover аппаратно не квалифицированы.

| Артефакт | Bytes | SHA256 |
| --- | --- | --- |
| Stage 11 A | 11051008 | c7a248e58f04fc72c4640d144f5e6f1a47dc32c85c50f020cefd3cae2ea9dfd2 |
| Stage 11 B | 11051008 | 3c33a57da1c473cf239dbd9fdc0b3cad26206461b4ba8151c371c86d60941c18 |
| Новый read-only kit | 794697 | 39b743d9b612e7ca74cc2f8ec5cd6c76ed62d257bca9fc609eb1a45629605b9b |
| Collector script | 37367 | 844de2dbaa361c5be5919cc11ea150c612fa34f05677ab20837fbc37f5218f15 |

Kit: `/workspace/stage12-artifacts/collector-2026-11-21/dora-stage12-preflight-kit-20261121.zip`.
Проверены ZIP roundtrip, SHA256 девяти файлов и standalone запуск с автоматическим
обнаружением pinned AOSP unpacker. Образы, backup и private Android данные в kit
и Git не включены. Старый kit и исходные Stage 11 image сохранены.

Доказательства: [host tests](validation-stage12/collector-2026-11-21/preflight-host-tests.json),
[preflight](validation-stage12/collector-2026-11-21/preflight.json),
[kit](validation-stage12/collector-2026-11-21/kit-verification.json).

## Аппаратный этап и восстановление

Test A: NOT EXECUTED. Test B: NOT EXECUTED. Достигнутые стадии Linux 4.19:
UNKNOWN. Новых panic/MMC/RPM/CPU/init/trace logs нет. Пустой pstore не доказывает
отсутствие kernel execution; Stage 11 trace остаётся volatile. Риск перезаписи
ramoops при TWRP не устранён и не маскируется проверками формата файла.

Пользовательское подтверждение LOS21/4.4 и недавнего TWRP сохранено с исходной
датой 2026-11-16; это не новая аппаратная проверка 2026-11-21. Полный backup
`/mnt/d/dora515-noreloc-flash/boot-stable-verified-full64M.img` здесь недоступен,
его заданный SHA не объявляется заново измеренным. Нет текущего boot partition
readback, подтверждённых S1 memory/CPU/watchdog contracts, write-tail policy и
failure-time transport. Итог остаётся STOP независимо от успешных host tests.

Следующий шаг — пользовательский read-only сбор на WSL-хосте с уже работающей
4.4 по [обновлённой инструкции](dora-stage12-readonly-guide.md), затем анализ
JSON и доступных частичных capture. Вход в TWRP для сбора не требуется.
Дальнейшие source fixes и следующий дифференциальный эксперимент определяются
аппаратными данными; причина не подменяется гипотезой. Запуск A, B или restore
потребует своих пройденных gates и отдельного явного разрешения пользователя.

Финальные publication SHA/remote/clean-tree evidence сохраняются в
`/workspace/stage12-artifacts/collector-2026-11-21/publication.json`.
