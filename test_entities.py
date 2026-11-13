"""实体关系引擎验收测试。

覆盖：
  1. 链式合并、桥边撤回、冲突边插入；与独立图求解器逐版本对拍实体划分；
     再用手工预期验证主 ID 继承、别名与生效版本。
  2. 4 种输入分块回放 + 重复部分边，核对实体 ID、别名与审计明细完全一致；
     仅影响小群组的撤回不得让其它群组重新分配 ID。
"""
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import entities
from entities import Engine, EngineError, edge_key, norm_pair

ROOT = Path(__file__).resolve().parent


# ----------------------------------------------------------------------
# 独立参考求解器：与引擎完全不同的实现风格（frozenset 合并 + 集合扫描），
# 只看给定前缀里的活动边，逐版本给出划分与活动冲突对。
# ----------------------------------------------------------------------
def reference_solve(nodes, links):
    """返回每版本: {version: (partition_sorted, blocked_links_sorted)}。"""
    active_same = {}   # pair -> set(sources)
    active_conf = {}   # pair -> set(sources)
    seen_events = {}
    out = {0: (tuple(sorted((n,) for n in nodes)), [])}
    version = 0
    for link in links:
        ev, a, b = link["event"], link["a"], link["b"]
        src, kind, action = link["source"], link["kind"], link["action"]
        payload = (a, b, src, kind, action)
        if ev in seen_events:
            if seen_events[ev] == payload:
                out[version] = _ref_partition(nodes, active_same, active_conf)
                continue
            raise AssertionError("重复 event 载荷冲突")
        seen_events[ev] = payload
        pair = norm_pair(a, b)
        table = active_same if kind == "same" else active_conf
        if action == "set":
            table.setdefault(pair, set()).add(src)
        else:
            table.get(pair, set()).discard(src)
            if pair in table and not table[pair]:
                del table[pair]
        version += 1
        out[version] = _ref_partition(nodes, active_same, active_conf)
    return out


def _ref_partition(nodes, active_same, active_conf):
    groups = {n: frozenset((n,)) for n in nodes}
    for pair in sorted(active_same):
        a, b = pair
        ga, gb = groups[a], groups[b]
        if ga == gb:
            continue
        merged = ga | gb
        violates = any((p[0] in merged and p[1] in merged)
                       for p in active_conf)
        if violates:
            continue
        for n in merged:
            groups[n] = merged
    partition = sorted({g for g in groups.values()}, key=lambda g: sorted(g))
    blocked = [list(p) for p in sorted(active_conf)]
    return tuple(tuple(sorted(g)) for g in partition), blocked


def canon(partition):
    """把引擎/参考求解器的划分统一成可比较的元组形式（组间也排序）。"""
    return tuple(sorted(tuple(sorted(members)) for members in partition))


def feed(engine, links):
    """喂入链接并收集每版本输出。"""
    outputs = [engine.output()]
    for link in links:
        engine.apply_event(link)
        outputs.append(engine.output())
    return outputs


def partition_of(output):
    return tuple(c["members"] for c in output["clusters"])


