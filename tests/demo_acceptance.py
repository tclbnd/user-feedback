#!/usr/bin/env python3
"""手工演示验收场景，打印人类可读的逐块状态/告警/吞吐。

    python3 tests/demo_acceptance.py [workdir]
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from make_fixture import gen  # noqa: E402

DL = os.path.join(ROOT, "dl.py")


def run(req, out, conc=None):
    cmd = [sys.executable, "-B", DL, req, out]
    if conc:
        cmd += ["--concurrency", str(conc)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p


def show(out, label):
    meta = json.load(open(out + ".meta.json", encoding="utf-8"))
    print("--- %s" % label)
    print("mode=%s status=%s blocks=%d/%d retries=%d quarantined=%d bytes/s=%.0f"
          % (meta["mode"], meta["status"], meta["stats"]["blocks_done"],
             meta["stats"]["blocks_total"], meta["stats"]["retries"],
             meta["stats"]["quarantined"], meta["stats"]["bytes_per_s"]))
    if meta["alerts"]:
        for a in meta["alerts"]:
            print("  ALERT %s" % a)
    for c in meta["chunks"]:
        flag = " RETRY(%s)" % ",".join(c["reasons"]) if c["retried"] else ""
        print("  block %2d [%7d,%7d) %-6s %s%s"
              % (c["i"], c["offset"], c["offset"] + c["length"] - 1,
                 c["status"], (c["sha"] or "-")[:12], flag))
    return meta


def scenario_a(base):
    print("=" * 78)
    print("A) 3 处断流 + 2 个字节翻转块，最终逐字节一致")
    print("=" * 78)
    size, chunk = 3 * 1024 * 1024 + 777, 256 * 1024
    d = os.path.join(base, "A")
    data = gen(d, size, chunk, 4, fail_at=[0.3, 0.62, 0.87], corrupt_chunks=[3, 9])
    req, out = os.path.join(d, "req.json"), os.path.join(d, "out.bin")
    codes = []
    for _ in range(10):
        code = run(req, out).returncode
        codes.append(code)
        if code == 0:
            break
    print("exit codes:", codes)
    same = open(out, "rb").read() == data
    meta = show(out, "final")
    print("byte-identical:", same)
    print("cut runs:", codes.count(3), "| quarantined blocks:",
          sorted({c["i"] for c in meta["chunks"] if c["quarantined"]}))
    return same and codes.count(3) == 3


def scenario_b(base):
    print()
    print("=" * 78)
    print("B) 1/4/8 并发：完成时间与块记录")
    print("=" * 78)
    size, chunk = 6 * 1024 * 1024, 256 * 1024
    sigs = []
    for conc in (1, 4, 8):
        d = os.path.join(base, "B%d" % conc)
        data = gen(d, size, chunk, conc, fail_at=[0.35, 0.7], corrupt_chunks=[7])
        req, out = os.path.join(d, "req.json"), os.path.join(d, "out.bin")
        total = 0.0
        codes = []
        for _ in range(10):
            code = run(req, out, conc).returncode
            codes.append(code)
            if code == 0:
                break
        meta = json.load(open(out + ".meta.json", encoding="utf-8"))
        ok = open(out, "rb").read() == data
        sig = [(c["i"], c["sha"], c["retried"]) for c in meta["chunks"]]
        sigs.append(sig)
        print("  concurrency=%d -> %s exit=%s identical=%s blocks=%d/%d retries=%d"
              % (conc, meta["status"], codes, ok, meta["stats"]["blocks_done"],
                 meta["stats"]["blocks_total"], meta["stats"]["retries"]))
    same = sigs[0] == sigs[1] == sigs[2]
    print("  block records identical across concurrency:", same)
    return same


def scenario_c(base):
    print()
    print("=" * 78)
    print("C) 仅头 1MB 支持 Range -> 正确退化并告警")
    print("=" * 78)
    size = 5 * 1024 * 1024
    d = os.path.join(base, "C")
    data = gen(d, size, 512 * 1024, 4, fail_at=[], range_head=1024 * 1024)
    req, out = os.path.join(d, "req.json"), os.path.join(d, "out.bin")
    codes = []
    for _ in range(6):
        code = run(req, out).returncode
        codes.append(code)
        if code == 0:
            break
    meta = show(out, "final")
    ok = open(out, "rb").read() == data
    print("byte-identical:", ok, "| exit codes:", codes)
    return ok and meta["mode"] == "full" and any("head" in a for a in meta["alerts"])


def scenario_d(base):
    print()
    print("=" * 78)
    print("D) 完全不支持 Range (src.bin.norange) -> 整体重传")
    print("=" * 78)
    d = os.path.join(base, "D")
    data = gen(d, 2 * 1024 * 1024, 256 * 1024, 4, fail_at=[0.5], norange=True)
    req, out = os.path.join(d, "req.json"), os.path.join(d, "out.bin")
    codes = []
    for _ in range(6):
        code = run(req, out).returncode
        codes.append(code)
        if code == 0:
            break
    meta = show(out, "final")
    ok = open(out, "rb").read() == data
    print("byte-identical:", ok, "| exit codes:", codes)
    return ok and meta["mode"] == "full"


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dl-demo"
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    results = {
        "A (3 cuts + 2 flips)": scenario_a(base),
        "B (1/4/8 concurrency)": scenario_b(base),
        "C (head-1MB Range)": scenario_c(base),
        "D (no Range at all)": scenario_d(base),
    }
    print()
    print("=" * 78)
    for k, v in results.items():
        print("%-24s %s" % (k, "OK" if v else "FAIL"))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
