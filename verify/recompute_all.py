"""对一批结果目录批量复算多重集校验和。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = [
    "runs/small_inner_64", "runs/small_left_1",
    "runs/large/s1_inner", "runs/large/s8_inner", "runs/large/s8_left",
    "tmpcheck/fd4_inner",
]

rc = 0
for rel in DIRS:
    path = ROOT / rel
    if not path.exists():
        print("skip missing", rel)
        continue
    proc = subprocess.run(
        [sys.executable, str(ROOT / "verify" / "recompute_digest.py"),
         str(path)])
    rc |= proc.returncode
sys.exit(rc)
