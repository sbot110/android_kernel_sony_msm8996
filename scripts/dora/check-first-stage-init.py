#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check opt-in LOS21 first-stage DTs and execute real GPT/PARTNAME code.

The first-stage metadata follows the supplied working Android14 tree and
LineageOS android_system_core lineage-21.0: GetBootDevices() reads the DT
boot_devices property; GetBlockDeviceSymlinks() recognizes literal "any".
The source can be reviewed at init/devices.cpp and fs_mgr/libfstab/fstab.cpp.
Kernel block reads and uevent allocation are modeled boundaries. This test
does not access an eMMC, mount a filesystem, execute init or qualify boot.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import runpy
import shlex
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts/dora'
DIAG = runpy.run_path(str(SCRIPTS / 'check-sdhc1-diagnostic.py'))
CPU = runpy.run_path(str(SCRIPTS / 'check-cpu-dt.py'))
DT = runpy.run_path(str(SCRIPTS / 'check-dt.py'))
EARLY = runpy.run_path(str(SCRIPTS / 'check-early-boot.py'))
DISPLAY = runpy.run_path(str(SCRIPTS / 'check-display-dt.py'))
PARSE = DIAG['POWER']['parse_fdt']
SUFFIX = '-lineage21-boot'
MARKER = 'sony,lineage21-boot'
SD1 = DIAG['SD1']
# Actual attached working image order, deliberately separate from normal order.
PAIRS = (
    ('msm8996-v3.0-sony-xperia-tone-dora', 'v3.0-pmi8994'),
    ('msm8996-sony-xperia-tone-dora', 'v3.1-pmi8994'),
    ('msm8996-v3.0-pmi8996-sony-xperia-tone-dora', 'v3.0-pmi8996'),
    ('msm8996-v3.1-pmi8996-sony-xperia-tone-dora', 'v3.1-pmi8996'),
)
ANDROID = '/firmware/android'
DEVICES = {'system': '/dev/block/by-name/system', 'vendor': '/dev/block/by-name/oem'}
ALLOWED = {('/', 'model'), ('/', MARKER), (SD1, 'status'),
           (ANDROID, 'boot_devices'),
           (ANDROID + '/fstab/system', 'dev'), (ANDROID + '/fstab/vendor', 'dev')}


def require(value, message):
    if not value:
        raise ValueError(message)


def model(variant):
    return ('Sony Xperia X Performance (LOS21 offline SDHC1 boot: ' + variant + ')\0').encode()


def validate_sources(sources=None, common=None, makefile=None):
    DIAG['validate_sources']()
    folder = ROOT / 'arch/arm64/boot/dts/qcom/boot'
    if sources is None:
        sources = {base: (folder / (base + SUFFIX + '.dts')).read_text() for base, _ in PAIRS}
    if common is None:
        common = (folder / 'msm8996-sony-xperia-tone-lineage21-boot.dtsi').read_text()
    for base, variant in PAIRS:
        expected = ('#include "../diagnostic/' + base + '-sdhc1-diagnostic.dts"\n'
                    '#include "msm8996-sony-xperia-tone-lineage21-boot.dtsi"\n'
                    '/ { model = "' + model(variant).rstrip(b'\0').decode() + '"; };')
        require(DIAG['source_tokens'](sources[base]) == DIAG['source_tokens'](expected),
                'boot source must inherit the exact qualified selection: ' + base)
    expected_common = '''/ { /delete-property/ sony,sdhc1-diagnostic; sony,lineage21-boot; };
        &{/firmware/android} { boot_devices = "any"; };
        &{/firmware/android/fstab/system} { dev = "/dev/block/by-name/system"; };
        &{/firmware/android/fstab/vendor} { dev = "/dev/block/by-name/oem"; };'''
    require(DIAG['source_tokens'](common) == DIAG['source_tokens'](expected_common),
            'unexpected activation/security/first-stage metadata in boot overlay')
    if makefile is None:
        makefile = (ROOT / 'arch/arm64/boot/dts/qcom/Makefile').read_text()
    boot_targets = []
    for line in re.sub(r'\\\n[ \t]*', ' ', makefile).splitlines():
        if SUFFIX in line:
            match = re.fullmatch(r'targets\s*\+=\s*(.*)', line)
            require(match is not None, 'boot trees must remain explicit targets outside the normal DTB list')
            boot_targets.extend(shlex.split(match[1]))
    require(boot_targets == ['boot/' + base + SUFFIX + '.dtb' for base, _ in PAIRS],
            'explicit boot targets/order changed')
    require('BOOT_OUT_DIR' not in (SCRIPTS / 'build.sh').read_text() and
            SUFFIX not in (SCRIPTS / 'build.sh').read_text(), 'boot workflow leaked into normal build')


