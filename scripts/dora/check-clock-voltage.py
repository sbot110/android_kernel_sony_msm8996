#!/usr/bin/env python3
"""Check final Sony fmax contracts and execute the production voltage adapter.

The bounded regulator model injects delivered/lost ACKs, not measured rails.
CCF ordering and native RPM transport are tested by their separate host tests.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference-commit', action='store_true',
                    help='also verify the manifest against the saved Sony Git object')
parser.add_argument('--self-test', action='store_true',
                    help='reject in-memory readiness, ACK and handoff regressions')
args = parser.parse_args()
manifest = json.loads((ROOT / 'Documentation/sony/dora-clock-voltage.json').read_text())
header = (ROOT / 'drivers/clk/qcom/clk-msm8996-voltage.h').read_text()
levels = manifest['levels']


def block(text, start):
    begin = text.index('{', start)
    end, depth = begin + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[begin:end]


def source_maps(controller):
    text = subprocess.check_output(['git', 'show', manifest['source_commit'] + ':' +
                                   manifest['source_files'][controller]], cwd=ROOT, text=True)
    result = {}
    for match in re.finditer(r'static struct (?:rcg_clk|alpha_pll_clk|pll_vote_clk) (\w+) = \{', text):
        entry = block(text, match.start())
        fmap = re.search(r'VDD_(DIG|MMPLL4)_FMAX_MAP\d\(([^)]+)\)', entry)
        if not fmap:
            continue
        cells = [cell.strip() for cell in fmap[2].split(',')]
        result[match[1]] = {'analog': fmap[1] == 'MMPLL4',
                            'fmax': dict(zip(cells[::2], map(int, cells[1::2])))}
    fixup = {'gcc': 'msm_clocks_gcc_8996_v2_fixup', 'mmcc': 'msm_mmsscc_8996_v3_fixup'}[controller]
    overrides = block(text, text.index('static void ' + fixup))
    for name, level, frequency in re.findall(r'(\w+)\.c\.fmax\[VDD_DIG_(\w+)\] = (\d+);', overrides):
        if name in result:
            result[name]['fmax'][level] = int(frequency)
    expected = {name: {'analog': entry['analog'], 'fmax': entry['fmax']}
                for name, entry in manifest['controllers'][controller].items()}
    assert result == expected, controller + ': Sony final source disagrees with manifest'


if args.reference_commit:
    for controller in ('gcc', 'mmcc'):
        source_maps(controller)
    print('PASS: all manifest contracts independently parsed from Sony source and final v3 fixups')


def check_maps(controller, text):
    match = re.search(r'static struct msm8996_voltage_clock ' + controller +
                      r'_msm8996_voltage_clocks\[\] = \{(.*?)\n\};', text, re.S)
    assert match, controller
    entries = re.findall(r'\{ \.hw = &(\w+)\.clkr\.hw, \.fmax = \{ ([\dUL, ]+) \},'
                         r'( \.analog = true,)?(?: \.pll = true,)? \}', match[1])
    actual = {}
    for name, values, analog in entries:
        assert name not in actual, name
        actual[name] = {'fmax': [int(value.strip().removesuffix('UL'))
                                for value in values.split(',')], 'analog': bool(analog)}
    expected = {name: {'fmax': [entry['fmax'].get(level, 0) for level in levels],
                       'analog': entry['analog']}
                for name, entry in manifest['controllers'][controller].items()}
    assert actual == expected, controller + ': final source fmax map changed'
    return len(actual)


counts = {}
for controller in ('gcc', 'mmcc'):
    code = (ROOT / ('drivers/clk/qcom/%s-msm8996.c' % controller)).read_text()
    counts[controller] = check_maps(controller, code)
    raw_ids = re.search(controller + r'_msm8996_internal_plls\[\] = \{(.*?)\n\};', code, re.S)[1]
    published_raw = re.findall(r'\[([A-Z0-9_]+_EARLY)\] =', code)
    assert set(re.findall(r'\b[A-Z0-9_]+_EARLY\b', raw_ids)) == set(published_raw)
    assert 'ret = devm_clk_register_regmap(dev, desc.clks[id]);' in code
    assert 'desc.clks[id] = NULL;' in code
    # A mutation must fail rather than silently accepting an edited fmax.
    bad = re.sub(r'(?<=\.fmax = \{ )0UL', '1UL', code, count=1)
    try:
        check_maps(controller, bad)
    except AssertionError:
        pass
    else:
        raise AssertionError('fmax mutation escaped')
gcc = (ROOT / 'drivers/clk/qcom/gcc-msm8996.c').read_text()
sdcc2 = re.search(r'ftbl_sdcc2_apps_clk_src\[\] = \{(.*?)\n\};', gcc, re.S)[1]
assert 'F(40000000, P_GPLL0, 7.5, 1, 2)' in sdcc2
assert 'F(80000000, P_GPLL0, 7.5, 0, 0)' in sdcc2
assert 'P_GPLL4' in re.search(r'ftbl_sdcc1_apps_clk_src\[\] = \{(.*?)\n\};', gcc, re.S)[1]
assert 'main->ops = &clk_alpha_pll_postdiv_ro_ops;' in gcc
assert 'early_ops.set_rate = NULL;' in gcc and 'early_ops.round_rate = NULL;' in gcc
assert 'main->flags |= CLK_IS_CRITICAL;' in gcc
assert [int(x) for x in re.search(r'msm8996_logical_corner\[\] = \{([^}]+)', header)[1].split(',')] == manifest['logical_corners']

harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define CLK_ENABLE_HAND_OFF 1
#define GFP_KERNEL 0
#define EPROBE_DEFER 517
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
struct device { void *of_node;unsigned refs; };
struct mutex { unsigned held; };
static void (*lock_boundary)(void);
static void mutex_init(struct mutex *m) {m->held=0;}
static void mutex_lock(struct mutex *m)
{ assert(!m->held);if(lock_boundary){void (*hook)(void)=lock_boundary;lock_boundary=NULL;hook();}m->held=1; }
static void mutex_unlock(struct mutex *m) {assert(m->held);m->held=0;}
struct clk_vdd_class {
    const char *class_name;unsigned num_levels;int *level_votes,cur_level;
    int (*set_vdd)(struct clk_vdd_class *,int);
    int (*sync_vdd)(struct clk_vdd_class *,int);
    bool skip_handoff,require_handoff;struct mutex lock;
};
struct clk_init_data {struct clk_vdd_class *vdd_class;unsigned long *rate_max;unsigned num_rate_max,flags;};
struct clk_hw {const struct clk_init_data *init;bool enabled,prepared;unsigned long rate;struct clk_hw *parent;};
/* API boundaries only: the actual core aggregation and RPM transaction engines
 * have separate source-executing tests. Cached load changes on every call,
 * including a failed or quarantined callback. Lost ACK delivery is selectable.
 */
struct regulator {
    unsigned id;int logical,physical,count,load_request,physical_load;
    bool physical_enabled,online,typed,quarantined;
};
static struct regulator digital,analog;
static unsigned steps,fail_at,gets,put_count,warnings,releases,ready_checks,queries,wire_requests;
static bool partial,machine,forever,handoff_done,release_marks_done;
static int release_error,ready_error;
static char trace[1024];
static int step(char event)
{ assert(steps+1<sizeof(trace));trace[steps++]=event;trace[steps]=0;
  return fail_at && (steps==fail_at || (forever && steps>=fail_at))?-EIO:0; }
static int analog_step(char event)
{ bool blocked=analog.quarantined;int ret=step(event);
  if(blocked)return -EUCLEAN;
  wire_requests++;if(ret)analog.quarantined=true;return ret; }
static bool of_machine_is_compatible(const char *name) {(void)name;return machine;}
static bool of_property_read_bool(void *node,const char *name) {(void)name;return !!node;}
static struct device *get_device(struct device *dev) {dev->refs++;return dev;}
static struct regulator *regulator_get(struct device *dev,const char *name)
{ (void)dev;gets++;return !strcmp(name,"defer")?ERR_PTR(-EPROBE_DEFER):!strcmp(name,"digital")?&digital:&analog; }
static void regulator_put(struct regulator *reg) {(void)reg;put_count++;}
static int regulator_set_voltage(struct regulator *reg,int min,int max)
{ bool may_apply=!reg->quarantined;int ret=reg->id?analog_step('u'):step('v');assert(min<=max);
  if(may_apply&&(!ret||partial)){reg->logical=min;reg->physical=min;}return ret; }
static int regulator_set_load(struct regulator *reg,int load)
{ bool may_apply=!reg->quarantined;int ret;assert(reg->id);reg->load_request=load;
  ret=analog_step('l');if(may_apply&&(!ret||partial))reg->physical_load=load;return ret; }
static int regulator_enable(struct regulator *reg)
{ bool may_apply=!reg->quarantined;int ret=reg->id?analog_step('a'):step('d');
  if(may_apply&&(!ret||partial)){reg->physical_enabled=true;}if(!ret){reg->count++;}return ret; }
static int regulator_disable(struct regulator *reg)
{ bool may_apply=!reg->quarantined;int ret=reg->id?analog_step('A'):step('D');assert(reg->count>0);
  if(may_apply&&(!ret||partial)){reg->physical_enabled=false;}if(!ret){reg->count--;}return ret; }
static int qcom_rpm_corner_sync(struct regulator *reg,bool enabled)
{ int ret=step('K');assert(!reg->id);if(!ret){reg->physical=reg->logical;reg->physical_enabled=enabled?true:reg->count>0;}return ret; }
static int qcom_rpm_physical_check_ready(struct regulator *reg)
{ assert(reg==&analog);ready_checks++;if(!reg->typed)return -EOPNOTSUPP;
  if(ready_error){return ready_error;}if(!reg->online){return -EPROBE_DEFER;}
  return reg->quarantined?-EUCLEAN:0; }
static int qcom_rpm_physical_sync(struct regulator *reg,bool enabled)
{ int ret;assert(reg==&analog&&reg->online&&reg->typed);ret=analog_step('P');
  if(!ret){assert(!enabled||reg->count>0);reg->physical=reg->logical;
    reg->physical_load=reg->load_request;reg->physical_enabled=reg->count>0;}return ret; }
static void *devm_kmemdup(struct device *dev,const void *p,size_t size,int flags)
{ void *copy=malloc(size);(void)dev;(void)flags;assert(copy);return memcpy(copy,p,size); }
static bool clk_hw_is_enabled(struct clk_hw *hw) {return hw->enabled;}
static bool clk_hw_is_prepared(struct clk_hw *hw) {return hw->prepared;}
static struct clk_hw *clk_hw_get_parent(struct clk_hw *hw) {return hw->parent;}
static unsigned long clk_hw_get_rate(struct clk_hw *hw) {return hw->rate;}
static int clk_vdd_class_release_handoff(struct clk_vdd_class *vdd)
{ (void)vdd;releases++;if(!release_error&&release_marks_done)handoff_done=true;return release_error; }
static bool clk_vdd_class_handoff_done(struct clk_vdd_class *vdd)
{ assert(!vdd->lock.held);queries++;return handoff_done; }
#define dev_warn(dev,...) ((void)(dev),(void)warnings++)
'''
harness += re.sub(r'^#include[^\n]*\n', '', header, flags=re.M)
harness += r'''
static struct msm8996_voltage voltage;
static struct device device;
static void fresh(void)
{
    struct msm8996_voltage initial=MSM8996_VOLTAGE_CLASS("test");
    voltage=initial;memset(&digital,0,sizeof(digital));memset(&analog,0,sizeof(analog));
    analog.id=1;analog.online=analog.typed=true;
    memset(&device,0,sizeof(device));steps=fail_at=gets=put_count=warnings=releases=0;
    ready_checks=queries=wire_requests=0;ready_error=release_error=0;
    lock_boundary=NULL;
    partial=machine=forever=handoff_done=release_marks_done=false;trace[0]=0;
}
static void reset(bool with_analog)
{
    fresh();
    assert(!msm8996_voltage_init(&device,&voltage,"digital",with_analog?"analog":NULL));
    assert(voltage.applied==MSM8996_VOLTAGE_HIGH && digital.physical==7);
    assert(voltage.digital_enabled && digital.count==1 && digital.physical_enabled);
    assert(!with_analog || (analog.physical==1800000 && analog.physical_enabled && analog.count==1));
    assert(voltage.analog_boot_load==with_analog);
    assert(!with_analog || (analog.load_request==10000&&analog.physical_load==10000&&ready_checks==1));
}
static void clear_events(void) {steps=fail_at=0;partial=forever=false;trace[0]=0;}
static void assert_sticky(void)
{
    unsigned before=wire_requests,old_gets=gets,refs=device.refs;
    int dig_count=digital.count,ana_count=analog.count;
    assert(voltage.analog_uncertain&&voltage.faulted);clear_events();
    assert(msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_HIGH)==-EIO&&!steps);
    assert(msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_HIGH)==-EIO&&!steps);
    assert(msm8996_voltage_init(&device,&voltage,"digital","analog")==-EIO&&!steps);
    assert(gets==old_gets&&device.refs==refs&&digital.count==dig_count&&analog.count==ana_count);
    /* Explicit provider baseline recovery alone cannot reconcile the clock
     * lease/counts and must not silently clear the helper's uncertainty.
     */
    analog.quarantined=false;
    assert(msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_HIGH)==-EIO&&!steps);
    assert(wire_requests==before&&voltage.analog_uncertain&&voltage.faulted);
}
static void test_digital_and_ownership(void)
{
    struct device other={0};struct clk_init_data original={0};
    struct clk_hw hw={.init=&original,.enabled=true,.rate=0};
    struct msm8996_voltage_clock maps[]={{.hw=&hw,.fmax={0,19200000,50000000,400000000,0}}};
    reset(false);assert(device.refs==1&&gets==1);clear_events();
    assert(voltage.vdd.require_handoff&&!voltage.vdd.skip_handoff);
    assert(msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_LOW)==-EAGAIN);
    assert(digital.physical==7&&voltage.applied==MSM8996_VOLTAGE_HIGH&&!voltage.faulted);
    assert(!msm8996_voltage_init(&device,&voltage,"digital",NULL));
    assert(gets==1&&device.refs==1&&digital.count==1);
    assert(msm8996_voltage_init(&other,&voltage,"digital",NULL)==-EBUSY);
    assert(msm8996_voltage_apply(&voltage,-1)==-EINVAL);
    assert(msm8996_voltage_apply(&voltage,MSM8996_VOLTAGE_LEVELS)==-EINVAL);
    assert(!msm8996_voltage_attach(&device,maps,1,&voltage,NULL));
    assert(hw.init->vdd_class==&voltage.vdd&&hw.init->rate_max==maps[0].fmax&&hw.init->flags&CLK_ENABLE_HAND_OFF);
    msm8996_voltage_ownership(&device,maps,1,&voltage,NULL);
    assert(!voltage.ownership_known&&warnings==1&&!releases);
    assert(msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_NONE)==-EAGAIN&&digital.count==1);
    hw.rate=19200000;msm8996_voltage_ownership(&device,maps,1,&voltage,NULL);
    assert(voltage.ownership_known&&releases==1);
    { struct clk_hw parent={.enabled=true};
      hw.parent=&parent;maps[0].pll=true;hw.prepared=false;releases=0;
      msm8996_voltage_ownership(&device,maps,1,&voltage,NULL);
      assert(!voltage.ownership_known&&!releases);
      hw.prepared=true;msm8996_voltage_ownership(&device,maps,1,&voltage,NULL);
      assert(voltage.ownership_known&&releases==1);maps[0].pll=false;hw.parent=NULL; }
    free((void *)hw.init);hw.init=&original;
    clear_events();assert(!msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_LOW));
    assert(digital.physical==4&&digital.count==1&&voltage.applied==MSM8996_VOLTAGE_LOW);
    clear_events();assert(!msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_LOW));assert(!strcmp(trace,"K"));
    clear_events();fail_at=1;assert(msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_LOW)==-EIO&&voltage.faulted);
    clear_events();assert(!msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_LOW)&&!voltage.faulted);
    clear_events();fail_at=1;forever=true;
    assert(msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_NOMINAL)==-EIO&&voltage.faulted);
    clear_events();assert(!msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_LOW)&&!voltage.faulted);
    clear_events();assert(!msm8996_voltage_set(&voltage.vdd,0));
    assert(!digital.count&&!voltage.digital_enabled&&!strcmp(trace,"vDK"));
}
static void test_analog_transitions(void)
{
    unsigned i,n,limit;int ret;char events[32];bool uncertain;
    reset(true);voltage.ownership_known=true;clear_events();
    assert(!msm8996_voltage_set(&voltage.vdd,0));assert(!strcmp(trace,"vADPK"));
    limit=steps;strcpy(events,trace);
    for(n=0;n<2;n++)for(i=1;i<=limit;i++) {
        reset(true);voltage.ownership_known=true;clear_events();fail_at=i;partial=n;
        ret=msm8996_voltage_set(&voltage.vdd,0);assert(ret==-EIO);
        uncertain=events[i-1]=='A'||events[i-1]=='P';
        assert(voltage.analog_uncertain==uncertain);
        if(uncertain)assert_sticky();
        else {
            assert(!voltage.faulted&&voltage.applied==MSM8996_VOLTAGE_HIGH);
            assert(digital.physical==7&&digital.physical_enabled&&digital.count==1);
            assert(analog.physical_enabled&&analog.count==1);
        }
    }
    reset(true);voltage.ownership_known=true;assert(!msm8996_voltage_set(&voltage.vdd,0));clear_events();
    assert(!msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_HIGH));assert(!strcmp(trace,"vdluaPK"));
    limit=steps;strcpy(events,trace);
    for(n=0;n<2;n++)for(i=1;i<=limit;i++) {
        reset(true);voltage.ownership_known=true;assert(!msm8996_voltage_set(&voltage.vdd,0));
        clear_events();fail_at=i;partial=n;
        ret=msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_HIGH);assert(ret==-EIO);
        uncertain=strchr("luaP",events[i-1])!=NULL;
        assert(voltage.analog_uncertain==uncertain);
        if(uncertain)assert_sticky();
        else assert(!voltage.faulted&&voltage.applied==0&&!digital.count&&!analog.count);
    }
    for(n=0;n<2;n++)for(i=1;i<=2;i++) {
        reset(true);voltage.ownership_known=true;clear_events();
        assert(!msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_HIGH));assert(!strcmp(trace,"KP"));
        clear_events();fail_at=i;partial=n;
        assert(msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_HIGH)==-EIO&&voltage.faulted);
        assert(voltage.analog_uncertain==(i==2));
        if(i==2)assert_sticky();
        else {clear_events();assert(!msm8996_voltage_sync(&voltage.vdd,MSM8996_VOLTAGE_HIGH));assert(!voltage.faulted);}
    }
}
static void test_init_and_defer(void)
{
    unsigned i,n,old_gets;int dig_count,ana_count;char events[32];
    reset(true);strcpy(events,trace);assert(!strcmp(events,"vdluaPK"));
    for(n=0;n<2;n++)for(i=1;i<=strlen(events);i++) {
        fresh();fail_at=i;partial=n;
        assert(msm8996_voltage_init(&device,&voltage,"digital","analog")==-EIO);
        assert(voltage.initialized&&gets==2&&!put_count&&device.refs==1);
        if(strchr("luaP",events[i-1]))assert_sticky();
        else {
            assert(!voltage.faulted&&voltage.applied==MSM8996_VOLTAGE_HIGH);
            old_gets=gets;dig_count=digital.count;ana_count=analog.count;clear_events();
            assert(!msm8996_voltage_init(&device,&voltage,"digital","analog"));
            assert(gets==old_gets&&device.refs==1&&digital.count==dig_count&&analog.count==ana_count);
        }
    }
    fresh();analog.online=false;
    for(i=1;i<=3;i++) {
        assert(msm8996_voltage_init(&device,&voltage,"digital","analog")==-EPROBE_DEFER);
        assert(gets==2*i&&put_count==2*i&&ready_checks==i);
        assert(!steps&&!wire_requests&&!device.refs&&!voltage.initialized);
        assert(!voltage.digital&&!voltage.analog&&!voltage.analog_uncertain&&!voltage.faulted);
    }
    analog.online=true;assert(!msm8996_voltage_init(&device,&voltage,"digital","analog"));
    assert(gets==8&&put_count==6&&device.refs==1&&digital.count==1&&analog.count==1);
    for(i=0;i<4;i++)assert(!msm8996_voltage_init(&device,&voltage,"digital","analog"));
    assert(gets==8&&device.refs==1&&digital.count==1&&analog.count==1&&ready_checks==4);
    fresh();analog.typed=false;
    assert(msm8996_voltage_init(&device,&voltage,"digital","analog")==-EOPNOTSUPP);
    assert(gets==2&&put_count==2&&!steps&&!device.refs&&!voltage.initialized);
    for(i=0;i<3;i++) {
        fresh();ready_error=(int[]){-ENODEV,-EBUSY,-EUCLEAN}[i];
        assert(msm8996_voltage_init(&device,&voltage,"digital","analog")==ready_error);
        assert(gets==2&&put_count==2&&!steps&&!device.refs&&!voltage.initialized&&!voltage.analog_uncertain);
    }
    fresh();assert(msm8996_voltage_init(&device,&voltage,"digital","defer")==-EPROBE_DEFER);
    assert(gets==2&&put_count==1&&!device.refs&&!voltage.initialized&&!steps);
    fresh();assert(msm8996_voltage_init(&device,&voltage,"defer","analog")==-EPROBE_DEFER);
    assert(gets==1&&!put_count&&!device.refs&&!voltage.initialized&&!steps);
    machine=false;device.of_node=NULL;assert(!msm8996_voltage_required(&device));
    machine=true;assert(msm8996_voltage_required(&device));machine=false;device.of_node=&device;
    assert(msm8996_voltage_required(&device));
}
static void concurrent_handoff_completed(void)
{
    /* Another qualified caller completed after our query and before our lock.
     * Its acknowledged request is represented only at this boundary.
     */
    voltage.analog_boot_load=false;analog.load_request=analog.physical_load=0;
}
static void test_qualified_handoff(void)
{
    unsigned i,n,before;struct clk_init_data init={0};
    struct clk_hw hw={.init=&init,.enabled=true,.prepared=true,.rate=19200000};
    struct msm8996_voltage_clock maps[]={{.hw=&hw}};
    fresh();assert(!msm8996_voltage_finish_handoff(&voltage)&&!queries&&!steps);
    reset(true);clear_events();
    assert(!msm8996_voltage_finish_handoff(&voltage)&&queries==1&&!steps&&voltage.analog_boot_load);
    handoff_done=true;assert(!msm8996_voltage_finish_handoff(&voltage)&&!steps&&voltage.analog_boot_load);
    voltage.ownership_known=true;voltage.faulted=true;
    assert(!msm8996_voltage_finish_handoff(&voltage)&&!steps&&voltage.analog_boot_load);
    voltage.faulted=false;voltage.analog_uncertain=true;
    assert(!msm8996_voltage_finish_handoff(&voltage)&&!steps&&voltage.analog_boot_load);
    voltage.analog_uncertain=false;assert(!msm8996_voltage_finish_handoff(&voltage));
    assert(!strcmp(trace,"lP")&&!voltage.analog_boot_load&&analog.load_request==0&&analog.physical_load==0);
    assert(analog.count==1&&voltage.analog_enabled);before=queries;clear_events();
    assert(!msm8996_voltage_finish_handoff(&voltage)&&queries==before&&!steps);
    assert(!msm8996_voltage_set(&voltage.vdd,MSM8996_VOLTAGE_LOW));
    assert(!strcmp(trace,"vluPK")&&analog.load_request==0&&analog.count==1);
    /* A failed or not-yet-complete CCF release must retain the source load.
     * The query stub models the public CCF completion boundary, not its engine.
     */
    reset(true);clear_events();release_error=-EIO;release_marks_done=true;
    msm8996_voltage_ownership(&device,maps,1,&voltage,&voltage);
    assert(releases==2&&warnings==2&&!handoff_done&&!steps&&voltage.analog_boot_load&&analog.load_request==10000);
    release_error=0;release_marks_done=false;
    msm8996_voltage_ownership(&device,maps,1,&voltage,&voltage);
    assert(!handoff_done&&!steps&&voltage.analog_boot_load&&analog.load_request==10000);
    release_marks_done=true;msm8996_voltage_ownership(&device,maps,1,&voltage,&voltage);
    assert(handoff_done&&!strcmp(trace,"lP")&&!voltage.analog_boot_load&&analog.load_request==0);
    reset(true);voltage.ownership_known=true;handoff_done=true;clear_events();
    lock_boundary=concurrent_handoff_completed;fail_at=1;
    assert(!msm8996_voltage_finish_handoff(&voltage)&&!steps&&!voltage.analog_boot_load);
    assert(!voltage.analog_uncertain&&!voltage.faulted&&analog.load_request==0);
    for(n=0;n<2;n++)for(i=1;i<=2;i++) {
        reset(true);voltage.ownership_known=true;handoff_done=true;clear_events();fail_at=i;partial=n;
        assert(msm8996_voltage_finish_handoff(&voltage)==-EIO);
        assert(!strcmp(trace,i==1?"ll":"lPl"));
        /* The failed call cached zero. Production must explicitly restore
         * 10mA even though quarantine rejects that restore's wire request.
         */
        assert(voltage.analog_boot_load&&analog.load_request==10000&&analog.quarantined);
        assert(voltage.analog_uncertain&&voltage.faulted);before=wire_requests;clear_events();
        assert(!msm8996_voltage_finish_handoff(&voltage)&&!steps&&voltage.analog_boot_load);
        assert(wire_requests==before&&analog.load_request==10000);assert_sticky();
    }
}
int main(void)
{
    test_digital_and_ownership();test_analog_transitions();test_init_and_defer();test_qualified_handoff();
    puts("PASS: production clock adapter digital rollback, every analog callback/typed ACK failure and sticky uncertainty");
    puts("PASS: typed readiness before bootstrap, repeated deferred/successful init without lease/count leaks");
    puts("PASS: qualified handoff load/ACK release, cached-load restoration on failure and no automatic recovery");
    return 0;
}
'''
host_flags = shlex.split(os.environ.get('HOSTCFLAGS', ''))


def run_adapter(code, expect_failure=False):
    with tempfile.TemporaryDirectory(prefix='dora-clock-voltage-') as directory:
        path = Path(directory)
        (path / 'test.c').write_text(code)
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                       ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', *host_flags,
                        '-I' + str(ROOT), str(path / 'test.c'), '-o', str(path / 'test')], check=True)
        result = subprocess.run([str(path / 'test')], capture_output=expect_failure, text=True)
        assert (result.returncode != 0) == expect_failure, 'adapter regression escaped checks'


run_adapter(harness)
if args.self_test:
    stripped_header = re.sub(r'^#include[^\n]*\n', '', header, flags=re.M)
    mutations = (
        ('typed preflight', 'ret = qcom_rpm_physical_check_ready(ana);',
         '(void)qcom_rpm_physical_check_ready; ret = 0;'),
        ('apply physical ACK', 'ret = qcom_rpm_physical_sync(voltage->analog, level != 0);', 'ret = 0;'),
        ('failed handoff cached load restoration', 'regulator_set_load(voltage->analog, 10000);', '(void)0;'),
        ('locked handoff idempotence', 'if (!voltage->analog_boot_load || !voltage->ownership_known ||',
         'if (!voltage->ownership_known ||'),
    )
    for label, before, after in mutations:
        assert before in stripped_header, label
        run_adapter(harness.replace(stripped_header, stripped_header.replace(before, after, 1), 1),
                    expect_failure=True)
        print('PASS: rejected', label, 'mutation')
print('PASS: final Sony GCC %d / MMCC %d fmax contracts, GPLL4 and SDCC2 source rates; mutation rejected' %
      (counts['gcc'], counts['mmcc']))

# Execute the actual postdivider decoder and read-only round callback. A
# zero-width single-bit GPLL0 divider used to round 1200 MHz instead of its
# observed 600 MHz, even though recalc decoded the same register correctly.
pll_source = (ROOT / 'drivers/clk/qcom/clk-alpha-pll.c').read_text()

def pll_function(name):
    match = re.search(r'static (?:unsigned long|long)\s+' + name + r'\([^;]*?\)\s*\{', pll_source, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (pll_source[end] == '{') - (pll_source[end] == '}')
        end += 1
    return pll_source[match.start():end] + '\n'

pll_test = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define CLK_SET_RATE_PARENT 1U
#define PLL_POST_DIV_SHIFT 8
#define PLL_POST_DIV_MASK(p) ((1U<<((p)->width+1))-1)
#define PLL_USER_CTL(p) ((p)->offset)
#define DIV_ROUND_UP_ULL(n,d) (((n)+(d)-1)/(d))
struct clk_hw { unsigned flags; struct clk_hw *parent; };
struct clk_alpha_pll_postdiv { struct clk_hw hw;unsigned width,offset;struct {void *regmap;} clkr; };
#define to_clk_alpha_pll_postdiv(hw) ((struct clk_alpha_pll_postdiv *)(hw))
static u32 control;
static int read_error;
static unsigned rounds;
static int fls(u32 value) {return value?32-__builtin_clz(value):0;}
static int regmap_read(void *map,unsigned address,u32 *value)
{ assert(map && !address);if(read_error)return read_error;*value=control;return 0; }
static unsigned clk_hw_get_flags(struct clk_hw *hw) {return hw->flags;}
static struct clk_hw *clk_hw_get_parent(struct clk_hw *hw) {return hw->parent;}
static long clk_hw_round_rate(struct clk_hw *hw,unsigned long rate)
{ assert(hw);rounds++;return rate; }
'''
pll_test += pll_function('clk_alpha_pll_postdiv_recalc_rate')
pll_test += pll_function('clk_alpha_pll_postdiv_round_ro_rate')
pll_test += r'''
int main(void)
{
    struct clk_alpha_pll_postdiv pll={.clkr.regmap=&control};
    struct clk_hw parent_hw={0};
    unsigned width,code;unsigned long parent;
    for(width=0;width<=3;width++) {
        pll.width=width;
        for(code=0;code<=(1U<<(width+1))-1;code++) {
            control=(code<<PLL_POST_DIV_SHIFT)|0x80000001U;
            parent=1200000000UL;
            assert(clk_alpha_pll_postdiv_round_ro_rate(&pll.hw,17,&parent)==
                   (long)clk_alpha_pll_postdiv_recalc_rate(&pll.hw,parent));
            assert(parent==1200000000UL && !rounds);
        }
    }
    pll.width=0;control=1U<<PLL_POST_DIV_SHIFT;parent=1200000000UL;
    assert(clk_alpha_pll_postdiv_round_ro_rate(&pll.hw,400000000,&parent)==600000000);
    read_error=-EIO;
    assert(clk_alpha_pll_postdiv_round_ro_rate(&pll.hw,600000000,&parent)==-EIO);
    assert(!clk_alpha_pll_postdiv_recalc_rate(&pll.hw,parent));read_error=0;
    pll.hw.flags=CLK_SET_RATE_PARENT;
    assert(clk_alpha_pll_postdiv_round_ro_rate(&pll.hw,600000000,&parent)==-EINVAL);
    pll.hw.parent=&parent_hw;
    assert(clk_alpha_pll_postdiv_round_ro_rate(&pll.hw,600000000,&parent)==600000000 && rounds==1);
    puts("PASS: actual read-only PLL divider round/recalc agreement, fixed GPLL0 600 MHz, read faults and parent policy");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='dora-pll-divider-') as directory:
    path=Path(directory);(path/'test.c').write_text(pll_test)
    subprocess.run(shlex.split(os.environ.get('HOSTCC','cc'))+
                   ['-std=gnu11','-O2','-Wall','-Wextra','-Werror',*host_flags,str(path/'test.c'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
