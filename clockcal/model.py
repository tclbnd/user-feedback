"""时钟映射核心。

规则总览
--------
1. 合并同设备的 sync/probe，按采集顺序号 seq 排序；同 seq 共用一个 tick。
2. 按采集顺序识别时钟回拨：当前 tick 比上一位置低超过 REGRESSION 秒即
   判定一次时钟重启，进入新 epoch。自然长间隔不产生回拨，不会被拆成重启。
3. 每个 epoch 内对可靠 sync 做稳健仿射拟合 R = slope*tick + offset，
   迭代剔除错配标记；再用 BIC + 残差结构检验判定连续的速率变化，
   在 epoch 内部拆分段（segment），速率变化段在节点处保持连续。
4. 有效范围：相邻可靠锚点间隔不超过 MAX_ANCHOR_GAP，并在覆盖两端各允许
   MAX_EXTRAP 秒外推；范围外的 probe 输出 null（未校准）。
5. 缺锚点的 epoch 不沿用旧模型，全部输出 null。
"""
import math

# ---- 规则常量（与 README 验收口径一致） ----
REGRESSION = 0.5        # 回拨门限（秒），超过即判重启
JITTER_FLOOR = 0.005    # 稳健残差门限地板 5 ms
MISMATCH_GATE = 0.020   # 残差超过 20 ms 视为错配，拒绝该标记
MAX_EXTRAP = 30.0       # 覆盖范围外最多外推 30 s
MAX_ANCHOR_GAP = 90.0   # 相邻可靠锚点最大允许间隔；真正缺标窗口达数百秒
MIN_MARKS = 5           # 可靠区间最少独立标记数
MIN_SPAN = 10.0         # 可靠区间最小时间跨度
DUP_TICK_TOL = 0.002    # tick 相差 2 ms 内视为重复时间
BIC_DELTA = 10.0        # 速率分段的 BIC 改善门限
MIN_SLOPE_DELTA = 2e-5  # 两侧速率差至少 20 ppm 才允许分段


def percentile95(values):
    """绝对残差序列的 p95。"""
    vals = list(values)
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def _ols(points):
    """普通最小二乘仿射拟合，points 为 (tick, reference) 列表。"""
    n = len(points)
    sx = sy = sxx = sxy = 0.0
    for x, y in points:
        sx += x
        sy += y
        sxx += x * x
        sxy += x * y
    det = n * sxx - sx * sx
    if abs(det) < 1e-18:
        return 1.0, sy / n
    slope = (n * sxy - sx * sy) / det
    offset = (sy - slope * sx) / n
    return slope, offset


def _residuals(points, slope, offset):
    return [y - (slope * x + offset) for x, y in points]


