#!/usr/bin/env python3
"""dvr.py 的一致性/崩溃恢复验收测试（unittest，无第三方依赖）。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import dvr  # noqa: E402


def build_request(**overrides):
    req = {
        "segs": 60,
        "window": 8,
        "crash_at": 25,
        "readers": 3,
        "speed": 2,
        "stress": False,
    }
    req.update(overrides)
    return req


class DvrRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dvr-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_req(self, **overrides):
        req = build_request(state_dir=os.path.join(self.tmp, "run"))
        req.update(overrides)
        return dvr.run(req)

    # -- 验收 1 ---------------------------------------------------------
    def test_three_crash_points_match_baseline(self):
        """片段写完后/清单更新前/更新后崩溃：清单完整且与基线一致。"""
        base = self.run_req(crash_at=None)
        self.assertFalse(base["recovered"])
        expected = [item["seq"] for item in base["manifest"]]
        self.assertEqual(expected, list(range(52, 60)))
        self.assertEqual(base["dangling"], 0)

        for phase in ("after_segment", "before_manifest", "after_manifest"):
            with self.subTest(phase=phase):
                res = self.run_req(crash_phase=phase)
                self.assertTrue(res["recovered"], phase)
                self.assertEqual(
                    [item["seq"] for item in res["manifest"]], expected
                )
                self.assertTrue(all(item["visible"] for item in res["manifest"]))
                self.assertEqual(res["dangling"], 0)

    def test_all_crash_phases_in_one_request(self):
        """不指定 crash_phase 时逐项跑完全部崩溃点，任一项偏离基线即失败。"""
        res = self.run_req(crash_phases=list(dvr.PHASES))
        self.assertEqual(
            [item["seq"] for item in res["manifest"]], list(range(52, 60))
        )
        self.assertEqual(res["dangling"], 0)

    def test_manifest_intact_at_every_crash_moment(self):
        """任一崩溃时刻磁盘清单都完整：提交前是旧代窗口，提交后含新片段。"""
        root = os.path.join(self.tmp, "moment")
        at = 20
        window = 4
        for phase in dvr.PHASES:
            with self.subTest(phase=phase):
                shutil.rmtree(root, ignore_errors=True)
                buf = dvr.DvrBuffer.open_fresh(root, window, window, reset=True)
                buf.crash = dvr.CrashInjector(at, phase)
                with self.assertRaises(dvr.SimulatedCrash):
                    for seq in range(40):
                        buf.append(seq)
                buf._closed = True

                manifest, dangling = dvr._checked_manifest(root)
                seqs = [item["seq"] for item in manifest]
                self.assertEqual(dangling, 0, "dangling at %s" % phase)
                self.assertTrue(all(item["visible"] for item in manifest))
                self.assertEqual(seqs, list(range(seqs[0], seqs[0] + window)))

                if phase in ("after_segment", "before_manifest", "after_state"):
                    # 提交前 / 清单未刷新：磁盘清单仍是上一代，不含 seq=at，
                    # 但它引用的片段全部仍在磁盘上且可读。
                    self.assertNotIn(at, seqs)
                    self.assertEqual(seqs[-1], at - 1)
                else:
                    # 清单提交后：新片段已进入清单并即刻可读。
                    self.assertEqual(seqs[-1], at)

                recovered = dvr.DvrBuffer.open_recover(root, window, window)
                self.assertTrue(recovered.recovered)
                committed = [e.seq for e in recovered.committed_entries]
                self.assertEqual(len(committed), window)
                if phase in ("after_segment", "before_manifest"):
                    # 状态未提交：该片段被当作孤立残留丢弃，写者从 at 重放。
                    self.assertEqual(recovered.next_seq, at)
                    self.assertEqual(committed[-1], at - 1)
                else:
                    # 状态已提交（清单可能只是滞后）：窗口含 at。
                    self.assertEqual(recovered.next_seq, at + 1)
                    self.assertEqual(committed[-1], at)
                recovered.close()

                # 恢复后目录里只剩清单引用的片段，无 tmp/孤立残留。
                names = sorted(os.listdir(os.path.join(root, "segments")))
                self.assertEqual(
                    names,
                    ["seg-%08d.bin" % s for s in range(committed[0], committed[-1] + 1)],
                )

    def test_partial_file_residue_is_ignored(self):
        """部分写入的片段残留不会进入清单，也不影响恢复后的窗口。"""
        root = os.path.join(self.tmp, "partial")
        buf = dvr.DvrBuffer.open_fresh(root, 4, 4, reset=True)
        for seq in range(6):
            buf.append(seq)
        buf._closed = True
        # 伪造中断写入：半截片段、半截状态文件、半截清单文件。
        seg = os.path.join(root, "segments")
        for name, payload in (
            ("seg-00000006.bin", dvr.MAGIC + b"half"),
            ("seg-00000007.bin.tmp", dvr.MAGIC + b"half"),
        ):
            with open(os.path.join(seg, name), "wb") as fh:
                fh.write(payload)
        for name in ("state.json.tmp.1.2", "manifest.json.tmp.1.2"):
            with open(os.path.join(root, name), "wb") as fh:
                fh.write(b"half-written-json")

        recovered = dvr.DvrBuffer.open_recover(root, 4, 4)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [2, 3, 4, 5]
        )
        self.assertEqual(recovered.next_seq, 6)
        recovered.append(6)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [3, 4, 5, 6]
        )
        recovered.close()

    def test_corrupt_state_file_recovers_from_confirmed_segments(self):
        """state.json 损坏时回退到磁盘上完整片段，不丢已确认窗口。"""
        root = os.path.join(self.tmp, "corrupt")
        buf = dvr.DvrBuffer.open_fresh(root, 4, 4, reset=True)
        for seq in range(10):
            buf.append(seq)
        buf._closed = True
        with open(os.path.join(root, "state.json"), "wb") as fh:
            fh.write(b"not json at all")

        recovered = dvr.DvrBuffer.open_recover(root, 4, 4)
        self.assertTrue(recovered.recovered)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [6, 7, 8, 9]
        )
        recovered.close()

    def test_missing_state_file_recovers_from_confirmed_segments(self):
        """state.json 丢失时用完整片段重建窗口，不会把已确认片段当孤儿删掉。"""
        root = os.path.join(self.tmp, "nostate")
        buf = dvr.DvrBuffer.open_fresh(root, 4, 4, reset=True)
        for seq in range(12):
            buf.append(seq)
        buf._closed = True
        os.unlink(os.path.join(root, "state.json"))

        recovered = dvr.DvrBuffer.open_recover(root, 4, 4)
        self.assertTrue(recovered.recovered)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [8, 9, 10, 11]
        )
        self.assertEqual(recovered.next_seq, 12)
        recovered.close()

    def test_corrupt_committed_segment_rolls_back_to_last_valid(self):
        """已提交片段损坏（磁盘损坏/截断）时回滚到最后一个有效条目并重放。"""
        root = os.path.join(self.tmp, "corruptseg")
        buf = dvr.DvrBuffer.open_fresh(root, 4, 4, reset=True)
        for seq in range(10):
            buf.append(seq)
        buf._closed = True
        # 截断窗口中间的片段（seq=8）：CRC/长度校验失败。
        with open(os.path.join(root, "segments", "seg-00000008.bin"), "wb") as fh:
            fh.write(dvr.MAGIC + b"truncated")

        recovered = dvr.DvrBuffer.open_recover(root, 4, 4)
        self.assertTrue(recovered.recovered)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [6, 7]
        )
        self.assertEqual(recovered.next_seq, 8)
        self.assertEqual(recovered.confirmed_seq, 7)
        # 写者从 8 重放后，窗口与未损坏基线一致。
        for seq in range(recovered.next_seq, 10):
            recovered.append(seq)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [6, 7, 8, 9]
        )
        manifest, dangling = dvr._checked_manifest(root)
        self.assertEqual(dangling, 0)
        self.assertEqual([item["seq"] for item in manifest], [6, 7, 8, 9])
        recovered.close()

    def test_window_change_on_restart_keeps_confirmed_seq(self):
        """重启时窗口变小：收缩窗口但保留已确认序号，滑出片段按孤儿清理。"""
        root = os.path.join(self.tmp, "windowchange")
        buf = dvr.DvrBuffer.open_fresh(root, 8, 8, reset=True)
        for seq in range(20):
            buf.append(seq)
        buf.close()

        recovered = dvr.DvrBuffer.open_recover(root, 3, 3)
        self.assertEqual([e.seq for e in recovered.committed_entries], [17, 18, 19])
        self.assertEqual(recovered.next_seq, 20)
        self.assertEqual(recovered.confirmed_seq, 19)
        self.assertEqual(len(os.listdir(recovered.segdir)), 3)
        recovered.append(20)
        self.assertEqual(
            [e.seq for e in recovered.committed_entries], [18, 19, 20]
        )
        recovered.close()

    def test_resume_after_clean_shutdown(self):
        """干净停机后带 resume 重启：窗口与已确认序号都延续，且无重放。"""
        base = self.run_req(crash_at=None)
        resumed = self.run_req(crash_at=None, resume=True)
        self.assertFalse(resumed["recovered"])  # 干净退出不应判定为恢复
        self.assertEqual(
            [item["seq"] for item in resumed["manifest"]],
            [item["seq"] for item in base["manifest"]],
        )
        self.assertEqual(resumed["dangling"], 0)

    def test_resume_continues_after_crash(self):
        """崩溃后从 state_dir 续写：接着已确认序号往后写，窗口与基线一致。"""
        root = os.path.join(self.tmp, "resume-crash")
        first = dvr.run(
            dict(segs=30, window=8, crash_at=20, readers=2, stress=False,
                 crash_phase="after_state", state_dir=root)
        )
        self.assertTrue(first["recovered"])
        self.assertEqual([item["seq"] for item in first["manifest"]],
                         list(range(22, 30)))

        second = dvr.run(
            dict(segs=60, window=8, readers=2, stress=False, resume=True,
                 state_dir=root)
        )
        self.assertFalse(second["recovered"])  # 上次已干净关闭
        self.assertEqual([item["seq"] for item in second["manifest"]],
                         list(range(52, 60)))
        self.assertEqual(second["dangling"], 0)

    def test_hard_process_kill_recovery_matches_baseline(self):
        """子进程在崩溃点 os._exit：父进程恢复后窗口仍与基线一致。"""
        base = self.run_req(crash_at=None)
        expected = [item["seq"] for item in base["manifest"]]
        for phase in dvr.PHASES:
            with self.subTest(phase=phase):
                res = self.run_req(
                    crash_phase=phase,
                    hard_crash=True,
                    state_dir=os.path.join(self.tmp, "hard-" + phase),
                )
                self.assertTrue(res["recovered"])
                self.assertEqual(
                    [item["seq"] for item in res["manifest"]], expected
                )
                self.assertEqual(res["dangling"], 0)

    # -- 验收 2 ---------------------------------------------------------
    def test_boundary_sliding_stress(self):
        """2 倍速滑动 200 个片段，3 读者在边界无失效引用且共享快照。"""
        reads, shared = dvr._boundary_stress(
            window=8, segs=200, readers=3, speed=2.0,
            base_delay=0.0005, grace=8,
        )
        self.assertGreater(reads, 0)
        self.assertGreaterEqual(shared, 1)

    def test_pinned_snapshot_survives_window_slide(self):
        """窗口滑走后旧快照仍可读；未被 pin 的旧片段延迟回收。"""
        root = os.path.join(self.tmp, "pin")
        buf = dvr.DvrBuffer.open_fresh(root, 4, 4, reset=True)
        for seq in range(4):
            buf.append(seq)
        snap = buf.snapshot()
        for seq in range(4, 20):
            buf.append(seq)
        self.assertEqual(snap.read_seqs(), [0, 1, 2, 3])
        snap.release()

        # grace 代之后旧文件被回收：窗口外最多再保留 grace 个已移除片段。
        remaining = sorted(
            int(name[4:-4])
            for name in os.listdir(buf.segdir)
            if name.endswith(".bin")
        )
        self.assertEqual(remaining[-4:], [16, 17, 18, 19])
        self.assertNotIn(0, remaining)
        self.assertNotIn(1, remaining)
        self.assertLessEqual(len(remaining), 4 + 4)
        self.assertEqual(remaining, list(range(min(remaining), 20)))
        buf.close()

    def test_reader_sees_consistent_snapshot_during_write(self):
        """写者并发滑动时，终态 3 读者快照一致且条目可读。"""
        res = self.run_req(crash_at=None, segs=80)
        snapshots = res["snapshots"]
        self.assertEqual(len(snapshots), 3)
        first = list(snapshots.values())[0]
        for seqs in snapshots.values():
            self.assertEqual(seqs, first)
        self.assertEqual(first, [item["seq"] for item in res["manifest"]])

    # -- CLI ------------------------------------------------------------
    def test_cli_output_schema(self):
        req_path = os.path.join(self.tmp, "req.json")
        out_path = os.path.join(self.tmp, "out.json")
        req = build_request()
        req["state_dir"] = os.path.join(self.tmp, "cli")
        with open(req_path, "w", encoding="utf-8") as fh:
            json.dump(req, fh)
        proc = subprocess.run(
            [sys.executable, "-B", os.path.join(ROOT, "dvr.py"), req_path, out_path],
            cwd=self.tmp, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out_path, encoding="utf-8") as fh:
            result = json.load(fh)
        self.assertEqual(
            set(result), {"manifest", "dangling", "recovered", "snapshots"}
        )
        self.assertEqual(result["dangling"], 0)
        self.assertTrue(result["recovered"])
        for item in result["manifest"]:
            self.assertEqual(set(item), {"seq", "visible"})
            self.assertTrue(item["visible"])
        self.assertEqual(len(result["snapshots"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
