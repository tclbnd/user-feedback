import sys, subprocess
sys.path.insert(0, "scratch")
import survey, mp4dump, h264
from goptest import decode_markers

FF = "tools/ffmpeg"

def splice_es(files):
    out = bytearray()
    for f in files:
        out += open(f, "rb").read()
    return bytes(out)

if __name__ == "__main__":
    # Take video2 gop2 (decode 23..35): I frame is non-IDR. Prepend SPS/PPS only, no earlier frames.
    # We already tested: decodes 227.. regularly (no garbage), so non-IDR I works as random access here.
    # But check frame_num continuity: if I at 23 has frame_num 0 and previous decoder state had frame_num 15,
    # gaps_in_frame_num_allowed_flag matters. Let's test gop3 after gop2 vs gop3 alone: results identical?
    from poctest import annexb
    r = survey.analyze_video("video2.mp4")
    a = decode_markers_from = None
