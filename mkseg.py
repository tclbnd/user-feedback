d = open("v0p.yuv", "rb").read()
fs = 64 * 48 * 3 // 2
open("seg.yuv", "wb").write(d[0:fs * 5])
