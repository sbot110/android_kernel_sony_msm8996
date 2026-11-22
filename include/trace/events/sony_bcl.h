/* SPDX-License-Identifier: GPL-2.0-only */
/* Copyright (c) 2014-2017, The Linux Foundation. All rights reserved. */
#undef TRACE_SYSTEM
#define TRACE_SYSTEM sony_bcl

#if !defined(_TRACE_SONY_BCL_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_SONY_BCL_H
#include <linux/tracepoint.h>

DECLARE_EVENT_CLASS(sony_bcl_print_reading,

	TP_PROTO(const char *sensor_name, long value),

	TP_ARGS(
		sensor_name, value
	),

	TP_STRUCT__entry(
		__string(_name, sensor_name)
		__field(long, reading)
	),

	TP_fast_assign(
		__assign_str(_name, sensor_name);
		__entry->reading = value;
	),

	TP_printk(
		"%s:[%ld]", __get_str(_name), __entry->reading
	)
);

DECLARE_EVENT_CLASS(sony_bcl_print_event,

	TP_PROTO(const char *event_name),

	TP_ARGS(
		event_name
	),

	TP_STRUCT__entry(
		__string(_name,	event_name)
	),

	TP_fast_assign(
		__assign_str(_name, event_name);
	),

	TP_printk(
		"Event:[%s]", __get_str(_name)
	)
);

DECLARE_EVENT_CLASS(sony_bcl_print_reg,

	TP_PROTO(const char *sensor_name, unsigned int address,
			unsigned int value),

	TP_ARGS(
		sensor_name, address, value
	),

	TP_STRUCT__entry(
		__string(_name, sensor_name)
		__field(unsigned int, _address)
		__field(unsigned int, _value)
	),

	TP_fast_assign(
		__assign_str(_name, sensor_name);
		__entry->_address = address;
		__entry->_value = value;
	),

	TP_printk(
		"%s: address 0x%x: data 0x%02x", __get_str(_name),
		__entry->_address, __entry->_value
	)
);

DEFINE_EVENT(sony_bcl_print_reading, sony_bcl_hw_sensor_reading,

	TP_PROTO(const char *sensor_name, long intensity),

	TP_ARGS(sensor_name, intensity)
);

DEFINE_EVENT(sony_bcl_print_reg, sony_bcl_hw_reg_access,

	TP_PROTO(const char *op_name, unsigned int address, unsigned int value),

	TP_ARGS(op_name, address, value)
);

DEFINE_EVENT(sony_bcl_print_reading, sony_bcl_hw_mitigation,

	TP_PROTO(const char *sensor_name, long intensity),

	TP_ARGS(sensor_name, intensity)
);

DEFINE_EVENT(sony_bcl_print_event, sony_bcl_hw_mitigation_event,

	TP_PROTO(const char *event_name),

	TP_ARGS(event_name)
);

DEFINE_EVENT(sony_bcl_print_reading, sony_bcl_hw_state_event,

	TP_PROTO(const char *sensor_name, long intensity),

	TP_ARGS(sensor_name, intensity)
);

DEFINE_EVENT(sony_bcl_print_event, sony_bcl_hw_event,

	TP_PROTO(const char *event_name),

	TP_ARGS(event_name)
);

#endif

#include <trace/define_trace.h>
