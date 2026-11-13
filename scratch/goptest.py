import sys, subprocess
sys.path.insert(0, "scratch")
import survey

FF = "tools/ffmpeg"
SAMPLE = 64 * 48 * 3 // 2

def run(args, check=True):
    return subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=check)

def decode_markers(path, extra=None):
    args = [FF, "-hide_banner", "-loglevel", "error", "-nostdin"]
    args += (extra or []) + ["-i", path, "-map", "0:v", "-fps_mode", "passthrough",
                             "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"]
    out = subprocess.run(args, check=True, capture_output=True).stdout
    n = len(out) // SAMPLE
    vals = []
    for i in range(n):
        base = i * SAMPLE
        bits = 0
        for b in range(12):
            x = b * 3
            total = 0
            for row in range(6):
                for col in range(3):
                    total += out[base + row * 64 + x + col]
            if total / 18.0 > 128:
                bits |= (1 << b)
        vals.append(bits)
    return vals

if __name__ == "__main__":
    print("full decode:", decode_markers("video2.mp4")[:16])
