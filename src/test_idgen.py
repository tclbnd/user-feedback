#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""idgen.py 验收测试。

覆盖：
  1. 100ms 回拨（等待）+ 50ms 突跳 + 600ms 超限回拨（漂移位接管）
  2. 序号耗尽等待下一毫秒 / 漂移期序号耗尽逻辑跳毫秒
  3. 16 线程并发 10^6 ID：全局唯一、按生成序单调
  4. 重启持久化（进程内 + 跨进程 CLI）：首 ID 时间位 >= 关闭前最后值
  5. 批大小 / 并发无关性：同样时钟轨迹产出同样 ID 序列
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import idgen  # noqa: E402
from idgen import (  # noqa: E402
    SnowflakeGenerator, VirtualClock, IdResult,
    ALERT_BACKWARD_WAIT, ALERT_SEQ_WAIT, ALERT_DRIFT_TAKEOVER,
    ALERT_DRIFT_EXTENDED, ALERT_DRIFT_RECOVER,
    ALERT_STARTUP_WAIT, ALERT_STARTUP_DRIFT,
    SEQ_MAX, SEQ_MOD,
)

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


def assert_monotonic_unique(rows, label):
    """rows: list[IdResult]（按生成序）。校验严格单调 + 全局唯一 + 时间位不减。"""
    check("%s: 数量正确" % label, len(rows) > 0)
    seen = set()
    prev_id, prev_ts = -1, -1
    ok_unique = ok_mono = ok_ts = ok_decode = True
    for r in rows:
        if r.id in seen:
            ok_unique = False
        seen.add(r.id)
        if r.id <= prev_id:
            ok_mono = False
        if r.ts < prev_ts:
            ok_ts = False
        if r.id != r.ts * SEQ_MOD + r.seq:
            ok_decode = False
        if not (0 <= r.seq <= SEQ_MAX):
            ok_decode = False
        prev_id, prev_ts = r.id, r.ts
    check("%s: 全局唯一" % label, ok_unique)
    check("%s: 按生成序严格单调" % label, ok_mono)
    check("%s: 时间位单调不减" % label, ok_ts)
    check("%s: id = ts*4096+seq 解码一致且 seq 合法" % label, ok_decode)


