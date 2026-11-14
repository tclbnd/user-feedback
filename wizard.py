#!/usr/bin/env python3
"""多步表单向导确定性回放.

用法: python3 wizard.py flow.json script.json output.json [state.json]

- 以页面指纹 fp 定位当前步骤(而非序号); 实际 fp 与脚本 expect 不符即终止并定位.
- 字段类型 text/select/date/checkbox 各有填写与校验语义; 失败输出字段级差异.
- 每步完成后持久化 state.json; resume_from=k 时前 k 步视为已提交不再重填
  (记入 skipped), 其载荷取自持久化状态并并入 final_payload.
"""
import json
import os
import sys
import hashlib
from datetime import date


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def flow_id(flow):
    blob = json.dumps(flow, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------- 字段类型语义 ----------------

def _norm_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()  # 严格 YYYY-MM-DD 且为真实日期
    except ValueError:
        return None


def validate_value(field, value):
    """按字段类型校验并规范化. 返回 (ok, normalized, reason)."""
    ftype = field.get("type", "text")
    if ftype == "text":
        if isinstance(value, str):
            return True, value, None
        return False, None, "expected string"
    if ftype == "select":
        options = field.get("options", [])
        if isinstance(value, str) and value in options:
            return True, value, None
        return False, None, "value not in options %r" % (options,)
    if ftype == "date":
        norm = _norm_date(value)
        if norm is not None:
            return True, norm, None
        return False, None, "invalid ISO date (YYYY-MM-DD)"
    if ftype == "checkbox":
        options = field.get("options")
        if options is None:  # 单个布尔复选框
            if isinstance(value, bool):
                return True, value, None
            return False, None, "expected boolean"
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            bad = [x for x in value if x not in options]
            if bad:
                return False, None, "values not in options: %r" % (bad,)
            seen, dedup = set(), []
            for x in value:  # 去重保序, 保证多次回放载荷一致
                if x not in seen:
                    seen.add(x)
                    dedup.append(x)
            return True, dedup, None
        return False, None, "expected list of option strings"
    return False, None, "unknown field type %r" % (ftype,)


def fill_page(page, step, k):
    """构造当前页提交载荷. 返回 (payload, diffs, fatal)."""
    fields = {f["name"]: f for f in page.get("fields", [])}
    fill = step.get("fill") or {}
    expect_prefill = step.get("expect_prefill") or {}
    diffs, payload = [], {}

    # 1) 未识别字段: 脚本与页面错位的强信号, 拒绝提交本页
    unknown = sorted(n for n in fill if n not in fields)
    for name in unknown:
        diffs.append({"step": k, "field": name, "kind": "unknown_field",
                      "expected": None, "actual": fill[name]})
    if unknown:
        return payload, diffs, True

    # 2) 预填值: 自身类型校验 + 与脚本期望比对(变更只记录, 不阻断)
    for name, fdef in fields.items():
        if "prefill" not in fdef:
            if name in expect_prefill:
                diffs.append({"step": k, "field": name, "kind": "prefill_mismatch",
                              "expected": expect_prefill[name], "actual": None})
            continue
        ok, norm, reason = validate_value(fdef, fdef["prefill"])
        if not ok:
            diffs.append({"step": k, "field": name, "kind": "invalid_prefill",
                          "expected": reason, "actual": fdef["prefill"]})
            continue
        if name in expect_prefill and expect_prefill[name] != norm:
            diffs.append({"step": k, "field": name, "kind": "prefill_mismatch",
                          "expected": expect_prefill[name], "actual": norm})
        payload[name] = norm  # 未显式覆盖的预填字段随页面一起提交

    # 3) 显式填写: 类型校验失败为致命错误
    fatal = False
    for name, value in fill.items():
        ok, norm, reason = validate_value(fields[name], value)
        if not ok:
            diffs.append({"step": k, "field": name, "kind": "invalid_value",
                          "expected": reason, "actual": value})
            fatal = True
        else:
            payload[name] = norm
    if fatal:
        return payload, diffs, True

    # 4) 必填检查
    missing = sorted(n for n, f in fields.items()
                     if f.get("required") and n not in payload)
    for name in missing:
        diffs.append({"step": k, "field": name, "kind": "missing_required",
                      "expected": "value", "actual": None})
    return payload, diffs, bool(missing)


def resolve_next(page, payload):
    """按 branch_on 字段的提交值求下一页指纹; 无 next 规则表示流程终点."""
    rules = page.get("next") or {}
    if not rules:
        return None
    branch_on = page.get("branch_on")
    key = str(payload.get(branch_on)) if branch_on else "default"
    return rules.get(key) or rules.get("*") or rules.get("default")


# ---------------- 状态持久化 ----------------

def save_state(path, fid, completed, done):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"flow_id": fid, "completed": completed, "done": done},
                  fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 原子替换, 中断不留下半截状态


