"""publish.py 端到端验收测试。

运行：python3 test_acceptance.py
覆盖 README 协议与题目验收点：
1. 首发三天、回填两天；未变分区保留来源，所有分区哈希/行数可复算；
   坏行、行日期不符、哈希错均退出 2 且不改变现有清单。
2. 暂存后中断（75）、移走原始文件后重试只产生一个新版本；
   两批相同期望版本并发提交恰有一批成功；成功批次重放结果不变。
3. 同 batch_id 不同身份冲突（3）；期望版本过时冲突（3）；首发前无 CURRENT。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLISH = ROOT / "publish.py"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")
    return sha256_bytes(text.encode("utf-8")), len(rows)


def run_publish(request_path, warehouse):
    proc = subprocess.run(
        [sys.executable, str(PUBLISH), str(request_path), str(warehouse)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode(), proc.stderr.decode()


def write_request(directory, batch_id, expected_version, specs,
                  stop_after_stage=False, name="request.json"):
    req = {
        "batch_id": batch_id,
        "expected_version": expected_version,
        "stop_after_stage": stop_after_stage,
        "partitions": [
            {"day": day, "path": rel, "sha256": digest, "rows": rows}
            for day, rel, digest, rows in specs
        ],
    }
    p = directory / name
    p.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    return p


def load_current(warehouse):
    return json.loads((warehouse / "CURRENT.json").read_text(encoding="utf-8"))


def load_manifest(warehouse):
    pointer = load_current(warehouse)
    mp = warehouse / pointer["manifest"]
    return pointer, json.loads(mp.read_text(encoding="utf-8"))


def verify_manifest(warehouse, manifest):
    """复算清单中每个分区的行数与 SHA-256，并检查来源关系字段。"""
    for day, entry in manifest["partitions"].items():
        p = warehouse / entry["path"]
        assert p.is_file(), "清单引用的仓库内副本不存在: " + entry["path"]
        data = p.read_bytes()
        assert sha256_bytes(data) == entry["sha256"], "哈希不可复算: " + day
        assert len(data.decode("utf-8").splitlines()) == entry["rows"], \
            "行数不可复算: " + day
        for line in data.decode("utf-8").splitlines():
            assert json.loads(line)["day"] == day
        assert isinstance(entry["batch_id"], str) and entry["batch_id"]


class PublishAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pubtest-"))
        self.reqdir = self.tmp / "requests"
        self.reqdir.mkdir()
        self.warehouse = self.tmp / "warehouse"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_day(self, day, values, subdir="data"):
        """在请求目录下生成一天的 JSONL，返回 (相对路径, sha256, rows)。"""
        rel = Path(subdir) / (day + ".jsonl")
        digest, rows = write_jsonl(
            self.reqdir / rel,
            [{"day": day, "id": i, "v": v} for i, v in enumerate(values)])
        return day, rel.as_posix(), digest, rows

    def test_01_initial_publish_and_backfill_keeps_untouched(self):
        d1 = self.make_day("2026-01-01", [10, 11, 12])
        d2 = self.make_day("2026-01-02", [20, 21])
        d3 = self.make_day("2026-01-03", [30])

        # 首批之前无 CURRENT
        self.assertFalse((self.warehouse / "CURRENT.json").exists())

        req = write_request(self.reqdir, "batch-init", 0, [d1, d2, d3])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)

        pointer, manifest = load_manifest(self.warehouse)
        self.assertEqual(pointer["version"], 1)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["batch_id"], "batch-init")
        self.assertEqual(sorted(manifest["partitions"]),
                         ["2026-01-01", "2026-01-02", "2026-01-03"])
        verify_manifest(self.warehouse, manifest)
        # 清单路径全部指向仓库内副本，不依赖原始输入
        for entry in manifest["partitions"].values():
            self.assertTrue(entry["path"].startswith("blobs/"))

        # 回填其中两天（新内容、新哈希）
        b1 = self.make_day("2026-01-01", [110, 111, 112], subdir="backfill")
        b2 = self.make_day("2026-01-02", [120, 121, 122], subdir="backfill")
        req2 = write_request(self.reqdir, "batch-backfill", 1, [b1, b2])
        code, _, err = run_publish(req2, self.warehouse)
        self.assertEqual(code, 0, err)

        pointer2, manifest2 = load_manifest(self.warehouse)
        self.assertEqual(pointer2["version"], 2)
        self.assertEqual(manifest2["version"], 2)
        verify_manifest(self.warehouse, manifest2)

        # 未变分区保留原来源（batch_id 与旧 blob）
        untouched = manifest2["partitions"]["2026-01-03"]
        self.assertEqual(untouched["batch_id"], "batch-init")
        self.assertEqual(untouched, manifest["partitions"]["2026-01-03"])
        # 回填分区来源更新
        self.assertEqual(manifest2["partitions"]["2026-01-01"]["batch_id"],
                         "batch-backfill")
        self.assertEqual(manifest2["partitions"]["2026-01-02"]["batch_id"],
                         "batch-backfill")
        # 旧清单不可变、消费者仍可读取
        old_mp = warehouse_manifest_path(self.warehouse, 1)
        self.assertTrue(old_mp.is_file())
        old_manifest = json.loads(old_mp.read_text(encoding="utf-8"))
        verify_manifest(self.warehouse, old_manifest)
        self.assertEqual(len(list((self.warehouse / "manifests").glob("*.json"))),
                         2)

    def test_02_bad_rows_day_and_hash_do_not_change_manifest(self):
        d1 = self.make_day("2026-01-01", [1, 2])
        d2 = self.make_day("2026-01-02", [3])
        req = write_request(self.reqdir, "batch-init", 0, [d1, d2])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)
        before = (self.warehouse / "CURRENT.json").read_bytes()
        manifests_before = sorted(p.name for p in
                                  (self.warehouse / "manifests").glob("*.json"))

        # 坏行：非 JSON
        bad_dir = self.reqdir / "bad"
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_file = bad_dir / "2026-01-01.jsonl"
        bad_file.write_text('{"day": "2026-01-01", "id": 0}\nnot-json\n',
                            encoding="utf-8")
        digest = sha256_bytes(bad_file.read_bytes())
        req_bad = write_request(
            self.reqdir, "batch-bad-row", 1,
            [("2026-01-01", "bad/2026-01-01.jsonl", digest, 2)])
        code, _, err = run_publish(req_bad, self.warehouse)
        self.assertEqual(code, 2, err)
        self.assertIn("JSON", err)

        # 行内日期不符
        wrong_day_file = bad_dir / "2026-01-02.jsonl"
        wrong_day_file.write_text(
            '{"day": "2026-01-09", "id": 0}\n', encoding="utf-8")
        digest = sha256_bytes(wrong_day_file.read_bytes())
        req_wrong = write_request(
            self.reqdir, "batch-bad-day", 1,
            [("2026-01-02", "bad/2026-01-02.jsonl", digest, 1)])
        code, _, err = run_publish(req_wrong, self.warehouse)
        self.assertEqual(code, 2, err)
        self.assertIn("day", err)

        # 哈希不符：请求里故意写错摘要
        good = self.make_day("2026-01-03", [7], subdir="good")
        day, rel, _, rows = good
        req_hash = write_request(
            self.reqdir, "batch-bad-hash", 1,
            [(day, rel, "0" * 64, rows)])
        code, _, err = run_publish(req_hash, self.warehouse)
        self.assertEqual(code, 2, err)
        self.assertIn("SHA-256", err)

        # 行数不符：请求里声明错误行数
        req_rows = write_request(
            self.reqdir, "batch-bad-rows", 1,
            [(day, rel, good[2], rows + 5)])
        code, _, err = run_publish(req_rows, self.warehouse)
        self.assertEqual(code, 2, err)
        self.assertIn("行数", err)

        # 现有清单与指针完全未变，且没有产生新清单/新暂存影响后续发布
        self.assertEqual((self.warehouse / "CURRENT.json").read_bytes(), before)
        manifests_after = sorted(p.name for p in
                                 (self.warehouse / "manifests").glob("*.json"))
        self.assertEqual(manifests_after, manifests_before)

        # 失败后一次合法回填仍可正常提交
        ok = self.make_day("2026-01-01", [900], subdir="fix")
        req_ok = write_request(self.reqdir, "batch-fix", 1, [ok])
        code, _, err = run_publish(req_ok, self.warehouse)
        self.assertEqual(code, 0, err)
        pointer, manifest = load_manifest(self.warehouse)
        self.assertEqual(pointer["version"], 2)
        verify_manifest(self.warehouse, manifest)

    def test_03_stage_interrupt_move_sources_then_resume(self):
        d1 = self.make_day("2026-01-01", [1, 2])
        d2 = self.make_day("2026-01-02", [3, 4, 5])
        req = write_request(self.reqdir, "batch-init", 0, [d1, d2])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)

        b1 = self.make_day("2026-01-01", [10, 20], subdir="bf")
        b2 = self.make_day("2026-01-02", [30], subdir="bf")
        # 暂存后主动中断
        req_stage = write_request(self.reqdir, "batch-bf", 1, [b1, b2],
                                  stop_after_stage=True)
        code, out, err = run_publish(req_stage, self.warehouse)
        self.assertEqual(code, 75, err)
        self.assertFalse((self.warehouse / "CURRENT.json").read_text(
            encoding="utf-8").count('"version": 2'))
        pointer = load_current(self.warehouse)
        self.assertEqual(pointer["version"], 1)  # 指针未变
        # 暂存副本与元数据已落仓库（每个批次一个暂存目录）
        stage_files = list((self.warehouse / "staging").rglob("stage.json"))
        self.assertEqual(len(stage_files), 2)
        import publish as publish_module
        bf_stage = json.loads(publish_module.stage_path(
            self.warehouse, "batch-bf").read_text(encoding="utf-8"))
        self.assertFalse(bf_stage["published"])
        blobs = list((self.warehouse / "blobs").glob("*"))
        self.assertGreaterEqual(len(blobs), 4)

        # 移走原始分区文件
        moved = self.tmp / "moved-away"
        shutil.move(str(self.reqdir / "bf"), str(moved))
        self.assertFalse((self.reqdir / "bf").exists())

        # 同一身份请求（不含 stop 开关），仅凭仓库暂存完成发布
        req_resume = write_request(self.reqdir, "batch-bf", 1, [b1, b2],
                                   name="resume.json")
        code, _, err = run_publish(req_resume, self.warehouse)
        self.assertEqual(code, 0, err)

        pointer2, manifest2 = load_manifest(self.warehouse)
        self.assertEqual(pointer2["version"], 2)
        verify_manifest(self.warehouse, manifest2)
        # 只生成一个新版本
        self.assertEqual(
            sorted(p.name for p in
                   (self.warehouse / "manifests").glob("*.json")),
            ["000001.json", "000002.json"])

        # 再重放：无新版本、CURRENT 不变
        before = (self.warehouse / "CURRENT.json").read_bytes()
        req_replay = write_request(self.reqdir, "batch-bf", 1, [b1, b2],
                                   name="replay.json")
        code, out, err = run_publish(req_replay, self.warehouse)
        self.assertEqual(code, 0, err)
        self.assertEqual((self.warehouse / "CURRENT.json").read_bytes(), before)
        self.assertEqual(
            sorted(p.name for p in
                   (self.warehouse / "manifests").glob("*.json")),
            ["000001.json", "000002.json"])

    def test_04_concurrent_publish_only_one_wins(self):
        d1 = self.make_day("2026-01-01", [1])
        d2 = self.make_day("2026-01-02", [2])
        req = write_request(self.reqdir, "batch-init", 0, [d1, d2])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)

        # 两批不同 batch_id、相同 expected_version=1，并发提交
        a1 = self.make_day("2026-01-01", [100], subdir="a")
        b1 = self.make_day("2026-01-01", [200], subdir="b")
        req_a = write_request(self.reqdir, "batch-concurrent-a", 1, [a1],
                              name="a.json")
        req_b = write_request(self.reqdir, "batch-concurrent-b", 1, [b1],
                              name="b.json")

        env = dict(os.environ)
        procs = [
            subprocess.Popen([sys.executable, str(PUBLISH), str(req_a),
                              str(self.warehouse)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env),
            subprocess.Popen([sys.executable, str(PUBLISH), str(req_b),
                              str(self.warehouse)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env),
        ]
        results = [p.wait() for p in procs]
        stderr_text = [p.stderr.read().decode() for p in procs]
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(sorted(results), [0, 3], stderr_text)

        pointer, manifest = load_manifest(self.warehouse)
        self.assertEqual(pointer["version"], 2)  # 只有一个新版本
        winner = manifest["batch_id"]
        self.assertIn(winner, ("batch-concurrent-a", "batch-concurrent-b"))
        loser = ("batch-concurrent-b" if winner == "batch-concurrent-a"
                 else "batch-concurrent-a")
        verify_manifest(self.warehouse, manifest)
        # 未被更新的分区保留
        self.assertEqual(manifest["partitions"]["2026-01-02"]["batch_id"],
                         "batch-init")

        # 失败方以更新后的期望版本重提应成功（其分区未被更新）。
        # 同一 batch_id 换新 expected_version 属于不同身份（协议要求冲突），
        # 因此真实重试使用新的 batch_id。
        loser_spec = b1 if loser == "batch-concurrent-b" else a1
        retry_id = loser + "-retry"
        req_retry = write_request(self.reqdir, retry_id, 2, [loser_spec],
                                  name="loser-retry.json")
        code, _, err = run_publish(req_retry, self.warehouse)
        self.assertEqual(code, 0, err)
        pointer2, manifest2 = load_manifest(self.warehouse)
        self.assertEqual(pointer2["version"], 3)
        self.assertEqual(manifest2["partitions"]["2026-01-01"]["batch_id"],
                         retry_id)
        verify_manifest(self.warehouse, manifest2)

        # 失败批次原 batch_id 以旧期望版本重放仍为冲突（身份未发布过且已过时）
        req_old = write_request(self.reqdir, loser, 1, [loser_spec],
                                name="loser-old-replay.json")
        code, _, err = run_publish(req_old, self.warehouse)
        self.assertEqual(code, 3, err)
        self.assertEqual(load_current(self.warehouse)["version"], 3)

        # 成功批次重放：结果不变（即使已有更高版本）
        before = (self.warehouse / "CURRENT.json").read_bytes()
        winner_spec = a1 if winner == "batch-concurrent-a" else b1
        req_wr = write_request(self.reqdir, winner, 1, [winner_spec],
                               name="winner-replay.json")
        code, _, err = run_publish(req_wr, self.warehouse)
        self.assertEqual(code, 0, err)
        self.assertEqual((self.warehouse / "CURRENT.json").read_bytes(), before)

    def test_05_same_batch_different_identity_and_stale_version_conflict(self):
        d1 = self.make_day("2026-01-01", [1, 2])
        req = write_request(self.reqdir, "batch-x", 0, [d1])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)

        # 同 batch_id 不同内容（不同 sha256）→ 冲突
        c1 = self.make_day("2026-01-01", [9], subdir="changed")
        req_diff = write_request(self.reqdir, "batch-x", 1, [c1])
        code, _, err = run_publish(req_diff, self.warehouse)
        self.assertEqual(code, 3, err)

        # 同 batch_id 不同 expected_version → 身份不同 → 冲突
        req_ver = write_request(self.reqdir, "batch-x", 1, [d1])
        code, _, err = run_publish(req_ver, self.warehouse)
        self.assertEqual(code, 3, err)

        # 新批次期望版本过时 → 冲突
        req_stale = write_request(self.reqdir, "batch-y", 0, [d1])
        code, _, err = run_publish(req_stale, self.warehouse)
        self.assertEqual(code, 3, err)
        self.assertIn("过时", err)

        # 指针仍是版本 1
        self.assertEqual(load_current(self.warehouse)["version"], 1)

    def test_06_stage_then_conflicting_identity_rejected(self):
        d1 = self.make_day("2026-01-01", [1])
        req = write_request(self.reqdir, "batch-init", 0, [d1])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)

        s1 = self.make_day("2026-01-02", [2, 3], subdir="s")
        req_stage = write_request(self.reqdir, "batch-s", 1, [s1],
                                  stop_after_stage=True)
        code, _, err = run_publish(req_stage, self.warehouse)
        self.assertEqual(code, 75, err)

        # 同 batch_id 换内容 → 冲突，不能覆盖已有暂存
        s2 = self.make_day("2026-01-02", [4], subdir="s2")
        req_conflict = write_request(self.reqdir, "batch-s", 1, [s2],
                                     name="conflict.json")
        code, _, err = run_publish(req_conflict, self.warehouse)
        self.assertEqual(code, 3, err)

        # 原身份仍可恢复发布
        req_resume = write_request(self.reqdir, "batch-s", 1, [s1],
                                   name="resume.json")
        code, _, err = run_publish(req_resume, self.warehouse)
        self.assertEqual(code, 0, err)
        pointer, manifest = load_manifest(self.warehouse)
        self.assertEqual(pointer["version"], 2)
        self.assertEqual(manifest["partitions"]["2026-01-02"]["rows"], 2)

    def test_07_request_validation_errors(self):
        d1 = self.make_day("2026-01-01", [1])
        day, rel, digest, rows = d1

        bad_cases = [
            {"expected_version": 0, "partitions": [
                {"day": day, "path": rel, "sha256": digest, "rows": rows}]},
            {"batch_id": "", "expected_version": 0, "partitions": [
                {"day": day, "path": rel, "sha256": digest, "rows": rows}]},
            {"batch_id": "b", "expected_version": -1, "partitions": [
                {"day": day, "path": rel, "sha256": digest, "rows": rows}]},
            {"batch_id": "b", "expected_version": 0, "partitions": []},
            {"batch_id": "b", "expected_version": 0, "partitions": [
                {"day": "2026-13-40", "path": rel,
                 "sha256": digest, "rows": rows}]},
            {"batch_id": "b", "expected_version": 0, "partitions": [
                {"day": day, "path": rel, "sha256": digest, "rows": rows},
                {"day": day, "path": rel, "sha256": digest, "rows": rows}]},
            {"batch_id": "b", "expected_version": 0, "partitions": [
                {"day": day, "path": rel, "sha256": "ABC", "rows": rows}]},
        ]
        for i, payload in enumerate(bad_cases):
            p = self.reqdir / ("bad-%d.json" % i)
            p.write_text(json.dumps(payload), encoding="utf-8")
            code, _, err = run_publish(p, self.warehouse)
            self.assertEqual(code, 2, "case %d: %s" % (i, err))
        self.assertFalse((self.warehouse / "CURRENT.json").exists())

    def test_08_empty_file_zero_rows_roundtrip(self):
        rel = Path("data") / "2026-02-01.jsonl"
        target = self.reqdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        digest = sha256_bytes(b"")
        req = write_request(self.reqdir, "batch-empty", 0,
                            [("2026-02-01", rel.as_posix(), digest, 0)])
        code, _, err = run_publish(req, self.warehouse)
        self.assertEqual(code, 0, err)
        _, manifest = load_manifest(self.warehouse)
        verify_manifest(self.warehouse, manifest)
        self.assertEqual(manifest["partitions"]["2026-02-01"]["rows"], 0)


def warehouse_manifest_path(warehouse, version):
    return warehouse / "manifests" / ("%06d.json" % version)


if __name__ == "__main__":
    unittest.main(verbosity=2)