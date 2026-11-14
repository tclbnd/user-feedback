#!/usr/bin/env python3
"""多步表单向导确定性回放引擎.

定位方式: 以页面指纹 fp 匹配当前步骤(不依赖步骤序号), 因而分支合并、
可选步骤跳过、预填值变更都不会把值写进错误页面。

字段语义: text / select / date / checkbox 各自有规范化与校验规则;
预填值与脚本预期不一致时产出字段级差异 field_diff, 不中断回放。

断点恢复: 每完成一步即原子落盘 state.json(含 flow/script 内容哈希),
恢复时只信任哈希一致的已完成前缀, 已提交步骤不再重填。
"""
import datetime
import json
import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# 错误与规范化
# ---------------------------------------------------------------------------

class ReplayError(Exception):
    """回放无法继续(配置错误、状态不一致等)。"""


def _canonical_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canon_text(v):
    if not isinstance(v, str):
        raise ValueError("text 需要字符串, 得到 %r" % (type(v).__name__,))
    return v.strip()


def _canon_select(v):
    if isinstance(v, bool):  # True/False 不是合法选项
        raise ValueError("select 需要字符串选项, 得到 bool")
    if not isinstance(v, (str, int)):
        raise ValueError("select 需要字符串/整数选项, 得到 %s" % type(v).__name__)
    return str(v)


def _canon_date(v):
    """date 统一为 ISO YYYY-MM-DD(支持 2024/1/2 等输入, 校验真实日历)。"""
    if isinstance(v, bool):
        raise ValueError("date 非法布尔值")
    if not isinstance(v, str):
        raise ValueError("date 需要字符串, 得到 %s" % type(v).__name__)
    s = v.strip()
    parts = s.replace("/", "-").split("-")
    if len(parts) != 3:
        raise ValueError("date 格式非法: %r" % v)
    y, m, d = parts
    datetime.date(int(y), int(m), int(d))  # 非法日期(如 2024-02-30)抛 ValueError
    return "%04d-%02d-%02d" % (int(y), int(m), int(d))


