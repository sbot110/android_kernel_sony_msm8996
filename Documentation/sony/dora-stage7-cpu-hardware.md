# Stage 7: native CPU callbacks и граница shared-rail ownership

Источник регистров/последовательностей: неизменённый Sony4.4
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`, `clock-cpu-8996.c`,
`clock-pll.c`, `clock-alpha-pll.c`, `kryo-l2-accessors.c`, SPM/CPR/APM DT.
Значения SDM845 не переносились. Официальный Sony4.4 repository revision
`8176b0892ba13a0401a22d7322bc109f473a24b9` распространяет prebuilt kernels,
а не эти C sources; его DTB служат независимой проверкой описания устройства.

## Что реализовано

`clk-msm8996-cpu-native.c` предоставляет настоящие callbacks существующего
transaction engine через `msm8996_cpu_native_bind()`. API скомпилирован и
связан с ядром, но **live hardware instance не создаётся**. Активный APCC
provider остаётся read-only. Нет нового writable CCF/DT provider, CPU_FREQ
и CPU_IDLE выключены, `maxcpus=2` сохранён.

* APCC PWR/PERF и CBF: отдельные source mux RMW, AUX parking с барьером и
  5 us settling, integer warm-PLL L programming, source CBF CFA sequence,
  50 us transient-lock delay, bounded lock polling и register readback.
* Fresh двойной readback выбранного PLL/mux; AUX вычисляется по реальным
  GPLL0 registers и APCS divider, не cached CCF rate. Неизвестный источник,
  нестабильный snapshot или AUX, отличный от source 300 MHz, отвергаются.
* ACD: настоящие Kryo L2 selector/data sysregs, target-cluster IPI под
  CPU hotplug read lock, raw selector lock, исходные PWR/PERF ordering и
  exact readback TD/DVMRC/SSCR/CR/SSS/PSCTL. Offline big cluster даёт ENODEV;
  online CPU не добавляются ради операции.
* PERF alternate PLL: source OFFLINE/ACK/FSM/ACTIVE protocol, bounded
  timeout, source VCO selection, integer L/alpha and exact readback. Нельзя
  retune выбранный alternate parent.
* Резервирование одного владельца всех трёх domains; второй shared owner
  отвергается. Peer cluster/CBF rates должны оставаться неизменными.
  Существующий engine сохраняет voltage-before-frequency, безопасное
  снижение частоты перед снятием votes, leases и quarantine при partial
  failure. Native layer не придумывает соотношение частот CPU/CBF.
* Подготовлены freeze/resume callbacks: active/quarantined transaction
  блокирует suspend; resume с изменённым readback оставляет freeze.
  Это API для будущего owner, не зарегистрированная аппаратная PM цепочка.

Поддерживается только подтверждённый source-v3 warm integer primary PLL:
XO 19.2 MHz, source config/test/divider, прежние fuse-selected limits.
Pro, fractional/cold/unknown state и alternate boot leg отвергаются **до
изменения mux**. Отсутствующий power backend возвращает EPROBE_DEFER.
Это намеренно неполное покрытие исходных режимов, не их автоматическая
переконфигурация. Register maps не угадываются и live ioremap не добавлен.

## Почему shared rails пока не подключены

S8/S9/S10/S11 образуют исходный SPM-controlled gang. Независимые native RPM
regulators не включались. Software mutex clock adapter не исключает
автономный CPR, SAW collapse или APM. Mandatory power_ops требуют настоящих:

1. Единого transferable guard для обоих clusters, CBF, CPR/SPM/APM,
   hotplug и collapse. Guard должен переживать возврат после quarantine;
   task-owned mutex, оставленный locked через return, непригоден.
2. Шести подтверждённых primary+digital leases и voltage corner arbitration,
   source HMSS/CPR3/Kryo functional/retention LDO/APM ownership.
3. Counted prepared clock parents и подтверждённого исходного voltage state.
4. Согласования thermal, bootloader, suspend/resume и lifetime. Owner/maps/
   selected plans сейчас должны быть permanently pinned; unsafe destroy
   или автоматический recovery API не предоставлены.

В дереве нет реализации этого backend. Нельзя активировать callbacks,
используя пустые enter/ready/vote или независимый RPM S8--S11 frontend.
Полный CPU hardware integration остаётся P0.

## Реальные исправления SPM

В `spm-regulator.c` убран автоматический direct-SPMI fallback после SPM
error; explicit source `qcom,bypass-spm` остаётся отдельной политикой.
Main и AVS rdev разделяют instance mutex. Неопределённый write/readback/SMC
оставляет sticky quarantine. Requested offline voltage больше не выдаётся
за hardware readback. Исправлены AVS rounding/limits, нулевой max step,
init errors и OF/rdev cleanup. В `spm_devices.c` сохранён signed errno,
отвергаются invalid CPU/ERR_PTR endpoints, failed SAW cache становится
UNKNOWN; неизвестное состояние не разрешает `turn_on_cpu_rail` обход.

Instance lock не является общим CPR/SPM/APM guard. Instance quarantine
пока не сохраняется через unbind/reprobe. Старый SAW remove оставляет
per-CPU ссылки; lifetime необходимо закрепить или исправить до активации.
SPM/CPR/S11 hardware nodes остались gated.

Host harness исполняет native callbacks **вместе с production engine**,
а SPM harness -- production main/AVS/SAW helpers. Проверяются sequencing,
lock/readback/timeouts, bad boot config, lost ACK, partial rollback,
quarantine, peer-rate drift, suspend/resume, deferred errors и concurrent
consumers. MMIO/sysreg/IPI/transport boundaries моделируются. ASan/UBSan
проверяют программные инварианты и не подтверждают физическую безопасность.
