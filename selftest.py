#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""idgen 验收测试套件（纯标准库，python3 selftest.py）。

覆盖题面三条实现要求 + 两条验收：
  * 注入 100ms 回拨与 50ms 突跳, ID 严格单调且不重复, 回拨期内时间位 >= 回拨前
  * 16 线程并发取 10^6, 全局唯一且按生成序单调
  * 重启后首 ID 时间位 >= 关闭前最后值
  * 批大小无关性、跨进程状态、告警编码、损坏状态 fail-closed
"""

import json
import os
import random
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import idgen
from idgen import (ClockRollbackIdGenerator, ManualClock, StateError,
                   VirtualClock, compose_id, MAX_SEQ, SEQ_BITS)


def ts_of(ident):
    return ident >> SEQ_BITS


def seq_of(ident):
    return ident & MAX_SEQ


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idgen-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state_path(self, name="state.json"):
        return os.path.join(self.tmp, name)


class TestRollbackInjection(TmpDirCase):
    """验收 1: 100ms 回拨 + 50ms 突跳 -> 严格单调/唯一, 时间位不回退。"""

    def test_100ms_rollback_and_50ms_jump(self):
        base = 1_000_000
        clock = ManualClock(base)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                       max_rollback_ms=500)
        out = []

        # 正常推进 20ms
        for _ in range(20):
            out.append(gen.take_detailed(1)[0])
            clock.advance(1)
        before = out[-1]["ts"]

        # 注入 100ms 回拨（容忍区内 -> 冻结时间位、消耗序号）
        clock.set(base + 20 - 100)
        for _ in range(5):
            out.append(gen.take_detailed(1)[0])
            clock.advance(1)

        # 回拨期内新 ID 的时间位必须 >= 回拨前
        for item in out[20:]:
            self.assertGreaterEqual(item["ts"], before, "回拨期内时间位回退了")
            self.assertTrue(item["src"] in ("wait", "drift"), item["src"])

        # 注入 50ms 突跳（向前跳跃，正常路径）
        clock.set(clock.now_ms() + 50)
        for _ in range(5):
            out.append(gen.take_detailed(1)[0])
            clock.advance(1)

        ids = [item["id"] for item in out]
        self.assertTrue(all(b > a for a, b in zip(ids, ids[1:])), "ID 非严格单调")
        self.assertEqual(len(set(ids)), len(ids), "存在重复 ID")
        # 告警: 回拨被冻结策略接管
        codes = [a["code"] for a in gen.alerts]
        self.assertIn("clock_rollback_hold", codes)
        gen.close()

    def test_severe_rollback_engages_drift(self):
        base = 5_000
        clock = ManualClock(base)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                       max_rollback_ms=500)
        gen.take(1)
        clock.advance(2)
        last_ts = gen.take_detailed(1)[0]["ts"]

        # 回拨 900ms > 容忍区 500ms -> 漂移位接管
        clock.set(clock.now_ms() - 900)
        items = gen.take_detailed(5)
        self.assertTrue(all(i["src"] == "drift" for i in items), "未走漂移位")
        self.assertGreaterEqual(items[0]["ts"], last_ts, "漂移期间时间位回退")
        codes = [a["code"] for a in gen.alerts]
        self.assertIn("rollback_exceeded_drift_engaged", codes)

        # 物理时钟追平后漂移位退役
        clock.set(items[-1]["ts"] + 1)
        tail = gen.take_detailed(1)[0]
        self.assertEqual(tail["src"], "normal")
        self.assertIn("drift_retired", [a["code"] for a in gen.alerts])
        self.assertEqual(gen.snapshot()["drift"], 0)
        gen.close()

    def test_seq_exhaustion_waits_for_next_ms(self):
        clock = ManualClock(1_000)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                       max_wait_ms=50)
        # 同一毫秒正好 4096 个序号(seq 0..4095), 第 4097 个必须等待下一毫秒
        items = gen.take_detailed(MAX_SEQ + 2)
        self.assertEqual(items[MAX_SEQ]["seq"], MAX_SEQ)
        self.assertEqual(items[MAX_SEQ + 1]["seq"], 0)
        self.assertEqual(items[MAX_SEQ + 1]["ts"], 1_001)
        self.assertEqual(items[MAX_SEQ + 1]["src"], "wait")
        # ManualClock.sleep 会推进虚拟时间, 因此不需要强制推进
        self.assertEqual(gen.stats["forced_advance"], 0)
        self.assertIn("seq_exhausted_wait", [a["code"] for a in gen.alerts])
        gen.close()

    def test_seq_exhaustion_forced_advance_when_stalled(self):
        class FrozenClock(ManualClock):
            def sleep(self, ms):
                pass          # 睡不醒: 模拟时钟完全停滞

        clock = FrozenClock(7_000)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                       max_wait_ms=5)
        items = gen.take_detailed(MAX_SEQ + 2)
        tail = items[-1]
        self.assertEqual(tail["src"], "wait")
        self.assertEqual(tail["ts"], 7_001)   # 强制推进 1ms
        self.assertEqual(gen.stats["forced_advance"], 1)
        self.assertIn("wait_timeout_forced_advance",
                      [a["code"] for a in gen.alerts])
        ids = [i["id"] for i in items]
        self.assertTrue(all(b > a for a, b in zip(ids, ids[1:])))
        gen.close()

    def test_random_rollback_storm_is_monotonic(self):
        rng = random.Random(20261114)
        clock = ManualClock(0)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None)
        ids, meta = [], []
        for _ in range(4000):
            roll = rng.random()
            if roll < 0.20:
                clock.set(max(0, clock.now_ms() - rng.randint(1, 700)))
            elif roll < 0.35:
                clock.set(clock.now_ms() + rng.randint(1, 60))
            else:
                clock.advance(rng.randint(0, 2))
            item = gen.take_detailed(1)[0]
            ids.append(item["id"])
            meta.append(item)
        self.assertTrue(all(b > a for a, b in zip(ids, ids[1:])), "随机风暴下非单调")
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(b["ts"] >= a["ts"] for a, b in zip(meta, meta[1:])))
        gen.close()


class TestConcurrencyAndBatch(TmpDirCase):
    """验收 2 + 要求 3: 16 线程 10^6 唯一单调; 批大小无关。"""

    def test_16_threads_1e6_unique_monotonic_ordered(self):
        clock = VirtualClock(1_000_000, calls_per_ms=1)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                       max_wait_ms=2_000_000)
        threads, per_thread = 16, 1_000_000 // 16

        results = [None] * threads
        barrier = threading.Barrier(threads)

        def worker(idx):
            barrier.wait()
            local = []
            remaining = per_thread
            while remaining:
                batch = 64 if remaining >= 64 else remaining
                local.extend(gen.take_detailed(batch))
                remaining -= batch
            results[idx] = local

        workers = [threading.Thread(target=worker, args=(i,))
                   for i in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

        total = sum(len(r) for r in results)
        self.assertEqual(total, per_thread * threads)

        # 全局唯一
        flat = [item["id"] for r in results for item in r]
        self.assertEqual(len(set(flat)), total, "并发下出现重复 ID")

        # 按生成序单调: 以锁内递增的 order 还原本来的生成顺序
        ordered = sorted((item for r in results for item in r),
                         key=lambda item: item["order"])
        self.assertEqual([item["order"] for item in ordered],
                         list(range(total)))
        ids = [item["id"] for item in ordered]
        self.assertTrue(all(b > a for a, b in zip(ids, ids[1:])),
                        "按生成序 ID 非严格单调")
        tss = [item["ts"] for item in ordered]
        self.assertTrue(all(b >= a for a, b in zip(tss, tss[1:])),
                        "按生成序时间位回退")
        self.assertEqual(gen.stats["issued"], total)
        gen.close()

    def test_batch_size_invariance(self):
        def generate(batch):
            clock = VirtualClock(50_000, calls_per_ms=1)
            gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                           max_wait_ms=2_000_000)
            out = []
            remaining = 2000
            while remaining:
                n = min(batch, remaining)
                out.extend(gen.take(n))
                remaining -= n
            gen.close()
            return out

        reference = generate(1)
        for batch in (2, 3, 7, 64, 1000, 2000):
            self.assertEqual(generate(batch), reference,
                             "批大小 %d 改变了输出序列" % batch)

    def test_take_detailed_matches_take(self):
        def run(kind):
            clock = VirtualClock(10, calls_per_ms=1)
            gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                           max_wait_ms=2_000_000)
            if kind == "detailed":
                items = gen.take_detailed(500)
                gen.close()
                return [i["id"] for i in items], items
            ids = gen.take(500)
            gen.close()
            return ids, None

        ids_a, items = run("detailed")
        ids_b, _ = run("plain")
        self.assertEqual(ids_a, ids_b, "take_detailed 与 take 输出不一致")
        # 逐 ID 明细字段齐全
        for item in items:
            for key in ("order", "id", "ts", "seq", "src", "wall", "drift"):
                self.assertIn(key, item)
            self.assertIn(item["src"], ("normal", "wait", "drift"))


class TestPersistence(TmpDirCase):
    """验收 2 后半 + 要求 2: 重启后首 ID 时间位 >= 关闭前最后值。"""

    def test_restart_first_id_not_earlier(self):
        path = self.state_path()
        clock = ManualClock(100_000)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=path,
                                       clock_id="node-a")
        ids = gen.take(100)
        last_ts = ts_of(ids[-1])
        last_id = ids[-1]
        gen.close()

        # 重启时墙钟被 NTP 回拨 80ms: 首 ID 仍不得早于关闭前
        clock.set(clock.now_ms() - 80)
        gen2 = ClockRollbackIdGenerator(clock=clock, state_path=path,
                                        clock_id="node-a")
        first = gen2.take_detailed(1)[0]
        self.assertGreaterEqual(first["ts"], last_ts)
        self.assertGreater(first["id"], last_id)
        # 80ms 在容忍区内 -> 冻结等待
        self.assertEqual(first["src"], "wait")
        gen2.close()

    def test_restart_without_rollback_continues_strictly(self):
        path = self.state_path()
        c1 = ManualClock(200_000)
        g1 = ClockRollbackIdGenerator(clock=c1, state_path=path)
        ids1 = g1.take(10)
        g1.close()

        c2 = ManualClock(200_005)
        g2 = ClockRollbackIdGenerator(clock=c2, state_path=path)
        ids2 = g2.take(10)
        self.assertGreater(ids2[0], ids1[-1])
        self.assertTrue(all(b > a for a, b in zip(ids2, ids2[1:])))
        g2.close()

    def test_unclean_restart_skips_to_next_ms(self):
        # 手工写入 closed=false 状态(相当于崩溃留下的水位线), 验证下一毫秒续发
        path = self.state_path("crash.json")
        store = idgen.StateStore(path)
        store.acquire()
        store.save({"last_ts": 300_000, "seq": 100, "last_id": 0,
                    "drift": 0, "clock_id": "node-a", "closed": False})
        store.release()

        g = ClockRollbackIdGenerator(clock=ManualClock(300_000),
                                     state_path=path, clock_id="node-a")
        first = g.take_detailed(1)[0]
        self.assertEqual(first["ts"], 300_001)
        self.assertIn("unclean_shutdown_recovery",
                      [a["code"] for a in g.alerts])
        g.close()

    def test_crash_without_close_still_skips(self):
        """真实崩溃路径: 生成水位线后不 close(), 重启仍从下一毫秒续发。"""
        path = self.state_path()
        clock = ManualClock(400_000)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=path,
                                       clock_id="node-a")
        # 同一毫秒内取超过一个满序号, 必然进入新毫秒并落盘水位线
        gen.take(MAX_SEQ + 2)
        watermark = gen.snapshot()["last_ts"]
        gen._store.release()       # 模拟进程消失: 释放 flock, 不写 closed
        del gen

        g2 = ClockRollbackIdGenerator(clock=ManualClock(400_000),
                                      state_path=path, clock_id="node-a")
        first = g2.take_detailed(1)[0]
        self.assertGreaterEqual(first["ts"], watermark)
        self.assertIn("unclean_shutdown_recovery",
                      [a["code"] for a in g2.alerts])
        g2.close()

    def test_restart_then_crash_does_not_reissue(self):
        """重启后再次崩溃: 不得按 closed 的 seq 重放同一批 ID。"""
        path = self.state_path()
        c1 = ManualClock(500_000)
        g1 = ClockRollbackIdGenerator(clock=c1, state_path=path)
        first_run = g1.take(5)
        g1.close()

        # 重启: 若把 closed=true 的精确 seq 再当成可续发点, 崩溃后会重发
        c2 = ManualClock(500_000)
        g2 = ClockRollbackIdGenerator(clock=c2, state_path=path)
        g2._store.release()        # 模拟第二次崩溃(未 close)
        del g2

        c3 = ManualClock(500_000)
        g3 = ClockRollbackIdGenerator(clock=c3, state_path=path)
        after = g3.take(5)
        g3.close()
        self.assertFalse(set(first_run) & set(after), "重启后又重发了旧 ID")
        self.assertTrue(all(b > a for a, b in zip(after, after[1:])))
        self.assertGreater(after[0], first_run[-1])

    def test_rollback_during_seq_exhaustion_holds_not_rewinds(self):
        """回拨区间内序号耗尽: 冻结时间位等待, 绝不回退到更早时间位。"""
        class FrozenClock(ManualClock):
            def sleep(self, ms):
                pass

        base = 800_000
        clock = FrozenClock(base)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None,
                                       max_rollback_ms=500, max_wait_ms=4)
        gen.take(1)
        clock.set(base - 100)                     # 容忍区内 100ms 回拨
        items = gen.take_detailed(MAX_SEQ + 3)    # 回拨期内跑满序号
        tss = [i["ts"] for i in items]
        self.assertTrue(all(b >= a for a, b in zip(tss, tss[1:])), "时间位回退")
        self.assertGreaterEqual(min(tss), base)
        codes = [a["code"] for a in gen.alerts]
        self.assertIn("clock_rollback_hold", codes)
        self.assertTrue("seq_exhausted_wait" in codes
                        or "wait_timeout_forced_advance" in codes)
        ids = [i["id"] for i in items]
        self.assertTrue(all(b > a for a, b in zip(ids, ids[1:])))
        gen.close()

    def test_corrupt_state_fails_closed(self):
        path = self.state_path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(StateError):
            ClockRollbackIdGenerator(state_path=path)

    def test_tampered_checksum_fails_closed(self):
        path = self.state_path()
        g = ClockRollbackIdGenerator(clock=ManualClock(1), state_path=path)
        g.take(1)
        g.close()
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["state"]["last_ts"] -= 10_000       # 篡改
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        with self.assertRaises(StateError):
            ClockRollbackIdGenerator(state_path=path)

    def test_legacy_state_is_adopted_conservatively(self):
        path = self.state_path()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"last": 42}, handle)
        clock = ManualClock(42)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=path)
        first = gen.take_detailed(1)[0]
        self.assertGreaterEqual(first["ts"], 42)
        self.assertIn("legacy_state_adopted",
                      [a["code"] for a in gen.alerts])
        gen.close()

    def test_exclusive_lock_blocks_second_generator(self):
        path = self.state_path()
        g1 = ClockRollbackIdGenerator(clock=ManualClock(1), state_path=path)
        with self.assertRaises(StateError):
            ClockRollbackIdGenerator(clock=ManualClock(1), state_path=path)
        g1.close()
        # 关闭后可重开
        g2 = ClockRollbackIdGenerator(clock=ManualClock(1), state_path=path)
        g2.close()


class TestAlerts(unittest.TestCase):
    """告警不能随 ID 数量线性膨胀(否则日志/内存都被淹没)。"""

    def test_no_alert_storm_under_long_stall(self):
        class FrozenClock(ManualClock):
            def sleep(self, ms):
                pass

        gen = ClockRollbackIdGenerator(clock=FrozenClock(1_000),
                                       state_path=None, max_wait_ms=1)
        gen.take(MAX_SEQ * 3 + 10)      # 三次序号耗尽 + 多次强制推进
        alert_count = len(gen.alerts)
        self.assertLessEqual(alert_count, 5,
                             "告警风暴: %d 条" % alert_count)
        codes = [a["code"] for a in gen.alerts]
        self.assertIn("seq_exhausted_wait", codes)
        self.assertIn("wait_timeout_forced_advance", codes)
        # 同一 code 只出现一次
        self.assertEqual(len(codes), len(set(codes)), "同一告警重复堆积")
        gen.close()

    def test_rollback_hold_alert_once_per_incident(self):
        clock = ManualClock(10_000)
        gen = ClockRollbackIdGenerator(clock=clock, state_path=None)
        gen.take(1)
        clock.set(9_900)                     # 100ms 回拨
        gen.take(1000)                       # 区间内发放 1000 个
        holds = [a for a in gen.alerts if a["code"] == "clock_rollback_hold"]
        self.assertEqual(len(holds), 1, "同一回拨区间告警了 %d 次" % len(holds))
        gen.close()


class TestDeterministicReplay(unittest.TestCase):
    """要求 3: 输出逐 ID 可复现(时间位/序号/来源标记)。"""

    def test_virtual_clock_replay_is_deterministic(self):
        def run_once():
            gen = ClockRollbackIdGenerator(clock=VirtualClock(9_000, 1),
                                           state_path=None,
                                           max_wait_ms=100_000)
            items = gen.take_detailed(300)
            gen.close()
            return [(i["ts"], i["seq"], i["src"]) for i in items]

        self.assertEqual(run_once(), run_once())

    def test_compose_id_layout(self):
        self.assertEqual(compose_id(1, 0), 4096)
        self.assertEqual(compose_id(0, MAX_SEQ), MAX_SEQ)
        self.assertEqual(ts_of(compose_id(1234, 7)), 1234)
        self.assertEqual(seq_of(compose_id(1234, 7)), 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)