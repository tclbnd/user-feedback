"""验收测试（标准库 unittest，运行：python3 test_acceptance.py -v）。

覆盖：
1. 8 次回溯修订 × 12 个切点，增量引擎与独立全量重建逐事实比对维表版本；
2. 系统时间相邻区间边界点 + 业务时间半开边界点；
3. 不同批次/乱序回放、重复修订幂等、同 version 载荷冲突整批拒绝；
4. 实际重关联键集合与手工影响范围核对；
5. 拆分/回收/撤销、历史报表可复现、双时间查询与零长区间不变量。
"""
from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

from bitemporal import Engine, ConflictError, ValidationError
from reference_rebuild import rebuild_report
import scenario


def row_index(report: dict) -> dict:
    return {r["id"]: r for r in report["rows"]}


def build_engine_per_revision():
    """逐修订应用，返回 (engine, 每次 apply 的返回值)。"""
    eng = Engine()
    receipts = [eng.apply_batch(scenario.INITIAL_BATCH)]
    for rev in scenario.REVISIONS:
        receipts.append(eng.apply_batch(copy.deepcopy(rev["events"])))
    return eng, receipts


def structural_fingerprint(eng: Engine) -> str:
    """双时间状态指纹：维表单元格 + 事实版本 + 关联链。

    原始事件分块顺序与变更日志的合并粒度随批次不同而不同，
    但这三类双时间结构必须与到达顺序、批次切分完全无关。
    """
    data = eng.to_dict()
    return json.dumps(
        {k: data[k] for k in ("dims", "facts", "links", "known_versions")},
        sort_keys=True, ensure_ascii=False)


