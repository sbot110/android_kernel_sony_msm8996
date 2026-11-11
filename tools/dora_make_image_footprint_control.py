#!/usr/bin/env python3
"""Generate unflashed Sony Linux 4.4 boot control with 5.15-size real Image.

Source: the already-booted 4.4 image_size=0x2160000 control.
Extend only the uncompressed Image with zeros to 34,349,568 bytes (the
real file length of 5.15). Preserve original code and all four Sony DTBs.
Recompress gzip and update Android v0 kernel_size and ID. Keep LOS21
ramdisk and source boot header other fields unchanged; preserve original
64MiB partition tail. DOES NOT FLASH OR ACCESS THE PHONE.
"""
import gzip
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

BASE = Path("/mnt/c/platform-tools")
SOURCE = BASE / "dora44-image-size-control/dora44-image-size-2160000-full64M.img"
BACKUP = BASE / "dora-boot-reference.img"
DEST = BASE / "dora44-image-footprint-control"
SOURCE_SHA = "b9a55c9a4f6e29f7f17573580e9197918ea57714fe84ebda8527b3da1465e05c"
BACKUP_SHA = "29e36966932799c43a616562928b519638b8fc2dcd9dc0d8dd12b208803ad943"
OLD_LENGTH = 26853396
NEW_LENGTH = 34349568
EFFECTIVE_IMAGE_SIZE = 0x2160000
DTB_SIZES = (470605, 471117, 473675, 474187)
PARTITION_SIZE = 67108864
PAGE = 4096


def sha(data):
    return hashlib.sha256(data).hexdigest()


def align(n):
    return (n + PAGE - 1)//PAGE*PAGE


def boot_id(kernel, ramdisk):
    h = hashlib.sha1()
    for item in (kernel,ramdisk,b""):
        h.update(item)
        h.update(struct.pack("<I",len(item)))
    return h.digest()+b"\0"*12


def decode_kernel(packed):
    d = zlib.decompressobj(31)
    raw = d.decompress(packed)+d.flush()
    if not d.eof or len(raw)<64 or raw[56:60]!=b"ARM\x64":
        raise ValueError("Invalid gzip ARM64 Image")
    rest=d.unused_data
    pos=0
    sizes=[]
    while pos<len(rest):
        if pos+8>len(rest):
            raise ValueError("Truncated DTB")
        magic,size=struct.unpack_from(">II",rest,pos)
        if magic!=0xd00dfeed or size<40 or pos+size>len(rest):
            raise ValueError(f"Bad DTB at offset {pos:#x}")
        sizes.append(size)
        pos+=size
    if tuple(sizes)!=DTB_SIZES:
        raise ValueError(f"Unexpected Sony 4.4 DTBs: {sizes}")
    return raw,rest


