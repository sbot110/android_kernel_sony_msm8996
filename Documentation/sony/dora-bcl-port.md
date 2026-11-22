# Sony MSM8996 BCL source port

The optional `CONFIG_SONY_MSM8996_BCL=y` builds the real legacy BCL framework
and PMIC peripheral backend. Disable `CONFIG_MSM_BCL_PERIPHERAL_CTL` when using
it: both backends can match `qcom,msm-bcl-lmh`, while the legacy backend also
implements the older `qcom,msm-bcl` register layout used by PMI8994.
`CONFIG_QCOM_SCM`, SPMI, OF and POWER_SUPPLY are required.

Sources originate from `sbot110/android_kernel_sony_msm8996` commit
`0c295b15b6d0fb59a64e13b9e0ef2070818ef2a1`:

- `drivers/power/supply/qcom/msm_bcl.c`
- `drivers/power/supply/qcom/bcl_peripheral.c`
- `include/linux/msm_bcl.h`
- the BCL hardware event definitions from `include/trace/trace_thermal.h`

The PMIC register addresses, calibration coefficients, microvolt/microamp
conversion formulas, repeated-read consistency checks, threshold programming
and hardware variants remain in the backend. `msm_bcl_read()` is an exported
measurement function; absent providers and failed hardware reads return errors.
The original PMI8994 DT names `fg_user_adc`, `pon_spare`,
`bcl-high-ibat-int`, `bcl-low-vbat-int` and original scaling properties are
required. The backend obtains the SPMI parent regmap and has no fabricated
ADC values. DT integration must retain the actual PMIC variant for each board.

Changes needed for the 4.19 integration and confirmed source defects:

- Separate Kconfig/backend names avoid binding both legacy and newer BCL
  implementations. Trace event names have a `sony_bcl_` prefix to avoid symbol
  collisions with other thermal trace definitions.
- Use the available OF address API and preserve IRQ `-EPROBE_DEFER`.
- Check DT divisors/poll delays, power-supply registration, LMH register writes
  and SCM call results. Failed trip reads propagate their hardware error.
- Register static API/sysfs objects with a device release callback and checked
  unwind. Protect reads and threshold programming against API withdrawal.
- Initialize locks/work before publishing measurement ops. Probe failure and
  remove stop IRQs, drain poll and notification work, unregister the notification
  supply, and withdraw framework ops before the device memory is released.
- Serialize owner notifications on one ordered workqueue, invoke callbacks
  outside hardware state locks, and invalidate queued notifications on disable.
  Disabling from an owner callback does not wait for that same callback.
- Balance every `power_supply_get_by_name()` reference and retry calibration
  after a failed hardware read. The notifier supply advertises no measurements;
  requests for unsupported properties return `-EINVAL`.
- When calibration changes current sensing, reprogram thresholds from the
  framework's physical-unit cache rather than treating old ADC codes as
  microvolts or microamps.

The driver is built-in and suppresses manual unbind. Threshold owners must
serialize enable/disable/replacement and keep callback context alive until
external disable returns (or their current self-disabling callback returns).
No threshold client is added by this port; the charger uses the reading API.

Validation: both ARM64 objects compile with GCC 12.2 against the 4.19 source and
an olddefconfig enabling this option. This is compile validation only. Confirm
probe, calibrated current/voltage, charger interaction, IRQ thresholds, polling,
suspend/resume and teardown on F8131 and F8132 separately. Neither SIM variant
nor a successful object build establishes the actual PMIC population or battery
measurement accuracy.