class HandcraftedScenarioTest(unittest.TestCase):
    """手工场景：链式合并、冲突插入、冲突撤回、桥撤回、多来源。"""

    LINKS = [
        {"event": "e1", "a": "a", "b": "b", "source": "s1",
         "kind": "same", "action": "set"},
        {"event": "e2", "a": "b", "b": "c", "source": "s1",
         "kind": "same", "action": "set"},                 # 链式合并 a-b-c
        {"event": "e3", "a": "a", "b": "c", "source": "s2",
         "kind": "conflict", "action": "set"},             # 冲突：拒绝 a|c 合并
        {"event": "e4", "a": "d", "b": "e", "source": "s1",
         "kind": "same", "action": "set"},
        {"event": "e5", "a": "c", "b": "d", "source": "s1",
         "kind": "same", "action": "set"},                 # 桥：想接两大群，被冲突挡
        {"event": "e6", "a": "a", "b": "c", "source": "s2",
         "kind": "conflict", "action": "retract"},         # 冲突撤回
        {"event": "e7", "a": "c", "b": "d", "source": "s1",
         "kind": "same", "action": "set"},                 # 重放桥边：五者合并
        {"event": "e8", "a": "b", "b": "c", "source": "s1",
         "kind": "same", "action": "retract"},             # 桥撤回 -> 拆分
        {"event": "e9", "a": "b", "b": "c", "source": "s3",
         "kind": "same", "action": "set"},                 # 新来源重建 b-c
        {"event": "e10", "a": "b", "b": "c", "source": "s3",
         "kind": "same", "action": "retract"},             # s3 撤回，s1 也早已撤
    ]
    NODES = ["a", "b", "c", "d", "e"]

    def setUp(self):
        self.engine = Engine(self.NODES)
        self.outputs = feed(self.engine, self.LINKS)

    def test_versions_against_reference(self):
        ref = reference_solve(self.NODES, self.LINKS)
        for version, out in enumerate(self.outputs):
            ref_part, ref_blocked = ref[version]
            self.assertEqual(canon(partition_of(out)), ref_part, version)
            self.assertEqual(out["blocked_links"], ref_blocked, version)

    def test_engine_solve_all_matches_reference(self):
        # 引擎全量求解路径也必须与独立求解器一致
        replay_versions = range(0, self.engine.version + 1)
        for v in replay_versions:
            eng = self.engine.replay(v)
            parent, blocked, _ = eng.solve_all()
            groups = {}
            for n in eng.nodes:
                r = n
                while parent[r] != r:
                    r = parent[r]
                groups.setdefault(r, []).append(n)
            part = tuple(sorted(sorted(m) for m in groups.values()))
            ref_part, ref_blocked = reference_solve(
                self.NODES, self.LINKS[:v])[v]
            self.assertEqual(canon(part), ref_part, v)
            self.assertEqual([list(p) for p in blocked], ref_blocked, v)

    def test_main_id_inheritance_manual(self):
        ids = lambda v: {c["id"]: c["members"]
                         for c in self.outputs[v]["clusters"]}
        # v1 链式合并第一步
        self.assertEqual(ids(1), {"entity-a": ["a", "b"],
                                  "entity-c": ["c"], "entity-d": ["d"],
                                  "entity-e": ["e"]})
        # v2 链式合并：最小锚 a 继承主 ID
        self.assertEqual(ids(2), {"entity-a": ["a", "b", "c"],
                                  "entity-d": ["d"], "entity-e": ["e"]})
        # v3 冲突边插入：a|c 成为活动冲突，区域重算后 abc 被拆成 {a,b} 与 {c}
        self.assertEqual(ids(3), {"entity-a": ["a", "b"], "entity-c": ["c"],
                                  "entity-d": ["d"], "entity-e": ["e"]})
        self.assertEqual(self.outputs[3]["blocked_links"], [["a", "c"]])
        # v5 桥 c-d 不跨越冲突对（冲突是 a|c，a 不在 cde 侧），允许合并
        self.assertEqual(ids(5), {"entity-a": ["a", "b"],
                                  "entity-c": ["c", "d", "e"]})
        self.assertEqual(self.outputs[5]["blocked_links"], [["a", "c"]])
        # 若再尝试把 ab 群与 cde 群合并，才会被 a|c 冲突拒绝并留证
        rejected = [m for m in self.engine.blocked_merges]
        self.assertTrue(any(m["blocked_by"] == [["a", "c"]] for m in rejected))
        # v6 冲突撤回：约束消失，重放扫描把全部节点合并到最小锚 a
        self.assertEqual(self.outputs[6]["blocked_links"], [])
        self.assertEqual(ids(6), {"entity-a": ["a", "b", "c", "d", "e"]})
        # v7 重放桥边：已是同一群，主 ID 仍为 entity-a
        self.assertEqual(ids(7), {"entity-a": ["a", "b", "c", "d", "e"]})
        # v8 桥撤回拆成两个群；c 与 d/e 同群且 c 是最小锚 -> 群 ID entity-c
        self.assertEqual(ids(8), {"entity-a": ["a", "b"],
                                  "entity-c": ["c", "d", "e"]})
        # v9 新来源 s3 重建 b-c：五者重新合并到最小锚 entity-a
        self.assertEqual(ids(9), {"entity-a": ["a", "b", "c", "d", "e"]})
        # v10 s3 也撤回，再次拆回 v8 形态
        self.assertEqual(ids(10), {"entity-a": ["a", "b"],
                                   "entity-c": ["c", "d", "e"]})

    def test_aliases_and_effective_versions(self):
        def aliases(v):
            return {a["from"]: a["to"] for a in self.outputs[v]["aliases"]}
        self.assertEqual(aliases(1), {"entity-b": "entity-a"})
        self.assertEqual(aliases(2), {"entity-b": "entity-a",
                                      "entity-c": "entity-a"})
        # v3-v7 合并形态不变
        self.assertEqual(aliases(7), {"entity-b": "entity-a",
                                      "entity-c": "entity-a",
                                      "entity-d": "entity-a",
                                      "entity-e": "entity-a"})
        # v8 拆分：c 恢复锚（别名撤销），d/e 并入 c 群，旧别名 repoint
        self.assertEqual(aliases(8), {"entity-b": "entity-a",
                                      "entity-d": "entity-c",
                                      "entity-e": "entity-c"})
        log = self.engine.alias_log
        self.assertTrue(any(e["version"] == 8 and e["action"] == "revoke"
                            and e["from"] == "entity-c" for e in log))
        self.assertTrue(any(e["version"] == 8 and e["action"] == "repoint"
                            and e["from"] == "entity-d"
                            and e["previous"] == "entity-a"
                            and e["to"] == "entity-c" for e in log))
        # v9 再合并：entity-c 重新指向 entity-a，d/e 也 repoint 回去
        self.assertEqual(aliases(9), {"entity-b": "entity-a",
                                      "entity-c": "entity-a",
                                      "entity-d": "entity-a",
                                      "entity-e": "entity-a"})
        # v10 再拆分：回到 v8 的别名形态
        self.assertEqual(aliases(10), {"entity-b": "entity-a",
                                       "entity-d": "entity-c",
                                       "entity-e": "entity-c"})
        # 别名键始终是合法历史实体 ID
        for v, out in enumerate(self.outputs):
            for item in out["aliases"]:
                self.assertTrue(item["from"].startswith("entity-"))

    def test_membership_changes(self):
        by_version = {c["version"]: c["move"] for c in self.engine.changes}
        self.assertEqual({m["node"] for m in by_version[1]}, {"b"})
        self.assertEqual({m["node"] for m in by_version[2]}, {"c"})
        # v8 拆分：c 恢复为锚，d/e 归入以 c 为最小锚的群
        self.assertEqual({m["node"] for m in by_version[8]}, {"c", "d", "e"})
        self.assertTrue(all(m["from"] == "entity-a" for m in by_version[8]))
        to_by_node = {m["node"]: m["to"] for m in by_version[8]}
        self.assertEqual(to_by_node, {"c": "entity-c", "d": "entity-c",
                                      "e": "entity-c"})

    def test_evidence_traceability(self):
        ev = self.engine.evidence(7)
        cluster = next(c for c in ev["clusters"] if c["id"] == "entity-a")
        self.assertEqual(cluster["members"], ["a", "b", "c", "d", "e"])
        support_edges = {tuple(r["edge"]) for r in cluster["support_records"]}
        self.assertEqual(support_edges, {("a", "b"), ("b", "c"),
                                         ("c", "d"), ("d", "e")})
        # 每条支持记录都能追溯到输入 event
        for rec in cluster["support_records"]:
            self.assertIn(rec["event"], {l["event"] for l in self.LINKS})
        # 决定性边集合：a-b, b-c, c-d, d-e 各出现一次
        decisive = {tuple(d["edge"]) for d in cluster["decisive_edges"]}
        self.assertEqual(decisive, {("a", "b"), ("b", "c"),
                                    ("c", "d"), ("d", "e")})
        # 历史版本成员清单：v3 冲突插入后 abc 已拆开
        v3 = self.engine.evidence(3)
        self.assertEqual({c["id"]: c["members"] for c in v3["membership_at"]},
                         {"entity-a": ["a", "b"], "entity-c": ["c"],
                          "entity-d": ["d"], "entity-e": ["e"]})
        # v3 被冲突挡住的 b-c 合并、以及各版本拒绝记录都保留审计证据
        self.assertTrue(any(m["version"] == 3 and m["pair"] == ["b", "c"]
                            for m in ev["blocked_merges"]))

    def test_incremental_region_isolation(self):
        # 每次重算的区域只包含受影响连通块；v4 处理 d-e 时不得触及 abc 群
        regions = {r["version"]: r["region"] for r in self.engine.region_log}
        self.assertEqual(sorted(regions[4]), ["d", "e"])
        # v3 冲突边 a-c：区域为 abc 连通块
        self.assertEqual(sorted(regions[3]), ["a", "b", "c"])
        # v8 撤回 b-c 后重算，种子覆盖两侧（整个原连通块都在区域内）
        self.assertEqual(sorted(regions[8]), ["a", "b", "c", "d", "e"])


