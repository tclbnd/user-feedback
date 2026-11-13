import sys, subprocess, os
sys.path.insert(0, "scratch")
import survey, mp4dump
from goptest import decode_markers

FF = "tools/ffmpeg"

def extract_samples(path, indices, out):
    r = survey.analyze_video(path)
    data = r["data"]
    st = r["st"]
    offs = mp4dump.sample_offsets(data, st)
    blob = bytearray()
    # prepend SPS/PPS as annexb for decoder; but test the sample span only
    ent = st["sample_entry"]
    off = 8 + 78
    avcc = ent[off+8:]
    n = avcc[5] & 31
    p = 6
    for _ in range(n):
        sz = int.from_bytes(avcc[p:p+2], "big")
        blob += b"\x00\x00\x00\x01" + avcc[p+2:p+2+sz]
        p += 2 + sz
    pc = avcc[p]
    p += 1
    for _ in range(pc):
        sz = int.from_bytes(avcc[p:p+2], "big")
        blob += b"\x00\x00\x00\x01" + avcc[p+2:p+2+sz]
        p += 2 + sz
    for i in indices:
        o, s = offs[i]
        sample = data[o:o+s]
        ls = r["length_size"]
        q = 0
        while q + ls <= len(sample):
            ln = int.from_bytes(sample[q:q+ls], "big")
            q += ls
            blob += b"\x00\x00\x00\x01" + sample[q:q+ln]
            q += ln
    open(out, "wb").write(bytes(blob))

def markers_from_es(path):
    return decode_markers(path)

if __name__ == "__main__":
    # video2 sync at decode index 23 (open GOP I frame) - decode starting there only
    for idx in (23, 36, 10):
        extract_samples("video2.mp4", list(range(idx, idx+8)), "scratch/span_%d.h264" % idx)
        vals = markers_from_es("scratch/span_%d.h264" % idx)
        print("start at decode idx", idx, "->", vals)
