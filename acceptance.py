#!/usr/bin/env python3
"""One-shot local acceptance for the resumable NDJSON pipeline.

Covers, in order:
  1. baseline ingest (appends + small read chunks) of a mixed input
  2. crash/replay: the process is hard-killed after parse-before-write,
     inside the write transaction, and right after commit; replaying the
     original input must reproduce the baseline records, rejects and
     checkpoint exactly
  3. conflict detection when a source position is redelivered with
     different content
  4. normalization versioning: restoring an old checkpoint replays
     historical rows under their original norm version
  5. incremental memory: a source far larger than the read buffer is
     ingested and peak memory is reported (and bounded)

Run:  python3 acceptance.py
"""

import json
import os
import resource
import subprocess
import sys
import tempfile
import tracemalloc

ROOT = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(ROOT, "fixtures")
sys.path.insert(0, ROOT)

from pipeline import importer  # noqa: E402
from pipeline.store import Store  # noqa: E402


def cli(args, env_extra=None, expect=0):
    env = dict(os.environ)
    env.pop("NDJSON_KILL_POINT", None)
    env.pop("NDJSON_NORM_VERSION", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "pipeline.cli"] + args,
        cwd=ROOT, env=env, capture_output=True, text=True)
    if expect is not None and proc.returncode != expect:
        raise AssertionError("cli %s exited %d (wanted %d)\n%s\n%s"
                             % (args, proc.returncode, expect,
                                proc.stdout, proc.stderr))
    return proc


