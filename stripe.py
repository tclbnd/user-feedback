#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分片落地冗余条带：k 个数据块 + m 个校验块的纠删码条带。

布局与能力
----------
- 编码矩阵 G 为 n=k+m 行、k 列：前 k 行为单位阵（数据块原样落地）。
  m=1 时校验行系数全为 1（单奇偶 XOR）；m>=2 时校验行为 GF(2^8) 上的
  Cauchy 矩阵 G[k+r][c] = 1/(x_r-y_c)，任意 k 行子矩阵均可逆，
  因此任意 m 块（含数据块/校验块混合）失联都可重建。
- m=1 单奇偶 XOR 容忍 1 块失联；m>=2 为多校验 Reed-Solomon 条带，
  容忍 m 块失联，降级读取在线重建。

语义
----
- 失联数 <= m：降级读取只在内存中在线重建，返回字节与原始一致，不落盘；
  rebuild 先重算、再校验存活校验块、最后一次性原子写回，写回后校验保持一致。
- 失联数 > m：明确报错（errors 记录、reads ok=false、data=null），
  不返回损坏数据，不修改任何存活块，不清空失联集合，不计数重建。
- 全流程确定性运算，失联序列可重放，stats/digest 逐次一致。

用法：python3 stripe.py input.json output.json
"""

import hashlib
import json
import sys


# --------------------------------------------------------------------------
# GF(2^8) 有限域，本原多项式 x^8+x^4+x^3+x^2+1 (0x11D)，本原元 2
# --------------------------------------------------------------------------
class GF256:
    def __init__(self, poly=0x11D):
        self.exp = [0] * 512
        self.log = [0] * 256
        x = 1
        for i in range(255):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= poly
        # 对数相加后无需每次取模
        for i in range(255, 512):
            self.exp[i] = self.exp[i - 255]

    def mul(self, a, b):
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def inv(self, a):
        if a == 0:
            raise ZeroDivisionError("GF(256) 中 0 无逆元")
        return self.exp[255 - self.log[a]]

    def power(self, a, n):
        if a == 0:
            return 0
        return self.exp[(self.log[a] * n) % 255]


def coding_matrix(gf, k, m):
    """
    返回 (k+m) x k 编码矩阵：上半部单位阵，下半部校验行。
    - m=1：校验行系数全 1，即单奇偶 XOR。
    - m>=2：采用 Cauchy 校验行 G[k+r][c] = 1 / (x_r - y_c)，
      x_r=k..(k+m-1) 与 y_c=0..(k-1) 互不相同；Cauchy 矩阵的任意方阵
      子式均非零，故任意 k 行（含部分数据行）构成的子矩阵在 GF(2^8) 上
      可逆，任意 m 盘失联都可重建。
    """
    n = k + m
    g = [[0] * k for _ in range(n)]
    for c in range(k):
        g[c][c] = 1
    if m == 1:
        g[k] = [1] * k
        return g
    for r in range(m):
        # 校验行横坐标取 k..(k+m-1)，与数据列横坐标 0..(k-1) 恒不相交
        x = k + r
        for c in range(k):
            g[k + r][c] = gf.inv(x ^ c)  # GF(2^8) 中减法即异或
    return g


def encode_parity(gf, row, data, size):
    """按一行编码系数计算一个校验块。系数为 1 时退化为纯 XOR。"""
    out = bytearray(size)
    for c, block in enumerate(data):
        coef = row[c]
        if coef == 0:
            continue
        if coef == 1:
            for i in range(size):
                out[i] ^= block[i]
        else:
            for i in range(size):
                out[i] ^= gf.mul(coef, block[i])
    return bytes(out)


def select_independent_rows(gf, rows, dim):
    """
    从 GF(256) 行向量序列 rows（每行 dim 列）中用 Gauss-Jordan 消元
    筛出 dim 个线性无关行，返回选中行的原下标列表。
    """
    basis = [None] * dim
    picked = []
    for idx, row in rows:
        v = row[:]
        for col in range(dim):
            if v[col] == 0:
                continue
            if basis[col] is None:
                inv_v = gf.inv(v[col])
                basis[col] = [gf.mul(x, inv_v) for x in v]
                picked.append(idx)
                break
            pivot = basis[col]
            factor = v[col]
            v = [v[c] ^ gf.mul(factor, pivot[c]) for c in range(dim)]
        if len(picked) == dim:
            break
    if len(picked) != dim:
        raise ValueError("编码矩阵奇异，无法求解失联块")
    return picked


def gf_solve(gf, coef, rhs_vecs):
    """
    在 GF(256) 上对 E 个方程、D 个未知向量做 Gauss-Jordan 消元。
    coef:     E x D 系数矩阵（列满秩，E >= D）
    rhs_vecs: E 个等长字节向量（方程右端）
    返回 D 个解向量（bytes 列表）。
    """
    eqs = len(coef)
    dim = len(coef[0])
    size = len(rhs_vecs[0])
    rows = [r[:] for r in coef]
    vecs = [bytearray(v) for v in rhs_vecs]
    pivot_row = 0
    for col in range(dim):
        pivot = next((r for r in range(pivot_row, eqs) if rows[r][col] != 0), None)
        if pivot is None:
            raise ValueError("编码矩阵奇异，无法求解失联块")
        rows[pivot_row], rows[pivot] = rows[pivot], rows[pivot_row]
        vecs[pivot_row], vecs[pivot] = vecs[pivot], vecs[pivot_row]
        inv_pivot = gf.inv(rows[pivot_row][col])
        rows[pivot_row] = [gf.mul(v, inv_pivot) for v in rows[pivot_row]]
        head = vecs[pivot_row]
        for t in range(size):
            head[t] = gf.mul(head[t], inv_pivot)
        for r in range(eqs):
            if r == pivot_row or rows[r][col] == 0:
                continue
            factor = rows[r][col]
            base_row = rows[pivot_row]
            for c in range(dim):
                rows[r][c] ^= gf.mul(factor, base_row[c])
            target, base = vecs[r], head
            for t in range(size):
                target[t] ^= gf.mul(factor, base[t])
        pivot_row += 1
    return [bytes(vecs[j]) for j in range(dim)]


def simulate(req):
    """
    执行完整条带操作序列，返回 (输出 JSON 结构, 内部状态)。
    内部状态供测试/审计：cur 为各盘当前字节（None 表示失联），dead 为失联盘号集合。
    """
    k, m = req["k"], req["m"]
    if not (isinstance(k, int) and isinstance(m, int) and k >= 1 and m >= 0):
        raise ValueError("k 必须 >=1 且 m 必须 >=0")
    raw = [b.encode() for b in req["blocks"]]
    if len(raw) != k:
        raise ValueError("blocks 数量必须等于 k")

    orig_lens = [len(b) for b in raw]
    size = max(orig_lens)
    data = [b.ljust(size, b"\0") for b in raw]

    gf = GF256()
    g = coding_matrix(gf, k, m)
    n = k + m
    parity = [encode_parity(gf, g[k + r], data, size) for r in range(m)]

    # cur[i] 为 None 即该盘失联；绝不读取失联盘的陈旧内容
    cur = list(data) + parity
    dead = set()

    reads = []
    errors = []
    stats = {"degraded_reads": 0, "rebuilds": 0}

    def recover_data(dead_set):
        """
        在线重建所有失联的数据块（仅内存，不写回 cur）。
        前置条件：|dead_set| <= m。返回 数据盘号 -> 重建字节 的映射。
        """
        missing_data = sorted(d for d in dead_set if d < k)
        if not missing_data:
            return {}
        present_data = [c for c in range(k) if c not in dead_set]
        surviving = [r for r in range(n) if r not in dead_set]
        # 按缺失块所在列做秩筛选，保证拿到的方程对未知缺失块线性无关
        candidate_rows = [(r, [g[r][b] for b in missing_data]) for r in surviving]
        picked = select_independent_rows(gf, candidate_rows, len(missing_data))
        coef = [[g[r][b] for b in missing_data] for r in picked]
        rhs = []
        for r in picked:
            v = bytearray(cur[r])
            for c in present_data:
                coef_rc = g[r][c]
                if coef_rc == 0:
                    continue
                block = cur[c]
                if coef_rc == 1:
                    for t in range(size):
                        v[t] ^= block[t]
                else:
                    for t in range(size):
                        v[t] ^= gf.mul(coef_rc, block[t])
            rhs.append(bytes(v))
        sols = gf_solve(gf, coef, rhs)
        return {b: sols[j] for j, b in enumerate(missing_data)}

    for seq, op in enumerate(req.get("ops", [])):
        kind = op.get("op")

        if kind == "fail":
            disks = op["disk"] if isinstance(op["disk"], list) else [op["disk"]]
            for d in disks:
                if not isinstance(d, int) or not (0 <= d < n):
                    errors.append("op#%d invalid disk index %r, ignored" % (seq, d))
                    continue
                dead.add(d)
                cur[d] = None  # 盘已失联，内容不可再用

        elif kind == "read":
            if len(dead) > m:
                errors.append(
                    "op#%d unrecoverable: %d disk(s) offline %s exceed tolerance m=%d"
                    % (seq, len(dead), sorted(dead), m)
                )
                reads.append({"ok": False, "data": None, "degraded": False})
                continue
            lost_data = [d for d in dead if d < k]
            if lost_data:
                # 可恢复范围内：在线重建，只在内存拼装视图，不落盘
                stats["degraded_reads"] += 1
                recovered = recover_data(dead)
                view = [recovered[i] if i in recovered else cur[i] for i in range(k)]
                degraded = True
            else:
                # 仅校验盘失联不影响数据读取，不算降级
                view = cur[:k]
                degraded = False
            payload = b"".join(view[i][:orig_lens[i]] for i in range(k))
            reads.append({
                "ok": True,
                "data": payload.decode(errors="replace"),
                "degraded": degraded,
            })

        elif kind == "rebuild":
            if len(dead) > m:
                # 不可恢复：明确报错，不写任何块、不清 dead、不计数
                errors.append(
                    "op#%d unrecoverable: rebuild aborted, %d disk(s) offline %s "
                    "exceed tolerance m=%d" % (seq, len(dead), sorted(dead), m)
                )
                continue
            # 1) 先在内存中恢复完整数据
            recovered = recover_data(dead)
            full_data = [recovered[i] if i in recovered else cur[i] for i in range(k)]
            # 2) 由完整数据重算全部校验块
            fresh_parity = {
                k + r: encode_parity(gf, g[k + r], full_data, size) for r in range(m)
            }
            # 3) 校验仍存活的校验块；不一致则整体放弃写回（不误写）
            mismatch = [
                k + r for r in range(m)
                if k + r not in dead and cur[k + r] != fresh_parity[k + r]
            ]
            if mismatch:
                errors.append(
                    "op#%d parity mismatch on surviving disk(s) %s; "
                    "rebuild aborted without write" % (seq, mismatch)
                )
                continue
            # 4) 一次性提交：数据块与缺失校验块写回，恢复后校验必然一致
            for idx, blob in recovered.items():
                cur[idx] = blob
            for r in range(m):
                if k + r in dead:
                    cur[k + r] = fresh_parity[k + r]
            dead.clear()
            stats["rebuilds"] += 1

        else:
            errors.append("op#%d unknown op %r, ignored" % (seq, kind))

    digest = hashlib.sha1(
        json.dumps([r.get("data") for r in reads], sort_keys=True).encode()
    ).hexdigest()
    result = {"reads": reads, "errors": errors, "stats": stats, "digest": digest}
    state = {"cur": cur, "dead": dead, "k": k, "m": m, "n": n,
             "size": size, "orig_lens": orig_lens}
    return result, state


def run(req):
    result, _ = simulate(req)
    return result


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        req = json.load(f)
    result = run(req)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
