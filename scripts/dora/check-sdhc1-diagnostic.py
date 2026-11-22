#!/usr/bin/env python3
"""Verify isolated experimental SDHC1 DT selections against all four normal DTBs.

This verifies compiled data and source/build isolation only. It does not qualify
MMC activation, RPM ACKs, electrical behavior, partition access or suspend.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts/dora'
POWER = runpy.run_path(str(SCRIPTS / 'check-power-dt.py'))
STORAGE = runpy.run_path(str(SCRIPTS / 'check-storage-dt.py'))
CLOCK = runpy.run_path(str(SCRIPTS / 'check-clock-voltage-dt.py'))
VARIANTS = runpy.run_path(str(SCRIPTS / 'check-variants.py'))
CONFIG = runpy.run_path(str(SCRIPTS / 'check-config.py'))
PAIRS = (
    ('msm8996-v3.0-sony-xperia-tone-dora', 'v3.0-pmi8994'),
    ('msm8996-v3.0-pmi8996-sony-xperia-tone-dora', 'v3.0-pmi8996'),
    ('msm8996-sony-xperia-tone-dora', 'v3.1-pmi8994'),
    ('msm8996-v3.1-pmi8996-sony-xperia-tone-dora', 'v3.1-pmi8996'),
)
SD1 = '/soc/sdhci@7464900'
SD2 = '/soc/sdhci@74a4900'
UFS = '/soc/ufshc@624000'
MARKER = 'sony,sdhc1-diagnostic'
SUFFIX = '-sdhc1-diagnostic'
ALLOWED = {('/', 'model'), ('/', MARKER), (SD1, 'status')}


def model(variant):
    return ('Sony Xperia X Performance (experimental SDHC1 diagnostic: ' + variant + ')\0').encode()


def source_tokens(text):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    return re.findall(r'"(?:\\.|[^"\\])*"|[^\s]', text)


def expected_source(base, variant):
    return ('#include "../' + base + '.dts"\n/ { model = "' +
            model(variant).rstrip(b'\0').decode() + '"; ' + MARKER +
            '; }; &sdhc1 { status = "okay"; };')


def validate_sources(sources=None, qcom_makefile=None, default_build=None):
    if sources is None:
        sources = {base: (ROOT / 'arch/arm64/boot/dts/qcom/diagnostic' /
                         (base + SUFFIX + '.dts')).read_text() for base, _ in PAIRS}
    for base, variant in PAIRS:
        if source_tokens(sources[base]) != source_tokens(expected_source(base, variant)):
            raise ValueError('diagnostic must inherit its real normal base and override only model/marker/SDHC1: ' + base)
    if qcom_makefile is None:
        qcom_makefile = (ROOT / 'arch/arm64/boot/dts/qcom/Makefile').read_text()
    if 'diagnostic' in qcom_makefile or SUFFIX in qcom_makefile:
        raise ValueError('diagnostic is included in the default DTB Makefile')
    if re.search(r'\$\([^)]*(?:wildcard|shell)[^)]*\)', qcom_makefile):
        raise ValueError('default QCOM DTB list requires explicit isolation review')
    if default_build is None:
        default_build = (SCRIPTS / 'build.sh').read_text()
    match = re.search(r'^dtbs=\((.*?)^\)', default_build, re.M | re.S)
    expected = ['qcom/' + base + '.dtb' for base, _ in PAIRS]
    if not match or shlex.split(match[1], comments=True) != expected:
        raise ValueError('default build must select exactly the original four normal DTBs')
    # These files must not add a diagnostic directory or target to normal DT
    # traversal/image concatenation. Explicit %.dtb rules remain supported.
    for name in ('arch/arm64/boot/dts/Makefile', 'arch/arm64/boot/Makefile'):
        if 'diagnostic' in (ROOT / name).read_text():
            raise ValueError('diagnostic leaked into default DT/image traversal: ' + name)


def validate_config(config):
    required = '''MMC MMC_BLOCK MMC_SDHCI MMC_SDHCI_PLTFM MMC_SDHCI_MSM
        REGULATOR REGULATOR_QCOM_SMD_RPM REGULATOR_QCOM_RPM_PHYSICAL
        REGULATOR_QCOM_RPM_CORNER QCOM_SMD_RPM COMMON_CLK_QCOM
        MSM_GCC_8996 MSM_MMCC_8996 QCOM_BUS_SCALING'''.split()
    for name in required:
        if config.get(name) != 'y':
            raise ValueError('diagnostic requires built-in CONFIG_' + name)
    if config.get('OF_ALL_DTBS') == 'y':
        raise ValueError('OF_ALL_DTBS is outside the isolated four-DTB workflow')
    for key in ('BUILD_ARM64_APPENDED_DTB_IMAGE_NAMES', 'BUILD_ARM_APPENDED_DTB_IMAGE_NAMES'):
        if 'diagnostic' in config.get(key, ''):
            raise ValueError('diagnostic appears in an appended-image default')


def fdt_metadata(path):
    data = path.read_bytes()
    if len(data) < 40:
        raise ValueError('truncated FDT header')
    header = struct.unpack_from('>10I', data)
    if header[0] != 0xd00dfeed or header[1] != len(data):
        raise ValueError('invalid FDT header')
    offset, reservations = header[4], []
    if offset < 40 or offset % 8:
        raise ValueError('invalid FDT reservation map offset')
    while offset + 16 <= min(header[2], len(data)):
        address, size = struct.unpack_from('>QQ', data, offset)
        offset += 16
        if not address and not size:
            return {'version': header[5], 'last_compatible_version': header[6],
                    'boot_cpuid_phys': header[7], 'reservations': reservations}
        reservations.append((address, size))
    raise ValueError('unterminated FDT reservation map')


def validate_normal(nodes, config, variant):
    if MARKER in nodes['/']['props']:
        raise ValueError('normal DTB contains diagnostic marker')
    STORAGE['validate'](nodes)
    CLOCK['validate'](nodes, config, 'active')
    if VARIANTS['validate'](nodes, mode='active') != variant:
        raise ValueError('normal DTB filename does not match its SoC/PMIC variant')


def compare(normal, diagnostic, variant, normal_meta, diagnostic_meta):
    if normal_meta != diagnostic_meta:
        raise ValueError('FDT header semantics or reservation map changed')
    if normal.keys() != diagnostic.keys():
        raise ValueError('diagnostic added or removed a node')
    if normal[SD1]['props'].get('status') != b'disabled\0':
        raise ValueError('normal SDHC1 must remain disabled')
    if diagnostic[SD1]['props'].get('status') != b'okay\0':
        raise ValueError('diagnostic must enable exactly SDHC1')
    if MARKER in normal['/']['props'] or diagnostic['/']['props'].get(MARKER) != b'':
        raise ValueError('diagnostic root marker is missing, malformed or present in normal')
    if diagnostic['/']['props'].get('model') != model(variant):
        raise ValueError('diagnostic model must name its experimental variant')
    for tree in (normal, diagnostic):
        for path in (SD2, UFS):
            if tree[path]['props'].get('status') != b'disabled\0':
                raise ValueError('SDHC2 and UFS must remain disabled')
    changed = set()
    for path, node in normal.items():
        other = diagnostic[path]
        if node['name'] != other['name'] or [n['name'] for n in node['children']] != [n['name'] for n in other['children']]:
            raise ValueError('diagnostic changed node identity/order: ' + path)
        for key in node['props'].keys() | other['props'].keys():
            if node['props'].get(key) != other['props'].get(key):
                changed.add((path, key))
    if changed != ALLOWED:
        raise ValueError('unexpected full-tree difference: ' + repr(sorted(changed ^ ALLOWED)))
    return sorted(changed)


def reject(label, action):
    try:
        action()
    except (ValueError, KeyError, IndexError, StopIteration, struct.error):
        return
    raise ValueError('accepted unsafe diagnostic fixture: ' + label)


def builder_isolation_selftest():
    # Exercise the real builder's early guards only. The fixture has no valid
    # DTBs/toolchain and must be rejected before any make command can run.
    with tempfile.TemporaryDirectory(prefix='dora-diag-isolation-') as temporary:
        directory = Path(temporary)
        normal, diagnostic = directory / 'normal', directory / 'diagnostic'
        normal.mkdir(); diagnostic.mkdir()
        (normal / '.config').write_text('')
        (normal / 'arch').mkdir()
        (diagnostic / 'arch').symlink_to(normal / 'arch', target_is_directory=True)
        environment = dict(os.environ, OUT_DIR=str(normal), DIAGNOSTIC_OUT_DIR=str(diagnostic))
        result = subprocess.run(['bash', str(SCRIPTS / 'build-sdhc1-diagnostic.sh')],
                                env=environment, capture_output=True, text=True)
        if not result.returncode or 'must be fresh/empty' not in result.stderr:
            raise ValueError('builder accepted a descendant alias into normal artifacts')
        if list((normal / 'arch').iterdir()) or set(diagnostic.iterdir()) != {diagnostic / 'arch'}:
            raise ValueError('builder wrote artifacts before rejecting aliased output')


def selftest(normal, variant, metadata, config):
    diagnostic = copy.deepcopy(normal)
    diagnostic['/']['props']['model'] = model(variant)
    diagnostic['/']['props'][MARKER] = b''
    diagnostic[SD1]['props']['status'] = b'okay\0'
    compare(normal, diagnostic, variant, metadata, metadata)
    cases = [(SD2, 'status', b'okay\0'), (UFS, 'status', b'okay\0'),
             (SD1, 'status', b'disabled\0'), ('/', MARKER, b'yes\0'),
             ('/', 'model', b'normal\0'), ('/', 'qcom,msm-id', b'\0' * 8),
             (SD1, 'clocks', b'\0' * 8), (SD1, 'sony,require-rpm-physical-acks', None),
             ('/rpm-glink/rpm_requests/pm8994-regulators/l20', 'regulator-max-microvolt', b'\0' * 4),
             ('/firmware/android/fstab/vendor', 'dev', b'/dev/block/sda1\0'),
             ('/', 'unsafe-extra-property', b'')]
    phandle_node = next(path for path, node in normal.items() if 'phandle' in node['props'])
    cases.append((phandle_node, 'phandle', b'\xff' * 4))
    for path, prop, value in cases:
        bad = copy.deepcopy(diagnostic)
        if value is None:
            del bad[path]['props'][prop]
        else:
            bad[path]['props'][prop] = value
        reject(path + ':' + prop, lambda: compare(normal, bad, variant, metadata, metadata))
    bad = copy.deepcopy(diagnostic); bad['/unsafe-node'] = {'name': 'unsafe-node', 'props': {}, 'children': []}
    reject('added node', lambda: compare(normal, bad, variant, metadata, metadata))
    bad = copy.deepcopy(diagnostic); del bad[SD2]
    reject('removed node', lambda: compare(normal, bad, variant, metadata, metadata))
    bad = copy.deepcopy(normal); bad[SD1]['props']['status'] = b'okay\0'
    reject('normal activation', lambda: compare(bad, diagnostic, variant, metadata, metadata))
    bad_meta = dict(metadata, boot_cpuid_phys=metadata['boot_cpuid_phys'] + 1)
    reject('boot CPU', lambda: compare(normal, diagnostic, variant, metadata, bad_meta))
    bad_meta = dict(metadata, reservations=metadata['reservations'] + [(0x1000, 0x1000)])
    reject('reservation map', lambda: compare(normal, diagnostic, variant, metadata, bad_meta))
    reject('missing typed backend', lambda: validate_config(dict(config, REGULATOR_QCOM_RPM_PHYSICAL='n')))
    reject('wildcard DTB selection', lambda: validate_config(dict(config, OF_ALL_DTBS='y')))
    sources = {base: expected_source(base, value) for base, value in PAIRS}
    first = PAIRS[0][0]
    sources[first] += '\n&sdhc2 { status = "okay"; };'
    reject('extra source override', lambda: validate_sources(sources=sources))
    sources[first] = expected_source(PAIRS[1][0], PAIRS[0][1])
    reject('wrong base include', lambda: validate_sources(sources=sources))
    reject('default dtb-y leak', lambda: validate_sources(qcom_makefile='dtb-y += ' + first + SUFFIX + '.dtb'))
    script = (SCRIPTS / 'build.sh').read_text().replace('dtbs=(', 'dtbs=(\nqcom/diagnostic/' + first + SUFFIX + '.dtb', 1)
    reject('default appended-image leak', lambda: validate_sources(default_build=script))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dtbs', nargs='*', type=Path, help='four normal DTBs with --default-only')
    parser.add_argument('--default-only', action='store_true')
    parser.add_argument('--normal', action='append', type=Path, default=[])
    parser.add_argument('--diagnostic', action='append', type=Path, default=[])
    parser.add_argument('--config', type=Path, required=True, help='resolved normal O=/.config')
    parser.add_argument('--selftest', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        if args.default_only:
            if args.normal or args.diagnostic:
                raise ValueError('--default-only accepts only positional normal DTBs')
            normal_paths, diagnostic_paths = args.dtbs, []
        else:
            if args.dtbs:
                raise ValueError('full comparison requires --normal and --diagnostic pairs')
            normal_paths, diagnostic_paths = args.normal, args.diagnostic
            if len(diagnostic_paths) != 4:
                raise ValueError('full comparison requires all four diagnostic variants')
        normals = {path.name: path for path in normal_paths}
        diagnostics = {path.name: path for path in diagnostic_paths}
        if len(normal_paths) != 4 or set(normals) != {base + '.dtb' for base, _ in PAIRS}:
            raise ValueError('supply exactly the four corresponding normal DTB files')
        if diagnostic_paths and set(diagnostics) != {base + SUFFIX + '.dtb' for base, _ in PAIRS}:
            raise ValueError('diagnostic filenames do not match the four explicit variants')
        config = CONFIG['assignments'](args.config)
        validate_config(config); validate_sources()
        if args.selftest:
            builder_isolation_selftest()
        report = {'qualification': 'not_assessed', 'config_sha256': hashlib.sha256(args.config.read_bytes()).hexdigest(), 'variants': []}
        for base, variant in PAIRS:
            path = normals[base + '.dtb']
            normal, metadata = POWER['parse_fdt'](path), fdt_metadata(path)
            validate_normal(normal, config, variant)
            entry = {'variant': variant, 'normal_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            if diagnostic_paths:
                diag_path = diagnostics[base + SUFFIX + '.dtb']
                delta = compare(normal, POWER['parse_fdt'](diag_path), variant, metadata, fdt_metadata(diag_path))
                entry.update(diagnostic_sha256=hashlib.sha256(diag_path.read_bytes()).hexdigest(), changed_properties=delta)
            if args.selftest:
                selftest(normal, variant, metadata, config)
            report['variants'].append(entry)
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + '\n')
    except (ValueError, KeyError, IndexError, StopIteration, struct.error, OSError) as error:
        print('FAIL:', error)
        return 1
    if args.default_only:
        print('PASS: all four normal DTBs keep SDHC1/2 and UFS off; diagnostic sources are excluded from default builds')
    else:
        print('PASS: all four complete compiled trees differ only in root model/marker and SDHC1 disabled -> okay')
    if args.selftest:
        print('PASS: unsafe source/compiled/config/header/reservation/default-build and builder-alias fixtures rejected')
    print('DT data/build isolation only; source qualification and runtime MMC/power behavior are not established.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
