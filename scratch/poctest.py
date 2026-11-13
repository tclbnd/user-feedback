import sys, subprocess
sys.path.insert(0, "scratch")
import survey, mp4dump
from goptest import decode_markers
import markers

FF = "tools/ffmpeg"

def annexb(r, indices):
    ent = r["st"]["sample_entry"]
    off = 8 + 78
    avcc = ent[off+8:]
    blob = bytearray()
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
    offs = mp4dump.sample_offsets(r["data"], r["st"])
    ls = r["length_size"]
    for i in indices:
        o, s = offs[i]
        sample = r["data"][o:o+s]
        q = 0
        while q + ls <= len(sample):
            ln = int.from_bytes(sample[q:q+ls], "big")
            q += ls
            blob += b"\x00\x00\x00\x01" + sample[q:q+ln]
            q += ln
    return bytes(blob)

if __name__ == "__main__":
    r = survey.analyze_video("video1.mp4")
    rows = r["rows"]
    # splice: video1 gop 12..23 (ends P at 23), then video0 gop 12..23
    blob = annexb(r, list(range(12, 24)))
    r2 = survey.analyze_video("video0.mp4")
    blob2 = annexb(r2, list(range(12, 24)))
    open("scratch/splice.h264", "wb").write(blob + blob2)
    vals = decode_markers("scratch/splice.h264")
    print("spliced markers:", vals)
