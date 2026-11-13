import sys
sys.path.insert(0, "scratch")
import survey
from goptest import decode_markers
from poctest import annexb

if __name__ == "__main__":
    v0 = survey.analyze_video("video0.mp4")
    v2 = survey.analyze_video("video2.mp4")
    # video0 gop (24..35) closed, IDR; video2 gop (36..47) non-IDR I. splice and decode.
    blob = annexb(v0, list(range(24, 36))) + annexb(v2, list(range(36, 48)))
    open("scratch/mix.h264", "wb").write(blob)
    print("v0 24-35 then v2 36-47:", decode_markers("scratch/mix.h264"))
    # reverse order
    blob2 = annexb(v2, list(range(36, 48))) + annexb(v0, list(range(24, 36)))
    open("scratch/mix2.h264", "wb").write(blob2)
    print("v2 36-47 then v0 24-35:", decode_markers("scratch/mix2.h264"))