def robust_affine(points):
    """迭代稳健仿射拟合。

    返回 (slope, offset, inliers, residual_p95)；标记数/跨度不足时
    返回 None。错配标记（README 给出 >=150 ms 的误差）与 1.5 ms 级抖动
    之间有两个数量级的间隔，MAD 尺度 + 硬门限可以稳定分离。
    """
    if len(points) < MIN_MARKS:
        return None
    span = max(x for x, _ in points) - min(x for x, _ in points)
    if span < MIN_SPAN:
        return None
    inliers = list(points)
    for _ in range(20):
        slope, offset = _ols(inliers)
        res = _residuals(inliers, slope, offset)
        med = sorted(abs(r) for r in res)[len(res) // 2]
        scale = max(1.4826 * med, 1e-6)
        gate = max(6.0 * scale, JITTER_FLOOR)
        kept = [p for p, r in zip(inliers, res) if abs(r) <= gate]
        if len(kept) == len(inliers):
            break
        inliers = kept
        if len(inliers) < MIN_MARKS:
            return None
        span_i = max(x for x, _ in inliers) - min(x for x, _ in inliers)
        if span_i < MIN_SPAN:
            return None
    # 极端误配比例不得超过 20%
    if len(inliers) < max(MIN_MARKS, math.ceil(0.8 * len(points))):
        return None
    res = _residuals(inliers, slope, offset)
    return slope, offset, inliers, percentile95(abs(r) for r in res)


def _bic(sse, n, k):
    return n * math.log(max(sse, 1e-24) / n) + k * math.log(max(n, 1))


def _fit_segments(points, depth=0):
    """递归判定连续速率变化，返回段列表。

    每段字段: tick_lo, tick_hi, slope, offset, anchors, residual_ms。
    仅当双线模型 BIC 显著占优、残差 RMS 明显下降且速率差足够大时才拆分，
    因此没有残差趋势的自然长间隔不会触发分段。
    """
    base = robust_affine(points)
    if base is None:
        return []
    slope, offset, inliers, p95 = base
    pts = sorted(inliers)
    n = len(pts)
    sse1 = sum(r * r for r in _residuals(pts, slope, offset))
    best = None
    if depth < 2:
        for k in range(MIN_MARKS, n - MIN_MARKS + 1):
            left, right = pts[:k], pts[k:]
            if (left[-1][0] - left[0][0] < MIN_SPAN or
                    right[-1][0] - right[0][0] < MIN_SPAN):
                continue
            sl, ol = _ols(left)
            sr, orr = _ols(right)
            sse_l = sum(r * r for r in _residuals(left, sl, ol))
            sse_r = sum(r * r for r in _residuals(right, sr, orr))
            sse2 = sse_l + sse_r
            if abs(sl - sr) < MIN_SLOPE_DELTA:
                continue
            rms_l = math.sqrt(sse_l / len(left))
            rms_r = math.sqrt(sse_r / len(right))
            rms1 = math.sqrt(sse1 / n)
            if rms1 < 2.5 * max(rms_l, rms_r):
                continue  # 没有结构性残差，稀疏/长间隔不应拆段
            if _bic(sse1, n, 2) - _bic(sse2, n, 4) < BIC_DELTA:
                continue
            if best is None or sse2 < best[0]:
                best = (sse2, k)
    if best is None:
        return [dict(tick_lo=pts[0][0], tick_hi=pts[-1][0],
                     slope=slope, offset=offset, anchors=pts,
                     residual_ms=p95 * 1000.0)]
    _, k = best
    return _fit_segments(pts[:k], depth + 1) + _fit_segments(pts[k:], depth + 1)


def _valid_intervals(segments):
    """根据每段锚点计算 tick 轴上的有效区间。

    锚点间间隔超过 MAX_ANCHOR_GAP 时断开；两端各外推 MAX_EXTRAP 秒的
    *参考时间*。由于本地 tick 与 R 之间有速率 slope，外推量按
    MAX_EXTRAP / slope 换算成 tick，保证 R 轴上恰好覆盖 30 s。
    相邻段的有效区间若在节点附近重叠则合并（连续速率变化），否则保留
    缺口（隐含跳变，按未校准处理）。
    """
    spans = []
    for seg in segments:
        ticks = sorted(set(p[0] for p in seg["anchors"]))
        pad = MAX_EXTRAP / max(abs(seg["slope"]), 1e-9)
        lo = ticks[0] - pad
        cur_lo = lo
        for a, b in zip(ticks, ticks[1:]):
            if b - a > MAX_ANCHOR_GAP:
                spans.append([cur_lo, a + pad])
                cur_lo = b - pad
        spans.append([cur_lo, ticks[-1] + pad])
    spans.sort()
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def assign_epochs(events, regression=REGRESSION):
    """按 seq 顺序给事件打 epoch 标签（原地写入 epoch 字段）。

    回拨判据只看相邻采集位置的 tick 差，长间隔不触发。
    同 seq 必须共用 tick，冲突时保留先到者；未达回拨门限的乱序仅记录。
    """
    ordered = sorted(events, key=lambda e: e["seq"])
    anomalies = dict(regressions=0, reordered=0, dup_seq=0, dup_tick_groups=0)
    epoch = 0
    prev_tick = None
    prev_seq = None
    seen_ticks = []
    for ev in ordered:
        if prev_seq is not None and ev["seq"] == prev_seq:
            anomalies["dup_seq"] += 1
            if abs(ev["tick"] - prev_tick) > DUP_TICK_TOL:
                ev["tick"] = prev_tick
        if prev_tick is not None:
            if ev["tick"] < prev_tick - regression:
                epoch += 1
                anomalies["regressions"] += 1
                seen_ticks = []
            elif ev["tick"] < prev_tick:
                anomalies["reordered"] += 1
        if any(abs(ev["tick"] - t) <= DUP_TICK_TOL for t in seen_ticks):
            anomalies["dup_tick_groups"] += 1
        seen_ticks.append(ev["tick"])
        ev["epoch"] = epoch
        prev_tick = ev["tick"]
        prev_seq = ev["seq"]
    return anomalies


def _dedup_marks(sync_events):
    """合并重复时间上的标记；参考时刻冲突的保留全部交给稳健拟合裁决。"""
    groups = []
    for ev in sorted(sync_events, key=lambda e: e["tick"]):
        if groups and abs(ev["tick"] - groups[-1][0]["tick"]) <= DUP_TICK_TOL:
            groups[-1].append(ev)
        else:
            groups.append([ev])
    points, dropped = [], 0
    for g in groups:
        refs = [e["reference"] for e in g]
        if len(g) > 1 and max(refs) - min(refs) <= JITTER_FLOOR:
            tick = sum(e["tick"] for e in g) / len(g)
            ref = sum(refs) / len(refs)
            points.append((tick, ref))
            dropped += len(g) - 1
        else:
            for e in g:
                points.append((e["tick"], e["reference"]))
            if len(g) > 1:
                dropped += len(g) - 1
    return points, dropped


def fit_epoch(sync_events):
    """拟合单个 epoch，返回 epoch 结果 dict（校准失败时 calibrated=False）。"""
    points, dup_dropped = _dedup_marks(sync_events)
    result = dict(calibrated=False, coverage=None, slope=None, offset=None,
                  residual_ms=None, segments=[], valid_intervals=[],
                  anchor_count=0, rejected_marks=0,
                  duplicate_marks_merged=dup_dropped)
    segments = _fit_segments(points)
    if not segments:
        result["rejected_marks"] = len(points)
        return result
    accepted = [p for seg in segments for p in seg["anchors"]]
    all_res = [abs(r) for seg in segments
               for r in _residuals(seg["anchors"], seg["slope"], seg["offset"])]
    result.update(dict(
        calibrated=True,
        coverage=[min(p[0] for p in accepted), max(p[0] for p in accepted)],
        slope=segments[0]["slope"],
        offset=segments[0]["offset"],
        residual_ms=percentile95(all_res) * 1000.0,
        segments=segments,
        valid_intervals=_valid_intervals(segments),
        anchor_count=len(accepted),
        rejected_marks=len(points) - len(accepted),
    ))
    return result


def _segment_knots(segments):
    """相邻段在 R 上连续的交点（tick 轴节点）。"""
    knots = []
    for a, b in zip(segments, segments[1:]):
        denom = a["slope"] - b["slope"]
        if abs(denom) > 1e-12:
            knots.append((b["offset"] - a["offset"]) / denom)
        else:
            knots.append((a["tick_hi"] + b["tick_lo"]) / 2.0)
    return knots


def map_tick(epoch_result, tick):
    """按 epoch 模型把本地 tick 映射到参考时钟；无有效映射返回 None。"""
    if not epoch_result or not epoch_result["calibrated"]:
        return None
    if not any(lo <= tick <= hi for lo, hi in epoch_result["valid_intervals"]):
        return None
    segs = epoch_result["segments"]
    if len(segs) == 1:
        chosen = segs[0]
    else:
        knots = _segment_knots(segs)
        chosen = segs[-1]
        for i, knot in enumerate(knots):
            if tick <= knot:
                chosen = segs[i]
                break
    return chosen["slope"] * tick + chosen["offset"]


def align(data):
    """实现 README 规定的 JSON 接口。"""
    mapped, epochs_out, anomalies_out = [], [], dict()
    for device in sorted(data["devices"]):
        dev = data["devices"][device]
        syncs = [dict(e, kind="s") for e in dev.get("sync", [])]
        probes = [dict(e, kind="p", probe_index=i)
                  for i, e in enumerate(dev.get("probes", []))]
        events = syncs + probes
        anomalies = assign_epochs(events)
        epoch_results = dict()
        for ep in sorted(set(e["epoch"] for e in events)):
            ep_syncs = [e for e in events
                        if e["epoch"] == ep and e["kind"] == "s"]
            epoch_results[ep] = fit_epoch(ep_syncs)
        for ep, res in sorted(epoch_results.items()):
            seg_summary = [dict(
                tick_range=[s["tick_lo"], s["tick_hi"]],
                slope=s["slope"], offset=s["offset"],
                residual_ms=s["residual_ms"],
                valid_intervals=_valid_intervals([s]),
                anchor_count=len(s["anchors"]),
            ) for s in res["segments"]]
            epochs_out.append(dict(
                device=device, epoch=ep, coverage=res["coverage"],
                slope=res["slope"], offset=res["offset"],
                residual_ms=res["residual_ms"],
                anchor_count=res["anchor_count"],
                rejected_marks=res["rejected_marks"],
                segments=seg_summary,
                valid_intervals=res["valid_intervals"],
            ))
        probe_epoch = dict((e["probe_index"], e["epoch"])
                           for e in events if e["kind"] == "p")
        for i, p in enumerate(dev.get("probes", [])):
            ep = probe_epoch[i]
            ref = map_tick(epoch_results[ep], p["tick"])
            mapped.append(dict(device=device, id=p["id"], epoch=ep,
                               reference=ref))
        anomalies_out[device] = anomalies
    return dict(mapped=mapped, epochs=epochs_out, anomalies=anomalies_out)
