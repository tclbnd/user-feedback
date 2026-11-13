"""可回放实体关系引擎。

按逻辑版本维护 same 支持边与 conflict 冲突边（含多来源、撤回），
在支持图上做带冲突约束的连通划分，并按确定性规则继承/派生实体 ID，
记录成员归属变化、旧 ID 别名与审计证据。新增或撤回边时只重算其
所在支持图连通区域，区域之外的实体 ID 保持不变。

CLI:
    python3 entities.py apply state.json input.json [--upto N]
    python3 entities.py inspect state.json [--version V] [--audit]
"""
import argparse
import json
from pathlib import Path


class EngineError(Exception):
    """可向调用方报告的输入错误；抛出时调用方负责不写回状态。"""


def norm_pair(a, b):
    """无向边端点归一化为字典序元组。"""
    return (a, b) if a <= b else (b, a)


def edge_key(a, b, source, kind):
    """边键 = 无向端点 + 来源 + 类型。"""
    x, y = norm_pair(a, b)
    return (x, y, source, kind)


def key_to_str(key):
    return json.dumps(list(key), ensure_ascii=False)


def str_to_key(text):
    return tuple(json.loads(text))


class Engine:
    """增量式实体合并/拆分引擎。"""

    def __init__(self, nodes):
        self.nodes = list(nodes)
        if len(set(self.nodes)) != len(self.nodes):
            raise EngineError("nodes 必须唯一")
        # event 标识 -> 归一化载荷 (a,b,source,kind,action)
        self.events = {}
        # 已接受事件的消费顺序；逻辑版本号即其长度（初始版本 0）
        self.event_order = []
        # 边键 -> 边记录（来源集合 + 每来源最近一次 set 的 event）
        self.edges = {}
        # 活动 same 边：归一化端点对 -> 来源集合
        self.active_pairs = {}
        # 支持图邻接表
        self.adj = {n: set() for n in self.nodes}
        # 每节点当前所属实体 ID
        self.owner = {n: "entity-%s" % n for n in self.nodes}
        # 曾经作为实体 ID 锚点出现过的节点（只有它们可能成为别名）
        self.ever_anchors = set(self.nodes)
        # 当前别名映射：旧实体 ID -> 当前实体 ID
        self.aliases = {}
        # 每版本快照、归属变化、别名历史、区域诊断、合并拒绝记录
        self.version = 0
        self.snapshots = {0: self._snapshot()}
        self.changes = []
        self.alias_log = []
        self.region_log = []
        self.blocked_merges = []
        # apply 已消费的输入位置总数（信息性记录；分块文件各自从 0 计数，
        # 真正的幂等性由 event 去重保证）
        self.input_cursor = 0

    # ------------------------------------------------------------------
    # 带冲突约束的区域求解
    # ------------------------------------------------------------------
    def _conflict_pairs(self):
        """当前全部活动 conflict 边的端点对。"""
        return {(e["a"], e["b"]) for e in self.edges.values()
                if e["kind"] == "conflict" and e["sources"]}

    def solve_region(self, seeds):
        """在 seeds 所在支持图连通区域内求解。

        与全量求解同一套确定性规则：按归一化端点对字典序扫描活动
        same 边；只要合并会把任一活动 conflict 对放进同一组就拒绝。
        返回 (parent, blocked, rejected)。
        """
        region = set()
        stack = list(seeds)
        while stack:
            node = stack.pop()
            if node in region:
                continue
            region.add(node)
            stack.extend(self.adj[node] - region)

        parent = {n: n for n in region}

        def find(x):
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        pairs = sorted(p for p in self.active_pairs
                       if p[0] in region and p[1] in region)
        conflicts = self._conflict_pairs()
        blocked, rejected = set(), []
        for a, b in pairs:
            ra, rb = find(a), find(b)
            if ra == rb:
                continue
            members_a = {n for n in region if find(n) == ra}
            members_b = {n for n in region if find(n) == rb}
            blocking = [p for p in conflicts
                        if (p[0] in members_a and p[1] in members_b)
                        or (p[0] in members_b and p[1] in members_a)]
            if blocking:
                blocked.update(blocking)
                rejected.append({"pair": [a, b],
                                 "blocked_by": [list(p) for p in sorted(blocking)]})
                continue
            parent[rb] = ra
        return parent, sorted(blocked), rejected

    def solve_all(self):
        """全量求解，供证据视图与测试对拍使用。"""
        parent = {n: n for n in self.nodes}

        def find(x):
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        conflicts = self._conflict_pairs()
        blocked, rejected = set(), []
        for a, b in sorted(self.active_pairs):
            ra, rb = find(a), find(b)
            if ra == rb:
                continue
            members_a = {n for n in self.nodes if find(n) == ra}
            members_b = {n for n in self.nodes if find(n) == rb}
            blocking = [p for p in conflicts
                        if (p[0] in members_a and p[1] in members_b)
                        or (p[0] in members_b and p[1] in members_a)]
            if blocking:
                blocked.update(blocking)
                rejected.append({"pair": [a, b],
                                 "blocked_by": [list(p) for p in sorted(blocking)]})
                continue
            parent[rb] = ra
        return parent, sorted(blocked), rejected

    # ------------------------------------------------------------------
    # 事件应用
    # ------------------------------------------------------------------
    def apply_event(self, link):
        """消费一个输入位置；接受则推进一个逻辑版本，重复等价事件幂等忽略。"""
        try:
            event = link["event"]
            a, b = link["a"], link["b"]
            source, kind, action = link["source"], link["kind"], link["action"]
        except (KeyError, TypeError):
            raise EngineError("link 缺少必需字段: %r" % (link,))
        if not isinstance(event, str) or not event:
            raise EngineError("event 必须为非空字符串")
        if kind not in ("same", "conflict") or action not in ("set", "retract"):
            raise EngineError("kind/action 非法: %r" % (link,))
        if a not in self.owner or b not in self.owner:
            raise EngineError("link 引用了未知节点: %r" % (link,))

        payload = (a, b, source, kind, action)
        if event in self.events:
            if self.events[event] == payload:
                return self.version  # 幂等忽略，不推进版本
            raise EngineError("event %s 重复但载荷冲突，状态保持不变" % event)

        key = edge_key(a, b, source, kind)
        x, y = norm_pair(a, b)
        edge = self.edges.get(key)
        if edge is None:
            edge = {"a": x, "b": y, "source": source, "kind": kind,
                    "sources": set(), "last_event": {}}
            self.edges[key] = edge

        if action == "set":
            if source not in edge["sources"]:
                edge["sources"].add(source)
                edge["last_event"][source] = event
                if kind == "same":
                    bucket = self.active_pairs.setdefault((x, y), set())
                    newly_active = not bucket
                    bucket.add(source)
                    if newly_active:
                        self.adj[x].add(y)
                        self.adj[y].add(x)
            else:
                edge["last_event"][source] = event  # 同来源重复 set：状态不变
        else:  # retract：只移除指定来源，其它来源的活动边并存
            edge["sources"].discard(source)
            edge["last_event"].pop(source, None)
            if kind == "same":
                bucket = self.active_pairs.get((x, y))
                if bucket is not None:
                    bucket.discard(source)
                    if not bucket:
                        del self.active_pairs[(x, y)]
                        self.adj[x].discard(y)
                        self.adj[y].discard(x)

        # 事件已接受：记账后只重算受影响连通区域（seeds 覆盖拆分后的两侧）
        self.events[event] = payload
        self.event_order.append(event)
        parent, blocked, rejected = self.solve_region((x, y))

        new_owner = dict(self.owner)
        touched_anchors = set()
        for node in parent:
            root = node
            while parent[root] != root:
                root = parent[root]
            touched_anchors.add(root)
            new_owner[node] = "entity-%s" % root
        self.ever_anchors.update(touched_anchors)

        next_version = self.version + 1
        moves = [{"node": n, "from": self.owner[n], "to": new_owner[n]}
                 for n in sorted(new_owner) if self.owner[n] != new_owner[n]]
        if moves:
            self.changes.append({"version": next_version, "move": moves})

        # 别名不变量：当过锚点的节点，其历史 ID 永远指向它当前所在实体
        old_aliases = dict(self.aliases)
        new_aliases = {}
        for anchor in sorted(self.ever_anchors):
            target = new_owner[anchor]
            if target != "entity-%s" % anchor:
                new_aliases["entity-%s" % anchor] = target
        for entry in self._alias_diff(old_aliases, new_aliases, next_version):
            self.alias_log.append(entry)

        for item in rejected:
            self.blocked_merges.append({"version": next_version,
                                        "pair": item["pair"],
                                        "blocked_by": item["blocked_by"]})
        self.region_log.append({"version": next_version, "event": event,
                                "region": sorted(parent),
                                "blocked_links": [list(p) for p in blocked]})

        self.owner = new_owner
        self.aliases = new_aliases
        self.version = next_version
        self.snapshots[next_version] = self._snapshot()
        return self.version

    @staticmethod
    def _alias_diff(old, new, version):
        """对比两版别名映射，产出 add/revoke/repoint 审计条目（含生效版本）。"""
        entries = []
        for alias in sorted(set(old) | set(new)):
            before, after = old.get(alias), new.get(alias)
            if before == after:
                continue
            if before is None:
                entries.append({"version": version, "action": "add",
                                "from": alias, "to": after})
            elif after is None:
                entries.append({"version": version, "action": "revoke",
                                "from": alias, "to": before})
            else:
                entries.append({"version": version, "action": "repoint",
                                "from": alias, "to": after, "previous": before})
        return entries

    # ------------------------------------------------------------------
    # 视图
    # ------------------------------------------------------------------
    def _clusters(self):
        groups = {}
        for node, cid in self.owner.items():
            groups.setdefault(cid, []).append(node)
        return [{"id": cid, "members": sorted(members)}
                for cid, members in sorted(groups.items())]

    def _blocked_links(self):
        return [list(p) for p in sorted(self._conflict_pairs())]

    def _snapshot(self):
        return {"version": self.version,
                "owner": dict(self.owner),
                "clusters": self._clusters(),
                "aliases": [{"from": f, "to": t}
                            for f, t in sorted(self.aliases.items())],
                "blocked_links": self._blocked_links()}

    def output(self):
        snap = self.snapshots[self.version]
        return {"version": self.version,
                "clusters": snap["clusters"],
                "aliases": snap["aliases"],
                "blocked_links": snap["blocked_links"],
                "changes": list(self.changes)}

    def replay(self, version):
        """用已接受事件从空状态重放到指定版本。"""
        if not 0 <= version <= self.version:
            raise EngineError("未知版本: %s" % version)
        engine = Engine(self.nodes)
        for event in self.event_order[:version]:
            a, b, source, kind, action = self.events[event]
            engine.apply_event({"event": event, "a": a, "b": b,
                                "source": source, "kind": kind, "action": action})
        return engine

    def evidence(self, version=None):
        """实体 -> 输入记录与决定性边的追溯视图（可指定任意已提交版本）。"""
        if version is None:
            version = self.version
        eng = self.replay(version)
        parent, _, _ = eng.solve_all()

        def root_of(x):
            while parent[x] != x:
                x = parent[x]
            return x

        # 再跑一次确定性扫描，记录真正促成合并的决定性边及其最终归属
        dsu = {n: n for n in eng.nodes}

        def find(x):
            while dsu[x] != x:
                x = dsu[x]
            return x

        decisive = []
        for pair in sorted(eng.active_pairs):
            a, b = pair
            ra, rb = find(a), find(b)
            if ra == rb:
                continue
            crossing_conflict = any(
                (find(p[0]) == ra and find(p[1]) == rb)
                or (find(p[0]) == rb and find(p[1]) == ra)
                for p in eng._conflict_pairs())
            if crossing_conflict:
                continue
            sources = sorted(eng.active_pairs[pair])
            decisive.append((pair, sources))
            dsu[rb] = ra

        clusters = {}
        for node in eng.nodes:
            clusters.setdefault(root_of(node), []).append(node)

        view_clusters = []
        for anchor in sorted(clusters):
            members = sorted(clusters[anchor])
            member_set = set(members)
            support, decisive_here, conflicts_here = [], [], []
            for pair, sources in sorted(eng.active_pairs.items()):
                if pair[0] in member_set and pair[1] in member_set:
                    for source in sorted(sources):
                        rec = eng.edges[edge_key(pair[0], pair[1], source, "same")]
                        support.append({"edge": list(pair), "source": source,
                                        "event": rec["last_event"].get(source)})
            for pair, sources in decisive:
                if root_of(pair[0]) == anchor and root_of(pair[1]) == anchor:
                    events = [eng.edges[edge_key(pair[0], pair[1], s, "same")]
                              ["last_event"].get(s) for s in sources]
                    decisive_here.append({"edge": list(pair), "sources": sources,
                                          "events": events})
            for pair in sorted(eng._conflict_pairs()):
                if pair[0] in member_set and pair[1] in member_set:
                    sources = sorted(
                        s for k, e in eng.edges.items()
                        if e["kind"] == "conflict" and (e["a"], e["b"]) == pair
                        for s in e["sources"])
                    events = [eng.edges[edge_key(pair[0], pair[1], s, "conflict")]
                              ["last_event"].get(s) for s in sources]
                    conflicts_here.append({"edge": list(pair), "sources": sources,
                                           "events": events})
            view_clusters.append({"id": "entity-%s" % anchor, "members": members,
                                  "support_records": support,
                                  "decisive_edges": decisive_here,
                                  "conflict_records": conflicts_here})

        return {"version": version,
                "clusters": view_clusters,
                "membership_at": eng.snapshots[version]["clusters"],
                "blocked_links": eng._blocked_links(),
                "blocked_merges": list(eng.blocked_merges),
                "alias_history": list(eng.alias_log),
                "region_log": list(eng.region_log),
                "changes": list(eng.changes)}

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def to_dict(self):
        return {"nodes": list(self.nodes),
                "version": self.version,
                "input_cursor": self.input_cursor,
                "event_order": list(self.event_order),
                "events": {e: list(p) for e, p in self.events.items()},
                "edges": {key_to_str(k): {"a": v["a"], "b": v["b"],
                                          "source": v["source"], "kind": v["kind"],
                                          "sources": sorted(v["sources"]),
                                          "last_event": dict(v["last_event"])}
                          for k, v in self.edges.items()},
                "active_pairs": {key_to_str(p): sorted(s)
                                 for p, s in self.active_pairs.items()},
                "adj": {n: sorted(ns) for n, ns in self.adj.items()},
                "owner": dict(self.owner),
                "ever_anchors": sorted(self.ever_anchors),
                "aliases": dict(self.aliases),
                "snapshots": {str(v): s for v, s in self.snapshots.items()},
                "changes": self.changes,
                "alias_log": self.alias_log,
                "region_log": self.region_log,
                "blocked_merges": self.blocked_merges}

    @classmethod
    def from_dict(cls, data):
        engine = cls.__new__(cls)
        engine.nodes = list(data["nodes"])
        engine.version = data["version"]
        engine.input_cursor = data.get("input_cursor", 0)
        engine.event_order = list(data["event_order"])
        engine.events = {e: tuple(p) for e, p in data["events"].items()}
        engine.edges = {}
        for text, v in data["edges"].items():
            engine.edges[str_to_key(text)] = {
                "a": v["a"], "b": v["b"], "source": v["source"],
                "kind": v["kind"], "sources": set(v["sources"]),
                "last_event": dict(v.get("last_event", {}))}
        engine.active_pairs = {tuple(str_to_key(t)): set(s)
                               for t, s in data["active_pairs"].items()}
        engine.adj = {n: set(ns) for n, ns in data["adj"].items()}
        for n in engine.nodes:
            engine.adj.setdefault(n, set())
        engine.owner = dict(data["owner"])
        engine.ever_anchors = set(data["ever_anchors"])
        engine.aliases = dict(data["aliases"])
        engine.snapshots = {int(v): s for v, s in data["snapshots"].items()}
        engine.changes = [dict(c) for c in data["changes"]]
        engine.alias_log = [dict(e) for e in data["alias_log"]]
        engine.region_log = [dict(e) for e in data["region_log"]]
        engine.blocked_merges = [dict(e) for e in data["blocked_merges"]]
        return engine


