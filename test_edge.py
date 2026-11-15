"""边界场景: 小窗/零信号/块大小=1/块大于窗, 结果必须稳定且与块无关。"""
import math
import os
import subprocess
import sys
import json

import xfade

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.environ.get("TMPDIR", "/tmp")
ok = True


def check(cond, msg):
    global ok
    print(("PASS" if cond else "FAIL"), "-", msg)
    if not cond:
        ok = False


# 1) 最小窗 L=32, 块大小 1
L = 32
a = [math.cos(0.2 * n) for n in range(L)]
b = [0.9 * math.cos(0.2 * n + 0.7) for n in range(L)]
o1, p1, s1 = xfade.crossfade(a, b, L, 1)
o2, p2, s2 = xfade.crossfade(a, b, L, L)
check(o1 == o2, "L=32 block=1 与 block=L 逐样本完全一致")
check(xfade.digest_of(o1) == xfade.digest_of(o2), "L=32 摘要一致")
check(len(o1) == L and 0 < p1 < L, "L=32 输出长度/剪接点合法")

# 2) 零信号不崩溃, 输出全零
za = [0.0] * 64
zb = [0.0] * 64
oz, pz, sz = xfade.crossfade(za, zb, 64, 7)
check(all(abs(v) < 1e-15 for v in oz), "零信号输出全零, 不崩溃")
check(sz["phase"]["aligned"] is False, "零信号不触发对齐")

# 3) 完全相同的信号: 验证等功率恒等式 g_a^2 + g_b^2 = 1(功率守恒,
#    而非幅度和为 1); 且超额跳变为 0、不触发对齐
Ls = 128
same = [math.cos(0.05 * n + 0.3) for n in range(Ls)]
os_, ps, ss = xfade.crossfade(same, same, Ls, 33)
# 理论输出 out[i] = (ga+gb)*same[i]; 校验增益平方和恒为 1
gain_err = 0.0
theory_err = 0.0
for i in range(Ls):
    ga = math.cos(0.5 * math.pi * i / (Ls - 1))
    gb = math.sin(0.5 * math.pi * i / (Ls - 1))
    gain_err = max(gain_err, abs(ga * ga + gb * gb - 1.0))
    theory_err = max(theory_err, abs(os_[i] - (ga + gb) * same[i]))
check(gain_err < 1e-15, "等功率增益 g_a^2+g_b^2=1 误差 < 1e-15 (实际 %.1e)"
      % gain_err)
check(theory_err < 1e-12, "相同信号输出符合 (ga+gb)*x 理论值 (实际 %.1e)"
      % theory_err)
check(ss["splice_jump"]["excess_jump_norm"] < 1e-12,
      "相同信号超额跳变 ~0")
check(ss["phase"]["aligned"] is False, "相同信号不触发对齐")

# 4) 块大小大于窗长也正常
ob, pb, sb = xfade.crossfade(same, same, Ls, 99999)
check(len(ob) == Ls, "block > L 正常产出")

# 5) CLI 进程隔离复现 digest 一致
inp = os.path.join(TMP, "edge_in.json")
outp = os.path.join(TMP, "edge_out.json")
with open(inp, "w") as f:
    json.dump({"a": a, "b": b, "len": 32, "block": 1, "seed": 1}, f)
subprocess.check_call([sys.executable, os.path.join(HERE, "xfade.py"),
                       inp, outp])
cli = json.load(open(outp))
check(cli["digest"] == xfade.digest_of(o1), "CLI 与库函数 digest 一致")

# 6) 确定性: 同输入连跑两次结果完全相同
r1 = xfade.crossfade(a, b, 32, 5)
r2 = xfade.crossfade(a, b, 32, 5)
check(r1[0] == r2[0] and r1[2] == r2[2], "同输入复跑完全确定")

print()
print("EDGE TESTS " + ("ALL PASSED" if ok else "HAD FAILURES"))
sys.exit(0 if ok else 1)