#!/usr/bin/env python3
"""sitemap + 变更信号驱动的重抓优先级调度器 (Python3.9+ stdlib).

信号来源与定义
==============
1. sitemap lastmod —— 过期信号 (staleness)
   * 缺失信号: 页面没有 lastmod -> 新鲜度按中性值 0.5, 标记 missing_lastmod
   * 过期信号: age_days=(now-lastmod)/86400 超过 horizon(默认7天) -> 标记 stale_lastmod
   * 新鲜度:   fresh = 1/(1 + min(age,30d)/7d), age=0 -> 1.0, age=7d -> 0.5
2. ETag —— 内容变更信号
   * 缺失信号: 历史 etag 或 etag_now 任一缺失 -> 变化信号按 0, 标记 missing_etag
   * 变化信号: 两侧都存在且不相等 -> 0.3 并标记 etag_changed; 相等 -> 0
3. hist —— 历史变更频率(次/周), 按 horizon 折算并截断到 [0,1]: min(1, hist*horizon/7)

优先级 (与 README 参考公式逐条一致)
==================================
p = 0.4*imp + 0.2*fresh + 0.2*(etag变化?0.3:0) + 0.2*hist_sig

入队规则
========
* 新发现 URL (无 lastmod、无历史 etag、etag_now 也无记录) 走快速通道,
  不占 budget, 记入 fast_lane, 仍计算 p 并参与覆盖统计。
* 其余页面按 p 降序取前 budget; 同 URL 多条记录先确定性去重合并。
* 所有排序带 URL 字典序 tie-break, 信号输入顺序变化不影响队列与 digest。
"""
import hashlib
import json
import sys

# ---- 权重与阈值 (参考实现固定值) ----
W_IMP, W_FRESH, W_ETAG, W_HIST = 0.4, 0.2, 0.2, 0.2
ETAG_DELTA = 0.3
MISSING_FRESH = 0.5
FRESH_HALF_LIFE_DAYS = 7.0
FRESH_CAP_DAYS = 30.0
DEFAULT_HORIZON_DAYS = 7
DEFAULT_NOW = 1_700_000_000
HIGH_P, MID_P = 0.66, 0.33             # 优先级分档阈值
HIGH_IMP, MID_IMP = 0.70, 0.40         # 重要度分档阈值
DAY = 86400


# ---------------- 信号计算 ----------------
def freshness_signal(lastmod, now, horizon_days):
    """返回 (新鲜度, missing_lastmod, stale_lastmod)。"""
    if lastmod is None:
        return MISSING_FRESH, True, False
    age_days = max(0.0, (now - lastmod) / DAY)
    x = min(age_days, FRESH_CAP_DAYS) / FRESH_HALF_LIFE_DAYS
    return 1.0 / (1.0 + x), False, age_days > horizon_days


def etag_signal(etag_old, etag_now):
    """返回 (变化信号值, etag_changed, missing_etag)。"""
    if not etag_old or not etag_now:
        return 0.0, False, True
    return (ETAG_DELTA if etag_old != etag_now else 0.0), etag_old != etag_now, False


def hist_signal(hist, horizon_days):
    """历史变更频率(次/周)按 horizon 折算到 [0,1], 缺失按 0。"""
    if hist is None:
        return 0.0
    return max(0.0, min(1.0, float(hist) * (horizon_days / 7.0)))


def score_page(rec, etag_now_map, now, horizon_days):
    """对合并后的单条页面记录计算优先级与全部信号细节。"""
    fresh, missing_lastmod, stale = freshness_signal(rec["lastmod"], now, horizon_days)
    etag_now = etag_now_map.get(rec["url"])
    e_sig, etag_changed, missing_etag = etag_signal(rec["etag"], etag_now)
    h_sig = hist_signal(rec["hist"], horizon_days)
    imp = rec["imp"]
    p = W_IMP * imp + W_FRESH * fresh + W_ETAG * e_sig + W_HIST * h_sig

    fast = (rec["lastmod"] is None and not rec["etag"] and not etag_now)
    tags = []
    if fast:
        tags.append("new_url")
    if etag_changed:
        tags.append("etag_changed")
    if stale:
        tags.append("stale_lastmod")
    if missing_lastmod:
        tags.append("missing_lastmod")
    if missing_etag:
        tags.append("missing_etag")
    if h_sig >= 0.5:
        tags.append("high_freq")
    return {
        "url": rec["url"],
        "p": p,
        "imp": imp,
        "reason": "+".join(tags) if tags else "priority",
        "signals": {
            "freshness": round(fresh, 6),
            "etag": round(e_sig, 6),
            "hist": round(h_sig, 6),
            "missing_lastmod": missing_lastmod,
            "stale_lastmod": stale,
            "missing_etag": missing_etag,
            "etag_changed": etag_changed,
            "fast_lane": fast,
        },
        "merged_from": rec["merged_from"],
    }


