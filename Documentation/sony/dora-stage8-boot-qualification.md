# Stage 8: USB parent, watchdog и boot chain qualification

**BOOT TEST READY: NO; BOOTED: NO; DAILY USE READY: NO.** В этом этапе
нет подключения телефона, прошивки или записи разделов/eMMC.

## USB/charging

Stage7 PHY gate был слишком поздним для parent driver: `dwc3-qcom.c`
сбрасывает controller, включает clocks, выбирает UTMI и записывает
qscratch VBUS override до child PHY init. Теперь Dora root aliases
`somc,dora-row`/`sony,dora-row` отвергаются в самом начале parent probe с
EOPNOTSUPP, до allocation/reset/clocks/MMIO/child population. Это проверено
исполнением **полного** production probe; generic boards сохраняют свой
path. Проверяются оба alias и compiled normal/diagnostic trees.

PHY gate Stage7 остаётся. Нет DT bypass, fake DPDM или charger activation.
Ранний отказ parent предотвращает его вмешательство в firmware USB state,
но **не доказывает безопасность физической зарядки**, оставленной firmware.
DPDM/VBUS/ID/BC1.2, FG/BCL/JEITA/current/thermal ownership и shutdown handoff
остаются P0. USB/ADB/MTP не доступны и не считаются logging transport.

## Watchdog

Источник Sony4.4 `0c295b15...` final Tone и официальный prebuilt revision
`8176b089...` подтверждают base0x9830000, size0x1000, SPI28/29,
bark12000ms/pet10000ms. Source default rate32765Hz -- не измерение частоты
на конкретном аппарате. Provenance и хэши Stage7 сохранены и используются
как прежние source evidence, не как новые phone observations.

Driver probe/resource/IRQ/worker/PM tests Stage7 повторно проходят в полном
Stage8 pipeline. Для handoff отсутствуют: observed WDT0_EN/STS/BARK_TIME/
BITE_TIME после bootloader, допустимость non-secure доступа, secure firmware
ownership/SCM policy, routing bark/bite и фактические reset/panic/suspend
логи. Не предлагается произвольный devmem: чтение MMIO надо организовать
только через проверенный диагностический механизм с известным ownership.
Inherited vendor config и node не активированы и не отключались ради boot.

## Поиск boot artifacts

Read-only поиск в доступных `/workspace` и `/tmp` не обнаружил рабочего
LOS20/LOS21 boot.img или идентифицированного LOS21 ramdisk. Protected
provider directory `/tmp/codex-daemon-1000` и internal metadata явно
исключены; отчёт поиска содержит roots/exclusions/errors. Известный Sony
`kernel-dtb-dora` -- kernel prebuilt, а не boot.img. Kernel-built O= initramfs
не является LOS21 ramdisk. Наличие BoardConfig не доказывает actual loader
offsets, partition size или merged cmdline.

Нужны следующие **реальные** файлы/сведения:

1. Рабочий boot.img LOS20 с provenance (build/device/hash), при наличии --
   собранный boot.img LOS21 и отдельный ramdisk, соответствующие этой модели.
2. Фактический размер boot partition, источник этого значения и observed
   symlink/block path; F8131/F8132 не получают общий образ по предположению.
3. Effective cmdline из рабочего boot/kernel log, наблюдения DTB selection,
   loader relocation/decompression и Sony header/signature requirements.
4. Serial/pstore/ramoops evidence, recovery/TWRP availability и реальные
   early kernel/RPM/eMMC/GPT/by-name/init/SELinux/APEX логи.

Безопасные host-команды для уже существующих файлов (пути и размер заменить
наблюдаемыми; команды ничего не собирают и не записывают на телефон):

```sh
sha256sum /path/to/working-LOS20-boot.img /path/to/built-LOS21-boot.img
stat --format='%n %s bytes' /path/to/working-LOS20-boot.img /path/to/built-LOS21-boot.img
python3 scripts/dora/check-boot-image.py --self-test \
  --boot-image /path/to/working-LOS20-boot.img --partition-size OBSERVED_BYTES \
  --compare /path/to/built-LOS21-boot.img --compare-partition-size OBSERVED_BYTES \
  --bootloader-cmdline /path/to/observed-cmdline.txt --report /tmp/boot-comparison.json
```

Для существующего LOS build output найти файлы read-only можно командой
`rg --files --hidden /path/to/LOS/out -g '*boot*.img' -g '*ramdisk*'`.
Передавать файлы обычным копированием с host backup/build output в workspace.
Здесь не выполнялись adb/fastboot/flash/dd или чтение block devices.
Parser анализирует Android v0/header/page layout, ARM64 raw/gzip/XZ,
expanded runtime extent, appended FDT/reservations/selection tuples,
newc и security cmdline. Неподдержанные Sony wrappers не угадываются.
Boot.img с неподтверждёнными параметрами не создавался.

## Минимальная цепочка и её границы

RPM transport → native/corner bank readiness/ACK → GCC/CX bootstrap →
SDHC1 physical L20/S4 → counted clocks/GPLL4/NoC → controller IRQ/DMA/CQE →
eMMC/GPT/partitions → init. Supplier links и matching early-PM phases
Stage5/6 сохранены; Stage8 CCF floor/handoff fixes не снимают HIGH при
неизвестном состоянии. Новый CPU shared power backend не активирован,
поэтому нельзя заявлять его runtime взаимодействие с SDHC1.

Четыре normal DTB оставляют SDHC1 off. Отдельный workflow собирает только
четыре experimental SDHC1 DTB и проверяет unchanged normal hashes; SDHC2
и UFS остаются off. До реальных CID/IRQ/GPT/HS200/HS400 logs накопитель
не объявляется обнаруженным. Display/GPU остаются gated; сохранены пять
LCD-ID panels 1080p60, без AUO120/dynamic FPS или выбора по модели SIM.
Отсутствие изображения не доказывает отсутствие kernel entry.
