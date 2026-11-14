import sys
d = open(sys.argv[1], "rb").read()
fs = 64 * 48 * 3 // 2
n = len(d) // fs
print("frames", n, "remainder", len(d) % fs)
for i in range(n):
    off = i * fs
    val = 0
    for b in range(12):
        y = d[off + b * 3]
        if y > 128:
            val |= 1 << b
    print(i, val)
