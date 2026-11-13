"""验收 2 编排：64 MiB 预算下跑 s=1..8 热点体积扫描，并做故障恢复测试。"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "large"
RUNS = ROOT / "runs" / "large"
MEM = 64
MAX_OPEN = 12


def join_cmd(case_left, out, jtype, extra=None):
    cmd = [sys.executable, str(ROOT / "join.py"), str(case_left),
           str(case_left.parent / "right.csv"), str(out),
           "--type", jtype, "--memory-mib", str(MEM),
           "--max-open-fds", str(MAX_OPEN)]
    if extra:
        cmd += extra
    return cmd


def main():
    if RUNS.exists():
        shutil.rmtree(RUNS)
    RUNS.mkdir(parents=True)
    manifest = json.loads((FIX / "manifest.json").read_text("utf-8"))

    for s in manifest["scales"]:
        case_left = FIX / ("s%d" % s) / "left.csv"
        out = RUNS / ("s%d_inner" % s)
        t0 = time.time()
        proc = subprocess.run(join_cmd(case_left, out, "inner"),
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr, file=sys.stderr)
            raise SystemExit("s=%d inner 运行失败" % s)
        print("s=%d inner 完成，用时 %.1fs" % (s, time.time() - t0))

    # s=8 左外连接
    t0 = time.time()
    proc = subprocess.run(
        join_cmd(FIX / "s8" / "left.csv", RUNS / "s8_left", "left"),
        capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout, proc.stderr, file=sys.stderr)
        raise SystemExit("s=8 left 运行失败")
    print("s=8 left 完成，用时 %.1fs" % (time.time() - t0))

    # ---------- 故障恢复测试 ----------
    reliability(RUNS)
    return 0


def reliability(runs_dir):
    print("\n== 故障恢复与句柄限制测试 ==")
    case_left = FIX / "s2" / "left.csv"
    spill_root = runs_dir / "spillarea"
    spill_root.mkdir(exist_ok=True)

    # 1) --crash-before-publish：退出码 97，目标不出现，暂存残留。
    crashed = runs_dir / "crash_target"
    proc = subprocess.run(
        join_cmd(case_left, crashed, "inner",
                 ["--crash-before-publish", "--spill-root", str(spill_root)]),
        capture_output=True, text=True)
    assert proc.returncode == 97, proc.returncode
    assert not crashed.exists(), "崩溃后目标目录不应存在"
    leftovers = list(spill_root.glob("crash_target.*.stage"))
    assert leftovers, "应残留属于该目标的暂存目录"
    print("[PASS] crash-before-publish: 退出97, 目标缺失, 暂存残留 %s"
          % leftovers[0].name)

    # 2) 无故障重跑：自动清理上次暂存并成功发布，其他目录不受影响。
    sentinel = spill_root / "other_target.keepme"
    sentinel.mkdir()
    proc = subprocess.run(
        join_cmd(case_left, crashed, "inner",
                 ["--spill-root", str(spill_root)]),
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (crashed / "_SUCCESS").exists() and (crashed / "rows.csv").exists()
    assert not list(spill_root.glob("crash_target.*.stage")), "旧暂存应被清理"
    assert sentinel.exists(), "不得修改不属于该目标的目录"
    print("[PASS] 故障后重跑: 清理旧暂存并成功发布，其他目录未受影响")

    # 3) 目标已存在时拒绝覆盖。
    proc = subprocess.run(join_cmd(case_left, crashed, "inner"),
                          capture_output=True, text=True)
    assert proc.returncode == 2, proc.returncode
    print("[PASS] 目标已存在: 拒绝覆盖，退出码 2")

    # 4) SIGTERM 中断：进程非零退出，暂存被信号处理器回收。
    killed = runs_dir / "killed_target"
    proc = subprocess.Popen(
        join_cmd(FIX / "s8" / "left.csv", killed, "inner",
                 ["--spill-root", str(spill_root)]),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(3)
    proc.send_signal(signal.SIGTERM)
    proc.wait()
    assert proc.returncode != 0, proc.returncode
    assert not killed.exists(), "中断后不应发布目标"
    assert not list(spill_root.glob("killed_target.*.stage")), \
        "信号中断后暂存应被回收"
    print("[PASS] SIGTERM 中断: 非零退出，目标与暂存均已回收")

    # 5) 中断后干净复跑成功。
    proc = subprocess.run(
        join_cmd(FIX / "s8" / "left.csv", killed, "inner",
                 ["--spill-root", str(spill_root)]),
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (killed / "_SUCCESS").exists()
    print("[PASS] 中断后复跑: 成功发布完整结果")


if __name__ == "__main__":
    sys.exit(main())
