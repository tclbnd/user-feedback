"""Print a compact one-line state summary for a collector root."""
import json
import subprocess
import sys

root = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else ""
proc = subprocess.run(
    [sys.executable, "outbox.py", root, "inspect"],
    capture_output=True, text=True, check=True,
)
v = json.loads(proc.stdout)
statuses = sorted(set(r["status"] for r in v["outbox"]))
rejects = [(r["partition"], r["offset"], r["reason"]) for r in v["rejects"]]
print(
    "%ssink=%d outbox=%s cp=%s ack=%s rejects=%s backpressure=%d"
    % (
        label,
        len(v["sink"]),
        statuses,
        v["checkpoints"],
        v["acks"],
        rejects,
        len(v["backpressure"]),
    )
)
