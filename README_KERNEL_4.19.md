# Sony Xperia X Performance — Linux 4.19.318

Dora / Tone / MSM8996, F8131 Single SIM и F8132 Dual SIM/DSDS.
Цель: LineageOS 21 / Android 14, ARM64 userdebug. Обновлено: 2026-11-02.

**Статус этапа 3: BUILDABLE. Ядро не готово к первой безопасной загрузке.**
Проверяемая ревизия исходников: `799e29584b029fe34b8344a726989d4836d258d5`.
Чистая сборка этой ревизии: **код завершения 0; vmlinux, Image/Image.gz, четыре DTB и Image.gz-dtb проверены**.
Полные результаты, предупреждения и хеши артефактов находятся в
[протоколе сборки](Documentation/sony/dora-build-validation.txt).
На F8131 и F8132 Linux 4.19 не загружался; `boot.img` не создан.

Этап 3 восстанавливает реальные аппаратные данные и зависимости. Критические
цепочки UFS, дисплея и GPU пока явно отключены в итоговом DT: native GCC/MMCC
не воспроизводят необходимые Sony RPM voltage-corner votes. CPU clock provider
только читает настройки firmware; DVFS, cpuidle и контроллеры напряжения не
включены. Это незавершённая реализация питания, которую нельзя компенсировать
предположением о напряжениях, оставленных загрузчиком.

## Что реализовано

- Восстановлена исходная MSM8996 BIMC/NoC топология: 145 настоящих
  fabric/master/slave узлов, hardware clock translations, RPM bandwidth
  transport и обработка ошибок/повторных запросов. В UFS возвращены все
  12 bus cases по двум путям. Ошибки регистрации host и PHY power-on исправлены;
  проверки обязательных bus данных сохранены.
- Добавлены четыре настоящих SoC/PMIC DT варианта. Сохранены конечные Sony
  GPU CPR/fuse/voltage/frequency данные, PMIC compatibles, BCL derating и
  flash ramp. IBB discharge равен 32 во всех четырёх вариантах. Это разные
  аппаратные наборы, а не копии с подменёнными ID; их активация и выбор
  загрузчиком ещё не подтверждены.
- APCC/CBF provider теперь распознаёт MSM8996 и читает firmware rates без
  записи в PLL/ACD. Перенесены HMSS CPR3, Kryo retention/LDO и APM frontends,
  реальные bin plans и SAW/SPM/mem-acc связи. Контроллеры напряжения остаются
  отключёнными; `maxcpus=2`, `CPU_FREQ=n`, `CPU_IDLE=n` сохранены.
- В DT описана цепочка MDSS/MDP/DSI/14nm PLL/SMMU/LAB/IBB/WLED с реальными
  clocks, bus votes, regulators и Sony GPIO. Native GDSC управляет MMIO;
  status-only regulator adapter даёт legacy потребителям настоящие genpd
  votes. Экспортированы правильные PLL parent aliases для native MMCC.
  Сохранены все пять панелей, выбор по LCD ID, исходные 1080p60 команды и
  timings; wildcard fallback, 120 Hz и dynamic switching отключены.
- GPU MMCC child provider разделяет GFX clocks и базовые MMCC/CPR clocks,
  устраняя циклическую provider-зависимость. MMSS CPR3 frontend адаптирован;
  неподдержанные revisions/bins отвергаются. GPU/CPR/PMIC S2 цепочка остаётся
  отключённой до завершения RPM corners и реального питания.
- Усилены проверки итоговой `.config`, четырёх DTB, bus routes, CPU rate
  decoder, PMIC pin safety, genpd rollback, всех пяти панелей и формата
  ARM64/appended DTB. Negative tests обнаруживают отсутствующие providers,
  повреждённые hardware данные и снятые safety gates.

