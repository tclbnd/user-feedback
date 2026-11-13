"""Acceptance tests for geo.py.

Run with ``python3 tests.py`` (no third-party dependencies).
"""
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEO = HERE / "geo.py"

_TEMP_ROOTS = []


def new_root(prefix):
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=str(HERE)))
    _TEMP_ROOTS.append(root)
    return root


def run_cli(args, cwd=None):
    proc = subprocess.run(
        [sys.executable, str(GEO)] + list(args),
        text=True,
        capture_output=True,
        cwd=str(cwd) if cwd else None,
    )
    return proc


def run_ok(args):
    proc = run_cli(args)
    assert proc.returncode == 0, "exit %d: %s" % (proc.returncode, proc.stderr)
    return json.loads(proc.stdout)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def canon_lon(value):
    return -180.0 if float(value) == 180 else float(value)


def arc_contains(west, east, lon):
    """Independent reference arc predicate (closed eastward arc)."""
    if west == -180 and east == 180:
        return True
    w = canon_lon(west)
    e = canon_lon(east)
    v = canon_lon(lon)
    if w == e:
        return v == w
    if w < e:
        return w <= v <= e
    return v >= w or v <= e


def is_crossing(west, east):
    if west == -180 and east == 180:
        return False
    w = canon_lon(west)
    e = canon_lon(east)
    return w > e


def reference_rows(items, query):
    """Point-by-point truth over the visible items list."""
    rows = [
        record
        for record in items
        if query["start"] <= record["t"] < query["end"]
        and query["south"] <= record["lat"] <= query["north"]
        and arc_contains(query["west"], query["east"], record["lon"])
    ]
    return sorted(rows, key=lambda record: (record["t"], record["device"]))


def read_manifest(root, version):
    path = Path(root) / "versions" / ("v%d.json" % version)
    return json.loads(path.read_text())


def resolve_manifest(root, version):
    """Resolve checkpoint/delta chain into {buckets, keys, total_points}.

    Deliberately implemented here from the on-disk format, independently of
    the reader under test, so tests really check the published bytes.
    """
    if version == 0:
        return {"buckets": {}, "keys": {}, "total_points": 0}
    chain = []
    current = version
    while current != 0:
        data = read_manifest(root, current)
        chain.append(data)
        if data.get("full"):
            break
        current = data["base"]
    chain.reverse()

    buckets = {}
    keys = {}
    total = 0
    for data in chain:
        if data.get("full"):
            buckets = dict(data["buckets"])
            keys = dict(data["keys"])
        else:
            for key_text in data["buckets_del"]:
                buckets.pop(key_text, None)
            for key_text in data["keys_del"]:
                keys.pop(key_text, None)
            buckets.update(data["buckets_set"])
            keys.update(data["keys_set"])
        total = data["total_points"]
    return {"buckets": buckets, "keys": keys, "total_points": total, "version": version}


def read_bucket(root, content_hash):
    path = Path(root) / "buckets" / ("%s.json" % content_hash)
    return json.loads(path.read_text())


def read_items(root, version):
    """All records visible through version `version`, assembled from buckets."""
    manifest = resolve_manifest(root, version)
    assert manifest["version"] == version
    seen = {}
    for bucket_text, content_hash in manifest["buckets"].items():
        for record in read_bucket(root, content_hash):
            seen[(record["device"], record["t"])] = record
    assert len(seen) == manifest["total_points"], (len(seen), manifest["total_points"])
    for key_text in manifest["keys"]:
        device, t = json.loads(key_text)
        assert (device, t) in seen, key_text
    return seen


def item_rows(items):
    return sorted(items.values(), key=lambda record: (record["t"], record["device"]))


def canon_record(record):
    """Canonical visible form of a stored record for comparison."""
    return {
        "device": record["device"],
        "t": record["t"],
        "rev": record["rev"],
        "lon": canon_lon(record["lon"]),
        "lat": float(record["lat"]),
        "source": record["source"],
    }


def reduce_points(batches):
    """Independent primary-key reduction: highest revision per (device, t).

    Later batches may correct an earlier row; an older revision is ignored.
    Raises if the same revision carries conflicting content.
    """
    state = {}
    for batch in batches:
        for record in batch:
            key = (record["device"], record["t"])
            canonical = canon_record(record)
            existing = state.get(key)
            if existing is None:
                state[key] = canonical
                continue
            if canonical["rev"] < existing["rev"]:
                continue
            if canonical["rev"] == existing["rev"]:
                if canonical != existing:
                    raise AssertionError("conflicting same revision in generator: %r" % (key,))
                continue
            state[key] = canonical
    return sorted(state.values(), key=lambda record: (record["t"], record["device"]))


