"""多版本离线表单采集器.

按 模板版本 + 可访问性标签 + 字段组(fieldset/legend) + 表单上下文 定位字段,
绝不按控件位置猜值. 状态机持久化到 root/.collect-state.json, 公开视图
原子写入 root/view.json, 保证进程中断后不会留下半条记录, 且每个任务键只
有一份结果.

用法:
  python3 forms.py ROOT collect jobs.json [--only ID] [--max-attempts N]
      [--backoff SECONDS] [--templates registry.json]
      [--crash during-extract|after-save]
  python3 forms.py ROOT inspect [--verbose]

测试钩子(环境变量):
  ZQ_FAULT=during-extract|after-save   等效 --crash, 默认作用于首个任务
  ZQ_RETRY_FAULT=<task-id>:<k>         该任务前 k 次尝试抛瞬时错误以演练重试
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

COLLECTOR_VERSION = "2.0.0"
STATE_NAME = ".collect-state.json"
VIEW_NAME = "view.json"
EXIT_CRASH = 97

# 终态分类(进入 review 的原因码)
REVIEW_REASONS = {
    "UNKNOWN_TEMPLATE", "AMBIGUOUS_FIELD", "MISSING_FIELD",
    "TIMEOUT", "EXTRACT_ERROR",
}
TERMINAL_STATES = {"DONE", "REVIEW"}


# --------------------------------------------------------------------------- #
# 模板注册表: 版本 -> 字段定位规则. 标签别名必须在此显式登记, 不做模糊猜测.
# --------------------------------------------------------------------------- #
def _field(group: str, labels: List[str], required: str = "always") -> Dict[str, Any]:
    return {"group": group, "labels": labels, "required": required}


BUILTIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "v1": {"fields": {
        "buyer": _field("Buyer", ["Name", "Full name"]),
        "contact": _field("Contact", ["Name", "Full name"]),
        "route": _field("Routing", ["Route", "Route code"], "routing_on"),
    }},
    "v2": {"fields": {
        "buyer": _field("Buyer", ["Name", "Full name"]),
        "contact": _field("Contact", ["Name", "Full name"]),
        "route": _field("Routing", ["Route", "Route code"], "routing_on"),
    }},
    "v3": {"fields": {
        "buyer": _field("Buyer", ["Name", "Full name"]),
        "contact": _field("Contact", ["Name", "Full name"]),
        "route": _field("Routing", ["Route", "Route code"], "routing_on"),
    }},
}

VOID_TAGS = {"input", "img", "br", "hr", "meta", "link", "area", "base",
             "col", "embed", "param", "source", "track", "wbr"}


# --------------------------------------------------------------------------- #
# 轻量 DOM: 标准库 html.parser 构建, 无网络依赖
# --------------------------------------------------------------------------- #
class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: Dict[str, Optional[str]], parent: "Optional[Node]"):
        self.tag = tag
        self.attrs = attrs
        self.children: List[Node] = []
        self.parent = parent
        self.text = ""

    def find_all(self, tag: str) -> List["Node"]:
        out: List[Node] = []
        stack = [self]
        while stack:
            cur = stack.pop()
            for ch in cur.children:
                if ch.tag == tag:
                    out.append(ch)
                stack.append(ch)
        return out

    def nearest(self, tag: str) -> "Optional[Node]":
        cur = self.parent
        while cur is not None:
            if cur.tag == tag:
                return cur
            cur = cur.parent
        return None

    def dom_path(self) -> str:
        """形如 form[0]>fieldset[2]>input[0] 的稳定 DOM 位置."""
        chain: List[str] = []
        cur: Optional[Node] = self
        while cur is not None and cur.parent is not None:
            same_tag = [c for c in cur.parent.children if c.tag == cur.tag]
            idx = same_tag.index(cur)
            chain.append(f"{cur.tag}[{idx}]")
            cur = cur.parent
        return ">".join(reversed(chain))


class _DOMBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        node = Node(tag, {k.lower(): v for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: List[tuple]) -> None:
        node = Node(tag, {k.lower(): v for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].text += data


def parse_html(html: str) -> Node:
    builder = _DOMBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").split()).strip().lower()


def _is_hidden(node: Node) -> bool:
    cur: Optional[Node] = node
    while cur is not None:
        if "hidden" in cur.attrs:
            return True
        cur = cur.parent
    return (node.attrs.get("type") or "").lower() == "hidden"


def _legend_text(fieldset: Node) -> str:
    for ch in fieldset.children:
        if ch.tag == "legend":
            return " ".join(ch.text.split())
    return ""


def _label_text(label: Node) -> str:
    return " ".join(label.text.split())


class ExtractionError(Exception):
    """瞬时错误: IO/解析失败, 可重试."""


# --------------------------------------------------------------------------- #
# 快照解析与字段定位
# --------------------------------------------------------------------------- #
class Snapshot:
    def __init__(self, file_name: str, form: Node, dom: Node,
                 template: str, ready: bool, routing_on: bool):
        self.file_name = file_name
        self.form = form
        self.dom = dom
        self.template = template
        self.ready = ready
        self.routing_on = routing_on

    @classmethod
    def load(cls, path: Path) -> "Snapshot":
        try:
            html = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ExtractionError(f"无法读取快照 {path.name}: {exc}") from exc
        try:
            dom = parse_html(html)
            forms = dom.find_all("form")
            if not forms:
                raise ExtractionError(f"快照 {path.name} 中没有 form 元素")
            form = forms[0]
            a = form.attrs
            return cls(
                path.name, form, dom,
                template=a.get("data-template") or "",
                ready=(a.get("data-ready") or "").lower() == "true",
                routing_on=(a.get("data-routing") or "").lower() == "on",
            )
        except ExtractionError:
            raise
        except Exception as exc:  # 解析器自身故障视为瞬时错误
            raise ExtractionError(f"快照 {path.name} 解析失败: {exc}") from exc


class Control:
    """一个可参与定位的 input 控件及其上下文."""

    def __init__(self, node: Node, label_map: Dict[str, Node]):
        self.node = node
        self.id = node.attrs.get("id")
        self.name = node.attrs.get("name")
        self.value = node.attrs.get("value", "") or ""
        self.disabled = "disabled" in node.attrs
        self.hidden = _is_hidden(node)
        fieldset = node.nearest("fieldset")
        self.group = _legend_text(fieldset) if fieldset is not None else ""
        self.dom_path = node.dom_path()
        # 可访问性标签: aria-label 优先, 其次 label[for], 再次包裹 label
        self.label_kind: str
        aria = " ".join((node.attrs.get("aria-label") or "").split())
        wrap = node.nearest("label")
        if aria:
            self.label, self.label_kind = aria, "aria-label"
        elif self.id and self.id in label_map:
            self.label, self.label_kind = _label_text(label_map[self.id]), "label-for"
        elif wrap is not None:
            self.label, self.label_kind = _label_text(wrap), "label-wrap"
        else:
            self.label, self.label_kind = "", "none"

    def location(self) -> Dict[str, Any]:
        return {
            "dom_id": self.id,
            "dom_path": self.dom_path,
            "group": self.group,
            "label": self.label,
            "label_kind": self.label_kind,
            "name": self.name,
            "value": self.value,
            "hidden": self.hidden,
            "disabled": self.disabled,
        }


def _controls(snap: Snapshot) -> List[Control]:
    label_map = {lb.attrs.get("for"): lb for lb in snap.form.find_all("label")
                 if lb.attrs.get("for")}
    out: List[Control] = []
    for inp in snap.form.find_all("input"):
        ctl = Control(inp, label_map)
        if ctl.hidden or ctl.disabled:
            continue  # 隐藏/禁用控件忽略
        out.append(ctl)
    return out


def _registry_hash(templates: Dict[str, Any]) -> str:
    blob = json.dumps(templates, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _input_fingerprint(job: Dict[str, Any], jobs_path: Path,
                       registry_hash: str) -> str:
    """任务输入指纹: 快照内容 + 模板注册表, 输入变化即触发重新采集."""
    h = hashlib.sha256()
    h.update(COLLECTOR_VERSION.encode())
    h.update(registry_hash.encode())
    h.update(json.dumps(job, sort_keys=True).encode())
    for name in job["snapshots"]:
        p = jobs_path.parent / name
        h.update(name.encode())
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:20]


def stable_task_key(job_id: str) -> str:
    return hashlib.sha256(("form-collect:" + job_id).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 核心抽取
# --------------------------------------------------------------------------- #
def _locate(snap: Snapshot, templates: Dict[str, Any]) -> Dict[str, Any]:
    """返回 outcome=ok 或隔离原因(UNKNOWN/AMBIGUOUS/MISSING)."""
    template = snap.template
    if template not in templates:
        controls = _controls(snap)
        return {"outcome": "UNKNOWN_TEMPLATE", "review": {
            "reason": "UNKNOWN_TEMPLATE",
            "file": snap.file_name,
            "template": template,
            "known_templates": sorted(templates),
            "snapshot": snap.file_name,
            "dom_locations": [c.location() for c in controls],
            "detail": "模板版本未在注册表登记, 拒绝按位置猜测字段",
            "needs_manual": True,
        }}

    spec = templates[template]["fields"]
    controls = _controls(snap)
    fields: Dict[str, Optional[str]] = {}
    sources: Dict[str, Optional[Dict[str, Any]]] = {}
    diagnostics: List[Dict[str, Any]] = []

    for field_key, rule in spec.items():
        required = rule["required"] == "always" or (
            rule["required"] == "routing_on" and snap.routing_on)
        allowed = {_norm(x) for x in rule["labels"]}
        group = _norm(rule["group"])
        candidates = [
            c for c in controls
            if _norm(c.group) == group
            and c.label_kind != "none"
            and _norm(c.label) in allowed
        ]
        if not candidates:
            if not required:
                fields[field_key] = None  # 条件字段未启用
                sources[field_key] = None
                continue
            diagnostics.append({
                "field": field_key, "kind": "MISSING_FIELD",
                "group": rule["group"], "allowed_labels": rule["labels"],
                "controls_seen": [c.location() for c in controls],
            })
            continue
        if len(candidates) > 1:
            diagnostics.append({
                "field": field_key, "kind": "AMBIGUOUS_FIELD",
                "group": rule["group"], "allowed_labels": rule["labels"],
                "candidates": [c.location() for c in candidates],
            })
            continue
        ctl = candidates[0]
        fields[field_key] = ctl.value
        sources[field_key] = {
            "dom_id": ctl.id,
            "dom_path": ctl.dom_path,
            "group": ctl.group,
            "label": ctl.label,
            "label_kind": ctl.label_kind,
            "snapshot": snap.file_name,
        }

    if diagnostics:
        ambiguous = [d for d in diagnostics if d["kind"] == "AMBIGUOUS_FIELD"]
        reason = "AMBIGUOUS_FIELD" if ambiguous else "MISSING_FIELD"
        return {"outcome": reason, "review": {
            "reason": reason,
            "file": snap.file_name,
            "template": template,
            "snapshot": snap.file_name,
            "diagnostics": diagnostics,
            "detail": "字段定位存在多义候选或必填缺失, 隔离该记录不进入结果表",
            "needs_manual": True,
        }}

    return {"outcome": "ok", "row": {
        "template": template,
        "fields": fields,
        "sources": {k: (v["dom_id"] if v else None) for k, v in sources.items()},
        "field_sources": sources,
        "snapshot": snap.file_name,
        "routing_on": snap.routing_on,
    }}


def _attempt(job: Dict[str, Any], jobs_path: Path,
             templates: Dict[str, Any]) -> Dict[str, Any]:
    """一次完整尝试: 选择首个 ready 快照并定位字段."""
    snaps: List[Snapshot] = []
    load_errors: List[str] = []
    for name in job["snapshots"]:
        try:
            snaps.append(Snapshot.load(jobs_path.parent / name))
        except ExtractionError as exc:
            load_errors.append(str(exc))
    if load_errors and not snaps:
        raise ExtractionError("; ".join(load_errors))

    ready = next((s for s in snaps if s.ready), None)
    if ready is None:
        return {"outcome": "TIMEOUT", "review": {
            "reason": "TIMEOUT",
            "file": snaps[-1].file_name if snaps else job["snapshots"][-1],
            "template": snaps[-1].template if snaps else "",
            "snapshots": [
                {"file": s.file_name, "ready": s.ready,
                 "template": s.template} for s in snaps
            ],
            "load_errors": load_errors,
            "detail": "所有快照 data-ready 均非 true, 表单未完成渲染",
            "needs_manual": True,
        }}
    return _locate(ready, templates)


# --------------------------------------------------------------------------- #
# 持久化状态机: PENDING -> EXTRACTING -> (RETRYING|WAITING) -> DONE/REVIEW
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_state(registry_hash: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "collector_version": COLLECTOR_VERSION,
        "registry_hash": registry_hash,
        "tasks": {},
        "events": [],
    }


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_state(root: Path, registry_hash: str) -> Dict[str, Any]:
    p = root / STATE_NAME
    if p.exists():
        state = json.loads(p.read_text(encoding="utf-8"))
        if state.get("registry_hash") != registry_hash:
            state["registry_hash"] = registry_hash
            state["tasks"] = {}  # 注册表变更, 旧结论全部作废重采
        return state
    return _new_state(registry_hash)


def _event(state: Dict[str, Any], run_id: str, job_id: str,
           event: str, **detail: Any) -> None:
    state["events"].append({"ts": _now(), "run_id": run_id,
                            "id": job_id, "event": event, "detail": detail})


def _public_view(state: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    for job_id in sorted(state["tasks"]):
        task = state["tasks"][job_id]
        if task["state"] == "DONE" and task.get("result"):
            rows.append({"id": job_id, **task["result"]})
        elif task["state"] == "REVIEW" and task.get("review"):
            review.append({"id": job_id, **task["review"]})
    return {"rows": rows, "review": review}


def _retry_fault() -> Dict[str, str]:
    raw = os.environ.get("ZQ_RETRY_FAULT", "")
    if not raw:
        return {}
    job_id, _, count = raw.partition(":")
    return {"id": job_id, "count": count or "1"}


def _crash_target(crash: Optional[str], first_id: str) -> Optional[str]:
    mode = crash or os.environ.get("ZQ_FAULT")
    if not mode:
        return None
    return f"{mode}:{first_id}"


def run_collect(root: Path, jobs_path: Path, only: Optional[str],
                max_attempts: int, backoff: float,
                templates: Dict[str, Any], crash: Optional[str]) -> Dict[str, Any]:
    registry_hash = _registry_hash(templates)
    root.mkdir(parents=True, exist_ok=True)
    state = _load_state(root, registry_hash)
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"]
    if only:
        jobs = [j for j in jobs if j["id"] == only]
        if not jobs:
            raise SystemExit(f"--only {only}: 任务不存在")
    run_id = hashlib.sha1(_now().encode()).hexdigest()[:12]
    crash_spec = _crash_target(crash, jobs[0]["id"] if jobs else "")
    fault = _retry_fault()

    for job in jobs:
        job_id = job["id"]
        fp = _input_fingerprint(job, jobs_path, registry_hash)
        task = state["tasks"].get(job_id)
        if task is None:
            task = state["tasks"][job_id] = {
                "id": job_id, "task_key": stable_task_key(job_id),
                "state": "PENDING", "attempts": [], "result": None,
                "review": None, "fingerprint": fp, "run_id": run_id,
                "updated_at": _now(),
            }
        elif only == job_id:
            # 针对单条重新采集: 显式重置, 但保留历史尝试痕迹
            task["state"] = "PENDING"
            task["result"] = task["review"] = None
            task["fingerprint"] = fp
            _event(state, run_id, job_id, "manual_recollect")
        elif task["state"] in TERMINAL_STATES and task.get("fingerprint") == fp:
            _event(state, run_id, job_id, "skip_terminal",
                   terminal_state=task["state"])
            continue
        elif task.get("fingerprint") != fp:
            _event(state, run_id, job_id, "input_changed",
                   old=task.get("fingerprint"), new=fp)
            task["state"], task["result"], task["review"] = "PENDING", None, None
            task["attempts"], task["fingerprint"] = [], fp

        if task["state"] == "EXTRACTING":
            # 上次进程在字段解析中途死亡: 记录中断后重试
            task["attempts"].append({"run_id": task.get("run_id"),
                                     "attempt": len(task["attempts"]) + 1,
                                     "outcome": "interrupted",
                                     "ts": _now()})
            _event(state, run_id, job_id, "resume_interrupted")
            task["state"] = "PENDING"

        last_outcome = None
        for attempt_no in range(1, max_attempts + 1):
            task["state"] = "EXTRACTING"
            task["run_id"] = run_id
            task["updated_at"] = _now()
            _atomic_write(root / STATE_NAME, state)  # 先落状态再解析
            _event(state, run_id, job_id, "attempt_start",
                   attempt=attempt_no)

            if crash_spec == f"during-extract:{job_id}":
                _atomic_write(root / STATE_NAME, state)
                sys.stderr.write(f"[fault] during-extract crash on {job_id}\n")
                os._exit(EXIT_CRASH)

            try:
                if fault.get("id") == job_id and attempt_no <= int(fault["count"]):
                    raise ExtractionError("注入的瞬时解析故障")
                record = _attempt(job, jobs_path, templates)
            except ExtractionError as exc:
                last_outcome = "transient_error"
                task["attempts"].append({
                    "run_id": run_id, "attempt": attempt_no,
                    "outcome": "transient_error", "error": str(exc),
                    "ts": _now()})
                _event(state, run_id, job_id, "retry_scheduled",
                       attempt=attempt_no, error=str(exc))
                task["state"] = "RETRYING"
                _atomic_write(root / STATE_NAME, state)
                if attempt_no < max_attempts:
                    time.sleep(backoff)
                    continue
                task["state"] = "REVIEW"
                task["review"] = {
                    "reason": "EXTRACT_ERROR", "file": job["snapshots"][-1],
                    "detail": f"重试 {max_attempts} 次仍失败: {exc}",
                    "needs_manual": True,
                }
                break

            outcome = record["outcome"]
            if outcome == "ok":
                result = record["row"]
                result["collector_version"] = COLLECTOR_VERSION
                result["registry_hash"] = registry_hash
                result["task_key"] = task["task_key"]
                result["fingerprint"] = fp
                task["result"] = result
                task["state"] = "DONE"
                task["attempts"].append({"run_id": run_id,
                                         "attempt": attempt_no,
                                         "outcome": "ok",
                                         "snapshot": result["snapshot"],
                                         "ts": _now()})
                last_outcome = "ok"
                break
            # 确定性分类: TIMEOUT 可等待重试, 其余隔离后不再重试
            task["attempts"].append({"run_id": run_id, "attempt": attempt_no,
                                     "outcome": outcome,
                                     "snapshot": record["review"].get("file"),
                                     "ts": _now()})
            if outcome == "TIMEOUT" and attempt_no < max_attempts:
                task["state"] = "WAITING"
                _event(state, run_id, job_id, "waiting_retry",
                       attempt=attempt_no)
                _atomic_write(root / STATE_NAME, state)
                time.sleep(backoff)
                last_outcome = outcome
                continue
            task["state"] = "REVIEW"
            task["review"] = record["review"]
            task["review"]["classification"] = outcome
            task["review"]["attempts"] = attempt_no
            last_outcome = outcome
            break

        task["updated_at"] = _now()
        _atomic_write(root / STATE_NAME, state)
        # 每个任务终态后刷新公开视图(原子替换, 永远不含中间态)
        _atomic_write(root / VIEW_NAME, _public_view(state))

        if crash_spec == f"after-save:{job_id}":
            sys.stderr.write(f"[fault] after-save crash on {job_id}\n")
            os._exit(EXIT_CRASH)
        _event(state, run_id, job_id, "task_terminal",
               terminal_state=task["state"], outcome=last_outcome)
        _atomic_write(root / STATE_NAME, state)

    _atomic_write(root / VIEW_NAME, _public_view(state))
    return _public_view(state)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(description="多版本离线表单采集器")
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)
    q = sub.add_parser("collect")
    q.add_argument("jobs", type=Path)
    q.add_argument("--only", help="只重新采集指定任务 id")
    q.add_argument("--max-attempts", type=int, default=3)
    q.add_argument("--backoff", type=float, default=0.02)
    q.add_argument("--templates", type=Path,
                   help="外部模板注册表 JSON, 与内置注册表合并")
    q.add_argument("--crash", choices=["during-extract", "after-save"])
    ins = sub.add_parser("inspect")
    ins.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)

    if a.op == "inspect":
        view_path = a.root / VIEW_NAME
        if not view_path.exists():
            print(json.dumps({"rows": [], "review": []}, ensure_ascii=False))
            return
        view = json.loads(view_path.read_text(encoding="utf-8"))
        if a.verbose and (a.root / STATE_NAME).exists():
            state = json.loads((a.root / STATE_NAME).read_text("utf-8"))
            view["_state_summary"] = {tid: t["state"] for tid, t in
                                      sorted(state["tasks"].items())}
            view["_events"] = len(state["events"])
        print(json.dumps(view, ensure_ascii=False, sort_keys=True))
        return

    templates = copy.deepcopy(BUILTIN_TEMPLATES)
    if a.templates:
        external = json.loads(a.templates.read_text(encoding="utf-8"))
        templates.update(external.get("templates", external))
    view = run_collect(a.root, a.jobs, a.only, a.max_attempts,
                       a.backoff, templates, a.crash)
    if a.only:  # 单条重采只回显该任务, 落盘的完整视图不受影响
        view = {
            "rows": [r for r in view["rows"] if r["id"] == a.only],
            "review": [r for r in view["review"] if r["id"] == a.only],
        }
    print(json.dumps(view, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