# ---------------------------------------------------------------------- #
def test_rollback_and_jump():
    print("\n== 用例1: 100ms 回拨 / 50ms 突跳 / 600ms 超限漂移 (阈值500ms) ==")
    clock = VirtualClock()
    gen = SnowflakeGenerator(max_backward_ms=500, clock=clock,
                             persist_every=1)
    rows = []
    pre_rollback_ts = None

    def take(tag):
        r = gen.next_id_detail()
        rows.append(r)
        print("    %-22s phys=%5d ts=%5d seq=%3d src=%-6s drift=%d id=%d"
              % (tag, r.phys, r.ts, r.seq, r.src, r.drift, r.id))
        return r

    clock.set_ms(1000); take("正常 t=1000")
    clock.set_ms(1000); r = take("同毫秒 t=1000 seq+1")
    pre_rollback_ts = r.ts

    # 注入 100ms 回拨：未超 500ms 阈值 -> 等待策略
    clock.set_ms(900)
    r = take("回拨-100ms t=900(等待)")
    check("回拨期时间位不小于回拨前", r.ts >= pre_rollback_ts,
          "r.ts=%d pre=%d" % (r.ts, pre_rollback_ts))
    check("回拨等待来源 src=wait", r.src == "wait")
    check("虚拟钟已被等待推平到 last_ts", clock.now_ms() >= pre_rollback_ts)

    # 注入 50ms 突跳（向前）
    clock.set_ms(1050)
    r = take("突跳+50ms t=1050")
    check("向前突跳正常分配 src=normal", r.src == "normal")
    check("突跳后时间位推进", r.ts == 1050)

    # 再次 50ms 回拨（仍在阈值内）
    ts_before = r.ts
    clock.set_ms(1000)
    r = take("回拨-50ms t=1000(等待)")
    check("50ms 回拨时间位不回退", r.ts >= ts_before and r.src == "wait")

    # 注入 600ms 超限回拨 -> 漂移位接管
    ts_before = r.ts
    clock.set_ms(500)
    r = take("回拨-600ms t=500(漂移接管)")
    check("超限回拨进入漂移 src=drift", r.src == "drift" and r.drift == 1)
    check("漂移期时间位不小于回拨前", r.ts >= ts_before,
          "r.ts=%d before=%d" % (r.ts, ts_before))
    takeover = [a for a in gen.alerts if a["type"] == ALERT_DRIFT_TAKEOVER]
    check("产生 DRIFT_TAKEOVER 告警", len(takeover) == 1, str(gen.alerts))

    # 漂移期内物理钟再次回退 -> 偏移扩展，逻辑时间位仍前进
    clock.set_ms(400)
    r1 = take("漂移中再回退 t=400")
    clock.set_ms(300)
    r2 = take("漂移中再回退 t=300")
    check("漂移扩展后仍 src=drift", r1.src == "drift" and r2.src == "drift")
    check("漂移期同毫秒内时间位保持（序号前进）", r2.ts == r1.ts and r2.seq > r1.seq)
    check("产生 DRIFT_EXTENDED 告警",
          len([a for a in gen.alerts if a["type"] == ALERT_DRIFT_EXTENDED]) >= 1)

    # 漂移期序号耗尽：逻辑时间位主动 +1ms，不依赖物理钟（仍停在 300）
    # r2 处 seq=4，先推进到 seq=4095（SEQ_MAX-4 个），再取一次即触发耗尽跳毫秒
    drift_exhaust = [r2]
    for _ in range(SEQ_MAX - r2.seq):
        drift_exhaust.append(gen.next_id_detail())
    check("耗尽前最后一个序号为 4095", drift_exhaust[-1].seq == SEQ_MAX)
    jump = gen.next_id_detail()  # 触发耗尽：逻辑跳毫秒
    drift_exhaust.append(jump)
    check("漂移耗尽后逻辑时间位主动前进", jump.ts > r2.ts,
          "jump.ts=%d r2.ts=%d" % (jump.ts, r2.ts))
    check("漂移耗尽跳毫秒后序号归零且仍 drift",
          jump.seq == 0 and jump.src == "drift" and jump.drift == 1)
    check("跳毫秒不依赖物理钟（phys 仍为 300）", jump.phys == 300)
    rows.extend(drift_exhaust[1:])

    # 物理钟恢复并追平 -> 退出漂移
    clock.set_ms(jump.ts + 50)
    r = take("物理钟恢复 src 回 normal")
    check("追平后退出漂移 src=normal", r.src == "normal" and r.drift == 0)
    check("产生 DRIFT_RECOVER 告警",
          len([a for a in gen.alerts if a["type"] == ALERT_DRIFT_RECOVER]) == 1)
    check("回拨等待告警数量正确",
          len([a for a in gen.alerts if a["type"] == ALERT_BACKWARD_WAIT]) == 2)

    assert_monotonic_unique(rows, "用例1")
    gen.close()


def test_sequence_exhaustion():
    print("\n== 用例2: 序号耗尽等待下一毫秒 + 漂移期耗尽逻辑跳毫秒 ==")
    clock = VirtualClock()
    gen = SnowflakeGenerator(max_backward_ms=500, clock=clock)
    clock.set_ms(2000)
    rows = [gen.next_id_detail() for _ in range(SEQ_MAX + 1)]  # 4096 个
    check("同毫秒前 4096 个序号 0..4095",
          [r.seq for r in rows] == list(range(SEQ_MAX + 1)))
    check("耗尽前均在 t=2000", all(r.ts == 2000 for r in rows))
    r = gen.next_id_detail()  # 第 4097 个：序号耗尽 -> 等待下一毫秒
    rows.append(r)
    check("第 4097 个等待到下一毫秒 t=2001", r.ts == 2001 and r.seq == 0)
    check("耗尽来源 src=wait", r.src == "wait")
    check("序号耗尽告警存在",
          len([a for a in gen.alerts if a["type"] == ALERT_SEQ_WAIT]) == 1)
    check("虚拟钟被推进到 2001", clock.now_ms() == 2001)
    assert_monotonic_unique(rows, "用例2-等待")

    # 漂移期内耗尽：不能睡眠等物理钟，逻辑时间位直接 +1ms
    clock.set_ms(100)  # 制造 1901ms 超限回拨
    r = gen.next_id_detail()
    check("超限回拨进入漂移", r.src == "drift" and r.drift == 1)
    drift_rows = [r]
    for _ in range(SEQ_MAX + 2):  # 耗尽多次
        drift_rows.append(gen.next_id_detail())
    check("漂移期耗尽后仍 src=drift",
          all(x.src == "drift" for x in drift_rows[1:]))
    check("漂移期时间位持续前进不依赖物理钟", clock.now_ms() == 100)
    assert_monotonic_unique(drift_rows, "用例2-漂移耗尽")
    gen.close()


