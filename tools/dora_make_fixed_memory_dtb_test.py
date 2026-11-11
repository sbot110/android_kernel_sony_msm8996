#!/usr/bin/env python3
"""Isolated diagnostic: prefill /memory/reg in four Sony Dora Linux 5.15 DTBs.

Only 16-byte reg fields are touched in existing verified Image.gz-dtb.
No kernel rebuild, source edit, reboot or flash. Not a production RAM map.
"""
import hashlib, json, shutil, struct, subprocess, sys, zlib
from pathlib import Path

BASE=Path("/mnt/c/platform-tools")
SRC=BASE/"dora515-preflight-memory-fix"
DST=BASE/"dora515-preflight-fixed-memory-dtb"
ROOT=Path(__file__).resolve().parent.parent
ROM=Path.home()/"android/lineage/out/target/product/dora/lineage-21.0-20261030-UNOFFICIAL-dora.zip"
OLD_PACK="696b8cd7ea8971f7764f2b87ccccbf313f8cd53efda8f81307dfadbf415e081e"
OLD_IMAGE="4ff9e3da210472124c70f4541ce8f85796256511a9a1e2f6d0294f3eb66967a5"
OLD_FULL="94a9d751d4f2f160863b81df11e49310835534d7c4163dd20b6665f9ffed1dba"
NEW_REG=struct.pack(">IIII",0,0x80000000,0,0xbeac0000)
from dora_audit_dtb_early import parse_fdt


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verified(path, expected):
    b=path.read_bytes()
    if sha(b)!=expected:
        raise ValueError(f"SHA256 mismatch: {path}")
    return b


def u32(b,pos):
    return struct.unpack_from(">I",b,pos)[0]


def patch(blob):
    total,nodes,reservations,_=parse_fdt(blob)
    if total!=len(blob) or nodes.get("/memory",{}).get("reg")!=bytes(16):
        raise ValueError("Unexpected original /memory/reg")
    if "/memory@80000000" in nodes:
        raise ValueError("Unexpected alternative memory node")
    _,_,st,strings,_,_,_,_,strings_size,st_size=struct.unpack_from(">10I",blob)
    stringtab=blob[strings:strings+strings_size]
    if st+st_size>len(blob) or strings+strings_size>len(blob):
        raise ValueError("Invalid DTB offsets")
    stack=[]
    pos=st
    changed=[]
    out=bytearray(blob)
    end_struct=st+st_size
    while pos+4<=end_struct:
        token=u32(blob,pos)
        pos+=4
        if token==1:
            end=blob.find(b"\0",pos,end_struct)
            if end<0: raise ValueError("Bad node name")
            name=blob[pos:end].decode()
            stack.append(stack[-1].rstrip("/")+"/"+name if stack else "/")
            pos=(end+4)&~3
        elif token==2:
            if not stack: raise ValueError("Bad end node")
            stack.pop()
        elif token==3:
            if not stack or pos+8>end_struct: raise ValueError("Bad prop")
            length,n=struct.unpack_from(">II",blob,pos)
            pos+=8
            if n>=len(stringtab) or pos+length>end_struct:
                raise ValueError("Bad property offsets")
            name_end=stringtab.find(b"\0",n)
            if name_end<0: raise ValueError("Bad property name")
            name=stringtab[n:name_end].decode()
            if stack[-1]=="/memory" and name=="reg":
                if length!=16 or blob[pos:pos+16]!=bytes(16):
                    raise ValueError("Unexpected reg length/contents")
                out[pos:pos+16]=NEW_REG
                changed.append(pos)
            pos=(pos+length+3)&~3
        elif token==4: pass
        elif token==9:
            if stack: raise ValueError("FDT ended with open nodes")
            break
        else: raise ValueError("Bad DTB token")
    if len(changed)!=1: raise ValueError("Expected one /memory/reg property")
    modified=bytes(out)
    offset=changed[0]
    if blob[:offset]!=modified[:offset] or blob[offset+16:]!=modified[offset+16:]:
        raise ValueError("Unexpected DTB changes")
    _,after,new_reservations,_=parse_fdt(modified)
    expected=dict(nodes)
    expected["/memory"]=dict(nodes["/memory"],reg=NEW_REG)
    if expected!=after or reservations!=new_reservations:
        raise ValueError("Unexpected DTB parse changes")
    return modified


