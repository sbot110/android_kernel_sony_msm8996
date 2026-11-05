// SPDX-License-Identifier: GPL-2.0-only
/*
 * Qualcomm PMI8994 / PMI8996 SPMI Fuel Gauge & SMBCHG Telemetry Driver
 *
 * Designed for Sony Xperia X Performance (Tone Dora: F8131 / F8132, MSM8996)
 *
 * Hardware-autonomous charging safety limits:
 * - Float voltage, fast-charge current, AICL, JEITA thresholds, PMIC safety
 *   timers, battery overvoltage protection, and thermal shutdown are preserved
 *   100% as programmed in hardware / PMIC OTP / Sony S1 bootloader.
 * - This driver operates strictly as a telemetry, health, and status monitor,
 *   performing memory interface and register reads without mutating charging
 *   safety parameters.
 */

#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/iio/consumer.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/pm.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/workqueue.h>

/* PMIC REVID on USID 2 */
#define PMIC_REVID_PERPH_SUBTYPE		0x0105
#define PMIC_SUBTYPE_PMI8994			10
#define PMIC_SUBTYPE_PMI8996			19

/* Common SPMI peripheral offsets */
#define PERPH_TYPE_OFFSET			0x04
#define PERPH_SUBTYPE_OFFSET			0x05
#define INT_RT_STS_OFFSET			0x10

/* SMBCHG chgr@1000 offsets */
#define CHGR_FV_STS_OFFSET			0x0c
#define CHGR_FCC_STS_OFFSET			0x0d
#define CHGR_STS_OFFSET				0x0e
#define CHGR_CHG_TYPE_MASK			GENMASK(2, 1)
#define CHGR_CHG_TYPE_SHIFT			1
#define CHG_TYPE_NONE				0
#define CHG_TYPE_PRE				1
#define CHG_TYPE_FAST				2
#define CHG_TYPE_TAPER				3

#define CHGR_RT_CHG_INHIBIT			BIT(1)
#define CHGR_RT_BAT_TCC_REACHED			BIT(7)

/* SMBCHG bat-if@1200 INT_RT_STS bits */
#define BAT_IF_RT_HOT				BIT(0)
#define BAT_IF_RT_WARM				BIT(1)
#define BAT_IF_RT_COLD				BIT(2)
#define BAT_IF_RT_COOL				BIT(3)
#define BAT_IF_RT_BATT_OV			BIT(4)
#define BAT_IF_RT_BATT_LOW			BIT(5)
#define BAT_IF_RT_BATT_MISSING			BIT(6)

/* SMBCHG usb-chgpth@1300 offsets & bits */
#define USB_ICL_STS_1_OFFSET			0x09
#define USB_ICL_STS_MASK			GENMASK(4, 0)
#define USB_APSD_DG_STS_OFFSET			0x0a
#define APSD_STS_SDP				BIT(0)
#define APSD_STS_CDP				BIT(1)
#define APSD_STS_DCP				BIT(2)
#define APSD_STS_TIMEOUT			BIT(3)
#define APSD_STS_HVDCP				BIT(4)

#define USB_RT_USBIN_UV				BIT(0)
#define USB_RT_USBIN_OV				BIT(1)
#define USB_RT_USBIN_SRC_DET			BIT(2)

/* SMBCHG chgr-misc@1600 INT_RT_STS bits */
#define MISC_RT_TEMP_SHUTDOWN			BIT(1)

/* Fuel Gauge fg-soc@4000 offsets */
#define FG_SOC_MONOTONIC_SOC			0x09
#define FG_SOC_RT_FULL_SOC			BIT(2)
#define FG_SOC_RT_EMPTY_SOC			BIT(3)

/* Fuel Gauge fg-memif@4400 RIF (PMI8994) vs IMA (PMI8996) offsets */
#define RIF_MEM_INTF_CFG			0x40
#define RIF_MEM_INTF_CTL			0x41
#define RIF_MEM_INTF_ADDR_LSB			0x42
#define RIF_MEM_INTF_RD_DATA0			0x4c

#define IMA_MEM_INTF_CFG			0x50
#define IMA_MEM_INTF_CTL			0x51
#define IMA_MEM_INTF_IMA_CFG			0x52
#define IMA_MEM_INTF_OPR_STS			0x54
#define IMA_MEM_INTF_ERR_STS			0x5f
#define IMA_MEM_INTF_ADDR_LSB			0x61
#define IMA_MEM_INTF_RD_DATA0			0x67

#define MEM_INTF_ACCESS_REQ			BIT(7)
#define MEM_INTF_IACS_SLCT			BIT(5)
#define MEM_INTF_WR_EN				BIT(7)
#define MEM_INTF_BURST_EN			BIT(6)

#define IMA_OPR_STS_READY			BIT(0)
#define RIF_RT_MEM_AVAIL			BIT(0)

/* Fuel Gauge SRAM addresses & byte offsets (Sony 4.4 qpnp-fg.c verified) */
#define FG_SRAM_BATT_TEMP_ADDR			0x550
#define FG_SRAM_BATT_TEMP_OFF			2

