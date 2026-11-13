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


def packetize(raw,faults=False):
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
                                 faults and i==4))
                j+=2
                continue
            if kind in (1,5) and len(nal)>=(4 if i==1 else 31):
                data=nal[1:]
                blocks=[data[:len(data)//3],data[len(data)//3:2*len(data)//3],data[2*len(data)//3:]]
                for k,chunk in enumerate(blocks):
                    indicator=(nal[0]&224)|28
                    head=kind|(128 if k==0 else 64 if k==2 else 0)
                    payloads.append((bytes([indicator,head])+chunk,faults and i==1 and k==1))
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--faults", action="store_true",
                        help="drop an FU-A middle fragment (frame 1) and the "
                             "SPS/PPS STAP-A of the IDR frame 4")
    parser.add_argument("-o", "--output", default="packets.json")
    args = parser.parse_args()
    raw = Path(__file__).with_name("source.h264").read_bytes()
    Path(args.output).write_text(json.dumps(packetize(raw, faults=args.faults)[0]) + "\n")
