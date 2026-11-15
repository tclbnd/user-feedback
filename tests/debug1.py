import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sched  # noqa: E402
from acceptance import build_trace, run_cli, without_digest  # noqa: E402

sim = build_trace()
baseline = sched.replay(copy.deepcopy(sim))
st = Path("artifacts/st1.json")
if st.exists():
    st.unlink()
run_cli(sim, "c1", state=st, fail_at="after-commit:1")
cp = json.loads(st.read_text())
print(
    "checkpoint tick", cp["tick"], "in_tick", cp["in_tick"],
    "dispatch", len(cp["dispatch"]), "cursor", cp["cursor"],
)
resumed = run_cli(sim, "r1", state=st)
for i, (a, b) in enumerate(zip(baseline["dispatch"], resumed["dispatch"])):
    if a != b:
        print("first dispatch diff", i)
        print(" base   ", a)
        print(" resumed", b)
        break
else:
    print("dispatch lens", len(baseline["dispatch"]), len(resumed["dispatch"]))
for i, (a, b) in enumerate(zip(baseline["balances"], resumed["balances"])):
    if a != b:
        print("first balance diff tick", i)
        print(" base   ", a)
        print(" resumed", b)
        break
print("service equal:", baseline["service"] == resumed["service"])
