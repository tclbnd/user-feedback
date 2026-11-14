"""验收基准：10,000 任务 + 1,000 次插删，对比逐次全量重建的访问规模。"""

import random
import time

import topo


def baseline_visit_cost(ops, n):
    """朴素做法：每来一个事件就全量重算一次计划。

    每次全量重建至少访问 n 个节点，共 m 个事件，因此访问量下界 n*m。
    """
    return n * len(ops)


def scenario(seed, n=10000, total=1000):
    """构造恰好 total 个插删事件：保活的长依赖链 + 随机插删 + 重复边 + 成环尝试。"""
    rng = random.Random(seed)
    # 1) 保活的长依赖链：跨 ODS/DWD/汇总 的深层依赖（不会被随机删除）
    deep = list(range(0, n, n // 10))
    reserved = set(zip(deep, deep[1:]))
    ops = [{"op": "add", "a": a, "b": b} for a, b in zip(deep, deep[1:])]
    live = set(reserved)

    # 2) 随机插删补齐到 total-1 个事件，删除只作用在非保留边上
    while len(ops) < total - 1:
        r = rng.random()
        pool = live - reserved
        if r < 0.6 or not pool:
            a, b = rng.randrange(n), rng.randrange(n)
            ops.append({"op": "add", "a": a, "b": b})
        elif r < 0.88:
            a, b = rng.choice(sorted(pool))
            ops.append({"op": "del", "a": a, "b": b})
            live.discard((a, b))
        else:
            a, b = rng.choice(sorted(pool))
            ops.append({"op": "add", "a": a, "b": b})   # 重复加边

    # 3) 末尾一次必然成环的加边（沿保留长链回边）
    ops.append({"op": "add", "a": deep[-1], "b": deep[0]})
    return ops


def final_live(ops, rejected):
    """按事件回放求出最终仍然存活的边集合（被拒的加边不生效）。"""
    bad = {(x["op"]["a"], x["op"]["b"]) for x in rejected}
    live = set()
    for op in ops:
        key = (op["a"], op["b"])
        if op["op"] == "add":
            if key not in bad:
                live.add(key)
        else:
            live.discard(key)
    return live


def measure(ops, n):
    t0 = time.perf_counter()
    r = topo.run({"n": n, "ops": ops})
    dt = time.perf_counter() - t0
    return r, dt


def main():
    n = 10000
    ops = scenario(1, n=n, total=1000)
    r, dt = measure(ops, n)
    print("任务数 n =", n, " 事件数 =", len(ops))
    print("valid =", r["valid"], " 计划长度 =", len(r["order"]),
          " 去重后元素数 =", len(set(r["order"])))
    print("被拒成环操作 =", len(r["rejected"]))
    if r["rejected"]:
        cyc = r["rejected"][0]["cycle"]
        print("首个环路径长度 =", len(cyc), " 首尾 =", (cyc[0], cyc[-1]))
    print("stats =", r["stats"])
    print("增量耗时 = %.3f s" % dt)
    print("逐次全量重建下界访问量 =", baseline_visit_cost(ops, n))
    print("相对访问规模 = %.4f%%" % (100.0 * r["stats"]["affected_sum"] /
                                     baseline_visit_cost(ops, n)))

    # 同一最终图，另一接入顺序 -> 同一计划
    accepted = final_live(ops, r["rejected"])
    pos = {v: i for i, v in enumerate(r["order"])}
    topo_ok = all(pos[u] < pos[v] for u, v in accepted)
    rng = random.Random(2)
    adds = [{"op": "add", "a": a, "b": b} for a, b in accepted]
    rng.shuffle(adds)
    t0 = time.perf_counter()
    r2 = topo.run({"n": n, "ops": adds})
    dt2 = time.perf_counter() - t0
    print("增量计划相对最终图是合法拓扑序 =", topo_ok)
    print("换序重建耗时 = %.3f s" % dt2)
    print("两序计划一致 =", r2["order"] == r["order"],
          " 换序 被拒 =", len(r2["rejected"]))

    # 下游删除过时依赖后，计划仍完整
    r3 = topo.run({"n": 200, "ops": [
        {"op": "add", "a": 100, "b": 150},
        {"op": "add", "a": 150, "b": 160},
        {"op": "add", "a": 100, "b": 160},
        {"op": "del", "a": 100, "b": 160},
    ]})
    print("下游删除过时依赖后 计划完整 =", r3["valid"], len(r3["order"]),
          " stats =", r3["stats"])


def stress():
    """对抗性负载：深层链上的级联抬升/压低、以及长路径成环搜索。"""
    n = 30000
    # 1) 在长链上反复追加"跳级"边，制造级联抬升
    ops = [{"op": "add", "a": i, "b": i + 1} for i in range(n - 1)]
    for (a, b) in [(0, 500), (0, 1500), (0, 3000), (500, 20000), (3000, 25000)]:
        ops.append({"op": "add", "a": a, "b": b})
    # 2) 长路径成环搜索：沿链回边，每次都要走完整条路径才判定成环
    for k in range(5, 0, -1):
        ops.append({"op": "add", "a": n - k, "b": 0})
    # 3) 删除链头附近的关键边，触发级联压低
    for i in range(0, 40):
        ops.append({"op": "del", "a": i, "b": i + 1})
    r, dt = measure(ops, n)
    print("stress n =", n, " 事件数 =", len(ops), " 耗时 = %.3f s" % dt)
    print("valid =", r["valid"], " 被拒 =", len(r["rejected"]),
          " stats =", r["stats"])
    print("相对逐次全量重建 = %.4f%%" %
          (100.0 * r["stats"]["affected_sum"] / (n * len(ops))))


if __name__ == "__main__":
    main()
    stress()
