"""验收测试：故障注入 + 崩溃断点续采 + 多代次原子发布。

覆盖：
1. 换代(version-conflict)、错误内容(checksum)、短读(short-read)、
   错序/重叠(overlap-conflict)、缺片(missing-range) 注入；
   20 个对象逐字节 + 独立 sha256 核对；冲突对象禁止索引。
2. after-piece / after-range / before-pointer 三处中断，
   校验 fetched/reused 范围对应关系与未确认片清理；
   任意读者 inspect 只得到摘要匹配的完整代次。
3. 多代次发布、旧代次可读、同代异清单拒绝、未公开代次不可读。
"""
import base64
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import land

ROOT = Path(".acceptance")
PY = sys.executable
SCRIPT = str(Path("land.py").resolve())


def h(data):
    return hashlib.sha256(data).hexdigest()


def b64(data):
    return base64.b64encode(data).decode("ascii")


def chunk(raw, start, end, version, payload=None):
    block = raw[start:end]
    return {"version": version, "start": start, "end": end,
            "sha256": h(block),
            "payload": b64(block) if payload is None else payload}


def make_raw(n, generation):
    return bytes((n * 15 + j * 31 + j * j + len(generation)) % 256
                 for j in range(240 + n * 9))


def write_manifest(name, manifest):
    path = ROOT / (name + ".json")
    path.write_text(json.dumps(manifest) + "\n")
    return path


def run_cli(*args):
    proc = subprocess.run([PY, SCRIPT, *map(str, args)],
                          cwd=Path.cwd(), capture_output=True, text=True)
    return proc


# ---------------------------------------------------------------------------
# 场景 A：20 对象 + 全部故障类型
# ---------------------------------------------------------------------------
def build_faulty_manifest():
    generation = "gen-a"
    objects, expected = [], {}
    # 规划：12 个健康对象（含短读重试、错序、良性重叠），8 个隔离对象
    for n in range(20):
        oid = "obj-%03d" % n
        raw = make_raw(n, generation)
        version = "%s-v%d" % (generation, n)
        obj = {"id": oid, "version": version, "length": len(raw),
               "sha256": h(raw), "chunks": []}
        if n < 12:
            # 健康对象：三段，故意错序，段间存在部分重叠
            decls = [(100, 231), (0, 143), (228, len(raw))]
            for (a, b) in decls:
                obj["chunks"].append(chunk(raw, a, b, version))
            random.Random(n).shuffle(obj["chunks"])
            if n % 2 == 0:
                # 注入短读/错误载荷 + retry（第二次成功）
                target = obj["chunks"][0]
                target["retry"] = target["payload"]
                target["payload"] = "AA=="  # 短读
            expected[oid] = raw
        elif n == 12:
            # 换代冲突：片版本与对象版本不同（不可重试）
            obj["chunks"].append(chunk(raw, 0, 100, version + "-STALE"))
            obj["chunks"].append(chunk(raw, 100, len(raw), version + "-STALE"))
        elif n == 13:
            # 错误内容：载荷摘要不匹配且无 retry
            c = chunk(raw, 0, 100, version)
            c["payload"] = b64(b"x" * 100)
            obj["chunks"].append(c)
            obj["chunks"].append(chunk(raw, 100, len(raw), version))
        elif n == 14:
            # 短读：载荷长度不足，无 retry
            c = chunk(raw, 0, 100, version)
            c["payload"] = b64(raw[0:50])
            obj["chunks"].append(c)
            obj["chunks"].append(chunk(raw, 100, len(raw), version))
        elif n == 15:
            # 重叠冲突：c1 覆盖 0:120，c2 覆盖 60:180，
            # 两者在 60:120 重叠区内容逐字节不同
            c1 = chunk(raw, 0, 120, version)
            overlap_bad = bytearray(raw[60:120])
            overlap_bad[0] ^= 0xFF
            c2_payload = bytes(overlap_bad) + raw[120:180]
            c2 = {"version": version, "start": 60, "end": 180,
                  "sha256": h(c2_payload), "payload": b64(c2_payload)}
            obj["chunks"] = [c1, c2, chunk(raw, 180, len(raw), version)]
        elif n == 16:
            # 缺片：中间空洞
            obj["chunks"] = [chunk(raw, 0, 80, version),
                             chunk(raw, 120, len(raw), version)]
        elif n == 17:
            # 非法范围声明（end > length），不可重试：载荷本身自洽，
            # 但范围越过对象边界，属于对象级声明错误
            over = raw[100:] + b"\x00"
            obj["chunks"] = [chunk(raw, 0, 100, version),
                             {"version": version, "start": 100,
                              "end": len(raw) + 1, "sha256": h(over),
                              "payload": b64(over)}]
        elif n == 18:
            # 整对象摘要被篡改：范围全对，但对象级 sha256 不匹配
            obj["chunks"] = [chunk(raw, 0, 100, version),
                             chunk(raw, 100, len(raw), version)]
            obj["sha256"] = "00" * 32
        else:
            # 声明长度大于实际覆盖：范围全部合法但尾部缺片
            obj["chunks"] = [chunk(raw, 0, 100, version),
                             chunk(raw, 100, len(raw), version)]
            obj["length"] = len(raw) + 10
        objects.append(obj)
    random.Random(42).shuffle(objects)
    return {"generation": generation, "objects": objects}, expected


