# 08. Аудит аппаратной совместимости Sony Xperia X Performance (`F8131` и `F8132`) на Linux 5.15.220

Настоящий документ фиксирует строго проверяемый инженерный статус каждой аппаратной и системной подсистемы смартфона **Sony Xperia X Performance (`dora`: `F8131` Single-SIM и `F8132` Dual-SIM, SoC Qualcomm MSM8996)** в ветке `kernel-5.5` (**Linux 5.15.220**) для целевых систем **LineageOS 21 (Android 14)** и **LineageOS 22 (Android 15)**.

В строгом соответствии с требованиями инженерной верификации статусы разделены на **6 независимых уровней**:
1. **`SOURCE IMPLEMENTED`** — драйвер, Device Tree и Kconfig реализованы в исходном коде ветки `kernel-5.5`;
2. **`STATIC VERIFIED`** — построчно проверены контракты регистров, MMIO-окон, GPIO, регуляторов, тактирования, `ioctl`/sysfs ABI против эталонного дерева Sony 4.4.302 (`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`) и декомпилированных `.dtb`;
3. **`COMPONENT BUILD VERIFIED`** — целевые объектные модули (`.o` / `.ko`) и `.dtb` скомпилированы с `KCFLAGS="-Werror"` без ошибок и предупреждений;
4. **`FULL BUILD VERIFIED`** — полная чистая сборка всего ядра (`vmlinux`, `System.map`, `Image`, `Image.gz`, `Image.gz-dtb`, 4 `.dtb`, модули) успешно завершена с **0 ошибок и 0 предупреждений компилятора**;
5. **`BOOT TEST READY`** — техническая готовность образа `Image.gz-dtb` к первому безопасному тесту на устройстве при выполнении явно указанных условий со стороны образов userspace ROM (не означает подтверждённой загрузки);
6. **`RUNTIME VERIFIED`** — **`NOT VERIFIED` для всех подсистем**, так как в сборочной среде отсутствует физический телефон `F8131` / `F8132` и натурные логи `dmesg` / `ramoops` / `logcat` ещё не снимались.

---

## 1. Покомпонентная матрица аудита драйверов и подсистем (6 уровней верификации)

