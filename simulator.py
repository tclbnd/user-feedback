#!/usr/bin/env python3
"""虚拟时钟双周期重抓调度模拟器 (Python3.9+ stdlib).

模拟一个 60 页站点在两个 7 天周期内的变化, 注入 sitemap lastmod 变更与 ETag 变更,
对比“全量重抓”基线与 prio.build_schedule 的预算调度, 并校验:
  1. 高重要度变更页在预算内全部被抓;
  2. 未变页抓取数相对全量基线下降 >= 60%;
  3. 随机重排信号输入顺序后队列摘要 (digest/queue/fast_lane/skipped/分布/覆盖) 一致。
"""
import copy
import json
import random

import prio

DAY = 86400
WEEK = 7 * DAY
T0 = 1_700_000_000
BUDGET = 20


class VirtualClock:
    def __init__(self, start):
        self.now = start

    def advance(self, seconds):
        self.now += seconds


def build_site():
    """60 页: 10 high / 20 mid / 30 low, 初始 lastmod 散布在过去 0~20 天。"""
    rnd = random.Random(20261115)
    state = {}
    for i in range(60):
        if i < 10:
            imp = round(rnd.uniform(0.75, 0.95), 3)
        elif i < 30:
            imp = round(rnd.uniform(0.40, 0.60), 3)
        else:
            imp = round(rnd.uniform(0.05, 0.25), 3)
        age_days = rnd.uniform(0, 20)
        lm = int(T0 - age_days * DAY)
        state["https://site/p%02d" % i] = {
            "imp": imp,
            "lastmod": lm,                 # sitemap 声明的 lastmod
            "etag": "e0-%02d" % i,         # 当前内容 ETag
            "etag_known": "e0-%02d" % i,   # 上次抓取留存的 ETag
            "discovered": True,
            "hist": round(rnd.uniform(0, 0.4), 3),
        }
    return state, rnd


def inject_change(state, url, now, tag, change_log):
    """模拟页面真实变更: 内容 ETag 与 sitemap lastmod 同时更新, 并记入变更日志。"""
    st = state[url]
    st["etag"] = tag
    st["lastmod"] = now
    st["_changed"] = True
    change_log.setdefault(url, []).append(now)


def discover(state, url, imp):
    """新发布 URL: sitemap 尚未给 lastmod, 本地也没有任何 ETag 记录。"""
    state[url] = {
        "imp": imp,
        "lastmod": None,
        "etag": None,
        "etag_known": None,
        "discovered": False,
        "hist": None,
    }


def build_feed(state, now, horizon_days, change_log, dup_url=None):
    """按当前站点状态构造 prio 输入信号。

    hist(次/周) = 基线频率 + 过去 7 天 [now-7d, now] 变更日志计数, 截断到 1;
    新发现 URL 无 lastmod / 无历史 etag, 且 etag_now 无记录 -> 快速通道。
    """
    pages, etag_now = [], {}
    window_start = now - 7 * DAY
    for url in sorted(state):
        st = state[url]
        if not st["discovered"]:
            pages.append({"url": url, "imp": st["imp"]})
            continue
        observed = sum(1 for ts in change_log.get(url, []) if window_start <= ts <= now)
        hist = min(1.0, (st["hist"] or 0.0) + observed)
        pages.append({
            "url": url,
            "imp": st["imp"],
            "lastmod": st["lastmod"],
            "etag": st["etag_known"],
            "hist": round(hist, 6),
        })
        if st["etag"] is not None:
            etag_now[url] = st["etag"]
    if dup_url:  # 注入一条重复记录, 验证去重合并
        pages.append(copy.deepcopy(next(p for p in pages if p["url"] == dup_url)))
    return {"pages": pages, "etag_now": etag_now, "budget": BUDGET,
            "horizon": horizon_days, "now": now}


def shuffled_copy(feed, rnd):
    f2 = copy.deepcopy(feed)
    rnd.shuffle(f2["pages"])
    items = list(f2["etag_now"].items())
    rnd.shuffle(items)
    f2["etag_now"] = dict(items)
    return f2


def simulate_crawl(state, scheduled, now, changed):
    """模拟抓取: 被抓页面留存最新 ETag, 新页在下周期转为已发现。返回未变页抓取数。"""
    wasted = 0
    for url in scheduled:
        st = state[url]
        if not st["discovered"]:
            st["discovered"] = True
            st["lastmod"] = now
            st["etag"] = "new-" + url.rsplit("/", 1)[1]
        st["etag_known"] = st["etag"]
        if url not in changed:
            wasted += 1
    return wasted