def main():
    if DEST.exists():
        raise ValueError(f"Existing output directory: {DEST}")
    source=SOURCE.read_bytes()
    backup=BACKUP.read_bytes()
    if (len(source)!=PARTITION_SIZE or sha(source)!=SOURCE_SHA or
            len(backup)!=PARTITION_SIZE or sha(backup)!=BACKUP_SHA):
        raise ValueError("Verified 4.4 source/backup missing or SHA mismatch")
    if source[:8]!=b"ANDROID!":
        raise ValueError("Android boot magic mismatch")
    ksize,kaddr,rsize,raddr,ssize,saddr,tags,page,unused,osver=struct.unpack_from("<10I",source,8)
    if (kaddr,rsize,raddr,ssize,tags,page,unused)!=(
            0x80008000,1224724,0x81000000,0,0x80000100,PAGE,0):
        raise ValueError("Unexpected Android v0 header")
    off=PAGE+align(ksize)
    kernel=source[PAGE:PAGE+ksize]
    ramdisk=source[off:off+rsize]
    if source[576:608]!=boot_id(kernel,ramdisk):
        raise ValueError("Boot-validated base has unexpected ID")
    raw,dtbs=decode_kernel(kernel)
    if len(raw)!=OLD_LENGTH:
        raise ValueError(f"Source raw Image size unexpected: {len(raw)}")
    offset,size,flags=struct.unpack_from("<QQQ",raw,8)
    if (offset,size,flags)!=(0x80000,EFFECTIVE_IMAGE_SIZE,0xa):
        raise ValueError("Unexpected Image header")
    if not (OLD_LENGTH<NEW_LENGTH<EFFECTIVE_IMAGE_SIZE):
        raise ValueError("Invalid padding target")
    padded=raw+b"\0"*(NEW_LENGTH-OLD_LENGTH)
    new_kernel=gzip.compress(padded,compresslevel=9,mtime=0)+dtbs
    verify_raw,verify_dtbs=decode_kernel(new_kernel)
    if verify_raw!=padded or verify_dtbs!=dtbs:
        raise ValueError("Raw Image or DTB changed beyond intended padding")
    if kaddr+len(new_kernel)>=raddr:
        raise ValueError("Compressed payload overlaps ramdisk_addr")

    header=bytearray(source[:PAGE])
    struct.pack_into("<I",header,8,len(new_kernel))
    header[576:608]=boot_id(new_kernel,ramdisk)
    if header[12:576]!=source[12:576] or header[608:]!=source[608:PAGE]:
        raise ValueError("Unexpected changes to Android header")
    compact=(bytes(header)+new_kernel+b"\0"*(align(len(new_kernel))-len(new_kernel))+
             ramdisk+b"\0"*(align(rsize)-rsize))
    if len(compact)>PARTITION_SIZE:
        raise ValueError("Boot image exceeds 64 MiB")
    full=compact+backup[len(compact):]
    if len(full)!=PARTITION_SIZE or full[len(compact):]!=backup[len(compact):]:
        raise ValueError("Full partition tail not preserved")
    new_ramdisk_offset=PAGE+align(len(new_kernel))
    if (full[new_ramdisk_offset:new_ramdisk_offset+rsize]!=ramdisk or
            full[576:608]!=boot_id(new_kernel,ramdisk)):
        raise ValueError("Repacked boot verification failed")

    DEST.mkdir(parents=True)
    out=DEST/"dora44-padded-image-34349568-full64M.img"
    out.write_bytes(full)
    manifest={
        "status":"UNFLASHED EXPERIMENTAL CONTROL",
        "base_boot_sha256":SOURCE_SHA,
        "backup_boot_sha256":BACKUP_SHA,
        "old_uncompressed_Image_size":OLD_LENGTH,
        "new_uncompressed_Image_size":NEW_LENGTH,
        "zero_padding_added":NEW_LENGTH-OLD_LENGTH,
        "ARM64_header_image_size_unchanged":hex(EFFECTIVE_IMAGE_SIZE),
        "original_Image_bytes_identical":True,
        "four_original_Sony_DTBS_identical":True,
        "original_LOS21_ramdisk_identical":True,
        "ramdisk_addr_unchanged":hex(raddr),
        "gzip_and_Android_kernel_size_and_ID_updated":True,
        "new_full64_sha256":sha(full),
        "output":str(out),
    }
    (DEST/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print("=== SONY 4.4 PADDED IMAGE FOOTPRINT TEST ===")
    print("Original raw Image:",OLD_LENGTH)
    print("New raw Image:     ",NEW_LENGTH)
    print("Added zero bytes:  ",NEW_LENGTH-OLD_LENGTH)
    print("ARM64 image_size:  ",hex(EFFECTIVE_IMAGE_SIZE),"unchanged")
    print("4 DTBs and LOS21 ramdisk unchanged")
    print("New full64 SHA256:",sha(full))
    print("Image:",out)
    print("PASS: Image footprint control generated; NOT FLASHED")


if __name__=="__main__":
    try:
        main()
    except (OSError,ValueError,struct.error,zlib.error) as exc:
        print("STOP:",exc,file=sys.stderr)
        sys.exit(2)
