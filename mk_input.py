"""生成 README 约定格式的示例 input.json。
python3 mk_input.py <len> <seed> > input.json
"""
import json
import math
import sys

L = int(sys.argv[1]) if len(sys.argv) > 1 else 512
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1

s = (seed * 2654435761 + 12345) & 0xFFFFFFFF


def rnd():
    global s
    s = (1664525 * s + 1013904223) & 0xFFFFFFFF
    return s / 4294967296.0


wa = 2 * math.pi * 0.061
wb = 2 * math.pi * 0.073
a = [math.cos(wa * n + 0.4) + 0.03 * (rnd() * 2 - 1) for n in range(L)]
b = [math.cos(wb * n + 1.3) + 0.03 * (rnd() * 2 - 1) for n in range(L)]
print(json.dumps({"a": a, "b": b, "len": L, "block": 128, "seed": seed}))