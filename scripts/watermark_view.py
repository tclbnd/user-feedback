"""Render the watermark backpressure result from the saved view."""
import json

v = json.load(open("demo-run/wm.json"))
keys = sorted(i["key"] for i in v["sink"])
print(
    "watermark=1 view: cp=%s sink=%s backpressure=%s rejects=%s"
    % (v["checkpoints"], keys, v["backpressure"], v["rejects"])
)
# watermark=1 with cp=0: every offset > 1 is deferred, including the invalid
# row; deferred rows are never persisted and remain replayable by the source
assert v["backpressure"] == [
    {"partition": "p", "offset": 2, "reason": "BEHIND_WATERMARK"},
    {"partition": "p", "offset": 3, "reason": "BEHIND_WATERMARK"},
], v["backpressure"]
persisted_offsets = [r["offset"] for r in v["outbox"] if r["partition"] == "p"]
assert persisted_offsets == [], persisted_offsets
assert v["checkpoints"] == {"p": 1, "q": 1}
print("backpressure rows were not persisted and can be replayed later")

