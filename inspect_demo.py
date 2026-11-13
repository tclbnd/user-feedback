import json

r = json.load(open("out_demo.json"))
print("chart ids:", sorted(r["charts"]))
print("atlas levels:", [(l["level"], l["size"]) for l in r["atlas"]])
print("placements:", json.dumps(r["placements"], sort_keys=True))
t = r["trace"]["slope"]["pixels"]
covered = [x for x in t if x and x["mask"] == "cover"]
print("slope covered texels:", len(covered), "example:", covered[0])
kinds = set()
for ch in r["charts"].values():
    for e in ch["levels"]:
        kinds.update(m for m in e["mask"] if m)
print("mask kinds:", sorted(kinds))
b = r["charts"]["bright"]["levels"][0]["light"]
print("bright min/max:", min(b), max(b))
d = r["charts"]["dark"]["levels"][0]["light"]
print("dark max:", max(x for x in d if x is not None))
for lvl in r["atlas"]:
    print("level", lvl["level"], "conflicts", len(lvl["conflicts"]),
          "owned", sum(1 for o in lvl["owners"] if o is not None))
