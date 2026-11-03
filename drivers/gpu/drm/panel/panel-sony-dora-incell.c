// SPDX-License-Identifier: GPL-2.0-only
/*
 * Sony Xperia X Performance (dora: F8131 / F8132) In-Cell Command-Mode DSI Panel Driver
 *
 * Ports Sony Linux 4.4.302 multi-panel LCD ID ADC auto-detection and DSI command
 * sequences to Linux 5.15 DRM/KMS without hardcoding a single panel variant:
 *   - Panel 4: somc,auo_synaptics_cmd_4_panel   (ADC 1087000..1231000 uV, 1080p60 ONLY)
 *   - Panel 6: somc,jdi_synaptics_cmd_6_panel   (ADC  353000..653000 uV,  1080p60)
 *   - Panel 8: somc,lgd_synaptics_cmd_8_panel   (ADC  215000..256000 uV,  1080p60)
 *   - Panel 3: somc,sharp_synaptics_cmd_3_panel (ADC 1236000..1395000 uV, 1080p60)
 *   - Panel 9: somc,sharp_synaptics_cmd_9_panel (ADC       0..57000 uV,   1080p60)
 *   - Default: somc,default_cmd_panel           (fallback 1080p60)
 *
 * Note on AUO Panel 4 (confirmed on F8132 with lcdid_adc=0x11FFEA / 1179626 uV,
 * 1179234 uV, 1180606 uV): problematic 1080p120 mode remains intentionally omitted
 * to preserve verified 1080p60 stability (pclk=149506560, bitclk=897039360).
 */

#include <linux/backlight.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/iio/consumer.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>

#include <video/mipi_display.h>

#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>

#define DORA_DEFAULT_ADC_UV 1505000UL

static unsigned long somc_lcdid_adc_uv = DORA_DEFAULT_ADC_UV;
static bool somc_lcdid_from_cmdline;

static int __init somc_lcdid_adc_setup(char *str)
{
	unsigned long val;

	if (!str || !*str)
		return 0;

	if (!kstrtoul(str, 0, &val)) {
		somc_lcdid_adc_uv = val;
		somc_lcdid_from_cmdline = true;
	}
	return 1;
}
__setup("lcdid_adc=", somc_lcdid_adc_setup);

struct somc_dsi_cmd {
	u8 dtype;
	u8 wait_ms;
	u16 len;
	const u8 *payload;
};

#define SOMC_CMD(dt, wait, ...) \
	{ \
		.dtype = (dt), \
		.wait_ms = (wait), \
		.len = sizeof((const u8[]){ __VA_ARGS__ }), \
		.payload = (const u8[]){ __VA_ARGS__ }, \
	}

struct somc_dora_panel_desc {
	const char *panel_name;
	const char *compatible_name;
	u32 adc_min_uv;
	u32 adc_max_uv;
	u32 lab_uv;
	u32 ibb_uv;
	bool hybrid_touch_avdd;
	const struct somc_dsi_cmd *on_cmds;
	size_t num_on_cmds;
	const struct somc_dsi_cmd *off_cmds;
	size_t num_off_cmds;
};

/* Panel 4: AUO + Synaptics Full In-Cell (confirmed F8132 0x11FFEA = 1179626 uV) */
static const struct somc_dsi_cmd auo_id4_on_cmds[] = {
	SOMC_CMD(0x29, 0, 0xB0, 0x04),
	SOMC_CMD(0x29, 0, 0xD6, 0x01),
	SOMC_CMD(0x29, 0, 0xC1,
		 0x84, 0x00, 0x00, 0xFF, 0x47, 0x99, 0x80, 0x39,
		 0xEB, 0xFF, 0xCF, 0x9A, 0x73, 0x8D, 0xFD, 0xBF,
		 0xD6, 0x31, 0x2F, 0x89, 0xF1, 0x3F, 0x00, 0x00,
		 0x40, 0x22, 0x82, 0x03, 0x08, 0x00, 0x01),
	SOMC_CMD(0x15, 0, 0x35, 0x00),
	SOMC_CMD(0x05, 120, 0x11),
	SOMC_CMD(0x29, 0, 0xC7,
		 0x0F, 0x1F, 0x29, 0x34, 0x41, 0x4D, 0x55, 0x61,
		 0x42, 0x49, 0x54, 0x60, 0x67, 0x6D, 0x79, 0x0B,
		 0x1F, 0x29, 0x34, 0x41, 0x4D, 0x55, 0x61, 0x42,
		 0x49, 0x54, 0x60, 0x67, 0x6D, 0x73),
	SOMC_CMD(0x29, 0, 0xC8,
		 0x00, 0x00, 0x00, 0x00, 0x00, 0xFC, 0x00, 0x00,
		 0x00, 0x00, 0x00, 0xFC, 0x00, 0x00, 0x00, 0x00,
		 0x00, 0xFC, 0x00),
	SOMC_CMD(0x05, 0, 0x29),
};