#define FG_SRAM_OCV_ADDR			0x588
#define FG_SRAM_OCV_OFF				3

#define FG_SRAM_VBAT_ADDR			0x5cc
#define FG_SRAM_VBAT_OFF			1

#define FG_SRAM_IBAT_ADDR			0x5cc
#define FG_SRAM_IBAT_OFF			3

#define FG_SRAM_CC_ADDR				0x570
#define FG_SRAM_CC_OFF				0

#define FG_SRAM_CYCLE_ADDR			0x5e8
#define FG_SRAM_CYCLE_OFF			0

/* Conversion multipliers per Qualcomm FG specification */
#define FG_LSB_16B_NUMRTR			976563ULL
#define FG_LSB_16B_DENMTR			1000ULL
#define FG_TEMP_LSB_16B				625
#define FG_DECIKELVIN				2730
#define FG_SRAM_CACHE_MS			1000

struct pmi8994_sram_cache {
	bool valid;
	unsigned long updated_jiffies;
	int vbat_uv;
	int ibat_ua;
	int temp_decidegc;
	int ocv_uv;
	int cycle_count;
	int charge_counter_uah;
	bool charge_counter_valid;
};

struct pmi8994_irq_desc {
	const char *name;
	int irq;
	bool wake_enabled;
};

struct pmi8994_fg_chg {
	struct device *dev;
	struct regmap *regmap;
	struct power_supply *batt_psy;
	struct power_supply *usb_psy;
	struct iio_channel *usbin_chan;
	struct delayed_work notify_work;
	struct mutex sram_lock;
	struct pmi8994_sram_cache cache;

	u32 chgr_base;
	u32 bat_if_base;
	u32 usb_base;
	u32 misc_base;
	u32 fg_soc_base;
	u32 fg_batt_base;
	u32 fg_memif_base;

	u8 pmic_subtype;
	bool is_pmi8996_ima;

	u32 design_capacity_uah;
	u32 design_vmax_uv;
	u32 design_vmin_uv;

	struct pmi8994_irq_desc irqs[10];
	size_t num_irqs;
};

static int pmi8994_read_u8(struct pmi8994_fg_chg *chip, u32 reg, u8 *val)
{
	unsigned int temp;
	int ret;

	ret = regmap_read(chip->regmap, reg, &temp);
	if (ret)
		return ret;

	*val = (u8)temp;
	return 0;
}

static int pmi8994_ima_wait_ready(struct pmi8994_fg_chg *chip)
{
	u8 sts = 0;
	int i, ret;

	for (i = 0; i < 20; i++) {
		ret = pmi8994_read_u8(chip,
				      chip->fg_memif_base + IMA_MEM_INTF_OPR_STS,
				      &sts);
		if (ret)
			return ret;
		if (sts & IMA_OPR_STS_READY)
			return 0;
		usleep_range(200, 400);
	}
	return -ETIMEDOUT;
}

static int pmi8994_rif_wait_avail(struct pmi8994_fg_chg *chip)
{
	u8 rt = 0;
	int i, ret;

	for (i = 0; i < 20; i++) {
		ret = pmi8994_read_u8(chip,
				      chip->fg_memif_base + INT_RT_STS_OFFSET,
				      &rt);
		if (ret)
			return ret;
		if (rt & RIF_RT_MEM_AVAIL)
			return 0;
		usleep_range(500, 800);
	}
	return -ETIMEDOUT;
}

/*
 * Read len bytes from Fuel Gauge SRAM starting at word-aligned addr + byte offset.
 * Fully supports multi-word reads crossing 32-bit word boundaries (e.g. IBAT at 0x5cc offset 3).
 * When crossing words, explicitly advances cur_addr += 4 and reprograms the address registers.
 */