def test_concurrency():
    print("\n== 用例3: 16 线程并发 10^6 ID（真实时钟）==")
    total = 1_000_000
    threads = 16
    per = total // threads
    generated = []
    lock = threading.Lock()

    def collect(r: IdResult):
        # 在生成器锁内回调，顺序即真实生成序
        with lock:
            generated.append(r)

    gen = SnowflakeGenerator(listener=collect, persist_every=100_000)
    t0 = time.time()

    def worker():
        for _ in range(per):
            gen.next_id()

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(worker) for _ in range(threads)]
        for f in futures:
            f.result()
    elapsed = time.time() - t0
    gen.close()

    check("产出数量 = 10^6", len(generated) == total,
          "got %d" % len(generated))
    assert_monotonic_unique(generated, "用例3")
    span_ms = generated[-1].ts - generated[0].ts
    print("    耗时 %.2fs, 吞吐 %d ID/s, 时间位跨度 %dms, src 分布=%s"
          % (elapsed, int(total / elapsed), span_ms,
             {k: sum(1 for r in generated if r.src == k)
              for k in ("normal", "wait", "drift")}))
    check("全部为 normal/wait（真实时钟无注入回拨）",
          all(r.src in ("normal", "wait") for r in generated))


def test_concurrency_with_rollback_injection():
    print("\n== 用例3b: 16 线程取号与时钟回拨/突跳注入交织（虚拟时钟）==")
    total = 200_000
    threads_n = 16
    per = total // threads_n
    clock = VirtualClock(start_ms=1_000_000)
    generated = []
    lock = threading.Lock()

    def collect(r: IdResult):
        with lock:
            generated.append(r)

    gen = SnowflakeGenerator(max_backward_ms=200, clock=clock,
                             listener=collect, persist_every=100_000)
    stop = threading.Event()

    def clock_poker():
        """独立线程持续注入回拨（阈值内/超限）与突跳。"""
        base = 1_000_000
        i = 0
        while not stop.is_set():
            i += 1
            if i % 3 == 0:
                clock.set_ms(base - 80)      # 80ms 回拨（阈值内 -> 等待）
            elif i % 3 == 1:
                clock.set_ms(base - 800)     # 800ms 回拨（超限 -> 漂移）
            else:
                base += 5
                clock.set_ms(base)           # 正常向前 / 小突跳
            time.sleep(0.0002)

    poker = threading.Thread(target=clock_poker)
    poker.start()

    def worker():
        for _ in range(per):
            gen.next_id()

    with ThreadPoolExecutor(max_workers=threads_n) as pool:
        futures = [pool.submit(worker) for _ in range(threads_n)]
        for f in futures:
            f.result()
    stop.set()
    poker.join()
    gen.close()

    check("产出数量 = 200000", len(generated) == total)
    assert_monotonic_unique(generated, "用例3b")
    src_dist = {k: sum(1 for r in generated if r.src == k)
                for k in ("normal", "wait", "drift")}
    alert_types = {a["type"] for a in gen.alerts}
    print("    src 分布=%s, 告警类型=%s" % (src_dist, sorted(alert_types)))
    check("回拨等待与漂移接管在并发下均真实发生",
          src_dist["wait"] > 0 and src_dist["drift"] > 0)
    check("并发回拨下时间位始终不小于回拨前（单调检查已覆盖）",
          all(generated[i].ts <= generated[i + 1].ts
              for i in range(len(generated) - 1)))


