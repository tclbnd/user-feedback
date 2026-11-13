import sys, subprocess, struct, json
sys.path.insert(0, "scratch")
import survey, markers

FF = "tools/ffmpeg"
SAMPLE = 64 * 48 * 3 // 2

def run(args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=True)

def decode_raw(path):
    out = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin",
                          "-i", path, "-map", "0:v", "-fps_mode", "passthrough",
                          "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
                         check=True, capture_output=True).stdout
    return out

def markers_of(raw, n):
    vals = []
    for i in range(n):
        base = i * SAMPLE
        bits = 0
        for b in range(12):
            x = b * 3
            total = 0
            for row in range(6):
                for col in range(3):
                    total += raw[base + row * 64 + x + col]
            if total / 18.0 > 128:
                bits |= (1 << b)
        vals.append(bits)
    return vals

if __name__ == "__main__":
    # take frames 12..23 of video0 (display order? no, decode order) -> re-encode with timebase of 1000 and given pts
    src = "video0.mp4"
    r = survey.analyze_video(src)
    rows = r["rows"]
    # decode frames 12..24 (13 frames: IDR at 12 + up to sync 24)
    import mp4dump
    data = r["data"]
    st = r["st"]
    offs = mp4dump.sample_offsets(data, st)
    # extract samples 12..23 (GOP 12-23 is closed: IDR at 12, next IDR 24)
    chunk = b"".join(data[offs[i][0]:offs[i][0]+offs[i][1]] for i in range(12, 24))
    # convert length prefixed to annexb for muxing? just use raw h264 stream
    open("scratch/gop12.h264", "wb").write(chunk)
    print("wrote gop12.h264")