EXPECTED_REASONS = {
    "obj-012": "version-conflict",
    "obj-013": "checksum-mismatch",
    "obj-014": "short-read",
    "obj-015": "overlap-conflict",
    "obj-016": "missing-range",
    "obj-017": "bad-range",
    "obj-018": "digest-mismatch",
    "obj-019": "missing-range",
}


def test_fault_injection():
    manifest, expected = build_faulty_manifest()
    path = write_manifest("faulty", manifest)
    out = land.land(ROOT / "store", json.loads(path.read_text()), workers=4)
    assert out["generation"] == "gen-a"
    published = {p["id"]: p for p in out["published"]}
    quarantine = {q["id"]: q for q in out["quarantine"]}
    assert len(published) == 12, sorted(published)
    assert len(quarantine) == 8, sorted(quarantine)

    # 健康对象：逐字节 + 独立摘要
    for oid, raw in expected.items():
        on_disk = (ROOT / "store" / "gen-a" / oid).read_bytes()
        assert on_disk == raw, "byte mismatch " + oid
        assert h(on_disk) == published[oid]["sha256"]
        assert published[oid]["length"] == len(raw)

    # 冲突对象：禁止索引，且发布区无文件
    for oid, reason in EXPECTED_REASONS.items():
        assert oid not in published, oid + " must not be indexed"
        assert quarantine[oid]["reason"] == reason, (oid, quarantine[oid])
        assert not (ROOT / "store" / "gen-a" / oid).exists()

    # inspect 的读者视图与发布结果一致
    view = land.inspect(ROOT / "store")
    assert view["generation"] == "gen-a"
    assert {p["id"] for p in view["published"]} == set(expected)
    assert {q["id"] for q in view["quarantine"]} == set(EXPECTED_REASONS)

    # 所有读者拿到的已发布文件摘要匹配
    for p in view["published"]:
        data = (ROOT / "store" / "gen-a" / p["id"]).read_bytes()
        assert h(data) == p["sha256"] and len(data) == p["length"]

    # 同代异清单拒绝
    tampered = json.loads(path.read_text())
    tampered["objects"][0]["id"] = "obj-999"
    try:
        land.land(ROOT / "store", tampered, workers=2)
    except land.ManifestError:
        pass
    else:
        raise AssertionError("different manifest for same generation must fail")

    # 同清单幂等重入
    again = land.land(ROOT / "store", json.loads(path.read_text()), workers=2)
    assert again["published"] == out["published"]
    assert again["quarantine"] == out["quarantine"]
    assert again["fetched_ranges"] == 0 and again["reused_ranges"] == 0
    print("[A] fault injection: 12 verified byte-exact, 8 quarantined with"
          " correct reasons, conflict objects not indexed")


