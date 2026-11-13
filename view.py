"""RGBA PNG reader and incomplete animation frame viewer."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

SIGNATURE=b"\x89PNG\r\n\x1a\n"


def chunks(data):
    if not data.startswith(SIGNATURE):
        raise ValueError("png_signature")
    cursor=len(SIGNATURE)
    while cursor<len(data):
        if cursor+12>len(data):
            raise ValueError(f"truncated at {cursor}")
        length,kind=struct.unpack_from(">I4s",data,cursor)
        start=cursor+8
        end=start+length
        if end+4>len(data):
            raise ValueError(f"truncated at {cursor}")
        body=data[start:end]
        crc,=struct.unpack_from(">I",data,end)
        if zlib.crc32(kind+body)!=crc:
            raise ValueError(f"crc at {cursor}")
        yield kind,body
        cursor=end+4
        if kind==b"IEND":
            return
    raise ValueError("truncated: missing IEND")


def pixels(width,height,compressed):
    data=zlib.decompress(compressed)
    stride=4*width
    if len(data)!=(stride+1)*height:
        raise ValueError("scanline_size")
    last=bytearray(stride)
    result=bytearray()
    for y in range(height):
        pos=y*(stride+1)
        mode=data[pos]
        if mode>4:
            raise ValueError("scanline_filter")
        row=bytearray(data[pos+1:pos+stride+1])
        for j in range(stride):
            a=row[j-4] if j>=4 else 0
            b=last[j]
            c=last[j-4] if j>=4 else 0
            if mode==1:
                v=a
            elif mode==2:
                v=b
            elif mode==3:
                v=(a+b)//2
            elif mode==4:
                p=a+b-c
                v=min((a,b,c),key=lambda k:abs(p-k))
            else:
                v=0
            row[j]=(row[j]+v)%256
        result.extend(row)
        last=row
    return bytes(result)


def view(data, requests, budget):
    width=height=0
    idat=[]
    for kind,body in chunks(data):
        if kind==b"IHDR":
            width,height=struct.unpack(">II",body[:8])
        if kind==b"IDAT":
            idat.append(body)
    rgba=pixels(width,height,b"".join(idat))
    h=hashlib.sha256(rgba).hexdigest()
    requested=[0] if requests is None else requests
    return {"frames":[{"index":i,"start":[0,1],"duration":[0,1],"rgba_sha256":h}
                      for i in requested],"stats":{"max_checkpoints":0,"max_replay_frames":0}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--request",type=Path)
    parser.add_argument("--budget",type=int,default=4)
    args=parser.parse_args()
    try:
        request=json.loads(args.request.read_text())["frames"] if args.request else None
        result=view(args.input.read_bytes(),request,args.budget)
        args.output.write_text(json.dumps(result,sort_keys=True)+"\n")
    except (ValueError,struct.error,zlib.error) as error:
        parser.exit(2,str(error)+"\n")


if __name__=="__main__":
    main()
