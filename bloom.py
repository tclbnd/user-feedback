import json, sys, hashlib, math

WARN_OCCUPANCY = 0.85  # 非空桶占用超过 85% 判定接近饱和


def run(req):
    """执行操作序列，返回判定/在线假阳性估计/预警/计数统计/终态摘要。

    修复点：
    1. del 仅在确认存在（k 个计数桶全 > 0）时执行，否则拒绝且不动任何
       计数器 —— 从结构上杜绝计数下溢污染其他键；
    2. 假阳性率在线估计：按插入量 p_n=(1-e^(-k*n/B))^k，并用桶占用
       p_occ=(非空桶/B)^k 校准，输出校准后的 p_occ；
    3. 非空桶占用 > 85% 时置 warn=true（饱和预警，粘性）。
    """
    B, K = req["buckets"], req["k"]
    seed = req.get("seed", 1)

    def h(key, i):
        return int.from_bytes(
            hashlib.sha1(f"{seed}:{i}:{key}".encode()).digest()[:4],
            "little") % B

    cnt = [0] * B
    verdicts = []
    fp_trace = [] if req.get("trace_estimates") else None
    n_ins = n_del = rejected = 0
    n_cur = 0      # 当前 distinct 元素数（按插入量在线维护）
    nonzero = 0    # 非空桶数（增量维护，用于占用估计与饱和预警）
    warn = False

    def present(key):
        return all(cnt[h(key, i)] > 0 for i in range(K))

    for o in req["ops"]:
        key, op = o["k"], o["op"]
        if op == "ins":
            n_ins += 1
            if not present(key):
                n_cur += 1
            for i in range(K):
                idx = h(key, i)
                if cnt[idx] == 0:
                    nonzero += 1
                cnt[idx] += 1
        elif op == "del":
            # 确认存在才撤回：k 个哈希位置可能碰撞到同一桶，
            # 须按去重桶的多重性确认 cnt >= 多重性，否则拒绝 —— 防计数下溢污染
            mult = {}
            for i in range(K):
                idx = h(key, i)
                mult[idx] = mult.get(idx, 0) + 1
            if all(cnt[idx] >= m for idx, m in mult.items()):
                for idx, m in mult.items():
                    cnt[idx] -= m
                    if cnt[idx] == 0:
                        nonzero -= 1
                n_del += 1
                # n_cur 是在线估计值：对误判存在的键撤回可能多减，钳位非负
                n_cur = max(0, n_cur - 1)
            else:
                rejected += 1  # 拒绝撤回：计数器完全不动
        elif op == "query":
            verdicts.append(present(key))
        if nonzero > WARN_OCCUPANCY * B:
            warn = True
        if fp_trace is not None:
            fp_trace.append((nonzero / B) ** K)

    # 在线估计：插入量估计 p_n 与桶占用校准 p_occ
    p_n = (1.0 - math.exp(-K * n_cur / B)) ** K if n_cur > 0 else 0.0
    p_occ = (nonzero / B) ** K
    res = {
        "verdicts": verdicts,
        "fp_estimate": p_occ,  # 桶占用校准后的在线假阳性率估计
        "warn": warn,
        "counts": {
            "inserted": n_ins,
            "deleted": n_del,
            "rejected_deletes": rejected,
            "current_items": n_cur,
            "bucket_sum": sum(cnt),
            "nonzero_buckets": nonzero,
            "min_counter": min(cnt),
            "max_counter": max(cnt),
            "fp_estimate_by_insertions": p_n,
        },
        "digest": hashlib.sha1(str(sorted(cnt)).encode()).hexdigest(),
    }
    if fp_trace is not None:
        res["fp_trace"] = fp_trace
    return res


def main():
    req = json.load(open(sys.argv[1]))
    json.dump(run(req), open(sys.argv[2], "w"))


if __name__ == "__main__":
    main()
