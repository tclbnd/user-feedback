import sys, json
sys.path.insert(0, "scratch")
from pathlib import Path
import mp4dump
import h264

def video_track(path):
    data = Path(path).read_bytes()
    moov = mp4dump.find(data, 0, len(data), "moov")
    for t, o, s, h in mp4dump.children(data, moov[0]+moov[2], moov[0]+moov[1]):
        if t != "trak":
            continue
        mdia = mp4dump.find(data, o+h, o+s, "mdia")
        hdlr = mp4dump.find(data, mdia[0]+mdia[2], mdia[0]+mdia[1], "hdlr")
        handler = data[hdlr[0]+hdlr[2]+8:hdlr[0]+hdlr[2]+12].decode("latin1")
        if handler != "vide":
            continue
        minf = mp4dump.find(data, mdia[0]+mdia[2], mdia[0]+mdia[1], "minf")
        stbl = mp4dump.find(data, minf[0]+minf[2], minf[0]+minf[1], "stbl")
        st = mp4dump.parse_stbl(data, stbl[0], stbl[1], stbl[2])
        return data, st
    raise SystemExit("no video track")

def parse_avcc(ent):
    avcc_off = 8 + 78
    assert ent[avcc_off+4:avcc_off+8] == b"avcC", ent[avcc_off:avcc_off+8]
    avcc = ent[avcc_off+8:]
    length_size = (avcc[4] & 3) + 1
    sps_cnt = avcc[5] & 31
    p = 6
    sps = None
    pps = None
    for _ in range(sps_cnt):
        sz = int.from_bytes(avcc[p:p+2], "big")
        sps = h264.parse_sps(h264.remove_epb(avcc[p+2:p+2+sz])[1:])
        p += 2 + sz
    pps_cnt = avcc[p]
    p += 1
    for _ in range(pps_cnt):
        sz = int.from_bytes(avcc[p:p+2], "big")
        pps = h264.parse_pps(h264.remove_epb(avcc[p+2:p+2+sz])[1:])
        p += 2 + sz
    return length_size, sps, pps

def timeline(st):
    sizes = st["sizes"]
    durs = mp4dump.expand(st["stts"], len(sizes))
    cts = mp4dump.expand(st["ctts"], len(sizes)) if "ctts" in st else [0]*len(sizes)
    dts = []
    acc = 0
    for d in durs:
        dts.append(acc)
        acc += d
    pts = [dts[i] + cts[i] for i in range(len(sizes))]
    return dts, pts, durs, cts

def analyze_video(path):
    data, st = video_track(path)
    length_size, sps, pps = parse_avcc(st["sample_entry"])
    sizes = st["sizes"]
    offs = mp4dump.sample_offsets(data, st)
    dts, pts, durs, cts = timeline(st)
    sync = set(st["stss"]) if st.get("stss") else set()
    rows = []
    for i in range(len(sizes)):
        off, sz = offs[i]
        nals = h264.split_nal(data[off:off+sz], length_size)
        types = [h264.nal_type(n) for n in nals]
        slices = [n for n in nals if h264.nal_type(n) in (1, 5)]
        sh = None
        if slices:
            sh = h264.parse_slice_header(h264.remove_epb(slices[0][1:]), sps, pps,
                                         h264.nal_ref_idc(slices[0]), h264.nal_type(slices[0]))
        rows.append({
            "i": i,
            "size": sz,
            "off": off,
            "types": types,
            "sync": (i+1) in sync,
            "dts": dts[i],
            "pts": pts[i],
            "dur": durs[i],
            "cts": cts[i],
            "slice": None if sh is None else {
                "type": h264.SLICE_NAMES.get(sh["slice_type"]),
                "frame_num": sh["frame_num"],
                "poc": sh["poc_lsb"],
                "idr": sh["idr_pic_id"],
                "refs": (sh["num_ref_l0"], sh["num_ref_l1"]),
                "nri": h264.nal_ref_idc(slices[0]),
            },
        })
    return {
        "path": path,
        "sps": sps,
        "pps": pps,
        "length_size": length_size,
        "rows": rows,
        "stts": st["stts"],
        "ctts": st.get("ctts"),
        "stss": st.get("stss"),
        "st": st,
        "data": data,
    }

def show(r):
    print("==", r["path"], "sps", r["sps"], "pps", r["pps"], "len_size", r["length_size"])
    for row in r["rows"]:
        sl = row["slice"]
        print("%2d %-2s fnum=%-3s poc=%-4s sync=%d pts=%-5d dts=%-5d dur=%-4d off=%-6d nri=%s types=%s" % (
            row["i"], sl["type"], sl["frame_num"], sl["poc"], row["sync"], row["pts"], row["dts"],
            row["dur"], row["off"], sl["nri"], ",".join(str(t) for t in row["types"])))

if __name__ == "__main__":
    for f in sys.argv[1:]:
        show(analyze_video(f))
