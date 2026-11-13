import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


def units(raw):
    pieces=[p for p in re.split(rb"\x00\x00(?:\x00)?\x01",raw) if p]
    out=[]
    for p in pieces:
        if p[0]&31==9:
            out.append([])
        if not out:
            out.append([])
        out[-1].append(p)
    return out


def packetize(raw,faults=()):
    """faults: subset of {"fu-middle","stap"}; drops the middle FU-A
    fragment of access unit 1 and/or the STAP-A parameter sets of the
    IDR access unit 4."""
    source=units(raw)
    packets=[]
    seq=65532
    base=2**32-3000
    for i,pieces in enumerate(source):
        payloads=[]
        j=0
        while j<len(pieces):
            nal=pieces[j]
            kind=nal[0]&31
            if kind==7 and j+1<len(pieces) and pieces[j+1][0]&31==8:
                vals=pieces[j:j+2]
                payloads.append((b"\x78"+b"".join(struct.pack(">H",len(v))+v for v in vals),
                                 "stap" in faults and i==4))
                j+=2
                continue
            if kind in (1,5) and len(nal)>=(4 if i==1 else 31):
                data=nal[1:]
                blocks=[data[:len(data)//3],data[len(data)//3:2*len(data)//3],data[2*len(data)//3:]]
                for k,chunk in enumerate(blocks):
                    indicator=(nal[0]&224)|28
                    head=kind|(128 if k==0 else 64 if k==2 else 0)
                    payloads.append((bytes([indicator,head])+chunk,
                                     "fu-middle" in faults and i==1 and k==1))
            else:
                payloads.append((nal,False))
            j+=1
        for j,(p,lost) in enumerate(payloads):
            if not lost:
                packets.append(dict(sequence=seq%65536,timestamp=(base+i*1500)%2**32,
                                    marker=j==len(payloads)-1,hex=p.hex()))
            seq+=1
    shuffled=[]
    for i in range(0,len(packets),5):
        group=packets[i:i+5]
        if len(group)>3:
            group[1],group[2]=group[2],group[1]
            group.insert(3,group[2])
        shuffled+=group
    return {"first_sequence":65532,"window":7,"packets":shuffled},source,base


def truth(source,base):
    """Generator ground truth: extended timestamps and per-NAL hashes."""
    return {"units":[{"timestamp":base+i*1500,
                      "nals":[hashlib.sha256(n).hexdigest() for n in au]}
                     for i,au in enumerate(source)]}


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--faults",default="",help="comma list: fu-middle,stap")
    ap.add_argument("--packets",default="packets.json")
    ap.add_argument("--truth",default="truth.json")
    a=ap.parse_args()
    raw=Path(__file__).with_name("source.h264").read_bytes()
    log,source,base=packetize(raw,{f for f in a.faults.split(",") if f})
    Path(a.packets).write_text(json.dumps(log)+"\n")
    Path(a.truth).write_text(json.dumps(truth(source,base))+"\n")
