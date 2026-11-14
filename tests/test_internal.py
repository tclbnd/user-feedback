#!/usr/bin/env python3
"""白盒测试：覆盖黑盒难以触发的路径（整体校验失败后的块级定位重取）。

    python3 tests/test_internal.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from make_fixture import gen  # noqa: E402


def load_dl():
    spec = importlib.util.spec_from_file_location("dlmod", os.path.join(ROOT, "dl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_whole_file_failure_localizes_and_refetches():
    """块级校验都通过，但整体校验失败 -> 按块定位 -> 隔离 -> 只重取坏块 -> 通过。"""
    dl = load_dl()
    tmp = tempfile.mkdtemp(prefix="int-")
    try:
        size, chunk = 160000, 20000
        data = gen(tmp, size, chunk, 2)
        req = os.path.join(tmp, "req.json")
        out = os.path.join(tmp, "out.bin")

        victim = [3]

        class Sabotaged(dl.Downloader):
            def _assemble_verify(self):
                if not getattr(self, "_sab", False):
                    self._sab = True
                    # 模拟「写盘错位 / 磁盘静默损坏」：所有块各自的 sha 都曾校验通过，
                    # 只有整体重组后才发现不对。
                    with open(self.part_path, "r+b") as f:
                        f.seek(victim[0] * chunk + 5)
                        f.write(b"\xde\xad\xbe\xef")
                return super()._assemble_verify()

        d = Sabotaged(req, out, {"quiet": True})
        code = d.run()
        assert code == 0, "exit code %r" % code
        with open(out, "rb") as f:
            assert f.read() == data, "final bytes"
        with open(out + ".meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        assert any("whole-file sha mismatch" in a for a in meta["alerts"]), meta["alerts"]
        reasons = {c["i"]: c["reasons"] for c in meta["chunks"]}
        assert any("whole-file verification failed" in r for r in reasons[victim[0]]), reasons
        retried = sorted(c["i"] for c in meta["chunks"] if c["retried"])
        assert retried == victim, "only the localized block refetched: %r" % retried
        assert meta["stats"]["quarantined"] >= 1, "bad block payload quarantined"

        # 清单本身必须与源逐块一致（可信基线）
        src = dl.Source(os.path.join(tmp, "src.bin"), {}, chunk)
        manifest = src.manifest()
        for item in manifest["chunks"]:
            start = item["offset"]
            assert sha(data[start:start + item["length"]]) == item["sha"], "manifest digest"
            assert item["offset"] + item["length"] <= size, "64-bit range within file"
        print("ok: whole-file failure localized to block %d and refetched" % victim[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plan_chunks_is_exactly_tiled():
    """块映射必须无重叠、无空洞、末块对齐（64 位）。"""
    dl = load_dl()
    cases = [(0, 1), (1, 1024), (1024, 1024), (1025, 1024), (10, 3),
             (2 ** 32 + 7, 2 ** 20)]
    for size, chunk in cases:
        plan = dl.plan_chunks(size, chunk)
        total = 0
        cursor = 0
        for (i, off, ln) in plan:
            assert off == cursor, "offset must be contiguous: %r" % plan
            assert ln > 0 and ln <= chunk, "length bound"
            assert isinstance(off, int) and isinstance(ln, int), "int offsets"
            assert off + ln <= dl.INT64_MAX, "within int64"
            cursor += ln
            total += ln
        assert total == size, "tiling for size=%d chunk=%d" % (size, chunk)
        assert len(plan) == len(set(i for i, _, _ in plan)), "unique indices"
    # 拒绝越界输入
    for bad in (-1, dl.INT64_MAX + 1):
        try:
            dl.plan_chunks(bad, 16)
        except dl.UsageError:
            pass
        else:
            raise AssertionError("plan_chunks must reject %r" % bad)
    print("ok: chunk plans tile exactly; int64 bounds enforced")


def test_i64_rejects_non_integers():
    dl = load_dl()
    for bad in (1.5, True, "abc", None, -1, 2 ** 63):
        try:
            dl.i64(bad, "x")
        except dl.UsageError:
            continue
        raise AssertionError("i64 accepted %r" % (bad,))
    assert dl.i64(2 ** 63 - 1, "x") == 2 ** 63 - 1, "int64 max accepted"
    assert dl.i64("42", "x") == 42, "numeric string accepted"
    print("ok: i64 rejects floats/bools/overflow")


def main():
    tests = [test_whole_file_failure_localizes_and_refetches,
             test_plan_chunks_is_exactly_tiled,
             test_i64_rejects_non_integers]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e))
    print("%d/%d internal checks passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