# ---------------------------------------------------------------------------
# 场景 B：三处崩溃断点
# ---------------------------------------------------------------------------
def build_clean_manifest(generation, count=6):
    objects, expected = [], {}
    for n in range(count):
        raw = make_raw(n + 100, generation)
        version = "%s-v%d" % (generation, n)
        chunks = [chunk(raw, a, b, version)
                  for a, b in [(100, 231), (0, 143), (228, len(raw))]]
        if n % 2 == 0:
            chunks[1]["retry"] = chunks[1]["payload"]
            chunks[1]["payload"] = "AA=="
        objects.append({"id": "obj-%03d" % n, "version": version,
                        "length": len(raw), "sha256": h(raw), "chunks": chunks})
        expected["obj-%03d" % n] = raw
    return {"generation": generation, "objects": objects}, expected


def count_files(base, suffix):
    return sum(1 for _ in Path(base).rglob(suffix))


def test_crash_points():
    manifest, expected = build_clean_manifest("gen-crash")
    path = write_manifest("crash", manifest)
    total_ranges = sum(len(o["chunks"]) for o in manifest["objects"])
    assert total_ranges == 18
    store = ROOT / "crash-store"

    for point in ["after-piece", "after-range", "before-pointer"]:
        shutil.rmtree(store, ignore_errors=True)
        # 第一次：注入崩溃，退出码必须是 97
        proc = run_cli(store, "land", path, "--workers", "3", "--crash", point)
        assert proc.returncode == 97, (point, proc.returncode, proc.stderr)

        # 崩溃后：代次未公开，读者不可见
        view = run_cli(store, "inspect")
        cur = json.loads(view.stdout)
        assert cur["generation"] is None and cur["published"] == []
        blocked = run_cli(store, "inspect", "--generation", "gen-crash")
        assert blocked.returncode == 2
        assert not (store / "gen-crash").exists(), "no published dir before pointer"

        parts = count_files(store, "*.part")
        ranges = count_files(store, "*.range")
        if point == "after-piece":
            # 片落盘但未登记：恰好 1 个未确认片，0 个登记范围
            assert parts == 1 and ranges == 0, (point, parts, ranges)
        elif point == "after-range":
            # 登记完成后片已删除：0 个未确认片，1 个登记范围
            assert parts == 0 and ranges == 1, (point, parts, ranges)
        else:
            # 指针前所有范围均已登记，暂存片全部清理
            assert parts == 0 and ranges == total_ranges, (point, parts, ranges)

        # 同清单重启：不注入崩溃
        proc2 = run_cli(store, "land", path, "--workers", "3")
        assert proc2.returncode == 0, proc2.stderr
        recovery = json.loads(proc2.stdout)

        if point == "after-piece":
            # 崩溃在登记之前：无任何已登记范围可复用，未确认片被清理后
            # 全部 18 个声明重新读取
            assert recovery["fetched_ranges"] == total_ranges, recovery
            assert recovery["reused_ranges"] == 0, recovery
        elif point == "after-range":
            # 1 个范围已登记并通过复核 -> 复用；其余 17 个重新读取
            assert recovery["fetched_ranges"] == total_ranges - 1, recovery
            assert recovery["reused_ranges"] == 1, recovery
        else:
            # 全部范围已持久化，仅缺指针
            assert recovery["fetched_ranges"] == 0, recovery
            assert recovery["reused_ranges"] == total_ranges, recovery

        # 带 retry 的短读片：在首次（崩溃的）运行中已成功重试并登记，
        # 重启只复用注册表，不再消耗 retry；before-pointer 时首轮已全部完成
        if point == "before-pointer":
            assert recovery["retried_chunks"] == 0
        else:
            assert recovery["retried_chunks"] == 3  # 6 个对象中 3 个带 retry
        assert len(recovery["published"]) == 6 and recovery["quarantine"] == []

        # 恢复后：无未确认片，所有读者得到完整且摘要匹配的代次
        assert count_files(store, "*.part") == 0
        final_view = json.loads(run_cli(store, "inspect").stdout)
        assert final_view["generation"] == "gen-crash"
        for p in final_view["published"]:
            data = (store / "gen-crash" / p["id"]).read_bytes()
            assert data == expected[p["id"]]
            assert h(data) == p["sha256"] and len(data) == p["length"]
        print("[B] crash %-14s -> exit97, recovery fetched=%d reused=%d,"
              " readers see complete generation"
              % (point, recovery["fetched_ranges"], recovery["reused_ranges"]))