static const struct somc_dsi_cmd auo_id4_off_cmds[] = {
	SOMC_CMD(0x05, 40, 0x28),
	SOMC_CMD(0x29, 0, 0xD3,
		 0x13, 0x3B, 0xBB, 0xB3, 0xA5, 0x33, 0x33, 0x33,
		 0x00, 0x80, 0xA1, 0xAA, 0x4F, 0x4F, 0x33, 0x33,
		 0x33, 0xF7, 0xF2, 0x0F, 0x7D, 0x7C, 0xFF, 0x0F,
		 0x99, 0x00, 0xFF, 0xFF),
	SOMC_CMD(0x29, 40, 0xD4, 0x00, 0x00, 0x00),
	SOMC_CMD(0x05, 120, 0x10),
};

/* Panel 6: JDI + Synaptics Hybrid In-Cell */
static const struct somc_dsi_cmd jdi_id6_on_cmds[] = {
	SOMC_CMD(0x29, 0, 0xB0, 0x00),
	SOMC_CMD(0x29, 0, 0xD6, 0x01),
	SOMC_CMD(0x29, 0, 0xC4, 0x70, 0x03),
	SOMC_CMD(0x29, 0, 0xED,
		 0x27, 0x31, 0x2F, 0x13, 0x00, 0x6A, 0x99, 0x03,
		 0x17, 0x91, 0xF2, 0x00, 0x00, 0x03, 0x14, 0x17,
		 0x3F, 0x14, 0x12, 0x26, 0x23, 0x00, 0x20, 0x00,
		 0x00, 0x57),
	SOMC_CMD(0x29, 0, 0xEE,
		 0x13, 0x61, 0x5F, 0x09, 0x00, 0x6A, 0x99, 0x03,
		 0x00, 0x01, 0xB2, 0x00, 0x00, 0x03, 0x00, 0x00,
		 0x33, 0x14, 0x12, 0x00, 0x21, 0x00, 0x20, 0x00,
		 0x00, 0x57),
	SOMC_CMD(0x29, 0, 0xEF,
		 0x27, 0x31, 0x2F, 0x13, 0x00, 0x00, 0x00, 0x00,
		 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x14, 0x17,
		 0x0F, 0x14, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00,
		 0x00, 0xA6),
	SOMC_CMD(0x29, 0, 0xF0,
		 0xE3, 0x07, 0x73, 0xDF, 0x00, 0x00, 0x00, 0x00,
		 0x00, 0x00, 0x00, 0x00, 0xE0, 0xE3, 0x00, 0x00,
		 0x03, 0x14, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00,
		 0x00, 0xA7),
	SOMC_CMD(0x39, 0, 0x35, 0x00),
	SOMC_CMD(0x39, 0, 0x36, 0x00),
	SOMC_CMD(0x39, 0, 0x3A, 0x77),
	SOMC_CMD(0x39, 0, 0x2A, 0x00, 0x00, 0x04, 0x37),
	SOMC_CMD(0x39, 0, 0x2B, 0x00, 0x00, 0x07, 0x7F),
	SOMC_CMD(0x39, 0, 0x44, 0x00, 0x00),
	SOMC_CMD(0x39, 120, 0x11),
	SOMC_CMD(0x39, 0, 0x29),
};

static const struct somc_dsi_cmd generic_std_off_cmds[] = {
	SOMC_CMD(0x39, 20, 0x28),
	SOMC_CMD(0x39, 80, 0x10),
};