# ---------------- 去重合并 ----------------
def merge_pages(pages):
    """同 URL 多条记录确定性合并, 结果与输入顺序无关。

    imp/hist 取最大值; lastmod 取最新; etag 取“最新 lastmod 记录携带的 etag”,
    lastmod 并列时取字典序最大的 etag; 全程不依赖输入先后。
    """
    buckets = {}
    for p in pages:
        u = p["url"]
        b = buckets.setdefault(u, {"url": u, "imps": [], "lms": [],
                                   "etag_picks": [], "hists": [], "n": 0})
        b["n"] += 1
        if p.get("imp") is not None:
            b["imps"].append(float(p["imp"]))
        lm = p.get("lastmod")
        if lm is not None:
            b["lms"].append(int(lm))
        if p.get("etag"):
            b["etag_picks"].append((int(lm) if lm is not None else -1, str(p["etag"])))
        if p.get("hist") is not None:
            b["hists"].append(float(p["hist"]))

    merged = []
    for u, b in buckets.items():
        etag = max(b["etag_picks"], default=(-1, None))[1] if b["etag_picks"] else None
        merged.append({
            "url": u,
            "imp": max(b["imps"], default=0.0),
            "lastmod": max(b["lms"]) if b["lms"] else None,
            "etag": etag,
            "hist": max(b["hists"]) if b["hists"] else None,
            "merged_from": b["n"],
        })
    return merged


def _tier(p):
    return "high" if p >= HIGH_P else ("mid" if p >= MID_P else "low")


def _imp_tier(imp):
    return "high" if imp >= HIGH_IMP else ("mid" if imp >= MID_IMP else "low")


def _digest(queue, fast_lane, skipped):
    h = hashlib.sha256()
    for row in queue:
        h.update(("Q|" + row["url"] + "|" + str(row["p"]) + "\n").encode())
    for u in fast_lane:
        h.update(("F|" + u + "\n").encode())
    for u in skipped:
        h.update(("S|" + u + "\n").encode())
    return h.hexdigest()[:16]


# ---------------- 调度主流程 ----------------
def build_schedule(sig):
    now = int(sig.get("now", DEFAULT_NOW))
    horizon_days = float(sig.get("horizon", DEFAULT_HORIZON_DAYS))
    budget = int(sig.get("budget", 0))
    etag_now_map = sig.get("etag_now", {}) or {}

    merged = merge_pages(sig["pages"])
    scored = [score_page(r, etag_now_map, now, horizon_days) for r in merged]

    fast, normal = [], []
    for s in scored:
        (fast if s["signals"]["fast_lane"] else normal).append(s)

    # 确定性排序: p 降序, URL 字典序兜底
    normal.sort(key=lambda s: (-s["p"], s["url"]))
    fast.sort(key=lambda s: s["url"])

    queue_rows = normal[:budget]
    skipped_rows = normal[budget:]
    queue = [{"url": s["url"], "p": round(s["p"], 6), "reason": s["reason"]}
             for s in queue_rows]
    skipped = [s["url"] for s in skipped_rows]
    fast_lane = [s["url"] for s in fast]

    # ---- 优先级分布 (全量已知页面) ----
    distribution = {"high": 0, "mid": 0, "low": 0}
    for s in scored:
        distribution[_tier(s["p"])] += 1
    queue_distribution = {"high": 0, "mid": 0, "low": 0}
    for s in queue_rows:
        queue_distribution[_tier(s["p"])] += 1

    # ---- 覆盖统计 ----
    total_imp = sum(s["imp"] for s in scored)
    scheduled_urls = set(s["url"] for s in queue_rows) | set(fast_lane)
    weighted_coverage = (sum(s["imp"] for s in scored if s["url"] in scheduled_urls)
                         / total_imp) if total_imp > 0 else 0.0
    imp_stats = {t: {"pages": 0, "scheduled": 0} for t in ("high", "mid", "low")}
    for s in scored:
        t = _imp_tier(s["imp"])
        imp_stats[t]["pages"] += 1
        if s["url"] in scheduled_urls:
            imp_stats[t]["scheduled"] += 1
    for t, st in imp_stats.items():
        st["coverage"] = round(st["scheduled"] / st["pages"], 6) if st["pages"] else 0.0

    decisions = []
    lane_map = {s["url"]: "queue" for s in queue_rows}
    lane_map.update({s["url"]: "skipped" for s in skipped_rows})
    lane_map.update({s["url"]: "fast_lane" for s in fast})
    for s in sorted(scored, key=lambda x: x["url"]):
        decisions.append({
            "url": s["url"],
            "lane": lane_map[s["url"]],
            "p": round(s["p"], 6),
            "imp": round(s["imp"], 6),
            "reason": s["reason"],
            "merged_from": s["merged_from"],
            "signals": s["signals"],
        })

    return {
        "queue": queue,
        "skipped": skipped,
        "fast_lane": fast_lane,
        "coverage": round(weighted_coverage, 6),
        "digest": _digest(queue, fast_lane, skipped),
        "distribution": distribution,
        "decisions": decisions,
        "coverage_stats": {
            "known_pages": len(scored),
            "budget": budget,
            "budget_used": len(queue_rows),
            "scheduled": len(queue_rows) + len(fast),
            "fast_lane_count": len(fast),
            "weighted_coverage": round(weighted_coverage, 6),
            "importance_coverage": imp_stats,
            "queue_distribution": queue_distribution,
        },
    }


def run(sig):
    return build_schedule(sig)


def main():
    if len(sys.argv) != 3:
        print("usage: python3 prio.py signals.json output.json", file=sys.stderr)
        sys.exit(2)
    result = run(json.load(open(sys.argv[1])))
    with open(sys.argv[2], "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
    cs = result["coverage_stats"]
    print("digest=" + result["digest"] +
          " distribution=" + str(result["distribution"]) +
          " queue=" + str(len(result["queue"])) +
          " fast_lane=" + str(len(result["fast_lane"])) +
          " skipped=" + str(len(result["skipped"])) +
          " coverage=" + str(result["coverage"]))


if __name__ == "__main__":
    main()