def _load_or_create(path, nodes):
    if path.exists():
        engine = Engine.from_dict(json.loads(path.read_text()))
        if nodes is not None and engine.nodes != nodes:
            raise EngineError("nodes 与已提交状态不一致，拒绝写入")
        return engine
    if nodes is None:
        raise EngineError("状态文件不存在且未提供输入")
    return Engine(nodes)


def main():
    parser = argparse.ArgumentParser(description="可回放实体关系引擎")
    sub = parser.add_subparsers(dest="op", required=True)
    apply_p = sub.add_parser("apply", help="消费输入链接并写回状态")
    apply_p.add_argument("db", type=Path)
    apply_p.add_argument("input", type=Path)
    apply_p.add_argument("--upto", type=int)
    inspect_p = sub.add_parser("inspect", help="查看已提交版本")
    inspect_p.add_argument("db", type=Path)
    inspect_p.add_argument("--version", type=int)
    inspect_p.add_argument("--audit", action="store_true",
                           help="输出证据、别名历史与区域诊断")
    opt = parser.parse_args()

    try:
        if opt.op == "apply":
            data = json.loads(opt.input.read_text())
            nodes = list(data["nodes"])
            links = data["links"]
            upto = len(links) if opt.upto is None else opt.upto
            if not 0 <= upto <= len(links):
                raise EngineError("--upto 超出输入范围")
            engine = _load_or_create(opt.db, nodes)
            feed = links if opt.upto is None else links[:opt.upto]
            # 全部成功后才写盘；任何异常都保留原状态文件。
            # 已消费 event 按幂等规则忽略，因此整文件重跑与分块累积等价。
            for link in feed:
                before = engine.version
                engine.apply_event(link)
                if engine.version != before:
                    engine.input_cursor += 1
            opt.db.write_text(json.dumps(engine.to_dict(), ensure_ascii=False,
                                         sort_keys=True) + "\n")
            print(json.dumps(engine.output(), ensure_ascii=False, sort_keys=True))
        else:
            engine = Engine.from_dict(json.loads(opt.db.read_text()))
            version = engine.version if opt.version is None else opt.version
            if not 0 <= version <= engine.version:
                raise EngineError("未知版本: %s" % version)
            if opt.audit:
                print(json.dumps(engine.evidence(version), ensure_ascii=False,
                                 sort_keys=True))
            else:
                snap = engine.snapshots[version]
                out = {"version": version,
                       "clusters": snap["clusters"],
                       "aliases": snap["aliases"],
                       "blocked_links": snap["blocked_links"],
                       "changes": [c for c in engine.changes
                                   if c["version"] <= version]}
                print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    except EngineError as exc:
        parser.exit(2, "错误: %s\n" % exc)


if __name__ == "__main__":
    main()
