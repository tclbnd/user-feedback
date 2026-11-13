"""分区回填与发布引擎.

实现要点:
  * DAG 节点分区依赖映射: 多父节点、滚动区间、跨时区划分在清单中展开,
    支持缺失分区(贡献 0 且有独立指纹), 拒绝循环。
  * 版本指纹: 源按 ["source",key,version,value] 规范化 JSON 取 SHA-256;
    原样重传(version/value 均未变)不触发任何重算。
  * 确定性回填计划: 按拓扑层 + 键升序排序, 标注每个目标分区的触发路径。
  * 分层暂存与恢复: 每完成一层原子落盘; --crash-after-layer 注入中断;
    恢复时按新依赖指纹判定暂存是否仍有效, 失效重算。
  * 可追踪版本清单: 原子发布 values/fingerprints/manifest; 部分失败或
    运行中依赖变化时拒绝发布, 已发布旧结果保持可读。
"""
import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

EXIT_OK = 0
EXIT_CYCLE = 2
EXIT_CRASH = 97          # 注入的中断
EXIT_STALE = 98          # 运行中依赖变化, 拒绝发布陈旧结果
EXIT_NODE_FAILED = 99    # 节点计算失败, 禁止部分发布

# 测试钩子(生产环境留空): before_layer(ctx) 可在层处理前修改清单文件
HOOKS = {"before_layer": None}


class CycleError(Exception):
    """DAG 中检测到循环依赖。"""


class StaleDependencyError(Exception):
    """执行过程中输入清单发生变化, 继续发布会得到陈旧结果。"""


class NodeFailureError(Exception):
    """节点计算注入/真实失败。"""


