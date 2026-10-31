# F8131 / F8132: совместимость и границы проверки

Обе модели являются обязательными целями: F8131 Single SIM и F8132 Dual SIM,
Sony Xperia X Performance / dora / MSM8996. Нельзя считать эту таблицу
заявлением о готовом аппаратном порте Linux 4.19 или успешной загрузке LOS21.

Статусы: **С** — проверено по исходникам; **Б** — проверено компиляцией;
**Д4.4** — данные пользователя с рабочего Sony 4.4; **Н** — физический тест
нового 4.19 не выполнен; **О** — необходимый компонент/связка отсутствует.
Ни одна функция 4.19 пока не имеет статуса физически подтверждённой.

## Модели, board-id и варианты DT

Эталон: 0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1.
После препроцессинга оригинальных DTS получены следующие корневые свойства:

| Оригинальный dora DTB | qcom,msm-id | qcom,board-id | qcom,pmic-id |
| --- | --- | --- | --- |
| msm8996-v3.0-tone-dora_generic | 246 0x30000 | 8 0 | 0x20009 0x2000a 0 0 |
| msm8996-v3-tone-dora_generic | 246 0x30001 | 8 0 | 0x20009 0x2000a 0 0 |
| msm8996-v3.0-pmi8996-tone-dora_generic | 246 0x30000 | 8 0 | 0x20009 0x10013 0 0 |
| msm8996-v3-pmi8996-tone-dora_generic | 246 0x30001 | 8 0 | 0x20009 0x10013 0 0 |

Машинная копия результата: [dora-original-dtb-ids.json](dora-original-dtb-ids.json).
Все четыре используют somc,dora-row и qcom,msm8996. Имена обозначают SoC/PMIC,
а не число SIM. **Нет доказательства**, что F8131 всегда соответствует одному
PMIC/stepping, а F8132 другому. Назначать DTB по этому предположению нельзя.

В оригинальном dora DTS нет отдельного F8131/F8132 GPIO override: используются
общие tone/dora includes. PMI8996 include меняет не только ID, но и параметры
IBB discharge, BCL derating и flash ramp. v3.0 имеет собственные clock/CPR
overrides. Копирование только ID в начальный 4.19 DTS не переносит эти отличия.
Поэтому фиктивные четыре DTB с одними изменёнными идентификаторами не созданы.

Текущий начальный 4.19 DT описывает только v3.1 + PM8994/PMI8994 и ограничивает
bring-up двумя CPU. Полное покрытие четырёх исходных вариантов ещё отсутствует.

## SIM / modem / radio

Прочитан android_device_sony_tone-common/rootdir/etc/init.qcom.msim.sh (main).
Скрипт проверяет строку Model в разделе LTALabel; F8132 входит в список MSIM.
Для него задаётся persist.vendor.radio.multisim.config=dsds, иначе ss.
Это источник различия моделей в Android userspace, а не подтверждение,
что modem/SSR/SMD/GLINK/RMNET/IPA в новом ядре уже работают.

Скрипты определения модели и раздел LTALabel не изменялись. Не добавлены
принудительные ss/dsds, модемная прошивка другой модели или SIM GPIO наугад.
Нужны перенос реального firmware/remoteproc/vendor ABI и раздельные тесты:
F8131 — SIM1, звонки/SMS/LTE/передача данных; F8132 — оба слота, DSDS standby,
переключение data SIM, входящие звонки на оба номера и восстановление модема.

## Панели: все штатные альтернативы сохранены

Все шесть исходных файлов dsi-panel-dora и AUO/JDI/LGD/Sharp ID3/ID9 сохранены
побайтово в arch/arm64/boot/dts/qcom/sony/dora/. Лицензии сохранены.
Команды включения, выключения и power/reset последовательности сравниваются
по SHA256 с эталоном; это проверка данных, не исполнение на панели.

| Панель | Panel Name | Диапазон somc,lcd-id-adc, включительно |
| --- | --- | --- |
| AUO Synaptics | 4 | 1087000–1231000 |
| JDI Synaptics | 6 | 353000–653000 |
| LGD Synaptics | 8 | 215000–256000 |
| Sharp ID3 | 3 | 1236000–1395000 |
| Sharp ID9 | 9 | 0–57000 |

Диапазоны не пересекаются. Сохранён исходный выбор по lcdid_adc из bootargs,
somc,lcd-id-adc и somc,dsi-index, а не выбор по номеру модели телефона.
Драйвер Sony panel/incell перенесён как исходный код и компилируется отдельно;
начальный board DT пока не включает эти панели и их полную цепочку providers.