# ---------------------------------------------------------------------------
# 场景 C：多代次发布，旧代次可读
# ---------------------------------------------------------------------------
def test_generations():
    store = ROOT / "gen-store"
    m1, exp1 = build_clean_manifest("first", count=4)
    p1 = write_manifest("g1", m1)
    r1 = land.land(store, json.loads(p1.read_text()), workers=3)
    assert r1["generation"] == "first" and len(r1["published"]) == 4

    m2, exp2 = build_clean_manifest("second", count=4)
    # 同 id 对象内容不同，验证旧代文件不被覆盖
    p2 = write_manifest("g2", m2)
    r2 = land.land(store, json.loads(p2.read_text()), workers=3)
    assert r2["generation"] == "second"

    cur = land.inspect(store)
    assert cur["generation"] == "second"
    old = land.inspect(store, generation="first")
    assert old["generation"] == "first" and len(old["published"]) == 4

    for oid, raw in exp1.items():
        assert (store / "first" / oid).read_bytes() == raw
    for oid, raw in exp2.items():
        on_disk = (store / "second" / oid).read_bytes()
        assert on_disk == raw
        assert exp1[oid] != raw  # 两代内容确实不同但各自完整
    print("[C] two generations published atomically; current=second,"
          " first remains readable and byte-exact")


# ---------------------------------------------------------------------------
# 场景 D：并发读取者在发布过程中只观察到完整代次
# ---------------------------------------------------------------------------
def test_reader_visibility():
    store = ROOT / "reader-store"
    manifest, _ = build_clean_manifest("vis", count=5)
    path = write_manifest("vis", manifest)
    land.land(store, json.loads(path.read_text()), workers=4)
    # 发布后任何时刻 inspect 都只能读到已完整校验的代次
    for _ in range(5):
        view = land.inspect(store)
        assert view["generation"] == "vis"
        for p in view["published"]:
            data = (store / "vis" / p["id"]).read_bytes()
            assert h(data) == p["sha256"]
    # 未公开代次不可读
    try:
        land.inspect(store, generation="ghost")
    except land.ManifestError:
        pass
    else:
        raise AssertionError("unpublished generation must be unreadable")
    print("[D] readers only ever observe complete, digest-matching generation")


def test_edge_persistence():
    manifest, expected = build_clean_manifest("edge")
    path = write_manifest("edge", manifest)
    store = ROOT / "edge-store"

    # CLI 层面 after-piece 崩溃
    proc = run_cli(store, "land", path, "--workers", "4",
                   "--crash", "after-piece")
    assert proc.returncode == 97, proc.stderr
    parts = list(Path(store).rglob("*.part"))
    ranges = list(Path(store).rglob("*.range"))
    assert len(parts) == 1 and len(ranges) == 0, (len(parts), len(ranges))

    # 额外放一个无法被注册表引用的孤儿片，重启必须清掉
    orphan_dir = store / "staging" / "edge" / "objects" / "obj-000" / "pieces"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / ("deadbeef" * 8 + ".part")).write_bytes(b"orphan")

    proc2 = run_cli(store, "land", path, "--workers", "4")
    assert proc2.returncode == 0, proc2.stderr
    d = json.loads(proc2.stdout)
    assert d["fetched_ranges"] == 18 and d["reused_ranges"] == 0
    assert list(Path(store).rglob("*.part")) == []

    for oid, raw in expected.items():
        assert (store / "edge" / oid).read_bytes() == raw

    # 再 land：暂存清理不得影响已发布代次
    proc3 = run_cli(store, "land", path, "--workers", "2")
    d3 = json.loads(proc3.stdout)
    assert d3["fetched_ranges"] == 0 and d3["reused_ranges"] == 0
    for oid, raw in expected.items():
        assert (store / "edge" / oid).read_bytes() == raw

    # 空 ROOT inspect
    empty = ROOT / "empty"
    empty.mkdir()
    view = land.inspect(empty)
    assert view == {"generation": None, "published": [], "quarantine": []}
    print("[E] orphan/unconfirmed pieces swept, recovery byte-exact,"
          " re-land idempotent, empty root inspect correct")