class AcceptanceTests(unittest.TestCase):

    # ------------------------------------------------ 验收 1：12 切点全量比对
    def test_01_twelve_cuts_against_full_rebuild_and_oracle(self):
        eng, _ = build_engine_per_revision()
        self.assertEqual(len(scenario.CUTS), 12)
        for cut in scenario.CUTS:
            inc = row_index(eng.report(cut))
            full = row_index(rebuild_report(eng.events, cut))
            oracle = scenario.expected_state(cut)
            # 增量引擎 vs 独立全量重建：键集合与每行字段完全一致
            self.assertEqual(set(inc), set(full), "cut=%s 事实集合不一致" % cut)
            self.assertEqual(set(inc), set(oracle), "cut=%s 与预言机集合不一致" % cut)
            for fid, row in inc.items():
                self.assertEqual(row, full[fid], "cut=%s id=%s 增量与重建不一致" % (cut, fid))
                dv, reason = oracle[fid]
                self.assertEqual(row["reason"], reason,
                                 "cut=%s id=%s reason 错误" % (cut, fid))
                self.assertEqual(row["dimension_version"], dv,
                                 "cut=%s id=%s 维表版本错误" % (cut, fid))

    # ------------------------------------------------ 验收 1：系统时间边界点
    def test_02_adjacent_system_boundary_points(self):
        eng, _ = build_engine_per_revision()
        for left, right in scenario.ADJACENT_SYSTEM_POINTS:
            before = row_index(eng.report(left))
            after = row_index(eng.report(right))
            # 恰好在修订刻度到达时可见（半开系统区间，asof 含该时刻事件）
            self.assertNotEqual(before, after, "边界 %s/%s 两侧状态应不同" % (left, right))
            # 同一刻度在重建侧也必须完全一致
            self.assertEqual(
                row_index(rebuild_report(eng.events, right)), after)

    # ------------------------------------------------ 验收 1：业务时间半开边界
    def test_03_valid_time_half_open_boundaries(self):
        eng, _ = build_engine_per_revision()
        asof = 100
        for left, right, lv, rv in scenario.VALID_BOUNDARIES:
            hl = eng.resolve("R", left, asof)
            hr = eng.resolve("R", right, asof)
            self.assertEqual(hl["dimension_version"], lv,
                             "业务边界左点 %s 版本错误" % left)
            self.assertEqual(hr["dimension_version"], rv,
                             "业务边界右点 %s 版本错误" % right)
            # 维表双时间单元格查询同样满足半开语义
            cells_l = {c["version"] for c in eng.dim_cells("R", at=left, asof=asof)}
            cells_r = {c["version"] for c in eng.dim_cells("R", at=right, asof=asof)}
            self.assertIn(lv, cells_l)
            if rv is not None:
                self.assertIn(rv, cells_r)

    # ------------------------------------------------ 验收 2：手工影响范围
    def test_04_affected_keys_match_hand_calc(self):
        _, receipts = build_engine_per_revision()
        self.assertEqual(len(receipts) - 1, 8)
        for rev, receipt in zip(scenario.REVISIONS, receipts[1:]):
            self.assertEqual(
                receipt["affected"], rev["expected_affected"],
                "%s 的实际重关联键集合与手工计算不符" % rev["name"])

    # ------------------------------------------------ 验收 2：不同批次回放
    def test_05_replay_with_different_batching_and_shuffle(self):
        ordered = scenario.INITIAL_BATCH + scenario.ALL_REVISION_EVENTS
        reference = Engine()
        reference.apply_batch(copy.deepcopy(ordered))
        ref_fp = structural_fingerprint(reference)

        for size in scenario.REPLAY_BATCH_SIZES:
            eng = Engine()
            stream = copy.deepcopy(ordered)
            for i in range(0, len(stream), size):
                eng.apply_batch(stream[i:i + size])
            self.assertEqual(structural_fingerprint(eng), ref_fp,
                             "批次大小 %s 回放结构不一致" % size)
            for cut in scenario.CUTS:
                self.assertEqual(
                    row_index(eng.report(cut)),
                    row_index(reference.report(cut)),
                    "批次大小 %s 在 cut=%s 报表不一致" % (size, cut))

        # 乱序到达：单批内随机洗牌，稳定顺序仍保证结果一致
        rng = random.Random(20261113)
        shuffled = copy.deepcopy(ordered)
        rng.shuffle(shuffled)
        eng2 = Engine()
        eng2.apply_batch(shuffled)
        self.assertEqual(structural_fingerprint(eng2), ref_fp)

    # ------------------------------------------------ 验收 2：重复一条修订
    def test_06_duplicate_revision_is_idempotent(self):
        eng, _ = build_engine_per_revision()
        before = structural_fingerprint(eng)
        rv1 = copy.deepcopy(scenario.REVISIONS[0]["events"])
        receipt = eng.apply_batch(rv1)  # 完整重复 Rv1
        self.assertEqual(receipt["accepted"], 0)
        self.assertEqual(receipt["affected"], [])
        self.assertEqual(structural_fingerprint(eng), before)
        for cut in scenario.CUTS:
            self.assertEqual(
                row_index(eng.report(cut)),
                row_index(rebuild_report(eng.events, cut)))

    def test_07_duplicate_version_conflicting_payload_rejected(self):
        eng, _ = build_engine_per_revision()
        bad = [{
            "type": "dimension", "entity": "R", "from": 10, "to": 30,
            "system": 10, "version": "d-r1", "op": "upsert",
            "label": "TAMPERED",  # 同 version 不同载荷
        }]
        with self.assertRaises(ConflictError):
            eng.apply_batch(bad)
        # 整批拒绝：任何状态都不得改变
        good, _ = build_engine_per_revision()
        self.assertEqual(eng.to_dict(), good.to_dict())

    # ------------------------------------------------ 同系统时间冲突规则
    def test_08_same_system_time_lexicographic_winner(self):
        eng, _ = build_engine_per_revision()
        hit = eng.resolve("R", 20, 30)
        self.assertEqual(hit["dimension_version"], "d-r3-b")
        self.assertEqual(hit["label"], "Y")
        visible = {c["version"] for c in eng.dim_cells("R", at=20, asof=30)}
        self.assertIn("d-r3-b", visible)
        self.assertNotIn("d-r3-a", visible)  # 败者被同刻度替换，不留零长区间
        # 更晚 system 下败者仍不可复活
        self.assertNotIn("d-r3-a",
                         {c["version"] for c in eng.dim_cells("R", at=20, asof=100)})

    # ------------------------------------------------ 拆分/回收/撤销语义
    def test_09_split_remove_revoke_lifecycle(self):
        eng, _ = build_engine_per_revision()
        hist = eng.fact_history("F6")
        reasons = [(l["reason"], l["dimension_version"]) for l in hist["links"]]
        # 初始 matched -> Rv2 removed -> Rv6 撤销回收后 matched 到新版本
        self.assertEqual(
            reasons,
            [("matched", "d-r3-0"), ("removed", None), ("matched", "d-r6")],
        )
        # 补录事实 F9：补录前报表不存在，补录时落入 removed，撤销后 matched
        self.assertNotIn("F9", row_index(eng.report(39)))
        f9_40 = row_index(eng.report(40))["F9"]
        self.assertEqual((f9_40["reason"], f9_40["dimension_version"]),
                         ("removed", None))
        f9_100 = row_index(eng.report(100))["F9"]
        self.assertEqual((f9_100["reason"], f9_100["dimension_version"]),
                         ("matched", "d-r6"))

    def test_10_historical_reports_remain_explainable(self):
        eng, _ = build_engine_per_revision()
        # F1 在 system=70 被修订业务时刻；旧报表仍引用旧事实版本和旧维表版本
        old = row_index(eng.report(69))["F1"]
        new = row_index(eng.report(70))["F1"]
        self.assertEqual(old["fact_version"], "f1")
        self.assertEqual(new["fact_version"], "f1-fix")
        self.assertEqual(old["at"], 5)
        self.assertEqual(new["at"], 6)
        # F2 在 system=50 被撤销：当前报表消失，但历史报表与关联链都保留
        self.assertNotIn("F2", row_index(eng.report(100)))
        f2_old = row_index(eng.report(49))["F2"]
        self.assertEqual(f2_old["fact_version"], "f2")
        f2_hist = eng.fact_history("F2")
        self.assertTrue(any(v["deleted"] for v in f2_hist["versions"]))
        self.assertTrue(any(l["dimension_version"] == "d-r3-b"
                            for l in f2_hist["links"]))

    # ------------------------------------------------ 不变量与辅助接口
    def test_11_no_zero_length_intervals_and_invariants(self):
        eng, _ = build_engine_per_revision()
        for rows in eng.dims.values():
            for c in rows:
                self.assertLess(c.vfrom, c.vto if c.vto is not None else 10 ** 9)
                self.assertLess(c.sfrom, c.sto if c.sto is not None else 10 ** 9)
        for rows in eng.links.values():
            for l in rows:
                self.assertLess(l.sfrom, l.sto if l.sto is not None else 10 ** 9)
        # 变更明细：每条记录都带新旧版本来源
        for ch in eng.change_log():
            self.assertIn("system", ch)
            self.assertIn("old", ch)
            self.assertIn("new", ch)
            self.assertIsInstance(ch["changed"], bool)

    def test_12_two_time_dimension_queries_and_filters(self):
        eng, _ = build_engine_per_revision()
        # 系统时间切片 + 业务时间切片的维表查询
        cells = eng.dim_cells("R", at=20, asof=100)
        self.assertTrue(all(c["vfrom"] <= 20 < (c["vto"] or 10 ** 9) for c in cells))
        # 报表按实体与业务时间范围过滤（业务时间半开 [valid_from, valid_to)）
        rep = eng.report(100, entity="R", valid_from=10, valid_to=30)
        ats = {r["at"] for r in rep["rows"]}
        self.assertTrue(all(10 <= a < 30 for a in ats))
        self.assertIn(10, ats)
        self.assertNotIn(30, ats)

    def test_13_validation_errors(self):
        eng = Engine()
        with self.assertRaises(ValidationError):
            eng.apply_batch([{"type": "fact", "id": "x", "entity": "R",
                              "at": 1, "system": -1, "version": "v"}])
        with self.assertRaises(ValidationError):
            eng.apply_batch([{"type": "dimension", "entity": "R", "from": 30,
                              "to": 10, "system": 1, "version": "v",
                              "op": "upsert", "label": "A"}])
        with self.assertRaises(ValidationError):
            eng.apply_batch([{"type": "dimension", "entity": "R", "from": 0,
                              "to": 10, "system": 1, "version": "",
                              "op": "upsert", "label": "A"}])

    def test_14_cli_smoke_and_rebuild_command(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state.db")
            demo = os.path.join(os.path.dirname(__file__), "demo.json")
            r = subprocess.run([sys.executable, "bitemporal.py", "apply", state, demo],
                               capture_output=True, text=True, cwd=os.path.dirname(__file__))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["accepted"], 3)
            r = subprocess.run([sys.executable, "bitemporal.py", "query", state,
                                "--asof", "9"], capture_output=True, text=True,
                               cwd=os.path.dirname(__file__))
            self.assertEqual(r.returncode, 0, r.stderr)
            row = row_index(json.loads(r.stdout))["a"]
            self.assertEqual(row["dimension_version"], "changed")
            # 独立重建子命令结果一致
            r2 = subprocess.run([sys.executable, "bitemporal.py", "rebuild", state,
                                 "--asof", "9"], capture_output=True, text=True,
                                cwd=os.path.dirname(__file__))
            self.assertEqual(json.loads(r2.stdout)["rows"], json.loads(r.stdout)["rows"])

    def test_15_cli_conflict_exit_code_and_untouched_state(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state.db")
            good = os.path.join(d, "good.json")
            bad = os.path.join(d, "bad.json")
            with open(good, "w") as fh:
                json.dump({"events": scenario.INITIAL_BATCH}, fh)
            with open(bad, "w") as fh:
                json.dump({"events": [{
                    "type": "dimension", "entity": "R", "from": 0, "to": 20,
                    "system": 0, "version": "d-r1-0", "op": "upsert",
                    "label": "HACK"}]}, fh)
            subprocess.run([sys.executable, "bitemporal.py", "apply", state, good],
                           check=True, cwd=os.path.dirname(__file__),
                           capture_output=True, text=True)
            with open(state, "r", encoding="utf-8") as fh:
                before = fh.read()
            r = subprocess.run([sys.executable, "bitemporal.py", "apply", state, bad],
                               cwd=os.path.dirname(__file__),
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            with open(state, "r", encoding="utf-8") as fh:
                after = fh.read()
            self.assertEqual(after, before)  # 原子发布：失败不留半成品


class ExtraSemanticsTests(unittest.TestCase):

    def _dim(self, entity, from_, to, system, version, op="upsert", label=None):
        return {"type": "dimension", "entity": entity, "from": from_, "to": to,
                "system": system, "version": version, "op": op,
                "label": label if label is not None else version}

    def _fact(self, fid, entity, at, system, version, op="upsert"):
        return {"type": "fact", "id": fid, "entity": entity, "at": at,
                "system": system, "version": version, "op": op}

    def test_open_ended_upsert_then_partial_revision(self):
        eng = Engine()
        eng.apply_batch([self._dim("E", 0, None, 0, "d0")])
        eng.apply_batch([self._dim("E", 10, 20, 5, "d1")])
        # 左残余保留 d0、中段 d1、右侧开放残余仍为 d0
        self.assertEqual(eng.resolve("E", 5, 10)["dimension_version"], "d0")
        self.assertEqual(eng.resolve("E", 15, 10)["dimension_version"], "d1")
        self.assertEqual(eng.resolve("E", 50, 10)["dimension_version"], "d0")
        # 历史系统时间点看不到未来修订
        self.assertEqual(eng.resolve("E", 15, 4)["dimension_version"], "d0")
        # 全量重建在每个切点都一致
        from reference_rebuild import rebuild_report
        for s in range(0, 12):
            self.assertEqual(
                {r["id"]: r for r in rebuild_report(eng.events, s)["rows"]},
                {r["id"]: r for r in eng.report(s)["rows"]})

    def test_fully_contained_replacement_keeps_both_remnants(self):
        eng = Engine()
        eng.apply_batch([self._dim("E", 0, 100, 0, "base")])
        eng.apply_batch([self._dim("E", 40, 60, 5, "mid")])
        for at, ver in ((10, "base"), (40, "mid"), (59, "mid"), (60, "base"), (99, "base")):
            self.assertEqual(eng.resolve("E", at, 6)["dimension_version"], ver)

    def test_remove_then_revoke_reintroduces_label(self):
        eng = Engine()
        eng.apply_batch([self._dim("E", 0, 10, 0, "base"),
                         self._fact("f", "E", 5, 1, "v1")])
        self.assertEqual(eng.report(2)["rows"][0]["reason"], "matched")
        eng.apply_batch([self._dim("E", 0, 10, 3, "rm", op="remove", label="x")])
        self.assertEqual(eng.report(4)["rows"][0]["reason"], "removed")
        # 更晚 system 的 upsert 撤销遮蔽
        eng.apply_batch([self._dim("E", 0, 10, 6, "back")])
        row = eng.report(7)["rows"][0]
        self.assertEqual((row["reason"], row["dimension_version"]), ("matched", "back"))
        # 但 asof=4 的历史报表仍解释为 removed
        self.assertEqual(eng.report(4)["rows"][0]["reason"], "removed")

    def test_out_of_order_arrival_matches_ordered(self):
        events = [
            self._dim("E", 0, 10, 0, "d0"),
            self._dim("E", 5, 15, 5, "d1"),
            self._fact("f", "E", 8, 2, "v1"),
        ]
        a = Engine()
        a.apply_batch(copy.deepcopy(events))
        b = Engine()
        b.apply_batch(list(reversed(copy.deepcopy(events))))
        for cut in range(0, 8):
            self.assertEqual(a.report(cut), b.report(cut))

    def test_duplicate_events_inside_one_batch(self):
        eng = Engine()
        ev = self._dim("E", 0, 10, 0, "d0")
        receipt = eng.apply_batch([copy.deepcopy(ev), copy.deepcopy(ev)])
        self.assertEqual(receipt["accepted"], 1)
        self.assertEqual(len(eng.dims["E"]), 1)

    def test_conflicting_versions_inside_one_batch_rejected(self):
        eng = Engine()
        with self.assertRaises(ConflictError):
            eng.apply_batch([
                self._dim("E", 0, 10, 0, "same", label="A"),
                self._dim("E", 0, 10, 0, "same", label="B"),
            ])
        self.assertEqual(eng.dims, {})

    def test_half_open_valid_interval_excludes_upper_endpoint(self):
        eng = Engine()
        eng.apply_batch([self._dim("E", 0, 10, 0, "d0"),
                         self._dim("E", 10, 20, 0, "d1")])
        self.assertEqual(eng.resolve("E", 9, 1)["dimension_version"], "d0")
        self.assertEqual(eng.resolve("E", 10, 1)["dimension_version"], "d1")
        self.assertEqual(eng.resolve("E", 20, 1)["reason"], "no_dimension")

    def test_change_log_only_marks_real_view_changes(self):
        eng = Engine()
        eng.apply_batch([self._dim("E", 0, 100, 0, "d0"),
                         self._fact("f", "E", 5, 1, "v1")])
        # 一次不触及该事实业务点的修订，不应让它进入 affected
        receipt = eng.apply_batch([self._dim("E", 80, 90, 5, "far")])
        self.assertNotIn("f", receipt["affected"])
        # 一次触及但结果视图不变（同标签新版本覆盖同一点）也要重算并标记 coalesced
        receipt2 = eng.apply_batch([self._dim("E", 0, 10, 9, "d0b")])
        self.assertIn("f", receipt2["affected"])
        last = [c for c in eng.change_log() if c["id"] == "f"][-1]
        self.assertTrue(last["coalesced"] or last["changed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