def run_cycle(state, clock, rnd, changed, new_urls, label, change_log):
    for u in changed:
        inject_change(state, u, clock.now,
                      label + "-" + u.rsplit("/", 1)[1], change_log)
    for u, imp in new_urls:
        discover(state, u, imp)

    dup = changed[0] if changed else None
    feed = build_feed(state, clock.now, 7, change_log, dup_url=dup)
    result = prio.run(feed)

    # 信号输入顺序随机重排, 队列摘要必须一致
    shuffled = prio.run(shuffled_copy(feed, rnd))
    for key in ("queue", "skipped", "fast_lane", "coverage", "digest", "distribution"):
        assert shuffled[key] == result[key], "信号重排后 %s 不一致" % key
    assert shuffled["coverage_stats"] == result["coverage_stats"]

    scheduled = [q["url"] for q in result["queue"]] + result["fast_lane"]
    wasted = simulate_crawl(state, scheduled, clock.now, set(changed))

    # 全量重抓基线: 所有已发现+新发现页面都抓, 未变页 = 总页 - 真实变更页
    total_pages = len(state)
    full_wasted = total_pages - len(changed)
    reduction = 1.0 - wasted / full_wasted if full_wasted else 1.0

    high_changed = [u for u in changed if state[u]["imp"] >= 0.70]
    high_missed = [u for u in high_changed if u not in scheduled]

    report = {
        "label": label,
        "now": clock.now,
        "pages": total_pages,
        "budget": BUDGET,
        "changed": sorted(changed),
        "new_urls": [u for u, _ in new_urls],
        "scheduled": scheduled,
        "wasted_scheduled": wasted,
        "full_crawl_wasted": full_wasted,
        "unchanged_reduction": round(reduction, 4),
        "high_changed": sorted(high_changed),
        "high_changed_missed": sorted(high_missed),
        "digest": result["digest"],
        "distribution": result["distribution"],
        "coverage": result["coverage"],
        "coverage_stats": result["coverage_stats"],
        "queue": result["queue"],
        "fast_lane": result["fast_lane"],
        "skipped": result["skipped"],
        "decisions": result["decisions"],
    }
    assert not high_missed, "高重要度变更页未在预算内抓取: %s" % high_missed
    assert reduction >= 0.60, "未变页抓取下降不足 60%%: %.2f" % reduction
    return report, result


def main():
    clock = VirtualClock(T0)
    state, rnd = build_site()
    change_log = {}
    reports = []

    # ---- 周期 1: 4 高 + 3 中 + 2 低 变更, 2 个新发布 URL ----
    high = sorted(u for u in state if state[u]["imp"] >= 0.70)
    mid = sorted(u for u in state if 0.40 <= state[u]["imp"] < 0.70)
    low = sorted(u for u in state if state[u]["imp"] < 0.40)
    c1_changed = high[:4] + mid[:3] + low[:2]
    c1_new = [("https://site/new-a", 0.88), ("https://site/new-b", 0.12)]
    r1, _ = run_cycle(state, clock, rnd, c1_changed, c1_new, "cycle1", change_log)
    reports.append(r1)

    # 周期间累积: 周期1变更过的页面 hist 已在抓取后进入历史
    clock.advance(WEEK)

    # ---- 周期 2: 3 高 + 2 中 变更, 2 个新发布 URL; hist 反映上周真实变更 ----
    c2_changed = high[4:7] + mid[3:5]
    c2_new = [("https://site/new-c", 0.81), ("https://site/new-d", 0.20)]
    r2, _ = run_cycle(state, clock, rnd, c2_changed, c2_new, "cycle2", change_log)
    reports.append(r2)

    summary = {
        "cycles": reports,
        "acceptance": {
            "high_changed_all_crawled": all(not r["high_changed_missed"] for r in reports),
            "min_unchanged_reduction": min(r["unchanged_reduction"] for r in reports),
            "shuffle_invariant": True,
        },
    }
    with open("sim_report.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)
    for r in reports:
        print("%s: scheduled=%d wasted=%d full_wasted=%d reduction=%.1f%% "
              "high_changed=%d missed=%d digest=%s dist=%s coverage=%.3f"
              % (r["label"], len(r["scheduled"]), r["wasted_scheduled"],
                 r["full_crawl_wasted"], r["unchanged_reduction"] * 100,
                 len(r["high_changed"]), len(r["high_changed_missed"]),
                 r["digest"], r["distribution"], r["coverage"]))
    print("acceptance:", summary["acceptance"])


if __name__ == "__main__":
    main()