static int pmi8994_fg_sram_read(struct pmi8994_fg_chg *chip, u16 addr,
				u8 offset, u8 *dst, size_t len)
{
	u32 cfg_reg, ctl_reg, lsb_reg, rd0_reg;
	u16 cur_addr = addr + (offset / 4) * 4;
	u8 cur_off = offset % 4;
	u8 addr_le[2];
	size_t rem = len;
	int ret;

	if (!len)
		return -EINVAL;

	if (chip->is_pmi8996_ima) {
		cfg_reg = chip->fg_memif_base + IMA_MEM_INTF_CFG;
		ctl_reg = chip->fg_memif_base + IMA_MEM_INTF_CTL;
		lsb_reg = chip->fg_memif_base + IMA_MEM_INTF_ADDR_LSB;
		rd0_reg = chip->fg_memif_base + IMA_MEM_INTF_RD_DATA0;

		ret = regmap_update_bits(chip->regmap, cfg_reg,
					 MEM_INTF_ACCESS_REQ | MEM_INTF_IACS_SLCT,
					 MEM_INTF_ACCESS_REQ | MEM_INTF_IACS_SLCT);
		if (ret)
			return ret;

		ret = regmap_update_bits(chip->regmap, ctl_reg,
					 MEM_INTF_BURST_EN | MEM_INTF_WR_EN, 0);
		if (ret)
			goto out_release;

		ret = pmi8994_ima_wait_ready(chip);
		if (ret)
			goto out_release;

		addr_le[0] = cur_addr & 0xff;
		addr_le[1] = (cur_addr >> 8) & 0xff;
		ret = regmap_bulk_write(chip->regmap, lsb_reg, addr_le, 2);
		if (ret)
			goto out_release;

		ret = pmi8994_ima_wait_ready(chip);
		if (ret)
			goto out_release;

		while (rem > 0) {
			size_t chunk = min_t(size_t, rem, (size_t)(4 - cur_off));

			ret = regmap_bulk_read(chip->regmap, rd0_reg + cur_off,
					       dst, chunk);
			if (ret)
				goto out_release;

			dst += chunk;
			rem -= chunk;

			if (rem > 0) {
				cur_addr += 4;
				cur_off = 0;
				addr_le[0] = cur_addr & 0xff;
				addr_le[1] = (cur_addr >> 8) & 0xff;
				ret = regmap_bulk_write(chip->regmap, lsb_reg, addr_le, 2);
				if (ret)
					goto out_release;
				ret = pmi8994_ima_wait_ready(chip);
				if (ret)
					goto out_release;
			}
		}
	} else {
		cfg_reg = chip->fg_memif_base + RIF_MEM_INTF_CFG;
		ctl_reg = chip->fg_memif_base + RIF_MEM_INTF_CTL;
		lsb_reg = chip->fg_memif_base + RIF_MEM_INTF_ADDR_LSB;
		rd0_reg = chip->fg_memif_base + RIF_MEM_INTF_RD_DATA0;

		ret = regmap_update_bits(chip->regmap, cfg_reg,
					 MEM_INTF_ACCESS_REQ | MEM_INTF_IACS_SLCT,
					 MEM_INTF_ACCESS_REQ);
		if (ret)
			return ret;

		ret = pmi8994_rif_wait_avail(chip);
		if (ret)
			goto out_release;

		ret = regmap_update_bits(chip->regmap, ctl_reg,
					 MEM_INTF_BURST_EN | MEM_INTF_WR_EN, 0);
		if (ret)
			goto out_release;

		addr_le[0] = cur_addr & 0xff;
		addr_le[1] = (cur_addr >> 8) & 0xff;
		ret = regmap_bulk_write(chip->regmap, lsb_reg, addr_le, 2);
		if (ret)
			goto out_release;

		while (rem > 0) {
			size_t chunk = min_t(size_t, rem, (size_t)(4 - cur_off));

			ret = regmap_bulk_read(chip->regmap, rd0_reg + cur_off,
					       dst, chunk);
			if (ret)
				goto out_release;

			dst += chunk;
			rem -= chunk;

			if (rem > 0) {
				cur_addr += 4;
				cur_off = 0;
				addr_le[0] = cur_addr & 0xff;
				addr_le[1] = (cur_addr >> 8) & 0xff;
				ret = regmap_bulk_write(chip->regmap, lsb_reg, addr_le, 2);
				if (ret)
					goto out_release;
				ret = pmi8994_rif_wait_avail(chip);
				if (ret)
					goto out_release;
			}
		}
	}

out_release:
	regmap_update_bits(chip->regmap, ctl_reg, MEM_INTF_BURST_EN, 0);
	regmap_update_bits(chip->regmap, cfg_reg,
			   MEM_INTF_ACCESS_REQ | MEM_INTF_IACS_SLCT, 0);
	return ret;
}

