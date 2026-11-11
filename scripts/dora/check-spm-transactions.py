#!/usr/bin/env python3
"""Execute real SPM regulator/SAW APIs with fault-injected transports.

This proves software sequencing and exclusion, not physical CPR/SPM ownership,
autonomous AVS exclusion, power collapse safety, or board qualification.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'drivers/regulator/spm-regulator.c').read_text()
spm = (ROOT / 'drivers/soc/qcom/spm_devices.c').read_text()


def function(text, name):
    match = re.search(r'(?:static )?(?:inline )?(?:int|void|unsigned int|struct msm_spm_device \*)\s*' +
                      re.escape(name) + r'\([^;]*?\)\s*\{', text, re.S)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end] + '\n'


preamble = r'''
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sched.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define EPROBE_DEFER 517
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define roundup(a,b) (DIV_ROUND_UP(a,b)*(b))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define likely(x) (x)
#define unlikely(x) (x)
#define REGULATOR_MODE_NORMAL 2
#define REGULATOR_MODE_IDLE 4
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define dev_err(...) ((void)0)
struct mutex { pthread_mutex_t native; };
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->native)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->native)); }
struct regulator_desc { const char *name; unsigned n_voltages; };
struct platform_device { int dev; };
struct regmap { u8 reg[256]; };
struct regulator_dev { void *data; };
#define rdev_get_drvdata(r) ((r)->data)
struct arm_smccc_res { long a0; };
static struct regmap hw;
static unsigned steps,fail_at,spmi_writes,spm_writes,spm_reads,smcs,limit_calls,mode_calls;
static int fault_code,smc_result,spm_level,limit_min,limit_max;
static bool partial_failure,drop_write;
static unsigned mode_history[8];
static int transport(void) { sched_yield(); return ++steps==fail_at?fault_code:0; }
static int regmap_bulk_read(struct regmap *map,unsigned off,void *out,size_t len)
{ int rc=transport();assert(map==&hw && off+len<=sizeof(hw.reg));if(!rc)memcpy(out,&map->reg[off],len);return rc; }
static int regmap_bulk_write(struct regmap *map,unsigned off,const void *in,size_t len)
{ int rc=transport();assert(map==&hw && off+len<=sizeof(hw.reg));spmi_writes++;
  if((!rc || partial_failure) && !drop_write)memcpy(&map->reg[off],in,len);
  return rc; }
static int regmap_write(struct regmap *map,unsigned off,unsigned value)
{ u8 v=value;if(off==0x45){if(mode_calls<8)mode_history[mode_calls]=value;mode_calls++;}
  return regmap_bulk_write(map,off,&v,1); }
static void udelay(unsigned delay) { (void)delay; }
static int msm_spm_set_vdd(unsigned cpu,unsigned level)
{ int rc=transport();assert(!cpu);spm_writes++;if(!rc || partial_failure){spm_level=level;hw.reg[0x41]=level;}return rc; }
static int msm_spm_get_vdd(unsigned cpu)
{ int rc=transport();assert(!cpu);spm_reads++;return rc?rc:spm_level; }
static int msm_spm_avs_set_limit(unsigned cpu,unsigned lo,unsigned hi)
{ int rc=transport();assert(!cpu && lo<=hi);limit_calls++;if(!rc){limit_min=lo;limit_max=hi;}return rc; }
static int msm_spm_avs_enable(unsigned cpu) { assert(!cpu);return transport(); }
static int msm_spm_avs_disable(unsigned cpu) { assert(!cpu);return transport(); }
static void arm_smccc_smc(unsigned fid,unsigned mask,unsigned cmd,unsigned a,unsigned b,
 unsigned c,unsigned d,unsigned e,struct arm_smccc_res *res)
{ assert(fid==0xC4000020 && mask==3 && cmd==2 && !(a|b|c|d|e));smcs++;res->a0=smc_result; }
'''
production = source[source.index('struct voltage_range {'):source.index('static int qpnp_smps_check_type(')]
production = re.sub(r'static struct regulator_ops \w+ = \{.*?\n\};', '', production, flags=re.S)
production += function(source, 'qpnp_smps_init_voltage')

# Probe must reject malformed non-progressing steps before exposing either rdev.
probe = function(source, 'spm_regulator_probe')
assert probe.index('if (!vreg->max_step_uV)') < probe.index('regulator_register(')
assert probe.index('vreg->has_avs =') < probe.index('regulator_register(')
assert 'spm_failed' not in production and 'falling back' not in production
assert 'of_node_put(avs_node)' in function(source, 'spm_regulator_avs_register')

tests = r'''
static struct spm_vreg v;
static struct regulator_dev main_rdev={&v},avs_rdev={&v};
static unsigned selector;
static void reset(void)
{
 memset(&v,0,sizeof(v));memset(&hw,0,sizeof(hw));assert(!pthread_mutex_init(&v.lock.native,NULL));
 v.regmap=&hw;v.range=&fts2_range0;v.regulator_type=QPNP_TYPE_FTS2;v.max_step_uV=100000;
 v.step_rate=1000;v.last_set_uV=v.uV=800000;v.last_set_vlevel=v.vlevel=160;v.has_avs=true;
 v.rdesc.n_voltages=(v.range->max_uV-v.range->set_point_min_uV)/v.range->step_uV+1;
 v.online=true;v.recal_cluster_mask=3;v.rdesc.name="gang";v.avs_rdesc.name="limit";
 hw.reg[0x41]=160;spm_level=160;steps=fail_at=spmi_writes=spm_writes=spm_reads=smcs=mode_calls=limit_calls=0;
 fault_code=-EIO;smc_result=0;partial_failure=drop_write=false;
}
static void finish(void) { assert(!pthread_mutex_destroy(&v.lock.native)); }
static int set_uv(int uv) { return spm_regulator_set_voltage(&main_rdev,uv,uv,&selector); }
static void invariant_fault(void)
{
 unsigned before=steps;
 assert(v.quarantined);
 assert(set_uv(800000)==-EIO);
 assert(spm_regulator_enable(&main_rdev)==-EIO);
 assert(spm_regulator_disable(&main_rdev)==-EIO);
 assert(spm_regulator_is_enabled(&main_rdev)==-EIO);
 assert(spm_regulator_get_voltage(&main_rdev)==-EIO);
 assert(spm_regulator_get_mode(&main_rdev)==0);
 assert(spm_regulator_set_mode(&main_rdev,REGULATOR_MODE_NORMAL)==-EIO);
 assert(spm_regulator_avs_set_voltage(&avs_rdev,700000,900000,&selector)==-EIO);
 assert(spm_regulator_avs_get_voltage(&avs_rdev)==-EIO);
 assert(spm_regulator_avs_enable(&avs_rdev)==-EIO);
 assert(spm_regulator_avs_disable(&avs_rdev)==-EIO);
 assert(spm_regulator_avs_is_enabled(&avs_rdev)==-EIO);
 assert(steps==before); /* No implicit retry or quarantine release. */
}
static void *consumer(void *arg)
{
 uintptr_t which=(uintptr_t)arg;unsigned i;
 for(i=0;i<300;i++) {
  if(which==0)assert(!set_uv(i&1?800000:950000));
  if(which==1) {assert(!spm_regulator_set_mode(&main_rdev,i&1?REGULATOR_MODE_NORMAL:REGULATOR_MODE_IDLE));
               assert(spm_regulator_get_voltage(&main_rdev)>0);}
  if(which==2) {unsigned s;assert(!spm_regulator_avs_set_voltage(&avs_rdev,700001,1004999,&s));
               assert(!spm_regulator_avs_enable(&avs_rdev));assert(!spm_regulator_avs_disable(&avs_rdev));}
 }
 return NULL;
}
int main(void)
{
 const struct voltage_range *ranges[]={&fts2_range0,&fts2_range1,&fts2p5_range0,&fts2p5_range1,
   &fts426_range,&hfs430_range,&ult_hf_range0,&ult_hf_range1,&hf_range0,&hf_range1};
 unsigned n,i;int errors[]={-EPROBE_DEFER,-EIO,-ETIMEDOUT,-ENODEV,1};pthread_t threads[3];
 for(i=0;i<sizeof(ranges)/sizeof(ranges[0]);i++) {
  int uv;reset();v.range=ranges[i];v.regulator_type=i==4?QPNP_TYPE_FTS426:i==5?QPNP_TYPE_HFS430:
       (i==6 || i==7)?QPNP_TYPE_ULT_HF:QPNP_TYPE_FTS2;
  for(uv=v.range->set_point_min_uV;uv<=v.range->max_uV;uv+=v.range->step_uV)
   assert(spm_regulator_vlevel_to_uv(&v,spm_regulator_uv_to_vlevel(&v,uv))==uv);
  finish();
 }
 reset();assert(!set_uv(1050000));assert(spm_writes==3 && mode_calls==2 && smcs==1);
 assert(v.last_set_uV==1050000 && spm_level==210);assert(mode_history[0]==0x80 && mode_history[1]==0x40);
 n=steps;finish();
 /* Fail every read, mode write, intermediate voltage step and final mode restore.
    A transport failure may already have applied, so retry from the cache is blocked. */
 for(i=1;i<=n;i++) {reset();fail_at=i;partial_failure=true;assert(set_uv(1050000)==-EIO);invariant_fault();finish();}
 for(i=0;i<sizeof(errors)/sizeof(errors[0]);i++) {
  reset();fail_at=3;fault_code=errors[i];partial_failure=true;
  assert(set_uv(1050000)==(errors[i]<0?errors[i]:-EIO));assert(spmi_writes==1);
  /* Only the earlier PWM write may use direct SPMI; never a voltage fallback. */
  invariant_fault();finish();
 }
 reset();smc_result=-ETIMEDOUT;assert(set_uv(900000)==-ETIMEDOUT);invariant_fault();finish();
 reset();smc_result=1;assert(set_uv(900000)==-EIO);invariant_fault();finish();
 reset();v.max_step_uV=0;assert(set_uv(900000)==-EINVAL && !spm_writes && !mode_calls);finish();
 reset();v.step_rate=0;assert(set_uv(900000)==-EINVAL && !spm_writes);finish();
 reset();v.online=false;assert(!set_uv(900000));assert(!spm_writes && spm_regulator_get_voltage(&main_rdev)==800000);
 assert(!spm_regulator_enable(&main_rdev) && v.online && spm_level==180);
 assert(!spm_regulator_disable(&main_rdev) && !spm_regulator_is_enabled(&main_rdev));finish();
 reset();v.has_avs=false;assert(spm_regulator_get_voltage(&main_rdev)==800000 && !spm_reads);finish();
 reset();v.has_avs=false;hw.reg[0x41]=1;
 assert(spm_regulator_get_voltage(&main_rdev)==-ERANGE && v.last_set_uV==800000);invariant_fault();finish();
 reset();hw.reg[0x41]=1;assert(qpnp_smps_init_voltage(&v)==-ERANGE && !spm_writes);finish();
 reset();spm_level=1;assert(spm_regulator_get_voltage(&main_rdev)==-ERANGE);invariant_fault();finish();
 reset();assert(!spm_regulator_set_mode(&main_rdev,REGULATOR_MODE_NORMAL));mode_calls=0;
 assert(!set_uv(900000) && !mode_calls && v.mode==QPNP_LOGICAL_MODE_PWM);finish();
 reset();fail_at=1;assert(spm_regulator_set_mode(&main_rdev,REGULATOR_MODE_NORMAL)==-EIO);
 assert(v.mode==QPNP_LOGICAL_MODE_AUTO);invariant_fault();finish();
 reset();v.bypass_spm=true;assert(!set_uv(900000) && !spm_writes && v.last_set_uV==900000);finish();
 reset();v.bypass_spm=true;v.mode=QPNP_LOGICAL_MODE_PWM;drop_write=true;
 assert(set_uv(900000)==-EIO && !spm_writes);invariant_fault();finish();
 reset();fail_at=2;fault_code=-EPROBE_DEFER;assert(qpnp_smps_init_voltage(&v)==-EPROBE_DEFER);finish();
 reset();assert(!qpnp_smps_init_voltage(&v) && v.uV==800000);finish();
 reset();assert(!spm_regulator_avs_set_voltage(&avs_rdev,700001,804999,&selector));
 assert(v.avs_min_uV==705000 && v.avs_max_uV==800000 && limit_max==160 && limit_min==141);
 assert(spm_regulator_avs_set_voltage(&avs_rdev,800001,804999,&selector)==-EINVAL);
 assert(spm_regulator_list_voltage(&main_rdev,selector)==705000);
 assert(!spm_regulator_list_voltage(&main_rdev,v.rdesc.n_voltages));finish();
 for(i=0;i<3;i++) {
  reset();fail_at=1;
  if(i==0)assert(spm_regulator_avs_enable(&avs_rdev)==-EIO);
  if(i==1)assert(spm_regulator_avs_disable(&avs_rdev)==-EIO);
  if(i==2)assert(spm_regulator_avs_set_voltage(&avs_rdev,700000,900000,&selector)==-EIO);
  invariant_fault();finish();
 }
 reset();for(i=0;i<3;i++)assert(!pthread_create(&threads[i],NULL,consumer,(void *)(uintptr_t)i));
 for(i=0;i<3;i++)assert(!pthread_join(threads[i],NULL));
 assert(!v.quarantined && v.last_set_uV>=800000 && v.last_set_uV<=950000);finish();
 puts("PASS: production SPM transactions: main/AVS exclusion, partial/lost ACK, quarantine, modes, SMC, real readback, bounded steps, AVS floor, boot handoff");
 return 0;
}
'''

# Independently execute actual SAW endpoint APIs: failed probes are error
# pointers, not a positive-success code or a valid reg_data address.
api_preamble = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define EPROBE_DEFER 517
#define VDD_DEFAULT 0xDEADF00D
#define VDD_UNKNOWN 0xDEADF00E
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p)>=(uintptr_t)-4095)
#define __iomem
#define per_cpu(name,cpu) ((name)[cpu])
#define for_each_possible_cpu(cpu) for((cpu)=0;(cpu)<4;(cpu)++)
static unsigned nr_cpu_ids=4;
static bool cpu_possible(unsigned cpu) { return cpu<4; }
struct msm_spm_device { unsigned reg_data,cpu_vdd,mask; };
struct msm_spm_vdd_info { struct msm_spm_device *vctl_dev;uint32_t vlevel;int err; };
struct device_node { int unused; };
enum msm_spm_avs_irq { MSM_SPM_AVS_IRQ_MIN,MSM_SPM_AVS_IRQ_MAX };
static struct msm_spm_device *cpu_vctl_device[4];
static int drv_result,ipi_result,drv_calls,maps,unmaps,writes;
static int msm_spm_drv_set_vdd(unsigned *d,unsigned l) { assert(d);(void)l;drv_calls++;return drv_result; }
static int msm_spm_drv_get_vdd(unsigned *d) { assert(d);drv_calls++;return drv_result?drv_result:160; }
static int msm_spm_drv_get_avs_enable(unsigned *d) { assert(d);drv_calls++;return drv_result; }
static int msm_spm_drv_set_avs_enable(unsigned *d,bool en) { (void)en;return msm_spm_drv_get_avs_enable(d); }
static int msm_spm_drv_set_avs_limit(unsigned *d,unsigned l,unsigned h) { (void)l;(void)h;return msm_spm_drv_get_avs_enable(d); }
static int msm_spm_drv_set_avs_irq_enable(unsigned *d,enum msm_spm_avs_irq i,bool en) { (void)i;(void)en;return msm_spm_drv_get_avs_enable(d); }
static int msm_spm_drv_avs_clear_irq(unsigned *d,enum msm_spm_avs_irq i) { (void)i;return msm_spm_drv_get_avs_enable(d); }
static int smp_call_function_any(unsigned *mask,void (*fn)(void *),void *info,bool wait)
{ assert(mask && wait);if(ipi_result)return ipi_result;fn(info);return 0; }
static char mmio[256];
static void *of_iomap(struct device_node *node,unsigned index) { (void)node;assert(index<2);maps++;return mmio; }
static void writel_relaxed(unsigned value,void *base) { (void)value;assert(base>= (void *)mmio && base< (void *)(mmio+256));writes++; }
static void iounmap(void *base) { assert(base==mmio);unmaps++; }
static void mb(void) {}
static void udelay(unsigned delay) { (void)delay; }
'''
api_names = ['msm_spm_vctl_device', 'msm_spm_smp_set_vdd', 'msm_spm_probe_done',
             'msm_spm_set_vdd', 'msm_spm_get_vdd', 'msm_spm_is_avs_enabled',
             'msm_spm_avs_enable', 'msm_spm_avs_disable', 'msm_spm_avs_set_limit',
             'msm_spm_avs_enable_irq', 'msm_spm_avs_disable_irq', 'msm_spm_avs_clear_irq',
             'msm_spm_turn_on_cpu_rail']
api_production = ''.join(function(spm, name) for name in api_names)
api_tests = r'''
static void reject(unsigned cpu,int error)
{
 int before=drv_calls;
 assert(msm_spm_set_vdd(cpu,160)==error);
 assert(msm_spm_get_vdd(cpu)==error);
 assert(msm_spm_is_avs_enabled(cpu)==error);
 assert(msm_spm_avs_enable(cpu)==error);
 assert(msm_spm_avs_disable(cpu)==error);
 assert(msm_spm_avs_set_limit(cpu,140,180)==error);
 assert(msm_spm_avs_enable_irq(cpu,MSM_SPM_AVS_IRQ_MIN)==error);
 assert(msm_spm_avs_disable_irq(cpu,MSM_SPM_AVS_IRQ_MAX)==error);
 assert(msm_spm_avs_clear_irq(cpu,MSM_SPM_AVS_IRQ_MIN)==error);
 assert(drv_calls==before);
}
int main(void)
{
 struct msm_spm_device d={.cpu_vdd=VDD_DEFAULT};unsigned i;int errors[]={-EIO,-EPROBE_DEFER,-ENOMEM};
 reject(0,-EPROBE_DEFER);reject(4,-EINVAL);reject(~0U,-EINVAL);
 assert(msm_spm_probe_done()==-EPROBE_DEFER);
 for(i=0;i<3;i++) {cpu_vctl_device[0]=ERR_PTR(errors[i]);reject(0,errors[i]);assert(msm_spm_probe_done()==errors[i]);
 assert(msm_spm_turn_on_cpu_rail(NULL,160,0,0)==errors[i] && !maps);}
 for(i=0;i<4;i++)cpu_vctl_device[i]=&d;
 assert(!msm_spm_probe_done());
 assert(!msm_spm_set_vdd(0,160) && d.cpu_vdd==160);
 assert(msm_spm_get_vdd(0)==160);
 assert(!msm_spm_turn_on_cpu_rail(NULL,160,0,0) && !maps);
 drv_result=-ETIMEDOUT;assert(msm_spm_set_vdd(0,180)==-ETIMEDOUT && d.cpu_vdd==VDD_UNKNOWN);
 assert(msm_spm_turn_on_cpu_rail(NULL,160,0,0)==-EIO && !maps);
 drv_result=0;ipi_result=-ENXIO;assert(msm_spm_set_vdd(0,180)==-ENXIO && d.cpu_vdd==VDD_UNKNOWN);
 assert(msm_spm_turn_on_cpu_rail(NULL,160,-1,0)==-EINVAL);
 cpu_vctl_device[0]=NULL;assert(!msm_spm_turn_on_cpu_rail(NULL,160,0,0));
 assert(maps==2 && unmaps==2 && writes==3);
 puts("PASS: actual SAW API endpoint errors/deferred probes, CPU bounds, failed voltage cache, bootstrap MMIO lifetime");
 return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='dora-spm-transactions-') as tmp:
    for name, contents in [('regulator', preamble + production + tests),
                           ('saw', api_preamble + api_production + api_tests)]:
        c, binary = Path(tmp) / (name + '.c'), Path(tmp) / name
        c.write_text(contents)
        flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('SPM_SANITIZE') else []
        subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                       ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-pthread',
                        *flags, str(c), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)
