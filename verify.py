#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stripe.py 验收脚本：
1. GF(2^8) 运算与编码矩阵可逆性自检
2. 单/多块失联（含数据块、校验块、混合）在可恢复范围内字节级一致
3. 超校验能力失联：明确报错、ok=false、不误写、不计数、不清理失联集合
4. rebuild 写回后数据与全部校验块均与原始条带一致
5. 3 组 k/m 配比复跑：降级统计与数据摘要一致（失联模式可重放）
6. CLI 端到端 input.json -> output.json
"""

import hashlib
import itertools
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stripe  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.environ.get("TMPDIR", HERE)

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("[%s] %s%s" % (PASS if cond else FAIL, name, (" -- " + detail) if detail else ""))


def random_blocks(k, rng):
    alpha = b"abcdefghijklmnopqrstuvwxyz0123456789-_"
    return [bytes(rng.choice(alpha) for _ in range(rng.randint(0, 48))).decode()
            for _ in range(k)]


def expected_payload(blocks):
    return "".join(blocks)


def parity_of(gf, g, data_padded, k, m):
    return [stripe.encode_parity(gf, g[k + r], data_padded, len(data_padded[0]))
            for r in range(m)]


# ---------------------------------------------------------------- 1. GF(256)
def test_gf():
    gf = stripe.GF256()
    ok = all(gf.mul(gf.inv(a), a) == 1 for a in range(1, 256))
    ok &= gf.mul(0, 123) == 0 and gf.mul(123, 0) == 0
    ok &= all(gf.mul(a, b) == gf.mul(b, a) for a in range(256) for b in (3, 17, 200))
    # 本原元 2 的 255 次幂回到 1
    ok &= gf.exp[255] == 1
    check("GF(256) 乘法/逆元/交换律/本原元", ok)


# ------------------------------------------- 2. 任意 <=m 失联子集均可重建
def test_all_recovery_combos(k, m, rng):
    n = k + m
    blocks = random_blocks(k, rng)
    raw = [b.encode() for b in blocks]
    size = max(len(x) for x in raw)
    padded = [x.ljust(size, b"\0") for x in raw]
    gf = stripe.GF256()
    g = stripe.coding_matrix(gf, k, m)
    orig_parity = parity_of(gf, g, padded, k, m)

    checked = 0
    for cnt in range(1, m + 1):
        for dead in itertools.combinations(range(n), cnt):
            ops = [{"op": "fail", "disk": list(dead)}, {"op": "read"}, {"op": "rebuild"},
                   {"op": "read"}]
            res, state = stripe.simulate({"k": k, "m": m, "blocks": blocks, "ops": ops})
            # 两次读取（降级 + 重建后）都必须与原始字节一致
            ok_reads = all(r["ok"] and r["data"] == expected_payload(blocks)
                           for r in res["reads"])
            # 降级标记：失联中含数据块时第一读 degraded
            expect_degraded = any(d < k for d in dead)
            ok_flag = res["reads"][0]["degraded"] == expect_degraded and \
                res["reads"][1]["degraded"] is False
            # 重建后 cur 与原始条带逐字节一致（数据块 + 全部校验块）
            ok_stripe = state["cur"][:k] == padded and state["cur"][k:] == orig_parity
            ok_stat = state["dead"] == set() and res["stats"]["rebuilds"] == 1 and \
                res["stats"]["degraded_reads"] == (1 if expect_degraded else 0)
            ok_err = res["errors"] == []
            if not (ok_reads and ok_flag and ok_stripe and ok_stat and ok_err):
                check("组合 %s k=%d m=%d 失联=%s" % ("", k, m, dead), False,
                      json.dumps(res, ensure_ascii=False))
                return False
            checked += 1
    check("k=%d m=%d：全部 %d 个失联子集（1..%d 盘，含数据/校验/混合）字节级重建且校验一致"
          % (k, m, checked, m), True)
    return True


# --------------------------------------- 3. 超能力失联：报错且不误写
def test_unrecoverable(k, m, rng):
    n = k + m
    blocks = random_blocks(k, rng)
    # 让失联盘至少含 1 个数据块
    dead = [0] + list(range(k, k + m))  # m+1 块：1 数据 + m 校验
    ops = [{"op": "fail", "disk": dead}, {"op": "read"}, {"op": "rebuild"}, {"op": "read"}]
    res_before, state_before = stripe.simulate(
        {"k": k, "m": m, "blocks": blocks,
         "ops": [{"op": "fail", "disk": dead}]})
    snapshot = list(state_before["cur"])
    res, state = stripe.simulate({"k": k, "m": m, "blocks": blocks, "ops": ops})

    ok_reads = all(r["ok"] is False and r["data"] is None and r["degraded"] is False
                   for r in res["reads"])
    ok_errors = len(res["errors"]) == 3 and all("unrecoverable" in e for e in res["errors"])
    # 不误写：各盘内容与失败前快照完全一致；失联盘仍为 None
    ok_no_write = state["cur"] == snapshot and all(state["cur"][d] is None for d in dead)
    # 不计数、不清失联集合
    ok_stats = res["stats"] == {"degraded_reads": 0, "rebuilds": 0}
    ok_dead = state["dead"] == set(dead)
    check("k=%d m=%d：%d 盘失联不可恢复，明确报错且不误写/不计数/不清状态"
          % (k, m, m + 1),
          ok_reads and ok_errors and ok_no_write and ok_stats and ok_dead,
      json.dumps(res["errors"], ensure_ascii=False))


# ----------------------------- 4. 降级读取只在线重建不落盘；恢复后再失联
def test_degraded_read_no_writeback(k, m, rng):
    blocks = random_blocks(k, rng)
    dead_disks = tuple(sorted(rng.sample(range(k + m), m))) if m > 0 else (0,)
    if m == 0:
        return  # m=0 无容错，跳过
    ops = [{"op": "fail", "disk": list(dead_disks)}, {"op": "read"}, {"op": "read"}]
    res, state = stripe.simulate({"k": k, "m": m, "blocks": blocks, "ops": ops})
    ok = all(r["ok"] and r["data"] == expected_payload(blocks) for r in res["reads"])
    ok &= all(state["cur"][d] is None for d in dead_disks)  # 在线重建不落盘
    ok &= state["dead"] == set(dead_disks)
    ok &= res["stats"]["degraded_reads"] == 2 and res["stats"]["rebuilds"] == 0
    check("k=%d m=%d：降级读取在线重建不落盘（失联 %s）" % (k, m, dead_disks), ok)


# -------------------------------- 5. 单奇偶退化为 XOR；重建多轮生命周期
def test_single_parity_xor_and_lifecycle(rng):
    k, m = 4, 1
    blocks = ["AAAA", "BBBB", "CCCC", "DDDD"]
    raw = [b.encode() for b in blocks]
    xor = bytearray(4)
    for b in raw:
        for i, v in enumerate(b):
            xor[i] ^= v
    gf = stripe.GF256()
    g = stripe.coding_matrix(gf, k, m)
    par = stripe.encode_parity(gf, g[k], raw, 4)
    ok_xor = bytes(xor) == par
    check("m=1 校验块等于单奇偶 XOR", ok_xor)

    ops = [
        {"op": "read"},                              # 正常读
        {"op": "fail", "disk": 2},                   # 数据盘失联
        {"op": "read"},                              # 降级读
        {"op": "rebuild"},                           # 重建写回
        {"op": "read"},                              # 恢复正常
        {"op": "fail", "disk": 4},                   # 校验盘失联
        {"op": "read"},                              # 数据可读、非降级
        {"op": "rebuild"},                           # 重建校验
        {"op": "fail", "disk": [0, 1]},              # 双盘失联
        {"op": "read"},                              # 不可恢复
        {"op": "rebuild"},                           # 拒绝重建
    ]
    res, state = stripe.simulate({"k": k, "m": m, "blocks": blocks, "ops": ops})
    payload = expected_payload(blocks)
    ok_flow = (
        [r["ok"] for r in res["reads"]] == [True, True, True, True, False] and
        [r["degraded"] for r in res["reads"]] == [False, True, False, False, False] and
        [r["data"] for r in res["reads"][:4]] == [payload] * 4 and
        res["reads"][4]["data"] is None and
        res["stats"] == {"degraded_reads": 1, "rebuilds": 2} and
        len(res["errors"]) == 2 and
        state["cur"][k] == bytes(xor)  # 最终校验块与 XOR 一致
    )
    check("m=1 生命周期：正常/降级/重建/校验盘故障/双盘不可恢复", ok_flow,
          json.dumps(res, ensure_ascii=False))


# ---------------- 6. 3 组 k/m 配比复跑：统计与摘要一致（可重放）
def test_replay_configs():
    rng = random.Random(20261116)
    configs = [(4, 1), (6, 2), (5, 3)]
    table = []
    all_ok = True
    for k, m in configs:
        blocks = random_blocks(k, rng)
        # 每种配比固定一条失联模式：先 m 盘失联 -> 读 -> 重建 -> 再读 -> m+1 盘失联 -> 读
        first_fail = list(range(0, m))                 # m 个数据盘
        second_fail = list(range(k, k + m)) + [k - 1]  # m 校验 + 1 数据 = m+1
        ops = [
            {"op": "fail", "disk": first_fail},
            {"op": "read"},
            {"op": "rebuild"},
            {"op": "read"},
            {"op": "fail", "disk": second_fail},
            {"op": "read"},
        ]
        req = {"k": k, "m": m, "blocks": blocks, "ops": ops}
        r1 = stripe.run(req)
        r2 = stripe.run(req)  # 重放同一失联序列
        same = r1 == r2
        # 成功读取字节与原始一致；最后一次读取明确失败
        payload = expected_payload(blocks)
        semantic = (r1["reads"][0]["ok"] and r1["reads"][0]["data"] == payload and
                    r1["reads"][0]["degraded"] and
                    r1["reads"][1]["ok"] and r1["reads"][1]["data"] == payload and
                    not r1["reads"][1]["degraded"] and
                    r1["reads"][2]["ok"] is False and r1["reads"][2]["data"] is None and
                    r1["stats"]["degraded_reads"] == 1 and
                    r1["stats"]["rebuilds"] == 1 and
                    len(r1["errors"]) == 1 and "unrecoverable" in r1["errors"][0])
        data_sha1 = hashlib.sha1(
            "".join(r["data"] or "" for r in r1["reads"]).encode()
        ).hexdigest()
        table.append((k, m, r1["stats"]["degraded_reads"], r1["stats"]["rebuilds"],
                      r1["digest"], data_sha1))
        all_ok &= same and semantic
        check("k=%d m=%d 重放一致且语义正确（digest=%s）" % (k, m, r1["digest"][:10]),
              same and semantic)
    print("\n%-4s %-4s %-14s %-10s %-42s %-42s" %
          ("k", "m", "degraded_reads", "rebuilds", "output_digest(sha1)", "read_data(sha1)"))
    for row in table:
        print("%-4d %-4d %-14d %-10d %-42s %-42s" % row)
    return all_ok


# ---------------------------------- 7b. 随机生命周期压力测试 + 矩阵可逆性
def test_stress():
    rng = random.Random(7)

    def rnd_blocks(k):
        # 用 latin1 可无损往返任意字节
        return [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64))
                     ).decode("latin1") for _ in range(k)]

    all_ok = True
    for k, m, rounds in [(4, 1, 20), (8, 2, 15), (6, 4, 10), (10, 3, 8)]:
        n = k + m
        gf = stripe.GF256()
        g = stripe.coding_matrix(gf, k, m)
        # 性质：任意 k 行子矩阵的行秩必须为 k（Cauchy MDS 性质）
        sub_count = 0
        for rows in itertools.combinations(range(n), k):
            sub = [[g[r][c] for c in range(k)] for r in rows]
            picked = stripe.select_independent_rows(
                gf, [(r, sub[r]) for r in range(k)], k)
            assert len(picked) == k
            sub_count += 1

        for t in range(rounds):
            blocks = rnd_blocks(k)
            # 与实现保持同一编码路径：输入字符串按 UTF-8 落地，按原始块长拼接预期
            raw = [b.encode() for b in blocks]
            size = max(len(b) for b in raw)
            padded = [b.ljust(size, b"\0") for b in raw]
            _, init_state = stripe.simulate(
                {"k": k, "m": m, "blocks": blocks, "ops": []})
            orig_par = init_state["cur"][k:]
            payload = "".join(blocks)

            # 场景 A：随机多轮 fail/read/rebuild 历史，含故意超 m 的失联；
            # 一旦超 m，后续读必须全部明确失败且永不误写
            history = []
            dead = set()
            ever_breached = False
            for _step in range(8):
                cnt = rng.randrange(0, m + 2)
                avail = [d for d in range(n) if d not in dead]
                picks = rng.sample(avail, min(cnt, len(avail)))
                if picks:
                    history.append({"op": "fail", "disk": picks})
                    dead.update(picks)
                if len(dead) > m:
                    ever_breached = True
                history.append({"op": "read"})
                if rng.random() < 0.6:
                    history.append({"op": "rebuild"})
                    if len(dead) <= m:
                        dead.clear()

            res, state = stripe.simulate(
                {"k": k, "m": m, "blocks": blocks, "ops": history})
            # 所有成功读取字节与原始一致；失败读取 ok=false/data=null
            ok = all((not r["ok"]) or r["data"] == payload for r in res["reads"])
            ok &= all(r["ok"] or r["data"] is None for r in res["reads"])
            # 终态无失联 => 重建必然已成功，整个条带与原始逐字节一致
            if not state["dead"]:
                ok &= state["cur"][:k] == padded and state["cur"][k:] == orig_par
            # 终态仍失联 => 失联盘内容必须保持 None（超能力后不误写）
            ok &= all(state["cur"][d] is None for d in state["dead"])
            res2, state2 = stripe.simulate(
                {"k": k, "m": m, "blocks": blocks, "ops": history})
            ok &= res == res2 and state["cur"] == state2["cur"] and \
                state["dead"] == state2["dead"]

            # 场景 B：始终不超过 m 的随机生命周期，末轮 rebuild 后条带必然完整
            history_b = []
            dead_b = set()
            for _step in range(8):
                cap = min(m - len(dead_b), n - len(dead_b))
                if cap > 0 and rng.random() < 0.7:
                    picks = rng.sample(
                        [d for d in range(n) if d not in dead_b],
                        rng.randrange(1, cap + 1))
                    history_b.append({"op": "fail", "disk": picks})
                    dead_b.update(picks)
                history_b.append({"op": "read"})
                if rng.random() < 0.5:
                    history_b.append({"op": "rebuild"})
                    dead_b.clear()
            history_b.append({"op": "rebuild"})
            res_b, state_b = stripe.simulate(
                {"k": k, "m": m, "blocks": blocks, "ops": history_b})
            ok &= all(r["ok"] and r["data"] == payload for r in res_b["reads"])
            ok &= state_b["dead"] == set()
            ok &= state_b["cur"][:k] == padded and state_b["cur"][k:] == orig_par
            ok &= stripe.run(
                {"k": k, "m": m, "blocks": blocks, "ops": history_b}) == res_b

            if not ok:
                check("stress k=%d m=%d round=%d" % (k, m, t), False)
                all_ok = False
                break
        check("stress k=%d m=%d：%d 轮随机生命周期 + %d 个 k 行子矩阵可逆性"
              % (k, m, rounds, sub_count), all_ok)
    return all_ok


# ---------------------------------------------- 8. CLI 端到端
def test_cli():
    req = {"k": 3, "m": 2,
           "blocks": ["abc", "defg", "hi"],
           "ops": [{"op": "fail", "disk": [0, 3]}, {"op": "read"},
                   {"op": "rebuild"}, {"op": "read"},
                   {"op": "fail", "disk": [1, 2, 4]}, {"op": "read"}]}
    in_path = os.path.join(TMP, "d21_in.json")
    out_path = os.path.join(TMP, "d21_out.json")
    with open(in_path, "w", encoding="utf-8") as f:
        json.dump(req, f)
    proc = subprocess.run([sys.executable, os.path.join(HERE, "stripe.py"),
                           in_path, out_path], capture_output=True, text=True)
    ok_proc = proc.returncode == 0
    with open(out_path, encoding="utf-8") as f:
        out = json.load(f)
    payload = "abcdefghi"
    ok = (ok_proc and
          out["reads"][0]["ok"] and out["reads"][0]["data"] == payload and
          out["reads"][0]["degraded"] and
          out["reads"][1]["ok"] and out["reads"][1]["data"] == payload and
          not out["reads"][1]["degraded"] and
          out["reads"][2]["ok"] is False and out["reads"][2]["data"] is None and
          out["stats"] == {"degraded_reads": 1, "rebuilds": 1} and
          len(out["errors"]) == 1 and
          set(out) == {"reads", "errors", "stats", "digest"})
    check("CLI: python3 stripe.py input.json output.json 端到端", ok,
          proc.stderr.strip() or "")


def main():
    rng = random.Random(42)
    test_gf()
    for k, m in [(4, 1), (6, 2), (5, 3)]:
        test_all_recovery_combos(k, m, rng)
        test_unrecoverable(k, m, rng)
        test_degraded_read_no_writeback(k, m, rng)
    test_single_parity_xor_and_lifecycle(rng)
    replay_ok = test_replay_configs()
    stress_ok = test_stress()
    test_cli()

    total = len(results)
    failed = [name for name, ok, _ in results if not ok]
    print("\n================ 汇总 ================")
    print("用例 %d，通过 %d，失败 %d" % (total, total - len(failed), len(failed)))
    if failed:
        print("失败项：")
        for name in failed:
            print("  - " + name)
        sys.exit(1)
    print("全部验收通过（重放一致=%s，压力测试一致=%s）" % (replay_ok, stress_ok))


if __name__ == "__main__":
    main()