def make_case(seed, count=400, query_count=800):
    rng = random.Random(seed)
    points = []
    endpoints = [
        (-180.0, -90.0), (180.0, 90.0), (179.999, 89.999), (-179.999, -89.999),
        (0.0, 0.0), (180.0, 0.0), (-180.0, 0.0), (90.0, 45.0), (-90.0, -45.0),
        (179.95, 84.0), (-179.98, 84.0),
    ]
    for i in range(count):
        if i < len(endpoints):
            lon, lat = endpoints[i]
        elif i % 3 == 0:
            lon = rng.choice([179.9 + rng.random() * 0.1, -180 + rng.random() * 0.1])
            lat = rng.uniform(-90, 90)
        else:
            lon = rng.uniform(-180, 180)
            lat = rng.uniform(-90, 90)
        # A unique device per row keeps the initial primary keys distinct,
        # so the generator itself never creates an ambiguous conflict.
        points.append(
            {
                "device": "d%04d" % i,
                "t": rng.randrange(-7200, 9000),
                "rev": 1,
                "lon": lon,
                "lat": lat,
                "source": "gen-%d" % i,
            }
        )

    # Same-second corrections: bump a revision (sometimes within one second).
    revisions = []
    for i in range(0, count, 6):
        base = points[i]
        revisions.append(
            {
                "device": base["device"],
                "t": base["t"],
                "rev": base["rev"] + rng.choice([1, 1, 2]),
                "lon": rng.choice([base["lon"], 180.0 if base["lon"] == -180 else -180.0 if base["lon"] == 180 else base["lon"]]),
                "lat": max(-90.0, min(90.0, base["lat"] + rng.choice([0.0, 0.001, -0.001]))),
                "source": "corr-%d" % i,
            }
        )
    # Stale replays of the original revision must be ignored.
    stale = [
        dict(points[i]) for i in range(0, count, 6)
    ]
    # Exact duplicates must be no-ops.
    duplicates = [dict(points[i]) for i in range(0, count, 11)]

    queries = []
    for i in range(query_count):
        kind = i % 10
        if kind in (0, 5):  # date-line crossing
            west = rng.uniform(170, 180)
            east = rng.uniform(-180, -170)
        elif kind == 1:  # ordinary interval
            west = rng.choice([-180.0, -179.999, -90.0, 0.0, 179.999])
            east = rng.choice([-180.0, -170.0, 0.0, 90.0, 179.9, 180.0])
            if west > east:
                west, east = east, west
        elif kind == 2:  # full circle
            west, east = -180.0, 180.0
        elif kind == 3:  # single meridian
            west = east = rng.choice([-180.0, 180.0, 0.0, 179.9, -179.9])
        elif kind == 4:  # longitude endpoint query
            west = rng.choice([-180.0, 180.0, 179.999, -179.999])
            east = rng.choice([-180.0, 180.0, 179.999, -179.999])
        elif kind == 6:  # latitude endpoint band
            west, east = -180.0, 180.0
        else:
            west = rng.uniform(-180, 180)
            east = rng.uniform(-180, 180)
        if kind == 6:
            south, north = rng.choice([(-90.0, -89.999), (89.999, 90.0), (-90.0, 90.0)])
        else:
            south = rng.choice([-90.0, rng.uniform(-90, 89), rng.uniform(-70, -10)])
            north = rng.choice([90.0, rng.uniform(-20, 90), rng.uniform(10, 70)])
        start = rng.choice([rng.randrange(-7200, 9000), -7200, 3600])
        end = start + rng.choice([0, 1, 3600, 7200, 20000])
        queries.append(
            {
                "id": "q%03d" % i,
                "west": west,
                "east": east,
                "south": south,
                "north": north,
                "start": start,
                "end": end,
            }
        )
    return points, queries, revisions, stale, duplicates


# ---------------------------------------------------------------------------
# Tests


CASES = [(1, 400, 800), (2, 260, 800), (3, 300, 800)]


def test_crossing_queries_match_baseline():
    for points, queries, revisions, stale, duplicates in [
        make_case(seed, count, queries_n) for seed, count, queries_n in CASES
    ]:
        root = new_root("geo-cross-")
        write_json(root / "base.json", {"crs": "EPSG:4326", "points": points})
        write_json(root / "rev.json", {"crs": "EPSG:4326", "points": revisions})
        write_json(root / "stale.json", {"crs": "EPSG:4326", "points": stale})
        write_json(root / "dup.json", {"crs": "EPSG:4326", "points": duplicates})
        write_json(root / "queries.json", {"crs": "EPSG:4326", "queries": queries})

        first = run_ok([str(root), "apply", str(root / "base.json")])
        assert first["version"] == 1, first
        assert first["total_points"] == len(points), first

        second = run_ok([str(root), "apply", str(root / "dup.json")])
        assert second["version"] == 1, "idempotent duplicate input published a new version"
        third = run_ok([str(root), "apply", str(root / "rev.json")])
        fourth = run_ok([str(root), "apply", str(root / "stale.json")])
        assert fourth["version"] == third["version"], "stale replay published a new version"

        # Independent truth from the generator, never from the store under test.
        expected = reduce_points([points, revisions, stale, duplicates])

        items = read_items(root, third["version"])
        assert item_rows(items) == expected, (len(items), len(expected))

        # Every stored bucket must be non-empty: only populated cells exist.
        manifest = resolve_manifest(root, third["version"])
        for bucket_text, content_hash in manifest["buckets"].items():
            assert read_bucket(root, content_hash), bucket_text

        # Second implementation of the same query: a fresh store full of the
        # reduced rows, scanned linearly by the same exact predicates.
        baseline_root = new_root("geo-base-")
        write_json(baseline_root / "all.json", {"crs": "EPSG:4326", "points": expected})
        run_ok([str(baseline_root), "apply", str(baseline_root / "all.json")])
        baseline = run_ok([str(baseline_root), "query", str(root / "queries.json")])

        indexed = run_ok([str(root), "query", str(root / "queries.json")])
        assert indexed["version"] == third["version"], (indexed["version"], third)
        assert len(indexed["results"]) == len(queries)
        non_empty = 0
        crossing_non_empty = 0
        for got, want, query in zip(indexed["results"], baseline["results"], queries):
            assert got["id"] == want["id"] == query["id"]
            assert got["rows"] == want["rows"], (query, got["rows"][:3], want["rows"][:3])

            # Point-by-point truth, plus ordering stability.
            manual = reference_rows(expected, query)
            assert got["rows"] == manual, (query["id"], len(got["rows"]), len(manual))
            keys = [(row["t"], row["device"]) for row in got["rows"]]
            assert keys == sorted(keys)

            # No row may appear twice, especially across a crossing query.
            identities = [(row["device"], row["t"]) for row in got["rows"]]
            assert len(identities) == len(set(identities)), query["id"]

            # Counts are per-query and honest: matched rows are scanned.
            assert got["candidate_blocks"] >= 0
            assert got["scanned_points"] >= len(got["rows"])

            if got["rows"]:
                non_empty += 1
                if is_crossing(query["west"], query["east"]) and any(
                    abs(canon_lon(row["lon"])) >= 170 for row in got["rows"]
                ):
                    crossing_non_empty += 1

        # The corpus must exercise the interesting cases, not just empty ones.
        assert non_empty >= len(queries) // 10, non_empty
        assert crossing_non_empty >= 10, crossing_non_empty
    print("crossing/baseline: ok (%d cases, %d queries each)" % (len(CASES), CASES[0][2]))


