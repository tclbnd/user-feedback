#!/usr/bin/env python3
"""重复运行验收套件，捕捉偶发失败（并发相关的不确定性）。

    python3 tests/repeat.py [N]
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
bad = 0
for i in range(n):
    p = subprocess.run([sys.executable, os.path.join(HERE, "acceptance.py")],
                       capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    print("round %d: exit=%d %s" % (i + 1, p.returncode, lines[-1] if lines else ""))
    if p.returncode != 0:
        bad += 1
        print(p.stdout[-3000:])
        print(p.stderr[-2000:])
print("%d/%d rounds clean" % (n - bad, n))
sys.exit(1 if bad else 0)