def env_manifest_mutation(ctx):
    """内置演练钩子: 在指定层开始前改写清单, 用于验证运行中陈旧检测。

    BACKFILL_MUTATE_AT_LAYER=<层号>;BACKFILL_MUTATE_SOURCE=<键>;
    BACKFILL_MUTATE_VALUE=<新值>;BACKFILL_MUTATE_VERSION=<新版本>
    """
    spec = os.environ.get("BACKFILL_MUTATE_AT_LAYER")
    if spec is None or ctx["layer"] != int(spec):
        return
    path = Path(ctx["manifest"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    key = os.environ["BACKFILL_MUTATE_SOURCE"]
    doc["inputs"][key]["value"] = int(os.environ["BACKFILL_MUTATE_VALUE"])
    doc["inputs"][key]["version"] = os.environ["BACKFILL_MUTATE_VERSION"]
    atomic_write_json(path, doc)


def canonical(obj):
    """规范 JSON: UTF-8、紧凑分隔符、ensure_ascii=False、无结尾换行。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sha256(obj):
    payload = obj if isinstance(obj, (bytes, bytearray)) else canonical(obj)
    return hashlib.sha256(payload).hexdigest()


def source_fingerprint(key, entry):
    return sha256(["source", key, str(entry["version"]), int(entry["value"])])


def missing_fingerprint(key):
    return sha256(["missing", key])


def node_fingerprint(key, dep_fps):
    return sha256(["node", key, list(dep_fps)])


# --------------------------------------------------------------------------
# DAG 结构: 拓扑分层 + 循环检测
# --------------------------------------------------------------------------
def topological_layers(nodes):
    """返回按依赖深度划分的节点层; 每层内部按键升序, 保证确定性。"""
    color = {}
    depth = {}
    stack = []

    def visit(k):
        state = color.get(k, 0)
        if state == 1:
            cyc = stack[stack.index(k):] + [k]
            raise CycleError("循环依赖: " + " -> ".join(cyc))
        if state == 2:
            return depth[k]
        color[k] = 1
        stack.append(k)
        d = 0
        for dep in nodes[k]:
            if dep in nodes:  # 依赖指向其他派生节点
                d = max(d, visit(dep) + 1)
        stack.pop()
        color[k] = 2
        depth[k] = d
        return d

    for key in sorted(nodes):
        visit(key)

    layers = defaultdict(list)
    for key, d in depth.items():
        layers[d].append(key)
    return [sorted(layers[i]) for i in sorted(layers)]


# --------------------------------------------------------------------------
# 目标全图计算(纯函数): 值 + 指纹
# --------------------------------------------------------------------------
def compute_graph(inputs, nodes):
    """依据当前清单计算全部派生节点的值与指纹。

    未知键即缺失输入: 贡献 0, 指纹为 ["missing", key]。
    节点值为依赖 value 的整数和, 依赖列表顺序保留在指纹中。
    """
    values = {}
    fingerprints = {}
    for layer in topological_layers(nodes):
        for key in layer:
            dep_fps = []
            total = 0
            for dep in nodes[key]:
                if dep in inputs:
                    entry = inputs[dep]
                    total += int(entry["value"])
                    dep_fps.append(source_fingerprint(dep, entry))
                elif dep in values:
                    total += int(values[dep])
                    dep_fps.append(fingerprints[dep])
                else:
                    dep_fps.append(missing_fingerprint(dep))
            values[key] = total
            fingerprints[key] = node_fingerprint(key, dep_fps)
    return values, fingerprints


def source_snapshot(inputs):
    """源分区当前指纹快照, 用于检测运行中清单变化。"""
    return dict(sorted(
        (k, source_fingerprint(k, v)) for k, v in inputs.items()))


# --------------------------------------------------------------------------
# ROOT 下的状态路径与原子 JSON 读写
# --------------------------------------------------------------------------
class Paths:
    def __init__(self, root):
        self.root = Path(root)
        self.published = self.root / "published.json"
        self.meta = self.root / "published.meta.json"
        self.stage_dir = self.root / "staging"
        self.progress = self.stage_dir / "progress.json"

    def stage_node(self, key):
        safe = key.replace("%", "%25").replace("/", "%2F")
        return self.stage_dir / (safe + ".json")


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def graph_signature(nodes):
    """DAG 结构指纹: 依赖映射本身发生变化也会使计划/暂存失效。"""
    return sha256(dict(sorted((k, list(v)) for k, v in nodes.items())))


# --------------------------------------------------------------------------
# 确定性回填计划
# --------------------------------------------------------------------------
def make_plan(inputs, nodes, published, staged):
    """生成确定性计划。

    返回 (plan, layers, trigger_roots, trigger_paths):
      action: reuse-published / reuse-staged / execute
      trigger_roots: 引发本次重算的源头(变化源或指纹变化节点)
      trigger_paths[key]: 从触发源头到 key 的全部简单依赖路径
    """
    layers = topological_layers(nodes)
    order = [k for layer in layers for k in layer]
    target_values, target_fps = compute_graph(inputs, nodes)

    pub_fp = (published or {}).get("fingerprints", {})
    pub_val = (published or {}).get("values", {})
    staged_fp = dict((k, v["fingerprint"]) for k, v in (staged or {}).items())

    old_inputs = (published or {}).get("sources", {})
    changed_sources = set()
    for key in sorted(set(old_inputs) | set(inputs)):
        old_fp = old_inputs.get(key, {}).get("fingerprint")
        new_fp = source_fingerprint(key, inputs[key]) if key in inputs else None
        if old_fp != new_fp:
            changed_sources.add(key)  # 含新增源与被删除源

    old_graph = (published or {}).get("graph_signature")
    structure_changed = old_graph is not None and \
        old_graph != graph_signature(nodes)

    plan = {}
    for key in order:
        fp = target_fps[key]
        if pub_fp.get(key) == fp and pub_val.get(key) == target_values[key]:
            action = "reuse-published"
        elif staged_fp.get(key) == fp and \
                (staged or {}).get(key, {}).get("value") == target_values[key]:
            action = "reuse-staged"
        else:
            action = "execute"
        plan[key] = {"action": action, "fingerprint": fp,
                     "value": target_values[key]}

    # 触发根: 变化的源分区 + 目标指纹变化节点(覆盖结构调整、缺失->补齐)
    roots = set(changed_sources)
    if structure_changed:
        for key in order:
            if pub_fp.get(key) != target_fps[key]:
                roots.add(key)

    # 反向邻接(仅派生边), 用于自根向下枚举触发路径
    reverse = defaultdict(list)
    for key in order:
        for dep in nodes[key]:
            reverse[dep].append(key)

    def enumerate_paths(root, goal):
        found = []

        def dfs(node, path):
            if node == goal:
                found.append(list(path))
                return
            for nxt in sorted(reverse.get(node, ())):
                if nxt not in path:  # 简单路径, DAG 无环必然终止
                    path.append(nxt)
                    dfs(nxt, path)
                    path.pop()

        dfs(root, [root])
        return found

    trigger_paths = {}
    for key in order:
        if plan[key]["action"] == "execute":
            paths = []
            for root in sorted(roots):
                paths.extend(enumerate_paths(root, key))
            if not paths:
                paths = [[key]]  # 无已发布基线(首次构建)等情况
            trigger_paths[key] = sorted(set(tuple(p) for p in paths))
    return plan, layers, sorted(roots), trigger_paths


# --------------------------------------------------------------------------
# 分层执行、暂存、中断与恢复
# --------------------------------------------------------------------------
def run_build(root, manifest_path, crash_after_layer=None, resume=False):
    paths = Paths(root)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.stage_dir.mkdir(parents=True, exist_ok=True)

    manifest = read_json(manifest_path)
    inputs = manifest["inputs"]
    nodes = manifest["nodes"]
    layers = topological_layers(nodes)  # 先做循环校验
    order = [k for layer in layers for k in layer]

    published = read_json(paths.published, None)
    meta_doc = read_json(paths.meta, None)
    if published is not None and meta_doc is not None:
        # 规划需要历史源指纹与结构指纹, 它们保存在版本清单 meta 中
        published = dict(published)
        for key in ("sources", "graph_signature", "layers"):
            if key in meta_doc:
                published[key] = meta_doc[key]
    progress = read_json(paths.progress, None)
    if resume and progress is None:
        raise FileNotFoundError("无可恢复的暂存进度: " + str(paths.progress))
    staged = progress["staged"] if progress else {}
    # 清单可能已删除节点: 丢弃不在当前 DAG 中的孤儿暂存索引
    staged = dict((k, v) for k, v in staged.items() if k in nodes)

    plan, layers, roots, trigger_paths = make_plan(
        inputs, nodes, published, staged)

    # 运行中依赖变化检测基线
    fresh_sources = source_snapshot(inputs)
    fresh_graph = graph_signature(nodes)
    fresh_manifest = sha256(canonical(manifest))

    progress_doc = {
        "status": "running",
        "plan": plan,
        "layers": layers,
        "trigger_roots": roots,
        "trigger_paths": dict((k, [list(p) for p in v])
                              for k, v in trigger_paths.items()),
        "staged": staged,
        "completed_layers": [],
        "source_snapshot": fresh_sources,
        "graph_signature": fresh_graph,
        "manifest_fingerprint": fresh_manifest,
    }
    atomic_write_json(paths.progress, progress_doc)

    available = {}
    executed = []

    def assert_fresh():
        """重新读取清单, 依赖在运行中变化则拒绝继续/发布。"""
        current = read_json(manifest_path)
        if source_snapshot(current["inputs"]) != fresh_sources \
                or graph_signature(current["nodes"]) != fresh_graph \
                or sha256(canonical(current)) != fresh_manifest:
            raise StaleDependencyError("运行中输入清单发生变化")

    fail_node = os.environ.get("BACKFILL_FAIL_NODE")
    for idx, layer in enumerate(layers, start=1):
        assert_fresh()
        if os.environ.get("BACKFILL_MUTATE_AT_LAYER"):
            env_manifest_mutation({"manifest": manifest_path, "layer": idx})
        if HOOKS["before_layer"] is not None:
            HOOKS["before_layer"]({"root": paths.root, "layer": idx,
                                   "manifest": Path(manifest_path)})
        assert_fresh()
        for key in layer:
            entry = plan[key]
            if entry["action"] == "execute":
                if fail_node == key:
                    raise NodeFailureError("节点 " + key + " 计算失败")
                # 依赖值实时解析: 已发布/已暂存结果在本层前已进入 available
                total = 0
                dep_fps = []
                for dep in nodes[key]:
                    if dep in inputs:
                        total += int(inputs[dep]["value"])
                        dep_fps.append(source_fingerprint(dep, inputs[dep]))
                    elif dep in available:
                        total += int(available[dep]["value"])
                        dep_fps.append(available[dep]["fingerprint"])
                    else:
                        dep_fps.append(missing_fingerprint(dep))
                fp = node_fingerprint(key, dep_fps)
                if fp != entry["fingerprint"] or total != entry["value"]:
                    raise StaleDependencyError(
                        "节点 " + key + " 重算指纹与计划不符, 拒绝发布陈旧结果")
                record = {"key": key, "value": total, "fingerprint": fp,
                          "layer": idx,
                          "trigger_paths": [list(p)
                                            for p in trigger_paths.get(key, ())]}
                atomic_write_json(paths.stage_node(key), record)
                staged[key] = record
                available[key] = record
                executed.append(key)
            else:
                if entry["action"] == "reuse-staged":
                    available[key] = staged[key]
                else:
                    available[key] = {"key": key, "value": entry["value"],
                                      "fingerprint": entry["fingerprint"],
                                      "layer": idx}
        progress_doc["staged"] = staged
        progress_doc["completed_layers"].append(idx)
        atomic_write_json(paths.progress, progress_doc)

        if crash_after_layer is not None and idx == crash_after_layer:
            progress_doc["status"] = "interrupted"
            atomic_write_json(paths.progress, progress_doc)
            print(json.dumps({"status": "interrupted", "exit": EXIT_CRASH,
                              "completed_layers":
                                  progress_doc["completed_layers"],
                              "staged_keys": sorted(staged)},
                             sort_keys=True))
            sys.stdout.flush()
            sys.exit(EXIT_CRASH)

    # 发布前最终校验: 全图指纹必须与当前数据版本一致
    assert_fresh()
    final_values, final_fps = compute_graph(inputs, nodes)
    for key in order:
        if final_fps[key] != plan[key]["fingerprint"] \
                or final_values[key] != plan[key]["value"]:
            raise StaleDependencyError(
                "发布前校验失败: " + key + " 指纹陈旧")

    publish(paths, inputs, nodes, order, layers, plan, staged,
            roots, trigger_paths, executed)
    return {
        "values": dict((k, plan[k]["value"]) for k in order),
        "fingerprints": dict((k, plan[k]["fingerprint"]) for k in order),
        "executed": executed,
        "reused": [k for k in order if plan[k]["action"] != "execute"],
        "layers": layers,
        "trigger_roots": roots,
        "trigger_paths": dict((k, [list(p) for p in v])
                              for k, v in trigger_paths.items()),
        "status": "published",
    }


def publish(paths, inputs, nodes, order, layers, plan, staged,
            roots, trigger_paths, executed):
    """原子发布一致全图; 失败分区永远不会进入最新版本。"""
    sources = dict(sorted(
        (k, {"version": str(v["version"]), "value": int(v["value"]),
             "fingerprint": source_fingerprint(k, v)})
        for k, v in inputs.items()))
    published_doc = {
        "values": dict((k, plan[k]["value"]) for k in order),
        "fingerprints": dict((k, plan[k]["fingerprint"]) for k in order),
    }
    meta = {
        "status": "published",
        "values": published_doc["values"],
        "fingerprints": published_doc["fingerprints"],
        "sources": sources,
        "nodes": dict(sorted((k, list(v)) for k, v in nodes.items())),
        "graph_signature": graph_signature(nodes),
        "layers": layers,
        "executed": executed,
        "trigger_roots": roots,
        "trigger_paths": dict((k, [list(p) for p in v])
                              for k, v in trigger_paths.items()),
        "staged_versions": dict(sorted(
            (k, staged[k]["fingerprint"]) for k in staged)),
    }
    atomic_write_json(paths.meta, meta)
    atomic_write_json(paths.published, published_doc)  # 最后切换最新版本
    progress = read_json(paths.progress, {})
    progress["status"] = "published"
    atomic_write_json(paths.progress, progress)


def inspect(root):
    paths = Paths(root)
    doc = read_json(paths.published, None)
    if doc is None:
        return {"values": {}, "fingerprints": {}}
    return {"values": doc.get("values", {}),
            "fingerprints": doc.get("fingerprints", {})}


def main(argv=None):
    p = argparse.ArgumentParser(description="分区回填与发布")
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)
    build = sub.add_parser("build")
    build.add_argument("source", type=Path)
    build.add_argument("--crash-after-layer", type=int)
    build.add_argument("--resume", action="store_true",
                       help="仅从已有暂存进度恢复")
    sub.add_parser("inspect")
    a = p.parse_args(argv)
    a.root.mkdir(parents=True, exist_ok=True)

    if a.op == "build":
        try:
            result = run_build(a.root, a.source,
                               crash_after_layer=a.crash_after_layer,
                               resume=a.resume)
        except CycleError as exc:
            print(json.dumps({"status": "rejected", "reason": str(exc)},
                             ensure_ascii=False))
            return EXIT_CYCLE
        except StaleDependencyError as exc:
            print(json.dumps({"status": "rejected", "reason": str(exc)},
                             ensure_ascii=False))
            return EXIT_STALE
        except NodeFailureError as exc:
            print(json.dumps({"status": "partial-failure",
                              "reason": str(exc)}))
            return EXIT_NODE_FAILED
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({"status": "error",
                              "reason": exc.__class__.__name__ + ": "
                              + str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return EXIT_OK

    print(json.dumps(inspect(a.root), sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
