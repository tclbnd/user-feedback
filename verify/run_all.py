"""一键总验收：依次跑小型 SQLite 核对、多热点双侧切块、无倾斜 BNL 兜底。

大型 64MiB 扫描因耗时较长（约 15 分钟）由 run_cases.py + large_check.py
单独编排；本脚本覆盖所有连接语义与全部三类执行路径，可在数分钟内完成。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
failures = 0


def step(title):
    print("\n=== %s ===" % title)


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    return proc


def check(left, right, result, jtype):
    global failures
    proc = run([PY, str(ROOT / "verify" / "sqlite_check.py"),
                str(left), str(right), str(result), jtype])
    if proc.returncode != 0:
        failures += 1
    print(proc.stdout.strip())


def join(left, right, out, jtype, mem, fds=8):
    proc = run([PY, str(ROOT / "join.py"), str(left), str(right), str(out),
                "--type", jtype, "--memory-mib", str(mem),
                "--max-open-fds", str(fds)])
    if proc.returncode != 0:
        failures += 1
        print(proc.stderr, file=sys.stderr)
    return json.loads(proc.stdout.strip()) if proc.returncode == 0 else None


work = ROOT / "runs" / "acceptance"
if work.exists():
    shutil.rmtree(work)
work.mkdir(parents=True)

# 1) 小型夹具：内连接/左外 × 64MiB/1MiB，全部 SQLite 精确核对。
step("1. 小型夹具（复合键/NULL/重复/空输入/特殊字符）SQLite 核对")
for mem in (64, 1):
    for jtype in ("inner", "left"):
        out = work / ("small_%s_%d" % (jtype, mem))
        join(ROOT / "fixtures/small/left.csv",
             ROOT / "fixtures/small/right.csv", out, jtype, mem)
        check(ROOT / "fixtures/small/left.csv",
              ROOT / "fixtures/small/right.csv", out, jtype)

# 2) 多热点双侧切块（1MiB 强制两侧均无法整块装入）。
step("2. 多热点双侧分段块连接（1MiB）SQLite 核对 600 万行")
for jtype in ("inner", "left"):
    out = work / ("multihot_%s" % jtype)
    m = join(ROOT / "fixtures/multihot/left.csv",
             ROOT / "fixtures/multihot/right.csv", out, jtype, 1, fds=6)
    assert m and m["hot_keys"] == 2 and m["segment_passes"] >= 2
    assert m["peak_managed_bytes"] <= m["budget_bytes"]
    check(ROOT / "fixtures/multihot/left.csv",
          ROOT / "fixtures/multihot/right.csv", out, jtype)

# 3) 无倾斜高基数：再分区/BNL 兜底路径。
step("3. 无倾斜高基数 BNL 兜底（1MiB）SQLite 核对")
for jtype in ("inner", "left"):
    out = work / ("uniform_%s" % jtype)
    m = join(ROOT / "fixtures/uniform/left.csv",
             ROOT / "fixtures/uniform/right.csv", out, jtype, 1)
    assert m and m["hot_keys"] == 0
    assert m["peak_managed_bytes"] <= m["budget_bytes"]
    check(ROOT / "fixtures/uniform/left.csv",
          ROOT / "fixtures/uniform/right.csv", out, jtype)

# 4) 独立第三方复算校验和。
step("4. 独立复算多重集校验和")
for sub in sorted(work.iterdir()):
    proc = run([PY, str(ROOT / "verify/recompute_digest.py"), str(sub)])
    if proc.returncode != 0:
        failures += 1
    print(proc.stdout.strip())

print("\n" + ("全部总验收通过" if failures == 0
              else "%d 项失败" % failures))
sys.exit(1 if failures else 0)
