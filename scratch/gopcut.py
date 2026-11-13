import sys
sys.path.insert(0, "scratch")
from goptest import decode_markers
from survey import analyze_video, video_track
import mp4dump, subprocess, struct

FF = "tools/ffmpeg"

def extract_range(path, first, last, out):
    # remux only samples [first..last] using -ss/-to on decode? use copy with select
    pass

def decode_packets(path):
    r = analyze_video(path)
    return r

if __name__ == "__main__":
    path = "video2.mp4"
    r = analyze_video(path)
    rows = r["rows"]
    data = r["data"]
    st = r["st"]
    offs = mp4dump.sample_offsets(data, st)
    # Build a raw h264 elementary stream from samples 23..41 plus the needed prefixes
    # Test: cut starting at sample 23 (sync sample, but open GOP: B at 24 has pts before 23's pts? check)
    print("rows 21..26")
    for i in range(21, 27):
        print(i, rows[i]["pts"], rows[i]["dts"], rows[i]["slice"], "sync", rows[i]["sync"])
