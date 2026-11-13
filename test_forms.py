"""forms.py 验收测试: 24 夹具 + 崩溃恢复 + 重试回放 + 单条重采.

运行: python3 test_forms.py   (或 python3 -m unittest test_forms -v)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import generate
import forms

WORK = Path(__file__).resolve().parent


def run_cli(*args, env=None, check=True):
    p = subprocess.run(
        [sys.executable, str(WORK / "forms.py"), *map(str, args)],
        capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and p.returncode != 0:
        raise AssertionError(f"CLI failed rc={p.returncode}\n{p.stderr}\n{p.stdout}")
    return p


class FixtureTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="d18-"))
        self.root = self.tmp / "out"
        self.expected = generate.make(self.tmp)
        self.jobs = self.tmp / "jobs.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def collect(self, *extra, env=None):
        p = run_cli(self.root, "collect", self.jobs, *extra, env=env)
        return json.loads(p.stdout)


class FullCollectTests(FixtureTestBase):
    def test_24_fixtures_values_and_dom_sources(self):
        view = self.collect()
        rows = {r["id"]: r for r in view["rows"]}
        self.assertEqual(len(rows), 20)
        self.assertEqual({r["id"] for r in view["review"]},
                         {"task-02", "task-09", "task-14", "task-17"})
        # 逐字段值 + DOM 来源与 generate.py 的真值比对
        for truth in self.expected["rows"]:
            row = rows[truth["id"]]
            self.assertEqual(row["fields"], truth["fields"], truth["id"])
            self.assertEqual(row["sources"], truth["sources"], truth["id"])
            self.assertEqual(row["template"], truth["template"])
            # 每个取值都带可查的 DOM 来源, 条件未启用字段为 null
            for key, src in row["field_sources"].items():
                if row["fields"][key] is None:
                    self.assertIsNone(src)
                else:
                    self.assertIsNotNone(src["dom_id"])
                    self.assertIn("dom_path", src)
                    self.assertIn(src["label_kind"],
                                  ("aria-label", "label-for", "label-wrap"))
                    self.assertEqual(src["snapshot"], row["snapshot"])

    def test_same_name_fields_resolved_by_group_not_position(self):
        # task-01: Contact 组在 Buyer 之前, 同名 Full name 不得串行
        view = self.collect()
        row = next(r for r in view["rows"] if r["id"] == "task-01")
        self.assertEqual(row["fields"]["buyer"], "Buyer 1")
        self.assertEqual(row["fields"]["contact"], "Contact 1")
        self.assertEqual(row["sources"]["buyer"], "task-01-buyer")
        self.assertEqual(row["sources"]["contact"], "task-01-contact")
        # task-03 同理(v3, label-for 关联, 字段顺序反转)
        row3 = next(r for r in view["rows"] if r["id"] == "task-03")
        self.assertEqual(row3["fields"]["buyer"], "Buyer 3")
        self.assertEqual(row3["sources"]["buyer"], "task-03-buyer")

    def test_conditional_route_field(self):
        view = self.collect()
        for row in view["rows"]:
            if row["id"][-2:] in {"01", "03", "05", "07", "09", "11",
                                  "13", "15", "17", "19", "21", "23"}:
                self.assertIs(row["routing_on"], False)
                self.assertIsNone(row["fields"]["route"])
                self.assertIsNone(row["sources"]["route"])
            else:
                self.assertIsNotNone(row["fields"]["route"])
                self.assertEqual(row["sources"]["route"],
                                 row["id"] + "-route")

    def test_hidden_and_disabled_controls_ignored(self):
        # 所有夹具都带 hidden ghost 输入, 任何结果都不得取到 Ghost
        view = self.collect()
        for row in view["rows"]:
            self.assertNotIn("Ghost", row["fields"].values())
            self.assertNotIn("ghost", row["sources"].values())
        # 额外夹具: disabled 控件同样忽略
        d = self.tmp / "d"
        d.mkdir()
        (d / "a.html").write_text(
            '<form data-template="v1" data-ready="true" data-routing="off">'
            '<fieldset><legend>Buyer</legend>'
            '<input disabled id="skip" aria-label="Name" value="X">'
            '<input id="keep" aria-label="Name" value="Y"></fieldset>'
            '<fieldset><legend>Contact</legend>'
            '<input id="c" aria-label="Full name" value="Z"></fieldset></form>')
        (d / "jobs.json").write_text(json.dumps(
            {"jobs": [{"id": "t-dis", "snapshots": ["a.html"]}]}))
        p = run_cli(d / "out", "collect", d / "jobs.json")
        r = json.loads(p.stdout)["rows"][0]
        self.assertEqual(r["fields"]["buyer"], "Y")
        self.assertEqual(r["sources"]["buyer"], "keep")

    def test_unknown_template_full_diagnostics_and_zero_extraction(self):
        view = self.collect()
        rev = next(r for r in view["review"] if r["id"] == "task-02")
        self.assertEqual(rev["reason"], "UNKNOWN_TEMPLATE")
        self.assertEqual(rev["template"], "v9")
        self.assertEqual(rev["known_templates"], ["v1", "v2", "v3"])
        self.assertTrue(rev["needs_manual"])
        self.assertTrue(rev["dom_locations"])  # 完整 DOM 位置留档
        ids_in_rows = {r["id"] for r in view["rows"]}
        self.assertNotIn("task-02", ids_in_rows)  # 误提取为零

    def test_ambiguous_and_missing_classification(self):
        view = self.collect()
        by_id = {r["id"]: r for r in view["review"]}
        amb = by_id["task-09"]
        self.assertEqual(amb["reason"], "AMBIGUOUS_FIELD")
        cands = amb["diagnostics"][0]["candidates"]
        self.assertEqual({c["dom_id"] for c in cands},
                         {"task-09-buyer", "task-09-conflict"})
        self.assertTrue(all("dom_path" in c for c in cands))
        miss = by_id["task-17"]
        self.assertEqual(miss["reason"], "MISSING_FIELD")
        self.assertEqual(miss["diagnostics"][0]["field"], "buyer")
        self.assertNotIn("task-09", {r["id"] for r in view["rows"]})
        self.assertNotIn("task-17", {r["id"] for r in view["rows"]})

    def test_timeout_waits_then_reviews(self):
        # 两次重试等待后进入 review, attempts 记录完整
        view = self.collect("--max-attempts", "3", "--backoff", "0")
        rev = next(r for r in view["review"] if r["id"] == "task-14")
        self.assertEqual(rev["reason"], "TIMEOUT")
        self.assertEqual(rev["attempts"], 3)
        state = json.loads((self.root / ".collect-state.json").read_text())
        outcomes = [a["outcome"] for a in state["tasks"]["task-14"]["attempts"]]
        self.assertEqual(outcomes, ["TIMEOUT", "TIMEOUT", "TIMEOUT"])
        waits = [e for e in state["events"]
                 if e["id"] == "task-14" and e["event"] == "waiting_retry"]
        self.assertEqual(len(waits), 2)


class IdempotencyTests(FixtureTestBase):
    def test_repeat_runs_keep_one_record_per_task_key(self):
        first = self.collect()
        state1 = json.loads(
            (self.root / ".collect-state.json").read_text())
        attempts1 = {tid: len(t["attempts"])
                     for tid, t in state1["tasks"].items()}
        third = self.collect()
        self.assertEqual(third, first)
        state = json.loads((self.root / ".collect-state.json").read_text())
        # 重复执行不再产生新尝试, 每个任务键仍只有一份结论
        self.assertEqual(
            {tid: len(t["attempts"]) for tid, t in state["tasks"].items()},
            attempts1)
        for tid, task in state["tasks"].items():
            self.assertTrue(task["task_key"])
        keys = [t["task_key"] for t in state["tasks"].values()]
        self.assertEqual(len(keys), len(set(keys)))  # 每任务键一份
        self.assertEqual(len(third["rows"]), 20)

    def test_classification_stable_across_runs(self):
        a = {(r["id"], r["reason"]) for r in self.collect()["review"]}
        b = {(r["id"], r["reason"]) for r in self.collect()["review"]}
        self.assertEqual(a, b)


class CrashRecoveryTests(FixtureTestBase):
    def test_crash_during_extract_resumes_without_half_records(self):
        p = run_cli(self.root, "collect", self.jobs,
                    "--crash", "during-extract", check=False)
        self.assertEqual(p.returncode, 97)
        # 崩溃后公开视图要么不存在, 要么不含任何中间态
        view_path = self.root / "view.json"
        if view_path.exists():
            partial = json.loads(view_path.read_text())
            self.assertEqual(partial["rows"], [])
        state = json.loads((self.root / ".collect-state.json").read_text())
        self.assertEqual(state["tasks"]["task-00"]["state"], "EXTRACTING")

        p2 = run_cli(self.root, "collect", self.jobs)
        view = json.loads(p2.stdout)
        self.assertEqual(len(view["rows"]), 20)
        self.assertEqual(len(view["review"]), 4)
        state2 = json.loads((self.root / ".collect-state.json").read_text())
        t0 = state2["tasks"]["task-00"]
        self.assertEqual(t0["state"], "DONE")
        self.assertIn("interrupted", [a["outcome"] for a in t0["attempts"]])
        # 每个任务键仍然只有一份公开结果
        self.assertEqual(len({r["id"] for r in view["rows"]}), len(view["rows"]))

    def test_crash_after_save_does_not_duplicate(self):
        p = run_cli(self.root, "collect", self.jobs,
                    "--crash", "after-save", check=False)
        self.assertEqual(p.returncode, 97)
        saved = json.loads((self.root / "view.json").read_text())
        self.assertEqual(len(saved["rows"]), 1)  # 首任务完整持久化
        p2 = run_cli(self.root, "collect", self.jobs)
        view = json.loads(p2.stdout)
        self.assertEqual(len(view["rows"]), 20)
        self.assertEqual(len([r for r in view["rows"]
                              if r["id"] == "task-00"]), 1)
        state = json.loads((self.root / ".collect-state.json").read_text())
        self.assertEqual(state["tasks"]["task-00"]["state"], "DONE")
        # 重跑被跳过, 没有产生第二次 ok 尝试
        ok_attempts = [a for a in state["tasks"]["task-00"]["attempts"]
                       if a["outcome"] == "ok"]
        self.assertEqual(len(ok_attempts), 1)


class RetryReplayTests(FixtureTestBase):
    def test_transient_failure_retried_then_succeeds(self):
        view = self.collect("--backoff", "0",
                            env={"ZQ_RETRY_FAULT": "task-05:2"})
        row = next(r for r in view["rows"] if r["id"] == "task-05")
        self.assertEqual(row["fields"]["buyer"], "Buyer 5")
        state = json.loads((self.root / ".collect-state.json").read_text())
        t = state["tasks"]["task-05"]
        outcomes = [a["outcome"] for a in t["attempts"]]
        self.assertEqual(outcomes,
                         ["transient_error", "transient_error", "ok"])
        retries = [e for e in state["events"]
                   if e["id"] == "task-05" and e["event"] == "retry_scheduled"]
        self.assertEqual(len(retries), 2)
        self.assertEqual(t["state"], "DONE")  # 没有半成品

    def test_exhausted_retries_go_to_review_with_classification(self):
        view = self.collect("--max-attempts", "2", "--backoff", "0",
                            env={"ZQ_RETRY_FAULT": "task-05:9"})
        rev = next(r for r in view["review"] if r["id"] == "task-05")
        self.assertEqual(rev["reason"], "EXTRACT_ERROR")
        self.assertTrue(rev["needs_manual"])
        self.assertNotIn("task-05", {r["id"] for r in view["rows"]})


class SingleRecollectTests(FixtureTestBase):
    def test_only_recollects_one_task_and_preserves_rest(self):
        self.collect()
        before = json.loads((self.root / "view.json").read_text())
        view = self.collect("--only", "task-05",
                            env={"ZQ_RETRY_FAULT": "task-05:1"})
        # 单条重采输出只含该任务的最新结果
        self.assertEqual([r["id"] for r in view["rows"]], ["task-05"])
        full = self.collect()  # 其余任务保持原分类, 无重复
        self.assertEqual(
            sorted(r["id"] for r in full["rows"]),
            sorted(r["id"] for r in before["rows"]))
        self.assertEqual(len(full["rows"]), 20)
        state = json.loads((self.root / ".collect-state.json").read_text())
        t5 = state["tasks"]["task-05"]
        self.assertTrue(any(e["event"] == "manual_recollect"
                            for e in state["events"]))
        self.assertEqual(t5["state"], "DONE")
        self.assertEqual(
            [a["outcome"] for a in t5["attempts"]][:2],
            ["ok", "transient_error"])


class DynamicAndAliasTests(unittest.TestCase):
    """延迟渲染(部分快照)与显式允许的标签别名."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="d18-dyn-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_late_rendered_field_group_resolves_on_ready_snapshot(self):
        # 快照 0: 表单未就绪且 Routing 组尚未展开; 快照 1: 就绪并展开
        (self.tmp / "s0.html").write_text(
            '<form data-template="v2" data-ready="false" data-routing="on">'
            '<fieldset><legend>Buyer</legend>'
            '<input id="b" aria-label="Name" value="B"></fieldset>'
            '<fieldset><legend>Contact</legend>'
            '<input id="c" aria-label="Full name" value="C"></fieldset></form>')
        (self.tmp / "s1.html").write_text(
            '<form data-template="v2" data-ready="true" data-routing="on">'
            '<fieldset><legend>Buyer</legend>'
            '<input id="b" aria-label="Name" value="B"></fieldset>'
            '<fieldset><legend>Contact</legend>'
            '<input id="c" aria-label="Full name" value="C"></fieldset>'
            '<fieldset><legend>Routing</legend>'
            '<input id="r" aria-label="Route" value="RT"></fieldset></form>')
        (self.tmp / "jobs.json").write_text(json.dumps(
            {"jobs": [{"id": "dyn", "snapshots": ["s0.html", "s1.html"]}]}))
        p = run_cli(self.tmp / "out", "collect", self.tmp / "jobs.json")
        row = json.loads(p.stdout)["rows"][0]
        self.assertEqual(row["snapshot"], "s1.html")
        self.assertEqual(row["fields"]["route"], "RT")
        self.assertEqual(row["sources"]["route"], "r")

    def test_alias_must_be_explicitly_registered(self):
        # 外部注册表只给 v7 登记 "采购方" 作为 Buyer.Name 别名
        (self.tmp / "a.html").write_text(
            '<form data-template="v7" data-ready="true" data-routing="off">'
            '<fieldset><legend>Buyer</legend>'
            '<input id="b1" aria-label="采购方" value="B"></fieldset>'
            '<fieldset><legend>Contact</legend>'
            '<input id="c1" aria-label="Full name" value="C"></fieldset></form>')
        (self.tmp / "jobs.json").write_text(json.dumps(
            {"jobs": [{"id": "alias", "snapshots": ["a.html"]}]}))
        reg = self.tmp / "reg.json"
        reg.write_text(json.dumps({"templates": {"v7": {"fields": {
            "buyer": {"group": "Buyer",
                      "labels": ["Name", "Full name", "采购方"],
                      "required": "always"},
            "contact": {"group": "Contact",
                        "labels": ["Name", "Full name"],
                        "required": "always"},
            "route": {"group": "Routing",
                      "labels": ["Route", "Route code"],
                      "required": "routing_on"},
        }}}}))
        p = run_cli(self.tmp / "out", "collect", self.tmp / "jobs.json",
                    "--templates", reg)
        row = json.loads(p.stdout)["rows"][0]
        self.assertEqual(row["fields"]["buyer"], "B")
        self.assertEqual(row["field_sources"]["buyer"]["label"], "采购方")
        # 未登记别名不得兜底: 去掉注册表后应隔离为 UNKNOWN_TEMPLATE
        p2 = run_cli(self.tmp / "out2", "collect", self.tmp / "jobs.json")
        view2 = json.loads(p2.stdout)
        self.assertEqual(view2["review"][0]["reason"], "UNKNOWN_TEMPLATE")
        self.assertEqual(view2["rows"], [])


class InspectAndStateTests(FixtureTestBase):
    def test_inspect_returns_completed_view(self):
        p = run_cli(self.root, "inspect")
        self.assertEqual(json.loads(p.stdout), {"rows": [], "review": []})
        self.collect()
        p = run_cli(self.root, "inspect", "--verbose")
        view = json.loads(p.stdout)
        self.assertEqual(len(view["rows"]), 20)
        self.assertEqual(set(view["_state_summary"].values()),
                         {"DONE", "REVIEW"})
        self.assertGreater(view["_events"], 0)

    def test_rows_carry_stable_key_version_and_fingerprint(self):
        view = self.collect()
        for row in view["rows"]:
            self.assertEqual(row["collector_version"], forms.COLLECTOR_VERSION)
            self.assertTrue(row["task_key"])
            self.assertTrue(row["registry_hash"])
            self.assertTrue(row["fingerprint"])
        state = json.loads((self.root / ".collect-state.json").read_text())
        for tid, task in state["tasks"].items():
            if task["state"] == "DONE":
                self.assertEqual(task["result"]["task_key"],
                                 task["task_key"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
