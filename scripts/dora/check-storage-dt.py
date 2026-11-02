#!/usr/bin/env python3
"""Verify Dora's real eMMC/SD graph and expected Android storage paths.

This validates final Sony hardware data and native bindings. It does not issue
MMC commands, discover actual partitions or prove HS400/data integrity.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import runpy
import struct

ROOT = Path(__file__).resolve().parents[2]
parse_fdt = runpy.run_path(str(ROOT/'scripts/dora/check-power-dt.py'))['parse_fdt']
SD1 = '/soc/sdhci@7464900'
SD2 = '/soc/sdhci@74a4900'


def cells(value):
    if len(value) % 4:
        raise ValueError('incomplete u32 array')
    return list(struct.unpack('>%dI' % (len(value)//4), value))


def string(value):
    return value.rstrip(b'\0').decode()


def compatibles(node):
    return node['props'].get('compatible',b'').split(b'\0')


def validate(nodes, config=None):
    expected = json.loads((ROOT/'Documentation/sony/dora-storage-dt.json').read_text())
    handles = {cells(n['props']['phandle'])[0]:n for n in nodes.values() if 'phandle'in n['props']}
    header = (ROOT/'include/dt-bindings/clock/qcom,gcc-msm8996.h').read_text()
    ids = {name:int(value) for name,value in re.findall(r'#define\s+(\w+)\s+(\d+)\b',header)}
    gpio = next(n for n in nodes.values() if b'qcom,msm8996-pinctrl'in compatibles(n))
    bus = next(n for n in nodes.values() if b'qcom,msm-bus-device'in compatibles(n))
    for path, gold in expected['controllers'].items():
        node=nodes[path];props=node['props']
        if node['name'] != path.rsplit('/',1)[1] or props.get('compatible')!=b'qcom,sdhci-msm\0':
            raise ValueError('SDHCI node/driver name mismatch')
        if props.get('status')!=b'disabled\0':
            raise ValueError('MMC activated before validated GCC CX coupling gate')
        if props.get('bus-width')!=props['qcom,bus-width']:
            raise ValueError('native/legacy bus width mismatch')
        if any(k in props for k in ['vmmc-supply','vqmmc-supply','sdhc-msm-crypto']):
            raise ValueError('unadapted upstream supply or removed legacy ICE binding')
        if handles[cells(props['qcom,msm-bus-provider'])[0]]!=bus:
            raise ValueError('missing real MMC bus supplier')
        for key,value in gold.items():
            if key=='status':continue
            actual=props[key]
            if key=='clocks':
                args=cells(actual)
                if len(args)!=len(value)*2:raise ValueError('MMC clock count mismatch')
                for handle,index,name in zip(args[::2],args[1::2],value):
                    provider=handles[handle]
                    if b'qcom,gcc-msm8996'not in compatibles(provider) or index!=ids[name]:
                        raise ValueError('MMC clock does not reference real GCC output')
                    if not re.search(r'\['+name+r'\]\s*=\s*&',(ROOT/'drivers/clk/qcom/gcc-msm8996.c').read_text()):
                        raise ValueError('unpopulated GCC clock output')
            elif key.endswith('-supply'):
                supply=handles[cells(actual)[0]]
                if string(supply['props'].get('regulator-name',b''))!=value:
                    # Native SMD regulators use the child name and real PM8994 bank.
                    if supply['name']!=value.removeprefix('pm8994_') or 'pm8994-regulators'not in supply['path']:
                        raise ValueError('wrong physical MMC supply: '+key)
                if supply['props'].get('status')==b'disabled\0':
                    raise ValueError('disabled MMC physical supply: '+key)
                if 'regulator-system-load'in supply['props'] or 'regulator-allow-set-load'not in supply['props']:
                    raise ValueError('MMC load votes disabled or duplicated by static system load: '+key)
            elif key in ['pinctrl-0','pinctrl-1']:
                states=[handles[h] for h in cells(actual)]
                if [n['name'] for n in states]!=value:raise ValueError('wrong MMC pad state graph')
                for state in states:
                    if len(state['children'])!=1:raise ValueError('unexpected pad subnodes')
                    selected={k:v.hex() for k,v in state['children'][0]['props'].items() if k not in ['phandle','linux,phandle']}
                    if selected!=expected['pins'][state['name']]:raise ValueError('Sony pad drive/bias differs')
            elif key in ['cd-gpios','uim2-gpios']:
                args=cells(actual)
                if handles[args[0]]!=gpio or args[1:]!=value[1:]:raise ValueError('wrong CD/UIM2 GPIO')
            elif actual.hex()!=value:
                raise ValueError('Sony MMC property differs: '+path+':'+key)
    aliases=nodes['/aliases']['props']
    for key,path in [('sdhc1',SD1),('sdhc2',SD2),('mmc0',SD1),('mmc1',SD2)]:
        if string(aliases[key])!=path:raise ValueError('unstable MMC host/driver alias: '+key)
    for path,gold in expected['fstab'].items():
        props={k:v.hex() for k,v in nodes[path]['props'].items() if k not in ['phandle','linux,phandle']}
        if props!=gold:raise ValueError('original Android early-mount metadata differs: '+path)
    if nodes['/soc/ufshc@624000']['props'].get('status')!=b'disabled\0':
        raise ValueError('Dora UFS must remain disabled: installed storage is eMMC')
    phy=next(n for n in nodes.values() if b'qcom,msm8996-ufs-phy-qmp-14nm'in compatibles(n))
    if phy['props'].get('status')!=b'disabled\0':raise ValueError('Dora unused UFS PHY enabled')
    if config:
        cfg=config.read_text()
        for name in ['MMC','MMC_BLOCK','MMC_SDHCI','MMC_SDHCI_PLTFM','MMC_SDHCI_MSM','EFI_PARTITION']:
            if not re.search(r'^CONFIG_'+name+r'=y$',cfg,re.M):raise ValueError('missing storage implementation: '+name)
        if not re.search(r'^CONFIG_MMC_BLOCK_MINORS=32$',cfg,re.M):raise ValueError('original MMC minors compatibility not preserved')
    return expected


def selftest(nodes,config):
    cases=[(SD1,'compatible',b'qcom,sdhci-msm-v4\0'),(SD1,'qcom,bus-width',struct.pack('>I',4)),
           (SD1,'clock-names',b'iface\0core\0'),(SD1,'qcom,clk-rates',struct.pack('>I',400000000)),
           (SD2,'uim2-gpios',b'\0'*12),('/aliases','mmc0',(SD2+'\0').encode()),
           ('/soc/ufshc@624000','status',b'okay\0'),(SD1,'status',b'okay\0'),
           ('/firmware/android/fstab/vendor','dev',b'/dev/block/sda1\0')]
    for path,prop,value in cases:
        bad=copy.deepcopy(nodes);bad[path]['props'][prop]=value
        try:validate(bad,config)
        except (ValueError,KeyError,StopIteration,IndexError,struct.error):continue
        raise ValueError('accepted invalid storage fixture: '+prop)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('dtb',type=Path);p.add_argument('--config',type=Path);p.add_argument('--selftest',action='store_true');a=p.parse_args()
    try:
        nodes=parse_fdt(a.dtb);validate(nodes,a.config)
        if a.selftest:selftest(nodes,a.config)
    except (ValueError,KeyError,StopIteration,IndexError,struct.error,OSError)as e:
        print('FAIL:',e);return 1
    print('PASS: Sony SDHC1 eMMC/SDHC2 hybrid SD-UIM2 data, native GCC/rails/pads, aliases and Android system/oem paths')
    if a.selftest:print('PASS: nine bad-controller/clock/GPIO/path/activation fixtures rejected')
    print('Controllers safety-gated pending GCC CX proof; GPT partitions/HS200/HS400/suspend require device tests.')
    return 0

if __name__=='__main__':raise SystemExit(main())
