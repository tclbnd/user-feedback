import sys, subprocess
sys.path.insert(0, "scratch")
import survey
from goptest import decode_markers
from poctest import annexb

if __name__ == "__main__":
    # video2 gops: gop2 (decode 23..35) is non-IDR I but closed (no early frames).
    # decode gop2 then gop3 (decode 36..47) - both non-IDR I, check POC continuity
    r = survey.analyze_video("video2.mp4")
    blob = annexb(r, list(range(23, 48)))
    open("scratch/v2_2347.h264", "wb").write(blob)
    print("23..47:", decode_markers("scratch/v2_2347.h264"))
    # also 36..47 alone
    open("scratch/v2_3647.h264", "wb").write(annexb(r, list(range(36, 48))))
    print("36..47:", decode_markers("scratch/v2_3647.h264"))
    print("expected pts order rows:",
          [(i, r["rows"][i]["pts"], r["rows"][i]["slice"]["type"]) for i in range(36, 48)])
    print("24..39:", decode_markers.__name__)
    open("scratch/v2_2439.h264", "wb").write(annexb(r, list(range(24, 40))))
    print("24..39:", decode_markers("scratch/v2_2439.h264"))