def main():
    if DST.exists(): raise ValueError("Output exists; refusing overwrite")
    orig=verified(SRC/"Image.gz-dtb",OLD_PACK)
    raw=verified(SRC/"Image",OLD_IMAGE)
    base=verified(SRC/"dora-515-diagnostic-boot-full64M.img",OLD_FULL)
    if len(base)!=67108864 or base[4096:4096+len(orig)]!=orig:
        raise ValueError("5.15 packaged source differs from original kernel")
    if sha((BASE/"dora-boot-stable-current.img").read_bytes()) != "07200b77a75f989533a477da355544dd25f1be99ee8c763df7ca75e5108d3178":
        raise ValueError("Current verified working 4.4 backup not found")
    d=zlib.decompressobj(31)
    image=d.decompress(orig)+d.flush()
    if not d.eof or image!=raw:
        raise ValueError("Kernel gzip does not match original raw Image")
    remain=d.unused_data
    gzip_length=len(orig)-len(remain)
    offset=0
    new_dtbs=[]
    for i in range(4):
        if offset+8>len(remain) or u32(remain,offset)!=0xd00dfeed:
            raise ValueError(f"Missing DTB {i+1}")
        length=u32(remain,offset+4)
        if length<40 or offset+length>len(remain):
            raise ValueError("Malformed DTB size")
        new_dtbs.append(patch(remain[offset:offset+length]))
        offset+=length
    if offset!=len(remain):
        raise ValueError("Unexpected extra bytes after fourth DTB")
    patched=orig[:gzip_length]+b"".join(new_dtbs)
    if len(patched)!=len(orig) or patched[:gzip_length]!=orig[:gzip_length]:
        raise ValueError("Compressed kernel modified")
    DST.mkdir(parents=True)
    (DST/"Image.gz-dtb").write_bytes(patched)
    shutil.copy2(SRC/"Image",DST/"Image")
    subprocess.run([sys.executable,str(ROOT/"tools/dora_prepare_boot515.py"),
        "--reference",str(BASE/"dora-boot-reference.img"),
        "--reference-zip",str(ROM),
        "--kernel",str(DST/"Image.gz-dtb"),
        "--image",str(DST/"Image"),
        "--outdir",str(DST)],check=True)
    new=(DST/"dora-515-diagnostic-boot-full64M.img").read_bytes()
    comparison=bytearray(base)
    comparison[576:608]=new[576:608]
    comparison[4096:4096+len(orig)]=patched
    if len(new)!=len(base) or bytes(comparison)!=new:
        raise ValueError("Unexpected difference from previously tested 5.15 boot")
    report={"status":"DIAGNOSTIC ONLY; NOT FLASHED",
        "new_boot_sha256":sha(new),"Image_sha256_unchanged":OLD_IMAGE,
        "source_boot_sha256":OLD_FULL,"DTBs_changed":4,
        "original_reg":bytes(16).hex(),"new_reg":NEW_REG.hex(),
        "only_changes":"4 DTB memory/reg properties and boot header ID"}
    (DST/"fixed-memory-test.json").write_text(json.dumps(report,indent=2)+"\n")
    print("PASS: 4 Dora DTBs prefilled; Image and ramdisk unchanged; NOT FLASHED")
    print("New full64 SHA256:",sha(new))
    print("Output:",DST)


if __name__=="__main__":
    try: main()
    except (OSError,ValueError,struct.error,zlib.error,subprocess.CalledProcessError) as e:
        print("STOP:",e,file=sys.stderr)
        sys.exit(2)
