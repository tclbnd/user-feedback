"""Manual CLI smoke script: rejection classification + idempotent replay."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
from tests.corpus import fixed_reject_ndjson, legal_ndjson  # noqa: E402

tmp = os.environ["TMPDIR"]
db = os.path.join(tmp, "demo2.db")
src = os.path.join(tmp, "mixed.ndjson")
for p in (db,):
    if os.path.exists(p):
        os.unlink(p)

data = legal_ndjson(seed=1, n=5) + fixed_reject_ndjson()
with open(src, "wb") as fh:
    fh.write(data)


def run(*extra):
    proc = subprocess.run(
        [sys.executable, "-m", "ndjson_pipe.cli", "import", "--db", db,
         "--source-id", "mix", "--file", src, *extra],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit(proc.returncode)
    return json.loads(proc.stdout)


first = run("--chunk-size", "3", "--batch-size", "4")
replay = run("--full-scan", "--chunk-size", "7")
print("first  :", first["accepted"], "accepted,", first["rejected"],
      "rejected;", first["rejection_breakdown"])
print("replay :", replay["accepted"], "accepted,", replay["rejected"], "rejected,",
      replay["replayed_identical"], "identical,", replay["conflicts"], "conflicts")
assert first["rejected"] == len(fixed_reject_ndjson().splitlines())
assert replay["accepted"] == 0 and replay["rejected"] == 0
assert replay["replayed_identical"] > 0 and replay["conflicts"] == 0
print("OK: fixed rejection classification and idempotent replay verified")