static int pmi8994_refresh_sram_cache(struct pmi8994_fg_chg *chip)
{
	u8 buf[4];
	u16 raw_u16;
	s16 raw_s16;
	int temp_dc;
	int ret;

	mutex_lock(&chip->sram_lock);
	if (chip->cache.valid &&
	    time_before(jiffies,
			chip->cache.updated_jiffies +
			msecs_to_jiffies(FG_SRAM_CACHE_MS))) {
		mutex_unlock(&chip->sram_lock);
		return 0;
	}

	/* Read VBAT (0x5cc offset 1, 2 bytes) */
	ret = pmi8994_fg_sram_read(chip, FG_SRAM_VBAT_ADDR,
				   FG_SRAM_VBAT_OFF, buf, 2);
	if (ret) {
		chip->cache.valid = false;
		goto out_unlock;
	}
	raw_u16 = buf[0] | (buf[1] << 8);
	chip->cache.vbat_uv = (int)div_u64((u64)raw_u16 * FG_LSB_16B_NUMRTR,
					   FG_LSB_16B_DENMTR);

	/* Read IBAT (0x5cc offset 3, 2 bytes - crosses into 0x5d0 offset 0) */
	ret = pmi8994_fg_sram_read(chip, FG_SRAM_IBAT_ADDR,
				   FG_SRAM_IBAT_OFF, buf, 2);
	if (ret) {
		chip->cache.valid = false;
		goto out_unlock;
	}
	raw_s16 = (s16)(buf[0] | (buf[1] << 8));
	/* Qualcomm FG reports discharge current positive; Linux power_supply expects charge positive */
	chip->cache.ibat_ua = -(int)div_s64((s64)raw_s16 * (s64)FG_LSB_16B_NUMRTR,
					    (s64)FG_LSB_16B_DENMTR);

	/* Read BATT_TEMP (0x550 offset 2, 2 bytes) */
	ret = pmi8994_fg_sram_read(chip, FG_SRAM_BATT_TEMP_ADDR,
				   FG_SRAM_BATT_TEMP_OFF, buf, 2);
	if (!ret) {
		raw_u16 = buf[0] | (buf[1] << 8);
		temp_dc = (((int)raw_u16 * FG_TEMP_LSB_16B) / 1000) - FG_DECIKELVIN;
		if (temp_dc > -600 && temp_dc < 1500)
			chip->cache.temp_decidegc = temp_dc;
	}

	/* Read OCV (0x588 offset 3, 2 bytes) */
	if (!pmi8994_fg_sram_read(chip, FG_SRAM_OCV_ADDR,
				  FG_SRAM_OCV_OFF, buf, 2)) {
		raw_u16 = buf[0] | (buf[1] << 8);
		chip->cache.ocv_uv = (int)div_u64((u64)raw_u16 * FG_LSB_16B_NUMRTR,
						  FG_LSB_16B_DENMTR);
	}

	/* Read CC_CHARGE (0x570 offset 0, 4 bytes) */
	if (!pmi8994_fg_sram_read(chip, FG_SRAM_CC_ADDR,
				  FG_SRAM_CC_OFF, buf, 4)) {
		s32 raw_cc = (s32)(buf[0] | (buf[1] << 8) | (buf[2] << 16) | (buf[3] << 24));
		chip->cache.charge_counter_uah = (int)div64_s64(
			(s64)raw_cc * (s64)chip->design_capacity_uah,
			(s64)0x10000000);
		chip->cache.charge_counter_valid = true;
	} else {
		chip->cache.charge_counter_valid = false;
	}

	/* Read CYCLE_COUNT (0x5e8 offset 0, 2 bytes) */
	if (!pmi8994_fg_sram_read(chip, FG_SRAM_CYCLE_ADDR,
				  FG_SRAM_CYCLE_OFF, buf, 2))
		chip->cache.cycle_count = buf[0] | (buf[1] << 8);

	chip->cache.valid = true;
	chip->cache.updated_jiffies = jiffies;

out_unlock:
	mutex_unlock(&chip->sram_lock);
	return ret;
}

static bool pmi8994_is_battery_present(struct pmi8994_fg_chg *chip)
{
	u8 rt = 0;

	if (pmi8994_read_u8(chip, chip->bat_if_base + INT_RT_STS_OFFSET, &rt))
		return true;

	return !(rt & BAT_IF_RT_BATT_MISSING);
}

static bool pmi8994_is_usb_present(struct pmi8994_fg_chg *chip)
{
	u8 rt = 0;

	if (pmi8994_read_u8(chip, chip->usb_base + INT_RT_STS_OFFSET, &rt))
		return false;

	if (rt & (USB_RT_USBIN_UV | USB_RT_USBIN_OV))
		return false;

	if (rt & USB_RT_USBIN_SRC_DET)
		return true;

	if (chip->usbin_chan) {
		int uv = 0;
		if (iio_read_channel_processed(chip->usbin_chan, &uv) >= 0 &&
		    uv > 4000000)
			return true;
	}
	return false;
}

static int pmi8994_get_batt_status(struct pmi8994_fg_chg *chip)
{
	u8 chgr_rt = 0, chgr_sts = 0, soc_rt = 0, chg_type;
	bool usb_present;

	if (!pmi8994_is_battery_present(chip))
		return POWER_SUPPLY_STATUS_UNKNOWN;

	usb_present = pmi8994_is_usb_present(chip);
	if (!usb_present)
		return POWER_SUPPLY_STATUS_DISCHARGING;

	pmi8994_read_u8(chip, chip->chgr_base + INT_RT_STS_OFFSET, &chgr_rt);
	pmi8994_read_u8(chip, chip->fg_soc_base + INT_RT_STS_OFFSET, &soc_rt);

	/* Full condition: TCC reached or SOC 100% full */
	if ((chgr_rt & CHGR_RT_BAT_TCC_REACHED) || (soc_rt & FG_SOC_RT_FULL_SOC))
		return POWER_SUPPLY_STATUS_FULL;

	if (pmi8994_read_u8(chip, chip->chgr_base + CHGR_STS_OFFSET, &chgr_sts))
		return POWER_SUPPLY_STATUS_UNKNOWN;

	chg_type = (chgr_sts & CHGR_CHG_TYPE_MASK) >> CHGR_CHG_TYPE_SHIFT;
	switch (chg_type) {
	case CHG_TYPE_PRE:
	case CHG_TYPE_FAST:
	case CHG_TYPE_TAPER:
		return POWER_SUPPLY_STATUS_CHARGING;
	default:
		break;
	}

	/* Inhibit condition: JEITA soft limits or top-off voltage window */
	if (chgr_rt & CHGR_RT_CHG_INHIBIT)
		return POWER_SUPPLY_STATUS_NOT_CHARGING;

	return POWER_SUPPLY_STATUS_NOT_CHARGING;
}

