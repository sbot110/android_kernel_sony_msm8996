# Stage 7: watchdog, USB и проверка boot artifacts

**BOOT TEST READY: NO.** Телефон не прошивался и не загружался. Kernel
entry, firmware ACK, eMMC enumeration/GPT и userspace init не наблюдались.
Сохранены четыре normal DTB и отдельные четыре SDHC1 diagnostic DTB;
SDHC1 не включён в normal images, SDHC2/GPU/display/charger не активированы.

## Watchdog: проверенные исходные сведения

Sony4.4 `0c295b15...` final Tone DT и оба DTB официального Sony prebuilt
`kernel-dtb-dora` revision `8176b089...` совпадают:

| Поле | Значение |
| --- | --- |
| APSS range | 0x09830000, size 0x1000 |
| Interrupts | GIC SPI 28 bark, SPI 29 bite |
| Final bark | **12000 ms** |
| Pet | 10000 ms |
| Driver default rate | 32765 Hz, clock phandle отсутствует |

Stage6 упоминал 11000 ms из generic msm8996.dtsi. Final
`msm8996-tone-common.dtsi` переопределяет bark на 12000 ms; это подтверждено
официальными DTB. Хэши и provenance: `validation-stage7/watchdog-provenance.json`.
Исходные данные не доказывают фактический bootloader/secure watchdog state.

`watchdog_v2.c` теперь возвращает реальные probe/IRQ/deferred errors,
проверяет resource/tick overflow, создаёт все fallible software resources
до WDT MMIO, корректно освобождает partial IRQ/notifier/sysfs/worker state.
Stop-aware worker/timer, PM/disable serialization, RCU global publication
и draining callbacks устраняют teardown races. При failed firmware
minidump removal backing memory удерживается. Initial-logbuf optional
path проверен отдельной сборкой. Исходные bite/restart и wake/no-wake PM
policies сохранены. Vendor watchdog config и DT activation **не включены**:
передача владения firmware и исключение раннего reset остаются P0.
Это не отключение работающего watchdog ради загрузки.

## USB/charging: намеренно закрытый Dora init

MSM8996 QUSB2 использует source DP_EN bit0/DM_EN bit2 и DP_HIGH bit1/
DM_HIGH bit3; прежняя общая раскладка SDM845 могла записать charger-detect
bit4. Исправлены masks и arming относительно фактического line-state.
Добавлены PM/mode serialization, counted clocks, PLL readback, checked
reset и quarantine после неопределённого результата; TCSR errors/defer
больше не скрываются. SDM845 использует собственную прежнюю раскладку.

Но Sony DPDM/VBUS/ID/BC1.2/SMBcharger owner отсутствует. У Dora public PHY
`.init` теперь **возвращает EOPNOTSUPP до rail/clock/reset/MMIO**, без DT
escape hatch. Подготовленные source tuning/callbacks остаются для будущей
интеграции; generic MSM8996 init не заблокирован этим Sony-only gate.
Это существенное изменение: **USB peripheral/ADB logging недоступны** в
нынешних normal и diagnostic Dora DT. Нельзя считать их fallback console.
DWC3 core распространяет PHY error до своих reset writes, что проверено
production-body host test. Parent qscratch VBUS override существует;
этот gate не доказывает безопасность всех USB/charging действий родителя.

Для снятия gate нужны реальный владелец DPDM/VBUS/ID/BC1.2, согласование
с firmware charging, L12 physical BOTH-ACK, BCL/FG/JEITA/thermal/current
measurement/protection и resource retirement через failed rollback/reprobe.
SMBcharger disabled не означает физического прекращения зарядки firmware.
Fake DPDM fixed-regulator, ослабление thermal/current или charger activation
не добавлялись. USB/charging остаётся P0.

## Read-only проверка предоставленного boot.img

Новый `scripts/dora/check-boot-image.py` принимает только существующие
файлы, не создаёт boot.img, не извлекает ramdisk на диск и не обращается
к телефону. Поддерживаемые форматы: bounded Android v0 header/page layout,
raw/gzip/XZ ARM64 Image, appended FDT v17 и newc initramfs. Неизвестные
Sony wrappers/header versions отвергаются. Проверяются header padding,
адресные интервалы по observed header, expanded Image `image_size` с BSS,
DT reserved memory, security cmdline и отсутствие alias input/report inode.
BoardConfig не подменяет наблюдаемый bootloader behavior.

```sh
python3 scripts/dora/check-boot-image.py --self-test \
  --kernel /path/to/out/arch/arm64/boot/Image.gz-dtb \
  --config /path/to/out/.config --report /tmp/kernel-inspection.json

python3 scripts/dora/check-boot-image.py --boot-image /path/to/working-LOS20-boot.img \
  --partition-size OBSERVED_BYTES \
  --compare /path/to/working-LOS21-boot.img \
  --compare-partition-size OBSERVED_BYTES \
  --bootloader-cmdline /path/to/observed-cmdline.txt \
  --report /tmp/boot-comparison.json
```

Partition size -- только фактический размер нужного boot partition; адреса
или cmdline не угадываются. При необходимости `--reserved-range START:SIZE`
добавляет независимо известный reservation. Проверка kernel artifacts не
доказывает bootloader DT selection/relocation, подпись, decompressor behavior
или запуск init. Даже успешный parser сохраняет `boot_test_ready=false`.
Рабочих boot.img LOS20/LOS21, LOS21 ramdisk, partition size и effective
bootloader cmdline сейчас нет; boot.img не собирался.

## Условия будущей диагностической загрузки

До любого плана запуска нужны P0 CPU shared power guard, watchdog и USB/
charging handoff qualification, проверенный исходный boot.img/ramdisk,
наблюдаемый boot size/DT selection/effective enforcing cmdline и load/
decompression/reserved-memory layout. Early serial transport и persistence
ramoops требуют проверки на устройстве. Проверить доступность TWRP и
проверенного восстановления **до** ручного запуска; сейчас этого evidence
нет. Никакого автоматического flash или записи eMMC не предусмотрено.

Для F8131 и F8132 отдельно фиксировать selected SoC/PMIC DTB и LCD-ID,
не выводить их из Single/Dual SIM. F8132 DSDS/hybrid-UIM2 и SDHC2 остаются
непроверенными. First-run evidence: kernel entry/serial, pstore, watchdog,
RPM ACK, SDHC1 IRQ/DMA, CID/GPT/partition names и init/SELinux/APEX stages.
Timeout/reset/quarantine/failed ACK требуют остановки квалификации;
отсутствие изображения не доказывает отсутствие kernel entry. Display
сохраняет все пять LCD-ID panels 1080p60 с activation gates.