def validate_pair(normal, boot, variant, normal_meta=None, boot_meta=None):
    require(normal.keys() == boot.keys(), 'boot selection added/removed a node')
    require(normal_meta == boot_meta, 'boot selection changed reservations/header semantics')
    require(normal[SD1]['props'].get('status') == b'disabled\0' and
            boot[SD1]['props'].get('status') == b'okay\0', 'SDHC1 activation policy changed')
    require(MARKER not in normal['/']['props'] and boot['/']['props'].get(MARKER) == b'',
            'missing/malformed boot marker or marker in normal tree')
    require('sony,sdhc1-diagnostic' not in boot['/']['props'], 'boot and diagnostic selections mixed')
    require(boot['/']['props'].get('model') == model(variant), 'wrong boot model/variant')
    require(boot[ANDROID]['props'].get('boot_devices') == b'any\0',
            'LineageOS first-stage generic by-name policy missing')
    for name, device in DEVICES.items():
        props = boot[ANDROID + '/fstab/' + name]['props']
        require(props.get('dev') == device.encode() + b'\0', 'incorrect generic first-stage path: ' + name)
        require(props.get('type') == (b'ext4\0' if name == 'system' else b'squashfs\0'),
                'incorrect first-stage filesystem: ' + name)
        require(props.get('mnt_flags') == b'ro,barrier=1\0' and props.get('fsmgr_flags') == b'wait\0',
                'working read-only mount/security metadata changed: ' + name)
    changed = set()
    for path, node in normal.items():
        other = boot[path]
        require(node['name'] == other['name'] and
                [n['name'] for n in node['children']] == [n['name'] for n in other['children']],
                'boot selection changed node identity/order: ' + path)
        changed.update((path, key) for key in node['props'].keys() | other['props'].keys()
                       if node['props'].get(key) != other['props'].get(key))
    require(changed == ALLOWED, 'unexpected full-tree delta: ' + repr(sorted(changed ^ ALLOWED)))
    # Full-tree equality outside the six explicit changes transfers all normal
    # hardware guards. Still run the CPU guard directly on the boot selection.
    CPU['validate'](boot)
    return sorted(changed)


def fixture(normal, variant):
    boot = copy.deepcopy(normal)
    boot['/']['props'].update({'model': model(variant), MARKER: b''})
    boot[SD1]['props']['status'] = b'okay\0'
    boot[ANDROID]['props']['boot_devices'] = b'any\0'
    for name, device in DEVICES.items():
        boot[ANDROID + '/fstab/' + name]['props']['dev'] = device.encode() + b'\0'
    return boot