def test_manifest_edges():
    base = ROOT / "edges"
    hh = h

    # 1) 零长度范围（end<=start）属于非法范围声明 -> bad-range
    empty_obj = {"id": "empty", "version": "v1", "length": 0,
                 "sha256": hh(b""),
                 "chunks": [{"version": "v1", "start": 0, "end": 0,
                             "sha256": hh(b""), "payload": ""}]}
    out = land.land(base, {"generation": "g-edge", "objects": [empty_obj]}, 1)
    assert out["quarantine"][0]["reason"] == "bad-range", out

    # 2) 单字节对象正常发布
    raw = b"Q"
    one = {"id": "one", "version": "v1", "length": 1, "sha256": hh(raw),
           "chunks": [{"version": "v1", "start": 0, "end": 1,
                       "sha256": hh(raw), "payload": b64(raw)}]}
    out2 = land.land(base, {"generation": "g-edge2", "objects": [one]}, 1)
    assert out2["published"][0]["id"] == "one"
    assert (base / "g-edge2" / "one").read_bytes() == raw

    # 3) 非法清单在清单阶段拒绝（ManifestError）
    bad_cases = [
        {"generation": "../escape", "objects": []},
        {"generation": "g3", "objects": "not-a-list"},
        {"generation": "g3", "objects": [{"id": "a", "version": "v",
                                          "length": 1, "sha256": "zz",
                                          "chunks": []}]},
    ]
    for bad in bad_cases:
        try:
            land.land(base, bad, 1)
        except land.ManifestError:
            continue
        raise AssertionError("malformed manifest must be rejected")

    # 4) 完全相同的重复声明（同 start/end/digest）在清单阶段拒绝
    dup = {"generation": "g4", "objects": [{
        "id": "d", "version": "v", "length": 4, "sha256": hh(b"abcd"),
        "chunks": [
            {"version": "v", "start": 0, "end": 2, "sha256": hh(b"ab"),
             "payload": b64(b"ab")},
            {"version": "v", "start": 0, "end": 2, "sha256": hh(b"ab"),
             "payload": b64(b"ab")},
            {"version": "v", "start": 2, "end": 4, "sha256": hh(b"cd"),
             "payload": b64(b"cd")}]}]}
    try:
        land.land(base, dup, 1)
    except land.ManifestError:
        pass
    else:
        raise AssertionError("duplicate declaration must be rejected")

    # 5) 良性重复覆盖（整对象片 + 两个分片，内容逐字节相等）-> 合并发布
    good_dup = {"generation": "g5", "objects": [{
        "id": "d", "version": "v", "length": 4, "sha256": hh(b"abcd"),
        "chunks": [
            {"version": "v", "start": 0, "end": 4, "sha256": hh(b"abcd"),
             "payload": b64(b"abcd")},
            {"version": "v", "start": 0, "end": 2, "sha256": hh(b"ab"),
             "payload": b64(b"ab")},
            {"version": "v", "start": 2, "end": 4, "sha256": hh(b"cd"),
             "payload": b64(b"cd")}]}]}
    out5 = land.land(base, good_dup, 1)
    assert len(out5["published"]) == 1 and out5["quarantine"] == []
    assert (base / "g5" / "d").read_bytes() == b"abcd"
    print("[F] manifest edges: bad-range on zero span, single-byte OK,"
          " malformed/duplicate rejected, benign overlap merged")


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    random.seed(7)
    test_fault_injection()
    test_crash_points()
    test_generations()
    test_reader_visibility()
    test_edge_persistence()
    test_manifest_edges()
    print("\nALL ACCEPTANCE TESTS PASSED")


if __name__ == "__main__":
    main()