Ранее выполненные исправления сохранены: Sony UFS QMP14nm calibration,
QUSB2 tuning `d0/a3/83/c7`, удаление ложного цифрового USB ID, Sony BCMDHD
WPA/cipher/AKM propagation, legacy ION handles, Tasha/Clearpad и QPNP
VADC/BCL/FG/SMBcharger source ports. Компиляция драйвера не подтверждает
его привязку к работающему устройству.

## Четыре аппаратных варианта

Порядок DTB в `Image.gz-dtb` повторяет оригинальный Sony Makefile:

| SoC / PMIC | DTB | msm-id revision | Первые слова pmic-id |
| --- | --- | --- | --- |
| v3.0 / PMI8994 | `msm8996-v3.0-sony-xperia-tone-dora.dtb` | `0x30000` | `0x20009 0x2000a` |
| v3.0 / PMI8996 | `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` | `0x30000` | `0x20009 0x10013` |
| v3.1 / PMI8994 | `msm8996-sony-xperia-tone-dora.dtb` | `0x30001` | `0x20009 0x2000a` |
| v3.1 / PMI8996 | `msm8996-v3.1-pmi8996-sony-xperia-tone-dora.dtb` | `0x30001` | `0x20009 0x10013` |

Все используют SoC ID 246 и board-id `<8 0>`. F8131/F8132 не привязаны к
PMIC, stepping или одной панели. Наличие четырёх FDT в образе не доказывает
выбор правильного дерева загрузчиком Sony. Полная матрица:
[dora-compatibility.md](Documentation/sony/dora-compatibility.md).

## Оставшиеся P0

| Цепочка | Конкретный незавершённый контракт |
| --- | --- |
| UFS / GCC | RPM CX corner frontend и clock-dependent fmax votes; native UFS initialization задаёт частоты, для которых один GDSC/bandwidth vote недостаточен. Host отключён. |
| CPU / CPR / SAW | Координированная ACD→CPR→clock транзакция, rollback, цифровые RPM corners и согласование S8/S9/S10/S11/SPM ownership. Нельзя включать DVFS/четыре CPU только по наличию DT. |
| MDSS / MMCC | Sony `vdd_dig` corner coupling для MDP/DSI/SMMU clocks; MDP setup может запросить 412.5 MHz, требующие HIGH corner. Вся display power chain отключена. |
| Adreno 530 | Настоящие CX/MX corner aggregation, PMI S2 frontend, независимый MX vote и KGSL/SMMU/secure-buffer sequencing. GPU отключён. |
| UFS recovery | Согласовать host/PHY recovery после частичного power-off failure: generic power_count и calibration/PCS state нельзя исправить простым return error. |
| Зарядка / USB | Согласованные DPDM/PHY ownership, BC1.2, VBUS, MicroUSB ID/ADC и safety paths. SMBcharger не активирован; фиктивного DPDM regulator нет. |
| Android boot | Проверенный Sony boot header/DTB selection и настоящий LOS21 ramdisk; полного checkout/готового ramdisk в окружении нет. |

Подробности: [UFS/GCC corners](Documentation/sony/dora-gcc-corners.txt),
[CPU](Documentation/sony/dora-cpu-port.txt),
[дисплей](Documentation/sony/dora-display-port.txt),
[GPU/варианты](Documentation/sony/dora-gfx-variants.txt),
[USB/DPDM](Documentation/sony/dora-usb-dpdm-gap.txt).
Audio/LPASS/PIL/SSR/SLIMbus, modem/RIL/DSDS, touch I2C и камеры также требуют
аппаратной интеграции; звук, связь и suspend не заявляются рабочими.

## LineageOS 21

Итоговая `.config` проверяется по **285 явным назначениям и 101 обязательному
built-in**: Binder/BinderFS, BPF syscall/JIT/cgroup BPF, PSI/cgroups, SELinux,
seccomp, namespaces, memfd/ashmem, fscrypt, ext4/F2FS, dm-verity/FEC, loop,
OverlayFS, ION/dma-buf/fences и USB gadget. Это проверка реализации и
конфигурации, не запуск Android 14 или проверка всего vendor ABI.

