"""生成验收夹具：手写小场景、空更正场景、1000 次更正的固定夹具。

运行：python3 tests/generate_fixture.py
所有数据均由固定种子确定性生成，便于回归。
"""
import json
import random
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def write_fixture(name, payload):
    path = FIXTURE_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print("wrote", path.relative_to(Path(__file__).parent.parent))


def small_scenario():
    rules = [
        dict(id="r1", pred=["dept=eng", "level>5"], allow=True),
        dict(id="r2", pred=["dept=ops"], allow=False),
        dict(id="r3", pred=["tier=gold"], allow=True),
    ]
    queries = [
        dict(sub=dict(dept="eng", level=7, tier="silver", note="a")),
        dict(sub=dict(dept="ops", level=2, tier="gold", note="b")),
        dict(sub=dict(dept="eng", level=3, tier="gold", note="c")),
        dict(sub=dict(level=9, tier="silver", note="d")),
    ]
    changes = [
        dict(kind="attr", sub_i=0, key="note", to="x"),
        dict(kind="attr", sub_i=0, key="level", to=9),
        dict(kind="attr", sub_i=0, key="level", to=4),
        dict(kind="attr", sub_i=1, key="level", to=99),
        dict(kind="attr", sub_i=2, key="tier", to="silver"),
        dict(kind="rule", id="r1", pred=["dept=ops"], allow=False),
        dict(kind="attr", sub_i=3, key="dept", to="eng"),
        dict(kind="rule", id="r3", allow=False),
    ]
    return dict(rules=rules, queries=queries, changes=changes)


def empty_scenario():
    rules = [dict(id="r1", pred=["dept=eng"], allow=True)]
    queries = [
        dict(sub=dict(dept="eng")),
        dict(sub=dict(dept="sales")),
    ]
    return dict(rules=rules, queries=queries, changes=[])


def large_scenario(n_rows=200, n_changes=1000, seed=20261114):
    rng = random.Random(seed)
    rules = [
        dict(id="r1", pred=["dept=eng", "level>5"], allow=True),
        dict(id="r2", pred=["dept=ops"], allow=False),
        dict(id="r3", pred=["tier=gold"], allow=True),
    ]
    depts = ["eng", "ops", "sales", "hr"]
    tiers = ["gold", "silver", "bronze"]
    rows = []
    for i in range(n_rows):
        rows.append(dict(
            dept=depts[i % len(depts)],
            level=rng.randint(1, 10),
            tier=tiers[i % len(tiers)],
            note="note-%d" % i,
            phone="139%08d" % i,
        ))
    queries = [dict(sub=row) for row in rows]

    # 开头放一组确定性事件：同一行连续更正、边界值翻转、无关字段、规则变更。
    head = [
        dict(kind="attr", sub_i=0, key="note", to="head-0"),
        dict(kind="attr", sub_i=0, key="note", to="head-1"),
        dict(kind="attr", sub_i=0, key="level", to=6),
        dict(kind="attr", sub_i=0, key="level", to=5),
        dict(kind="attr", sub_i=1, key="phone", to="10000000000"),
        dict(kind="attr", sub_i=2, key="dept", to="eng"),
        dict(kind="rule", id="r1", pred=["dept=eng", "level>6"], allow=True),
        dict(kind="attr", sub_i=3, key="tier", to="gold"),
        dict(kind="attr", sub_i=3, key="tier", to="bronze"),
        dict(kind="rule", id="r3", allow=False),
    ]

    rule_variants = [
        dict(kind="rule", id="r1", pred=["dept=eng", "level>4"], allow=True),
        dict(kind="rule", id="r2", pred=["dept=ops", "level>0"], allow=False),
        dict(kind="rule", id="r3", pred=["tier=gold"], allow=True),
        dict(kind="rule", id="r1", allow=False),
    ]
    relevant_keys = ["dept", "level", "tier"]
    irrelevant_keys = ["note", "phone"]
    dept_values = ["eng", "ops", "sales", "hr"]
    tier_values = ["gold", "silver", "bronze"]

    changes = list(head)
    while len(changes) < n_changes:
        roll = rng.random()
        if roll < 0.88:  # 绝大多数为与当前结论无关的属性更正 -> 缓存命中
            key = rng.choice(irrelevant_keys)
            change = dict(
                kind="attr",
                sub_i=rng.randrange(n_rows),
                key=key,
                to="v-%d" % rng.randrange(10_000_000),
            )
        elif roll < 0.99:  # 少量相关字段更正，含边界值
            key = rng.choice(relevant_keys)
            sub_i = rng.randrange(n_rows)
            if key == "dept":
                to = rng.choice(dept_values)
            elif key == "tier":
                to = rng.choice(tier_values)
            else:
                to = rng.choice([4, 5, 6, 7, 1])
            change = dict(kind="attr", sub_i=sub_i, key=key, to=to)
        else:  # 偶发规则更新，验证全量失效与索引重建
            change = dict(rng.choice(rule_variants))
        changes.append(change)
    changes = changes[:n_changes]
    return dict(rules=rules, queries=queries, changes=changes)


def main():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    write_fixture("scenario_small.json", small_scenario())
    write_fixture("scenario_empty.json", empty_scenario())
    write_fixture("scenario_1000.json", large_scenario())


if __name__ == "__main__":
    main()