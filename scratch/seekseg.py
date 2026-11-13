import sys, os
sys.path.insert(0, "scratch")
import survey, markers

FF = "tools/ffmpeg"
SAMPLESIZE = 64 * 48 * 3 // 2

def read_frame(yuv, i):
    base = i * SAMPLESIZE
    bits = 0
    for b in range(12):
        x = b * 3
        total = 0
        for row in range(6):
            for col in range(3):
                total += yuv[base + row*64 + x + col]
        if total / 18.0 > 128:
            bits |= (1 << b)
    return bits

if __name__ == "__main__":
    yuv = open(sys.argv[1], "rb").read()
    n = len(yuv) // SAMPLESIZE
    print([read_frame(yuv, i) for i in range(n)])
