# 05. Аудит целостности веток Git, история коммитов `kernel-5.5` и публикация

## 1. Подтверждение полной сохранности всех существующих веток

В строгом соответствии с разделом 2 задания проверено, что ни одна из 6 исходных веток репозитория `sbot110/android_kernel_sony_msm8996` не изменялась, не удалялась, не переименовывалась и не подвергалась `rebase` / `reset` / `force-push`:

| Ветка | Локальный SHA (`refs/heads/*`) | Удалённый SHA (`refs/remotes/origin/*`) | Состояние |
|---|---|---|---|
| `main` (default `HEAD`) | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | **100% идентично (не изменена)** |
| `Lineageos-21` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | **100% идентично (не изменена)** |
| `4.9-test-not-startable` | `d8587034624fdf13432814248e4d63fa2771879c` | `d8587034624fdf13432814248e4d63fa2771879c` | **100% идентично (не изменена)** |
| `dora-full-state-20261017` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | **100% идентично (не изменена)** |
| `kernel-4.19-701905467803253630` | `8fca411c60281990b223dbfa25436a138cf16e5e` | `8fca411c60281990b223dbfa25436a138cf16e5e` | **100% идентично (не изменена)** |
| `wpa2-dora-debug` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | **100% идентично (не изменена)** |

Рабочая ветка **`kernel-5.5`** (фактическая версия ядра в `Makefile`: **Linux 5.15.220**) развивается исключительно как линейный **fast-forward** потомок удалённого коммита `origin/kernel-5.5` (`54f7a7200d5e662854877e6df5099eb2c63e5e7e`), берущего начало от эталонного ядра `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`. Временные ветки (`work` и т.п.) и Pull Request не создавались.

---

## 2. Поэтапная история коммитов в ветке `kernel-5.5`

```text
<HEAD>    docs: porting: document P0 kernel audit fixes, initrd/PSI/THIS_MODULE and updated artifacts
c47e99b8d kernel: dora: fix built-in THIS_MODULE deref, initrd XZ, PSI, rmi4 wakeup and brcmfmac paths
54f7a7200 docs: porting: add Sony Dora F8131/F8132 Linux 5.15 porting, WSL build and Git audit reports
be3e328ff build: dora: fix -Werror comment globs, build-in wireless/NFC/Venus and add S1 build script
c8aa1039f arm64: configs: add dora_defconfig for LineageOS 21/22 (Android 14/15)
9e1713fb0 platform: somc: dora: port peripherals, F8132 hybrid UIM2/SD, FPC1145, Wi-Fi/BT/NFC/Audio
6725576b4 drm/panel: dora: add Sony LCD ID multi-panel DSI driver, PMI8994 LAB/IBB and Adreno 530
60f2543df arm64: dts: qcom: dora: port MSM8996 base platform, S1 board-ids and eMMC layout
9c23b3fe6 kernel: import official Android Common Kernel 5.15.220 (android14-5.15)
```

*(Все 8 коммитов ветки `kernel-5.5` являются прямыми линейными fast-forward потомками `54f7a7200d5e662854877e6df5099eb2c63e5e7e` и эталона `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`).*

---

## 3. Публикация fast-forward обновления в `origin/kernel-5.5`

Если секрет `GITHUB_TOKEN` (Fine-grained PAT с правами `Contents: Read & Write` для `sbot110/android_kernel_sony_msm8996`) задан в панели Secrets AI Studio, прямая отправка выполняется кнопкой **«Отправить kernel-5.5 в GitHub»** (`POST /api/push-branch`), которая перед `git push` проверяет `git merge-base --is-ancestor` и никогда не использует `--force`.

Для автономной публикации на локальной машине (WSL / Linux) экспортирован верифицированный инкрементальный Git-пакет **`dora-kernel-5.5.bundle`** (поверх `54f7a7200d5e662854877e6df5099eb2c63e5e7e`), а также полный архив патчей **`dora-porting-patches-5.15.220.tar.gz`**.

### Команда прямой fast-forward публикации только ветки `kernel-5.5` (WSL / Linux):

```bash
git clone --branch kernel-5.5 --single-branch https://github.com/sbot110/android_kernel_sony_msm8996.git
cd android_kernel_sony_msm8996

# Проверка целостности пакета и fast-forward импорт ветки kernel-5.5
git bundle verify /path/to/dora-kernel-5.5.bundle
git fetch /path/to/dora-kernel-5.5.bundle refs/heads/kernel-5.5:refs/heads/kernel-5.5
git checkout kernel-5.5

# Публикация исключительно ветки kernel-5.5 без изменения default branch и остальных веток
git push origin refs/heads/kernel-5.5:refs/heads/kernel-5.5
```
