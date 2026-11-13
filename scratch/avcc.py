import sys
sys.path.insert(0, "scratch")
from survey import video_track
for p in sys.argv[1:]:
    data, st = video_track(p)
    ent = st["sample_entry"]
    off = 8 + 78
    print(p, "avcC", ent[off+8:off+8+50].hex())