class FuzzAgainstReferenceTest(unittest.TestCase):
    """随机序列：链式合并、撤回、冲突插入、多来源，逐版本对拍。"""

    def _random_links(self, rng, nodes, n_events):
        links = []
        active = []  # (pair, source, kind) 当前活动
        for i in range(n_events):
            if active and rng.random() < 0.35:
                pair, source, kind = active.pop(rng.randrange(len(active)))
                links.append({"event": "ev%03d" % i, "a": pair[0], "b": pair[1],
                              "source": source, "kind": kind,
                              "action": "retract"})
                continue
            a, b = rng.sample(nodes, 2)
            kind = "conflict" if rng.random() < 0.3 else "same"
            source = rng.choice(["s1", "s2", "s3"])
            pair = norm_pair(a, b)
            links.append({"event": "ev%03d" % i, "a": a, "b": b,
                          "source": source, "kind": kind, "action": "set"})
            active.append((pair, source, kind))
        return links

    def test_fuzz_matches_reference(self):
        rng = random.Random(20261114)
        for trial in range(40):
            nodes = ["n%d" % i for i in range(rng.randint(2, 9))]
            links = self._random_links(rng, nodes, rng.randint(10, 40))
            engine = Engine(nodes)
            outputs = feed(engine, links)
            ref = reference_solve(nodes, links)
            for v, out in enumerate(outputs):
                ref_part, ref_blocked = ref[v]
                self.assertEqual(canon(partition_of(out)), ref_part,
                                 (trial, v, links))
                self.assertEqual(out["blocked_links"], ref_blocked,
                                 (trial, v, links))
            # 全量求解路径与增量结果在最终版本一致
            parent, blocked, _ = engine.solve_all()
            groups = {}
            for n in nodes:
                r = n
                while parent[r] != r:
                    r = parent[r]
                groups.setdefault(r, []).append(n)
            part = tuple(sorted(sorted(m) for m in groups.values()))
            self.assertEqual(canon(part), canon(partition_of(outputs[-1])),
                             trial)