def test_duplicate_and_correction_semantics():
    root = new_root("geo-rev-")
    base = [
        {"device": "d1", "t": 100, "rev": 1, "lon": 179.5, "lat": 10, "source": "a"},
        {"device": "d1", "t": 100, "rev": 2, "lon": -179.5, "lat": 10, "source": "b"},
        {"device": "d1", "t": 100, "rev": 2, "lon": -179.5, "lat": 10, "source": "b"},
        {"device": "d1", "t": 100, "rev": 1, "lon": 0, "lat": 0, "source": "old"},
        {"device": "d2", "t": 100, "rev": 1, "lon": 180, "lat": 0, "source": "c"},
        {"device": "d2", "t": 100, "rev": 1, "lon": -180, "lat": 0, "source": "c"},
    ]
    write_json(root / "a.json", {"crs": "EPSG:4326", "points": base})
    out = run_ok([str(root), "apply", str(root / "a.json")])
    assert out["total_points"] == 2, out
    found = read_items(root, out["version"])
    assert found[("d1", 100)]["rev"] == 2 and found[("d1", 100)]["source"] == "b"
    assert found[("d2", 100)]["lon"] == -180, found[("d2", 100)]

    # Identical same-revision duplicate: no effect, still no new version.
    write_json(root / "same.json", {"crs": "EPSG:4326", "points": [base[1], base[4]]})
    out2 = run_ok([str(root), "apply", str(root / "same.json")])
    assert out2 == {"version": 1, "total_points": 2}, out2

    # Conflicting same-revision content is rejected with exit 2.
    write_json(
        root / "conflict.json",
        {
            "crs": "EPSG:4326",
            "points": [{"device": "d1", "t": 100, "rev": 2, "lon": 0, "lat": 0, "source": "x"}],
        },
    )
    proc = run_cli([str(root), "apply", str(root / "conflict.json")])
    assert proc.returncode == 2, proc.returncode
    assert "points[0]" in proc.stderr and "conflict" in proc.stderr, proc.stderr
    # The rejected apply must not have published or altered anything.
    assert json.loads((root / "head.json").read_text())["version"] == 1
    print("revision semantics: ok")


def test_crash_preserves_old_version():
    root = new_root("geo-crash-")
    write_json(
        root / "v1.json",
        {"crs": "EPSG:4326", "points": [{"device": "d", "t": 5, "rev": 1, "lon": 179.9, "lat": 0, "source": "s"}]},
    )
    first = run_ok([str(root), "apply", str(root / "v1.json")])
    assert first["version"] == 1
    write_json(
        root / "v2.json",
        {"crs": "EPSG:4326", "points": [{"device": "d", "t": 3605, "rev": 1, "lon": -179.9, "lat": 0, "source": "s"}]},
    )
    buckets_before = sorted(os.listdir(str(root / "buckets")))
    proc = run_cli([str(root), "apply", str(root / "v2.json"), "--crash-before-publish"])
    assert proc.returncode == 97, proc.returncode
    head = json.loads((root / "head.json").read_text())
    assert head["version"] == 1, head
    assert not (root / "versions" / "v2.json").exists(), os.listdir(str(root / "versions"))
    # The crashed apply published no bucket payload either.
    assert sorted(os.listdir(str(root / "buckets"))) == buckets_before
    # Replaying normally completes publication.
    again = run_ok([str(root), "apply", str(root / "v2.json")])
    assert again["version"] == 2, again
    print("crash-before-publish: ok")


def test_sparse_region_scan_budget():
    rng = random.Random(7)
    root = new_root("geo-sparse-")
    points = []
    for i in range(4000):
        points.append(
            {
                "device": "d%03d" % (i % 50),
                "t": 3600 + i,
                "rev": 1,
                "lon": rng.uniform(-180, 180),
                "lat": rng.uniform(-90, 90),
                "source": "s",
            }
        )
    write_json(root / "all.json", {"crs": "EPSG:4326", "points": points})
    out = run_ok([str(root), "apply", str(root / "all.json")])
    assert out["total_points"] == len(points)
    # Pick real, isolated sample points so a tiny box is guaranteed non-empty
    # while still covering only a minuscule fraction of the dataset.
    picks = [points[123], points[2500], points[3777]]
    for point in picks:
        assert -170 < point["lon"] < 170, point  # keep boxes off the antimeridian
    tiny = []
    for name, point in zip(["tiny-a", "tiny-b", "tiny-c"], picks):
        # A 0.2 degree box stays inside one or two grid cells.
        tiny.append(
            (
                name,
                point,
                {
                    "id": name,
                    "west": point["lon"] - 0.1,
                    "east": point["lon"] + 0.1,
                    "south": point["lat"] - 0.1,
                    "north": point["lat"] + 0.1,
                    "start": 3600,
                    "end": 3600 + 4000,
                },
            )
        )
    extra = [
        (
            "cross-tiny",
            None,
            {"id": "cross-tiny", "west": 179.8, "east": -179.8, "south": 70, "north": 71, "start": 3600, "end": 3600 + 4000},
        )
    ]
    cases = tiny + extra
    write_json(root / "q.json", {"queries": [query for _, _, query in cases]})
    result = run_ok([str(root), "query", str(root / "q.json")])
    total = len(points)
    for entry, (name, point, query) in zip(result["results"], cases):
        assert entry["id"] == name
        # Sparse-region scans must stay within 5% of the full dataset.
        assert entry["scanned_points"] <= 0.05 * total, (name, entry["scanned_points"], total)
        if point is not None:
            assert entry["candidate_blocks"] > 0, entry
            assert any(
                row["device"] == point["device"] and row["t"] == point["t"] for row in entry["rows"]
            ), entry
        manual = reference_rows(item_rows(read_items(root, out["version"]), ), query)
        assert entry["rows"] == manual, (name, len(entry["rows"]), len(manual))
    print("sparse scan budget: ok")


