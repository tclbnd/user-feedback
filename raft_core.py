#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Raft 节点状态机。

覆盖三条核心安全机制：
1. 任期与投票约束：每个任期最多投一票、候选人日志必须足够新、多数派才能当选；
   收到更高任期的 RPC 立即降级为 follower，过期任期的写请求在节点侧被拒绝。
2. 日志冲突按 (任期, 索引) 截断：AppendEntries 的 prevLog 一致性检查 +
   Raft 标准 conflictTerm/conflictIndex 回退；截断点永远不允许越过 commit_idx。
3. 安全提交点：leader 只能提交“多数派已复制且条目 term == 当前任期”的条目，
   commit_idx 单调不减；状态机严格按日志索引顺序 apply。

节点不直接接触时钟/网络，全部通过 ctx（Simulator 实现）发送消息、调度定时器、
落决策轨迹，保证每条决策都能回溯到某个时序事件 id。
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

FOLLOWER, CANDIDATE, LEADER = "follower", "candidate", "leader"

# 定时器默认参数（仿真时间单位）
ELECTION_MIN, ELECTION_MAX, HEARTBEAT = 8, 15, 3


class SafetyViolation(RuntimeError):
    """触发 Raft 安全不变量违例（例如截断已提交条目、同任期双主）。"""


@dataclass
class LogEntry:
    term: int
    value: Any
    index: int  # 1 基索引，等于它在日志中的位置


@dataclass
class Message:
    src: int
    dst: int
    term: int
    mtype: str  # RequestVote / RequestVoteReply / AppendEntries / AppendEntriesReply
    payload: Dict[str, Any]
    origin: Optional[int] = None  # 触发本消息的事件 id，构成因果链


