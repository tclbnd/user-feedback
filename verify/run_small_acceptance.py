"""验收 1：小型夹具的 inner/left × 多预算，全部用 SQLite 精确核对。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "small"
RUNS = ROOT / "runs"


def run_join(left, right, out, jtype, mem, extra=None):
    cmd = [sys.executable, str(ROOT / "join.py"), str(left), str(right),
           str(out), "--type", jtype, "--memory-mib", str(mem),
           "--max-open-fds", "8"]
    if extra:
        cmd += extra
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit("join 失败: %s" % " ".join(cmd))
    return json.loads(proc.stdout.strip())


def main():
    if RUNS.exists():
        shutil.rmtree(RUNS)
    RUNS.mkdir()
    expected = json.loads((FIX / "expected.json").read_text("utf-8"))
    failures = 0
    for mem in (64, 1):
        for jtype in ("inner", "left"):
            out = RUNS / ("small_%s_%s" % (jtype, mem))
            metrics = run_join(FIX / "left.csv", FIX / "right.csv", out,
                               jtype, mem)
            sys.path.insert(0, str(ROOT))
            from verify.sqlite_check import run as check
            rows, digest, problems, _ = check(
                FIX / "left.csv", FIX / "right.csv", out, jtype)
            want = expected[jtype]["rows"]
            ok = not problems and rows == want
            print(("[%s] mem=%2dMiB type=%-5s rows=%d (期望 %d) "
                   "hot=%d segments=%d spill=%dB fds=%d/%d")
                  % ("PASS" if ok else "FAIL", mem, jtype, rows, want,
                     metrics["hot_keys"], metrics["segment_passes"],
                     metrics["spill_bytes"], metrics["peak_open_fds"],
                     metrics["max_open_fds"]))
            failures += 0 if ok else 1

    # 空输入：两种连接都只有表头、0 行，且必须正常发布 _SUCCESS。
    for jtype in ("inner", "left"):
        out = RUNS / ("empty_%s" % jtype)
        metrics = run_join(FIX / "empty" / "left.csv",
                           FIX / "empty" / "right.csv", out, jtype, 1)
        rows_csv = (out / "rows.csv").read_text("utf-8").splitlines()
        ok = metrics["rows"] == 0 and len(rows_csv) == 1
        print("[%s] empty input type=%-5s rows=%d"
              % ("PASS" if ok else "FAIL", jtype, metrics["rows"]))
        failures += 0 if ok else 1

    # 单侧为空。
    pairs = [("empty/left.csv", "right.csv"), ("left.csv", "empty/right.csv")]
    for idx, (lrel, rrel) in enumerate(pairs):
        lp = FIX / lrel if not lrel.startswith("empty/") else FIX / lrel
        rp = FIX / rrel if not rrel.startswith("empty/") else FIX / rrel
        for jtype in ("inner", "left"):
            out = RUNS / ("oneempty_%d_%s" % (idx, jtype))
            metrics = run_join(lp, rp, out, jtype, 1)
            from verify.sqlite_check import run as check
            rows, _, problems, _ = check(lp, rp, out, jtype)
            ok = not problems
            print("[%s] one-side-empty case=%d type=%-5s rows=%d"
                  % ("PASS" if ok else "FAIL", idx, jtype, rows))
            failures += 0 if ok else 1

    print("\n小型夹具验收结论:", "全部通过" if failures == 0
          else ("%d 项失败" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
