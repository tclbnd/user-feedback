"""After the watermark is raised, deferred rows replay without reordering."""
import json

first = json.load(open("demo-run/wm.json"))
v = json.load(open("demo-run/wm2.json"))

# the previously admitted sink item is untouched; no duplicate key commits
first_keys = [i["key"] for i in first["sink"]]
keys = [i["key"] for i in v["sink"]]
assert first_keys == ["a", "d"], first_keys
assert keys == ["a", "b", "d"], keys
assert len(keys) == len(set(keys))
# full contiguous prefix is now confirmed in offset order
assert v["checkpoints"] == {"p": 3, "q": 1}, v["checkpoints"]
assert v["acks"] == {"p": 3, "q": 1}, v["acks"]
assert v["backpressure"] == [], v["backpressure"]
assert v["rejects"] == [
    {"partition": "p", "offset": 2, "reason": "INVALID"}
], v["rejects"]
print(
    "replay ok: sink=%s cp=%s ack=%s (invalid row audited, order preserved)"
    % (keys, v["checkpoints"], v["acks"])
)