Контрольные данные пользователя **F8132 / рабочее 4.4**:

~~~text
lcdid_adc=0x11FFEA = physical=1179626
Panel Name=4; somc,auo_synaptics_cmd_4_panel
1080x1920 @ 60 Hz
bitclk=897039360; pclk=149506560
byteclk=112129920; escclk=19200000
~~~

ADC выбирает AUO однозначно. bitclk=8*byteclk=6*pclk; значения согласованы
с 24 bpp / 4 DSI lanes. Эти частоты не назначены всем панелям и не измерены
под 4.19. Не добавлены принудительные lcdid_adc или panel_name в bootargs.
Для JDI/LGD/Sharp сохраняются их собственные PHY timings и init data.
Сохраняются ограничения 1080p60 из итогового dora override эталона;
не включается 120 Гц только потому, что базовые файлы содержат такие режимы.

Проверка данных: python3 scripts/dora/check-panels.py.

## Матрица функций

| Функция | F8131 | F8132 | Что ещё требуется в 4.19 |
| --- | --- | --- | --- |
| Принадлежность dora/MSM8996 и общая архитектура | С | С | Реальные SoC/PMIC IDs обоих аппаратов |
| Конфигурация ARM64/COMPAT/Binder/BPF/SELinux/ФС | Б, Н | Б, Н | Userspace LOS21, policy/FCM/bpfloader на обоих |
| AUO 1080p60 | С, Н | Д4.4, С, Н | Полный MDSS/SMMU/bus/clock/regulator DT и физическая загрузка |
| JDI/LGD/Sharp ID3/ID9 | С, Н | С, Н | Аппараты с каждой реально установленной панелью |
| GPU Adreno 530 / ION | Б, О, Н | Б, О, Н | GPU DT, secure firmware/heaps, ioctl/cache ABI и vendor EGL/gralloc |
| BCM4359 / Wi-Fi | Б, О, Н | Б, О, Н | Согласовать upstream PCIe DT с vendor msm_pcie API; firmware/calibration |
| Тачскрин Clearpad | С, О, Н | С, О, Н | Совместимый incell provider, I2C12 DT и PM/reset binding |
| Звук / DSP / WCD9335 | С, О, Н | С, О, Н | Полная vendor DSP/machine/SLIMbus связка и маршруты |
| Камеры | О, Н | О, Н | Sensor/CCI/ISP/EEPROM/actuator DT, drivers и vendor ABI |
| UFS / SD | Б, Н | Б, Н | Оба набора PMIC/stepping, ICE, suspend, filesystem integrity |
| USB / ADB / MTP / OTG | Б, Н | Б, Н | PHY/VBUS/role detection и физическая проверка |
| Зарядка / FG / thermal / BCL | О, Н | О, Н | Настоящие VADC/BCL и Sony policy; без отключения safety paths |
| Modem / calls / SMS / data | О, Н | О, Н | Firmware/SSR/SMD/GLINK/RMNET/IPA, RIL и регистрация в сети |
| SIM configuration | С: ss userspace | С: dsds userspace | SIM1 отдельно и оба слота/DSDS отдельно |
| Suspend/resume / 4 CPU / DVFS | О, Н | О, Н | CPR/SAW/binning/clock/PMIC variants и измерения |

Б означает наличие кода в сборочной проверке, а не привязку к работающему DT.
В частности, существование KGSL или PCI_MSM в vmlinux не доказывает probe GPU/Wi-Fi.

## Общее ядро и boot.img

Оригинальная Android-конфигурация dora перечисляет F8131/F8132 и выбирает
tone_dora_defconfig; это свидетельство общей организации исходников 4.4.
Для 4.19 пригодность одного kernel image — цель, а пригодность одного boot.img
для обоих аппаратов **не подтверждена**. Сейчас Image.gz-dtb содержит один
начальный DTB, а не полный набор исходных SoC/PMIC вариантов.

Нужно сверить на каждом устройстве boot header/size, cmdline, ramdisk,
реальные msm-id/board-id/pmic-id и механизм выбора DTB загрузчиком. Затем
собрать корректный multi-DTB набор с настоящими аппаратными overrides.
F8131/F8132 не должны подменять SoC/PMIC IDs; выбор SIM остаётся отдельной задачей.

Ни компиляция, ни эти проверки исходников не завершают универсальный порт.
До отдельного успешного теста каждой модели нельзя объявлять полную поддержку,
совместимый общий boot.img или готовность LOS21 к использованию на телефоне.
