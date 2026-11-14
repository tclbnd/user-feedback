#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离散事件 Raft 仿真器 + 命令行入口。

用法（与基线接口保持一致）：
    python3 raft_sim.py scenario.json output.json
    python3 raft_sim.py --demo

scenario.json 示例：
    {"nodes": 5, "seed": 7, "duration": 120,
     "network": {"delay": [1, 3], "drop": 0.05, "reorder": 0.25},
     "events": [
       {"t": 0,  "kind": "client_write", "to": 0, "value": "x=1"},
       {"t": 20, "kind": "partition", "groups": [[0, 1], [2, 3, 4]]},
       {"t": 30, "kind": "client_write", "to": 0, "value": "x=2"},
       {"t": 60, "kind": "heal"},
       {"t": 80, "kind": "checkpoint"}]}

网络在分区之上叠加随机延迟、乱序与丢包，全部由 seed 驱动，结果可复现。
"""
import json
import sys
import heapq
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from raft_core import (RaftNode, Message, SafetyViolation, LEADER)


# ====================================================================== #
# 网络模型：分区 + 随机延迟 + 乱序 + 丢包
# ====================================================================== #
class Network:
    def __init__(self, n: int, rng: random.Random,
                 delay: Tuple[int, int] = (1, 3),
                 drop: float = 0.0, reorder: float = 0.0):
        self.n = n
        self.rng = rng
        self.delay = delay
        self.drop = drop
        self.reorder = reorder
        self.groups: List[List[int]] = [list(range(n))]
        self.sent = 0
        self.dropped = 0

    def partition(self, groups: List[List[int]]) -> None:
        flat = sorted(x for g in groups for x in g)
        assert flat == list(range(self.n)), "分组必须覆盖全部节点"
        self.groups = [list(g) for g in groups]

    def heal(self) -> None:
        self.groups = [list(range(self.n))]

    def connected(self, a: int, b: int) -> bool:
        for g in self.groups:
            if a in g:
                return b in g
        return False

    def route(self, now: float, m: Message) -> Optional[float]:
        """返回投递时间；None 表示丢弃。"""
        if not self.connected(m.src, m.dst):
            self.dropped += 1
            return None
        self.sent += 1
        if self.rng.random() < self.drop:
            self.dropped += 1
            return None
        d = self.rng.randint(*self.delay)
        if self.rng.random() < self.reorder:
            d += self.rng.randint(self.delay[1], self.delay[1] * 3)
        return now + d


# ====================================================================== #
# 事件队列与仿真器（实现 raft_core 所需的 ctx 接口）
# ====================================================================== #
@dataclass(order=True)
class Event:
    time: float
    seq: int
    kind: str = field(compare=False)
    payload: Any = field(compare=False, default=None)


class Simulator:
    def __init__(self, n: int = 5, seed: int = 0, duration: float = 120.0,
                 network: Optional[Dict[str, Any]] = None,
                 et_min: int = 8, et_max: int = 15, hb: int = 3,
                 verbose: bool = False):
        self.n = n
        self.rng = random.Random(seed)
        self.seed = seed
        self.duration = duration
        self.verbose = verbose
        nw = network or {}
        self.net = Network(
            n, self.rng,
            delay=tuple(nw.get("delay", (1, 3))),
            drop=nw.get("drop", 0.0),
            reorder=nw.get("reorder", 0.0))
        self.events: List[Event] = []
        self._seq = 0
        self.time = 0.0
        self.event_log: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.rejected: List[Dict[str, Any]] = []
        self.term_leader: List[Dict[str, Any]] = []
        self.proposals: Dict[str, Dict[str, Any]] = {}
        self.violations: List[str] = []
        self.checkpoints: List[Dict[str, Any]] = []
        self.nodes = [RaftNode(i, list(range(n)), self,
                               et_min, et_max, hb) for i in range(n)]
        self._cmd_seq = 0

    # ---------------- ctx 接口（供 RaftNode 调用） ---------------- #
    def schedule_timer(self, kind: str, node: int, gen: int,
                       delay: float) -> None:
        self._push(self.time + delay, "timer",
                   dict(kind=kind, node=node, gen=gen))

    def send(self, m: Message) -> None:
        deliver_at = self.net.route(self.time, m)
        if deliver_at is None:
            self._log("msg_dropped", src=m.src, dst=m.dst, mtype=m.mtype)
            return
        self._push(deliver_at, "rpc", m)

    def record(self, node: int, action: str, detail: Dict[str, Any]) -> None:
        rec = dict(event_id=self._last_id, t=self.time, node=node,
                   action=action, detail=detail)
        self.decisions.append(rec)
        if self.verbose:
            print("  t=%6.1f n%d %-18s %s"
                  % (self.time, node, action, _short(detail)))

    def on_leader(self, term: int, node: int) -> None:
        self.term_leader.append(dict(term=term, leader=node, t=self.time))

    # ---------------- 事件管理 ---------------- #
    def _push(self, t: float, kind: str, payload: Any) -> None:
        self._seq += 1
        heapq.heappush(self.events, Event(t, self._seq, kind, payload))

    def _log(self, etype: str, **kw) -> int:
        eid = len(self.event_log) + 1
        self.event_log.append(dict(event_id=eid, t=self.time,
                                   kind=etype, **kw))
        return eid

    def add_client_write(self, t: float, to: int, value: Any,
                         cmd_id: Optional[str] = None,
                         route_to_leader: bool = False) -> str:
        if cmd_id is None:
            self._cmd_seq += 1
            cmd_id = "cmd-%d" % self._cmd_seq
        self._push(t, "client_write",
                   dict(to=to, value=value, cmd=cmd_id,
                        route_to_leader=route_to_leader))
        return cmd_id

    def add_partition(self, t: float, groups: List[List[int]]) -> None:
        self._push(t, "partition", groups)

    def add_heal(self, t: float) -> None:
        self._push(t, "heal", None)

    def add_checkpoint(self, t: float) -> None:
        self._push(t, "checkpoint", None)

    def add_drop(self, t: float, src: int, dst: int) -> None:
        """在时刻 t 主动丢弃 src->dst 的下一条在途消息由网络丢包率模拟，
        这里提供确定性的一次性链路抖动事件：制造一次临时不可达窗口。"""
        self._push(t, "link_blackhole", dict(src=src, dst=dst))

    def load_events(self, scenario_events: List[Dict[str, Any]]) -> None:
        for e in scenario_events:
            k = e["kind"]
            t = e["t"]
            if k == "client_write":
                cid = e.get("cmd")
                cid = self.add_client_write(
                    t, e["to"], e["value"], cid,
                    route_to_leader=e.get("route_to_leader", False))
                if cid not in self.proposals:
                    self.proposals[cid] = dict(
                        cmd=cid, value=e["value"], to=e["to"], submit_t=t)
            elif k == "partition":
                self.add_partition(t, e["groups"])
            elif k == "heal":
                self.add_heal(t)
            elif k == "checkpoint":
                self.add_checkpoint(t)
            elif k == "drop":
                self.add_drop(t, e["src"], e["dst"])
            else:
                raise ValueError("未知事件类型: %s" % k)

    # ---------------- 主循环 ---------------- #
    def _dispatch(self, ev: Event) -> None:
        t, kind, p = ev.time, ev.kind, ev.payload
        if kind == "timer":
            self.nodes[p["node"]].tick(p["kind"], p["gen"])
        elif kind == "rpc":
            m: Message = p
            self.nodes[m.dst].handle_message(m)
        elif kind == "client_write":
            cid = p["cmd"]
            self.proposals.setdefault(cid, dict(
                cmd=cid, value=p["value"], to=p["to"], submit_t=t))
            target = p["to"]
            # 客户端可选地把请求转发给它所知的 leader（模拟代理路由）
            if p.get("route_to_leader"):
                leaders = [i for i, nd in enumerate(self.nodes)
                           if nd.role == LEADER]
                if leaders:
                    target = leaders[0]
            out = self.nodes[target].client_write(cid, p["value"])
            self.proposals[cid]["outcome"] = out
            if out["status"] == "rejected":
                self.rejected.append(dict(
                    t=t, to=target, value=p["value"], cmd=cid,
                    reason=out["reason"], term=out.get("term")))
        elif kind == "partition":
            self.net.partition(p)
        elif kind == "heal":
            self.net.heal()
            for nd in self.nodes:
                if nd.role == LEADER:
                    nd._broadcast_append_entries()
        elif kind == "checkpoint":
            rep = self.check_safety()
            self.checkpoints.append(dict(t=t, **rep))
        elif kind == "link_blackhole":
            # 一次性抖动：丢弃当前在途的该方向消息
            kept, removed = [], []
            while self.events:
                ev2 = heapq.heappop(self.events)
                if (ev2.kind == "rpc" and ev2.payload.src == p["src"]
                        and ev2.payload.dst == p["dst"]):
                    removed.append(ev2)
                else:
                    kept.append(ev2)
            for ev2 in kept:
                heapq.heappush(self.events, ev2)
            self.net.dropped += len(removed)
            self._log("link_blackhole", src=p["src"], dst=p["dst"],
                      removed=len(removed))

    def run(self, horizon: Optional[float] = None) -> Tuple[Dict[str, Any], List[str]]:
        horizon = self.duration if horizon is None else horizon
        while self.events and self.events[0].time <= horizon:
            ev = heapq.heappop(self.events)
            self.time = ev.time
            # 每个时序事件先登记 event_id，节点决策通过 record() 关联到它
            self._last_id = self._register_event(ev)
            try:
                self._dispatch(ev)
            except SafetyViolation as ex:
                self.violations.append(str(ex))
                raise
        self.time = horizon
        return self.result(), self.violations

    def _register_event(self, ev: Event) -> int:
        kind, p = ev.kind, ev.payload
        if kind == "timer":
            return self._log("timer", node=p["node"], timer=p["kind"], gen=p["gen"])
        if kind == "rpc":
            return self._log("rpc", src=p.src, dst=p.dst,
                             mtype=p.mtype, term=p.term,
                             origin=getattr(p, "origin", None))
        if kind == "client_write":
            return self._log("client_write", to=p["to"],
                             value=p["value"], cmd=p["cmd"])
        if kind == "partition":
            return self._log("partition", groups=p)
        if kind == "heal":
            return self._log("heal")
        if kind == "checkpoint":
            return self._log("checkpoint")
        if kind == "link_blackhole":
            return self._log("link_blackhole", src=p["src"], dst=p["dst"])
        return self._log(kind)


    # ---------------- 安全不变量 ---------------- #
    def check_safety(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {}
        # 1. 选举安全：同一任期至多一个 leader
        leaders_by_term: Dict[int, List[int]] = {}
        for rec in self.term_leader:
            leaders_by_term.setdefault(rec["term"], [])
            if rec["leader"] not in leaders_by_term[rec["term"]]:
                leaders_by_term[rec["term"]].append(rec["leader"])
        double = {tn: ls for tn, ls in leaders_by_term.items() if len(ls) > 1}
        info["election_safety"] = not double
        info["double_leaders"] = double

        # 2. 运行中（含分区期间/刚恢复尚未对账）的日志匹配安全口径：
        #    任意两节点在“双方都已提交”的长度 min(commit_i, commit_j) 上，
        #    日志必须逐字节一致——这正是“已提交条目永不被覆盖”的等价表述。
        #    未提交尾部允许冲突（Raft Figure 8：两个通信域可能在同索引写过
        #    不同任期的条目），恢复后由 AppendEntries 按 (任期,索引) 截断；
        #    全量日志一致性在 assert_convergence 中强制校验。
        seq = [[(e.term, e.value) for e in nd.log] for nd in self.nodes]
        commits = [nd.commit_idx for nd in self.nodes]
        committed_prefix_ok = True
        for a in range(self.n):
            for b in range(a + 1, self.n):
                k = min(commits[a], commits[b])
                if seq[a][:k] != seq[b][:k]:
                    committed_prefix_ok = False
        info["log_prefix_match"] = committed_prefix_ok
        info["committed_prefix_preserved"] = committed_prefix_ok

        # 3. 已提交不可覆盖：所有节点共同提交点之前的日志完全一致
        cmin = min(nd.commit_idx for nd in self.nodes)
        base = [(e.term, e.value) for e in self.nodes[0].log[:cmin]]
        info["committed_identical"] = all(
            [(e.term, e.value) for e in nd.log[:cmin]] == base
            for nd in self.nodes)
        info["min_commit_idx"] = cmin

        # 4. 应用序列前缀一致
        apps = [list(nd.applied) for nd in self.nodes]
        app_ok = True
        for a in range(self.n):
            for b in range(a + 1, self.n):
                k = min(len(apps[a]), len(apps[b]))
                if apps[a][:k] != apps[b][:k]:
                    app_ok = False
        info["applied_prefix_match"] = app_ok

        # 5. apply 严格等于各自日志前缀，且无跳号
        state_ok = all(
            nd.applied == [e.value for e in nd.log[:nd.commit_idx]]
            and nd.last_applied == nd.commit_idx
            for nd in self.nodes)
        info["state_machine_order"] = state_ok
        return info

    # ---------------- 结果输出 ---------------- #
    def result(self) -> Dict[str, Any]:
        # 集群提交点 = 任一节点达到的最大 commit_idx（leader 提交即视为集群提交，
        # 分区内落后节点稍后会通过 AppendEntries 补齐）
        cluster_commit = max(nd.commit_idx for nd in self.nodes)
        ref = next((nd for nd in self.nodes if nd.role == LEADER),
                   self.nodes[0])
        committed_values = [e.value for e in ref.log[:cluster_commit]]
        for cid, prop in self.proposals.items():
            out = prop.get("outcome")
            if out and out.get("status") == "accepted":
                prop["final"] = ("committed" if prop["value"]
                                 in committed_values else "lost_uncommitted")
            else:
                prop["final"] = "rejected"
        return dict(
            seed=self.seed,
            nodes=[nd.snapshot() for nd in self.nodes],
            rejected=self.rejected,
            term_log=self.term_leader,
            commit_trajectory=_commit_trajectory(self.nodes, self.decisions),
            proposals=list(self.proposals.values()),
            network=dict(sent=self.net.sent, dropped=self.net.dropped,
                         groups=self.net.groups),
            checkpoints=self.checkpoints,
            events=self.event_log,
            decisions=self.decisions,
            violations=self.violations,
        )


def _short(d: Dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False, default=str)[:110]


def _commit_trajectory(nodes: List[RaftNode],
                       decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for rec in decisions:
        if rec["action"] == "commit_advance":
            out.append(dict(t=rec["t"], node=rec["node"],
                            commit_idx=rec["detail"]["new_commit"],
                            source=rec["detail"].get("source"),
                            event_id=rec["event_id"]))
    return out


# ====================================================================== #
# 场景加载
# ====================================================================== #
def run_scenario(sc: Dict[str, Any], verbose: bool = False
                 ) -> Tuple[Dict[str, Any], List[str]]:
    sim = Simulator(
        n=sc.get("nodes", 5),
        seed=sc.get("seed", 0),
        duration=sc.get("duration", 120),
        network=sc.get("network", {}),
        et_min=sc.get("election_min", 8),
        et_max=sc.get("election_max", 15),
        hb=sc.get("heartbeat", 3),
        verbose=verbose)
    sim.load_events(sc.get("events", []))
    return sim.run()


# ====================================================================== #
# 可读轨迹演示（脑裂场景）
# ====================================================================== #
def demo() -> None:
    sc = dict(nodes=5, seed=7, duration=260,
              election_min=16, election_max=28, heartbeat=3,
              network=dict(delay=(1, 2), drop=0.0, reorder=0.35),
              events=[
                  # 首个选举约在 t=16~28，本 seed 下节点 4 当选初始 leader
                  dict(t=30, kind="client_write", to=4, value="x=1",
                       route_to_leader=True),
                  # 把旧主节点 4 切进 2 节点少数派，节点 0,1,2 构成多数派
                  dict(t=55, kind="partition",
                       groups=[[4, 3], [0, 1, 2]]),
                  # 定向打给旧主节点 4：它仍以为自己是 leader，会接受但
                  # 拿不到多数派 ack，条目只能停留未提交，最终被截断
                  dict(t=62, kind="client_write", to=4, value="x=stale-old"),
                  # 多数派一侧的合法写（此时新主应已选出），交给其 leader
                  dict(t=110, kind="client_write", to=0, value="x=2",
                       route_to_leader=True),
                  dict(t=180, kind="heal"),
                  # 恢复后旧主已因更高任期降级，定向写必须被拒
                  dict(t=195, kind="client_write", to=4, value="x=after-heal"),
                  dict(t=215, kind="client_write", to=0, value="x=3",
                       route_to_leader=True),
                  dict(t=250, kind="checkpoint"),
              ])
    print("=" * 72)
    print("脑裂演示场景 seed=%s（事件级决策轨迹）" % sc["seed"])
    print("=" * 72)
    result, violations = run_scenario(sc, verbose=True)
    _print_report(result, violations)


def _print_report(result: Dict[str, Any], violations: List[str]) -> None:
    print("=" * 72)
    print("任期变化 / 选举轨迹")
    for r in result["term_log"]:
        print("  t=%5.1f  任期 %2d  leader = 节点 %d"
              % (r["t"], r["term"], r["leader"]))

    print("-" * 72)
    print("提交点轨迹（只推进到多数派 + 当前任期条目）")
    for r in result["commit_trajectory"]:
        print("  t=%5.1f  节点%d commit_idx -> %d (%s, event %d)"
              % (r["t"], r["node"], r["commit_idx"], r["source"],
                 r["event_id"]))

    print("-" * 72)
    print("被拒绝的写（过期任期 / 非 leader）")
    for r in result["rejected"]:
        print("  t=%5.1f  节点%d 拒绝 %r reason=%s term=%s"
              % (r["t"], r["to"], r["value"], r["reason"], r["term"]))

    print("-" * 72)
    print("逐节点最终日志 / 应用序列")
    for nd in result["nodes"]:
        log_repr = ["%d@t%d" % (e["index"], e["term"]) for e in nd["log"]]
        print("  节点%d term=%d role=%-9s commit=%d log=[%s]"
              % (nd["id"], nd["term"], nd["role"], nd["commit_idx"],
                 " ".join(log_repr)))
        print("         applied=%s state=%s" % (nd["applied"], nd["state"]))

    print("-" * 72)
    print("客户端提案最终命运")
    for p in result["proposals"]:
        print("  %s value=%r -> %s" % (p["cmd"], p["value"], p["final"]))

    print("-" * 72)
    cp = result["checkpoints"][-1] if result["checkpoints"] else None
    print("安全不变量:", json.dumps(cp, ensure_ascii=False)
          if cp else "无 checkpoint")
    print("违例:", violations if violations else "无")
    print("网络: 发送 %d 丢包 %d"
          % (result["network"]["sent"], result["network"]["dropped"]))


def main(argv: List[str]) -> int:
    if len(argv) == 2 and argv[1] == "--demo":
        demo()
        return 0
    if len(argv) != 3:
        print(__doc__)
        print("用法: python3 raft_sim.py scenario.json output.json")
        print("      python3 raft_sim.py --demo")
        return 2
    with open(argv[1], "r", encoding="utf-8") as f:
        sc = json.load(f)
    result, _ = run_scenario(sc)
    with open(argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    print("已写出 %s；违例数=%d" % (argv[2], len(result["violations"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

