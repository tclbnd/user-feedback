import sys, subprocess, struct, wave
sys.path.insert(0, "scratch")

FF = "tools/ffmpeg"

def run(args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=True)

def peaks(path, thr=8000):
    w = wave.open(path, "rb")
    n = w.getnframes()
    vals = struct.unpack("<%dh" % n, w.readframes(n))
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
    return n, out

if __name__ == "__main__":
    # decode source audio ignoring edit list, then re-encode raw with explicit trimming
    run(["-ignore_editlist", "1", "-i", "video0.mp4", "-map", "0:a", "-f", "s16le",
         "-ar", "48000", "-ac", "1", "scratch/a0_raw.pcm"])
    raw = open("scratch/a0_raw.pcm", "rb").read()
    n = len(raw) // 2
    print("raw samples", n)
    # drop 1024 priming => audible starts at 1024
    body = raw[1024*2:]
    open("scratch/a0_body.pcm", "wb").write(body)
    print("body samples", len(body)//2)
    run(["-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "scratch/a0_body.pcm",
         "-c:a", "aac", "-b:a", "160k", "scratch/a0_body.m4a"])
    run(["-i", "scratch/a0_body.m4a", "-map", "0:a", "-f", "s16le", "-ar", "48000",
         "-ac", "1", "scratch/a0_body_rt.pcm"])
    rt = open("scratch/a0_body_rt.pcm", "rb").read()
    print("rt samples", len(rt)//2)
    vals = struct.unpack("<%dh" % (len(rt)//2), rt)
    first = [(i, vals[i]) for i in range(6000) if abs(vals[i]) > 8000]
    print("rt first pulse", first[:8])
