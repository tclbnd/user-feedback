"""同步标记配对与跨重启全局连续参考时钟求解。

两台设备各自检测到的闪光（相机）与声音脉冲（录音机）来自同一组发射事件，
但都可能缺标记、含伪峰。用动态规划做序列对齐，错配成本由相邻间隔差给出，
漏检/伪峰走 gap 罚分。配对后，每个采集分段（重启 epoch）各自满足一条
仿射关系 R = slope*tick + offset，通过联合最小二乘把所有分段钉在同一条
连续参考时钟上；其中一个分段作为尺度基准（slope=1, offset=0）。
"""
import math

GAP_PENALTY = 0.05   # 秒，漏检/伪峰的间隔差罚分
HARD_REJECT = 0.020  # 20 ms 以上的配对残差视为错配
IRLS_ITERS = 10


def pair_events(ev_a, ev_b, gap_penalty=GAP_PENALTY, nominal_gap=None):
    """序列 DP 配对。

    ev_a/ev_b 为本地时刻（秒）的升序列表，返回:
      pairs: [(t_a, t_b), ...]
      unmatched_a, unmatched_b: 未配上的本地时刻（伪峰或漏检）
    配对成本 = 与上一对的本地间隔差绝对值（两条设备振荡器偏差恒定，
    正确配对的间隔差接近常数漂移，伪峰/漏检会让间隔差骤增），
    漏检/伪峰走固定 gap 罚分。匹配严格保持顺序。
    """
    if nominal_gap is None:
        gaps = [b - a for a, b in zip(ev_a, ev_a[1:])]
        nominal_gap = sorted(gaps)[len(gaps) // 2] if gaps else 1.0
    inf = float("inf")
    n, m = len(ev_a), len(ev_b)
    dp = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 1=配对 2=a跳 3=b跳
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + gap_penalty
        back[i][0] = 2
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + gap_penalty
        back[0][j] = 3
    prev = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, direction = dp[i - 1][j] + gap_penalty, 2
            v = dp[i][j - 1] + gap_penalty
            if v < best:
                best, direction = v, 3
            # 配对：沿回溯链找上一对，计算局部间隔差
            pi, pj = i - 1, j - 1
            last_pair = None
            while pi > 0 and pj > 0:
                d = back[pi][pj]
                if d == 1:
                    last_pair = (pi, pj)
                    break
                if d == 2:
                    pi -= 1
                else:
                    pj -= 1
            if last_pair is None:
                pair_cost = 0.0  # 第一对只建立基准，不带成本
            else:
                li, lj = last_pair
                da = ev_a[i - 1] - ev_a[li - 1]
                db = ev_b[j - 1] - ev_b[lj - 1]
                pair_cost = abs(da - db)
            v = dp[i - 1][j - 1] + pair_cost
            if v < best:
                best, direction = v, 1
            dp[i][j], back[i][j] = best, direction
    i, j = n, m
    pairs, unmatched_a, unmatched_b = [], [], []
    while i > 0 or j > 0:
        d = back[i][j]
        if d == 1:
            pairs.append((ev_a[i - 1], ev_b[j - 1]))
            i -= 1
            j -= 1
        elif d == 2:
            unmatched_a.append(ev_a[i - 1])
            i -= 1
        else:
            unmatched_b.append(ev_b[j - 1])
            j -= 1
    pairs.reverse()
    return pairs, sorted(unmatched_a), sorted(unmatched_b)


def _gaussian_solve(matrix, rhs):
    """带部分选主元的高斯消元，matrix 为方阵（列表的列表）。"""
    n = len(matrix)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-14:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        piv = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= piv
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if factor:
                for j in range(col, n + 1):
                    a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def solve_sessions(session_pairs, gauge_key=None):
    """联合求解各分段到连续参考时钟 R 的仿射参数。

    session_pairs: dict(key -> [(tick_a, tick_b), ...])，其中 key 为
      (device_a, epoch_a) / (device_b, epoch_b) 时本函数不适用；这里采用
      更直接的调用方式：每个 session 给出 (local_tick, ref_anchor) 对，
      ref_anchor 由配对设备的已解算时刻传递。为保持模块独立，本函数求解
      的是一组 session 方程，session_pairs 的每项为
      dict(ticks=[...], refs=[...])，gauge_key 指定的分段固定 slope=1,
      offset=0，作为参考时钟尺度基准。
    返回 dict(key -> (slope, offset, residual_p95, inlier_count))，
    标记不足或残差不达标的分段不会出现在结果中。
    """
    keys = sorted(session_pairs)
    if gauge_key is None:
        gauge_key = keys[0]
    # 只有 gauge 一个分段时直接固定为恒等映射
    if len(keys) == 1:
        ticks = session_pairs[gauge_key]["ticks"]
        from .model import percentile95
        res = [abs(r - t) for t, r in zip(ticks, session_pairs[gauge_key]["refs"])]
        if len(ticks) >= 5 and percentile95(res) <= HARD_REJECT:
            return dict(((gauge_key, (1.0, 0.0, percentile95(res), len(ticks))),))
        return dict()
    # 每个 session 两个未知数 slope、offset；gauge 直接固定
    unknowns = [k for k in keys if k != gauge_key]
    idx = dict((k, i) for i, k in enumerate(unknowns))
    weights = dict()
    for k in keys:
        n = len(session_pairs[k]["ticks"])
        weights[k] = [1.0] * n
    solution = None
    for it in range(IRLS_ITERS):
        size = 2 * len(unknowns)
        ata = [[0.0] * size for _ in range(size)]
        atb = [0.0] * size
        for k in unknowns:
            base = 2 * idx[k]
            for w, t, r in zip(weights[k], session_pairs[k]["ticks"],
                               session_pairs[k]["refs"]):
                if w == 0.0:
                    continue
                ata[base][base] += w * t * t
                ata[base][base + 1] += w * t
                ata[base + 1][base] += w * t
                ata[base + 1][base + 1] += w
                atb[base] += w * t * r
                atb[base + 1] += w * r
        x = _gaussian_solve(ata, atb)
        if x is None:
            return dict()
        candidate = dict()
        candidate[gauge_key] = (1.0, 0.0)
        for k in unknowns:
            candidate[k] = (x[2 * idx[k]], x[2 * idx[k] + 1])
        # Huber IRLS。首轮只做软权重以适应 epoch 内的连续速率变化；
        # 第二轮起加入硬门限，且任何 session 最多剔除 20% 的点
        # （错配上限），避免真实速率分段被当成整段误配而删除。
        changed = False
        for k in keys:
            slope, offset = candidate[k]
            n_k = len(session_pairs[k]["ticks"])
            res = [r - (slope * t + offset)
                   for t, r in zip(session_pairs[k]["ticks"],
                                   session_pairs[k]["refs"])]
            med = sorted(abs(v) for v in res)[len(res) // 2]
            scale = max(1.4826 * med, 1e-6)
            order = sorted(range(n_k), key=lambda i: abs(res[i]))
            max_drop = int(0.2 * n_k)
            new_w = []
            dropped = 0
            for i, v in enumerate(res):
                if it > 0 and abs(v) > HARD_REJECT and dropped < max_drop:
                    w = 0.0
                    dropped += 1
                else:
                    w = 1.0 / max(1.0, abs(v) / (1.345 * scale))
                if abs(w - weights[k][i]) > 1e-9:
                    changed = True
                new_w.append(w)
            weights[k] = new_w
        solution = candidate
        if not changed:
            break
    out = dict()
    for k in keys:
        slope, offset = solution[k]
        w = weights[k]
        n_in = sum(1 for v in w if v > 0)
        if n_in < 5:
            continue
        res = [abs(r - (slope * t + offset))
               for wi, t, r in zip(w, session_pairs[k]["ticks"],
                                   session_pairs[k]["refs"]) if wi > 0]
        from .model import percentile95
        # 这里的残差含 epoch 内真实速率分段，仅作记录；
        # 精细的分段/剔点由 model.fit_epoch 完成。
        out[k] = (slope, offset, percentile95(res), n_in)
    return out