def _canon_checkbox(v):
    """checkbox 稳定语义: 真→true / 假→false, 仅接受布尔或布尔语义字符串。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v in (0, 1):
            return bool(v)
        raise ValueError("checkbox 仅接受 0/1, 得到 %r" % v)
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "1", "yes", "on", "checked", "是", "勾选"):
            return True
        if low in ("false", "0", "no", "off", "unchecked", "否", "未勾选", ""):
            return False
    raise ValueError("checkbox 无法解析: %r" % v)


_CANON = {
    "text": _canon_text,
    "select": _canon_select,
    "date": _canon_date,
    "checkbox": _canon_checkbox,
}


def canonicalize(ftype, value):
    fn = _CANON.get(ftype)
    if fn is None:
        raise ReplayError("未知字段类型: %r" % ftype)
    return fn(value)


def _option_allowed(field, canon):
    """select 限定 options 时做枚举校验; 未声明 options 则只做类型规范化。"""
    opts = field.get("options")
    if not opts:
        return True
    return canon in [_canon_select(o) for o in opts]


# ---------------------------------------------------------------------------
# 运行时: 模拟向导页面, 以 fp 自报身份
# ---------------------------------------------------------------------------

class WizardRuntime:
    """模拟浏览器中的表单向导。

    current_fp 是页面"自报"的指纹, 引擎拿它和脚本 expect 比对——
    分支变化/意外页面都会在这里暴露, 而不会被序号掩盖。
    """

    def __init__(self, flow, inject=None):
        self.flow = flow
        self.pages = {p["fp"]: p for p in flow["pages"]}
        if len(self.pages) != len(flow["pages"]):
            raise ReplayError("flow 中存在重复 fp")
        entry = flow.get("entry", flow["pages"][0]["fp"])
        if entry not in self.pages:
            raise ReplayError("入口页 %r 不存在" % entry)
        self.current_fp = entry
        # inject: 注入意外页面, 形如 {"3": {"when": "observe", "fp": "pX"}}
        self.inject = inject or {}

    def _inject_spec(self, step_index):
        return self.inject.get(str(step_index)) or self.inject.get(step_index)

    def page(self, fp):
        if fp not in self.pages:
            raise ReplayError("flow 未定义页面 fp=%r" % fp)
        return self.pages[fp]

    def observe(self, step_index):
        """读取当前实际页面指纹(注入点 1: 分支点出现意外页面)。"""
        spec = self._inject_spec(step_index)
        if spec and spec.get("when", "observe") == "observe":
            return spec["fp"]
        return self.current_fp

    def resolve_next(self, fp, payload):
        """按 flow.next 规则求值: 支持 branch_on 或直接按载荷值匹配。"""
        page = self.page(fp)
        nxt = page.get("next")
        if not nxt:
            return None
        if "branch_on" in nxt:
            key = nxt["branch_on"]
            if key not in payload:
                raise ReplayError("页面 %s 的分支字段 %r 未出现在提交载荷中" % (fp, key))
            raw_key = payload[key]
            # 布尔分支值规范化为小写字符串, 与 flow 规则键(true/false)对齐
            rule_key = ("true" if raw_key is True else
                        "false" if raw_key is False else str(raw_key))
            table = nxt.get("rules", {})
        else:
            table = nxt
            rule_key = None
            for k in payload:  # 无 branch_on 时: 用载荷中能命中规则表的字段值
                raw = payload[k]
                cand = ("true" if raw is True else
                        "false" if raw is False else str(raw))
                if cand in table:
                    rule_key = cand
                    break
            if rule_key is None:
                rule_key = "default"
        target = table.get(rule_key, table.get("default"))
        if target is None:
            raise ReplayError("页面 %s 无法解析下一页面(规则键=%r)" % (fp, rule_key))
        if target in ("end", None):
            return None
        if target not in self.pages:
            raise ReplayError("页面 %s 的下一页面 %r 未在 flow 中定义" % (fp, target))
        return target

    def submit(self, step_index, fp, payload):
        """提交本页并按 flow 规则推进; 返回下一页面 fp(None 表示向导结束)。

        注入点 2: 提交后浏览器实际跳到意外页面(测试用 when=submit)。
        """
        nxt = self.resolve_next(fp, payload)
        spec = self._inject_spec(step_index)
        if spec and spec.get("when", "observe") == "submit":
            self.current_fp = spec["fp"]
        else:
            self.current_fp = nxt
        return nxt


# ---------------------------------------------------------------------------
# 状态持久化
# ---------------------------------------------------------------------------

def _load_state(path, flow_hash, script_hash):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise ReplayError("state.json 无法读取: %s" % exc)
    if state.get("flow_hash") != flow_hash or state.get("script_hash") != script_hash:
        raise ReplayError(
            "state.json 与当前 flow/script 哈希不一致, 拒绝在旧状态上继续")
    return state


def _save_state(path, state):
    if not path:
        return
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)  # 原子替换, 中断不会留下半截状态
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 回放引擎
# ---------------------------------------------------------------------------

class ReplayEngine:
    def __init__(self, flow, script, state_path=None, inject=None):
        self.flow = flow
        self.script = script
        self.state_path = state_path
        self.runtime = WizardRuntime(flow, inject=inject)
        self.flow_hash = _canonical_json(flow)
        self.script_hash = _canonical_json(script.get("steps"))
        self.field_index = {
            p["fp"]: {f["name"]: f for f in p.get("fields", [])}
            for p in flow["pages"]
        }

    # -- 单页填写与校验 -----------------------------------------------------
    def _build_payload(self, fp, step):
        """按字段类型规范化填写值, 并校验页面预填值, 产出 (payload, diffs)。"""
        fields = self.field_index.get(fp, {})
        fill = step.get("fill", {})
        prefill = step.get("prefill", {})
        payload, diffs = {}, []

        for name, raw in fill.items():
            field = fields.get(name)
            if field is None:
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "unknown_field", "expected": raw, "actual": None,
                    "detail": "页面 %s 不存在字段 %r" % (fp, name),
                })
                continue
            ftype = field.get("type", "text")
            try:
                value = canonicalize(ftype, raw)
            except ValueError as exc:
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "invalid_value", "type": ftype,
                    "expected": raw, "actual": None, "detail": str(exc),
                })
                continue
            if ftype == "select" and not _option_allowed(field, value):
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "option_not_allowed", "type": ftype,
                    "expected": value, "actual": None,
                    "options": field.get("options"),
                })
                continue
            payload[name] = value

        # 预填值校验: 实际页面的预填值若与脚本录制时不同, 记字段级差异
        for name, expected in prefill.items():
            field = fields.get(name)
            if field is None:
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "unknown_field", "expected": expected,
                    "actual": None,
                    "detail": "页面 %s 不存在字段 %r" % (fp, name),
                })
                continue
            ftype = field.get("type", "text")
            try:
                want = canonicalize(ftype, expected)
            except ValueError as exc:
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "invalid_expected", "type": ftype,
                    "expected": expected, "actual": None, "detail": str(exc),
                })
                continue
            got_raw = field.get("prefill")
            if got_raw is None:
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "prefill_missing", "type": ftype,
                    "expected": want, "actual": None,
                })
                continue
            try:
                got = canonicalize(ftype, got_raw)
            except ValueError:
                got = got_raw
            if got != want:
                diffs.append({
                    "step": step["_index"], "fp": fp, "field": name,
                    "kind": "prefill_mismatch", "type": ftype,
                    "expected": want, "actual": got,
                })
            # 预填值同样进入载荷: 真实向导会把预填值随表提交
            payload.setdefault(name, got)
        return payload, diffs

    # -- 恢复准备 -----------------------------------------------------------
    def _prepare_resume(self):
        state = _load_state(self.state_path, self.flow_hash, self.script_hash)
        completed, skipped, diffs, observed = [], [], [], []
        resume_idx = 0
        if state:
            entries = sorted(state.get("completed", []), key=lambda e: e["step"])
            for e in entries:
                completed.append(e)
                skipped.append(e["step"])          # 已提交步骤: 恢复时跳过, 不重填
                diffs.extend(e.get("field_diff", []))
                observed.append(e["fp"])
            resume_idx = (entries[-1]["step"] + 1) if entries else 0
            # 运行时直接定位到上次离开时的实际页面, 不依赖序号重放
            if state.get("current_fp"):
                if state["current_fp"] not in self.runtime.pages:
                    raise ReplayError("状态中的 current_fp 已不在 flow 中")
                self.runtime.current_fp = state["current_fp"]

        explicit = self.script.get("resume_from")
        if explicit is not None:
            if not state:
                raise ReplayError("resume_from=%s 但找不到一致的 state.json" % explicit)
            if explicit != resume_idx:
                raise ReplayError(
                    "resume_from=%s 与持久化进度不一致(最近一致步骤之后应为 %s)"
                    % (explicit, resume_idx))
        return state, completed, skipped, diffs, observed, resume_idx

    # -- 主流程 -------------------------------------------------------------
    def run(self):
        steps = self.script["steps"]
        for i, st in enumerate(steps):
            st["_index"] = i
        stop_after = self.script.get("stop_after")
        if stop_after is not None:
            stop_after = int(stop_after)

        state, completed, skipped, diffs, observed, idx = self._prepare_resume()
        if state is None:
            state = {
                "flow_hash": self.flow_hash,
                "script_hash": self.script_hash,
                "completed": [],
                "current_fp": self.runtime.current_fp,
            }

        result = {
            "submitted": [],
            "skipped": list(skipped),
            "aborted_at": None,
            "expected": None,
            "actual": None,
            "reason": None,
            "field_diff": list(diffs),
            "path": list(observed),
            "final_payload": {},
        }
        final_payload = {}
        for e in completed:  # 已提交载荷按原顺序并入, 保证恢复后最终载荷一致
            final_payload.update(e["payload"])

        n = len(steps)
        while idx < n:
            if stop_after is not None and idx > stop_after:
                break
            st = steps[idx]
            expect = st["expect"]
            if expect not in self.field_index:
                raise ReplayError("脚本第 %s 步 expect=%r 在 flow 中不存在" % (idx, expect))

            actual = self.runtime.observe(idx)
            if actual != expect:
                # 可选步骤被跳过时: 实际页已推进到后续页面, 记 skipped 后对齐
                if st.get("optional"):
                    result["skipped"].append(idx)
                    result["path"].append(None)  # None 占位: 该可选页本次未出现
                    idx += 1
                    continue
                result.update({
                    "aborted_at": idx, "expected": expect,
                    "actual": actual, "reason": "fingerprint_mismatch",
                })
                self._persist(state, result, finalize=False)
                return result

            result["path"].append(actual)
            payload, step_diffs = self._build_payload(actual, st)
            result["field_diff"].extend(step_diffs)

            entry = {
                "step": idx, "fp": actual, "payload": payload,
                "field_diff": step_diffs,
            }
            result["submitted"].append(entry)
            final_payload.update(payload)

            goto = self.runtime.submit(idx, actual, payload)
            # 脚本声明的 goto 与 flow 规则求值结果交叉校验
            if st.get("goto") is not None and st["goto"] != (goto or "end"):
                result.update({
                    "aborted_at": idx, "expected": st["goto"],
                    "actual": goto or "end", "reason": "goto_mismatch",
                })
                self._persist(state, result, finalize=False)
                return result

            # 每页提交成功后立即持久化(崩溃时最近一致步骤可恢复)
            state["completed"].append(entry)
            state["current_fp"] = self.runtime.current_fp
            _save_state(self.state_path, state)

            idx += 1

        result["final_payload"] = final_payload
        all_done = (idx >= n) and result["aborted_at"] is None
        self._persist(state, result, finalize=all_done)
        return result

    def _persist(self, state, result, finalize):
        if not self.state_path:
            return
        state["last_result"] = {k: v for k, v in result.items() if k != "final_payload"}
        state["finished"] = bool(finalize)
        _save_state(self.state_path, state)


def run(flow, script, state_path=None, inject=None):
    return ReplayEngine(flow, script, state_path=state_path, inject=inject).run()


def main(argv):
    if len(argv) < 4:
        print("用法: python3 wizard.py flow.json script.json output.json [state.json]",
              file=sys.stderr)
        return 2
    flow_path, script_path, out_path = argv[1], argv[2], argv[3]
    state_path = argv[4] if len(argv) > 4 else None
    with open(flow_path, "r", encoding="utf-8") as fh:
        flow = json.load(fh)
    with open(script_path, "r", encoding="utf-8") as fh:
        script = json.load(fh)
    try:
        result = run(flow, script, state_path=state_path)
    except ReplayError as exc:
        result = {
            "submitted": [], "skipped": [], "aborted_at": None,
            "expected": None, "actual": None, "reason": "replay_error",
            "field_diff": [], "path": [], "final_payload": {},
            "error": str(exc),
        }
        print("回放终止: %s" % exc, file=sys.stderr)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