/* Panel 8: LGD + Synaptics Full In-Cell */
static const struct somc_dsi_cmd lgd_id8_on_cmds[] = {
	SOMC_CMD(0x23, 0, 0xB0, 0x04),
	SOMC_CMD(0x23, 0, 0xD6, 0x01),
	SOMC_CMD(0x29, 0, 0xC1,
		 0x84, 0x00, 0x10, 0xF0, 0x47, 0xF9, 0xFF, 0xAF,
		 0xFF, 0xAF, 0xCF, 0x9A, 0x73, 0x8D, 0xFD, 0xF5,
		 0x7F, 0xFD, 0xFF, 0x0F, 0xF1, 0x1F, 0x00, 0xAA,
		 0x40, 0x02, 0xC2, 0x11, 0x08, 0x00, 0x01),
	SOMC_CMD(0x29, 0, 0xCB,
		 0x8D, 0xF4, 0x4B, 0x2C, 0x00, 0x04, 0x08, 0x00, 0x00),
	SOMC_CMD(0x05, 100, 0x11),
	SOMC_CMD(0x05, 0, 0x29),
};

/* Panel 3: Sharp ID3 + Synaptics Full In-Cell */
static const struct somc_dsi_cmd sharp_id3_on_cmds[] = {
	SOMC_CMD(0x39, 0, 0x51, 0xFF),
	SOMC_CMD(0x39, 0, 0x53, 0x0C),
	SOMC_CMD(0x39, 0, 0x55, 0x00),
	SOMC_CMD(0x39, 0, 0x35, 0x00),
	SOMC_CMD(0x23, 0, 0xB0, 0x04),
	SOMC_CMD(0x23, 0, 0xD6, 0x01),
	SOMC_CMD(0x29, 0, 0xC9, 0x1F, 0x60, 0x1F, 0x60, 0x90, 0x90, 0xC4, 0x00),
	SOMC_CMD(0x29, 0, 0xD0,
		 0x19, 0x01, 0x91, 0x0B, 0x94, 0x1A, 0x09, 0x00,
		 0x00, 0x00, 0x19, 0x99, 0x00, 0x00, 0x00, 0x00),
	SOMC_CMD(0x29, 0, 0xD3,
		 0x1B, 0x3B, 0xBB, 0xBD, 0xB5, 0x33, 0x33, 0x33,
		 0x00, 0x80, 0xA6, 0xA0, 0x5F, 0x5F, 0x33, 0x33,
		 0x33, 0xF7, 0xF2, 0x0F, 0x7D, 0x7C, 0xFF, 0x0F,
		 0x99, 0x00, 0xFF, 0xFF),
	SOMC_CMD(0x29, 0, 0xD7,
		 0x04, 0xFF, 0x23, 0x55, 0x47, 0x1E, 0xFE, 0x78,
		 0xF0, 0x19, 0xA8, 0x0F, 0x38, 0x3E, 0x60, 0x1C,
		 0x0F, 0x8F, 0xC7, 0xE3, 0x99, 0xE1, 0x20, 0x00,
		 0x00, 0x00, 0x00, 0xF0, 0xEB, 0x39, 0x18, 0x07),
	SOMC_CMD(0x29, 0, 0xC1,
		 0x84, 0x00, 0x00, 0x00, 0x88, 0x01, 0x00, 0x40,
		 0xC0, 0x35, 0xE6, 0x1A, 0x63, 0xCD, 0xB1, 0xE6,
		 0xC0, 0x7A, 0x00, 0xC8, 0x08, 0x00, 0x00, 0xAA,
		 0x40, 0x02, 0x82, 0x01, 0x08, 0x00, 0x01),
	SOMC_CMD(0x29, 0, 0xC7,
		 0x06, 0x10, 0x1A, 0x23, 0x35, 0x44, 0x4E, 0x5D,
		 0x42, 0x4B, 0x57, 0x64, 0x6B, 0x71, 0x79, 0x04,
		 0x0E, 0x19, 0x22, 0x35, 0x43, 0x4E, 0x5D, 0x42,
		 0x4A, 0x55, 0x63, 0x6B, 0x70, 0x79),
	SOMC_CMD(0x29, 0, 0xC8,
		 0x01, 0x00, 0xFD, 0x01, 0xFF, 0xFC, 0x00, 0x00,
		 0xFD, 0x01, 0xFF, 0xFC, 0x00, 0x00, 0xFD, 0x01,
		 0xFF, 0xFC, 0x00),
	SOMC_CMD(0x05, 0, 0x29),
	SOMC_CMD(0x05, 120, 0x11),
};

