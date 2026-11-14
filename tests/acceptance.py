#!/usr/bin/env python3
"""验收测试：断流续传、块级坏块隔离、并发一致性、Range 退化。

    python3 tests/acceptance.py [-k NAME] [-v]

全部用 stdlib；每个用例在临时目录里独立跑。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DL = os.path.join(ROOT, "dl.py")

sys.path.insert(0, HERE)
from make_fixture import gen  # noqa: E402

VERBOSE = "-v" in sys.argv
ONLY = None
if "-k" in sys.argv:
    ONLY = sys.argv[sys.argv.index("-k") + 1]


class Case:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn

    def run(self):
        tmp = tempfile.mkdtemp(prefix="dlacc-")
        try:
            return self.fn(tmp)
        finally:
            if not VERBOSE:
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                print("    kept:", tmp)


CASES = []


def case(fn):
    CASES.append(Case(fn.__name__, fn))
    return fn


def run_dl(req, out, *extra):
    cmd = [sys.executable, "-B", DL, req, out] + list(extra)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if VERBOSE:
        sys.stderr.write(proc.stderr)
    return proc


def meta_of(out):
    with open(out + ".meta.json", encoding="utf-8") as f:
        return json.load(f)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def assert_eq(a, b, msg):
    if a != b:
        raise AssertionError("%s: %r != %r" % (msg, a, b))


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run_resumable(tmp, size, chunk, concurrency=4, fail_at=(0.35, 0.6, 0.85),
                  corrupt_chunks=None, misalign_chunks=None, range_head=None,
                  norange=False, corrupt_times=1, flips=2):
    """反复续跑，直到退出码为 0；返回 (data, runs, metas)。"""
    data = gen(tmp, size, chunk, concurrency, fail_at=list(fail_at),
               corrupt_chunks=corrupt_chunks, misalign_chunks=misalign_chunks,
               range_head=range_head, norange=norange,
               corrupt_times=corrupt_times, flips=flips)
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    runs = []
    metas = []
    for _ in range(12):
        proc = run_dl(req, out, "--concurrency", str(concurrency))
        runs.append(proc.returncode)
        if proc.returncode == 0:
            break
        assert_true(proc.returncode in (3,), "unexpected exit %d" % proc.returncode)
        assert_true(os.path.exists(out + ".meta.json"), "meta missing after run")
        metas.append(meta_of(out))
        assert_true(not os.path.exists(out), "final output must not exist before verification")
    else:
        raise AssertionError("did not converge: %r" % runs)
    metas.append(meta_of(out))
    return data, runs, metas


# --------------------------------------------------------------------------- #
@case
def test_interrupt_resume_byte_identical(tmp):
    size = 350000
    data, runs, metas = run_resumable(tmp, size, 32768, concurrency=4,
                                      fail_at=(0.35, 0.6, 0.85))
    out = os.path.join(tmp, "out.bin")
    assert_eq(read(out), data, "final bytes must equal source")
    assert_eq(sha(read(out)), sha(data), "sha must match")
    # 3 次断流 + 1 次完成
    assert_eq(runs[:3], [3, 3, 3], "first three runs must be stream cuts")
    assert_eq(runs[3], 0, "fourth run completes")
    # 每轮的块记录单调前进且不重复传输已完成块
    seen = []
    for m in metas:
        assert_eq(m["mode"], "chunked", "mode")
        seen.append(m["stats"]["blocks_done"])
    assert_true(seen == sorted(seen), "progress must be monotonic: %r" % seen)
    final = metas[-1]
    assert_eq(final["status"], "complete", "status")
    assert_eq(final["stats"]["blocks_done"], final["stats"]["blocks_total"], "all blocks done")
    assert_eq(final["source"]["size"], size, "size")


@case
def test_range_requests_only_missing_blocks(tmp):
    """续传只对缺失块发 Range：第二轮请求的区间必须都是未完成块。"""
    size = 262144
    gen(tmp, size, 32768, 2, fail_at=[0.5])
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    p1 = run_dl(req, out)
    assert_eq(p1.returncode, 3, "first run must cut")
    state = json.load(open(os.path.join(tmp, "state.json"), encoding="utf-8"))
    done = set(state["completed"].keys())
    assert_true(len(done) > 0, "some blocks persisted")
    p2 = run_dl(req, out, "--concurrency", "1")
    assert_eq(p2.returncode, 0, "resume completes")
    meta = meta_of(out)
    for c in meta["chunks"]:
        if str(c["i"]) in done:
            assert_eq(c["source"], "state", "block %d should be reused from state" % c["i"])
        else:
            assert_eq(c["source"], "range", "block %d fetched by range" % c["i"])
    assert_eq(read(out), read(os.path.join(tmp, "src.bin")), "bytes")


@case
def test_corrupt_blocks_quarantined_block_level(tmp):
    """2 个字节翻转块：只重取这两个块，坏数据进隔离区。"""
    size = 300000
    chunk = 32768
    corrupt = [2, 6]
    data, runs, metas = run_resumable(tmp, size, chunk, concurrency=4,
                                      fail_at=(0.4, 0.75), corrupt_chunks=corrupt)
    out = os.path.join(tmp, "out.bin")
    assert_eq(read(out), data, "bytes must match despite injected corruption")
    final = metas[-1]
    by_index = {c["i"]: c for c in final["chunks"]}
    retried = sorted(i for i, c in by_index.items() if c["retried"])
    assert_eq(retried, corrupt, "only the corrupted blocks may be retried")
    for i in corrupt:
        c = by_index[i]
        assert_true(any("checksum" in r for r in c["reasons"]),
                    "block %d should record a checksum reason: %r" % (i, c["reasons"]))
        assert_true(c["quarantined"] >= 1, "block %d bad bytes must be quarantined" % i)
    qdir = out + ".quarantine"
    qfiles = sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []
    assert_eq(len(qfiles), 2, "one quarantined payload per corrupt block: %r" % qfiles)
    # 隔离文件长度 == 块长，且内容与源不一致（确认是真坏块）；每个坏块恰好 1 份
    quarantined_idx = []
    for name in qfiles:
        blob = read(os.path.join(qdir, name))
        idx = int(name.split("-")[1])
        quarantined_idx.append(idx)
        good = data[idx * chunk:(idx + 1) * chunk]
        assert_eq(len(blob), len(good), "quarantine size")
        assert_true(blob != good, "quarantine payload should differ from good block")
        diff = sum(1 for a, b in zip(blob, good) if a != b)
        assert_eq(diff, 2, "exactly 2 flipped bytes expected, got %d" % diff)
    assert_eq(sorted(quarantined_idx), corrupt, "quarantine must be block-precise")
    assert_eq(final["stats"]["quarantined"], 2, "quarantine counter")


@case
def test_misaligned_range_is_rejected(tmp):
    """服务端回错位起点：客户端必须识别并按块重取，而不是拼错。"""
    data, runs, metas = run_resumable(tmp, 200000, 25000, concurrency=2,
                                      fail_at=(0.5,), misalign_chunks=[1, 4])
    out = os.path.join(tmp, "out.bin")
    assert_eq(read(out), data, "bytes")
    final = metas[-1]
    by_index = {c["i"]: c for c in final["chunks"]}
    for i in (1, 4):
        assert_true(any("misaligned" in r for r in by_index[i]["reasons"]),
                    "block %d must record misalignment: %r" % (i, by_index[i]["reasons"]))


@case
def test_concurrency_invariance(tmp):
    """1/4/8 并发：最终文件相同，块记录相同（i/sha/retried）。"""
    size = 420000
    chunk = 24000
    snapshots = []
    elapsed = {}
    for conc in (1, 4, 8):
        sub = os.path.join(tmp, "c%d" % conc)
        data, runs, metas = run_resumable(sub, size, chunk, concurrency=conc,
                                          fail_at=(0.3, 0.6, 0.9),
                                          corrupt_chunks=[3, 9])
        out = os.path.join(sub, "out.bin")
        assert_eq(read(out), data, "bytes at concurrency %d" % conc)
        final = metas[-1]
        snapshots.append([(c["i"], c["sha"], c["retried"], c["offset"], c["length"])
                          for c in final["chunks"]])
        elapsed[conc] = final["stats"]["elapsed_s"]
        assert_eq(final["stats"]["blocks_done"], final["stats"]["blocks_total"], "all done")
        assert_eq(runs[0], 3, "first run cut")
    assert_eq(snapshots[0], snapshots[1], "1 vs 4 block records must match")
    assert_eq(snapshots[0], snapshots[2], "1 vs 8 block records must match")
    if VERBOSE:
        print("    elapsed by concurrency:", elapsed)


@case
def test_norange_degrades_to_full(tmp):
    data, runs, metas = run_resumable(tmp, 150000, 16384, concurrency=4,
                                      fail_at=(0.5,), norange=True)
    out = os.path.join(tmp, "out.bin")
    assert_eq(read(out), data, "bytes")
    final = metas[-1]
    assert_eq(final["mode"], "full", "must degrade to full mode")
    assert_true(final["alerts"], "degradation must warn")
    assert_true(any("Range" in a or "degraded" in a for a in final["alerts"]),
                "alert should mention Range/degrade: %r" % final["alerts"])
    assert_eq(final["status"], "complete", "status")
    assert_true(final["stats"]["degraded"], "degraded flag in stats")


@case
def test_head_range_only_degrades(tmp):
    """仅头 1MB 支持 Range：越过 1MB 的块触发退化，且告警明确。"""
    size = 3 * 1024 * 1024 + 12345
    data = gen(tmp, size, 256 * 1024, 4, fail_at=[], range_head=1024 * 1024)
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    proc = run_dl(req, out)
    assert_eq(proc.returncode, 0, "should complete after degrading")
    final = meta_of(out)
    assert_eq(read(out), data, "bytes")
    assert_eq(final["mode"], "full", "mode must be full")
    assert_true(any("head" in a for a in final["alerts"]),
                "alert must mention head-only Range: %r" % final["alerts"])


@case
def test_head_range_within_head_stays_chunked(tmp):
    """若所有块都落在头部支持区内，则不得退化。"""
    size = 900 * 1024
    data = gen(tmp, size, 128 * 1024, 4, fail_at=[], range_head=1024 * 1024)
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    proc = run_dl(req, out)
    assert_eq(proc.returncode, 0, "completes")
    final = meta_of(out)
    assert_eq(final["mode"], "chunked", "no degradation needed")
    assert_eq(read(out), data, "bytes")


@case
def test_tampered_part_is_detected(tmp):
    """篡改 .part 后：state 不被盲信，只重取被篡改的块。"""
    size = 200000
    chunk = 20000
    gen(tmp, size, chunk, 2, fail_at=[0.55])
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    assert_eq(run_dl(req, out).returncode, 3, "cut")
    state = json.load(open(os.path.join(tmp, "state.json"), encoding="utf-8"))
    done = sorted(int(k) for k in state["completed"])
    assert_true(done, "have completed blocks")
    victim = done[0]
    part = out + ".part"
    with open(part, "r+b") as f:
        f.seek(victim * chunk)
        f.write(b"\x00" * 8)
    proc = run_dl(req, out)
    assert_eq(proc.returncode, 0, "completes after re-verification")
    final = meta_of(out)
    retried = [c["i"] for c in final["chunks"] if c["retried"]]
    assert_eq(retried, [victim], "only tampered block refetched: %r" % retried)
    assert_true(any("re-verified" in a for a in final["alerts"]),
                "alert about block-level re-verification: %r" % final["alerts"])


@case
def test_state_survives_torn_write(tmp):
    """state 被截断时不能让下载失败：重建并从 .part 复核可救回的块。"""
    size = 180000
    chunk = 18000
    data = gen(tmp, size, chunk, 2, fail_at=[0.4])
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    assert_eq(run_dl(req, out).returncode, 3, "baseline run cuts")
    # 破坏 state（模拟半截 JSON 写入），保留 .part
    with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
        f.write('{"version": 2, "layout": ')
    codes = []
    for _ in range(6):
        proc = run_dl(req, out)
        codes.append(proc.returncode)
        if proc.returncode == 0:
            break
    assert_eq(codes[-1], 0, "recovers from torn state file: %r" % codes)
    final = meta_of(out)
    assert_eq(read(out), data, "bytes")
    assert_true(any("unreadable" in a or "reconstructed" in a for a in final["alerts"]),
                "alert about state recovery: %r" % final["alerts"])



@case
def test_throughput_and_per_block_status_output(tmp):
    data, runs, metas = run_resumable(tmp, 256000, 32000, concurrency=4, fail_at=(0.5,))
    final = metas[-1]
    for key in ("bytes_per_s", "elapsed_s", "blocks_per_s"):
        assert_true(key in final["throughput"], "throughput." + key)
    assert_true(final["stats"]["bytes_per_s"] > 0, "throughput positive")
    c = final["chunks"][0]
    for key in ("i", "offset", "length", "sha", "retried", "status", "ranges", "reasons"):
        assert_true(key in c, "chunk record field " + key)
    assert_true(final["log"], "per-block log lines present")


@case
def test_seventeen_digit_offsets_use_int(tmp):
    """块边界/偏移必须是整数（64 位语义），不能出现浮点/字符串。"""
    data, runs, metas = run_resumable(tmp, 100000, 33333, concurrency=3, fail_at=(0.5,))
    final = metas[-1]
    for c in final["chunks"]:
        assert_true(isinstance(c["offset"], int) and not isinstance(c["offset"], bool),
                    "offset must be int")
        assert_true(isinstance(c["offset"], int) and c["offset"] >= 0, "offset non-negative")
        assert_true(isinstance(c["length"], int) and c["length"] > 0, "length int")
        assert_true(c["offset"] + c["length"] <= final["source"]["size"], "range within file")
    # 累积偏移必须严格等于文件大小（无重叠、无空洞）
    total = sum(c["length"] for c in final["chunks"])
    assert_eq(total, final["source"]["size"], "blocks tile the file exactly")


@case
def test_full_mode_also_resumes_on_cut(tmp):
    """退化整体重传遇到断流：返回 3，重跑仍退化为 full 并最终完成。"""
    data = gen(tmp, 200000, 20000, 2, fail_at=[0.5], norange=True)
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    codes = []
    for _ in range(5):
        code = run_dl(req, out).returncode
        codes.append(code)
        if code == 0:
            break
    assert_eq(codes[0], 3, "full transfer must honour fail_at: %r" % codes)
    assert_eq(codes[-1], 0, "eventually completes: %r" % codes)
    final = meta_of(out)
    assert_eq(final["mode"], "full", "mode")
    assert_eq(read(out), data, "bytes")
    assert_eq(final["status"], "complete", "status")


@case
def test_rerun_is_idempotent(tmp):
    """已完成后再跑一次：不重传，块记录仍完整。"""
    data, runs, metas = run_resumable(tmp, 120000, 20000, concurrency=3, fail_at=(0.5,))
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    final = metas[-1]
    before_records = [(c["i"], c["sha"], c["retried"], c["offset"], c["length"])
                      for c in final["chunks"]]
    proc = run_dl(req, out)
    assert_eq(proc.returncode, 0, "rerun ok")
    again = meta_of(out)
    assert_eq(again["stats"]["run_requests"], 0, "no range requests on a satisfied rerun")
    assert_true(again["stats"]["requests"] >= 0 and again["stats"]["runs"] >= 2,
                "cumulative counters span runs: %r" % again["stats"].get("runs"))
    assert_eq(again["status"], "complete", "status")
    assert_eq(read(out), data, "bytes untouched")
    after_records = [(c["i"], c["sha"], c["retried"], c["offset"], c["length"])
                     for c in again["chunks"]]
    assert_eq(after_records, before_records, "block records stable across rerun")
    assert_eq(again["stats"]["blocks_done"], again["stats"]["blocks_total"], "all blocks accounted for")


@case
def test_zero_and_tiny_files(tmp):
    """0 字节与 1 字节文件：块映射退化为单块/零块也要正确。"""
    for size in (0, 1, 7):
        sub = os.path.join(tmp, "s%d" % size)
        data = gen(sub, size, 1024, 2)
        req = os.path.join(sub, "req.json")
        out = os.path.join(sub, "out.bin")
        proc = run_dl(req, out)
        assert_eq(proc.returncode, 0, "size %d completes" % size)
        final = meta_of(out)
        assert_eq(read(out), data, "size %d bytes" % size)
        assert_eq(final["source"]["size"], size, "reported size")
        assert_eq(sum(c["length"] for c in final["chunks"]), size, "tiling")


@case
def test_layout_change_rebuilds_state(tmp):
    """chunk 改变后旧 state 不得误用：块映射重建、不报错、结果正确。"""
    size = 160000
    data = gen(tmp, size, 16000, 2)
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    assert_eq(run_dl(req, out).returncode, 0, "first pass")
    proc = run_dl(req, out, "--chunk", "8000")
    assert_eq(proc.returncode, 0, "second pass with new chunk")
    final = meta_of(out)
    assert_eq(final["chunk"], 8000, "chunk applied")
    assert_eq(read(out), data, "bytes")
    assert_true(any("layout" in a for a in final["alerts"]),
                "alert about layout rebuild: %r" % final["alerts"])


@case
def test_bad_request_arguments(tmp):
    """非法参数必须是 usage error（退出码 2），不能崩。"""
    data = gen(tmp, 50000, 5000, 2)
    out = os.path.join(tmp, "out.bin")
    req = os.path.join(tmp, "req.json")
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("not json at all")
    assert_eq(run_dl(bad, out).returncode, 2, "non-JSON request")
    assert_eq(run_dl(os.path.join(tmp, "missing.json"), out).returncode, 2, "missing request")
    import json as _json
    with open(bad, "w", encoding="utf-8") as f:
        _json.dump({"source": "nope.bin"}, f)
    assert_eq(run_dl(bad, out).returncode, 2, "missing source")
    with open(bad, "w", encoding="utf-8") as f:
        _json.dump({"source": "src.bin", "chunk": 0}, f)
    assert_eq(run_dl(bad, out).returncode, 2, "zero chunk")
    with open(bad, "w", encoding="utf-8") as f:
        _json.dump({"source": "src.bin", "chunk": -5}, f)
    assert_eq(run_dl(bad, out).returncode, 2, "negative chunk")
    with open(bad, "w", encoding="utf-8") as f:
        _json.dump({"source": "src.bin", "chunk": 4096, "fail_at": ["x"]}, f)
    assert_eq(run_dl(bad, out).returncode, 2, "bad fail_at")


@case
def test_corrupt_twice_still_recovers(tmp):
    """连续两次投递坏数据：仍在块级隔离并最终一致。"""
    data = gen(tmp, 240000, 30000, 3, fail_at=[0.5], corrupt_chunks=[5],
               corrupt_times=2, flips=3)
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    codes = []
    for _ in range(8):
        code = run_dl(req, out).returncode
        codes.append(code)
        if code == 0:
            break
    assert_eq(codes[-1], 0, "recovers: %r" % codes)
    final = meta_of(out)
    assert_eq(read(out), data, "bytes")
    c = [x for x in final["chunks"] if x["i"] == 5][0]
    assert_true(len(c["reasons"]) >= 2, "two failures recorded: %r" % c["reasons"])
    assert_true(c["quarantined"] >= 2, "two payloads quarantined")
    retried = sorted(x["i"] for x in final["chunks"] if x["retried"])
    assert_eq(retried, [5], "only block 5 retried")


@case
def test_hard_kill_does_not_refetch_valid_blocks(tmp):
    """模拟硬杀（.part 有数据但 state 未记录）：能从 .part 认领，不重复下载。"""
    size, chunk = 160000, 20000
    data = gen(tmp, size, chunk, 2, fail_at=[])
    req = os.path.join(tmp, "req.json")
    out = os.path.join(tmp, "out.bin")
    # 先完整拉一次到临时位置，拿到"已经下载好一半"的 .part
    assert_eq(run_dl(req, out).returncode, 0, "baseline")
    half = size // 2
    with open(out + ".part", "wb") as f:          # 重建一个只含前一半的 .part
        f.write(data[:half] + b"\x00" * (size - half))
    for name in ("state.json",):
        os.remove(os.path.join(tmp, name))
    os.remove(out)
    proc = run_dl(req, out)
    assert_eq(proc.returncode, 0, "resume completes: %s" % proc.stderr[-500:])
    final = meta_of(out)
    assert_eq(read(out), data, "bytes")
    reused = [c["i"] for c in final["chunks"] if c["source"] == "state"]
    expected = list(range(half // chunk))
    assert_eq(reused, expected, "blocks wholly inside .part must be adopted: %r" % reused)


def main():
    failures = []
    selected = [c for c in CASES if ONLY is None or ONLY in c.name]
    for c in selected:
        t0 = time.time()
        try:
            c.run()
            print("PASS %-48s %.2fs" % (c.name, time.time() - t0))
        except AssertionError as e:
            failures.append((c.name, str(e)))
            print("FAIL %-48s %s" % (c.name, e))
        except Exception as e:
            failures.append((c.name, "%s: %s" % (type(e).__name__, e)))
            import traceback
            traceback.print_exc()
            print("ERROR %-47s %s" % (c.name, e))
    print()
    print("%d/%d passed" % (len(selected) - len(failures), len(selected)))
    if failures:
        for name, msg in failures:
            print(" - %s: %s" % (name, msg))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
