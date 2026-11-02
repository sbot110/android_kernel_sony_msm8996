#!/usr/bin/env python3
"""Compare compiled Dora bus hardware with the final Sony MSM8996 DT.

Clock IDs are translated to native providers. This checks hardware description
and reachability, not RPM acknowledgements, bandwidth or storage at runtime.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import runpy
import struct

ROOT = Path(__file__).resolve().parents[2]
parse_fdt = runpy.run_path(str(ROOT / 'scripts/dora/check-power-dt.py'))['parse_fdt']


def cells(value):
    if len(value) % 4:
        raise ValueError('unaligned cell array')
    return list(struct.unpack('>%dI' % (len(value) // 4), value))


def text(value):
    return value.rstrip(b'\0').decode()


def compatible(node, value):
    return value.encode() in node['props'].get('compatible', b'').split(b'\0')


def one(nodes, predicate):
    found = [n for n in nodes.values() if predicate(n)]
    if len(found) != 1:
        raise ValueError('expected one provider/client, got %d' % len(found))
    return found[0]


def handles(nodes):
    return {cells(n['props']['phandle'])[0]: n for n in nodes.values()
            if 'phandle' in n['props']}


def enabled(nodes, node):
    while True:
        if node['props'].get('status', b'okay\0') not in (b'okay\0', b'ok\0'):
            return False
        if node['path'] == '/':
            return True
        node = nodes[node['path'].rsplit('/', 1)[0] or '/']


def project(nodes, old_clock_ids=None):
    bus = one(nodes, lambda n: compatible(n, 'qcom,msm-bus-device'))
    h = handles(nodes)
    result = {}
    for node in nodes.values():
        if node != bus and not node['path'].startswith(bus['path'] + '/'):
            continue
        props = {}
        for key, value in node['props'].items():
            if key in ('phandle', 'linux,phandle', 'qcom,rpm') or key.startswith('coresight-'):
                continue
            if key in ('qcom,connections', 'qcom,blacklist', 'qcom,bus-dev'):
                props[key] = [h[v]['path'][len(bus['path']):] for v in cells(value)]
            elif key.endswith('-supply'):
                provider = h[cells(value)[0]]
                props[key] = text(provider['props']['regulator-name'])
                if old_clock_ids is None:
                    if not compatible(provider, 'qcom,genpd-regulator') or not enabled(nodes, provider):
                        raise ValueError('missing real genpd supply: ' + key)
                    domain = cells(provider['props']['power-domains'])
                    controller = h[domain[0]]
                    if not enabled(nodes, controller) or len(domain) != 2:
                        raise ValueError('invalid native domain: ' + key)
            elif key == 'clocks':
                values = cells(value)
                if len(values) % 2:
                    raise ValueError('expected one-cell clocks')
                clocks = []
                for phandle, index in zip(values[::2], values[1::2]):
                    if old_clock_ids is not None:
                        clocks.append(old_clock_ids[str(index)])
                    else:
                        provider = h[phandle]
                        if not enabled(nodes, provider) or cells(provider['props']['#clock-cells']) != [1]:
                            raise ValueError('unavailable clock provider')
                        clocks.append(text(provider['props']['compatible']) + ':' + str(index))
                props[key] = clocks
            else:
                props[key] = value.hex()
        result[node['path'][len(bus['path']):] or '/'] = props
    return result


def ufs_project(nodes):
    host = one(nodes, lambda n: n['path'].endswith('/ufshc@624000'))
    return {k: v.hex() for k, v in host['props'].items()
            if k.startswith('qcom,msm-bus,') or k == 'qcom,bus-vector-names'}


def validate(nodes, expected):
    actual = project(nodes)
    wanted = expected['topology']
    changed = [key for key in sorted(set(actual) | set(wanted))
               if actual.get(key) != wanted.get(key)]
    if changed:
        raise ValueError('Sony bus hardware differs: ' + ', '.join(changed))
    bus = one(nodes, lambda n: compatible(n, 'qcom,msm-bus-device'))
    host = one(nodes, lambda n: n['path'].endswith('/ufshc@624000'))
    h = handles(nodes)
    if not enabled(nodes, bus):
        raise ValueError('bus provider disabled')
    if host['props'].get('status') != expected['ufs_host_safety_status'].encode() + b'\0':
        raise ValueError('UFS activated before GCC CX corner coupling is implemented')
    for name, domain in expected['domain_migration'].items():
        proxy = one(nodes, lambda n: n['props'].get('regulator-name') == name.encode() + b'\0')
        spec = cells(proxy['props']['power-domains'])
        if len(spec) != 2 or spec[1] != domain['index'] or not compatible(h[spec[0]], domain['provider']):
            raise ValueError('incorrect native GDSC mapping: ' + name)
    rpm = h[cells(bus['props']['qcom,rpm'])[0]]
    if not compatible(rpm, 'qcom,rpm-msm8996') or not enabled(nodes, rpm):
        raise ValueError('missing native MSM8996 RPM transport')
    if h[cells(host['props']['qcom,msm-bus-provider'])[0]] != bus:
        raise ValueError('UFS does not name real bus supplier')
    if ufs_project(nodes) != expected['ufs_client']:
        raise ValueError('Sony UFS vectors/names differ')
    ids = {}
    for child in bus['children']:
        index = cells(child['props']['cell-id'])[0]
        if index in ids:
            raise ValueError('duplicate bus cell-id')
        ids[index] = child
    for source, destination in ((95, 512), (1, 650)):
        queue, seen = [ids[source]], set()
        while queue:
            node = queue.pop()
            index = cells(node['props']['cell-id'])[0]
            if index in seen:
                continue
            seen.add(index)
            queue.extend(h[v] for v in cells(node['props'].get('qcom,connections', b'')))
        if destination not in seen:
            raise ValueError('unreachable UFS bandwidth route')
    # IDs must be populated for this SoC, not merely declared in a shared header.
    for mapping in expected['clock_migration']:
        source = (ROOT / mapping['driver']).read_text()
        if not re.search(r'\[' + re.escape(mapping['native_symbol']) + r'\]\s*=\s*&', source):
            raise ValueError('native clock missing from provider table: ' + mapping['native_symbol'])
    return len(ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dtb', type=Path)
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    try:
        expected = json.loads((ROOT / 'Documentation/sony/dora-bus-dt.json').read_text())
        nodes = parse_fdt(args.dtb)
        count = validate(nodes, expected)
        if args.selftest:
            bus = one(nodes, lambda n: compatible(n, 'qcom,msm-bus-device'))['path']
            host = one(nodes, lambda n: n['path'].endswith('/ufshc@624000'))['path']
            cases = [(host, 'qcom,msm-bus-provider'), (host, 'qcom,msm-bus,vectors-KBps'),
                     (bus, 'qcom,rpm'), (bus + '/fab-bimc', 'clocks'),
                     (bus + '/fab-a0noc', 'bus-gdsc-supply'), (host, 'status')]
            for path, key in cases:
                broken = copy.deepcopy(nodes)
                del broken[path]['props'][key]
                try:
                    validate(broken, expected)
                except (ValueError, KeyError, IndexError, struct.error):
                    continue
                raise ValueError('negative test accepted missing ' + key)
    except (ValueError, KeyError, IndexError, struct.error) as error:
        print('FAIL:', error)
        return 1
    print('PASS: %d original MSM8996 fabric/master/slave nodes, native clocks/domains, RPM and both UFS routes' % count)
    if args.selftest:
        print('PASS: six missing-provider/vector/safety-gate negative tests')
    print('UFS host safety-gated: native GCC CX voltage-corner coupling is missing.')
    print('DT structure only; RPM votes and UFS I/O require device tests.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