/* Panel 9: Sharp ID9 + Synaptics Full In-Cell */
static const struct somc_dsi_cmd sharp_id9_on_cmds[] = {
	SOMC_CMD(0x39, 0, 0x51, 0xFF),
	SOMC_CMD(0x39, 0, 0x53, 0x0C),
	SOMC_CMD(0x39, 0, 0x55, 0x00),
	SOMC_CMD(0x39, 0, 0x35, 0x00),
	SOMC_CMD(0x23, 0, 0xB0, 0x04),
	SOMC_CMD(0x23, 0, 0xD6, 0x01),
	SOMC_CMD(0x29, 0, 0xC9, 0x1F, 0x60, 0x1F, 0x60, 0x90, 0x90, 0xC4, 0x00),
	SOMC_CMD(0x29, 0, 0xD5,
		 0x66, 0x00, 0x00, 0x01, 0x25, 0x01, 0x25, 0x00,
		 0x25, 0x00, 0x25),
	SOMC_CMD(0x29, 0, 0xD0,
		 0x19, 0x01, 0x91, 0x0B, 0x94, 0x0F, 0x0A, 0x00,
		 0x00, 0x00, 0x19, 0x99, 0x00, 0x00, 0x00, 0x00),
	SOMC_CMD(0x29, 0, 0xD3,
		 0x1B, 0x3B, 0xBB, 0xBD, 0xB5, 0x33, 0x33, 0x33,
		 0x00, 0x80, 0xA6, 0xA0, 0x5F, 0x5F, 0x33, 0x33,
		 0x33, 0xF7, 0xF2, 0x0F, 0x7D, 0x7C, 0xFF, 0x0F,
		 0x99, 0x00, 0xFF, 0xFF),
	SOMC_CMD(0x29, 0, 0xD7,
		 0x04, 0xFF, 0x23, 0x55, 0x47, 0x1E, 0xFE, 0x78,
		 0xF0, 0x19, 0xA8, 0x0F, 0x38, 0x3E, 0x60, 0x1C,
		 0x0F, 0x8F, 0xC7, 0xE3, 0x99, 0xE1, 0x20, 0x00,
		 0x00, 0x00, 0x00, 0xF0, 0xEB, 0x39, 0x18, 0x07),
	SOMC_CMD(0x29, 0, 0xC1,
		 0x84, 0x00, 0x00, 0x00, 0x88, 0x01, 0x00, 0x40,
		 0xC0, 0x35, 0xE6, 0x1A, 0x63, 0xCD, 0xB1, 0xE6,
		 0xC0, 0x7A, 0x00, 0xC8, 0x08, 0x00, 0x00, 0xAA,
		 0x40, 0x02, 0x82, 0x01, 0x08, 0x00, 0x01),
	SOMC_CMD(0x29, 0, 0xC7,
		 0x06, 0x10, 0x1A, 0x23, 0x35, 0x44, 0x4E, 0x5D,
		 0x42, 0x4B, 0x57, 0x64, 0x6B, 0x71, 0x79, 0x04,
		 0x0E, 0x19, 0x22, 0x35, 0x43, 0x4E, 0x5D, 0x42,
		 0x4A, 0x55, 0x63, 0x6B, 0x70, 0x79),
	SOMC_CMD(0x29, 0, 0xC8,
		 0x01, 0x00, 0xFD, 0x01, 0xFF, 0xFC, 0x00, 0x00,
		 0xFD, 0x01, 0xFF, 0xFC, 0x00, 0x00, 0xFD, 0x01,
		 0xFF, 0xFC, 0x00),
	SOMC_CMD(0x05, 0, 0x29),
	SOMC_CMD(0x05, 120, 0x11),
};

/* Default Safe Fallback 1080p60 Panel */
static const struct somc_dsi_cmd default_panel_on_cmds[] = {
	SOMC_CMD(0x39, 0, 0x36, 0x00),
	SOMC_CMD(0x39, 0, 0x3A, 0x77),
	SOMC_CMD(0x39, 0, 0x2A, 0x00, 0x00, 0x04, 0x37),
	SOMC_CMD(0x39, 0, 0x2B, 0x00, 0x00, 0x07, 0x7F),
	SOMC_CMD(0x39, 120, 0x11),
	SOMC_CMD(0x39, 0, 0x29),
};