| Подсистема / Узел | Драйвер в Linux 5.15.220 | Ключевые ресурсы (MMIO / GPIO / Питание) | 1. Source | 2. Static | 3. Comp. Build | 4. Full Build | 5. Boot Test Ready | 6. Runtime | Остаточные риски и условия |
|---|---|---|---|---|---|---|---|---|---|
| **Ранняя загрузка S1, `chosen`, `ramoops`** | ARM64 boot + Sony S1 multi-DTB + `ramoops` | 4 `.dtb` (`v2/v3` × `PMI8994/96`), `ramoops` @ `0xa7f00000` (1 МБ), `RD_XZ=y`, убран `earlycon` нетактированного UART | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Требуется проверка выбора `.dtb` загрузчиком S1 и сохранности `0xa7f00000` при тёплом ребуте. |
| **SoC / SMP / Kryo CPUFreq & Idle** | `qcom-cpufreq-nvmem`, `cpufreq-dt`, `qcom-apcc-msm8996`, `qcom_spm`, PSCI | Kryo 2×1.59 ГГц + 2×2.15 ГГц, CBF, QFPROM speedbin, SAW2/SPM | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Снятие частотно-температурной телеметрии кластеров Silver/Gold под нагрузкой. |
| **Тактирование и шины** | `gcc-msm8996`, `mmcc-msm8996`, `clk-smd-rpm`, `icc-msm8996` | GCC (`=y`), MMCC (`=y`), RPM SMD XO/BB/DIV clocks, NoC Interconnect | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка отсутствия преждевременного отключения клоков в `clk_disable_unused`. |
| **PMIC, регуляторы и RPM Corners** | `qcom_smd_rpm`, `qcom-spmi-regulator`, `qcom-labibb-regulator`, `qcom-rpmpd` | PM8994 + PMI8994 / PMI8996 (`l17=2.2V`, `l27=1.0V`, `s4=1.8V`, LAB/IBB 5.5..5.7V, RPM power domains) | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Контроль токов глубокого сна (`XO shutdown` / `vddmin`) на реальном аппарате. |
| **Внутренняя память eMMC 5.1** | `sdhci_msm` (`sdhc1` @ `0x07464900`) | MMIO `0x314` + `0x800`, `GCC_SDCC1_BCR`, `RPM_SMD_XO_CLK_SRC`, `pm8994_l20` (2.95V), `pm8994_s4` (1.8V), HS200/HS400 | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка калибровки строба HS400 DLL на серийных чипах Samsung/SK Hynix eMMC. |
| **MicroSD + гибридный лоток `F8132`** | `sdhci_msm` + `mmc/core/{core,host,slot-gpio}` (`sdhc2` @ `0x074a4900`) | `GCC_SDCC2_BCR`, `cd-gpios` = GPIO 40, `uim2-gpios` = GPIO 29 (`output-low` без конфликта `output-high`) | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Натурный тест горячей замены лотка SIM2 ↔ MicroSD на `F8132`. |
| **Дисплей 5.0" 1080p60 In-Cell** | `msm` (`mdp5` + `dsi`), `panel-sony-dora-incell`, `qcom-wled` | GPIO 8 (двойной сброс), GPIO 51 (`vddio`), LAB/IBB, WLED 3 гирлянды (без `qcom,cabc`), VADC `MUX6_1_1` (`uV`) для 5 панелей | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка перехода S1 splash -> `mdp5` и таймингов на всех 5 ревизиях панелей (`AUO ID4`, `JDI ID6`, `LGD ID8`, `Sharp ID3/ID9`). |
| **Тачскрин Clearpad S3320** | `rmi4_core`, `rmi4_i2c`, `rmi4_f12`, `somc-dora-platform` | BLSP2 I2C6 (`0x2c`), IRQ GPIO 125, Reset GPIO 89, `vdd` = `pm8994_l22`, `vio` = `panel_tvdd` (GPIO 50), честный `wakeup_gesture` и одиночный `irq_wake` | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка совместной последовательности suspend/resume In-Cell панели и S3320 при `wakeup_gesture=0/1`. |
| **GPU Adreno 530** | `msm` (`adreno_a5xx`), `arm-smmu` | `a530_zap.mdt`, `adreno_smmu`, `zap_shader_region`, `/dev/dri/renderD128` | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `CONDITIONAL` | `NOT VERIFIED` | **Условие userspace:** требует открытый стек **Mesa Freedreno (`freedreno`/`turnip`)** + `drm_hwcomposer`; старые блобы KGSL (`/dev/kgsl-3d0`) несовместимы. |
| **Модем MPSS и RIL (`F8131` / `F8132`)** | `qcom_q6v5_mss`, `qrtr`, `qrtr-smd`, `qcom_rmtfs_mem`, `somc-dora-platform` | `mba.mbn` + `modem.mdt`, `pm8994_l12`, SIM1 detect GPIO 112, UIM2 mux GPIO 29 | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `CONDITIONAL` | `NOT VERIFIED` | Требуется проверка совместимости QMI RIL / `rmtfs` с mainline `qrtr` в Android 14/15. |
| **Аудио WCD9335 (Tasha) + Q6DSP** | `qcom_q6v5_pas` (`adsp_pil`), `snd-soc-apq8096`, `snd-soc-wcd9335`, `slim-qcom-ngd-ctrl` | `adsp.mdt`, SLIMbus, PM8994 GPIO 15 (`div1_mclk` 9.6 МГц), `/sys/kernel/boot_adsp/boot` | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `CONDITIONAL` | `NOT VERIFIED` | Требуется адаптация профилей микшера ALSA UCM под mainline Q6AFE/Q6ASM DAI. |
| **Wi-Fi BCM4359 PCIe** | `pcie-qcom`, `brcmfmac` + `somc-dora-platform` | PCIe0 (`14e4:43ef` / `14e4:4359`), `wlan_en` GPIO 84, `fw_bcmdhd.bin` / `fw_bcmdhd_apsta.bin`, `bcmdhd.cal` | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка парсинга Sony `bcmdhd.cal` и переключения STA ↔ SoftAP в `brcmfmac`. |
| **Bluetooth BCM4359 UART** | `hci_uart` (`BCM` serdev) | BLSP1 UART2 (`7570000`, 3 Мбод), `shutdown` GPIO 83, `dev-wake` GPIO 81, `host-wake` GPIO 82 | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка загрузки `.hcd` патча через serdev `hci0`. |
| **NFC NXP PN547 / PN548** | `nxp-nci_i2c` | BLSP2 I2C2 (`0x28`), IRQ GPIO 9, Enable PM8994 GPIO 7, FW GPIO 12 | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `CONDITIONAL` | `NOT VERIFIED` | Проверка адаптации NFC HAL под mainline NCI интерфейс вместо legacy `/dev/pn54x`. |
| **Сканер отпечатка FPC1145** | `somc-dora-platform` (`/dev/fingerprint` + sysfs) | Reset GPIO 21, IRQ GPIO 132 (`HIGH | ONESHOT | NO_AUTOEN`), `pm8994_l18` + `pm8994_s4` (вкл. по `FPC_IOCWPREPARE`), ABI `0x45` | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `BLOCKED BY QSEECOM` | `NOT VERIFIED` | **Блокер TEE:** ядерный `/dev/fingerprint` готов и не расходует ток в простое, но для связи HAL с SPI0 в TZ нужен драйвер `/dev/qseecom`. |
| **USB 2.0 Micro-B (Capless)** | `dwc3`, `dwc3-qcom`, `phy-qcom-qusb2`, `pm8994_vadc` | Peripheral mode (ADB/MTP/RNDIS ConfigFS), GPIO 24 `output-low` (антикоррозионная защита), VADC `0x39` (`usb_id_lv`) | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY (GADGET)` | `NOT VERIFIED` | Режим USB Peripheral (ADB/MTP) готов; автоматический USB Host OTG на открытом разъёме требует драйвера аналогового опроса VADC `0x39`. |
| **Зарядка АКБ и Fuel Gauge (`PMI8994` / `PMI8996`)** | Аппаратный автономный контроллер PMIC (в mainline 5.15 драйверы `smbchg`/`fg` для PMI8994 отсутствуют) | SPMI USID 2/3 (`pmi8994_charger` @ `0x1000`, `pmi8994_fg` @ `0x4000`), термисторы VADC `0x14`/`0x73`/`0x74` | `PARTIAL (THERM ONLY)` | `VERIFIED` | — | — | `GAP (NO FG TELEMETRY)` | `NOT VERIFIED` | **Реальный пробел mainline 5.15:** автономная 5В зарядка идёт через OTP PMIC/S1, но для отображения % заряда в Android Health HAL требуется портирование драйверов `pmi8994-smbchg` и `pmi8994-fg` (фиктивные драйверы запрещены и не создавались). |
| **Датчики (SLPI SSC), Кнопки, Холл, Вибро** | `qcom_q6v5_pas` (`slpi_pil`), `qcom_fastrpc`, `gpio-keys`, `pm8941-pwrkey`, `gpio-vibrator` | `slpi.mdt`, кнопки Vol+/Vol-/Cam Focus/Snapshot, Hall GPIO 78 (`SW_LID`), Vibra PMI GPIO 1 | `IMPLEMENTED` | `VERIFIED` | `VERIFIED` | `VERIFIED` | `READY` | `NOT VERIFIED` | Проверка FastRPC сессий датчиков SLPI на устройстве. |
| **Камеры (23 МП IMX300 + 13 МП IMX260/IMX258)** | Отсутствует в 5.15 | LDO `pm8994_l17` (2.2V) и `pm8994_l27` (1.0V) ограничены по спецификации Dora | `NOT IMPLEMENTED` | `LDO SAFE` | — | — | `BLOCKED` | `NOT VERIFIED` | **Архитектурный блокер:** стек Sony `mm-camera2` несовместим с V4L2 Media Controller 5.15; драйвера IMX300 в mainline нет. |

---

## 2. Раздельная оценка готовности моделей `F8131` и `F8132`

### 2.1. Модель `F8131` (Single-SIM, 32 ГБ eMMC 5.1)
- **Охват ревизий SoC и PMIC (4 `.dtb` в составе `Image.gz-dtb`):**
  1. `msm8996-sony-xperia-tone-dora.dtb` (MSM8996 v2.x/v3.1 + PMI8994)
  2. `msm8996-pmi8996-sony-xperia-tone-dora.dtb` (MSM8996 v2.x/v3.1 + PMI8996)
  3. `msm8996-v3.0-sony-xperia-tone-dora.dtb` (MSM8996 v3.0 + PMI8994)
  4. `msm8996-v3.0-pmi8996-sony-xperia-tone-dora.dtb` (MSM8996 v3.0 + PMI8996)
- **Особенности SIM / MicroSD:** Детектирование Nano-SIM1 работает по GPIO 112 (`sim1-det-gpios`), слот MicroSD на `sdhc2` (GPIO 40 `cd-gpios`) работает независимо. Линия `uim2-gpios` (GPIO 29) удерживается в чистом `output-low` (конфликт с `output-high` из `tone.dtsi` устранён) и не оказывает побочного влияния на односимочную плату.
- **Сводный статус `F8131`:** `SOURCE IMPLEMENTED` | `STATIC VERIFIED` | `COMPONENT BUILD VERIFIED` | `FULL BUILD VERIFIED`. Проверка на устройстве — `NOT VERIFIED`.

### 2.2. Модель `F8132` (Dual-SIM, 64 ГБ eMMC 5.1, гибридный лоток SIM2 / MicroSD)
- **Гибридный коммутатор `UIM2` ↔ `MicroSD`:**
  - В исходном состоянии пин GPIO 29 (`uim_detect_en`) удерживается строго в `LOW` (тракт подключён к `sdhc2` для безопасного опроса SD-карты на 3.0 В / 1.8 В без подачи напряжения SD на модемный вход UIM2).
  - При закрытии лотка (`cd-gpios` GPIO 40 = `HIGH`) `mmc_rescan()` в `drivers/mmc/core/core.c` сначала проверяет отклик SD-карты (`mmc_attach_sd() == 0`).
  - **Только если лоток закрыт и SD-карта не ответила**, ядро переводит `uim2-gpios` (GPIO 29) в `HIGH`, маршрутизируя контакты гибридного слота к модему `mss_pil` для работы второй Nano-SIM (`SIM2`). При извлечении лотка GPIO 29 немедленно сбрасывается в `LOW`.
- **Дисплей `F8132`:** Подтверждённый образец с матрицей AUO ID4 (`ADC = 0x11FFEA` / `1179626 uV`) попадает строго внутрь окна `[1087000..1231000] uV`, а экземпляры с JDI/LGD/Sharp определяются динамически по VADC `MUX6_1_1`.
- **Сводный статус `F8132`:** `SOURCE IMPLEMENTED` | `STATIC VERIFIED` | `COMPONENT BUILD VERIFIED` | `FULL BUILD VERIFIED`. Проверка на устройстве — `NOT VERIFIED`.

---

## 3. Оценка готовности для LineageOS 21 (Android 14 — основная целевая ОС)

- **Статус ядерного контракта Android 14 (`5.15.220` ACK base):** **`FULL BUILD VERIFIED`**
  - Включены и слинкованы в `vmlinux`: `CONFIG_ANDROID_BINDER_IPC=y`, `CONFIG_ANDROID_BINDERFS=y`, `CONFIG_PSI=y`, `CONFIG_CGROUPS=y`, `CONFIG_MEMCG=y`, `CONFIG_CGROUP_BPF=y`, `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y`, `CONFIG_BPF_JIT_ALWAYS_ON=y`, `CONFIG_FS_ENCRYPTION=y`, `CONFIG_FS_VERITY=y`, `CONFIG_DM_VERITY=y`, `CONFIG_DMABUF_HEAPS_SYSTEM=y`, `CONFIG_DMABUF_HEAPS_CMA=y`, `CONFIG_PID_NS=y`, `CONFIG_RSEQ=y`, `CONFIG_FHANDLE=y`, `CONFIG_BLK_DEV_INITRD=y`, `CONFIG_RD_XZ=y`, `CONFIG_OVERLAY_FS=y`, `CONFIG_SQUASHFS=y`, `CONFIG_F2FS_FS=y`.
  - Криптографические самотесты, SELinux (`CONFIG_SECURITY_SELINUX=y`), `dm-verity` и тепловые защиты не ослаблялись.
- **Условия со стороны дерева устройства LineageOS 21:**
  1. Сборка графического стека с **Mesa Freedreno (`freedreno`/`turnip`) + `drm_hwcomposer`** (под `CONFIG_DRM_MSM=y`);
  2. Наличие драйвера/моста `/dev/qseecom`, если в сборке задействуются проприетарные TEE-клиенты Sony 4.4 (`keymaster`, `gatekeeper`, `fingerprint.msm8996.so`);
  3. Портирование драйверов `pmi8994-smbchg` / `pmi8994-fg` для полноценной телеметрии заряда в Android Health AIDL.

---

## 4. Оценка готовности для LineageOS 22 (Android 15 — дополнительная целевая ОС)

- **Статус ядерного контракта Android 15 (`5.15.220`):** **`FULL BUILD VERIFIED (ТРЕБУЕТ ENABLE_BTF=1 ПРИ СБОРКЕ ROM)`**
  - Ядро Linux 5.15 входит в матрицу поддерживаемых веток Android 15 (GKI/ACK 5.15), системные вызовы `rseq`, `pidfd`, `memfd`, `binderfs`, `cgroup v2`, `fscrypt v2` и `PSI` полностью присутствуют.
  - **Ключевое дополнительное требование Android 15 (`bpfloader` / `netd` BTF):** Начиная с Android 14 QPR3 / Android 15 системные eBPF-программы сетевого стека активно используют метаданные **CO-RE BTF (`CONFIG_DEBUG_INFO_BTF=y`)**. В сборочном скрипте `scripts/dora/build-dora-5.15.sh` для этого предусмотрен флаг `ENABLE_BTF=1` (при установленном пакете `dwarves` / `pahole >= 1.21` в WSL2 Ubuntu), автоматически включающий `CONFIG_DEBUG_INFO_DWARF4=y` и `CONFIG_DEBUG_INFO_BTF=y` с контролем лимита 48 МБ образа `Image.gz-dtb` под 64 МБ раздел `boot` Sony S1.
