"""按需补跑指定连接用例（已完成且带 _SUCCESS 的跳过），避免重复计算。"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    cases = json.loads(sys.argv[1])
    for case in cases:
        scale, jtype = case["scale"], case["type"]
        out = ROOT / "runs" / "large" / ("s%d_%s" % (scale, jtype))
        if (out / "_SUCCESS").exists():
            print("skip existing", out.name)
            continue
        left = ROOT / "fixtures" / "large" / ("s%d" % scale) / "left.csv"
        right = left.parent / "right.csv"
        cmd = [sys.executable, str(ROOT / "join.py"), str(left), str(right),
               str(out), "--type", jtype, "--memory-mib", "64",
               "--max-open-fds", "12"]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print("FAILED", out.name, proc.stderr)
            return 1
        m = json.loads(proc.stdout.strip())
        print("done %-9s rows=%-9d hot=%d seg=%d peak=%d spill=%dMB %.0fs"
              % (out.name, m["rows"], m["hot_keys"], m["segment_passes"],
                 m["peak_managed_bytes"], m["spill_bytes"] >> 20,
                 time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