static const struct somc_dora_panel_desc dora_panel_profiles[] = {
	{
		.panel_name = "4",
		.compatible_name = "somc,auo_synaptics_cmd_4_panel",
		.adc_min_uv = 1087000,
		.adc_max_uv = 1231000,
		.lab_uv = 5600000,
		.ibb_uv = 5600000,
		.hybrid_touch_avdd = false,
		.on_cmds = auo_id4_on_cmds,
		.num_on_cmds = ARRAY_SIZE(auo_id4_on_cmds),
		.off_cmds = auo_id4_off_cmds,
		.num_off_cmds = ARRAY_SIZE(auo_id4_off_cmds),
	},
	{
		.panel_name = "6",
		.compatible_name = "somc,jdi_synaptics_cmd_6_panel",
		.adc_min_uv = 353000,
		.adc_max_uv = 653000,
		.lab_uv = 5600000,
		.ibb_uv = 5600000,
		.hybrid_touch_avdd = true,
		.on_cmds = jdi_id6_on_cmds,
		.num_on_cmds = ARRAY_SIZE(jdi_id6_on_cmds),
		.off_cmds = generic_std_off_cmds,
		.num_off_cmds = ARRAY_SIZE(generic_std_off_cmds),
	},
	{
		.panel_name = "8",
		.compatible_name = "somc,lgd_synaptics_cmd_8_panel",
		.adc_min_uv = 215000,
		.adc_max_uv = 256000,
		.lab_uv = 5500000,
		.ibb_uv = 5500000,
		.hybrid_touch_avdd = false,
		.on_cmds = lgd_id8_on_cmds,
		.num_on_cmds = ARRAY_SIZE(lgd_id8_on_cmds),
		.off_cmds = generic_std_off_cmds,
		.num_off_cmds = ARRAY_SIZE(generic_std_off_cmds),
	},
	{
		.panel_name = "3",
		.compatible_name = "somc,sharp_synaptics_cmd_3_panel",
		.adc_min_uv = 1236000,
		.adc_max_uv = 1395000,
		.lab_uv = 5600000,
		.ibb_uv = 5600000,
		.hybrid_touch_avdd = false,
		.on_cmds = sharp_id3_on_cmds,
		.num_on_cmds = ARRAY_SIZE(sharp_id3_on_cmds),
		.off_cmds = generic_std_off_cmds,
		.num_off_cmds = ARRAY_SIZE(generic_std_off_cmds),
	},
	{
		.panel_name = "9",
		.compatible_name = "somc,sharp_synaptics_cmd_9_panel",
		.adc_min_uv = 0,
		.adc_max_uv = 57000,
		.lab_uv = 5600000,
		.ibb_uv = 5600000,
		.hybrid_touch_avdd = false,
		.on_cmds = sharp_id9_on_cmds,
		.num_on_cmds = ARRAY_SIZE(sharp_id9_on_cmds),
		.off_cmds = generic_std_off_cmds,
		.num_off_cmds = ARRAY_SIZE(generic_std_off_cmds),
	},
};

static const struct somc_dora_panel_desc dora_default_profile = {
	.panel_name = "Default Panel",
	.compatible_name = "somc,default_cmd_panel",
	.adc_min_uv = 0,
	.adc_max_uv = 0x7fffffff,
	.lab_uv = 5600000,
	.ibb_uv = 5600000,
	.hybrid_touch_avdd = true,
	.on_cmds = default_panel_on_cmds,
	.num_on_cmds = ARRAY_SIZE(default_panel_on_cmds),
	.off_cmds = generic_std_off_cmds,
	.num_off_cmds = ARRAY_SIZE(generic_std_off_cmds),
};

struct somc_dora_panel {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi;
	const struct somc_dora_panel_desc *desc;
	u32 detected_adc_uv;

	struct regulator *vddio;
	struct regulator *lab;
	struct regulator *ibb;
	struct regulator *touch_avdd;

	struct gpio_desc *reset_gpio;
	struct gpio_desc *vddio_gpio;
	bool prepared;
};

static inline struct somc_dora_panel *to_somc_dora_panel(struct drm_panel *panel)
{
	return container_of(panel, struct somc_dora_panel, panel);
}

