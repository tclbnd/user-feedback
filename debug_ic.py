# -*- coding: utf-8 -*-
"""初始条件记忆长度诊断: 比较零/非零初始状态两个环在相同输入下的轨迹差。"""
import dither

q = 1.0 / 32768.0
coef = [1.8, -0.9]
seed = 20261116
M = 300
probe = [q * 1.2] + [0.0] * (M - 1)

cz = dither._ShaperCore(16, "none", coef, seed)
cn = dither._ShaperCore(16, "none", coef, seed)
cn.s1, cn.s2, cn.yp = 0.03 * q, -0.02 * q, 0.0
cn.e1, cn.e2 = 0.07 * q, -0.05 * q

du = []
dcode = 0
for i, x in enumerate(probe):
    # 量化前的 s2(即量化输入, 无抖动模式 u=s2)
    uz_s2 = cz.s2
    un_s2 = cn.s2
    cz.step(x)
    cn.step(x)
    du.append(abs(un_s2 - uz_s2) / q)
    if cz.out[-1] != cn.out[-1]:
        dcode += 1

print("u(s2) diff in LSB, every 10 samples:")
print([round(du[i], 5) for i in range(0, M, 10)])
first = next((i for i, d in enumerate(du) if d < 1e-6), None)
print("first sample below 1e-6 LSB:", first, " final diff LSB:", du[-1],
      " code-divergent samples:", dcode)

# 齐次状态差: 完全相同的零输入 + 无量化(浮点线性), 纯看状态矩阵极点
# 手动复现线性化(量化恒等 y=s2)的状态递推, 验证极点模 0.949
g1, g2 = 2.0 - coef[0], 1.0 + coef[1]
a1, a2 = coef
# 线性环: s1'=s1-y ; s2'=s2+s1-y+g1 e1+g2 e2, e=0(恒等量化) => s2'=s2+s1-y ; y=s2
s1, s2, yp = 0.03, -0.02, 0.0
traj = []
for _ in range(M):
    s1n = s1 - yp
    s2n = s2 + s1n - yp
    y = s2n
    traj.append(abs(y))
    s1, s2, yp = s1n, s2n, y
print("\nlinear homogeneous |y| first 12:", [round(v, 5) for v in traj[:12]])
print("linear |y| at n=50,100,200:", round(traj[50], 8),
      round(traj[100], 10), round(traj[200], 12))
# 极点
import math
disc = a1 * a1 + 4.0 * a2
rmag = math.sqrt(-a2)
print("NTF pole magnitude sqrt(-a2) =", rmag,
      " time constant ~1/(-ln r) =", round(-1.0 / math.log(rmag), 2), "samples")

# 通用系数 C 环(含 g1/g2 误差反馈支路)线性化: 量化恒等 y=s2 => e=0
g1, g2 = 2.0 - coef[0], 1.0 + coef[1]
s1, s2, yp = 0.03, -0.02, 0.0
e1, e2 = 0.07, -0.05
traj2 = []
for _ in range(M):
    s1n = s1 - yp
    s2n = s2 + s1n - yp + g1 * e1 + g2 * e2
    y = s2n
    e = 0.0
    e2, e1 = e1, e
    s1, s2, yp = s1n, s2n, y
    traj2.append(abs(y))
print("\ngeneral EF linear |s2| n=0..11:", [round(v, 6) for v in traj2[:12]])
below = next((i for i, v in enumerate(traj2) if v < 1e-6), None)
print("first below 1e-6:", below, " n=100:", traj2[100], " n=200:", traj2[200])
print("theory r^100 =", rmag ** 100, " r^200 =", rmag ** 200)