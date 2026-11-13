"""端到端演示运行器：用引擎 API 跑完整验收场景并打印关键证据。

运行：python3 run_demo.py
输出：
- 每次修订的 accepted 与实际重关联键集合（对照手工影响范围）；
- 12 个切点上增量引擎与独立全量重建、手工预言机的逐条事实比对；
- 两类边界点、变更明细、双时间查询与历史报表可复现的样例。
"""
from __future__ import annotations

import copy

from bitemporal import Engine
from reference_rebuild import rebuild_report
import scenario


def row_map(report: dict) -> dict:
    return {r["id"]: (r["dimension_version"], r["reason"]) for r in report["rows"]}


def main() -> None:
    eng = Engine()
    r0 = eng.apply_batch(copy.deepcopy(scenario.INITIAL_BATCH))
    print("初始装载: accepted=%d" % r0["accepted"])

    print("\n== 8 次回溯修订：实际重关联键集合 vs 手工影响范围 ==")
    for rev in scenario.REVISIONS:
        receipt = eng.apply_batch(copy.deepcopy(rev["events"]))
        ok = receipt["affected"] == rev["expected_affected"]
        print("system=%-3d %-32s 实际=%-14s 手工=%-14s %s" % (
            rev["system"], rev["name"], receipt["affected"],
            rev["expected_affected"], "OK" if ok else "MISMATCH"))

    print("\n== 12 个系统时间切点：增量引擎 vs 独立全量重建 vs 预言机 ==")
    all_ok = True
    for cut in scenario.CUTS:
        inc = row_map(eng.report(cut))
        full = row_map(rebuild_report(eng.events, cut))
        oracle = scenario.expected_state(cut)
        ok = inc == full == oracle
        all_ok &= ok
        print("asof=%-3d 事实数=%-2d 三者一致: %s" % (cut, len(inc), ok))
    print("全部切点一致:", all_ok)

    print("\n== 系统时间相邻边界点（左 vs 右，asof 含该时刻事件）==")
    for left, right in scenario.ADJACENT_SYSTEM_POINTS:
        differs = row_map(eng.report(left)) != row_map(eng.report(right))
        rebuild_ok = row_map(rebuild_report(eng.events, right)) == row_map(eng.report(right))
        print("(%2d,%2d) 两侧不同: %s, 右侧与重建一致: %s" % (left, right, differs, rebuild_ok))

    print("\n== 业务时间半开边界（asof=100，维表 R）==")
    for left, right, lv, rv in scenario.VALID_BOUNDARIES:
        hl = eng.resolve("R", left, 100)
        hr = eng.resolve("R", right, 100)
        right_label = hr["dimension_version"] if hr["reason"] != "no_dimension" else "无维表"
        print("at=%-3d -> %-8s | at=%-3d -> %s" % (
            left, hl["dimension_version"], right, right_label))

    print("\n== 变更明细样例（F6：matched -> removed -> 撤销恢复）==")
    for ch in eng.change_log():
        if ch["id"] == "F6":
            print("system=%-3d old=%s new=%s changed=%s coalesced=%s" % (
                ch["system"], ch["old"], ch["new"], ch["changed"],
                ch.get("coalesced")))

    print("\n== 双时间查询样例：维表 R 在业务点 20、系统点 30 可见的单元格 ==")
    for c in eng.dim_cells("R", at=20, asof=30):
        print(c)

    print("\n== 历史报表可复现：F2 撤销前后 ==")
    print("asof=49 :", row_map(eng.report(49)).get("F2"))
    print("asof=100 是否仍含 F2:", "F2" in row_map(eng.report(100)))
    print("F2 关联链:",
          [(l["sfrom"], l["sto"], l["dimension_version"], l["reason"])
           for l in eng.fact_history("F2")["links"]])


if __name__ == "__main__":
    main()

