"""针对题述两个缺陷的最小复现与回归验证。

缺陷 A：无关属性更正被当作全量失效 -> 期望 0 miss、0 invalidation。
缺陷 B：规则更新后复用旧拦截结论 -> 期望规则更改后全部重算，
        且输出与无缓存全重算一致。
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.test_pcache import ref_recompute_every_step, run_pcache  # noqa: E402


def run(scenario):
    return run_pcache(copy.deepcopy(scenario))


def main():
    # 缺陷 A：note 不被任何规则读取，更正 note 不应使任何缓存失效。
    scenario_a = dict(
        rules=[dict(id="r1", pred=["dept=eng"], allow=True)],
        queries=[dict(sub=dict(dept="eng", note="old")),
                 dict(sub=dict(dept="sales", note="old"))],
        changes=[dict(kind="attr", sub_i=0, key="note", to="new")],
    )
    out_a = run(scenario_a)
    assert out_a["results"] == ref_recompute_every_step(scenario_a)
    assert out_a["hits"] == 2 and out_a["misses"] == 0, out_a
    assert out_a["invalidations"] == {}, out_a
    print("A 无关属性更正: hits=%d misses=%d invalidations=%s -> OK"
          % (out_a["hits"], out_a["misses"], out_a["invalidations"]))

    # 缺陷 B：规则翻转后若复用旧缓存，结论会停留在 True。
    scenario_b = dict(
        rules=[dict(id="r1", pred=["dept=eng"], allow=True)],
        queries=[dict(sub=dict(dept="eng"))],
        changes=[
            dict(kind="rule", id="r1", allow=False),
        ],
    )
    out_b = run(scenario_b)
    assert out_b["results"] == ref_recompute_every_step(scenario_b)
    assert out_b["results"] == [False], out_b
    assert out_b["misses"] == 1 and out_b["hits"] == 0, out_b
    assert out_b["invalidations"] == {"rule": 1}, out_b
    print("B 规则更新强制重算: results=%s invalidations=%s -> OK"
          % (out_b["results"], out_b["invalidations"]))

    # 相关字段边界值更正必须使本行失效并重算，其他行保持命中。
    scenario_c = dict(
        rules=[dict(id="r1", pred=["level>5"], allow=True)],
        queries=[dict(sub=dict(level=6)), dict(sub=dict(level=9))],
        changes=[dict(kind="attr", sub_i=0, key="level", to=5)],
    )
    out_c = run(scenario_c)
    assert out_c["results"] == [False, True], out_c
    assert out_c["invalidations"] == {"level": 1}, out_c
    assert out_c["hits"] == 1 and out_c["misses"] == 1, out_c
    print("C 边界值更正: results=%s invalidations=%s -> OK"
          % (out_c["results"], out_c["invalidations"]))

    # 同一行连续更正累积生效。
    scenario_d = dict(
        rules=[dict(id="r1", pred=["level>5"], allow=True)],
        queries=[dict(sub=dict(level=1))],
        changes=[
            dict(kind="attr", sub_i=0, key="level", to=6),
            dict(kind="attr", sub_i=0, key="level", to=3),
            dict(kind="attr", sub_i=0, key="level", to=9),
        ],
    )
    out_d = run(scenario_d)
    assert out_d["results"] == [True, False, True], out_d
    assert out_d["invalidations"] == {"level": 3}, out_d
    print("D 同行连续更正: results=%s invalidations=%s -> OK"
          % (out_d["results"], out_d["invalidations"]))
    print("\n全部回归断言通过。")


if __name__ == "__main__":
    main()