def validate_boot_tree(path, config=None):
    """Validate one real boot DTB for the image assembler, without executing it.

    config is the actual resolved .config Path or its assignments dictionary.
    Omitting it fails closed: a DTB alone cannot qualify built-in dependencies.
    The paired workflow separately proves the exact delta from its normal DTB.
    """
    require(config is not None, 'actual resolved .config required for a boot DTB')
    values = config if isinstance(config, dict) else DIAG['CONFIG']['assignments'](Path(config))
    DIAG['validate_config'](values)
    validate_sources()
    path = Path(path)
    boot = PARSE(path)
    require(boot['/']['props'].get(MARKER) == b'', 'not an opt-in LOS21 boot DTB')
    normal = copy.deepcopy(boot)
    normal['/']['props'].pop(MARKER)
    normal['/']['props']['model'] = b'Sony Xperia X Performance\0'
    normal[SD1]['props']['status'] = b'disabled\0'
    normal[ANDROID]['props'].pop('boot_devices', None)
    for name, partition in (('system', 'system'), ('vendor', 'oem')):
        normal[ANDROID + '/fstab/' + name]['props']['dev'] = (
            '/dev/block/platform/soc/7464900.sdhci/by-name/' + partition + '\0').encode()
    variant = DIAG['VARIANTS']['validate'](normal, mode='active')
    require(variant in dict(PAIRS).values(), 'unexpected Sony boot selection tuple')
    DIAG['validate_normal'](normal, values, variant)
    DT['validate'](normal)
    text = '\n'.join('CONFIG_' + key + '=' + value if value != 'n' else
                     '# CONFIG_' + key + ' is not set' for key, value in values.items()) + '\n'
    EARLY['validate'](normal, text)
    DISPLAY['validate'](boot, require_gated=True)
    validate_pair(normal, boot, variant)
    return dict(variant=variant, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                selection_tuples={key: list(DIAG['STORAGE']['cells'](boot['/']['props'][key]))
                                  for key in ('qcom,msm-id', 'qcom,board-id', 'qcom,pmic-id')},
                sdhc1='enabled in opt-in tree; physical probe unverified',
                first_stage_devices=DEVICES, boot_devices='any',
                hardware_verified=False, boot_test_ready=False)


def negative(label, action):
    try:
        action()
    except (ValueError, KeyError, IndexError, struct.error):
        return
    raise ValueError('accepted unsafe first-stage fixture: ' + label)


def source_selftest():
    folder = ROOT / 'arch/arm64/boot/dts/qcom/boot'
    common = (folder / 'msm8996-sony-xperia-tone-lineage21-boot.dtsi').read_text()
    makefile = (ROOT / 'arch/arm64/boot/dts/qcom/Makefile').read_text()
    negative('missing generic by-name policy', lambda: validate_sources(common=common.replace('"any"', '"wrong"')))
    negative('renamed OEM partition', lambda: validate_sources(common=common.replace('/by-name/oem', '/by-name/vendor')))
    negative('boot targets in defaults', lambda: validate_sources(makefile=makefile.replace(
        'targets += boot/', 'always += boot/')))
    sources = {base: (folder / (base + SUFFIX + '.dts')).read_text() for base, _ in PAIRS}
    sources[PAIRS[0][0]] += '\n&sdhc2 { status = "okay"; };\n'
    negative('extra source activation', lambda: validate_sources(sources=sources))
    # Exercise the actual shell builder's early guards before any make runs.
    with tempfile.TemporaryDirectory(prefix='dora-boot-isolation-') as temporary:
        normal, output = Path(temporary) / 'normal', Path(temporary) / 'boot'
        normal.mkdir(); output.mkdir(); (normal / 'arch').mkdir()
        (normal / '.config').write_text('')
        (output / 'arch').symlink_to(normal / 'arch', target_is_directory=True)
        env = dict(os.environ, OUT_DIR=str(normal), BOOT_OUT_DIR=str(output))
        result = subprocess.run(['bash', str(SCRIPTS / 'build-boot-dtbs.sh')], env=env,
                                capture_output=True, text=True)
        require(result.returncode and 'fresh/empty' in result.stderr,
                'boot builder accepted descendant alias into normal artifacts')
        require(not list((normal / 'arch').iterdir()) and set(output.iterdir()) == {output / 'arch'},
                'boot builder wrote before rejecting aliased output')
    print('PASS: four unsafe boot-source/default mutations and real builder alias rejected')
    return 5


def tree_selftest(normal, variant, metadata):
    boot = fixture(normal, variant)
    validate_pair(normal, boot, variant, metadata, metadata)
    cases = [(ANDROID, 'boot_devices', b'wrong\0'),
             (ANDROID + '/fstab/vendor', 'dev', b'/dev/block/by-name/vendor\0'),
             (ANDROID + '/fstab/system', 'fsmgr_flags', b'wait,avb=disabled\0'),
             (SD1, 'status', b'disabled\0'),
             (DIAG['SD2'], 'status', b'okay\0'), (DIAG['UFS'], 'status', b'okay\0'),
             ('/soc/cpr3-ctrl@99e8000', 'status', b'okay\0'),
             ('/chosen', 'bootargs', b'maxcpus=2 enforcing=0\0'),
             ('/', MARKER, b'yes\0'), ('/', 'qcom,pmic-id', b'\0' * 16),
             ('/reserved-memory/ramoops@a7f00000', 'reg', b'\0' * 16)]
    for path, key, value in cases:
        bad = copy.deepcopy(boot)
        bad[path]['props'][key] = value
        negative(path + ':' + key, lambda: validate_pair(normal, bad, variant, metadata, metadata))
    return len(cases)