def test_invalid_inputs_point_at_field_and_index():
    root = new_root("geo-bad-")
    cases = [
        ({"crs": "EPSG:4326", "points": [{"device": "d", "t": 1, "rev": 1, "lon": float("nan"), "lat": 0, "source": "s"}]}, "points[0].lon"),
        ({"crs": "EPSG:4326", "points": [{"device": "d", "t": 1, "rev": 1, "lon": 181, "lat": 0, "source": "s"}]}, "points[0].lon"),
        ({"crs": "EPSG:4326", "points": [{"device": "d", "t": 1, "rev": 1, "lon": 0, "lat": 91, "source": "s"}]}, "points[0].lat"),
        ({"crs": "EPSG:4326", "points": [{"device": "", "t": 1, "rev": 1, "lon": 0, "lat": 0, "source": "s"}]}, "points[0].device"),
        ({"crs": "EPSG:4326", "points": [{"device": "d", "t": 1, "rev": 0, "lon": 0, "lat": 0, "source": "s"}]}, "points[0].rev"),
        ({"crs": "EPSG:3857", "points": [{"device": "d", "t": 1, "rev": 1, "lon": 0, "lat": 0, "source": "s"}]}, "crs"),
        ({"points": [{"device": "d", "t": 1, "rev": 1, "lon": 0, "lat": 0, "source": "s"}]}, "crs"),
        ({"crs": "EPSG:4326", "points": [{"device": "d", "t": 1, "rev": 1, "lon": 0, "lat": 0, "source": "s"}, {"device": "e", "t": 2, "rev": 1, "lon": 0, "lat": float("inf"), "source": "s"}]}, "points[1].lat"),
    ]
    for payload, marker in cases:
        write_json(root / "bad.json", payload)
        proc = run_cli([str(root), "apply", str(root / "bad.json")])
        assert proc.returncode == 2, (payload, proc.returncode, proc.stderr)
        assert marker in proc.stderr, (marker, proc.stderr)
    write_json(root / "empty.json", {"crs": "EPSG:4326", "points": []})
    out = run_ok([str(root), "apply", str(root / "empty.json")])
    assert out["version"] == 0, out

    write_json(
        root / "qbad.json",
        {"queries": [{"id": "ok", "west": 0, "east": 1, "south": 0, "north": 1, "start": 0, "end": 1},
                     {"id": "bad", "west": 0, "east": 361, "south": 0, "north": 1, "start": 0, "end": 1}]},
    )
    proc = run_cli([str(root), "query", str(root / "qbad.json")])
    assert proc.returncode == 2 and "queries[1].east" in proc.stderr, proc.stderr

    # Unknown / unpublished versions are locatable errors, not silent empties.
    good = {"queries": [{"id": "ok", "west": 0, "east": 1, "south": 0, "north": 1, "start": 0, "end": 1}]}
    write_json(root / "qok.json", good)
    proc = run_cli([str(root), "query", str(root / "qok.json"), "--version", "0"])
    assert proc.returncode == 0 and json.loads(proc.stdout)["version"] == 0, proc
    proc = run_cli([str(root), "query", str(root / "qok.json"), "--version", "7"])
    assert proc.returncode == 2 and "version 7" in proc.stderr, proc.stderr
    proc = run_cli([str(root), "query", str(root / "qok.json"), "--version", "-1"])
    assert proc.returncode == 2, proc.returncode
    print("invalid input errors: ok")


def test_old_snapshot_and_new_version_truth():
    rng = random.Random(11)
    root = new_root("geo-versions-")
    rows = [
        {
            "device": "d%02d" % (i % 20),
            "t": 3600 + i * 3,
            "rev": 1,
            "lon": rng.uniform(-180, 180),
            "lat": rng.uniform(-80, 80),
            "source": "v1-%d" % i,
        }
        for i in range(300)
    ]
    write_json(root / "v1.json", {"crs": "EPSG:4326", "points": rows})
    v1 = run_ok([str(root), "apply", str(root / "v1.json")])
    assert v1["version"] == 1

    late_rows = [
        {
            "device": "late%02d" % i,
            "t": 3600,
            "rev": 1,
            "lon": rng.uniform(170, 180) if i % 2 else rng.uniform(-180, -170),
            "lat": 10,
            "source": "late-%d" % i,
        }
        for i in range(40)
    ]
    corrections = [
        {
            "device": rows[i]["device"],
            "t": rows[i]["t"],
            "rev": 2,
            "lon": rng.uniform(-180, 180),
            "lat": rows[i]["lat"],
            "source": "corr-%d" % i,
        }
        for i in range(0, 300, 10)
    ]
    write_json(root / "v2.json", {"crs": "EPSG:4326", "points": late_rows + corrections})
    v2 = run_ok([str(root), "apply", str(root / "v2.json")])
    assert v2["version"] == 2, v2

    queries = [
        {"id": "cross", "west": 170, "east": -170, "south": 5, "north": 15, "start": 3600, "end": 7200},
        {"id": "all", "west": -180, "east": 180, "south": -90, "north": 90, "start": 3600, "end": 7200},
    ]
    write_json(root / "q.json", {"queries": queries})

    current = run_ok([str(root), "query", str(root / "q.json")])
    old = run_ok([str(root), "query", str(root / "q.json"), "--version", "1"])
    assert current["version"] == 2 and old["version"] == 1

    for snap_version, result in [(2, current), (1, old)]:
        snap_items = item_rows(read_items(root, snap_version))
        for got, query in zip(result["results"], queries):
            assert got["rows"] == reference_rows(snap_items, query), (snap_version, query["id"])
    # Late rows must be visible now and must not appear in the old version.
    crossing_new = current["results"][0]
    assert any(row["device"].startswith("late") for row in crossing_new["rows"])
    crossing_old = old["results"][0]
    assert not any(row["device"].startswith("late") for row in crossing_old["rows"])
    # Corrections are visible only in version 2 with the new source.
    new_by_key = {(row["device"], row["t"]): row for row in current["results"][1]["rows"]}
    for i in range(0, 300, 10):
        key = (rows[i]["device"], rows[i]["t"])
        assert new_by_key[key]["source"] == "corr-%d" % i, new_by_key[key]
    print("version isolation: ok")