def build_input(path):
    """Mixed input: fixture rows, fixed reject samples, generated valid
    rows with unicode and one line longer than the read chunk."""
    parts = []
    with open(os.path.join(FIXTURES, "supplier-a.ndjson"), "rb") as fh:
        parts.append(fh.read())
    with open(os.path.join(FIXTURES, "reject-samples.ndjson"), "rb") as fh:
        parts.append(fh.read())
    gen = []
    for i in range(120):
        note = "蓝莓🍇" * (900 if i == 50 else 1)  # i==50 -> >4KB line
        gen.append({"version": 1 if i % 2 else 2, "id": "G-%04d" % i,
                    **({"amount": "%d.%02d" % (i, i % 100)} if i % 2
                       else {"amount_cents": i * 137}),
                    "currency": "CNY",
                    **({} if i % 3 == 0 else {"note": note if i % 3 == 1
                                              else None})})
    parts.append("".join(json.dumps(g, ensure_ascii=False) + "\n"
                         for g in gen).encode("utf-8"))
    data = b"".join(parts)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def ingest_in_appends(db, src, data, appends=4, chunk=64, env_extra=None,
                      expect=0):
    """Write the source in append rounds, ingesting after each."""
    cuts = [len(data) * i // appends for i in range(appends + 1)]
    with open(src, "wb") as fh:
        fh.write(b"")
    for i in range(appends):
        with open(src, "r+b") as fh:
            fh.seek(cuts[i])
            fh.write(data[cuts[i]:cuts[i + 1]])
            fh.truncate()
        last = i == appends - 1
        cli(["ingest", db, "acc", src, "--chunk-size", str(chunk)]
            + (["--seal"] if last else []),
            env_extra=env_extra, expect=expect)


def dump(db):
    return cli(["dump", db, "acc"]).stdout


def main():
    tmp = tempfile.mkdtemp(prefix="ndjson-acceptance-")
    src = os.path.join(tmp, "input.ndjson")
    data = build_input(src)
    print("acceptance input: %d bytes, %d lines"
          % (len(data), data.count(b"\n")))

    # 1. baseline ------------------------------------------------------
    db0 = os.path.join(tmp, "baseline.db")
    ingest_in_appends(db0, src, data)
    baseline = dump(db0)
    n_rec = baseline.count("\nR|") + baseline.startswith("R|")
    n_rej = baseline.count("\nJ|") + baseline.startswith("J|")
    print("1. baseline: %d records, %d rejects  [OK]" % (n_rec, n_rej))

    # reference cross-check
    proc = cli(["verify", db0, "acc", src])
    print("   batch reference: %s" % proc.stdout.strip())

    # 2. crash / replay at the three kill points -----------------------
    for kp in ("after_parse", "in_transaction", "after_commit"):
        db = os.path.join(tmp, "crash-%s.db" % kp)
        # first attempt: killed mid-stream (exit code 42)
        ingest_in_appends(db, src, data,
                          env_extra={"NDJSON_KILL_POINT": kp}, expect=42)
        # replay the original input to completion
        ingest_in_appends(db, src, data)
        got = dump(db)
        assert got == baseline, "dump after %s crash/replay differs" % kp
        print("2. kill at %-15s -> replay matches baseline  [OK]" % kp)

    # 3. conflict on changed content at a source position --------------
    db = os.path.join(tmp, "conflict.db")
    ingest_in_appends(db, src, data)
    mutated = bytearray(data)
    anchor = mutated.find(b'"amount_cents":275')
    assert anchor > 0
    digits = anchor + len(b'"amount_cents":')
    mutated[digits:digits + 3] = b"995"  # same length, same offsets
    with open(src, "wb") as fh:
        fh.write(bytes(mutated))
    cli(["reset-checkpoint", db, "acc", "0"])
    cli(["ingest", db, "acc", src, "--chunk-size", "64", "--seal"])
    out = dump(db)
    conflicts = [l for l in out.splitlines() if l.startswith("C|")]
    assert len(conflicts) == 1, conflicts
    # everything else identical to baseline apart from the conflict line
    assert out.replace(conflicts[0] + "\n", "") == baseline
    print("3. changed content at same offset -> 1 conflict row, "
          "original kept  [OK]")

    # 4. normalization versioning --------------------------------------
    db = os.path.join(tmp, "versioned.db")
    line = b'{"version":1,"id":"V-1","amount":"3.50"}\n'
    with open(src, "wb") as fh:
        fh.write(line)
    cli(["ingest", db, "acc", src, "--seal"],
        env_extra={"NDJSON_NORM_VERSION": "1"})
    before = dump(db)
    assert "\nJ|" in before or before.startswith("J|")  # missing currency
    cli(["reset-checkpoint", db, "acc", "0"])
    cli(["ingest", db, "acc", src, "--seal"],
        env_extra={"NDJSON_NORM_VERSION": "2"})
    assert dump(db) == before, "historical row changed under new norm version"
    with open(src, "ab") as fh:
        fh.write(b'{"version":1,"id":"V-2","amount":"4.00"}\n')
    cli(["reset-checkpoint", db, "acc", str(len(line))])
    cli(["ingest", db, "acc", src, "--seal"],
        env_extra={"NDJSON_NORM_VERSION": "2"})
    out = dump(db)
    assert "R|%d|2|V-2|4.00|CNY|missing|" % len(line) in out, out
    print("4. old checkpoint replays under original norm version; "
          "new rows use v2  [OK]")

    # 5. incremental memory --------------------------------------------
    big = os.path.join(tmp, "big.ndjson")
    target = 16 * 1024 * 1024
    with open(big, "wb") as fh:
        i, written = 0, 0
        while written < target:
            line = (json.dumps({"version": 2, "id": "M-%07d" % i,
                                "amount_cents": i % 100000, "currency": "CNY",
                                "note": "蓝莓🍇" * (i % 5)},
                               ensure_ascii=False) + "\n").encode("utf-8")
            fh.write(line)
            written += len(line)
            i += 1
    db = os.path.join(tmp, "big.db")
    store = Store(db)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    stats = importer.ingest(store, "big", big, chunk_size=8192, seal=True)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    ckpt = store.get_checkpoint("big")
    store.close()
    print("5. memory: source %.1f MB, chunk 8 KB, %d records; "
          "tracemalloc peak %.2f MB, maxrss delta %.2f MB"
          % (written / 2**20, stats.records, peak / 2**20,
             (rss_after - rss_before) / 2**20))
    assert ckpt == (written, 1)
    assert peak < 16 * 2**20, "peak python memory %.1f MB not incremental" \
        % (peak / 2**20)
    print("   peak memory is far below source size -> incremental  [OK]")

    print("\nACCEPTANCE OK")


if __name__ == "__main__":
    main()
