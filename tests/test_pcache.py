"""pcache.py 验收测试。

覆盖：
1. 独立参考实现 + 无缓存逐步全重算，与增量缓存输出逐步一致；
2. 冷启动（截断场景重跑）与增量结果、依赖索引一致；
3. 小场景下 hits/misses/invalidations/dep_index 的精确数值断言；
4. 短路求值、缺字段、边界值、字符串/整数比较等 evaluate 语义；
5. 1000 次更正固定夹具命中率 >= 90%，且无关字段从不触发失效；
6. CLI 子进程契约。
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pcache  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures"


# --------------------------------------------------------------------------
# 独立参考实现：不使用 pcache 的任何内部函数，按 README 语义重写。
# --------------------------------------------------------------------------
def ref_evaluate(row, rules):
    read = set()
    for rule in rules:
        ok = True
        for term in rule["pred"]:
            if "=" in term:
                field, expected = term.split("=", 1)
                op = "="
            else:
                field, expected = term.split(">", 1)
                op = ">"
            read.add(field)
            if field not in row:  # 缺字段不命中，短路
                ok = False
                break
            if op == "=":
                if str(row[field]) != expected:
                    ok = False
                    break
            else:
                if int(row[field]) <= int(expected):
                    ok = False
                    break
        if ok:
            return rule["allow"], frozenset(read)
    return False, frozenset(read)


def ref_recompute_every_step(scenario):
    """无缓存：每个更正后对全部行从头求值，返回展平的布尔结果。"""
    rows = copy.deepcopy([q["sub"] for q in scenario["queries"]])
    rules = copy.deepcopy(scenario["rules"])
    flat = []
    for change in scenario["changes"]:
        if change["kind"] == "attr":
            rows[change["sub_i"]][change["key"]] = change["to"]
        else:
            for rule in rules:
                if rule["id"] == change["id"]:
                    if "pred" in change:
                        rule["pred"] = list(change["pred"])
                    if "allow" in change:
                        rule["allow"] = change["allow"]
                    break
        for row in rows:
            flat.append(ref_evaluate(row, rules)[0])
    return flat


def ref_simulate_counters(scenario):
    """按规格模拟缓存计数，独立于 pcache 实现。"""
    rows = copy.deepcopy([q["sub"] for q in scenario["queries"]])
    rules = copy.deepcopy(scenario["rules"])
    cache = {}
    invalidations = {}
    hits = misses = 0
    results = []

    def fill(row_id):
        verdict, deps = ref_evaluate(rows[row_id], rules)
        cache[row_id] = (verdict, deps)
        return verdict

    for row_id in range(len(rows)):
        fill(row_id)

    for change in scenario["changes"]:
        if change["kind"] == "attr":
            row_id = change["sub_i"]
            key = change["key"]
            rows[row_id][key] = change["to"]
            entry = cache.get(row_id)
            if entry is not None and key in entry[1]:
                del cache[row_id]
                invalidations[key] = invalidations.get(key, 0) + 1
        else:
            for rule in rules:
                if rule["id"] == change["id"]:
                    if "pred" in change:
                        rule["pred"] = list(change["pred"])
                    if "allow" in change:
                        rule["allow"] = change["allow"]
                    break
            invalidations["rule"] = invalidations.get("rule", 0) + len(cache)
            cache.clear()

        for row_id in range(len(rows)):
            entry = cache.get(row_id)
            if entry is not None:
                hits += 1
                results.append(entry[0])
            else:
                misses += 1
                results.append(fill(row_id))

    dep_index = {}
    for row_id in sorted(cache):
        for field in cache[row_id][1]:
            dep_index.setdefault(field, set()).add(row_id)
    dep_index = {f: sorted(ids) for f, ids in sorted(dep_index.items())}
    return dict(results=results, hits=hits, misses=misses,
                invalidations=dict(sorted(invalidations.items())),
                dep_index=dep_index)


def run_pcache(scenario):
    """在临时文件上跑 pcache.main，返回解析后的输出 dict。"""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "in.json"
        out_path = Path(tmp) / "out.json"
        in_path.write_text(json.dumps(scenario))
        pcache.main(str(in_path), str(out_path))
        return json.loads(out_path.read_text())


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


class EvaluateSemanticsTest(unittest.TestCase):
    def test_first_matching_rule_wins_and_default_false(self):
        rules = [
            dict(id="r1", pred=["dept=eng"], allow=True),
            dict(id="r2", pred=["tier=gold"], allow=False),
        ]
        verdict, deps = pcache.evaluate(dict(dept="eng", tier="gold"), rules)
        self.assertTrue(verdict)
        self.assertEqual(deps, frozenset({"dept"}))
        verdict, _ = pcache.evaluate(dict(dept="sales", tier="bronze"), rules)
        self.assertFalse(verdict)

    def test_greater_than_integer_and_boundary(self):
        rules = [dict(id="r1", pred=["level>5"], allow=True)]
        verdict, deps = pcache.evaluate(dict(level=5), rules)
        self.assertFalse(verdict)  # 边界：相等不命中
        self.assertEqual(deps, frozenset({"level"}))
        verdict, _ = pcache.evaluate(dict(level=6), rules)
        self.assertTrue(verdict)
        verdict, _ = pcache.evaluate(dict(level="7"), rules)  # 数字字符串
        self.assertTrue(verdict)

    def test_missing_field_does_not_match_but_is_recorded(self):
        rules = [dict(id="r1", pred=["level>5"], allow=True)]
        verdict, deps = pcache.evaluate(dict(other=1), rules)
        self.assertFalse(verdict)
        self.assertEqual(deps, frozenset({"level"}))

    def test_short_circuit_records_only_read_fields(self):
        rules = [dict(id="r1", pred=["a=1", "b=2"], allow=True),
                 dict(id="r2", pred=["c=3"], allow=False)]
        verdict, deps = pcache.evaluate(dict(a=9, b=2, c=3), rules)
        # r1 首谓词失败即停，b 未读；落到 r2 读 c
        self.assertFalse(verdict)
        self.assertEqual(deps, frozenset({"a", "c"}))

    def test_equality_compares_as_string(self):
        rules = [dict(id="r1", pred=["level=7"], allow=True)]
        verdict, _ = pcache.evaluate(dict(level=7), rules)
        self.assertTrue(verdict)
        verdict, _ = pcache.evaluate(dict(level="7"), rules)
        self.assertTrue(verdict)

    def test_unsupported_predicate_raises(self):
        rules = [dict(id="r1", pred=["level<5"], allow=True)]
        with self.assertRaises(ValueError):
            pcache.evaluate(dict(level=1), rules)


class SmallScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_fixture("scenario_small.json")
        cls.output = run_pcache(copy.deepcopy(cls.scenario))

    def test_results_match_full_recompute(self):
        self.assertEqual(self.output["results"],
                         ref_recompute_every_step(self.scenario))

    def test_exact_boolean_sequence(self):
        f, t = False, True
        expected = [
            t, f, t, f,  # note 更正与 r1 无关，结论不变
            t, f, t, f,  # level 7->9 仍命中 r1
            f, f, t, f,  # level 9->4，r0 落到默认 false
            f, f, t, f,  # r1 只依赖 dept，改 level 不影响其拦截结论
            f, f, f, f,  # r2 的 tier gold->silver，r3 不再命中
            f, f, f, f,  # 规则更新后全量重算，全部 false
            f, f, f, f,  # r3 补 dept=eng，仍无规则命中
            f, f, f, f,  # r3 allow 翻转，全量重算
        ]
        self.assertEqual(self.output["results"], expected)

    def test_exact_hit_miss_invalidation_accounting(self):
        self.assertEqual(self.output["hits"], 20)
        self.assertEqual(self.output["misses"], 12)
        self.assertEqual(self.output["hits"] + self.output["misses"],
                         len(self.output["results"]))
        self.assertEqual(self.output["invalidations"],
                         {"dept": 1, "level": 2, "rule": 8, "tier": 1})

    def test_dep_index_explains_final_results(self):
        # 终态 r1 仅剩 dept=ops、r3 allow 翻转为 false，level 不再被任何行读取。
        self.assertEqual(self.output["dep_index"], {
            "dept": [0, 1, 2, 3],
            "tier": [0, 2, 3],
        })
        # 反向核验：索引中的每个 (字段, 行) 确实出现在该行最终求值依赖里。
        rows = copy.deepcopy([q["sub"] for q in self.scenario["queries"]])
        rules = copy.deepcopy(self.scenario["rules"])
        for change in self.scenario["changes"]:
            if change["kind"] == "attr":
                rows[change["sub_i"]][change["key"]] = change["to"]
            else:
                for rule in rules:
                    if rule["id"] == change["id"]:
                        rule["pred"] = change.get("pred", rule["pred"])
                        rule["allow"] = change.get("allow", rule["allow"])
        for row_id, row in enumerate(rows):
            _, deps = ref_evaluate(row, rules)
            for field, ids in self.output["dep_index"].items():
                self.assertEqual(field in deps, row_id in ids)

    def test_matches_independent_counter_simulation(self):
        expected = ref_simulate_counters(self.scenario)
        for key in ("results", "hits", "misses", "invalidations", "dep_index"):
            self.assertEqual(self.output[key], expected[key])

    def test_cold_start_equivalence_at_every_snapshot(self):
        width = len(self.scenario["queries"])
        for k in range(1, len(self.scenario["changes"]) + 1):
            truncated = copy.deepcopy(self.scenario)
            truncated["changes"] = truncated["changes"][:k]
            cold = run_pcache(truncated)
            # 同一记录快照：增量结果是全程结果的前缀，且与独立模拟逐快照一致。
            self.assertEqual(cold["results"],
                             self.output["results"][:k * width])
            snapshot = ref_simulate_counters(truncated)
            self.assertEqual(cold["results"], snapshot["results"])
            self.assertEqual(cold["dep_index"], snapshot["dep_index"])
            self.assertEqual(cold["hits"], snapshot["hits"])
            self.assertEqual(cold["misses"], snapshot["misses"])
            self.assertEqual(cold["invalidations"],
                             snapshot["invalidations"])


class EmptyScenarioTest(unittest.TestCase):
    def test_empty_changes(self):
        scenario = load_fixture("scenario_empty.json")
        output = run_pcache(copy.deepcopy(scenario))
        self.assertEqual(output["results"], [])
        self.assertEqual(output["hits"], 0)
        self.assertEqual(output["misses"], 0)
        self.assertEqual(output["invalidations"], {})
        # 无更正时依赖索引仍反映启动填缓存的最终状态。
        self.assertEqual(output["dep_index"], {"dept": [0, 1]})


class LargeFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_fixture("scenario_1000.json")
        cls.output = run_pcache(copy.deepcopy(cls.scenario))

    def test_fixture_shape(self):
        self.assertEqual(len(self.scenario["changes"]), 1000)
        n_rows = len(self.scenario["queries"])
        self.assertEqual(len(self.output["results"]), 1000 * n_rows)

    def test_results_match_full_recompute(self):
        self.assertEqual(self.output["results"],
                         ref_recompute_every_step(self.scenario))
        self.assertEqual(
            {k: self.output[k] for k in
             ("results", "hits", "misses", "invalidations", "dep_index")},
            ref_simulate_counters(self.scenario),
        )

    def test_cache_hit_rate_at_least_90_percent(self):
        total = self.output["hits"] + self.output["misses"]
        rate = self.output["hits"] / total
        print("\n1000-change fixture: hits=%d misses=%d hit_rate=%.4f"
              % (self.output["hits"], self.output["misses"], rate))
        self.assertGreaterEqual(rate, 0.90)

    def test_irrelevant_fields_never_invalidate(self):
        # note/phone 不被任何规则谓词读取，任何更正都不应造成失效。
        self.assertNotIn("note", self.output["invalidations"])
        self.assertNotIn("phone", self.output["invalidations"])

    def test_rule_invalidation_clears_all_rows_each_time(self):
        n_rows = len(self.scenario["queries"])
        rule_changes = sum(1 for c in self.scenario["changes"]
                           if c["kind"] == "rule")
        self.assertGreater(rule_changes, 0)
        self.assertEqual(self.output["invalidations"]["rule"],
                         rule_changes * n_rows)

    def test_dep_index_only_contains_predicate_fields(self):
        predicate_fields = set()
        for rule in self.scenario["rules"]:
            for term in rule["pred"]:
                predicate_fields.add(
                    term.split("=", 1)[0] if "=" in term
                    else term.split(">", 1)[0])
        self.assertEqual(set(self.output["dep_index"]), predicate_fields)

    def test_cold_start_equivalence_final_snapshot(self):
        cold = run_pcache(copy.deepcopy(self.scenario))
        self.assertEqual(cold["results"], self.output["results"])
        self.assertEqual(cold["dep_index"], self.output["dep_index"])
        self.assertEqual(cold["invalidations"], self.output["invalidations"])


class CliContractTest(unittest.TestCase):
    def test_cli_matches_in_process_run(self):
        scenario = load_fixture("scenario_small.json")
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "in.json"
            out_path = Path(tmp) / "out.json"
            in_path.write_text(json.dumps(scenario))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "pcache.py"),
                 str(in_path), str(out_path)],
                capture_output=True, text=True, check=True)
            self.assertEqual(proc.stdout, "")
            cli_output = json.loads(out_path.read_text())
        self.assertEqual(cli_output, run_pcache(copy.deepcopy(scenario)))
        self.assertEqual(
            set(cli_output),
            {"results", "hits", "misses", "invalidations", "dep_index"})


if __name__ == "__main__":
    unittest.main(verbosity=2)