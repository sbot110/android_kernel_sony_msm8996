# Stage 8: CPU shared power, исправленные границы и live activation

**Полный shared CPU power backend пока не реализован; live APCC provider
остаётся read-only.** Новые исправления исполняются в настоящих APM/CPR/SPM/
CCF paths, но не дают основания активировать gated power nodes. Сохранены
`maxcpus=2`, CPU_FREQ=n, CPU_IDLE=n и все source frequency/voltage/fuse plans.

## Проверенная модель Sony

Эталон -- неизменённый Sony4.4
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`:

* `msm8996-regulator.dtsi`: PM8994 S8+S9+S10+S11 образуют четырёхфазный
  VDD_APCC, S11 -- gang leader (`spm-regulator@3200`). Это не четыре
  независимых RPM supply. Main и AVS-limit providers управляют одним gang.
* APCC CPR3/HMSS controller агрегирует оба threads; CBF связан через proxy
  supply. VDD и VDD-limit ссылаются на S11 main/AVS, mem-acc -- на L3 и
  cluster accelerators, functional/retention LDO -- на Kryo regulators.
* APM выбирает APCC/MX для CPU/L2/L3 arrays. HMSS<1.2 дополнительно меняет
  источники APC/CBF PLL и SAW events; secure source override идёт через LMH
  SMC. Этот второй путь записи clocks нельзя исключить одним native mutex.
* Sony pre_set_rate уводит ACD до повышения voltage; post_set_rate
  восстанавливает ACD после снижения. Generic CCF corner/notifier frontend
  по-прежнему не подходит: он поднимает voltage до PRE_RATE_CHANGE.

В 4.19 эти frontends собраны, их данные сохранены во всех четырёх variants,
но CPUPM/SAW/CPR/APM/Kryo controllers не активированы. Native S8--S11 остаются
disabled; значения SDM845 не переносились.

## APM/CPR: устранён опасный rollback

Ранее `msm_apm_get_supply()` возвращал zero-initialized/cached enum. После
APM timeout `cpr3_regulator_switch_apm_mode()` без проверки снижал VDD к
старому voltage и объявлял его last_volt, хотя switch мог уже примениться.
Это не подтверждённый baseline.

Теперь MSM8996/pro APM читает реальные MODE/CTL_STS дважды, проверяет
стабильность и согласованность source states. Unknown/intermediate state
возвращает errno. Public get/set сериализуются вокруг всей secure/MMIO
последовательности. Same-supply operation требует fresh readback; после
switch обязательны completion/status readback и совпадение target supply.
SMC error, timeout или invalid readback сохраняет первый error; повторный
get/set не снимает quarantine, даже если позднее регистры выглядят нормально.
Внутренние исходные revision-specific sequences не заменены.

CPR проверяет APM до update/scale voltage paths. У crossover сохраняется
подтверждённый запрос threshold voltage; при failed switch нет снижения к
старому значению. Partial regulator/LDO preparation тоже оставляет power
fault. Sensing loop не перезапускается автоматически. Это удержание
неопределённого состояния, не новый voltage plan или отключение thermal
protection. Значение запроса не выдаётся за измеренное напряжение PMIC.
APM errno теперь хранится в signed int у LDO/mem-acc consumers; сравнение
отрицательного errno с unsigned enum больше не может скрыть отказ.
CPR suspend/resume возвращают настоящий error; failed resume сохраняет
suspended/power-fault state вместо ложного success.

## APM ownership и lifetime

Probe проверяет resource bounds и запрещает второго owner того же DT node
или пересекающегося physical APM range. До publication проверяется исходный
MSM8996 register state без записей. Deferred/resource failures освобождают
неопубликованный descriptor. У public APM raw handle нет put API: после
publication descriptor/device/module сохраняются как bounded tombstone.
Remove сериализуется с in-flight get/set, помечает owner retired до devres
unmap; старый handle возвращает sticky error без MMIO. Reprobe этого bank
запрещён до reboot; попытка unbind не очищает quarantine. Descriptor не
освобождается как будто voltage/clock ownership уже безопасно передано.
Debugfs использует тот же readback/error API, а не cached enum.

Это настоящее владение APM endpoint, **не общий владелец всей CPU gang**.
Сохранение descriptor намеренно удерживает bounded memory/device/module
references; автоматического release/recovery после uncertain writes нет.
Для MSM8953 сохранён его прежний supply readback контракт; новое MODE/STS
декодирование относится к исходным MSM8996/pro maps.

## SAW/SPM sequences и publication

`msm_spm_drv_write_seq_data()` раньше читал cmd до первого 0x0f без длины
и без границы выделенного shadow RAM. Новое внутреннее API требует cmd_len,
проверяет terminator и всю ёмкость до изменения offset/shadow. Число entries
сохраняется из реального SAW ID при allocation. Byte replacement сохраняет
соседние bytes вместо ошибочного OR с предыдущим значением. Flush проверяет
NULL до обращения к descriptor и использует размер выделенного shadow.

DT parser передаёт настоящую property length, проверяет число modes и
заполняет весь массив; unknown mode не превращается в partial success.
Незавершённый init не публикует list/per-CPU provider; failed allocation/name
не выдаётся за successful partial-goods probe. Partial init освобождает
shadow/modes. Повторная инициализация уже initialized CPU object отвергается
до перезаписи. Внутренние board-data callers вне этого дерева должны
предоставлять cmd_len; в дереве источником modes является DT parser.

SPM main/AVS lock и sticky instance quarantine Stage7 сохранены. Полные SAW
in-flight removal, raw low-power handles, quarantine через SPM reprobe и
autonomous AVS/collapse exclusion ещё не решены. Нельзя включать S11 на
основании sequence tests.

## Native callbacks и CCF

Native bind теперь резервирует уникальные native object **и** transaction
object. Повторная привязка к другому domain больше не перенаправляет data
старого engine и не переинициализирует его mutex. До записи проверяются
частоты всех трёх domains по выбранным fuse plans; неизвестный CFA/test
state отвергается до AUX/mux операций. Сам transaction engine не заменялся.

CCF сохраняет максимум прежнего recovery floor при потере следующих
per-clock owners. Faulted/enable-uncertain class не может объявить handoff
завершённым и тем самым разрешить освобождение bootstrap analog vote.
Stage6 same-owner sole healthy HIGH reattach остаётся ограниченным;
потерянные per-clock identities/parents и uncertain PLL state не угадываются.
Все 56 GCC/49 MMCC contracts и fresh ACK/quarantine rules сохранены.

## Что требуется для настоящего bind к live provider

Остаётся P0: transferable, сохраняемый при quarantine owner для обоих
clusters/CBF, CPR controller и main/AVS S11, APM/LMH source override,
Kryo retention, thermal, CPU hotplug и power collapse. Нужны counted
prepared parents и шесть реальных primary+digital leases с известным
bootloader baseline. Нынешние controller mutexes не образуют этот guard,
и их нельзя удерживать через return после failure из другого task.
Также не подтверждены firmware state/handoff и реальные thermal/collapse
sequences телефона. API для согласованного снятия этих блокировок не готов.

Поэтому native power_ops не подменены пустыми callbacks, writable CCF ops
не добавлены, experimental CPU activation configuration не создавалась.
Это остающаяся инженерная работа, а не утверждение о завершении основной
цели Stage8. Production-code host tests и ASan/UBSan подтверждают только
программные инварианты на явных API/MMIO/SMC boundaries.
