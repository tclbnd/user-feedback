#!/usr/bin/env python3
"""验收演示驱动: 以 CLI 同级的引擎 API 跑通两个验收场景并打印证据。

场景A: 3 处分支脚本的逐页指纹路径 + 分支点意外页面注入立即终止并定位
场景B: 第 2/5/7 步后中断恢复, 已填字段不重复提交, 两次回放最终载荷逐字段一致

运行: python3 demo_acceptance.py
"""
import copy
import json
import os

import wizard

ROOT = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(ROOT, "run")
os.makedirs(RUN, exist_ok=True)


def load(name):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump(name, obj):
    with open(os.path.join(RUN, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)


def banner(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


def main():
    flow = load("flow.json")
    script = load("script.json")
    expected_path = [
        "p_intro", "p_personal", "p_merge_contact", "p_product",
        "p_card", "p_merge_verify", "p_promo", "p_review",
    ]

    # ---------- 场景A-1: 全量回放, 逐页指纹 ----------
    banner("A1 含3处分支脚本的完整回放(指纹定位, 非序号)")
    full = wizard.run(flow, copy.deepcopy(script),
                      state_path=os.path.join(RUN, "state_full.json"))
    dump("out_full.json", full)
    print("实际指纹路径:", full["path"])
    print("预期路径    :", expected_path)
    print("逐页指纹一致:", full["path"] == expected_path)
    print("aborted_at  :", full["aborted_at"])
    assert full["path"] == expected_path and full["aborted_at"] is None

    # ---------- 场景A-2: 分支点注入意外页面 ----------
    banner("A2 分支点(step3 期望 p_product)注入意外页面 p_loan")
    aborted = wizard.run(
        flow, copy.deepcopy(script),
        state_path=os.path.join(RUN, "state_abort.json"),
        inject={"3": {"when": "observe", "fp": "p_loan"}},
    )
    dump("out_aborted.json", aborted)
    print("aborted_at:", aborted["aborted_at"],
          "| expected:", aborted["expected"],
          "| actual:", aborted["actual"],
          "| reason:", aborted["reason"])
    print("终止前已提交步骤:", [s["step"] for s in aborted["submitted"]],
          "(意外页面未被写入任何值)")
    assert (aborted["aborted_at"], aborted["expected"],
            aborted["actual"]) == (3, "p_product", "p_loan")

    # ---------- 场景B: 第 2/5/7 步后中断恢复 ----------
    banner("B 第2/5/7步后中断 -> 恢复, 不重填已提交页面")
    baseline = full["final_payload"]
    for cut in (2, 5, 7):
        state_path = os.path.join(RUN, "state_cut%d.json" % cut)
        stop = copy.deepcopy(script)
        stop["stop_after"] = cut - 1            # 完成 step 0..cut-1 后"中断"
        first = wizard.run(flow, stop, state_path=state_path)

        resume = copy.deepcopy(script)
        resume["resume_from"] = cut
        second = wizard.run(flow, resume, state_path=state_path)
        dump("out_resume_cut%d.json" % cut, second)

        new_steps = [s["step"] for s in second["submitted"]]
        print(
            "cut=%d 中断时完成=%s | 恢复后 skipped=%s | 本次新提交=%s"
            % (cut, [s["step"] for s in first["submitted"]],
               second["skipped"], new_steps),
        )
        print("       恢复后路径一致:", second["path"] == expected_path,
              "| 最终载荷与基线逐字段一致:",
              second["final_payload"] == baseline)
        assert second["skipped"] == list(range(cut))
        assert new_steps == list(range(cut, 8))
        assert second["path"] == expected_path
        assert second["final_payload"] == baseline

    # ---------- 两次独立回放载荷一致性 ----------
    banner("B+ 两次独立回放最终提交载荷逐字段对比")
    run_a = wizard.run(flow, copy.deepcopy(script),
                       state_path=os.path.join(RUN, "state_indep_a.json"))
    run_b = wizard.run(flow, copy.deepcopy(script),
                       state_path=os.path.join(RUN, "state_indep_b.json"))
    same = json.dumps(run_a["final_payload"], sort_keys=True) == \
        json.dumps(run_b["final_payload"], sort_keys=True)
    print("载荷 A:", json.dumps(run_a["final_payload"], ensure_ascii=False, sort_keys=True))
    print("载荷 B:", json.dumps(run_b["final_payload"], ensure_ascii=False, sort_keys=True))
    print("逐字段一致:", same)
    assert same

    # ---------- 附加: 预填值变更的字段级差异 ----------
    banner("C 预填值变更 -> 字段级 field_diff(不中断)")
    changed = wizard.run(
        flow, load(os.path.join("fixtures", "script_prefill_changed.json")),
        state_path=os.path.join(RUN, "state_prefill.json"),
    )
    dump("out_prefill.json", changed)
    for d in changed["field_diff"]:
        print("step=%s fp=%s field=%s kind=%s expected=%r actual=%r"
              % (d["step"], d["fp"], d["field"], d["kind"],
                 d["expected"], d["actual"]))
    assert any(d["kind"] == "prefill_mismatch" for d in changed["field_diff"])

    print("\n全部验收演示通过 ✔")


if __name__ == "__main__":
    main()