class RaftNode:
    def __init__(self, node_id: int, all_nodes: List[int], ctx,
                 et_min: int = ELECTION_MIN, et_max: int = ELECTION_MAX,
                 hb: int = HEARTBEAT):
        self.id = node_id
        self.peers = [j for j in all_nodes if j != node_id]
        self.n = len(all_nodes)
        self.majority = self.n // 2 + 1
        self.ctx = ctx
        self.et_min, self.et_max, self.hb = et_min, et_max, hb

        # 持久化状态（仿真中即普通内存）
        self.term = 0
        self.voted_for: Optional[int] = None
        self.log: List[LogEntry] = []  # 位置 i 对应索引 i+1

        # 易失状态
        self.role = FOLLOWER
        self.leader_id: Optional[int] = None
        self.commit_idx = 0
        self.last_applied = 0
        self.applied: List[Any] = []          # 已应用值序列（严格日志序）
        self.state: Dict[str, Any] = {}       # 由 "k=v" 写入派生的 KV 状态机
        self.votes: Set[int] = set()
        self.next_idx: Dict[int, int] = {}
        self.match_idx: Dict[int, int] = {}

        # 定时器代数：每次重置定时器 +1，旧定时器触发时代数不匹配即作废
        self.election_gen = 0
        self.heartbeat_gen = 0
        self.reset_election_timer()

    # ------------------------------------------------------------------ #
    # 基础工具
    # ------------------------------------------------------------------ #
    def trace(self, action: str, **detail) -> None:
        self.ctx.record(self.id, action, detail)

    def last_log_index(self) -> int:
        return len(self.log)

    def last_log_term(self) -> int:
        return self.log[-1].term if self.log else 0

    def entry(self, index: int) -> LogEntry:
        return self.log[index - 1]

    def reset_election_timer(self) -> None:
        self.election_gen += 1
        gen = self.election_gen
        delay = self.ctx.rng.randint(self.et_min, self.et_max)
        self.ctx.schedule_timer("election", self.id, gen, delay)

    def _truncate(self, keep: int, reason: str) -> None:
        """只保留索引 <= keep 的条目；禁止删除任何已提交条目。"""
        if keep < self.commit_idx:
            raise SafetyViolation(
                "node %d: 截断到索引 %d 将覆盖已提交点 %d"
                % (self.id, keep, self.commit_idx))
        if keep < len(self.log):
            removed = [e.index for e in self.log[keep:]]
            del self.log[keep:]
            self.trace("log_truncate", keep=keep, removed=removed, reason=reason)

    # ------------------------------------------------------------------ #
    # 角色与任期转换
    # ------------------------------------------------------------------ #
    def _to_follower(self, term: int, reason: str) -> None:
        if term < self.term:
            return
        if term > self.term:
            old = self.term
            self.term = term
            self.voted_for = None
            self.votes = set()
            self.trace("term_bump", old_term=old, new_term=term, reason=reason)
        if self.role != FOLLOWER:
            self.trace("role_change", old_role=self.role,
                       new_role=FOLLOWER, reason=reason)
        self.role = FOLLOWER
        self.leader_id = None
        self.next_idx, self.match_idx = {}, {}
        self.heartbeat_gen += 1  # 旧主残留心跳立即失效
        self.reset_election_timer()

    def _start_election(self) -> None:
        self.term += 1
        self.role = CANDIDATE
        self.voted_for = self.id
        self.votes = {self.id}
        self.leader_id = None
        self.trace("term_bump", old_term=self.term - 1,
                   new_term=self.term, reason="election_timeout")
        self.trace("election_start", last_index=self.last_log_index(),
                   last_term=self.last_log_term())
        self.reset_election_timer()
        for j in self.peers:
            self.ctx.send(Message(
                self.id, j, self.term, "RequestVote",
                dict(last_log_index=self.last_log_index(),
                     last_log_term=self.last_log_term())))

    def _become_leader(self) -> None:
        if self.role != CANDIDATE:
            return
        self.role = LEADER
        self.leader_id = self.id
        self.election_gen += 1  # 取消自己的选举定时器
        last = self.last_log_index()
        for j in self.peers:
            self.next_idx[j] = last + 1
            self.match_idx[j] = 0
        self.trace("leader_elected", term=self.term, votes=sorted(self.votes))
        self.ctx.on_leader(self.term, self.id)
        self.heartbeat_gen += 1
        self._broadcast_append_entries()
        self.ctx.schedule_timer("heartbeat", self.id, self.heartbeat_gen, self.hb)

    # ------------------------------------------------------------------ #
    # 定时器事件
    # ------------------------------------------------------------------ #
    def tick(self, kind: str, gen: int) -> None:
        if kind == "election":
            if gen != self.election_gen or self.role == LEADER:
                return
            self.trace("election_timeout_fire")
            self._start_election()
        elif kind == "heartbeat":
            if gen != self.heartbeat_gen or self.role != LEADER:
                return
            self._broadcast_append_entries()
            self.ctx.schedule_timer("heartbeat", self.id, gen, self.hb)

    # ------------------------------------------------------------------ #
    # 客户端写
    # ------------------------------------------------------------------ #
    def client_write(self, cmd_id: str, value: Any) -> Dict[str, Any]:
        # 只有当前任期的 leader 能接受写；已观测到更高任期的旧主此时是 follower
        if self.role != LEADER:
            self.trace("client_reject", cmd=cmd_id, reason="not-leader",
                       node_term=self.term, leader_hint=self.leader_id)
            return dict(status="rejected", reason="not-leader",
                        term=self.term, leader_hint=self.leader_id)
        entry = LogEntry(self.term, value, len(self.log) + 1)
        self.log.append(entry)
        self.trace("client_append", cmd=cmd_id, index=entry.index, term=entry.term)
        self._broadcast_append_entries()  # 立即并行复制
        return dict(status="accepted", index=entry.index, term=entry.term)

    # ------------------------------------------------------------------ #
    # RPC 入口
    # ------------------------------------------------------------------ #
    def handle_message(self, m: Message) -> None:
        table = dict(
            RequestVote=self._on_request_vote,
            RequestVoteReply=self._on_request_vote_reply,
            AppendEntries=self._on_append_entries,
            AppendEntriesReply=self._on_append_entries_reply,
        )
        table[m.mtype](m)

    def _reply(self, req: Message, mtype: str, payload: Dict[str, Any]) -> None:
        self.ctx.send(Message(self.id, req.src, self.term, mtype,
                              payload, origin=req.origin))

    # ---------------- RequestVote ---------------- #
    def _on_request_vote(self, m: Message) -> None:
        if m.term < self.term:
            self.trace("vote_deny", candidate=m.src, reason="stale_term",
                       candidate_term=m.term)
            self._reply(m, "RequestVoteReply", dict(vote_granted=False))
            return
        if m.term > self.term:
            self._to_follower(m.term, "rv_higher_term")

        p = m.payload
        up_to_date = (
            p["last_log_term"] > self.last_log_term()
            or (p["last_log_term"] == self.last_log_term()
                and p["last_log_index"] >= self.last_log_index())
        )
        can_vote = self.voted_for in (None, m.src) and up_to_date
        if can_vote:
            self.voted_for = m.src
            self.trace("vote_grant", candidate=m.src, term=self.term)
            self.reset_election_timer()
            self._reply(m, "RequestVoteReply", dict(vote_granted=True))
        else:
            reason = "already_voted" if self.voted_for not in (None, m.src) \
                else "log_behind"
            self.trace("vote_deny", candidate=m.src, reason=reason,
                       voted_for=self.voted_for)
            self._reply(m, "RequestVoteReply", dict(vote_granted=False))

    def _on_request_vote_reply(self, m: Message) -> None:
        if m.term > self.term:
            self._to_follower(m.term, "rv_reply_higher_term")
            return
        if self.role != CANDIDATE or m.term != self.term:
            return  # 过期回复
        if m.payload.get("vote_granted"):
            self.votes.add(m.src)
            self.trace("vote_received", from_node=m.src,
                       count=len(self.votes))
            if len(self.votes) >= self.majority:
                self._become_leader()

    # ---------------- AppendEntries ---------------- #
    def _broadcast_append_entries(self) -> None:
        for j in self.peers:
            self._send_append_entries(j)

    def _send_append_entries(self, j: int) -> None:
        # 乱序下迟到的 ACK 可能把 next_idx 推到当前日志末端之后，这里钳制
        ni = min(self.next_idx[j], self.last_log_index() + 1)
        self.next_idx[j] = ni
        prev_index = ni - 1
        prev_term = self.entry(prev_index).term if prev_index >= 1 else 0
        entries = [LogEntry(e.term, e.value, e.index)
                   for e in self.log[ni - 1:]]
        self.ctx.send(Message(
            self.id, j, self.term, "AppendEntries",
            dict(prev_log_index=prev_index,
                 prev_log_term=prev_term,
                 entries=entries,
                 leader_commit=self.commit_idx)))

    def _on_append_entries(self, m: Message) -> None:
        if m.term < self.term:
            # 过期 leader：拒绝并回传当前任期，帮助旧主尽快降级
            self.trace("ae_reject", leader=m.src, reason="stale_term",
                       leader_term=m.term, my_term=self.term)
            self._reply(m, "AppendEntriesReply",
                        dict(success=False, conflict_term=0,
                             conflict_index=self.last_log_index() + 1))
            return
        if m.term > self.term:
            self._to_follower(m.term, "ae_higher_term")
        if self.role == CANDIDATE:
            self.role = FOLLOWER
            self.trace("role_change", old_role=CANDIDATE,
                       new_role=FOLLOWER, reason="discover_same_term_leader")
        if self.role == LEADER:
            raise SafetyViolation(
                "node %d: 任期 %d 出现两个 leader" % (self.id, self.term))
        self.leader_id = m.src
        self.reset_election_timer()

        p = m.payload
        prev_index, prev_term = p["prev_log_index"], p["prev_log_term"]

        # prevLog 一致性检查（任期 + 索引）
        if len(self.log) < prev_index:
            conflict_index = len(self.log) + 1
            conflict_term = 0
        elif prev_index >= 1 and self.entry(prev_index).term != prev_term:
            conflict_term = self.entry(prev_index).term
            conflict_index = prev_index
            while conflict_index > 1 \
                    and self.entry(conflict_index - 1).term == conflict_term:
                conflict_index -= 1
        else:
            conflict_index = -1  # 一致性通过

        if conflict_index != -1:
            self.trace("ae_conflict", leader=m.src,
                       prev_index=prev_index, prev_term=prev_term,
                       conflict_term=conflict_term,
                       conflict_index=conflict_index)
            self._reply(m, "AppendEntriesReply",
                        dict(success=False, conflict_term=conflict_term,
                             conflict_index=conflict_index))
            return

        # 一致性通过：按 (任期, 索引) 解决冲突，严格日志序追加
        for e in p["entries"]:
            if e.index <= len(self.log):
                if self.entry(e.index).term != e.term:
                    # 同索引不同任期：截断该条及其后全部，再追加
                    self._truncate(e.index - 1, "term_mismatch")
                    self.log.append(LogEntry(e.term, e.value, e.index))
            else:
                self.log.append(LogEntry(e.term, e.value, e.index))
        self.trace("ae_append", leader=m.src,
                   added=[e.index for e in p["entries"]],
                   last_index=self.last_log_index())

        # 提交点跟随 leader，但不超过自己日志长度，且单调不减
        if p["leader_commit"] > self.commit_idx:
            new_commit = min(p["leader_commit"], self.last_log_index())
            if new_commit > self.commit_idx:
                self.commit_idx = new_commit
                self.trace("commit_advance", new_commit=new_commit,
                           source="leader", leader=m.src)
                self._apply()
        self._reply(m, "AppendEntriesReply",
                    dict(success=True, conflict_term=0,
                         conflict_index=0, match_index=self.last_log_index()))

    def _on_append_entries_reply(self, m: Message) -> None:
        if m.term > self.term:
            self._to_follower(m.term, "ae_reply_higher_term")
            return
        if self.role != LEADER or m.term != self.term:
            return
        p = m.payload
        src = m.src
        if not p["success"]:
            ct, ci = p["conflict_term"], p["conflict_index"]
            if ct == 0:
                self.next_idx[src] = max(1, ci)
            else:
                leader_terms = [k for k, e in enumerate(self.log, start=1)
                                if e.term == ct]
                self.next_idx[src] = (leader_terms[-1] + 1) if leader_terms \
                    else max(1, ci)
            self.trace("ae_backoff", follower=src,
                       next_index=self.next_idx[src],
                       conflict_term=ct, conflict_index=ci)
            self._send_append_entries(src)
            return

        new_match = p["match_index"]
        if new_match > self.match_idx.get(src, 0):
            self.match_idx[src] = new_match
            self.next_idx[src] = new_match + 1
            self.trace("ae_ack", follower=src, match=new_match)
            self._maybe_commit()

    # ------------------------------------------------------------------ #
    # 提交与应用
    # ------------------------------------------------------------------ #
    def _maybe_commit(self) -> None:
        """Raft 安全提交规则：只提交多数派复制、且属于当前任期的条目。

        更早任期的条目不能直接数票数提交（Figure 8 问题）；一旦当前任期的
        某个条目提交，其之前的所有条目借助日志匹配性质一并提交，commit_idx
        只增不减，因此“已提交”状态天然被保留。
        """
        for i in range(self.last_log_index(), self.commit_idx, -1):
            if self.entry(i).term != self.term:
                continue
            replicated = 1 + sum(1 for j in self.peers
                                 if self.match_idx.get(j, 0) >= i)
            if replicated >= self.majority:
                self.commit_idx = i
                self.trace("commit_advance", new_commit=i,
                           source="quorum", replicated=replicated)
                self._apply()
                return

    def _apply(self) -> None:
        """严格按日志索引顺序应用到状态机，绝不跳号、乱序。"""
        while self.last_applied < self.commit_idx:
            self.last_applied += 1
            e = self.entry(self.last_applied)
            if e.index != self.last_applied:
                raise SafetyViolation("apply 索引与日志位置不一致")
            self.applied.append(e.value)
            if isinstance(e.value, str) and "=" in e.value:
                k, v = e.value.split("=", 1)
                self.state[k.strip()] = v
            self.trace("apply", index=e.index, term=e.term, value=e.value)

    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        return dict(
            id=self.id, term=self.term, role=self.role,
            voted_for=self.voted_for,
            log=[dict(index=e.index, term=e.term, value=e.value)
                 for e in self.log],
            commit_idx=self.commit_idx,
            last_applied=self.last_applied,
            applied=list(self.applied),
            state=dict(self.state),
        )