def test_restart_persistence():
    print("\n== 用例4: 持久化重启校验（进程内 + 跨进程）==")
    tmpdir = tempfile.mkdtemp(prefix="idgen-state-")
    state = os.path.join(tmpdir, "state.json")
    clock = VirtualClock()

    # --- 第一轮：正常生成并关闭 ---
    gen1 = SnowflakeGenerator(state_path=state, clock=clock,
                              max_backward_ms=500, persist_every=1)
    clock.set_ms(5000)
    rows1 = [gen1.next_id_detail() for _ in range(5)]
    last_ts_before_close = gen1.last_ts
    gen1.close()
    saved = json.load(open(state, encoding="utf-8"))
    check("状态文件持久化 last_ts/last_seq",
          saved["last_ts"] == last_ts_before_close
          and saved["last_seq"] == rows1[-1].seq)

    # --- 第二轮：物理钟落后 100ms（阈值内）-> 启动等待 ---
    clock.set_ms(last_ts_before_close - 100)
    gen2 = SnowflakeGenerator(state_path=state, clock=clock,
                              max_backward_ms=500)
    check("启动等待告警",
          len([a for a in gen2.alerts if a["type"] == ALERT_STARTUP_WAIT]) == 1)
    r = gen2.next_id_detail()
    check("重启首 ID 时间位 >= 关闭前最后值（等待路径）",
          r.ts >= last_ts_before_close,
          "r.ts=%d last=%d" % (r.ts, last_ts_before_close))
    gen2.close()

    # --- 第三轮：物理钟落后 900ms（超 500ms 阈值）-> 启动即漂移 ---
    saved2 = json.load(open(state, encoding="utf-8"))
    anchor = saved2["last_ts"]
    clock.set_ms(anchor - 900)
    gen3 = SnowflakeGenerator(state_path=state, clock=clock,
                              max_backward_ms=500)
    check("启动漂移告警",
          len([a for a in gen3.alerts if a["type"] == ALERT_STARTUP_DRIFT]) == 1)
    check("启动即处于漂移态", gen3.in_drift and gen3.drift_offset == 900)
    r = gen3.next_id_detail()
    check("重启首 ID 时间位 >= 关闭前最后值（漂移路径）",
          r.ts >= anchor, "r.ts=%d anchor=%d" % (r.ts, anchor))
    check("首 ID 来源 drift", r.src == "drift")
    gen3.close()

    # --- 第四轮：状态文件损坏不崩溃，按无状态启动 ---
    with open(state, "w", encoding="utf-8") as f:
        f.write("{broken json")
    gen4 = SnowflakeGenerator(state_path=state, clock=clock)
    check("损坏状态文件容错启动", gen4.last_ts == 0)
    gen4.close()

    # --- 跨进程：CLI 写状态，第二个 CLI 进程在回拨时钟下读状态 ---
    req1 = {"clock": [10.0, 10.001, 10.002], "batch": 1,
            "state": state, "max_backward_ms": 500}
    out1 = os.path.join(tmpdir, "o1.json")
    req1p = os.path.join(tmpdir, "r1.json")
    json.dump(req1, open(req1p, "w"))
    subprocess.run([sys.executable, os.path.join(WORK, "idgen.py"),
                    req1p, out1], check=True, capture_output=True)
    res1 = json.load(open(out1, encoding="utf-8"))
    cross_last_ts = res1["meta"][-1]["ts"]
    # 第二个进程：脚本时钟整体比上次落后 800ms（超过阈值）
    req2 = {"clock": [9.2, 9.201], "batch": 1, "state": state,
            "max_backward_ms": 500}
    out2 = os.path.join(tmpdir, "o2.json")
    req2p = os.path.join(tmpdir, "r2.json")
    json.dump(req2, open(req2p, "w"))
    proc = subprocess.run([sys.executable, os.path.join(WORK, "idgen.py"),
                           req2p, out2], capture_output=True, text=True)
    check("跨进程 CLI 正常退出", proc.returncode == 0, proc.stderr)
    res2 = json.load(open(out2, encoding="utf-8"))
    check("跨进程重启首 ID 时间位 >= 上一进程最后值",
          res2["meta"][0]["ts"] >= cross_last_ts,
          "%s vs %s" % (res2["meta"][0]["ts"], cross_last_ts))
    check("跨进程首 ID 为漂移来源", res2["meta"][0]["src"] == "drift")
    check("跨进程告警包含 STARTUP_DRIFT_TAKEOVER",
          any(a["type"] == ALERT_STARTUP_DRIFT for a in res2["alerts"]))


