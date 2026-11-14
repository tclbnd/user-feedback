#!/usr/bin/env python3
"""wizard.py 回放引擎测试: 覆盖指纹定位/分支合并/可选跳过/预填变更/断点恢复。

运行: python3 test_wizard.py   (stdlib unittest, 无需第三方依赖)
"""
import copy
import json
import os
import shutil
import tempfile
import unittest

import wizard

ROOT = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class EngineTestBase(unittest.TestCase):
    def setUp(self):
        self.flow = load("flow.json")
        self.script = load("script.json")
        self.tmp = tempfile.mkdtemp(prefix="wz-", dir=ROOT)
        self.state_path = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_engine(self, script=None, inject=None):
        script = script or self.script
        return wizard.run(self.flow, copy.deepcopy(script),
                          state_path=self.state_path, inject=inject)

    # 用深拷贝保证 stop_after/resume_from 等临时改写不污染其它用例
    def script_copy(self):
        return copy.deepcopy(self.script)


class TestFingerprintAndBranches(EngineTestBase):
    EXPECTED_PATH = [
        "p_intro", "p_personal", "p_merge_contact", "p_product",
        "p_card", "p_merge_verify", "p_promo", "p_review",
    ]

    def test_happy_path_fingerprints_match(self):
        """验收1: 含3处分支的脚本, 逐页指纹与预期路径一致。"""
        r = self.run_engine()
        self.assertIsNone(r["aborted_at"])
        self.assertEqual(r["path"], self.EXPECTED_PATH)
        self.assertEqual([s["fp"] for s in r["submitted"]], self.EXPECTED_PATH)

    def test_three_branch_points_resolve_by_value_not_index(self):
        """三处分支: 客户类型/产品/条款, 均由载荷值驱动到正确页面。"""
        # 分支1+2: 企业 + 贷款 -> 走到 p_business / p_loan(序号错位旧逻辑必错)
        biz = load(os.path.join("fixtures", "script_business_loan.json"))
        r = self.run_engine(script=biz)
        self.assertIsNone(r["aborted_at"])
        self.assertEqual(
            r["path"],
            ["p_intro", "p_business", "p_merge_contact", "p_product",
             "p_loan", "p_merge_verify", "p_promo", "p_review"],
        )
        # 合并页 p_merge_contact 在两条分支上都能被指纹正确命中
        self.assertEqual(r["submitted"][2]["fp"], "p_merge_contact")
        self.assertEqual(r["submitted"][5]["fp"], "p_merge_verify")

    def test_unexpected_page_at_branch_aborts_immediately(self):
        """验收1: 分支点注入意外页面 -> 立即终止并定位到步骤与指纹。"""
        inject = {"3": {"when": "observe", "fp": "p_loan"}}  # 期望 p_product
        r = self.run_engine(inject=inject)
        self.assertEqual(r["aborted_at"], 3)
        self.assertEqual(r["expected"], "p_product")
        self.assertEqual(r["actual"], "p_loan")
        self.assertEqual(r["reason"], "fingerprint_mismatch")
        # 终止前已完成的步骤仍已持久化, 意外页面没有被写入任何值
        self.assertEqual([s["step"] for s in r["submitted"]], [0, 1, 2])
        with open(self.state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(len(state["completed"]), 3)

    def test_unexpected_page_after_submit_aborts_next_observe(self):
        """提交后浏览器跳到意外页面, 下一次 observe 即终止。"""
        inject = {"2": {"when": "submit", "fp": "p_deposit"}}
        r = self.run_engine(inject=inject)
        self.assertEqual(r["aborted_at"], 3)
        self.assertEqual(r["expected"], "p_product")
        self.assertEqual(r["actual"], "p_deposit")

    def test_optional_step_skipped(self):
        """可选步骤被跳过时不终止, 记 skipped, 后续页面按指纹对齐。"""
        sk = load(os.path.join("fixtures", "script_skip_promo.json"))
        r = self.run_engine(script=sk)
        self.assertIsNone(r["aborted_at"])
        self.assertIn(6, r["skipped"])           # p_promo 步骤被跳过
        self.assertNotIn("p_promo", r["path"])   # 实际路径不含可选页
        self.assertEqual(r["path"][-1], "p_review")
        # 被跳过的步骤没有产生提交
        self.assertEqual(
            [s["step"] for s in r["submitted"]], [0, 1, 2, 3, 4, 5, 7])

    def test_goto_cross_check(self):
        """脚本 goto 与 flow 规则求值不一致时终止(分支录制漂移)。"""
        bad = self.script_copy()
        bad["steps"][0]["goto"] = "p_loan"
        r = self.run_engine(script=bad)
        self.assertEqual(r["aborted_at"], 0)
        self.assertEqual(r["reason"], "goto_mismatch")
        self.assertEqual(r["expected"], "p_loan")
        self.assertEqual(r["actual"], "p_personal")


class TestFieldSemantics(EngineTestBase):
    def test_type_canonicalization_in_payload(self):
        r = self.run_engine()
        step5 = r["submitted"][5]["payload"]
        self.assertEqual(step5["verify_date"], "2024-03-05")   # 2024/03/05 -> ISO
        self.assertIs(step5["agree_terms"], True)              # "是" -> 布尔
        step7 = r["submitted"][7]["payload"]
        self.assertEqual(step7["confirm_date"], "2024-03-06")
        self.assertIs(step7["confirmed"], True)

    def test_prefill_change_field_level_diff(self):
        """预填值变更: 产出字段级差异, 含 expected/actual, 回放不中断。"""
        ch = load(os.path.join("fixtures", "script_prefill_changed.json"))
        r = self.run_engine(script=ch)
        self.assertIsNone(r["aborted_at"])
        diffs = [d for d in r["field_diff"] if d["kind"] == "prefill_mismatch"]
        self.assertEqual(len(diffs), 1)
        d = diffs[0]
        self.assertEqual((d["step"], d["fp"], d["field"]), (2, "p_merge_contact", "mobile"))
        self.assertEqual(d["expected"], "13700000000")
        self.assertEqual(d["actual"], "13800000000")
        # 实际预填值仍随载荷提交
        self.assertEqual(r["submitted"][2]["payload"]["mobile"], "13800000000")

    def test_invalid_values_field_diffs_only(self):
        """非法 select/日期/复选/未知字段 -> 字段级差异, 不写错值, 可继续的继续。"""
        inv = load(os.path.join("fixtures", "script_invalid_values.json"))
        r = self.run_engine(script=inv)
        kinds = [(d["step"], d["field"], d["kind"]) for d in r["field_diff"]]
        self.assertIn((1, "ghost_field", "unknown_field"), kinds)
        self.assertIn((4, "card_level", "option_not_allowed"), kinds)
        self.assertIn((4, "need_cheque", "invalid_value"), kinds)
        self.assertIn((5, "verify_date", "invalid_value"), kinds)
        self.assertIn((2, "mobile", "prefill_mismatch"), kinds)
        # 非法值不得进入载荷
        p4 = r["submitted"][4]["payload"]
        self.assertNotIn("card_level", p4)
        self.assertNotIn("need_cheque", p4)
        # 可解析的 0/1 复选语义仍然有效
        self.assertIs(r["submitted"][6]["payload"]["subscribe_promo"], True)

    def test_missing_branch_field_is_structural_abort(self):
        """分支字段非法被剔除导致无法路由: 结构性错误直接抛出, 区别于字段级差异。"""
        bad = self.script_copy()
        bad["steps"][0]["fill"] = {"customer_type": "alien"}
        with self.assertRaises(wizard.ReplayError):
            self.run_engine(script=bad)

    def test_canonicalize_unit_rules(self):
        self.assertEqual(wizard.canonicalize("text", "  abc "), "abc")
        self.assertEqual(wizard.canonicalize("select", 24), "24")
        self.assertEqual(wizard.canonicalize("date", "2024/1/2"), "2024-01-02")
        self.assertIs(wizard.canonicalize("checkbox", "是"), True)
        self.assertIs(wizard.canonicalize("checkbox", "0"), False)
        self.assertIs(wizard.canonicalize("checkbox", 1), True)
        with self.assertRaises(ValueError):
            wizard.canonicalize("date", "2024-02-30")
        with self.assertRaises(ValueError):
            wizard.canonicalize("checkbox", "maybe")
        with self.assertRaises(wizard.ReplayError):
            wizard.canonicalize("radio", "x")


class TestResume(EngineTestBase):
    def _chunk(self, stop_after, resume_from=None):
        sc = self.script_copy()
        sc["stop_after"] = stop_after
        if resume_from is not None:
            sc["resume_from"] = resume_from
        return self.run_engine(script=sc)

    def _resume(self, resume_from):
        sc = self.script_copy()
        sc["resume_from"] = resume_from
        return self.run_engine(script=sc)

    def test_resume_at_steps_2_5_7_no_resubmit(self):
        """验收2: 第2/5/7步后中断恢复, 已填字段不重复提交, 最终载荷逐字段一致。"""
        full = self.run_engine()                       # 基线: 一次跑完
        baseline = full["final_payload"]

        for cut in (2, 5, 7):
            # 独立状态目录, 模拟一次全新的"中断 -> 恢复"
            self.state_path = os.path.join(self.tmp, "state_cut_%d.json" % cut)
            first = self._chunk(stop_after=cut - 1)    # 完成 step 0..cut-1
            self.assertEqual([s["step"] for s in first["submitted"]],
                             list(range(cut)))
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            self.assertEqual(len(state["completed"]), cut)

            second = self._resume(resume_from=cut)
            # 已提交步骤进入 skipped, 且 submitted 中不重复出现
            self.assertEqual(sorted(second["skipped"]), list(range(cut)))
            submitted_steps = [s["step"] for s in second["submitted"]]
            self.assertEqual(submitted_steps, list(range(cut, 8)))
            for k in range(cut):
                self.assertNotIn(k, submitted_steps)
            # 恢复后路径与最终载荷和一次跑完完全一致
            self.assertEqual(second["path"], full["path"])
            self.assertEqual(second["final_payload"], baseline)

    def test_resume_payload_identical_across_two_runs(self):
        """验收2: 两次独立回放的最终提交载荷逐字段一致。"""
        r1 = self.run_engine()
        self.state_path = os.path.join(self.tmp, "state_again.json")
        r2 = self.run_engine()
        self.assertEqual(r1["final_payload"], r2["final_payload"])
        self.assertEqual(
            json.dumps(r1["final_payload"], sort_keys=True),
            json.dumps(r2["final_payload"], sort_keys=True),
        )

    def test_resume_uses_persisted_current_page(self):
        """恢复时运行时直接定位到持久化的 current_fp, 不按序号重放。"""
        self._chunk(stop_after=4)                      # 停在 p_card 提交之后
        with open(self.state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["current_fp"], "p_merge_verify")
        r = self._resume(resume_from=5)
        self.assertEqual(r["path"][5:],
                         ["p_merge_verify", "p_promo", "p_review"])

    def test_resume_from_mismatch_rejected(self):
        self._chunk(stop_after=2)
        sc = self.script_copy()
        sc["resume_from"] = 1                          # 状态实际停在 3
        with self.assertRaises(wizard.ReplayError):
            self.run_engine(script=sc)

    def test_stale_state_hash_rejected(self):
        self.run_engine()
        changed = self.script_copy()
        changed["steps"][0]["fill"]["customer_type"] = "business"
        with self.assertRaises(wizard.ReplayError):
            self.run_engine(script=changed)

    def test_resume_without_state_rejected(self):
        sc = self.script_copy()
        sc["resume_from"] = 3
        self.state_path = os.path.join(self.tmp, "missing.json")
        with self.assertRaises(wizard.ReplayError):
            self.run_engine(script=sc)

    def test_resume_after_abort_continues_from_last_consistent(self):
        """意外页面终止后, 去掉注入即可从最近一致步骤恢复, 不重填已提交页。"""
        first = self.run_engine(inject={"3": {"when": "observe", "fp": "p_loan"}})
        self.assertEqual(first["aborted_at"], 3)
        r = self._resume(resume_from=3)
        self.assertIsNone(r["aborted_at"])
        self.assertEqual(r["skipped"], [0, 1, 2])
        self.assertEqual([s["step"] for s in r["submitted"]], [3, 4, 5, 6, 7])
        self.assertEqual(r["path"], TestFingerprintAndBranches.EXPECTED_PATH)

    def test_resume_optional_skip_path(self):
        sk = load(os.path.join("fixtures", "script_skip_promo.json"))
        self.state_path = os.path.join(self.tmp, "state_skip.json")
        first = wizard.run(self.flow, copy.deepcopy(sk),
                           state_path=self.state_path)
        # 完整路径回放后再"恢复": 已全部完成, 仅做状态校验
        again = wizard.run(self.flow, copy.deepcopy(sk),
                           state_path=self.state_path)
        self.assertEqual(again["final_payload"], first["final_payload"])

    def test_runs_without_state_path(self):
        """不传 state 路径时引擎仍可正常回放(纯内存模式)。"""
        r = wizard.run(self.flow, copy.deepcopy(self.script))
        self.assertIsNone(r["aborted_at"])
        self.assertEqual(len(r["submitted"]), 8)
        self.assertEqual(r["skipped"], [])

    def test_state_written_atomically_no_tmp_left(self):
        self.run_engine()
        leftovers = [f for f in os.listdir(self.tmp)
                     if f.startswith(".state-")]
        self.assertEqual(leftovers, [])

    def test_field_diffs_aggregated_after_resume(self):
        """中断前产生的字段差异在恢复后的输出中仍然完整保留。"""
        inv = load(os.path.join("fixtures", "script_invalid_values.json"))
        first = wizard.run(self.flow, copy.deepcopy(inv),
                           state_path=self.state_path)
        # 该夹具本身可完整跑完(结构性错误已避开), 再跑一次幂等恢复
        again = wizard.run(self.flow, copy.deepcopy(inv),
                           state_path=self.state_path)
        self.assertEqual(
            len(again["field_diff"]), len(first["field_diff"]))
        self.assertEqual(
            sorted((d["step"], d["field"], d["kind"])
                   for d in again["field_diff"]),
            sorted((d["step"], d["field"], d["kind"])
                   for d in first["field_diff"]),
        )

    def test_corrupt_state_rejected(self):
        self.run_engine()
        with open(self.state_path, "w", encoding="utf-8") as fh:
            fh.write("{not-json")
        with self.assertRaises(wizard.ReplayError):
            self.run_engine()


class TestBaselineDefectFixed(EngineTestBase):
    def test_index_based_baseline_would_misplace_values(self):
        """对照: 旧的序号对齐逻辑在分支变体上会把值填错页面, 新引擎不会。"""
        biz = load(os.path.join("fixtures", "script_business_loan.json"))

        def legacy_index_run(flow, script):
            subs = []
            for i, st in enumerate(script["steps"]):
                page = flow["pages"][i] if i < len(flow["pages"]) else None
                payload = {}
                for f in (page or {}).get("fields", []):
                    if f["name"] in st["fill"]:
                        payload[f["name"]] = st["fill"][f["name"]]
                subs.append({"step": i, "fp": (page or {}).get("fp"),
                             "payload": payload})
            return subs

        legacy = legacy_index_run(self.flow, biz)
        # 旧逻辑第1步按序号拿到 p_personal(个人页), 填的却是企业字段 -> 载荷为空
        self.assertEqual(legacy[1]["fp"], "p_personal")
        self.assertEqual(legacy[1]["payload"], {})

        r = self.run_engine(script=biz)
        self.assertEqual(r["submitted"][1]["fp"], "p_business")
        self.assertEqual(
            r["submitted"][1]["payload"],
            {"company_name": "甲子科技有限公司", "licence_no": "LIC90001"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
