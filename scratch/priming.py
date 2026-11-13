import sys, subprocess, struct, os
sys.path.insert(0, "scratch")

FF = "tools/ffmpeg"

def run(args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=True)

def mkpulse(path, n, positions):
    pcm = bytearray(n * 2)
    for p in positions:
        struct.pack_into("<hhh", pcm, 2 * p, 16000, 30000, 15500)
    open(path, "wb").write(bytes(pcm))

def decode(path):
    out = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin",
                          "-i", path, "-map", "0:a", "-f", "s16le", "-ar", "48000",
                          "-ac", "1", "-"], check=True, capture_output=True).stdout
    return struct.unpack("<%dh" % (len(out) // 2), out)

def pulses(vals, thr=8000):
    out = []
    i = 0
    while i < len(vals):
        if abs(vals[i]) > thr:
            j = i
            best = -1
            bi = i
            while j < len(vals) and abs(vals[j]) > thr:
                if abs(vals[j]) > best:
                    best = abs(vals[j])
                    bi = j
                j += 1
            out.append(bi)
            i = j
        else:
            i += 1
    return out

if __name__ == "__main__":
    n = 5000
    positions = [100, 2000, 4000]
    mkpulse("scratch/p.pcm", n, positions)
    run(["-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "scratch/p.pcm",
         "-c:a", "aac", "-b:a", "160k", "scratch/p.mp4"])
    vals = decode("scratch/p.mp4")
    print("input pulses", positions, "decoded len", len(vals), "pulses", pulses(vals))
    print("tail raw:", vals[4980:5010])
    print("tail end:", vals[5000:5010])