static int somc_dsi_send_table(struct mipi_dsi_device *dsi,
			       const struct somc_dsi_cmd *cmds,
			       size_t count, u16 flags)
{
	size_t i;
	ssize_t err;

	for (i = 0; i < count; i++) {
		struct mipi_dsi_msg msg = {
			.channel = dsi->channel,
			.type = cmds[i].dtype,
			.flags = flags,
			.tx_len = cmds[i].len,
			.tx_buf = cmds[i].payload,
		};

		if (!dsi->host || !dsi->host->ops || !dsi->host->ops->transfer)
			return -ENODEV;

		err = dsi->host->ops->transfer(dsi->host, &msg);
		if (err < 0)
			return (int)err;

		if (cmds[i].wait_ms)
			msleep(cmds[i].wait_ms);
	}

	return 0;
}

static int somc_dora_panel_prepare(struct drm_panel *panel)
{
	struct somc_dora_panel *ctx = to_somc_dora_panel(panel);
	int ret;

	if (ctx->prepared)
		return 0;

	ret = regulator_enable(ctx->vddio);
	if (ret < 0)
		return ret;

	if (ctx->vddio_gpio)
		gpiod_set_value_cansleep(ctx->vddio_gpio, 1);

	usleep_range(1000, 2000);

	if (ctx->desc->hybrid_touch_avdd && ctx->touch_avdd) {
		ret = regulator_enable(ctx->touch_avdd);
		if (ret < 0)
			goto err_vddio;
	}

	regulator_set_voltage(ctx->lab, ctx->desc->lab_uv, ctx->desc->lab_uv);
	regulator_set_voltage(ctx->ibb, ctx->desc->ibb_uv, ctx->desc->ibb_uv);

	ret = regulator_enable(ctx->lab);
	if (ret < 0)
		goto err_touch_avdd;

	usleep_range(1000, 2000);

	ret = regulator_enable(ctx->ibb);
	if (ret < 0)
		goto err_lab;

	msleep(20);

	/* Sony 4.4 pw-on-rst-seq: low 10ms -> high 10ms */
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	msleep(10);
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	msleep(10);

	ret = somc_dsi_send_table(ctx->dsi, ctx->desc->on_cmds,
				  ctx->desc->num_on_cmds, MIPI_DSI_MSG_USE_LPM);
	if (ret < 0) {
		dev_err(panel->dev, "Failed to send panel %s init sequence: %d\n",
			ctx->desc->panel_name, ret);
		goto err_ibb;
	}

	ctx->prepared = true;
	return 0;

err_ibb:
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	regulator_disable(ctx->ibb);
err_lab:
	regulator_disable(ctx->lab);
err_touch_avdd:
	if (ctx->desc->hybrid_touch_avdd && ctx->touch_avdd)
		regulator_disable(ctx->touch_avdd);
err_vddio:
	if (ctx->vddio_gpio)
		gpiod_set_value_cansleep(ctx->vddio_gpio, 0);
	regulator_disable(ctx->vddio);
	return ret;
}

static int somc_dora_panel_unprepare(struct drm_panel *panel)
{
	struct somc_dora_panel *ctx = to_somc_dora_panel(panel);

	if (!ctx->prepared)
		return 0;

	somc_dsi_send_table(ctx->dsi, ctx->desc->off_cmds,
			    ctx->desc->num_off_cmds, 0);

	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	regulator_disable(ctx->ibb);
	regulator_disable(ctx->lab);
	msleep(70);

	if (ctx->desc->hybrid_touch_avdd && ctx->touch_avdd)
		regulator_disable(ctx->touch_avdd);

	if (ctx->vddio_gpio)
		gpiod_set_value_cansleep(ctx->vddio_gpio, 0);

	regulator_disable(ctx->vddio);
	ctx->prepared = false;
	return 0;
}

/*
 * Exact verified Dora 1080p60 mode from Sony 4.4:
 *   hdisplay=1080, hfp=56, hpw=8, hbp=8   -> htotal = 1152
 *   vdisplay=1920, vfp=227, vpw=8, vbp=8  -> vtotal = 2163
 *   pclk = 1152 * 2163 * 60 = 149,506,560 Hz (149507 kHz)
 *   bitclk (4 lanes, 24bpp) = 897,039,360 Hz, byteclk = 112,129,920 Hz
 */
