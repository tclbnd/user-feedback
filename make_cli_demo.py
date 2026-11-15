# -*- coding: utf-8 -*-
"""按 README 契约生成 input.json, 调用 dither.py CLI, 校验 output.json。"""
import json
import math
import os
import subprocess
import sys

sr = 96000
n = 4096
amp = 10.0 ** (-60.0 / 20.0)
samples = [amp * math.sin(2.0 * math.pi * 1000.0 * i / sr)
           for i in range(n)]
req = {"sr": sr, "samples": samples, "bits": 16, "mode": "tpdf",
       "shaping": [2.0, -1.0], "block": 1024, "seed": 1}
with open("results/cli_input.json", "w") as f:
    json.dump(req, f)

subprocess.run([sys.executable, "dither.py",
                "results/cli_input.json", "results/cli_output.json"],
               check=True)
res = json.load(open("results/cli_output.json"))
assert set(res.keys()) == {"out", "digest", "stats"}
assert len(res["out"]) == n
assert all(isinstance(v, int) for v in res["out"])
assert all(-32768 <= v <= 32767 for v in res["out"])
assert isinstance(res["digest"], str) and len(res["digest"]) == 40
st = res["stats"]
for key in ("n", "q", "rms_code", "rms_level", "err_mean", "err_rms",
            "err_var", "n_clipped", "final_state", "block_mode",
            "architecture"):
    assert key in st, key
print("CLI contract OK")
print("digest:", res["digest"])
print("block_mode:", st["block_mode"], "arch:", st["architecture"])
print("final_state keys:", sorted(st["final_state"].keys()))
print("code range:", min(res["out"]), max(res["out"]))

# 与库调用结果一致性
import dither
lib = dither.process(req)
assert lib["digest"] == res["digest"]
assert lib["out"] == res["out"]
print("CLI and library outputs identical: True")