def function(source, name):
    match = re.search(r'^(?:static\s+)?(?:inline\s+)?int\s+' + re.escape(name) +
                      r'\([^;]*?\)\s*\{', source, re.M | re.S)
    require(match, 'cannot extract production function ' + name)
    pos, depth = match.end(), 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[match.start():pos] + '\n'


PREAMBLE = r'''
#include <assert.h>
#include <ctype.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t efi_guid_t;
#define NULL_GUID 0ULL
#define PARTITION_LINUX_RAID_GUID 2ULL
#define ADDPART_FLAG_RAID 1
#define PAGE_SIZE 4096
#define strlcat test_strlcat
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define le32_to_cpu(x) (x)
#define le64_to_cpu(x) (x)
#define pr_debug(...) ((void)0)
#define kfree free
#define dev_to_part(d) ((d)->part)
struct partition_meta_info { char uuid[37]; u8 volname[64]; };
struct disk_part { struct partition_meta_info info; bool has_info; unsigned flags; u64 start,size; };
struct hd_struct { unsigned partno; struct partition_meta_info *info; };
struct device { struct hd_struct *part; };
struct kobj_uevent_env { char lines[8][96]; unsigned count; };
struct block_device { unsigned block_bytes; u64 last; };
struct parsed_partitions { struct block_device *bdev; unsigned limit; struct disk_part parts[8]; char pp_buf[PAGE_SIZE]; };
typedef struct { u32 num_partition_entries; } gpt_header;
typedef struct { efi_guid_t partition_type_guid,unique_partition_guid; u64 starting_lba,ending_lba; uint16_t partition_name[36]; } gpt_entry;
static gpt_entry input[4];
static unsigned input_count;
static bool valid_header;
static unsigned bdev_logical_block_size(struct block_device *d) { return d->block_bytes; }
static u64 last_lba(struct block_device *d) { return d->last; }
static int efi_guidcmp(efi_guid_t a,efi_guid_t b) { return a!=b; }
static void efi_guid_to_str(efi_guid_t *g,char *s) { (void)g;strcpy(s,"test-uuid"); }
static void put_partition(struct parsed_partitions *s,unsigned i,u64 start,u64 size)
{ assert(i<s->limit && i<ARRAY_SIZE(s->parts));s->parts[i].start=start;s->parts[i].size=size; }
static int find_valid_gpt(struct parsed_partitions *s,gpt_header **g,gpt_entry **e)
{
 (void)s;*g=NULL;*e=NULL;if(!valid_header)return 0;
 *g=calloc(1,sizeof(**g));*e=calloc(input_count,sizeof(**e));assert(*g&&*e);
 (*g)->num_partition_entries=input_count;memcpy(*e,input,input_count*sizeof(**e));return 1;
}
static size_t strlcat(char *dst,const char *src,size_t size)
{ size_t n=strlen(dst),m=strlen(src);assert(n+m<size);memcpy(dst+n,src,m+1);return n+m; }
static int add_uevent_var(struct kobj_uevent_env *e,const char *fmt,...)
{ va_list a;assert(e->count<8);va_start(a,fmt);int n=vsnprintf(e->lines[e->count++],96,fmt,a);va_end(a);assert(n>=0&&n<96);return 0; }
'''