static const struct drm_display_mode somc_dora_1080p60_mode = {
	.clock = 149507,
	.hdisplay = 1080,
	.hsync_start = 1080 + 56,
	.hsync_end = 1080 + 56 + 8,
	.htotal = 1080 + 56 + 8 + 8,
	.vdisplay = 1920,
	.vsync_start = 1920 + 227,
	.vsync_end = 1920 + 227 + 8,
	.vtotal = 1920 + 227 + 8 + 8,
	.width_mm = 61,
	.height_mm = 110,
};

static int somc_dora_panel_get_modes(struct drm_panel *panel,
				     struct drm_connector *connector)
{
	struct drm_display_mode *mode;

	mode = drm_mode_duplicate(connector->dev, &somc_dora_1080p60_mode);
	if (!mode)
		return -ENOMEM;

	drm_mode_set_name(mode);
	mode->type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED;
	connector->display_info.width_mm = mode->width_mm;
	connector->display_info.height_mm = mode->height_mm;
	drm_mode_probed_add(connector, mode);

	return 1;
}

static const struct drm_panel_funcs somc_dora_panel_funcs = {
	.prepare = somc_dora_panel_prepare,
	.unprepare = somc_dora_panel_unprepare,
	.get_modes = somc_dora_panel_get_modes,
};

static int somc_dora_detect_adc_uv(struct device *dev, u32 *out_uv)
{
	struct device_node *chosen;
	const char *bootargs = NULL;
	struct iio_channel *chan;
	int val_uv = 0;
	int ret;

	if (somc_lcdid_from_cmdline) {
		*out_uv = (u32)somc_lcdid_adc_uv;
		return 0;
	}

	chosen = of_find_node_by_path("/chosen");
	if (chosen) {
		if (!of_property_read_string(chosen, "bootargs", &bootargs) && bootargs) {
			const char *p = strstr(bootargs, "lcdid_adc=");

			if (p) {
				char buf[24];
				unsigned long parsed;
				size_t len = strcspn(p + 10, " \t\n");

				if (len > 0 && len < sizeof(buf)) {
					memcpy(buf, p + 10, len);
					buf[len] = '\0';
					if (!kstrtoul(buf, 0, &parsed)) {
						of_node_put(chosen);
						*out_uv = (u32)parsed;
						return 0;
					}
				}
			}
		}
		of_node_put(chosen);
	}

	chan = iio_channel_get(dev, "lcd-id");
	if (IS_ERR(chan)) {
		if (PTR_ERR(chan) == -EPROBE_DEFER)
			return -EPROBE_DEFER;
		*out_uv = (u32)somc_lcdid_adc_uv;
		return 0;
	}

	ret = iio_read_channel_processed(chan, &val_uv);
	iio_channel_release(chan);
	if (ret >= 0 && val_uv >= 0) {
		*out_uv = (u32)val_uv;
		return 0;
	}

	*out_uv = (u32)somc_lcdid_adc_uv;
	return 0;
}

static const struct somc_dora_panel_desc *somc_dora_match_profile(u32 adc_uv)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(dora_panel_profiles); i++) {
		if (adc_uv >= dora_panel_profiles[i].adc_min_uv &&
		    adc_uv <= dora_panel_profiles[i].adc_max_uv)
			return &dora_panel_profiles[i];
	}

	return &dora_default_profile;
}

static ssize_t panel_name_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	struct somc_dora_panel *ctx = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%s\n", ctx->desc->panel_name);
}
static DEVICE_ATTR_RO(panel_name);

static ssize_t panel_compatible_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct somc_dora_panel *ctx = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%s\n", ctx->desc->compatible_name);
}
static DEVICE_ATTR_RO(panel_compatible);

static ssize_t lcdid_adc_uv_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct somc_dora_panel *ctx = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%u\n", ctx->detected_adc_uv);
}
static DEVICE_ATTR_RO(lcdid_adc_uv);

static struct attribute *somc_dora_panel_attrs[] = {
	&dev_attr_panel_name.attr,
	&dev_attr_panel_compatible.attr,
	&dev_attr_lcdid_adc_uv.attr,
	NULL,
};
ATTRIBUTE_GROUPS(somc_dora_panel);