static int pmi8994_get_batt_health(struct pmi8994_fg_chg *chip)
{
	u8 bat_rt = 0, misc_rt = 0;

	if (pmi8994_read_u8(chip, chip->misc_base + INT_RT_STS_OFFSET, &misc_rt) == 0 &&
	    (misc_rt & MISC_RT_TEMP_SHUTDOWN))
		return POWER_SUPPLY_HEALTH_OVERHEAT;

	if (pmi8994_read_u8(chip, chip->bat_if_base + INT_RT_STS_OFFSET, &bat_rt))
		return POWER_SUPPLY_HEALTH_UNKNOWN;

	if (bat_rt & BAT_IF_RT_BATT_MISSING)
		return POWER_SUPPLY_HEALTH_DEAD;
	if (bat_rt & BAT_IF_RT_BATT_OV)
		return POWER_SUPPLY_HEALTH_OVERVOLTAGE;
	if (bat_rt & BAT_IF_RT_HOT)
		return POWER_SUPPLY_HEALTH_OVERHEAT;
	if (bat_rt & BAT_IF_RT_COLD)
		return POWER_SUPPLY_HEALTH_COLD;
	if (bat_rt & BAT_IF_RT_WARM)
		return POWER_SUPPLY_HEALTH_WARM;
	if (bat_rt & BAT_IF_RT_COOL)
		return POWER_SUPPLY_HEALTH_COOL;

	return POWER_SUPPLY_HEALTH_GOOD;
}

static enum power_supply_property pmi8994_batt_props[] = {
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_HEALTH,
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_TECHNOLOGY,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_VOLTAGE_OCV,
	POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN,
	POWER_SUPPLY_PROP_VOLTAGE_MIN_DESIGN,
	POWER_SUPPLY_PROP_CURRENT_NOW,
	POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN,
	POWER_SUPPLY_PROP_CHARGE_COUNTER,
	POWER_SUPPLY_PROP_TEMP,
	POWER_SUPPLY_PROP_CYCLE_COUNT,
	POWER_SUPPLY_PROP_CHARGE_TYPE,
};

static int pmi8994_batt_get_prop(struct power_supply *psy,
				 enum power_supply_property psp,
				 union power_supply_propval *val)
{
	struct pmi8994_fg_chg *chip = power_supply_get_drvdata(psy);
	u8 reg = 0;
	int ret;

	switch (psp) {
	case POWER_SUPPLY_PROP_STATUS:
		val->intval = pmi8994_get_batt_status(chip);
		return 0;
	case POWER_SUPPLY_PROP_HEALTH:
		val->intval = pmi8994_get_batt_health(chip);
		return 0;
	case POWER_SUPPLY_PROP_PRESENT:
		val->intval = pmi8994_is_battery_present(chip) ? 1 : 0;
		return 0;
	case POWER_SUPPLY_PROP_TECHNOLOGY:
		val->intval = POWER_SUPPLY_TECHNOLOGY_LION;
		return 0;
	case POWER_SUPPLY_PROP_CAPACITY:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_read_u8(chip,
				      chip->fg_soc_base + FG_SOC_MONOTONIC_SOC,
				      &reg);
		if (ret)
			return ret;
		val->intval = clamp_t(int, DIV_ROUND_CLOSEST((int)reg * 100, 255),
				      0, 100);
		return 0;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_refresh_sram_cache(chip);
		if (ret && !chip->cache.valid)
			return ret;
		val->intval = chip->cache.vbat_uv;
		return 0;
	case POWER_SUPPLY_PROP_VOLTAGE_OCV:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_refresh_sram_cache(chip);
		if (ret && !chip->cache.valid)
			return ret;
		val->intval = chip->cache.ocv_uv ? chip->cache.ocv_uv :
						   chip->cache.vbat_uv;
		return 0;
	case POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN:
		val->intval = chip->design_vmax_uv;
		return 0;
	case POWER_SUPPLY_PROP_VOLTAGE_MIN_DESIGN:
		val->intval = chip->design_vmin_uv;
		return 0;
	case POWER_SUPPLY_PROP_CURRENT_NOW:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_refresh_sram_cache(chip);
		if (ret && !chip->cache.valid)
			return ret;
		val->intval = chip->cache.ibat_ua;
		return 0;
	case POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN:
		val->intval = chip->design_capacity_uah;
		return 0;
	case POWER_SUPPLY_PROP_CHARGE_COUNTER:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_refresh_sram_cache(chip);
		if (ret && !chip->cache.valid)
			return ret;
		if (!chip->cache.charge_counter_valid)
			return -ENODATA;
		val->intval = chip->cache.charge_counter_uah;
		return 0;
	case POWER_SUPPLY_PROP_TEMP:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_refresh_sram_cache(chip);
		if (ret && !chip->cache.valid)
			return ret;
		val->intval = chip->cache.temp_decidegc;
		return 0;
	case POWER_SUPPLY_PROP_CYCLE_COUNT:
		if (!pmi8994_is_battery_present(chip))
			return -ENODATA;
		ret = pmi8994_refresh_sram_cache(chip);
		if (ret && !chip->cache.valid)
			return ret;
		val->intval = chip->cache.cycle_count;
		return 0;
	case POWER_SUPPLY_PROP_CHARGE_TYPE:
		ret = pmi8994_read_u8(chip, chip->chgr_base + CHGR_STS_OFFSET, &reg);
		if (ret)
			return ret;
		switch ((reg & CHGR_CHG_TYPE_MASK) >> CHGR_CHG_TYPE_SHIFT) {
		case CHG_TYPE_PRE:
			val->intval = POWER_SUPPLY_CHARGE_TYPE_TRICKLE;
			break;
		case CHG_TYPE_FAST:
		case CHG_TYPE_TAPER:
			val->intval = POWER_SUPPLY_CHARGE_TYPE_FAST;
			break;
		default:
			val->intval = POWER_SUPPLY_CHARGE_TYPE_NONE;
			break;
		}
		return 0;
	default:
		return -EINVAL;
	}
}

