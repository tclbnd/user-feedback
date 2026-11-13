"""验收测试.

验收 1: 跨周及滚动窗口边界的 6 次改动, 每个分区结果与独立全量重建
        (reference_*) 逐分区比较; 实际执行集合与独立依赖枚举器
        (independent_affected_set) 比较。
验收 2: 两个依赖层之间改变输入并注入中断: 恢复后旧结果仍可读;
        运行中变化被拒绝发布(98); 最终发布的每个分区依赖指纹与
        对应数据版本匹配。另覆盖部分失败不得发布(99)与循环拒绝(2)。
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill as bf

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# 独立参考实现(不 import 引擎的图/指纹/计划逻辑, 仅共用同一规范定义)
# --------------------------------------------------------------------------
def ref_canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def ref_sha(obj):
    return hashlib.sha256(ref_canon(obj)).hexdigest()


def reference_full_rebuild(inputs, nodes):
    """朴素全量重建: 反复扫描直到全部节点可解, 返回 values/fingerprints。"""
    values, fps = {}, {}
    remaining = dict(nodes)
    while remaining:
        progressed = False
        for key in sorted(remaining):
            deps = remaining[key]
            if all((d in inputs) or (d in values) or (d not in inputs and d not in nodes)
                   for d in deps):
                total, dep_fps = 0, []
                for dep in deps:
                    if dep in inputs:
                        e = inputs[dep]
                        total += int(e["value"])
                        dep_fps.append(ref_sha(
                            ["source", dep, str(e["version"]), int(e["value"])]))
                    elif dep in values:
                        total += int(values[dep])
                        dep_fps.append(fps[dep])
                    else:
                        dep_fps.append(ref_sha(["missing", dep]))
                values[key] = total
                fps[key] = ref_sha(["node", key, dep_fps])
                del remaining[key]
                progressed = True
                break
        if not progressed:
            raise AssertionError("参考实现无法解析的依赖: " + str(remaining))
    return values, fps


def independent_affected_set(old_manifest, new_manifest):
    """独立依赖枚举器: 只依据清单结构做变化源 + 下游可达集合。"""
    old_in, new_in = old_manifest["inputs"], new_manifest["inputs"]
    nodes = new_manifest["nodes"]

    def sfp(m, key):
        e = m[key]
        return ref_sha(["source", key, str(e["version"]), int(e["value"])])

    changed = set()
    for key in set(old_in) | set(new_in):
        before = sfp(old_in, key) if key in old_in else None
        after = sfp(new_in, key) if key in new_in else None
        if before != after:
            changed.add(key)
    if old_manifest["nodes"] != new_manifest["nodes"]:
        # 结构变化: 依赖列表改变的节点视为新的变化根
        for key in nodes:
            if old_manifest["nodes"].get(key) != nodes[key]:
                changed.add(key)

    children = {}
    for node, deps in nodes.items():
        for dep in deps:
            children.setdefault(dep, set()).add(node)
    affected, stack = set(), list(changed)
    while stack:
        cur = stack.pop()
        for nxt in children.get(cur, ()):  # 仅派生节点会出现在 children 的键值
            if nxt not in affected:
                affected.add(nxt)
                stack.append(nxt)
    return affected


# --------------------------------------------------------------------------
# 子进程/清单工具
# --------------------------------------------------------------------------
def run_cli(root, *args, env=None, expect=None):
    proc = subprocess.run(
        [sys.executable, str(HERE / "backfill.py"), str(root), *args],
        capture_output=True, text=True, env=env)
    if expect is not None:
        assert proc.returncode == expect, \
            "exit=%s stdout=%s stderr=%s" % (proc.returncode, proc.stdout,
                                             proc.stderr)
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, payload


def write_manifest(directory, manifest, name="manifest.json"):
    path = Path(directory) / name
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(HERE))
        self.work = Path(self.tmp.name)
        self.base = json.loads((HERE / "rolling_demo.json").read_text("utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # 验收 1: 6 次跨周/滚动边界改动
    # ------------------------------------------------------------------
    def test_six_changes_vs_full_rebuild_and_enumerator(self):
        root = self.work / "acc1"
        manifest_path = write_manifest(self.work, self.base)
        code, first = run_cli(root, "build", str(manifest_path), expect=0)

        # 首次构建: 执行集合 == 枚举器在“空基线”下的全部派生节点
        self.assertEqual(sorted(first["executed"]),
                         sorted(self.base["nodes"].keys()))
        ref_v, ref_fp = reference_full_rebuild(
            self.base["inputs"], self.base["nodes"])
        self.assertEqual(first["values"], ref_v)
        self.assertEqual(first["fingerprints"], ref_fp)

        # 6 次改动: 滚动边界两端、跨周边界、原样重传、版本指纹、缺失补齐、
        # 跨时区划分(同一日不同地区键)
        def m1(m):
            m["inputs"]["day/2026-08-01/cn"]["value"] = 11
            m["inputs"]["day/2026-08-01/cn"]["version"] = "2"

        def m2(m):  # 原样重传: 值与版本都不变
            m["inputs"]["day/2026-08-05/cn"] = dict(
                m["inputs"]["day/2026-08-05/cn"])

        def m3(m):  # 仅版本变化(值相同), 指纹仍应改变
            m["inputs"]["day/2026-08-10/cn"]["version"] = "7"

        def m4(m):  # 滚动窗口另一端 + 跨周边界
            m["inputs"]["day/2026-08-09/cn"]["value"] = 90
            m["inputs"]["day/2026-08-09/cn"]["version"] = "3"

        def m5(m):  # 缺失分区补齐(us 周节点曾包含缺失键)
            m["inputs"]["day/2026-08-09/us"] = {"value": 99, "version": "1"}

        def m6(m):  # 跨时区: 同日期另一地区划分变化, 只影响 us/tz 链路
            m["inputs"]["day/2026-08-08/us"]["value"] = 800
            m["inputs"]["day/2026-08-08/us"]["version"] = "2"

        previous = self.base
        current = copy.deepcopy(self.base)
        for idx, mutate in enumerate((m1, m2, m3, m4, m5, m6), start=1):
            mutate(current)
            write_manifest(self.work, current)
            expected_set = independent_affected_set(previous, current)
            code, out = run_cli(root, "build", str(manifest_path), expect=0)

            # 1) 实际执行集合 == 独立依赖枚举器
            self.assertEqual(sorted(out["executed"]), sorted(expected_set),
                             "改动 %d 执行集合不符" % idx)
            # 2) 逐分区与独立全量重建比较值和指纹
            ref_v, ref_fp = reference_full_rebuild(
                current["inputs"], current["nodes"])
            self.assertEqual(out["values"], ref_v,
                             "改动 %d values 与全量重建不一致" % idx)
            self.assertEqual(out["fingerprints"], ref_fp,
                             "改动 %d fingerprints 与全量重建不一致" % idx)
            # 3) inspect 已发布版本同样逐分区一致
            _, insp = run_cli(root, "inspect", expect=0)
            self.assertEqual(insp["values"], ref_v)
            self.assertEqual(insp["fingerprints"], ref_fp)
            # 4) 触发路径: 每个执行分区至少一条路径, 路径终点是自身,
            #    且起点属于本次触发根
            for key in out["executed"]:
                paths = out["trigger_paths"][key]
                self.assertTrue(paths)
                for path in paths:
                    self.assertEqual(path[-1], key)
                    self.assertIn(path[0], out["trigger_roots"])
            previous = copy.deepcopy(current)

        # 显式校验改动 2(原样重传)没有触发任何重算
        # 通过重放: 重新构建 m2 场景
        replay = copy.deepcopy(self.base)
        m1(replay)
        before_m2 = copy.deepcopy(replay)
        m2(replay)
        r2_root = self.work / "replay"
        p2 = write_manifest(self.work, before_m2, "m_before.json")
        run_cli(r2_root, "build", str(p2), expect=0)
        p2b = write_manifest(self.work, replay, "m_after.json")
        _, out2 = run_cli(r2_root, "build", str(p2b), expect=0)
        self.assertEqual(out2["executed"], [])
        self.assertEqual(independent_affected_set(before_m2, replay), set())

    def test_determinism_and_layers(self):
        """同样输入两次独立构建, 计划/指纹/分层完全确定。"""
        manifest_path = write_manifest(self.work, self.base)
        _, a = run_cli(self.work / "det1", "build", str(manifest_path),
                       expect=0)
        _, b = run_cli(self.work / "det2", "build", str(manifest_path),
                       expect=0)
        for key in ("values", "fingerprints", "layers", "executed",
                    "trigger_roots", "trigger_paths"):
            self.assertEqual(a[key], b[key])
        # 分层: roll/week/tz 在第 1 层, summary 在第 2 层
        self.assertEqual(len(a["layers"]), 2)
        self.assertIn("summary/tz", a["layers"][1])
        self.assertIn("week/2026-W32/cn", a["layers"][0])

    # ------------------------------------------------------------------
    # 验收 2: 中断、恢复、陈旧拒绝、指纹匹配、部分失败、循环
    # ------------------------------------------------------------------
    def test_crash_resume_keeps_old_result_readable_and_reuses_staged(self):
        root = self.work / "acc2"
        manifest_path = write_manifest(self.work, self.base)
        run_cli(root, "build", str(manifest_path), expect=0)
        old_inspect = run_cli(root, "inspect", expect=0)[1]

        # 在两个依赖层之间(第 1 层结束后)注入中断
        changed = copy.deepcopy(self.base)
        changed["inputs"]["day/2026-08-01/cn"]["value"] = 100
        changed["inputs"]["day/2026-08-01/cn"]["version"] = "9"
        write_manifest(self.work, changed)
        code, crash_out = run_cli(
            root, "build", str(manifest_path), "--crash-after-layer", "1",
            expect=bf.EXIT_CRASH)
        self.assertEqual(crash_out["status"], "interrupted")
        self.assertIn("roll3/2026-08-03/cn", crash_out["staged_keys"])

        # 恢复前旧发布结果仍可读, 且未被中断污染
        readable = run_cli(root, "inspect", expect=0)[1]
        self.assertEqual(readable, old_inspect)

        # 恢复: 第 1 层有效暂存被复用, 仅执行受影响的第二层节点
        _, resumed = run_cli(root, "build", str(manifest_path), "--resume",
                             expect=0)
        self.assertIn("roll3/2026-08-03/cn", resumed["reused"])
        self.assertNotIn("roll3/2026-08-03/cn", resumed["executed"])
        ref_v, ref_fp = reference_full_rebuild(
            changed["inputs"], changed["nodes"])
        self.assertEqual(resumed["values"], ref_v)
        self.assertEqual(resumed["fingerprints"], ref_fp)

        # 最终发布: 每个分区的依赖指纹与对应数据版本全量重算结果匹配
        meta = json.loads((root / "published.meta.json").read_text("utf-8"))
        published = json.loads((root / "published.json").read_text("utf-8"))
        for key, node_fp in published["fingerprints"].items():
            self.assertEqual(node_fp, ref_fp[key], key)
        for key, src in meta["sources"].items():
            expect_fp = bf.source_fingerprint(key, changed["inputs"][key])
            self.assertEqual(src["fingerprint"], expect_fp)

    def test_stale_manifest_mid_run_rejects_publish(self):
        """运行中(层之间)输入变化: 拒绝发布陈旧结果, 旧版本保持可读。"""
        root = self.work / "stale"
        manifest_path = write_manifest(self.work, self.base)
        run_cli(root, "build", str(manifest_path), expect=0)
        old = run_cli(root, "inspect", expect=0)[1]

        # 子进程在第 2 层开始前改写清单, assert_fresh 必须拒绝继续
        env = dict(os.environ,
                   BACKFILL_MUTATE_AT_LAYER="2",
                   BACKFILL_MUTATE_SOURCE="day/2026-08-08/cn",
                   BACKFILL_MUTATE_VALUE="777",
                   BACKFILL_MUTATE_VERSION="stale")
        code, out = run_cli(root, "build", str(manifest_path),
                            env=env, expect=bf.EXIT_STALE)
        self.assertEqual(out["status"], "rejected")

        # 已发布旧结果仍可读且未被替换
        self.assertEqual(run_cli(root, "inspect", expect=0)[1], old)
        # 进度保留, 可从中断处恢复(清单已是新内容, 失效暂存重算)
        _, recovered = run_cli(root, "build", str(manifest_path),
                               "--resume", expect=0)
        final_doc = json.loads(manifest_path.read_text("utf-8"))
        ref_v, ref_fp = reference_full_rebuild(
            final_doc["inputs"], final_doc["nodes"])
        self.assertEqual(recovered["values"], ref_v)
        self.assertEqual(recovered["fingerprints"], ref_fp)

    def test_stale_staged_entries_are_recomputed_on_resume(self):
        """恢复前更换清单: 仍有效的暂存复用, 失效暂存必须重算。"""
        root = self.work / "invalidate"
        manifest_path = write_manifest(self.work, self.base)
        run_cli(root, "build", str(manifest_path),
                "--crash-after-layer", "1", expect=bf.EXIT_CRASH)
        staged_after_crash = json.loads(
            (root / "staging" / "progress.json").read_text("utf-8"))["staged"]
        self.assertTrue(staged_after_crash)

        # 恢复前改变一个第 1 层节点的输入, 该暂存失效; 无关暂存保留
        changed = copy.deepcopy(self.base)
        changed["inputs"]["day/2026-08-02/cn"]["value"] = 22
        changed["inputs"]["day/2026-08-02/cn"]["version"] = "55"
        write_manifest(self.work, changed)
        _, out = run_cli(root, "build", str(manifest_path), "--resume",
                         expect=0)
        self.assertIn("roll3/2026-08-03/cn", out["executed"])
        self.assertIn("roll3/2026-08-04/cn", out["executed"])
        # 未受影响的第 1 层暂存被复用
        unaffected = [k for k in staged_after_crash
                      if k not in ("roll3/2026-08-03/cn",
                                   "roll3/2026-08-04/cn")]
        for key in unaffected:
            self.assertIn(key, out["reused"])
        ref_v, ref_fp = reference_full_rebuild(
            changed["inputs"], changed["nodes"])
        self.assertEqual(out["values"], ref_v)
        self.assertEqual(out["fingerprints"], ref_fp)

    def test_partial_failure_never_publishes_failed_partition(self):
        """节点失败: 退出 99, 失败分区不会成为最新版本, 之后可恢复。"""
        root = self.work / "partial"
        manifest_path = write_manifest(self.work, self.base)
        run_cli(root, "build", str(manifest_path), expect=0)
        old = run_cli(root, "inspect", expect=0)[1]

        changed = copy.deepcopy(self.base)
        changed["inputs"]["day/2026-08-05/cn"]["value"] = 50
        changed["inputs"]["day/2026-08-05/cn"]["version"] = "8"
        write_manifest(self.work, changed)
        env = dict(os.environ, BACKFILL_FAIL_NODE="week/2026-W32/cn")
        code, out = run_cli(root, "build", str(manifest_path),
                            env=env, expect=bf.EXIT_NODE_FAILED)
        self.assertEqual(out["status"], "partial-failure")
        # 最新发布仍是旧全图, 失败分区未被标成最新
        self.assertEqual(run_cli(root, "inspect", expect=0)[1], old)

        # 清除故障后恢复成功
        env_clean = dict(os.environ)
        env_clean.pop("BACKFILL_FAIL_NODE", None)
        _, recovered = run_cli(root, "build", str(manifest_path),
                               "--resume", env=env_clean, expect=0)
        ref_v, ref_fp = reference_full_rebuild(
            changed["inputs"], changed["nodes"])
        self.assertEqual(recovered["values"], ref_v)
        self.assertEqual(recovered["fingerprints"], ref_fp)

    def test_cycle_is_rejected(self):
        cyc = {"inputs": {"a": {"value": 1, "version": "1"}},
               "nodes": {"x": ["y", "a"], "y": ["x"]}}
        path = write_manifest(self.work, cyc, "cycle.json")
        code, out = run_cli(self.work / "cyc", "build", path,
                            expect=bf.EXIT_CYCLE)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("循环", out["reason"])
        self.assertFalse((self.work / "cyc" / "published.json").exists())

    def test_resume_without_progress_fails(self):
        path = write_manifest(self.work, self.base)
        code, _ = run_cli(self.work / "noprog", "build", path, "--resume",
                          expect=1)

    def test_demo_json_missing_partition(self):
        """README 自带 demo: 缺失键贡献 0, 指纹独立。"""
        path = HERE / "demo.json"
        _, out = run_cli(self.work / "demo", "build", path, expect=0)
        self.assertEqual(out["values"], {"week": 12, "report": 20})
        missing_fp = bf.missing_fingerprint("d2")
        # week 指纹中包含缺失分区指纹
        week_canon = ref_canon(["node", "week", [
            bf.source_fingerprint("d0", {"value": 4, "version": "1"}),
            bf.source_fingerprint("d1", {"value": 8, "version": "1"}),
            missing_fp]])
        self.assertEqual(out["fingerprints"]["week"],
                         hashlib.sha256(week_canon).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
