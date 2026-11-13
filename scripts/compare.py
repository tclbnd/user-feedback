"""Compare a collector root against the no-failure reference sink."""
import json
import subprocess
import sys

root = sys.argv[1]
ref = json.load(open("demo-run/ref-view.json"))
proc = subprocess.run(
    [sys.executable, "outbox.py", root, "inspect"],
    capture_output=True, text=True, check=True,
)
v = json.loads(proc.stdout)

ref_pairs = [(i["key"], i["sha256"]) for i in ref["sink"]]
got_pairs = [(i["key"], i["sha256"]) for i in v["sink"]]
assert got_pairs == ref_pairs, (got_pairs, ref_pairs)
keys = [i["key"] for i in v["sink"]]
assert len(keys) == len(set(keys)), "duplicate sink commit detected"
assert v["checkpoints"] == ref["checkpoints"], v["checkpoints"]
assert v["acks"] == ref["acks"], v["acks"]
assert v["rejects"] == ref["rejects"], v["rejects"]
print(
    "MATCH reference: sink keys+digests %s, cp=%s, ack=%s, rejects=%s"
    % (keys, v["checkpoints"], v["acks"], v["rejects"])
)