HARNESS = r'''
static void label(gpt_entry *e,const char *s)
{ unsigned i;for(i=0;i<36&&s[i];i++)e->partition_name[i]=(unsigned char)s[i]; }
static void event(struct partition_meta_info *info,unsigned partno,const char *name)
{
 struct hd_struct p={partno,info};struct device dev={&p};struct kobj_uevent_env env={0};
 char expected[96];assert(!part_uevent(&dev,&env));snprintf(expected,sizeof(expected),"PARTN=%u",partno);
 assert(!strcmp(env.lines[0],expected));assert(env.count==(name?2U:1U));
 if(name){snprintf(expected,sizeof(expected),"PARTNAME=%s",name);assert(!strcmp(env.lines[1],expected));}
}
int main(void)
{
 struct block_device dev={512,1023};struct parsed_partitions state={.bdev=&dev,.limit=8};
 gpt_entry p={.partition_type_guid=1,.starting_lba=100,.ending_lba=200};
 assert(is_pte_valid(&p,dev.last));p.ending_lba=100;assert(is_pte_valid(&p,dev.last));
 p.ending_lba=98;assert(!is_pte_valid(&p,dev.last));
 p.ending_lba=1024;assert(!is_pte_valid(&p,dev.last));
 p.ending_lba=100;p.starting_lba=1024;assert(!is_pte_valid(&p,dev.last));
 p.starting_lba=100;p.partition_type_guid=0;assert(!is_pte_valid(&p,dev.last));
 assert(!efi_partition(&state));assert(!state.parts[1].has_info);
 valid_header=true;input_count=4;
 input[0]=(gpt_entry){.partition_type_guid=1,.starting_lba=40,.ending_lba=139};label(&input[0],"system");
 input[1]=(gpt_entry){.partition_type_guid=1,.starting_lba=140,.ending_lba=199};label(&input[1],"oem");
 input[2]=(gpt_entry){.partition_type_guid=1,.starting_lba=300,.ending_lba=298};label(&input[2],"broken");
 input[3]=(gpt_entry){.partition_type_guid=2,.starting_lba=400,.ending_lba=439};label(&input[3],"userdata");
 assert(efi_partition(&state)==1);assert(state.parts[1].has_info&&state.parts[2].has_info);
 assert(state.parts[1].start==40&&state.parts[1].size==100);assert(state.parts[2].start==140&&state.parts[2].size==60);
 assert(!state.parts[3].has_info&&!state.parts[3].start&&!state.parts[3].size);
 assert(state.parts[4].has_info&&state.parts[4].flags==ADDPART_FLAG_RAID);
 assert(!strcmp((char *)state.parts[1].info.volname,"system"));assert(!strcmp((char *)state.parts[2].info.volname,"oem"));
 /* Production copied label storage remains valid after GPT backing memory was freed. */
 event(&state.parts[1].info,1,"system");event(&state.parts[2].info,2,"oem");event(NULL,3,NULL);
 struct partition_meta_info empty={0};event(&empty,4,NULL);
 memset(&state.parts,0,sizeof(state.parts));state.limit=2;dev.block_bytes=4096;
 assert(efi_partition(&state)==1&&state.parts[1].size==800&&!state.parts[2].has_info);
 memset(&state.parts,0,sizeof(state.parts));state.limit=8;input_count=1;
 for(unsigned i=0;i<36;i++)input[0].partition_name[i]='A';
 assert(efi_partition(&state)==1&&strlen((char *)state.parts[1].info.volname)==36);
 assert(state.parts[1].info.volname[36]==0);event(&state.parts[1].info,1,"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
 puts("PASS: production GPT range validation, bounded label copy, partition lifetime and PARTNAME/PARTN uevents");
 puts("Host GPT-read/uevent boundaries only; no partition discovery, filesystem mount or Android boot verified.");
 return 0;
}
'''


