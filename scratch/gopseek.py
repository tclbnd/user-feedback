import sys
sys.path.insert(0, "scratch")
from goptest import decode_markers
from survey import analyze_video

if __name__ == "__main__":
    path = "video2.mp4"
    full = decode_markers(path)
    print("full:", full)
    r = analyze_video(path)
    rows = r["rows"]
    order = sorted(range(len(rows)), key=lambda i: rows[i]["pts"])
    print("display order:", order)
    for start_pts in (750, 1250, 1251):
        vals = decode_markers(path, ["-ss", "%dms" % start_pts])
        print("ss", start_pts, "->", vals[:20])
