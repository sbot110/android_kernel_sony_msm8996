# 05. Аудит целостности веток Git, история коммитов `kernel-5.5` и безопасная публикация

## 1. Подтверждение полной сохранности всех существующих веток

В строгом соответствии с ограничениями проекта проверено, что ни одна из остальных веток репозитория `sbot110/android_kernel_sony_msm8996` не изменялась, не удалялась, не переименовывалась и не подвергалась `rebase` / `reset` / `force-push`:

| Ветка | Локальный SHA (`refs/heads/*`) | Удалённый SHA (`refs/remotes/origin/*`) | Состояние |
|---|---|---|---|
| `main` (default `HEAD`) | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | **100% идентично (не изменена)** |
| `Lineageos-21` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | **100% идентично (не изменена)** |
| `4.9-test-not-startable` | `d8587034624fdf13432814248e4d63fa2771879c` | `d8587034624fdf13432814248e4d63fa2771879c` | **100% идентично (не изменена)** |
| `dora-full-state-20261017` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | **100% идентично (не изменена)** |
| `kernel-4.19-701905467803253630` | `8fca411c60281990b223dbfa25436a138cf16e5e` | `8fca411c60281990b223dbfa25436a138cf16e5e` | **100% идентично (не изменена)** |
| `wpa2-dora-debug` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | **100% идентично (не изменена)** |

Рабочая ветка **`kernel-5.5`** (фактическая версия ядра в `Makefile`: **Linux 5.15.220**) развивается исключительно как линейный **fast-forward** потомок удалённого коммита `origin/kernel-5.5` (`54f7a7200d5e662854877e6df5099eb2c63e5e7e`), берущего начало от эталонного ядра `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`. Новые или временные ветки и Pull Request не создавались.

---

## 2. Линейная история коммитов в ветке `kernel-5.5`

```text
<HEAD-G1> docs: porting: update boot checklist (13) and document Stage G.1 fixes (14), build verification (15) and safety audit (16)
<G1-KRN>  kernel: dora: fix Fuel Gauge charge state, SRAM boundary reads, wake IRQ lifecycle, Kryo clock error handling and Wi-Fi reprobe
d6368815b docs: porting: document Stage G power audit (11), LineageOS 21 compatibility (12) and first boot readiness (13)
a49663785 kernel: dora: fix P0 regulator mapping, Kryo OPP/ACD init, SPMI battery telemetry and LineageOS 21 HAL contracts
470fdeebc docs: porting: add full 5.15.220 build verification (09) and update Stage B/F audit reports (05,07,08,10)
e0510458c kernel: dora: fix retail earlycon SError, rmi_i2c double wake IRQ, FPC1145 standby power and capless USB/UIM2 pinconf
a87c4bc73 kernel: dora: fix FPC1145 Sony 0x45 ABI, Sharp SLEEP_OUT, sdhc1 BCR, WLED CABC, CPUFREQ_DT and brcmfmac HAL paths
f7d2d7afc docs: porting: document P0 kernel audit fixes, initrd/PSI/THIS_MODULE and updated artifacts
c47e99b8d kernel: dora: fix built-in THIS_MODULE deref, initrd XZ, PSI, rmi4 wakeup and brcmfmac paths
54f7a7200 docs: porting: add Sony Dora F8131/F8132 Linux 5.15 porting, WSL build and Git audit reports
be3e328ff build: dora: fix -Werror comment globs, build-in wireless/NFC/Venus and add S1 build script
c8aa1039f arm64: configs: add dora_defconfig for LineageOS 21/22 (Android 14/15)
9e1713fb0 platform: somc: dora: port peripherals, F8132 hybrid UIM2/SD, FPC1145, Wi-Fi/BT/NFC/Audio
6725576b4 drm/panel: dora: add Sony LCD ID multi-panel DSI driver, PMI8994 LAB/IBB and Adreno 530
60f2543df arm64: dts: qcom: dora: port MSM8996 base platform, S1 board-ids and eMMC layout
9c23b3fe6 kernel: import official Android Common Kernel 5.15.220 (android14-5.15)
0c295b15b Merge tag 'LA.UM.5.5.r1-06700-8x96.0' (Sony 4.4.302 reference)
```

Все коммиты после `54f7a7200d5e662854877e6df5099eb2c63e5e7e` упакованы в:
1. **`/exports/dora-kernel-5.5-updated.bundle`** — полный инкрементальный Git-bundle поверх `54f7a7200d5e662854877e6df5099eb2c63e5e7e`;
2. **`/exports/dora-porting-patches-5.15.220-updated.tar.gz`** — серия `git format-patch` всех коммитов после `54f7a7200d5e662854877e6df5099eb2c63e5e7e`;
3. **`/exports/dora-source-changes-5.15.220.tar.gz`** — архив всех добавленных/изменённых исходных файлов и отчётов портирования;
4. Предыдущие артефакты (`dora-kernel-5.5.bundle`, `dora-porting-patches-5.15.220.tar.gz`, `dora-compiled-dtbs-5.15.220.tar.gz`) полностью сохранены без перезаписи.

---

## 3. Безопасная fast-forward публикация только ветки `kernel-5.5` (WSL / Linux)

Ниже приведена процедура безопасной публикации без встраивания токена в URL `origin` (аутентификация выполняется штатно через **Git Credential Manager**, `gh auth setup-git` или SSH-ключ):

```bash
# 1. Клонировать только целевую ветку kernel-5.5
git clone --branch kernel-5.5 --single-branch https://github.com/sbot110/android_kernel_sony_msm8996.git
cd android_kernel_sony_msm8996

# 2. Убедиться, что базовый удалённый HEAD равен 54f7a7200d5e662854877e6df5099eb2c63e5e7e
git rev-parse HEAD

# 3. Проверить целостность обновлённого bundle
git bundle verify /path/to/dora-kernel-5.5-updated.bundle

# 4. Импортировать коммиты и выполнить строгое fast-forward слияние без новых веток
git fetch /path/to/dora-kernel-5.5-updated.bundle refs/heads/kernel-5.5
git merge --ff-only FETCH_HEAD

# 5. Проверить историю и чистоту рабочего дерева перед отправкой
git status --short
git log --oneline -7

# 6. Опубликовать исключительно ветку kernel-5.5 (без --force; вход через GCM / gh auth / SSH)
git push origin HEAD:refs/heads/kernel-5.5

# 7. Независимо подтвердить обновление удалённой ветки на GitHub
git ls-remote origin refs/heads/kernel-5.5
```
