import struct, sys
from pathlib import Path

CONT = ("moov", "trak", "mdia", "minf", "stbl", "edts", "dinf", "udta", "meta")

def children(data, start, end):
    off = start
    out = []
    while off + 8 <= end:
        size = struct.unpack(">I", data[off:off+4])[0]
        typ = data[off+4:off+8].decode("latin1")
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[off+8:off+16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        out.append((typ, off, size, hdr))
        off += size
    return out

def find(data, start, end, typ):
    for t, o, s, h in children(data, start, end):
        if t == typ:
            return o, s, h
    return None

def fullbox(data, off):
    return data[off], int.from_bytes(data[off+1:off+4], "big")

def parse_mvhd(data, off, hdr):
    base = off + hdr
    ver = data[base]
    if ver == 1:
        ts = struct.unpack(">I", data[base+20:base+24])[0]
        dur = struct.unpack(">Q", data[base+24:base+32])[0]
    else:
        ts = struct.unpack(">I", data[base+12:base+16])[0]
        dur = struct.unpack(">I", data[base+16:base+20])[0]
    return ts, dur

def parse_elst(data, off, hdr):
    base = off + hdr
    ver = data[base]
    n = struct.unpack(">I", data[base+4:base+8])[0]
    p = base + 8
    out = []
    for _ in range(n):
        if ver == 1:
            segd = struct.unpack(">Q", data[p:p+8])[0]
            mt = struct.unpack(">q", data[p+8:p+16])[0]
            rate = struct.unpack(">i", data[p+16:p+20])[0]
            p += 20
        else:
            segd = struct.unpack(">I", data[p:p+4])[0]
            mt = struct.unpack(">i", data[p+4:p+8])[0]
            rate = struct.unpack(">i", data[p+8:p+12])[0]
            p += 12
        out.append((segd, mt, rate))
    return out

def parse_stbl(data, off, size, hdr):
    st = {}
    start, end = off + hdr, off + size
    box = find(data, start, end, "stsd")
    base = box[0] + box[2]
    st["sample_entry_count"] = struct.unpack(">I", data[base+4:base+8])[0]
    p = base + 8
    esize = struct.unpack(">I", data[p:p+4])[0]
    etype = data[p+4:p+8].decode("latin1")
    st["sample_type"] = etype
    st["sample_entry"] = data[p:p+esize]
    for typ in ("stts", "stss", "ctts", "stsz", "stsc", "stco", "co64", "sgpd", "sbgp"):
        b = find(data, start, end, typ)
        if not b:
            continue
        o, s, h = b
        o = o + h
        if typ == "stts":
            n = struct.unpack(">I", data[o+4:o+8])[0]
            st["stts"] = [struct.unpack(">II", data[o+8+8*i:o+16+8*i]) for i in range(n)]
        elif typ == "stss":
            n = struct.unpack(">I", data[o+4:o+8])[0]
            st["stss"] = [struct.unpack(">I", data[o+8+4*i:o+12+4*i])[0] for i in range(n)]
        elif typ == "ctts":
            ver = data[o]
            n = struct.unpack(">I", data[o+4:o+8])[0]
            p = o + 8
            vals = []
            for _ in range(n):
                if ver == 1:
                    cnt, val = struct.unpack(">Ii", data[p:p+8])
                else:
                    cnt, val = struct.unpack(">II", data[p:p+8])
                vals.append((cnt, val))
                p += 8
            st["ctts"] = vals
        elif typ == "stsz":
            ss = struct.unpack(">I", data[o+4:o+8])[0]
            n = struct.unpack(">I", data[o+8:o+12])[0]
            if ss:
                st["sizes"] = [ss] * n
            else:
                st["sizes"] = [struct.unpack(">I", data[o+12+4*i:o+16+4*i])[0] for i in range(n)]
        elif typ == "stsc":
            n = struct.unpack(">I", data[o+4:o+8])[0]
            st["stsc"] = [struct.unpack(">III", data[o+8+12*i:o+20+12*i]) for i in range(n)]
        elif typ == "stco":
            n = struct.unpack(">I", data[o+4:o+8])[0]
            st["chunk_offsets"] = [struct.unpack(">I", data[o+8+4*i:o+12+4*i])[0] for i in range(n)]
        elif typ == "co64":
            n = struct.unpack(">I", data[o+4:o+8])[0]
            st["chunk_offsets"] = [struct.unpack(">Q", data[o+8+8*i:o+16+8*i])[0] for i in range(n)]
        else:
            st[typ] = data[o:o+s]
    return st

def sample_offsets(data, st):
    sizes = st["sizes"]
    sc = st["stsc"]
    chunks = st["chunk_offsets"]
    out = []
    idx = 0
    for ci, base in enumerate(chunks):
        entry = sc[0]
        for e in sc:
            if ci + 1 >= e[0]:
                entry = e
        off = base
        for _ in range(entry[1]):
            if idx >= len(sizes):
                break
            out.append((off, sizes[idx]))
            off += sizes[idx]
            idx += 1
    assert len(out) == len(sizes), (len(out), len(sizes))
    return out

def expand(entries, n):
    vals = []
    for cnt, val in entries:
        vals.extend([val] * cnt)
    return vals[:n]

def describe(path):
    data = Path(path).read_bytes()
    moov = find(data, 0, len(data), "moov")
    mv = find(data, moov[0]+moov[2], moov[0]+moov[1], "mvhd")
    print("== %s mvhd ts=%d dur=%d" % (path, *parse_mvhd(data, mv[0], mv[2])))
    for t, o, s, h in children(data, moov[0]+moov[2], moov[0]+moov[1]):
        if t != "trak":
            continue
        tk = find(data, o+h, o+s, "tkhd")
        base = tk[0] + tk[2]
        ver = data[base]
        tid = struct.unpack(">I", data[base+12:base+16])[0]
        if ver == 1:
            tdur = struct.unpack(">Q", data[base+28:base+36])[0]
        else:
            tdur = struct.unpack(">I", data[base+20:base+24])[0]
        mdia = find(data, o+h, o+s, "mdia")
        mdhd = find(data, mdia[0]+mdia[2], mdia[0]+mdia[1], "mdhd")
        mb = mdhd[0] + mdhd[2]
        ver = data[mb]
        if ver == 1:
            ts = struct.unpack(">I", data[mb+20:mb+24])[0]
            mdur = struct.unpack(">Q", data[mb+24:mb+32])[0]
            lang = data[mb+32:mb+34]
        else:
            ts = struct.unpack(">I", data[mb+12:mb+16])[0]
            mdur = struct.unpack(">I", data[mb+16:mb+20])[0]
            lang = data[mb+20:mb+22]
        hdlr = find(data, mdia[0]+mdia[2], mdia[0]+mdia[1], "hdlr")
        handler = data[hdlr[0]+hdlr[2]+8:hdlr[0]+hdlr[2]+12].decode("latin1")
        elst = None
        edts = find(data, o+h, o+s, "edts")
        if edts:
            e = find(data, edts[0]+edts[2], edts[0]+edts[1], "elst")
            elst = parse_elst(data, e[0], e[2]) if e else None
        minf = find(data, mdia[0]+mdia[2], mdia[0]+mdia[1], "minf")
        stbl = find(data, minf[0]+minf[2], minf[0]+minf[1], "stbl")
        st = parse_stbl(data, stbl[0], stbl[1], stbl[2])
        n = len(st["sizes"])
        durs = expand(st["stts"], n)
        cts = expand(st["ctts"], n) if "ctts" in st else [0]*n
        dts = []
        acc = 0
        for d in durs:
            dts.append(acc)
            acc += d
        pts = [dts[i] + cts[i] for i in range(n)]
        print(" track id=%d %s ts=%d tkhd_dur=%d mdhd_dur=%d elst=%s" % (tid, handler, ts, tdur, mdur, elst))
        print("  samples=%d sync=%s" % (n, st.get("stss")))
        print("  stts=%s" % (st["stts"][:8],))
        print("  ctts=%s" % (st.get("ctts", [])[:8],))
        print("  dts=%s .. %s" % (dts[:8], dts[-4:]))
        print("  pts=%s .. %s" % (pts[:14], pts[-4:]))
        print("  durs=%s unique=%s" % (durs[:10], sorted(set(durs))))
        print("  sizes=%s" % (st["sizes"][:8],))
        print("  chunks=%d stsc=%s" % (len(st["chunk_offsets"]), st["stsc"][:6]))
        print("  first_offsets=%s" % (sample_offsets(data, st)[:4],))
        if "sgpd" in st:
            print("  sgpd=%s sbgp=%s" % (st["sgpd"].hex(), st["sbgp"].hex() if "sbgp" in st else None))
        if handler == "vide":
            ent = st["sample_entry"]
            p = 8 + 78
            while p + 8 <= len(ent):
                bsz = struct.unpack(">I", ent[p:p+4])[0]
                btyp = ent[p+4:p+8].decode("latin1", "replace")
                print("   sub=%s size=%d hex=%s" % (btyp, bsz, ent[p+8:p+bsz].hex()[:120]))
                p += bsz
        else:
            print("  entry=%s" % (st["sample_entry"].hex(),))

if __name__ == "__main__":
    for f in sys.argv[1:]:
        describe(f)
