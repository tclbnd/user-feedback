import json, random, subprocess, sys, tempfile, os
from pathlib import Path

HERE = Path(__file__).parent

def run(inp):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(inp, f, ensure_ascii=False)
        ip = f.name
    op = ip + ".out"
    r = subprocess.run([sys.executable, str(HERE / "ot_sim.py"), ip, op],
                       capture_output=True, text=True)
    out = json.loads(Path(op).read_text()) if r.returncode == 0 else None
    os.unlink(ip)
    if os.path.exists(op):
        os.unlink(op)
    return r.returncode, out

def baseline(doc, events):
    uniq = {}
    for e in events:
        uniq.setdefault(e["id"], e)
    ins = sorted((e for e in uniq.values() if e["op"] == "ins"),
                 key=lambda e: (e["pos"], -e["source"], e["seq"], e["id"]))
    deleted = set()
    for e in uniq.values():
        if e["op"] == "del":
            deleted.update(range(e["pos"], e["pos"] + e["len"]))
    ins_at = {}
    for e in ins:
        ins_at.setdefault(e["pos"], []).append(e["text"])
    parts = []
    for i, ch in enumerate(doc):
        parts += ins_at.get(i, [])
        if i not in deleted:
            parts.append(ch)
    parts += ins_at.get(len(doc), [])
    return "".join(parts)

def make_batches(evs, k):
    b = [[] for _ in range(k)]
    for i, e in enumerate(evs):
        b[i % k].append(e)
    return [x for x in b if x]

random.seed(42)

# 验收 1：200 组重叠删除 + 同位插入 + 重复事件，三种到达顺序
doc = "".join(random.choice("abc中文xyz") for _ in range(400))
n = len(doc)
events = []
for g in range(200):
    p = random.randint(0, n - 5)
    l1 = random.randint(1, min(8, n - p))
    l2 = random.randint(1, 8)
    off = random.randint(0, l1)
    s1, s2 = random.sample([1, 2], 2)
    events.append(dict(id="del%da" % g, source=s1, seq=g, op="del", pos=p, len=l1))
    events.append(dict(id="del%db" % g, source=s2, seq=g, op="del",
                       pos=min(n - l2, p + off), len=l2))
    ip = random.randint(0, n)
    events.append(dict(id="ins%da" % g, source=1, seq=g, op="ins", pos=ip,
                       text="<A%d>" % g))
    events.append(dict(id="ins%db" % g, source=2, seq=g, op="ins", pos=ip,
                       text="<B%d>" % g))
dups = random.sample(events, 150)
all_events = events + dups
random.shuffle(all_events)

batches = make_batches(all_events, 7)
orders = [list(range(7)), list(reversed(range(7))), [3, 0, 6, 1, 5, 2, 4]]
rc, out = run(dict(doc=doc, batches=batches, orders=orders))
assert rc == 0
exp = baseline(doc, all_events)
assert out["converged"] is True
assert len(out["final"]) == 3
for f in out["final"]:
    assert f == exp, "final mismatch vs offline baseline"
assert out["dedupe"] == dict(unique=len(events), duplicates=len(dups))
ids = [a["id"] for a in out["audit"]]
assert ids == sorted(ids)
assert sum(a["copies"] for a in out["audit"]) == len(all_events)
assert all(a["outcome"] == "applied" for a in out["audit"] if a["op"] == "ins")
assert any(a["outcome"] == "merged" for a in out["audit"] if a["op"] == "del")
print("accept1 OK: converged, 3 orders identical to baseline, dedupe =", out["dedupe"])

# 验收 2a：>=1000 条历史更正，改变分批边界
doc2 = "".join(random.choice("0123456789abcdef") for _ in range(2000))
n2 = len(doc2)
evs2 = []
for i in range(1200):
    if i % 3 == 0:
        p = random.randint(0, n2 - 1)
        evs2.append(dict(id="d%d" % i, source=random.randint(1, 2), seq=i,
                         op="del", pos=p, len=random.randint(1, n2 - p)))
    else:
        evs2.append(dict(id="i%d" % i, source=random.randint(1, 2), seq=i,
                         op="ins", pos=random.randint(0, n2), text="#%d#" % i))
evs2 += random.sample(evs2, 300)
random.shuffle(evs2)
exp2 = baseline(doc2, evs2)
tot, uniq_n = len(evs2), len(set(e["id"] for e in evs2))

audits = []
for k in (1, 5, 64, 1000):
    bs = make_batches(evs2, k)
    ords = [list(range(len(bs))), list(reversed(range(len(bs))))]
    rc, out = run(dict(doc=doc2, batches=bs, orders=ords))
    assert rc == 0
    assert out["converged"] and all(f == exp2 for f in out["final"])
    assert out["dedupe"] == dict(unique=uniq_n, duplicates=tot - uniq_n)
    audits.append(json.dumps(out["audit"], sort_keys=True))
assert len(set(audits)) == 1, "audit changed across batch boundaries"
print("accept2a OK: 1500 events (1200 unique), batchings 1/5/64/1000 identical")

# 验收 2b：冲突 ID 退出 2
bad = dict(doc="abc",
           batches=[[dict(id="x", source=1, seq=0, op="ins", pos=1, text="A")],
                    [dict(id="x", source=1, seq=0, op="ins", pos=2, text="A")]],
           orders=[[0, 1]])
rc, out = run(bad)
assert rc == 2 and out is None, "expected exit 2, got %d" % rc
print("accept2b OK: conflicting id exits 2")

# 边界：删除区间内插入保留；重叠删除 merged；末尾插入
evs3 = [dict(id="d1", source=1, seq=0, op="del", pos=1, len=3),
        dict(id="i1", source=2, seq=0, op="ins", pos=2, text="XY"),
        dict(id="i2", source=1, seq=0, op="ins", pos=2, text="mn"),
        dict(id="d2", source=2, seq=1, op="del", pos=0, len=4),
        dict(id="i3", source=1, seq=1, op="ins", pos=5, text="!")]
rc, out = run(dict(doc="hello", batches=[evs3], orders=[[0]]))
assert rc == 0
assert out["final"] == ["XYmno!"], out["final"]
a = dict((x["id"], x["outcome"]) for x in out["audit"])
assert a["d2"] == "applied" and a["d1"] == "merged"
print("edge OK:", out["final"], a)
print("ALL PASS")