def production_tests(self_test):
    efi = (ROOT / 'block/partitions/efi.c').read_text()
    generic = (ROOT / 'block/partition-generic.c').read_text()
    functions = function(efi, 'is_pte_valid') + function(efi, 'efi_partition') + function(generic, 'part_uevent')
    mutations = [
        ('reversed-range', 'le64_to_cpu(pte->ending_lba) < le64_to_cpu(pte->starting_lba)', 'false'),
        ('missing-gpt-metadata', 'state->parts[i + 1].has_info = true;', 'state->parts[i + 1].has_info = false;'),
        ('missing-partname', 'if (part->info && part->info->volname[0])', 'if (false)'),
    ]
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('FIRST_STAGE_SANITIZE') else []
    with tempfile.TemporaryDirectory(prefix='dora-first-stage-') as temporary:
        folder = Path(temporary)
        for name, text, expected in [('production', functions, True)] + ([
                (name, functions.replace(before, after, 1), False) for name, before, after in mutations
                if before in functions] if self_test else []):
            source, binary = folder / (name + '.c'), folder / name
            source.write_text(PREAMBLE + text + HARNESS)
            subprocess.run(shlex.split(os.environ.get('HOSTCC', 'cc')) +
                           ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', *flags,
                            str(source), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            require((result.returncode == 0) == expected, name + ': ' + result.stdout + result.stderr)
            if expected:
                print(result.stdout, end='')
    if self_test:
        require(all(before in functions for _, before, _ in mutations), 'production mutation anchor changed')
        print('PASS: %d unsafe GPT/partition-event source mutations rejected' % len(mutations))
    return len(mutations) if self_test else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--normal', type=Path, action='append', default=[])
    parser.add_argument('--boot', type=Path, action='append', default=[])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--ramdisk', type=Path, help='provided existing ramdisk, read only; never copied into evidence')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    validate_sources()
    rejected = production_tests(args.self_test)
    if args.self_test:
        rejected += source_selftest()
    require(len(args.normal) == len(args.boot) and len(args.boot) in (0, 4), 'supply four matching normal/boot trees')
    if args.boot:
        require(args.config is not None, 'actual .config required for compiled boot selections')
    config = DIAG['CONFIG']['assignments'](args.config) if args.config else None
    records = []
    for (base, variant), normal_path, boot_path in zip(PAIRS, args.normal, args.boot):
        require(normal_path.name == base + '.dtb' and boot_path.name == base + SUFFIX + '.dtb',
                'boot DTB order must match actual working Sony selection order')
        normal, boot = PARSE(normal_path), PARSE(boot_path)
        DIAG['validate_normal'](normal, config, variant)
        DT['validate'](normal)
        nm, bm = DIAG['fdt_metadata'](normal_path), DIAG['fdt_metadata'](boot_path)
        delta = validate_pair(normal, boot, variant, nm, bm)
        validate_boot_tree(boot_path, args.config)
        if args.self_test:
            rejected += tree_selftest(normal, variant, nm)
        records.append(dict(variant=variant, filename=boot_path.name, sha256=hashlib.sha256(boot_path.read_bytes()).hexdigest(),
                            selection_tuples={key: list(DIAG['STORAGE']['cells'](boot['/']['props'][key]))
                                              for key in ('qcom,msm-id', 'qcom,board-id', 'qcom,pmic-id')},
                            exact_normal_delta=delta))
    ramdisk = None
    if args.ramdisk:
        require(args.config is not None, 'actual config required for ramdisk analysis')
        inspect = runpy.run_path(str(SCRIPTS / 'check-boot-image.py'))['ramdisk_info']
        ramdisk = inspect(args.ramdisk.read_bytes(), args.config.read_text())
        require(ramdisk.get('android14_dora_first_stage_candidate'), 'ramdisk is not an identified Android14 Dora first-stage candidate')
        require(not ramdisk['init']['elf']['has_dynamic_interpreter'], 'ramdisk init depends on unavailable dynamic interpreter')
    result = dict(status='SOFTWARE_CHECKED / HARDWARE_UNVERIFIED', boot_test_ready=False, hardware_verified=False,
                  normal_artifacts_modified=False, production_functions=3, rejected_unsafe_mutations=rejected,
                  boot_dtb_order=records, first_stage_device_policy='LineageOS21 boot_devices=any, generic system/oem by-name',
                  ramdisk=ramdisk, actual_emmc_gpt='UNVERIFIED', actual_first_stage_mount='UNVERIFIED')
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + '\n')
    if records:
        print('PASS: four isolated LOS21 DTBs, exact full-tree delta, original hardware gates and working-image selection order')
    print('First-stage metadata checks do not prove loader acceptance, eMMC detection, SELinux policy or Android boot.')


if __name__ == '__main__':
    main()