static int somc_dora_panel_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	struct somc_dora_panel *ctx;
	int ret;

	ctx = devm_kzalloc(dev, sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ret = somc_dora_detect_adc_uv(dev, &ctx->detected_adc_uv);
	if (ret)
		return dev_err_probe(dev, ret, "Waiting for PM8994 VADC lcd-id channel\n");

	ctx->desc = somc_dora_match_profile(ctx->detected_adc_uv);

	dev_info(dev, "DSI-PANEL-DIAG: physical=%u Panel Name=%s (%s) 1080x1920@60 bitclk=897039360 pclk=149506560\n",
		 ctx->detected_adc_uv, ctx->desc->panel_name, ctx->desc->compatible_name);

	ctx->vddio = devm_regulator_get(dev, "vddio");
	if (IS_ERR(ctx->vddio))
		return dev_err_probe(dev, PTR_ERR(ctx->vddio), "Failed to get vddio\n");

	ctx->lab = devm_regulator_get(dev, "lab");
	if (IS_ERR(ctx->lab))
		return dev_err_probe(dev, PTR_ERR(ctx->lab), "Failed to get lab\n");

	ctx->ibb = devm_regulator_get(dev, "ibb");
	if (IS_ERR(ctx->ibb))
		return dev_err_probe(dev, PTR_ERR(ctx->ibb), "Failed to get ibb\n");

	ctx->touch_avdd = devm_regulator_get_optional(dev, "touch-avdd");
	if (IS_ERR(ctx->touch_avdd)) {
		if (PTR_ERR(ctx->touch_avdd) != -ENODEV)
			return PTR_ERR(ctx->touch_avdd);
		ctx->touch_avdd = NULL;
	}

	ctx->reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ctx->reset_gpio))
		return dev_err_probe(dev, PTR_ERR(ctx->reset_gpio), "Failed to get reset gpio\n");

	ctx->vddio_gpio = devm_gpiod_get_optional(dev, "vddio", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->vddio_gpio))
		return dev_err_probe(dev, PTR_ERR(ctx->vddio_gpio), "Failed to get vddio gpio\n");

	ctx->dsi = dsi;
	mipi_dsi_set_drvdata(dsi, ctx);

	dsi->lanes = 4;
	dsi->format = MIPI_DSI_FMT_RGB888;
	dsi->mode_flags = MIPI_DSI_CLOCK_NON_CONTINUOUS;

	drm_panel_init(&ctx->panel, dev, &somc_dora_panel_funcs,
		       DRM_MODE_CONNECTOR_DSI);

	ret = drm_panel_of_backlight(&ctx->panel);
	if (ret)
		return dev_err_probe(dev, ret, "Failed to get WLED backlight\n");

	drm_panel_add(&ctx->panel);

	ret = mipi_dsi_attach(dsi);
	if (ret < 0) {
		drm_panel_remove(&ctx->panel);
		return dev_err_probe(dev, ret, "Failed to attach to DSI host\n");
	}

	return 0;
}

static int somc_dora_panel_remove(struct mipi_dsi_device *dsi)
{
	struct somc_dora_panel *ctx = mipi_dsi_get_drvdata(dsi);

	mipi_dsi_detach(dsi);
	drm_panel_remove(&ctx->panel);
	return 0;
}

static const struct of_device_id somc_dora_panel_of_match[] = {
	{ .compatible = "somc,dora-incell-panel" },
	{ .compatible = "somc,auo_synaptics_cmd_4_panel" },
	{ .compatible = "somc,jdi_synaptics_cmd_6_panel" },
	{ .compatible = "somc,lgd_synaptics_cmd_8_panel" },
	{ .compatible = "somc,sharp_synaptics_cmd_3_panel" },
	{ .compatible = "somc,sharp_synaptics_cmd_9_panel" },
	{ }
};
MODULE_DEVICE_TABLE(of, somc_dora_panel_of_match);

static struct mipi_dsi_driver somc_dora_panel_driver = {
	.probe = somc_dora_panel_probe,
	.remove = somc_dora_panel_remove,
	.driver = {
		.name = "panel-sony-dora-incell",
		.of_match_table = somc_dora_panel_of_match,
		.dev_groups = somc_dora_panel_groups,
	},
};
module_mipi_dsi_driver(somc_dora_panel_driver);

MODULE_DESCRIPTION("Sony Xperia X Performance (dora) In-Cell Command-Mode DSI Panel Driver");
MODULE_LICENSE("GPL v2");
