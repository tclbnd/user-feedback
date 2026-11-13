import sys, subprocess, struct
sys.path.insert(0, "scratch")

FF = "tools/ffmpeg"

def run(args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=True)

def mkpcm(path, n, seed=1):
    import math
    vals = []
    for i in range(n):
        x = int(12000 * math.sin(2 * math.pi * (200 + seed) * i / 48000.0))
        vals.append(x)
    open(path, "wb").write(struct.pack("<%dh" % n, *vals))

def adts_packets(path):
    d = open(path, "rb").read()
    off = 0
    out = []
    while off + 7 <= len(d):
        assert d[off] == 0xFF and (d[off+1] & 0xF0) == 0xF0
        flen = ((d[off+3] & 3) << 11) | (d[off+4] << 3) | (d[off+5] >> 5)
        out.append(flen)
        off += flen
    return out

def decoded_len(path):
    run(["-i", path, "-map", "0:a", "-f", "s16le", "-ar", "48000", "-ac", "1", path + ".pcm"])
    import os
    return os.path.getsize(path + ".pcm") // 2

for n in (1000, 1024, 2048, 3072, 5000, 48000):
    mkpcm("scratch/x.pcm", n)
    run(["-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "scratch/x.pcm",
         "-c:a", "aac", "-b:a", "160k", "-f", "adts", "scratch/x.aac"])
    pkts = adts_packets("scratch/x.aac")
    total = sum(pkts)
    print("n=%d packets=%d samples=%d packet_samples=%d decoded=%d" % (
        n, len(pkts), n, total, decoded_len("scratch/x.aac")))
