# Stage G.2 Boot Readiness Decision

## 1. Executive Verdict
**STATUS: STAGE G.2 BUILD VERIFIED — BOOT READINESS PENDING**

The Linux 5.15.220 kernel tree has been verified to build completely without errors or warnings under strict `KCFLAGS="-Werror"`. All critical hardware safety mechanisms—including PMI8994 4.35V clamp, JEITA thermal trip-points, AICL USB current limiting, and Kryo cluster-accurate L2 ACD sequencing—have been audited and confirmed.

However, physical flashing (`fastboot flash boot`) remains **strictly prohibited** until volatile live boot testing is conducted.

## 2. Mandatory Bring-Up Rules
1. **Volatile Boot Only:**
   Never write the unverified kernel image to the device's eMMC partition:
   ```bash
   # CORRECT - Volatile live execution:
   fastboot boot boot-dora-5.15.220-stage-g2.img

   # FORBIDDEN - Permanent partition flash:
   # fastboot flash boot boot-dora-5.15.220-stage-g2.img
   ```
2. **Hardware Pre-Conditions:**
   - Battery voltage must be >= 3.85V before boot attempt.
   - Device must not be hot or under direct sunlight.
   - USB connection should be routed through a current-limited development hub or monitored USB port.
3. **Diagnostic Extraction Procedure:**
   - If the device reboots or fails to initialize display:
     ```bash
     fastboot oem ramoops
     ```
     or boot into TWRP/recovery to inspect `/sys/fs/pstore/console-ramoops-0`.
   - Verify dmesg for:
     - `pmi8994-fg-chg` probe confirmation
     - `clk-cpu-8996` cpuhp online transitions for CPU1, CPU2, CPU3
     - Remoteproc loading of ADSP and SLPI firmware from `/vendor/firmware`

## 3. Next Steps
Upon successful volatile boot log retrieval, Stage H will focus on:
- Display panel (DSI dual-link / Novatek NT35596) bring-up in Linux 5.15 DRM
- Synaptics Touchscreen I2C input validation
- Android 14 HAL service binding
