"""随机化属性测试：多组随机场景下缓存增量 == 无缓存全重算。"""
import copy
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_pcache import (  # noqa: E402
    ref_recompute_every_step, ref_simulate_counters, run_pcache)

FIELDS = ["dept", "level", "tier", "note"]
DEPTS = ["eng", "ops", "sales", "hr"]
TIERS = ["gold", "silver", "bronze"]


def make_scenario(seed):
    rng = random.Random(seed)
    n_rules = rng.randint(1, 4)
    rule_ids = ["r%d" % i for i in range(n_rules)]
    rules = []
    for rid in rule_ids:
        preds = []
        for _ in range(rng.randint(1, 3)):
            field = rng.choice(["dept", "level", "tier"])
            if field == "level":
                preds.append("level>%d" % rng.randint(0, 9))
            elif field == "dept":
                preds.append("dept=%s" % rng.choice(DEPTS))
            else:
                preds.append("tier=%s" % rng.choice(TIERS))
        rules.append(dict(id=rid, pred=preds,
                          allow=rng.random() < 0.5))

    n_rows = rng.randint(1, 30)
    queries = []
    for i in range(n_rows):
        row = dict(dept=rng.choice(DEPTS),
                   level=rng.randint(1, 10),
                   tier=rng.choice(TIERS),
                   note="n-%d" % i)
        if rng.random() < 0.2:  # 部分行缺字段
            del row[rng.choice(["dept", "level", "tier"])]
        queries.append(dict(sub=row))

    changes = []
    for _ in range(rng.randint(0, 120)):
        if rng.random() < 0.85:
            field = rng.choice(FIELDS)  # note 永远不被规则读取
            if field == "dept":
                to = rng.choice(DEPTS)
            elif field == "level":
                to = rng.randint(1, 10)
            elif field == "tier":
                to = rng.choice(TIERS)
            else:
                to = "x-%d" % rng.randrange(1000)
            changes.append(dict(kind="attr",
                                sub_i=rng.randrange(n_rows),
                                key=field, to=to))
        else:
            target = rng.choice(rule_ids)
            change = dict(kind="rule", id=target)
            if rng.random() < 0.6:
                change["pred"] = [rng.choice([
                    "dept=%s" % rng.choice(DEPTS),
                    "level>%d" % rng.randint(0, 9),
                    "tier=%s" % rng.choice(TIERS),
                ])]
            if rng.random() < 0.6:
                change["allow"] = rng.random() < 0.5
            if "pred" not in change and "allow" not in change:
                change["allow"] = True
            changes.append(change)
    return dict(rules=rules, queries=queries, changes=changes)


class RandomizedEquivalenceTest(unittest.TestCase):
    def test_many_scenarios(self):
        for seed in range(60):
            with self.subTest(seed=seed):
                scenario = make_scenario(seed)
                output = run_pcache(copy.deepcopy(scenario))
                # 1) 与无缓存逐步全重算完全一致
                self.assertEqual(output["results"],
                                 ref_recompute_every_step(scenario))
                # 2) 与独立缓存模拟的全部计数一致
                sim = ref_simulate_counters(scenario)
                for key in ("results", "hits", "misses",
                            "invalidations", "dep_index"):
                    self.assertEqual(output[key], sim[key])
                # 3) 失效字段必须在初始规则或历次规则版本中被谓词读取过；
                #    note 从未出现在任何规则版本里，因此永不失效。
                ever_used = set()
                for rule in scenario["rules"]:
                    for term in rule["pred"]:
                        ever_used.add(
                            term.split("=", 1)[0] if "=" in term
                            else term.split(">", 1)[0])
                for change in scenario["changes"]:
                    if change["kind"] == "rule" and "pred" in change:
                        for term in change["pred"]:
                            ever_used.add(
                                term.split("=", 1)[0] if "=" in term
                                else term.split(">", 1)[0])
                for field in output["invalidations"]:
                    if field != "rule":
                        self.assertIn(field, ever_used)
                self.assertNotIn("note", output["invalidations"])
                # 4) 冷启动整体重跑结果一致
                cold = run_pcache(copy.deepcopy(scenario))
                self.assertEqual(cold, output)
                # 5) hits + misses 等于输出结果总数
                self.assertEqual(output["hits"] + output["misses"],
                                 len(output["results"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