class ReplayAndChunkingTest(unittest.TestCase):
    """4 种分块回放 + 重复边，要求 ID/别名/审计逐字节稳定。"""

    NODES = ["a", "b", "c", "d", "e", "f"]
    LINKS = HandcraftedScenarioTest.LINKS + [
        {"event": "e11", "a": "e", "b": "f", "source": "s2",
         "kind": "same", "action": "set"},
        {"event": "e12", "a": "a", "b": "f", "source": "s2",
         "kind": "conflict", "action": "set"},
        {"event": "e13", "a": "e", "b": "f", "source": "s2",
         "kind": "same", "action": "retract"},
    ]

    def _canonical(self, engine):
        return json.dumps(engine.to_dict(), ensure_ascii=False,
                          sort_keys=True)

    def test_full_run_is_deterministic(self):
        runs = []
        for _ in range(3):
            eng = Engine(self.NODES)
            for link in self.LINKS:
                eng.apply_event(link)
            runs.append(self._canonical(eng))
        self.assertEqual(len(set(runs)), 1)

    def test_four_chunkings_and_duplicates(self):
        full = Engine(self.NODES)
        for link in self.LINKS:
            full.apply_event(link)
        expected = self._canonical(full)

        n = len(self.LINKS)
        # 4 种分块方式：1 块、2 块、3 块、逐事件
        plans = [[n], [n // 2, n - n // 2],
                 [n // 3, n // 3, n - 2 * (n // 3)],
                 [1] * n]
        for plan in plans:
            self.assertEqual(sum(plan), n)
            eng = Engine(self.NODES)
            pos = 0
            for size in plan:
                chunk = self.LINKS[pos:pos + size]
                # 在块内重复部分边（等价 event 必须幂等）
                if chunk:
                    chunk = chunk + [chunk[0], chunk[-1]]
                for link in chunk:
                    eng.apply_event(link)
                pos += size
            self.assertEqual(self._canonical(eng), expected, plan)
            # 输出视图（ID、别名、changes）也相同
            self.assertEqual(eng.output(), full.output())
            self.assertEqual(eng.evidence(), full.evidence())

    def test_small_group_retract_keeps_other_ids(self):
        # 独立小群 x-y 的撤回，不得改动主群的任何实体 ID
        nodes = self.NODES + ["x", "y"]
        eng = Engine(nodes)
        base = HandcraftedScenarioTest.LINKS
        for link in base:
            eng.apply_event(link)
        before = {n: eng.owner[n] for n in self.NODES}
        eng.apply_event({"event": "xy1", "a": "x", "b": "y", "source": "s9",
                         "kind": "same", "action": "set"})
        eng.apply_event({"event": "xy2", "a": "x", "b": "y", "source": "s9",
                         "kind": "same", "action": "retract"})
        after = {n: eng.owner[n] for n in self.NODES}
        self.assertEqual(before, after)
        self.assertEqual(eng.owner["x"], "entity-x")
        self.assertEqual(eng.owner["y"], "entity-y")
        # 小群操作的重算区域仅包含 x/y
        last_regions = [r["region"] for r in eng.region_log
                        if r["event"] in ("xy1", "xy2")]
        self.assertTrue(all(set(r) <= {"x", "y"} for r in last_regions))

    def test_duplicate_event_conflict_exits_and_preserves_state(self):
        eng = Engine(self.NODES)
        eng.apply_event(self.LINKS[0])
        snapshot = self._canonical(eng)
        bad = dict(self.LINKS[0])
        bad["source"] = "other"  # 同 event 不同载荷
        with self.assertRaises(EngineError):
            eng.apply_event(bad)
        self.assertEqual(self._canonical(eng), snapshot)
        # 等价重复 event 幂等，版本不动
        v = eng.version
        eng.apply_event(self.LINKS[0])
        self.assertEqual(eng.version, v)


class MultiSourceTest(unittest.TestCase):
    def test_retract_one_source_keeps_edge(self):
        eng = Engine(["a", "b", "c"])
        eng.apply_event({"event": "1", "a": "a", "b": "b", "source": "s1",
                         "kind": "same", "action": "set"})
        eng.apply_event({"event": "2", "a": "a", "b": "b", "source": "s2",
                         "kind": "same", "action": "set"})
        eng.apply_event({"event": "3", "a": "a", "b": "b", "source": "s1",
                         "kind": "same", "action": "retract"})
        self.assertEqual(eng.owner["a"], eng.owner["b"])
        self.assertEqual(eng.active_pairs[("a", "b")], {"s2"})
        # 审计里两个来源的输入记录都可追溯
        ev = eng.evidence()
        rec = ev["clusters"][0]["support_records"]
        self.assertEqual({r["source"] for r in rec}, {"s2"})

    def test_conflict_priority_blocks_merge(self):
        eng = Engine(["a", "b", "c"])
        eng.apply_event({"event": "1", "a": "a", "b": "c", "source": "s1",
                         "kind": "conflict", "action": "set"})
        eng.apply_event({"event": "2", "a": "a", "b": "b", "source": "s1",
                         "kind": "same", "action": "set"})
        eng.apply_event({"event": "3", "a": "b", "b": "c", "source": "s1",
                         "kind": "same", "action": "set"})  # 被冲突拒绝
        self.assertNotEqual(eng.owner["a"], eng.owner["c"])
        self.assertEqual(eng.owner["a"], eng.owner["b"])
        self.assertEqual(eng.output()["blocked_links"], [["a", "c"]])


class CliTest(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "entities.py"), *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_cli_apply_inspect_chunked_and_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            inp = tmp / "in.json"
            db = tmp / "state.json"
            inp.write_text(json.dumps(
                {"nodes": HandcraftedScenarioTest.NODES,
                 "links": HandcraftedScenarioTest.LINKS}))
            # 全量 apply，再整文件重跑（幂等）
            r1 = self._run("apply", str(db), str(inp))
            self.assertEqual(r1.returncode, 0, r1.stderr)
            r2 = self._run("apply", str(db), str(inp))
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertEqual(r1.stdout, r2.stdout)

            # --upto 前缀与 inspect --version
            db2 = tmp / "state2.json"
            r3 = self._run("apply", str(db2), str(inp), "--upto", "5")
            self.assertEqual(r3.returncode, 0, r3.stderr)
            r4 = self._run("inspect", str(db2), "--version", "5")
            self.assertEqual(json.loads(r4.stdout)["version"], 5)

            # 分块文件累积 apply 与一次性全量结果一致
            db3 = tmp / "state3.json"
            links = HandcraftedScenarioTest.LINKS
            cuts = [0, 3, 7, len(links)]
            outs = []
            for i in range(3):
                part = tmp / ("part%d.json" % i)
                part.write_text(json.dumps(
                    {"nodes": HandcraftedScenarioTest.NODES,
                     "links": links[cuts[i]:cuts[i + 1]]}))
                rr = self._run("apply", str(db3), str(part))
                self.assertEqual(rr.returncode, 0, rr.stderr)
                outs.append(rr.stdout)
            full_state = json.loads(db.read_text())
            chunk_state = json.loads(db3.read_text())
            for key in ("owner", "aliases", "changes", "alias_log",
                        "blocked_merges", "region_log", "snapshots"):
                self.assertEqual(full_state[key], chunk_state[key], key)

            # audit 视图可在任意版本输出
            r5 = self._run("inspect", str(db), "--version", "3", "--audit")
            self.assertEqual(r5.returncode, 0, r5.stderr)
            audit = json.loads(r5.stdout)
            self.assertEqual(audit["version"], 3)
            self.assertTrue(audit["clusters"])

    def test_cli_conflict_payload_exit_2_preserves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            db = tmp / "state.json"
            good = tmp / "good.json"
            bad = tmp / "bad.json"
            good.write_text(json.dumps({"nodes": ["a", "b"], "links": [
                {"event": "e1", "a": "a", "b": "b", "source": "s1",
                 "kind": "same", "action": "set"}]}))
            bad.write_text(json.dumps({"nodes": ["a", "b"], "links": [
                {"event": "e1", "a": "a", "b": "b", "source": "HACK",
                 "kind": "same", "action": "set"}]}))
            r1 = self._run("apply", str(db), str(good))
            self.assertEqual(r1.returncode, 0)
            before = db.read_text()
            r2 = self._run("apply", str(db), str(bad))
            self.assertEqual(r2.returncode, 2)
            self.assertEqual(db.read_text(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