Программные map flags `BPF_F_RDONLY_PROG`/`BPF_F_WRONLY_PROG` остаются
конкретным разрывом для соответствующих BPF клиентов; они не добавлены в
UAPI без семантики. Старые `bpfloader exited 1`,
`boringssl-self-check-failed` и `vold-failed` не объявлены исправленными:
нужны логи новых kernel/userspace. BoringSSL self-test, SELinux и dm-verity
не отключались. [Аудит Android 14](Documentation/sony/dora-android14-audit.md)
разделяет возможные kernel и userspace причины. Device/vendor репозитории
не менялись; приложенные внешние патчи не выдаются за применённые.

## Сборка и проверки

Рабочая ветка только `kernel-4.19-701905467803253630`. Сборка использует
AArch64 и ARM32 COMPAT toolchain, GNU make, bc, bison, flex, OpenSSL/ELF
headers и Python 3. Toolchain и O= каталог находятся вне tracked sources.

```bash
JOBS=4 OUT_DIR="$PWD/out/dora-clean" scripts/dora/build.sh
```

Для чистой проверки указывать новый пустой `OUT_DIR`. Скрипт не переключает
ветки и не прошивает устройство. Он генерирует `.config`, выполняет host
checks, собирает `vmlinux`, `System.map`, `Image`, `Image.gz` и четыре DTB,
проверяет каждый DT и их реальные различия, формирует `Image.gz-dtb` в явном
порядке и проверяет ELF/ARM64/gzip/appended FDT. Результаты:
`toolchain.txt`, `config.log`, `build.log`, `artifact-validation.json`,
`SHA256SUMS` в O= каталоге. Успех заявляется только при exit 0 всего скрипта.

Полной сборки LOS21 и `boot.img` здесь нет. До BOOT TEST READY необходимо
закончить перечисленные source P0, проверить vendor/firmware и boot format,
затем подготовить экспериментальный образ с реальным ramdisk и способом
восстановления. Прошивка, форматирование или запись userdata/modem/TA/persist
не выполнялись и не входят в эту проверку.

## Происхождение

- База Linux 4.19.318:
  [Tama-4-19/android_kernel_sony_sdm845-419](https://github.com/Tama-4-19/android_kernel_sony_sdm845-419),
  `lineage-20`, commit `51b2850a389ec9069d1d6f6ecd8fbf22f9c03f99`,
  tree `3feca968f677b76dbbdc0532b7d9a7b8463fb6d8`.
  [Запись импорта](Documentation/sony/dora-source-base.txt).
- Аппаратный эталон Sony 4.4.302:
  [0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1](https://github.com/sbot110/android_kernel_sony_msm8996/commit/0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1).
  История и исходные лицензии сохранены; рабочее 4.4 не изменено.
- Оригинальные Sony:
  [4.4 common](https://github.com/sonyxperiadev/kernel-sony-msm-4.4-common),
  [4.9 common](https://github.com/sonyxperiadev/kernel-sony-msm-4.9-common).
- Контракт Android устройства:
  [dora](https://github.com/sbot110/android_device_sony_dora),
  [tone-common](https://github.com/sbot110/android_device_sony_tone-common),
  [vendor Sony](https://github.com/sbot110/android_vendor_sony).

Данные пользователя о F8132/AUO Panel Name=4, LCD ID `0x11FFEA` и
1080×1920@60 относятся только к рабочему **4.4**. Они сохранены как
контрольный эталон и не являются свидетельством загрузки или дисплея 4.19.

SMBcharger не привязан в DT. Это не доказывает, что загрузчик остановил
физическую зарядку: исходное состояние PMIC и реальные токи не измерялись.
Безопасная зарядка и обычное использование не подтверждены.

После сборки удалена только пустая строка в конце DTS; повторно собранные
DTB и все хеши артефактов совпали. Функциональных изменений после неё нет.

Полный перечень изменённых файлов этапа: [dora-stage3-files.txt](Documentation/sony/dora-stage3-files.txt).