def test_batch_and_concurrency_independence():
    print("\n== 用例5: 生成行为与批大小 / 并发方式无关 ==")
    # 不变量：时钟观测计划（观测点 + 每个观测点取号数）固定时，
    # 无论请求被切成多大的批，取到的 ID 序列必须逐 ID 完全一致。
    # 观测计划本身与 batch 无关，保证不同 batch 看到的时钟轨迹相同。
    plan = []
    t = 10_000
    rng = []
    for i in range(120):
        if i and i % 17 == 0:
            t -= 30          # 周期性 30ms 回拨（阈值内，走等待）
        elif i and i % 23 == 0:
            t += 40          # 周期性突跳
        else:
            t += 1
        draw = (i % 5) + 1   # 每个观测点取 1..5 个 ID
        plan.append((t, draw))
        rng.append(draw)
    total_n = sum(rng)

    def build(batch):
        clock = VirtualClock()
        gen = SnowflakeGenerator(max_backward_ms=500, clock=clock)
        out = []
        remaining = list(plan)
        while remaining:
            obs_t, draws = remaining.pop(0)
            clock.set_ms(obs_t)
            done = 0
            while done < draws:
                n = min(batch, draws - done)
                for _ in range(n):
                    out.append(gen.next_id_detail())
                done += n
        gen.close()
        return out

    rows1 = build(1)
    rows5 = build(5)
    rows16 = build(16)
    ids1 = [r.id for r in rows1]
    check("产出总数与观测计划一致", len(rows1) == total_n
          and len(rows5) == total_n and len(rows16) == total_n)
    check("batch=1/5/16 在同一时钟轨迹下 ID 序列逐 ID 一致",
          [r.id for r in rows5] == ids1 and [r.id for r in rows16] == ids1)
    check("三种批大小的 (ts,seq,src,drift) 解码完全一致",
          [r.meta() for r in rows5] == [r.meta() for r in rows1]
          and [r.meta() for r in rows16] == [r.meta() for r in rows1])
    check("序列严格单调且唯一",
          all(ids1[i] < ids1[i + 1] for i in range(len(ids1) - 1))
          and len(set(ids1)) == total_n)

    # 并发方式无关：同一起点并发取号，结果集合/唯一性与串行一致
    clock = VirtualClock()
    gen = SnowflakeGenerator(clock=clock, max_backward_ms=500)
    clock.set_ms(7000)
    serial = [gen.next_id() for _ in range(2000)]
    gen.close()

    clock2 = VirtualClock()
    gen2 = SnowflakeGenerator(clock=clock2, max_backward_ms=500)
    clock2.set_ms(7000)
    concurrent = []

    def grab(n):
        for _ in range(n):
            concurrent.append(gen2.next_id())

    workers = [threading.Thread(target=grab, args=(500,)) for _ in range(4)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    gen2.close()
    check("并发取号数量正确且唯一", len(concurrent) == 2000
          and len(set(concurrent)) == 2000)
    check("并发与串行产出同一 ID 集合（同起点同轨迹）",
          sorted(concurrent) == sorted(serial))


def test_cli_contract():
    print("\n== 用例6: CLI 契约（脚手架时钟样例 + 输出结构）==")
    tmpdir = tempfile.mkdtemp(prefix="idgen-cli-")
    reqp = os.path.join(tmpdir, "req.json")
    outp = os.path.join(tmpdir, "out.json")
    statep = os.path.join(tmpdir, "state.json")
    req = {"clock": [0, 1, 2, 1.5, 1.4, 50, 49, 100], "batch": 2,
           "state": statep, "max_backward_ms": 500}
    json.dump(req, open(reqp, "w"))
    proc = subprocess.run([sys.executable, os.path.join(WORK, "idgen.py"),
                           reqp, outp], capture_output=True, text=True)
    check("CLI 退出码 0", proc.returncode == 0, proc.stderr)
    res = json.load(open(outp, encoding="utf-8"))
    check("输出包含 ids/meta/alerts/stats",
          set(res.keys()) >= {"ids", "meta", "alerts", "stats"})
    check("8 观测点 x batch2 = 16 个 ID", len(res["ids"]) == 16)
    check("ids 与 meta 一一对应", len(res["meta"]) == len(res["ids"]))
    rows = [IdResult(res["ids"][i], res["meta"][i]["ts"],
                     res["meta"][i]["seq"], res["meta"][i]["src"],
                     res["meta"][i]["drift"], res["meta"][i]["phys"])
            for i in range(len(res["ids"]))]
    assert_monotonic_unique(rows, "用例6")
    check("回拨观测点产生 wait 来源",
          any(m["src"] == "wait" for m in res["meta"]))


if __name__ == "__main__":
    print("Python", sys.version.split()[0], "| cwd", WORK)
    t0 = time.time()
    test_rollback_and_jump()
    test_sequence_exhaustion()
    test_concurrency()
    test_concurrency_with_rollback_injection()
    test_restart_persistence()
    test_batch_and_concurrency_independence()
    test_cli_contract()
    print("\n================ 结果: %d 通过 / %d 失败，总耗时 %.1fs ================"
          % (PASS, FAIL, time.time() - t0))
    sys.exit(1 if FAIL else 0)