static enum power_supply_usb_type pmi8994_usb_types[] = {
	POWER_SUPPLY_USB_TYPE_UNKNOWN,
	POWER_SUPPLY_USB_TYPE_SDP,
	POWER_SUPPLY_USB_TYPE_CDP,
	POWER_SUPPLY_USB_TYPE_DCP,
};

static enum power_supply_property pmi8994_usb_props[] = {
	POWER_SUPPLY_PROP_ONLINE,
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_USB_TYPE,
	POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_CURRENT_MAX,
};

static int pmi8994_usb_get_prop(struct power_supply *psy,
				enum power_supply_property psp,
				union power_supply_propval *val)
{
	struct pmi8994_fg_chg *chip = power_supply_get_drvdata(psy);
	u8 reg = 0;
	int ret, uv = 0;

	switch (psp) {
	case POWER_SUPPLY_PROP_ONLINE:
	case POWER_SUPPLY_PROP_PRESENT:
		val->intval = pmi8994_is_usb_present(chip) ? 1 : 0;
		return 0;
	case POWER_SUPPLY_PROP_USB_TYPE:
		ret = pmi8994_read_u8(chip,
				      chip->usb_base + USB_APSD_DG_STS_OFFSET,
				      &reg);
		if (ret || !pmi8994_is_usb_present(chip)) {
			val->intval = POWER_SUPPLY_USB_TYPE_UNKNOWN;
			return 0;
		}
		if (reg & (APSD_STS_DCP | APSD_STS_HVDCP))
			val->intval = POWER_SUPPLY_USB_TYPE_DCP;
		else if (reg & APSD_STS_CDP)
			val->intval = POWER_SUPPLY_USB_TYPE_CDP;
		else if (reg & APSD_STS_SDP)
			val->intval = POWER_SUPPLY_USB_TYPE_SDP;
		else
			val->intval = POWER_SUPPLY_USB_TYPE_UNKNOWN;
		return 0;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		if (!pmi8994_is_usb_present(chip)) {
			val->intval = 0;
			return 0;
		}
		if (chip->usbin_chan &&
		    iio_read_channel_processed(chip->usbin_chan, &uv) >= 0) {
			val->intval = uv;
			return 0;
		}
		val->intval = 5000000;
		return 0;
	case POWER_SUPPLY_PROP_CURRENT_MAX:
		if (!pmi8994_is_usb_present(chip)) {
			val->intval = 0;
			return 0;
		}
		ret = pmi8994_read_u8(chip,
				      chip->usb_base + USB_ICL_STS_1_OFFSET,
				      &reg);
		if (ret) {
			val->intval = 500000;
			return 0;
		}
		/* SMBCHG ICL code: 0x00=100mA, 0x01=150mA, ..., 0x18=2500mA */
		val->intval = (int)(reg & USB_ICL_STS_MASK) * 100000;
		if (val->intval < 100000)
			val->intval = 500000;
		return 0;
	default:
		return -EINVAL;
	}
}

static const struct power_supply_desc pmi8994_batt_psy_desc = {
	.name = "battery",
	.type = POWER_SUPPLY_TYPE_BATTERY,
	.properties = pmi8994_batt_props,
	.num_properties = ARRAY_SIZE(pmi8994_batt_props),
	.get_property = pmi8994_batt_get_prop,
};

static const struct power_supply_desc pmi8994_usb_psy_desc = {
	.name = "usb",
	.type = POWER_SUPPLY_TYPE_USB,
	.usb_types = pmi8994_usb_types,
	.num_usb_types = ARRAY_SIZE(pmi8994_usb_types),
	.properties = pmi8994_usb_props,
	.num_properties = ARRAY_SIZE(pmi8994_usb_props),
	.get_property = pmi8994_usb_get_prop,
};

static void pmi8994_notify_work_fn(struct work_struct *work)
{
	struct pmi8994_fg_chg *chip =
		container_of(work, struct pmi8994_fg_chg, notify_work.work);

	pmi8994_refresh_sram_cache(chip);
	if (chip->batt_psy)
		power_supply_changed(chip->batt_psy);
	if (chip->usb_psy)
		power_supply_changed(chip->usb_psy);
}

