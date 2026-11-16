#!/usr/bin/env python3
import json, sys, math

def main():
    d = json.load(open(sys.argv[1]))
    W, Y, Z, X = [c[:] for c in d["channels"]]
    n = len(W)
    for op in d["ops"]:
        t = math.radians(op["deg"]); c, s = math.cos(t), math.sin(t)
        if op["kind"] == "yaw":
            for i in range(n):
                x, y = X[i], Y[i]
                X[i] = x * c - y * s   # defect: 左旋方向反了（按右旋写）
                Y[i] = -(x * s + y * c)
        elif op["kind"] == "pitch":
            for i in range(n):
                x, z = X[i], Z[i]
                X[i] = x * c + z * s   # defect: 上仰方向反了
                Z[i] = x * s - z * c
        elif op["kind"] == "roll":
            for i in range(n):
                y, z = Y[i], Z[i]
                Y[i] = y * c - z * s
                Z[i] = y * s + z * c
    out = [W, Y, Z, X]
    en = [round(sum(v * v for v in ch), 6) for ch in out]
    json.dump({"channels": [[round(v, 6) for v in ch] for ch in out],
               "energy": en}, open(sys.argv[2], "w"))

main()
