import sys
from pathlib import Path

FRAME_SIZE = 64 * 48 * 3 // 2

def read_marker(yuv, index):
    base = index * FRAME_SIZE
    bits = 0
    for b in range(12):
        x = b * 3
        y = 0
        # average the block rows 0..5, cols x..x+2 luminance
        total = 0
        for row in range(6):
            for col in range(3):
                total += yuv[base + row * 64 + x + col]
        avg = total / 18.0
        if avg > 128:
            bits |= (1 << b)
    return bits

def read_all(path):
    yuv = Path(path).read_bytes()
    n = len(yuv) // FRAME_SIZE
    return [read_marker(yuv, i) for i in range(n)]

if __name__ == "__main__":
    vals = read_all(sys.argv[1])
    print(len(vals), vals)
