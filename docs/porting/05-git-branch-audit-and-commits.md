# 05. Аудит целостности веток Git, история коммитов `kernel-5.5` и публикация

## 1. Подтверждение полной сохранности всех существующих веток

В строгом соответствии с разделом 2 задания проверено, что ни одна из ранее существовавших веток репозитория `sbot110/android_kernel_sony_msm8996` не изменялась, не удалялась, не переименовывалась и не подвергалась `rebase` / `reset` / `force-push`:

| Ветка | Локальный SHA (`refs/heads/*`) | Удалённый SHA (`refs/remotes/origin/*`) | Состояние |
|---|---|---|---|
| `main` | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1` | **100% идентично (не изменена)** |
| `Lineageos-21` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | `2e5470fd532fec62f20265062b34c5a0d7cd453f` | **100% идентично (не изменена)** |
| `4.9-test-not-startable` | `d8587034624fdf13432814248e4d63fa2771879c` | `d8587034624fdf13432814248e4d63fa2771879c` | **100% идентично (не изменена)** |
| `dora-full-state-20261017` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | `d686db8c1cbc1299c95e4ca15da0fcc306d88126` | **100% идентично (не изменена)** |
| `kernel-4.19-701905467803253630` | `8fca411c60281990b223dbfa25436a138cf16e5e` | `8fca411c60281990b223dbfa25436a138cf16e5e` | **100% идентично (не изменена)** |
| `wpa2-dora-debug` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | `3a9646ed5ade6c62483c46d4a85f27653bfcb191` | **100% идентично (не изменена)** |

Создана ровно одна новая изолированная ветка в отдельном Git worktree: **`kernel-5.5`** (фактическая версия ядра в `Makefile`: **Linux 5.15.220**), берущая начало от коммита эталонного рабочего ядра `0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`. Временные ветки (`work` и т.п.) и Pull Request не создавались.

---

## 2. Поэтапная история коммитов в ветке `kernel-5.5`

```text
be3e328ff build: dora: fix -Werror comment globs, build-in wireless/NFC/Venus and add S1 build script
c8aa1039f arm64: configs: add dora_defconfig for LineageOS 21/22 (Android 14/15)
9e1713fb0 platform: somc: dora: port peripherals, F8132 hybrid UIM2/SD, FPC1145, Wi-Fi/BT/NFC/Audio
6725576b4 drm/panel: dora: add Sony LCD ID multi-panel DSI driver, PMI8994 LAB/IBB and Adreno 530
60f2543df arm64: dts: qcom: dora: port MSM8996 base platform, S1 board-ids and eMMC layout
9c23b3fe6 kernel: import official Android Common Kernel 5.15.220 (android14-5.15)
```

*(Плюс завершающий коммит этапа 8 с добавлением документации `docs/porting/`).*

---

## 3. Ограничение прямого `git push` из облачной песочницы и публикация одной командой

При проверке `GIT_TERMINAL_PROMPT=0 git push --dry-run origin kernel-5.5` в изолированной облачной среде зафиксировано точное штатное ограничение отсутствия учётных данных GitHub:

```text
fatal: could not read Username for 'https://github.com': terminal prompts disabled
```

В соответствии с п. 2 задания никакие обходные ветки не подменялись, а полная ветка `kernel-5.5` со всей историей коммитов упакована в верифицированный Git-пакет **`dora-kernel-5.5.bundle`** (а также в компактный архив патчей `dora-porting-patches-5.15.220.tar.gz`).

### Команда прямой публикации только ветки `kernel-5.5` на вашей машине (WSL / Linux):

```bash
git clone https://github.com/sbot110/android_kernel_sony_msm8996.git
cd android_kernel_sony_msm8996

# Проверка целостности пакета и импорт строго одной ветки kernel-5.5
git bundle verify /path/to/dora-kernel-5.5.bundle
git fetch /path/to/dora-kernel-5.5.bundle kernel-5.5:kernel-5.5

# Публикация исключительно новой ветки kernel-5.5 без изменения default branch и остальных веток
git push -u origin kernel-5.5
```
