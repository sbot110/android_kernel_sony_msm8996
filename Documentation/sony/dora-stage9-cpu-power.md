# Dora Stage 9: CPU shared resources и граница writable provider

Аппаратный reference — неизменённый Sony Linux4.4 commit
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`. База Stage9 — опубликованный
Stage8 `e9f1a7be0c13e1447a0cf398af31a9efe5868ad1`.

Stage9 подключает один общий resource owner к настоящим APCC/CBF read-only
clock providers. Это частичная интеграция: singleton владеет counted
XO/GPLL0 parent preparations и постоянными provider ports, но **не владеет
CPU voltage leases**. Native APCC/CBF/ACD/PLL modifying callbacks остаются
неподключёнными. Software mutex не объявляется исключением автономных
CPR/SPM/LMH изменений. Второй transaction engine не создаётся.

## Подтверждённые аппаратные зависимости

### CPU digital supply — MX S2 ACTIVE-only

В Sony `arch/arm/boot/dts/qcom/msm8996.dtsi` CPU clock `vdd-dig-supply`
указывает на `pm8994_s2_corner_ao`; окончательные v3/Tone overrides этот
контракт не заменяют. `msm8996-regulator.dtsi` описывает VDD_MX S2+S12 как
RPM `smpa`, resource-id2, ACTIVE-only set1. Это отдельный ресурс относительно
GCC/MMCC CX `smpa` id1 BOTH. Подмена CPU digital supply существующим CX vote
была бы ошибкой аппаратного контракта.

CPU PLL использует Sony `VDD_DIG_FMAX_MAP1(LOW, 3000000000)`;
`drivers/clk/msm/vdd-level-8996.h` отображает LOW на
`RPM_CORNER_SVS_SOC = 4` (wire corn3). Это **не** CPU CPR `plan.level` и
не абсолютное напряжение. Stage9 восстанавливает четыре supply phandles
APCC: PWR, PERF, CBF и MX AO. MX provider и CPU voltage controllers остаются
выключенными; свойства сами по себе не захватывают rail и не посылают ACK.

### S11 и исходный LMHv1 DPM/ODCM

Sony `msm8996-v3.dtsi` связывает `qcom,lmh_v1` с физическим S11 через
`vdd-apss-supply = <&pm8994_s11>`. Исходный `lmh_lite.c` использует
`lmh_voltage_change_notifier()` / `evaluate_and_config_odcm()`:

| Событие S11 | Требование Sony LMHv1 |
| --- | --- |
| Повышение напряжения | Записать DPM voltage scale до повышения; при пересечении порога включить ODCM до повышения |
| Понижение напряжения | Обновить DPM scale после соответствующего POST_VOLTAGE_CHANGE; выключить ODCM после понижения |
| Abort | Восстановить предыдущее состояние ODCM |

Именованное `qcom,lmh-odcm-disable-threshold-mA = <850>` исходный driver
сохраняет как `odcm_thresh_mV` и сравнивает с `rail_uV / 1000`. Поэтому
операционная единица исходного кода — mV, несмотря на DT suffix. Этот
source факт не разрешает менять Sony voltage/frequency limits.

Source constants DPM `0x09950804` и ODCM
`0x09981030/0x09991030/0x099a1028/0x099b1030/0x099c1030/0x099d1028`
служат provenance. Stage9 не добавляет записи по этим адресам. Существующий
4.19 `drivers/thermal/qcom/msm_lmh_dcvs.c`, compatible `qcom,msm-hw-limits`,
реализует более новый DCVSH, а не исходный MSM8996 LMHv1. Его OSM frequency
register model нельзя подставить вместо Sony DPM/ODCM и firmware protocol.
Полный перенос LMHv1 с secure service/profile/sensor/IRQ ownership и
интеграцией физического S11 остаётся конкретным P0.

### CPR/AVS stop не подтверждается одним BUSY bit

Sony и текущий CPR3 определяют CPR_CTL+0x4 LOOP_EN bit0,
CPR_STATUS+0x8 BUSY bit0, HW_CLOSED_LOOP+0x3000 (ENABLE=0, DISABLE=1).
IRQ recovery ожидает BUSY=1 вместе с LOOP_EN=0; отдельный aging protocol
ожидает BUSY=0. Ни одно из этих состояний отдельно не является доказанным
универсальным exclusion handshake. Нужны revision-specific stop protocol,
HW-loop/SAW AVS readback, IRQ drain, PM и ownership endpoints.

Dora v3 включает generic `msm8996.dtsi` непосредственно; HW closed-loop DT
property исходно относится к v2. Ожидаемый software closed-loop режим v3
не доказывает bootloader state или отсутствие автономного SAW/firmware.
Linux `scm_lmh_lock` сериализует лишь участвующие Linux calls. Он не выдаёт
firmware ownership grant; исходный v3 clock set_rate вообще пропускает этот
mutex. Удержание task mutex после quarantined return было бы некорректным.
Thermal/emergency handling не отключается ради получения такого guard.

## Реальная интеграция Stage9

`clk-msm8996-cpu-power.c` обслуживает три домена через два постоянных
APCC/CBF ports. Attach проверяет Sony supply identity, XO19.2MHz, GPLL0
600MHz и реальный GCC supplier link. Один owner удерживает ровно counted
parent references; второй port использует ту же CCF identity. Провайдер
остаётся recalc-only: parent preparation не программирует CPU PLL и не
означает CPU MX/CPR voltage vote.

Selected fuse plans копируются в постоянное storage. После публикации
массивы неизменяемы. Короткий observation spinlock согласует предшествующий
CBF readback с публикацией APCC plans; под ним нет CCF/regulator/OF/sleeping
calls. CCF recalc не берёт owner mutex, поэтому нет prepare-lock → owner-lock
→ prepare-lock inversion. Нулевой/нестабильный readback или rate выше
selected plan фиксирует sticky fault. Первый fault не заменяется последующими.

Проверки до захвата owner допускают обычный deferred probe. После частично
применённого parent prepare результат неизвестен: owner остаётся quarantined,
подготовленные parents не выключаются. Retirement сохраняет ограниченный
постоянный tombstone и references, чтобы другой probe не смог объявить
старое состояние OFF или заново захватить ownership. Это намеренное
удержание CPU-зависимых ресурсов, а не доказанный hardware cleanup/handoff.

Sysfs `cpu_power_state` сообщает readonly, domains, suspended, parents,
voltage_leases=0, blockers, errno, CPR/MX status и последние rates. Typed
CPR/RPM by-node snapshots находят только опубликованный exact provider под
registry/controller locks; regulator consumer lookup не используется.
Это важно: обычный `regulator_get_optional()` способен при supply resolution
включить ещё не разрешённый upstream supply и потому не годится для
гарантированно observational status read.

CPR snapshot дважды читает CPR_CTL/STATUS/HW mode при уже включённых clocks;
нестабильность даёт EAGAIN, suspended — EBUSY, отсутствующий подтверждённый
active corner — ENODATA, sticky power error передаётся вызывающему.
`vdd_uv` — reported parent setpoint, не ADC measurement. RPM snapshot
показывает typed metadata/request/ownership/quarantine без новых сообщений,
votes или recovery. Успешный snapshot не превращается в lease/ACK.

Suspend/resume owner пока сериализует диагностические flags. Он не
программирует rails/PLL и не подтверждает hardware resume readiness. Полный
CPR/SPM/APM/thermal/collapse PM guard остаётся незавершённым.

SPM/SAW изменения закрывают software endpoint lifetime: постоянный descriptor,
exclusive physical-range/CPU/name claim, active users drain до devres unmap,
retirement tombstone, shared API/IPI serialization. Свежий AVS/PMIC readback,
register/sequence bounds и propagated failures не заменяют firmware handoff.
CPR fault closure запрещает продолжение IRQ voltage handling после
неопределённого rail/APM результата и сохраняет quarantine. Это не означает
активацию отключённых HMSS/SPM/APM/Kryo nodes.

Сохраняется дополнительное ограничение унаследованного низкоуровневого
`msm_spm_drv_set_vdd()`: после failed PMIC ACK он восстанавливает AVS до
того, как API wrapper фиксирует quarantine. Endpoint locking и свежий
readback не превращают эту последовательность в подтверждённое исключение
автономного AVS. Она требует отдельного source/hardware qualification до
активации CPU voltage path; текущие исправления этого не заявляют.

## Sony function → Linux4.19 → resource → dependency → activation

| Sony4.4 function / binding | Linux4.19 implementation | Resource / possible writer | Dependency / completion | Activation |
| --- | --- | --- | --- | --- |
| clock_cpu_8996_resources_init / vdd-dig | shared owner + typed qcom-rpm-corner | MX S2+S12, smpa2 ACTIVE-only corn | LOW logical4, actual RPM ACK, AO aggregate | Supply link restored; provider disabled, no lease |
| resources_init / vdd-pwrcl | CPR3 regulator-pwrcl | PWR demand, thread0 | Selected CPU CPR index | Disabled |
| resources_init / vdd-perfcl | CPR3 thread1 regulator | PERF demand, shared S11 | Same controller aggregation, Kryo1/L3/mem-acc | Disabled |
| resources_init / vdd-cbf | CPR3 thread0 CBF regulator/proxy | CBF demand shares PWR thread and S11 | Proxy/aggregate source CBF plan | Disabled |
| cpu_clk_8996_pre_set_rate | Existing transaction engine + native_safe | ACD/main mux before voltage increase | Safe source readback before CPR vote; CPU-affine collapse exclusion | Modifying callbacks unbound |
| cpu_clk_8996_post_set_rate | Engine restore_acd + native_program | ACD output after voltage decrease | Final rate/lock; collapse reservation released last | Modifying callbacks unbound |
| cpu_clk_8996_set_rate / source PLL ops | native_primary/set_alt/div2/rate | APCC/CBF primary/alternate PLL and mux | Prepared parents, lock/readback, source limits | Observer bound; native writer unbound |
| cpu_clock_8996_acd_init / CPU_STARTING | native_acd_cpu/native_acd | Target-cluster L2ACD and APCC SSS/PSCTL | Target online CPU, selector lock, hotplug/collapse guard | Unbound |
| cpr3_msm8996_hmss_read_fuse_data | HMSS frontend | CPR/PVS fuses/calibration | Source row/quotient/interpolation data; not merely clock speedbin | Compiled, controller disabled |
| cpr3_regulator_set_voltage/update_ctrl_state | CPR3 core + sticky power faults | Aggregate CPU/CBF corners, S11/LDO/mem-acc | Controller mutex plus unresolved autonomous exclusion | Disabled |
| cpr3_irq_handler / ceiling handler | CPR3 core + IRQ quarantine | Software CPR feedback and SAW max IRQ | Correct stop/IRQ/readback, no completion after uncertain transition | Disabled |
| msm_spm_set_vdd / drv_set_vdd | SPM API + signed errors/lifetime | Four-phase S11 gang leader via SAW | PMIC sent-data and FSM idle, not ADC | CPU controllers/S11 disabled |
| spm_regulator_avs_* | SPM frontend | Gang main/AVS limits | Main/AVS serialization and fresh readback; transfer protocol missing | Disabled |
| msm_apm_set_supply / msm8996_apm_switch_* | Stage8 APM owner/readback/tombstones | APCC/MX memory-array supply switch | VDD threshold, LDO/mem-acc, MODE/STATUS/SMC confirmation | Disabled |
| kryo_regulator_set_voltage/set_bypass | Kryo frontend | Functional LDO/BHS | Supply headroom, APM/mem-acc sequencing | Disabled |
| kryo CPU_PM callback / lpm_prepare/resume | Kryo frontend | Independent LDO/BHS/retention writes | Shared guard, CPU_PM event affinity/counters | Disabled |
| lmh_voltage_change_notifier/lmh_dpm_init | No compatible LMHv1 backend | S11 DPM scale and ODCM | Before-increase/after-decrease/abort notifier contract | Missing; P0 |
| lmh_poll/ISR / profile/intensity SCM | Newer DCVSH is not equivalent | Secure firmware throttling | LMHv1 services/profile/sensors/source IRQ23 | Missing; P0 |
| msm_thermal limits/hotplug/VDD restriction | Generic TSENS/thermal pieces | CPU cap/online mask/CX/emergency reset | Final Tone calibration/trips/cooling/firmware | Unqualified |
| lpm-levels L2/L3/PSCI sleep | Vendor lpm-levels / platform PSCI | SAW programs/RPM sleep/collapse | Actual PSCI IDs and hotplug/IRQ/latency ordering | CPU_IDLE=n |
| Original PM QoS cpus_affine + IPI | No complete transferable guard | Cluster-collapse exclusion | Modern latency API alone is insufficient | Missing; P0 |
| GCC/MMCC vdd_dig | Existing voltage contracts/RPM | CX smpa1 BOTH, L12 analog | Preserve HIGH/BOTH ACK/quarantine | 56 GCC / 49 MMCC retained |

## Что необходимо до writable CPU binding

1. Подтверждённый revision-specific CPR/SAW stop/readback и IRQ drain с
   transferable token, который остаётся действителен после fault return.
2. Одна согласованная S11 gang ownership модель для CPR/SPM/APM/Kryo и LMHv1;
   четыре независимых native RPM S8–S11 не допускаются.
3. Реальный LMHv1 DPM/ODCM notifier, secure/thermal profile и emergency
   arbitration, не блокирующий защитные действия firmware.
4. Шесть подтверждённых primary/digital leases существующего transaction
   engine, подготовленные нужные clock parents, проверенный known baseline
   и recovery только после подтверждённого readback.
5. CPU hotplug/collapse/retention/suspend coordination и runtime evidence
   actual selected fuse/SoC/PMIC/SAW versions, APCC/CBF/ACD/PLL, APM/CPR/AVS,
   S11 completion и thermal events на работающем reference.

Требуемые phone observations должны поступить через уже проверенные
read-only diagnostics работающего Sony4.4, а не произвольные devmem/SMC
операции. Сохраняются maxcpus=2, CPU_FREQ=n, CPU_IDLE=n. Host tests
подтверждают software invariants; аппаратная безопасность и загрузка
Linux4.19 не подтверждены.