def test_head_never_partial_under_interruption():
    """Kill an apply at arbitrary points; head must name a complete snapshot."""
    import signal
    import time

    root = new_root("geo-kill-")
    write_json(root / "v1.json", {"crs": "EPSG:4326", "points": [{"device": "d", "t": 1, "rev": 1, "lon": 0, "lat": 0, "source": "s"}]})
    run_ok([str(root), "apply", str(root / "v1.json")])
    for round_index in range(5):
        payload = {
            "crs": "EPSG:4326",
            "points": [
                {
                    "device": "k%d_%03d" % (round_index, i),
                    "t": 3600,
                    "rev": 1,
                    "lon": (179.9 if i % 2 else -179.9),
                    "lat": 0,
                    "source": "s",
                }
                for i in range(2000)
            ],
        }
        write_json(root / "big.json", payload)
        proc = subprocess.Popen(
            [sys.executable, str(GEO), str(root), "apply", str(root / "big.json")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.05 + 0.05 * round_index)
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        head = json.loads((root / "head.json").read_text())
        assert (root / "versions" / ("v%d.json" % head["version"])).exists(), head
        manifest = resolve_manifest(root, head["version"])
        assert manifest["version"] == head["version"]
        # Every bucket referenced by the live manifest must be present,
        # hash-valid, and add up to the manifest's point count.
        items = read_items(root, head["version"])
        assert len(items) == manifest["total_points"]
    # A subsequent normal apply still completes.
    final = run_ok([str(root), "apply", str(root / "big.json")])
    assert final["version"] >= 2
    print("interrupted publication safety: ok")


def test_endpoints_full_circle_and_meridian_arcs():
    root = new_root("geo-edges-")
    points = [
        {"device": "m180", "t": 1, "rev": 1, "lon": 180, "lat": 90, "source": "edge"},
        {"device": "mn180", "t": 1, "rev": 1, "lon": -180, "lat": -90, "source": "edge"},
        {"device": "zero", "t": -1, "rev": 1, "lon": 0, "lat": 0, "source": "edge"},
        {"device": "near", "t": 2, "rev": 1, "lon": 179.999, "lat": 89.999, "source": "edge"},
        {"device": "far", "t": 3, "rev": 1, "lon": -179.999, "lat": -89.999, "source": "edge"},
    ]
    write_json(root / "all.json", {"crs": "EPSG:4326", "points": points})
    applied = run_ok([str(root), "apply", str(root / "all.json")])
    assert applied["total_points"] == 5

    # +180 must be stored canonically as -180 without losing its identity.
    items = read_items(root, applied["version"])
    assert items[("m180", 1)]["lon"] == -180, items[("m180", 1)]
    assert items[("m180", 1)]["lat"] == 90

    queries = [
        {"id": "full", "west": -180, "east": 180, "south": -90, "north": 90, "start": -5, "end": 5},
        {"id": "meridian180", "west": 180, "east": 180, "south": -90, "north": 90, "start": -5, "end": 5},
        {"id": "meridian-180", "west": -180, "east": -180, "south": -90, "north": 90, "start": -5, "end": 5},
        {"id": "meridian0", "west": 0, "east": 0, "south": -90, "north": 90, "start": -5, "end": 5},
        {"id": "cross", "west": 179.9, "east": -179.9, "south": -90, "north": 90, "start": -5, "end": 5},
        {"id": "cross-other", "west": -179.9, "east": 179.9, "south": -90, "north": 90, "start": -5, "end": 5},
        {"id": "corner", "west": 179.999, "east": 179.999, "south": 89.999, "north": 89.999, "start": 2, "end": 3},
        {"id": "empty-time", "west": -180, "east": 180, "south": -90, "north": 90, "start": 1, "end": 1},
        {"id": "reversed-lat", "west": -180, "east": 180, "south": 10, "north": -10, "start": -5, "end": 5},
    ]
    write_json(root / "q.json", {"queries": queries})
    result = run_ok([str(root), "query", str(root / "q.json")])
    by_id = {entry["id"]: entry for entry in result["results"]}

    names = lambda entry: [row["device"] for row in entry["rows"]]
    full = item_rows(items)
    assert by_id["full"]["rows"] == full
    # A meridian query at +180 and at -180 is the same one-meridian arc.
    assert names(by_id["meridian180"]) == ["m180", "mn180"]
    assert names(by_id["meridian-180"]) == ["m180", "mn180"]
    assert names(by_id["meridian0"]) == ["zero"]
    # Crossing arc [179.9, 180) U [-180, -179.9] hits the near/far pair.
    assert names(by_id["cross"]) == ["m180", "mn180", "near", "far"]
    # The complementary arc is the ordinary closed interval [-179.9, 179.9];
    # the +-180 and +-179.999 points all fall outside it.
    assert names(by_id["cross-other"]) == ["zero"]
    assert names(by_id["corner"]) == ["near"]
    assert by_id["empty-time"]["rows"] == []
    assert by_id["reversed-lat"]["rows"] == []
    for entry in result["results"]:
        keys = [(row["t"], row["device"]) for row in entry["rows"]]
        assert keys == sorted(keys)
    print("endpoint/arc edges: ok")


def test_interrupted_update_with_old_reader():
    """Crash an update while a reader pins version 1; both truths hold."""
    import signal
    import time

    rng = random.Random(19)
    root = new_root("geo-mixed-")
    v1_points = [
        {
            "device": "d%02d" % (i % 25),
            "t": 3600 + i * 2,
            "rev": 1,
            "lon": (179.6 + (i % 400) / 1000.0) if i % 2 else (-179.6 - (i % 400) / 1000.0),
            "lat": rng.uniform(-20, 20),
            "source": "v1-%d" % i,
        }
        for i in range(500)
    ]
    write_json(root / "v1.json", {"crs": "EPSG:4326", "points": v1_points})
    first = run_ok([str(root), "apply", str(root / "v1.json")])
    assert first["version"] == 1

    late = [
        {
            "device": "late%03d" % i,
            "t": 3600,
            "rev": 1,
            "lon": rng.choice([rng.uniform(179, 180), rng.uniform(-180, -179)]),
            "lat": rng.uniform(-5, 5),
            "source": "late-%d" % i,
        }
        for i in range(3000)
    ]
    write_json(root / "v2.json", {"crs": "EPSG:4326", "points": late})

    queries = [
        {"id": "cross", "west": 179, "east": -179, "south": -10, "north": 10, "start": 3600, "end": 5000},
        {"id": "wide", "west": -180, "east": 180, "south": -30, "north": 30, "start": 3600, "end": 5000},
    ]
    write_json(root / "q.json", {"queries": queries})

    pinned = run_ok([str(root), "query", str(root / "q.json"), "--version", "1"])
    v1_truth = item_rows(read_items(root, 1))

    proc = subprocess.Popen(
        [sys.executable, str(GEO), str(root), "apply", str(root / "v2.json")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.08)
    proc.send_signal(signal.SIGKILL)
    proc.wait()

    # Head still points at a complete version; the crashed apply published
    # nothing new.
    head = json.loads((root / "head.json").read_text())
    assert head["version"] == 1, head
    assert not (root / "versions" / "v2.json").exists()

    # The pinned reader still sees exactly the version-1 truth.
    still = run_ok([str(root), "query", str(root / "q.json"), "--version", "1"])
    assert still["version"] == 1
    for got, query in zip(still["results"], queries):
        assert got["rows"] == reference_rows(v1_truth, query), query["id"]
    assert still["results"] == pinned["results"]

    # Replay completes publication; version 2 sees the late rows, version 1
    # never does, and both match their own independent truth.
    replay = run_ok([str(root), "apply", str(root / "v2.json")])
    assert replay["version"] == 2, replay
    v2_truth = item_rows(read_items(root, 2))
    new = run_ok([str(root), "query", str(root / "q.json")])
    old = run_ok([str(root), "query", str(root / "q.json"), "--version", "1"])
    assert new["version"] == 2 and old["version"] == 1
    for got, query in zip(new["results"], queries):
        assert got["rows"] == reference_rows(v2_truth, query), query["id"]
    for got, query in zip(old["results"], queries):
        assert got["rows"] == reference_rows(v1_truth, query), query["id"]
    crossing_new = new["results"][0]
    crossing_old = old["results"][0]
    assert any(row["device"].startswith("late") for row in crossing_new["rows"])
    assert not any(row["device"].startswith("late") for row in crossing_old["rows"])
    print("interrupted update + old reader: ok")


def test_incremental_apply_touches_only_changed_buckets():
    """A late point must not rewrite unrelated buckets."""
    root = new_root("geo-incr-")
    rng = random.Random(23)
    base = [
        {
            "device": "d%03d" % i,
            "t": 3600 + i,
            "rev": 1,
            "lon": rng.uniform(-180, 180),
            "lat": rng.uniform(-90, 90),
            "source": "base",
        }
        for i in range(500)
    ]
    write_json(root / "base.json", {"crs": "EPSG:4326", "points": base})
    v1 = run_ok([str(root), "apply", str(root / "base.json")])
    assert v1["version"] == 1
    manifest1 = resolve_manifest(root, 1)
    bucket_files1 = {
        name[: -len(".json")]: os.stat(str(root / "buckets" / name)).st_mtime_ns
        for name in os.listdir(str(root / "buckets"))
    }

    # One late point in a far-away hour/region, plus one correction that
    # moves a record between buckets.
    late = {"device": "late", "t": 3600 + 9 * 3600, "rev": 1, "lon": 10.05, "lat": -10.05, "source": "late"}
    moved_target = base[7]
    correction = {
        "device": moved_target["device"],
        "t": moved_target["t"],
        "rev": 2,
        "lon": -moved_target["lon"],
        "lat": -moved_target["lat"],
        "source": "moved",
    }
    write_json(root / "late.json", {"crs": "EPSG:4326", "points": [late, correction]})
    v2 = run_ok([str(root), "apply", str(root / "late.json")])
    assert v2["version"] == 2 and v2["total_points"] == 501, v2

    manifest2 = resolve_manifest(root, 2)
    # Unchanged buckets keep their exact old content hash.
    shared = set(manifest1["buckets"].values()) & set(manifest2["buckets"].values())
    changed_old = set(manifest1["buckets"].values()) - shared
    changed_new = set(manifest2["buckets"].values()) - shared
    # A late insert plus a move touches very few distinct buckets.
    assert len(changed_old) <= 4, len(changed_old)
    assert len(changed_new) <= 4, len(changed_new)
    # The many unrelated payload files were neither rewritten nor deleted.
    for content_hash, mtime in bucket_files1.items():
        assert (root / "buckets" / ("%s.json" % content_hash)).exists()
        if content_hash in shared:
            assert os.stat(str(root / "buckets" / ("%s.json" % content_hash))).st_mtime_ns == mtime

    # Data is still exactly right after the incremental update.
    items = read_items(root, 2)
    assert items[("late", late["t"])] == {
        "device": "late", "t": late["t"], "rev": 1, "lon": 10.05, "lat": -10.05, "source": "late"
    }
    moved = items[(moved_target["device"], moved_target["t"])]
    assert moved["rev"] == 2 and moved["source"] == "moved"
    assert moved["lon"] == -moved_target["lon"] and moved["lat"] == -moved_target["lat"]
    assert len(items) == 501

    # A narrow query reads only the buckets its boxes intersect; the count
    # it reports equals the payloads its scan really needed.
    queries = [
        {"id": "late-box", "west": 10, "east": 10.1, "south": -10.1, "north": -10,
         "start": late["t"], "end": late["t"] + 10},
    ]
    write_json(root / "q.json", {"queries": queries})
    result = run_ok([str(root), "query", str(root / "q.json")])
    entry = result["results"][0]
    assert [row["device"] for row in entry["rows"]] == ["late"], entry
    assert 1 <= entry["candidate_blocks"] <= 2, entry
    assert entry["scanned_points"] <= 5, entry
    # Nowhere near the 501 stored points.
    assert entry["scanned_points"] < 0.05 * 501
    print("incremental bucket writes: ok")


def test_pruning_follows_query_area_not_store_size():
    """The same tiny box must read the same blocks regardless of store size."""
    def build(root, count, seed):
        rng = random.Random(seed)
        points = []
        for i in range(count):
            if i % 2 == 0:
                # Scatter unrelated rows across the globe and many hours.
                lon = rng.uniform(-150, 150)
                lat = rng.uniform(-80, 80)
                t = rng.randrange(0, 40) * 3600 + rng.randrange(0, 3600)
            else:
                # Keep a dense cluster away from the probe box too.
                lon = rng.uniform(60, 120)
                lat = rng.uniform(-40, 40)
                t = 7200 + rng.randrange(0, 30)
            points.append({"device": "s%05d" % i, "t": t, "rev": 1, "lon": lon, "lat": lat, "source": "s"})
        # One known probe point in a fixed spot.
        points.append({"device": "probe", "t": 10, "rev": 1, "lon": 1.25, "lat": 2.15, "source": "probe"})
        write_json(root / "all.json", {"crs": "EPSG:4326", "points": points})
        applied = run_ok([str(root), "apply", str(root / "all.json")])
        query = {
            "id": "probe", "west": 1.0, "east": 1.5, "south": 2.0, "north": 2.5,
            "start": 0, "end": 3600,
        }
        write_json(root / "q.json", {"queries": [query]})
        result = run_ok([str(root), "query", str(root / "q.json")])
        entry = result["results"][0]
        assert [row["device"] for row in entry["rows"]] == ["probe"], entry
        return applied["total_points"], entry["candidate_blocks"], entry["scanned_points"]

    small = new_root("geo-scale-small-")
    large = new_root("geo-scale-large-")
    total_small, blocks_small, scan_small = build(small, 1200, 31)
    total_large, blocks_large, scan_large = build(large, 24000, 32)
    assert total_large > 10 * total_small
    # Because the probe box is ~0.5x0.5 degrees, it spans a handful of cells;
    # the count must be bounded by that, not by the 20x larger store.
    assert blocks_small == blocks_large, (blocks_small, blocks_large)
    assert blocks_large <= 9, blocks_large
    for scanned, total in [(scan_small, total_small), (scan_large, total_large)]:
        assert scanned <= max(3, 0.02 * total), (scanned, total)

    # A crossing box at the antimeridian must read only its two longitude
    # fringes, never the dense mid-longitude cluster.
    cross_root = new_root("geo-scale-cross-")
    rng = random.Random(33)
    pts = []
    for i in range(4000):
        lon = rng.uniform(-150, 150)
        lat = rng.uniform(-10, 10)
        pts.append({"device": "mid%04d" % i, "t": 3600, "rev": 1, "lon": lon, "lat": lat, "source": "mid"})
    for i in range(40):
        lon = 179.95 if i % 2 else -179.95
        pts.append({"device": "edge%02d" % i, "t": 3600 + i, "rev": 1, "lon": lon, "lat": 0, "source": "edge"})
    write_json(cross_root / "all.json", {"crs": "EPSG:4326", "points": pts})
    run_ok([str(cross_root), "apply", str(cross_root / "all.json")])
    cross_query = {"id": "cross", "west": 179.5, "east": -179.5, "south": -1, "north": 1, "start": 3600, "end": 3700}
    write_json(cross_root / "q.json", {"queries": [cross_query]})
    entry = run_ok([str(cross_root), "query", str(cross_root / "q.json")])["results"][0]
    assert all(row["source"] == "edge" for row in entry["rows"]), entry["rows"][:2]
    assert len(entry["rows"]) == 40, len(entry["rows"])
    assert entry["scanned_points"] <= 60, entry
    assert entry["candidate_blocks"] <= 4, entry
    print("pruning scales with area: ok")


def test_corrupt_payload_is_locatable_error():
    root = new_root("geo-corrupt-")
    write_json(
        root / "all.json",
        {"crs": "EPSG:4326", "points": [
            {"device": "d", "t": 10, "rev": 1, "lon": 179.5, "lat": 1, "source": "s"},
            {"device": "e", "t": 11, "rev": 1, "lon": -179.5, "lat": 1, "source": "s"},
        ]},
    )
    applied = run_ok([str(root), "apply", str(root / "all.json")])
    manifest = resolve_manifest(root, applied["version"])
    target_hash = next(iter(manifest["buckets"].values()))
    path = root / "buckets" / ("%s.json" % target_hash)

    # Content hash mismatch must be reported, never silently accepted.
    original = path.read_text()
    path.write_text(original.replace('"rev":1', '"rev":9'))
    write_json(root / "q.json", {"queries": [
        {"id": "all", "west": -180, "east": 180, "south": -90, "north": 90, "start": 0, "end": 100},
    ]})
    proc = run_cli([str(root), "query", str(root / "q.json")])
    assert proc.returncode == 2, proc.returncode
    assert target_hash in proc.stderr and "content hash" in proc.stderr, proc.stderr
    path.write_text(original)

    # A payload that hashes correctly but sits in the wrong bucket is also
    # rejected rather than returning rows from the wrong partition.
    other_hash = manifest["buckets"][
        next(key for key in manifest["buckets"]
             if manifest["buckets"][key] != target_hash)
    ]
    other_path = root / "buckets" / ("%s.json" % other_hash)
    wrong = read_bucket(root, other_hash)
    moved = dict(wrong[0])
    moved["lon"] = 0.0
    moved["lat"] = 0.0
    moved_text = json.dumps([moved], sort_keys=True, separators=(",", ":"))
    # Write the mis-placed payload under its own correct hash.
    import hashlib
    wrong_hash = hashlib.sha256(moved_text.encode("utf-8")).hexdigest()
    (root / "buckets" / ("%s.json" % wrong_hash)).write_text(moved_text + "\n")
    tampered = json.loads((root / "versions" / ("v%d.json" % applied["version"])).read_text())
    # Version 1 is a full checkpoint, so its bucket map lives under "buckets".
    for bucket_text, content_hash in list(tampered["buckets"].items()):
        if content_hash == other_hash:
            tampered["buckets"][bucket_text] = wrong_hash
    (root / "versions" / ("v%d.json" % applied["version"])).write_text(json.dumps(tampered))
    proc = run_cli([str(root), "query", str(root / "q.json")])
    assert proc.returncode == 2, proc.returncode
    assert "belongs in" in proc.stderr, proc.stderr
    print("corrupt payload handling: ok")


def test_delta_manifests_and_checkpoints():
    """Each apply must publish a small delta; checkpoints bound the chain."""
    from geo import CHECKPOINT_INTERVAL

    root = new_root("geo-delta-")
    rng = random.Random(41)
    base = [
        {
            "device": "d%03d" % i,
            "t": 3600 + i,
            "rev": 1,
            "lon": rng.uniform(-179, 179),
            "lat": rng.uniform(-80, 80),
            "source": "base",
        }
        for i in range(600)
    ]
    write_json(root / "base.json", {"crs": "EPSG:4326", "points": base})
    v1 = run_ok([str(root), "apply", str(root / "base.json")])
    assert v1["version"] == 1
    first_bytes = (root / "versions" / "v1.json").stat().st_size
    history = {1: item_rows(read_items(root, 1))}

    version = 1
    delta_sizes = []
    kinds = {}
    rounds = CHECKPOINT_INTERVAL + 3
    for step in range(rounds):
        # A handful of corrections and one late arrival per apply.
        corrections = []
        for i in range(3):
            target = base[(step * 7 + i * 13) % len(base)]
            corrections.append(
                {
                    "device": target["device"],
                    "t": target["t"],
                    "rev": 2 + step,
                    "lon": rng.uniform(-179, 179),
                    "lat": target["lat"],
                    "source": "corr-%d-%d" % (step, i),
                }
            )
        late = {
            "device": "late-%02d" % step,
            "t": 3600,
            "rev": 1,
            "lon": rng.uniform(179, 180) if step % 2 else rng.uniform(-180, -179),
            "lat": rng.uniform(-5, 5),
            "source": "late",
        }
        write_json(root / "step.json", {"crs": "EPSG:4326", "points": corrections + [late]})
        out = run_ok([str(root), "apply", str(root / "step.json")])
        version = out["version"]
        assert version == step + 2
        delta_sizes.append((root / "versions" / ("v%d.json" % version)).stat().st_size)
        kinds[version] = "full" if read_manifest(root, version).get("full") is True else "delta"
        history[version] = item_rows(read_items(root, version))

    # Ordinary incremental versions publish deltas far smaller than a
    # checkpoint holding every key and bucket.
    delta_only = [delta_sizes[n - 2] for n in kinds if kinds[n] == "delta"]
    full_only = [delta_sizes[n - 2] for n in kinds if kinds[n] == "full"]
    assert delta_only and full_only, (kinds, delta_sizes)
    assert max(delta_only) < first_bytes / 2, delta_only
    assert min(full_only) > max(delta_only) * 10, (full_only, delta_only)

    # A checkpoint must appear within CHECKPOINT_INTERVAL versions.
    checkpoint_versions = [
        n for n in range(1, version + 1)
        if read_manifest(root, n).get("full") is True
    ]
    assert 1 in checkpoint_versions
    gaps = [b - a for a, b in zip(checkpoint_versions, checkpoint_versions[1:])]
    assert all(gap <= CHECKPOINT_INTERVAL for gap in gaps), (checkpoint_versions, gaps)

    # Every historical version still resolves to its own exact truth, and
    # reads the same whether it is a checkpoint or a delta.
    probes = sorted({1, 2, version // 2, version - 1, version})
    queries = [
        {"id": "cross", "west": 179, "east": -179, "south": -90, "north": 90, "start": 3600, "end": 3700},
        {"id": "all", "west": -180, "east": 180, "south": -90, "north": 90, "start": 0, "end": 99999},
    ]
    write_json(root / "q.json", {"queries": queries})
    for probe in probes:
        result = run_ok([str(root), "query", str(root / "q.json"), "--version", str(probe)])
        assert result["version"] == probe
        truth = history[probe]
        for got, query in zip(result["results"], queries):
            assert got["rows"] == reference_rows(truth, query), (probe, query["id"])

    # Late rows accumulate over time and never appear in older versions.
    newest = history[version]
    assert sum(1 for row in newest if row["device"].startswith("late")) == rounds
    assert not any(row["device"].startswith("late") for row in history[1])
    print("delta manifests/checkpoints: ok")


def main():
    tests = [
        test_crossing_queries_match_baseline,
        test_duplicate_and_correction_semantics,
        test_crash_preserves_old_version,
        test_sparse_region_scan_budget,
        test_invalid_inputs_point_at_field_and_index,
        test_old_snapshot_and_new_version_truth,
        test_head_never_partial_under_interruption,
        test_endpoints_full_circle_and_meridian_arcs,
        test_interrupted_update_with_old_reader,
        test_incremental_apply_touches_only_changed_buckets,
        test_pruning_follows_query_area_not_store_size,
        test_corrupt_payload_is_locatable_error,
        test_delta_manifests_and_checkpoints,
    ]
    try:
        for test in tests:
            test()
    finally:
        for root in _TEMP_ROOTS:
            shutil.rmtree(str(root), ignore_errors=True)
    print("all %d test groups passed" % len(tests))


if __name__ == "__main__":
    main()