def _consistent_prefix(stored, steps):
    """已持久化轨迹与当前脚本一致的最长前缀长度."""
    n = 0
    for i, rec in enumerate(stored):
        if i >= len(steps) or steps[i].get("expect") != rec.get("fp"):
            break
        if i + 1 < len(stored) and stored[i + 1].get("fp") != rec.get("next_fp"):
            break
        n = i + 1
    return n


# ---------------- 回放主循环 ----------------

def run(flow, script, state_path="state.json"):
    pages = {}
    for p in flow.get("pages", []):
        fp = p.get("fp")
        if fp in pages:
            raise ValueError("duplicate page fingerprint: %r" % (fp,))
        pages[fp] = p
    steps = script.get("steps", [])
    fid = flow_id(flow)

    # 载入持久化状态(校验流程身份, 异流程状态直接作废)
    stored, state_ok = [], False
    if os.path.exists(state_path):
        try:
            st = load_json(state_path)
            state_ok = st.get("flow_id") == fid and isinstance(st.get("completed"), list)
            if state_ok:
                stored = st["completed"]
        except (ValueError, OSError):
            pass
    prefix = _consistent_prefix(stored, steps)

    # 确定恢复点: resume_from 给出意图, 实际退到最近一致步骤
    resume_from = script.get("resume_from")
    adjusted = None
    if resume_from is None:
        start, kept = 0, []
        save_state(state_path, fid, [], False)  # 全新回放, 作废旧状态
    else:
        if not state_ok or (resume_from > 0 and prefix == 0):
            return {"status": "aborted", "submitted": [], "skipped": [],
                    "aborted_at": resume_from,
                    "expected": steps[resume_from].get("expect") if resume_from < len(steps) else None,
                    "actual": None, "reason": "resume_unavailable",
                    "field_diff": [], "resume_from": resume_from,
                    "resume_adjusted_to": None, "final_payload": {}}
        if resume_from > prefix:
            adjusted = prefix
        start = min(resume_from, prefix)
        kept = stored[:start]

    submitted, diffs = [], []
    skipped = list(range(start))
    final_payload = {}
    for rec in kept:  # 已提交页的载荷并入最终结果, 但不重填
        final_payload.update(rec["payload"])
    completed = list(kept)
    current = (kept[-1]["next_fp"] if kept
               else flow.get("start") or (flow["pages"][0]["fp"] if flow.get("pages") else None))

    status, aborted_at, expected, actual, reason = "completed", None, None, None, None
    interrupt_after = script.get("interrupt_after")  # 测试钩子: 模拟第 k 步后中断

    for k in range(start, len(steps)):
        step = steps[k]
        exp = step.get("expect")
        # 指纹定位: 当前页必须存在且与脚本期望一致
        if current is None or current not in pages or exp != current:
            status, aborted_at, expected, actual, reason = \
                "aborted", k, exp, current, "page_mismatch"
            break
        page = pages[current]
        payload, fd, fatal = fill_page(page, step, k)
        diffs.extend(fd)
        if fatal:
            status, aborted_at, expected, actual, reason = \
                "aborted", k, exp, current, "field_validation"
            break
        nxt = resolve_next(page, payload)
        submitted.append({"step": k, "fp": current, "payload": payload})
        completed.append({"step": k, "fp": current, "payload": payload, "next_fp": nxt})
        final_payload.update(payload)
        save_state(state_path, fid, completed, False)  # 每步落盘后再推进
        goto = step.get("goto")
        if goto is not None and goto != nxt:  # 分支点落到了意外页面: 立即终止
            status, aborted_at, expected, actual, reason = \
                "aborted", k, goto, nxt, "branch_mismatch"
            break
        current = nxt
        if interrupt_after is not None and k == interrupt_after:
            status, reason = "interrupted", "interrupt_after"
            break

    if status == "completed":
        save_state(state_path, fid, completed, True)

    return {"status": status,
            "submitted": submitted,
            "skipped": skipped,
            "aborted_at": aborted_at,
            "expected": expected,
            "actual": actual,
            "reason": reason,
            "field_diff": diffs,
            "resume_from": resume_from,
            "resume_adjusted_to": adjusted,
            "final_payload": final_payload}


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    flow, script = load_json(argv[1]), load_json(argv[2])
    state_path = argv[4] if len(argv) > 4 else "state.json"
    try:
        result = run(flow, script, state_path)
    except ValueError as exc:
        result = {"status": "error", "message": str(exc)}
    with open(argv[3], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return 0 if result.get("status") in ("completed", "interrupted") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