static irqreturn_t pmi8994_fg_chg_isr(int irq, void *data)
{
	struct pmi8994_fg_chg *chip = data;

	mod_delayed_work(system_wq, &chip->notify_work, msecs_to_jiffies(50));
	return IRQ_HANDLED;
}

static int pmi8994_read_reg_base(struct platform_device *pdev,
				 const char *name, u32 *base)
{
	int idx;

	idx = of_property_match_string(pdev->dev.of_node, "reg-names", name);
	if (idx < 0)
		return idx;

	return of_property_read_u32_index(pdev->dev.of_node, "reg", idx, base);
}

static void pmi8994_disable_wake_irqs(struct pmi8994_fg_chg *chip)
{
	size_t i;

	for (i = 0; i < chip->num_irqs; i++) {
		if (chip->irqs[i].wake_enabled && chip->irqs[i].irq > 0) {
			disable_irq_wake(chip->irqs[i].irq);
			chip->irqs[i].wake_enabled = false;
		}
	}
}

static void pmi8994_cancel_work(void *data)
{
	struct pmi8994_fg_chg *chip = data;

	cancel_delayed_work_sync(&chip->notify_work);
	pmi8994_disable_wake_irqs(chip);
}

static int pmi8994_fg_chg_probe(struct platform_device *pdev)
{
	static const char * const irq_names[] = {
		"usbin-src-det",
		"chg-p2f-thr",
		"chg-tcc-thr",
		"batt-hot",
		"batt-warm",
		"batt-cold",
		"batt-cool",
		"delta-soc",
		"full-soc",
		"empty-soc",
	};
	struct device *dev = &pdev->dev;
	struct power_supply_config psy_cfg = {};
	struct pmi8994_fg_chg *chip;
	u8 chgr_type = 0, fg_type = 0, memif_sub = 0;
	size_t i;
	int ret;

	chip = devm_kzalloc(dev, sizeof(*chip), GFP_KERNEL);
	if (!chip)
		return -ENOMEM;

	chip->dev = dev;
	mutex_init(&chip->sram_lock);
	INIT_DELAYED_WORK(&chip->notify_work, pmi8994_notify_work_fn);
	platform_set_drvdata(pdev, chip);

	chip->regmap = dev_get_regmap(dev->parent, NULL);
	if (!chip->regmap)
		return dev_err_probe(dev, -ENODEV, "parent SPMI regmap missing\n");

	ret = pmi8994_read_reg_base(pdev, "chgr", &chip->chgr_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing chgr reg base\n");

	ret = pmi8994_read_reg_base(pdev, "bat-if", &chip->bat_if_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing bat-if reg base\n");

	ret = pmi8994_read_reg_base(pdev, "usb-chgpth", &chip->usb_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing usb-chgpth reg base\n");

	ret = pmi8994_read_reg_base(pdev, "chgr-misc", &chip->misc_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing chgr-misc reg base\n");

	ret = pmi8994_read_reg_base(pdev, "fg-soc", &chip->fg_soc_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing fg-soc reg base\n");

	ret = pmi8994_read_reg_base(pdev, "fg-batt", &chip->fg_batt_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing fg-batt reg base\n");

	ret = pmi8994_read_reg_base(pdev, "fg-memif", &chip->fg_memif_base);
	if (ret)
		return dev_err_probe(dev, ret, "missing fg-memif reg base\n");

	/* Verify real SPMI hardware peripherals are present on USID 2 */
	ret = pmi8994_read_u8(chip, PMIC_REVID_PERPH_SUBTYPE, &chip->pmic_subtype);
	if (ret)
		return dev_err_probe(dev, ret, "failed to read PMIC REVID subtype\n");

	ret = pmi8994_read_u8(chip, chip->chgr_base + PERPH_TYPE_OFFSET, &chgr_type);
	if (ret || chgr_type == 0 || chgr_type == 0xff)
		return dev_err_probe(dev, ret ? ret : -ENODEV,
				     "invalid SMBCHG peripheral type 0x%02x\n",
				     chgr_type);

	ret = pmi8994_read_u8(chip, chip->fg_soc_base + PERPH_TYPE_OFFSET, &fg_type);
	if (ret || fg_type == 0 || fg_type == 0xff)
		return dev_err_probe(dev, ret ? ret : -ENODEV,
				     "invalid FG_SOC peripheral type 0x%02x\n",
				     fg_type);

	pmi8994_read_u8(chip, chip->fg_memif_base + PERPH_SUBTYPE_OFFSET, &memif_sub);
	chip->is_pmi8996_ima = (chip->pmic_subtype == PMIC_SUBTYPE_PMI8996) ||
			       of_device_is_compatible(dev->of_node,
						       "qcom,pmi8996-smbchg-fg");

	/* Default Sony Dora LIP1621ERPC nominal design values (overridable via DT) */
	chip->design_capacity_uah = 2700000;
	chip->design_vmax_uv = 4350000;
	chip->design_vmin_uv = 3400000;
	of_property_read_u32(dev->of_node, "qcom,design-capacity-uah",
			     &chip->design_capacity_uah);
	of_property_read_u32(dev->of_node, "qcom,design-vmax-uv",
			     &chip->design_vmax_uv);
	of_property_read_u32(dev->of_node, "qcom,design-vmin-uv",
			     &chip->design_vmin_uv);

	chip->usbin_chan = devm_iio_channel_get(dev, "usbin");
	if (IS_ERR(chip->usbin_chan)) {
		ret = PTR_ERR(chip->usbin_chan);
		if (ret == -EPROBE_DEFER)
			return ret;
		chip->usbin_chan = NULL;
	}

	device_init_wakeup(dev, true);
	ret = devm_add_action_or_reset(dev, pmi8994_cancel_work, chip);
	if (ret)
		return ret;

	/* Prime initial SRAM telemetry snapshot */
	pmi8994_refresh_sram_cache(chip);

	psy_cfg.drv_data = chip;
	psy_cfg.of_node = dev->of_node;

	chip->batt_psy = devm_power_supply_register(dev, &pmi8994_batt_psy_desc,
						    &psy_cfg);
	if (IS_ERR(chip->batt_psy))
		return dev_err_probe(dev, PTR_ERR(chip->batt_psy),
				     "failed to register battery power_supply\n");

	chip->usb_psy = devm_power_supply_register(dev, &pmi8994_usb_psy_desc,
						   &psy_cfg);
	if (IS_ERR(chip->usb_psy))
		return dev_err_probe(dev, PTR_ERR(chip->usb_psy),
				     "failed to register usb power_supply\n");

	chip->num_irqs = ARRAY_SIZE(irq_names);
	for (i = 0; i < chip->num_irqs; i++) {
		int irq = platform_get_irq_byname_optional(pdev, irq_names[i]);

		chip->irqs[i].name = irq_names[i];
		chip->irqs[i].irq = irq;
		chip->irqs[i].wake_enabled = false;

		if (irq <= 0)
			continue;

		ret = devm_request_threaded_irq(dev, irq, NULL,
						pmi8994_fg_chg_isr,
						IRQF_ONESHOT, irq_names[i],
						chip);
		if (ret) {
			chip->irqs[i].irq = 0;
			continue;
		}

		ret = enable_irq_wake(irq);
		if (ret == 0)
			chip->irqs[i].wake_enabled = true;
	}

	dev_info(dev,
		 "Qualcomm %s SMBCHG+FG ready (subtype=%u, memif=0x%02x, %s)\n",
		 chip->is_pmi8996_ima ? "PMI8996" : "PMI8994",
		 chip->pmic_subtype, memif_sub,
		 chip->is_pmi8996_ima ? "IMA" : "RIF");

	return 0;
}

static int pmi8994_fg_chg_remove(struct platform_device *pdev)
{
	struct pmi8994_fg_chg *chip = platform_get_drvdata(pdev);

	cancel_delayed_work_sync(&chip->notify_work);
	pmi8994_disable_wake_irqs(chip);
	return 0;
}

static void pmi8994_fg_chg_shutdown(struct platform_device *pdev)
{
	struct pmi8994_fg_chg *chip = platform_get_drvdata(pdev);

	cancel_delayed_work_sync(&chip->notify_work);
	pmi8994_disable_wake_irqs(chip);
}

static int __maybe_unused pmi8994_fg_chg_suspend(struct device *dev)
{
	struct pmi8994_fg_chg *chip = dev_get_drvdata(dev);

	cancel_delayed_work_sync(&chip->notify_work);
	return 0;
}

static int __maybe_unused pmi8994_fg_chg_resume(struct device *dev)
{
	struct pmi8994_fg_chg *chip = dev_get_drvdata(dev);

	pmi8994_refresh_sram_cache(chip);
	mod_delayed_work(system_wq, &chip->notify_work, msecs_to_jiffies(100));
	return 0;
}

static SIMPLE_DEV_PM_OPS(pmi8994_fg_chg_pm_ops,
			 pmi8994_fg_chg_suspend,
			 pmi8994_fg_chg_resume);

static const struct of_device_id pmi8994_fg_chg_of_match[] = {
	{ .compatible = "qcom,pmi8994-smbchg-fg" },
	{ .compatible = "qcom,pmi8996-smbchg-fg" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, pmi8994_fg_chg_of_match);

static struct platform_driver pmi8994_fg_chg_driver = {
	.driver = {
		.name = "qcom-pmi8994-fg-chg",
		.of_match_table = pmi8994_fg_chg_of_match,
		.pm = &pmi8994_fg_chg_pm_ops,
	},
	.probe = pmi8994_fg_chg_probe,
	.remove = pmi8994_fg_chg_remove,
	.shutdown = pmi8994_fg_chg_shutdown,
};
module_platform_driver(pmi8994_fg_chg_driver);

MODULE_AUTHOR("Sony Xperia Tone Porting Team");
MODULE_DESCRIPTION("Qualcomm PMI8994 / PMI8996 Fuel Gauge & Charger Telemetry Driver");
MODULE_LICENSE("GPL v2");
