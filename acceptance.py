#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收测试。

1. 50 次随机分区 / 乱序 / 丢包注入：
   - 任意已提交条目不被覆盖（commit 前缀逐节点一致，截断永不越过 commit_idx）；
   - 任意两节点应用序列前缀一致（checkpoint 中途 + 收敛后双重校验）；
   - 同任期不出现双 leader；日志在共同长度上互为前缀。
2. 确定性脑裂专项：
   - 少数派旧主在隔离期的写无法提交；
   - 恢复后旧主观测到更高任期被降级，其写请求被拒绝；
   - 旧主未提交条目被 (任期, 索引) 冲突截断，最终状态机一致；
   - 竞态写（heal 同一时刻打到旧主）即便被接受也只能是未提交且被截断。
3. 每条节点决策都能追溯到时序事件 event_id，且该事件真实存在。
"""
import random
import sys
from typing import Any, Dict, List, Tuple

from raft_sim import Simulator
from raft_core import SafetyViolation, LEADER

N_NODES = 5
N_RUNS = 50
HORIZON = 240


# ====================================================================== #
# 随机场景生成
# ====================================================================== #
def build_random_sim(seed: int) -> Simulator:
    rng = random.Random(seed * 1009 + 17)
    # 选举超时取心跳周期的 5~10 倍，避免正常心跳延迟引发无谓选举（Raft 活性
    # 参数的标准工程取值）；安全不变量不依赖该比例，乱序/丢包照常注入。
    sim = Simulator(
        n=N_NODES, seed=seed, duration=HORIZON,
        et_min=16, et_max=28, hb=3,
        network=dict(delay=(1, 3),
                     drop=rng.choice([0.0, 0.03, 0.08, 0.12]),
                     reorder=rng.choice([0.0, 0.2, 0.35, 0.5])))

    # 周期性客户端写：随机目标节点（含非 leader / 被隔离节点，覆盖拒绝路径）
    cmd = 0
    t = 2.0
    while t < HORIZON - 25:
        cmd += 1
        target = rng.randrange(N_NODES)
        sim.add_client_write(t, target, "k%d=v%d" % (cmd, seed))
        t += rng.randint(4, 9)

    # 随机分区 / 恢复，分区互不重叠
    t = 12.0
    while t < HORIZON - 30:
        t += rng.randint(18, 34)
        nodes = list(range(N_NODES))
        rng.shuffle(nodes)
        cut = rng.randint(1, N_NODES - 1)
        groups = [sorted(nodes[:cut]), sorted(nodes[cut:])]
        # 保证至少一侧是多数派（避免完全无法提交的极端轮次过多）
        if rng.random() < 0.25:
            cut2 = rng.randint(1, N_NODES - 1)
            groups = [sorted(nodes[:cut2]), sorted(nodes[cut2:])]
        sim.add_partition(t, groups)
        heal_t = t + rng.randint(15, 30)
        sim.add_heal(heal_t)
        t = heal_t

    # 定期 checkpoint：在运行中途就校验前缀不变量
    for ct in range(30, HORIZON - 10, 30):
        sim.add_checkpoint(ct)
    return sim


# ====================================================================== #
# 不变量校验
# ====================================================================== #
def assert_safety(result: Dict[str, Any], label: str) -> None:
    cps = result["checkpoints"]
    # 没有 checkpoint 事件时，对最终状态做一次即时不变量检查
    if not cps:
        live = _live_invariants(result)
        targets = [dict(t="final", **live)]
    else:
        targets = cps
    for cp in targets:
        for key in ("election_safety", "log_prefix_match",
                    "committed_prefix_preserved", "committed_identical",
                    "applied_prefix_match", "state_machine_order"):
            if not cp[key]:
                raise AssertionError(
                    "%s: checkpoint t=%s 不变量 %s 失败: %s"
                    % (label, cp["t"], key, cp))
    if result["violations"]:
        raise AssertionError("%s: SafetyViolation %s"
                             % (label, result["violations"]))


def _live_invariants(result: Dict[str, Any]) -> Dict[str, Any]:
    nodes = result["nodes"]
    # 选举安全：同一任期至多一个 leader（取 term_log 历史）
    by_term: Dict[int, List[int]] = {}
    for rec in result["term_log"]:
        by_term.setdefault(rec["term"], [])
        if rec["leader"] not in by_term[rec["term"]]:
            by_term[rec["term"]].append(rec["leader"])
    double = {t: ls for t, ls in by_term.items() if len(ls) > 1}
    seq = [[(e["term"], e["value"]) for e in nd["log"]] for nd in nodes]
    commits = [nd["commit_idx"] for nd in nodes]
    prefix_ok = all(
        seq[a][:min(commits[a], commits[b])]
        == seq[b][:min(commits[a], commits[b])]
        for a in range(len(nodes)) for b in range(a + 1, len(nodes)))
    cmin = min(commits)
    base = seq[0][:cmin]
    apps = [nd["applied"] for nd in nodes]
    app_ok = all(
        apps[a][:min(len(apps[a]), len(apps[b]))]
        == apps[b][:min(len(apps[a]), len(apps[b]))]
        for a in range(len(nodes)) for b in range(a + 1, len(nodes)))
    state_ok = all(
        nd["applied"] == [e["value"] for e in nd["log"][:nd["commit_idx"]]]
        and nd["last_applied"] == nd["commit_idx"]
        for nd in nodes)
    return dict(
        election_safety=not double, double_leaders=double,
        log_prefix_match=prefix_ok,
        committed_prefix_preserved=prefix_ok,
        committed_identical=all(s[:cmin] == base for s in seq),
        min_commit_idx=cmin,
        applied_prefix_match=app_ok, state_machine_order=state_ok)


def assert_traceability(result: Dict[str, Any], label: str) -> None:
    event_ids = {e["event_id"] for e in result["events"]}
    for d in result["decisions"]:
        if d["event_id"] not in event_ids:
            raise AssertionError(
                "%s: 决策 %s 找不到来源事件 %s"
                % (label, d["action"], d["event_id"]))


def assert_convergence(result: Dict[str, Any], label: str) -> None:
    nodes = result["nodes"]
    cmin = min(nd["commit_idx"] for nd in nodes)
    if cmin < 1:
        raise AssertionError("%s: 收敛后没有任何已提交条目" % label)
    base = [(e["term"], e["index"], e["value"]) for e in nodes[0]["log"]]
    for nd in nodes[1:]:
        cur = [(e["term"], e["index"], e["value"]) for e in nd["log"]]
        if cur != base:
            raise AssertionError("%s: 节点%d 日志未收敛" % (label, nd["id"]))
        if nd["applied"] != nodes[0]["applied"]:
            raise AssertionError("%s: 节点%d 应用序列不一致" % (label, nd["id"]))
        if nd["state"] != nodes[0]["state"]:
            raise AssertionError("%s: 节点%d 状态机不一致" % (label, nd["id"]))
    # 被拒绝的【命令】绝不能出现在任何节点的应用序列中（按 cmd 判定，
    # 同名值可能在重试到 leader 后合法提交）
    rejected_cmds = {r["cmd"] for r in result["rejected"] if "cmd" in r}
    proposals = {p["cmd"]: p["value"] for p in result["proposals"]}
    rejected_values = {proposals[c] for c in rejected_cmds if c in proposals}
    for nd in nodes:
        applied_cmds = set()
        # applied 里只存值，需要结合 proposals 映射：若某被拒 cmd 的值出现，
        # 必须存在另一条同值且最终 committed 的提案，否则视为被拒写泄漏
        for val in nd["applied"]:
            if val in rejected_values:
                committed_same_value = any(
                    p["value"] == val and p["final"] == "committed"
                    and p["cmd"] not in rejected_cmds
                    for p in result["proposals"])
                if not committed_same_value:
                    raise AssertionError(
                        "%s: 被拒绝的写 %r 出现在节点%d 应用序列"
                        % (label, val, nd["id"]))


def reconverge(sim: Simulator, tag: str, rounds: int = 6) -> Dict[str, Any]:
    """恢复连通并通过当前 leader 追加 marker 条目，强制全日志对账。

    Raft 保证当前 leader 的新条目复制到 follower 时，会按 (任期,索引)
    截断其冲突尾部；几轮 marker 后所有节点完整日志必然一致。
    """
    sim.net.heal()  # 先愈合任何残留分区，验证“脑裂恢复后”的收敛性
    result = None
    for r in range(rounds):
        leaders = [i for i, nd in enumerate(sim.nodes) if nd.role == LEADER]
        if leaders:
            sim.add_client_write(sim.time + 1, leaders[0],
                                 "marker-%s-%d" % (tag, r))
        result, _ = sim.run(sim.time + 30)
    # 静默期：不再注入新写，只让在途 AppendEntries / 提交点传播完成
    result, _ = sim.run(sim.time + 20)
    return result


def test_random_runs() -> Tuple[int, int]:
    committed_total = 0
    rejected_total = 0
    for seed in range(N_RUNS):
        sim = build_random_sim(seed)
        result, _ = sim.run()
        label = "random-seed-%d" % seed
        assert_safety(result, label)
        assert_traceability(result, label)

        # 主场景结束后强制对账，再验证完整日志/状态机收敛
        result = reconverge(sim, "s%d" % seed)
        assert_safety(result, label + "-reconverge")
        assert_traceability(result, label + "-reconverge")
        assert_convergence(result, label)
        committed_total += min(nd["commit_idx"] for nd in result["nodes"])
        rejected_total += len(result["rejected"])
        # 已接受但未提交的写：对账后必须已被截断，不残留在任何节点日志中
        for p in result["proposals"]:
            if p["final"] == "lost_uncommitted":
                for nd in result["nodes"]:
                    vals = [e["value"] for e in nd["log"]]
                    if p["value"] in vals:
                        raise AssertionError(
                            "%s: 未提交写 %r 残留于节点%d 日志"
                            % (label, p["value"], nd["id"]))
        print("  [随机 %02d] 提交条目=%2d 拒绝写=%2d 丢包=%3d 决策=%4d OK"
              % (seed,
                 min(nd["commit_idx"] for nd in result["nodes"]),
                 len(result["rejected"]),
                 result["network"]["dropped"],
                 len(result["decisions"])))
    return committed_total, rejected_total


# ====================================================================== #
# 确定性脑裂专项
# ====================================================================== #
def _find_leader(sim: Simulator) -> int:
    leaders = [i for i, nd in enumerate(sim.nodes) if nd.role == LEADER]
    if len(leaders) != 1:
        raise AssertionError("期望唯一 leader，实际 %s" % leaders)
    return leaders[0]


def _wait_commit(sim: Simulator, value: str, horizon: float) -> bool:
    sim.run(horizon)
    for nd in sim.nodes:
        if value in nd.applied:
            return True
    return False


def test_split_brain() -> None:
    print("\n[脑裂专项] 确定性时序验证")
    sim = Simulator(n=5, seed=42, duration=420,
                    et_min=16, et_max=28, hb=3,
                    network=dict(delay=(1, 2), drop=0.0, reorder=0.2))
    # 阶段 0：先等首个 leader 选出，再提交一条写
    sim.run(35)
    old_leader = _find_leader(sim)
    sim.add_client_write(38, old_leader, "x=1")
    if not _wait_commit(sim, "x=1", 55):
        raise AssertionError("初始写未提交")
    old_term = sim.nodes[old_leader].term
    print("  初始 leader=节点%d term=%d，x=1 已提交" % (old_leader, old_term))

    # 阶段 1：2/3 分区，旧主落在 2 节点少数派
    minority = sorted([old_leader,
                       (old_leader + 1) % N_NODES])
    majority = [i for i in range(N_NODES) if i not in minority]
    sim.add_partition(60, [minority, majority])
    sim.run(62)

    # 阶段 2：旧主在少数派继续接收写（无法获得多数派 ack，不能提交）
    sim.add_client_write(70, old_leader, "x=stale")
    sim.run(95)
    old_node = sim.nodes[old_leader]
    stale_accepted = any(e.value == "x=stale" for e in old_node.log)
    if not stale_accepted:
        raise AssertionError("旧主应当先接受本地写（尚未知自己已过期）")
    if "x=stale" in old_node.applied:
        raise AssertionError("少数派写不得被提交/应用")
    if any("x=stale" in nd.applied for nd in sim.nodes):
        raise AssertionError("少数派写不得扩散到任何应用序列")
    print("  旧主少数派写 x=stale 仅停留未提交日志，commit_idx=%d"
          % old_node.commit_idx)

    # 阶段 3：多数派选出新主并提交自己的写（多次尝试直到打到新主）
    for attempt, wt in enumerate((105, 125, 145)):
        leaders = [i for i in majority if sim.nodes[i].role == LEADER]
        target = leaders[0] if leaders else majority[attempt % len(majority)]
        sim.add_client_write(wt, target, "x=2")
        result, _ = sim.run(wt + 12)
        if any("x=2" in nd.applied for nd in sim.nodes):
            break
    new_leader = _find_leader_in_group(sim, majority)
    if "x=2" not in sim.nodes[new_leader].applied:
        raise AssertionError("多数派任期写 x=2 未提交")
    new_term = sim.nodes[new_leader].term
    if new_term <= old_term:
        raise AssertionError("新主任期应当高于旧主")
    print("  多数派新主=节点%d term=%d，x=2 已提交" % (new_leader, new_term))

    # 阶段 4：恢复连通。先让 RPC 自然流动，旧主必须因更高任期降级
    sim.add_heal(150)
    sim.run(170)
    if sim.nodes[old_leader].role == LEADER:
        raise AssertionError("恢复后旧主未降级")
    if sim.nodes[old_leader].term < new_term:
        raise AssertionError("旧主未同步到更高任期")
    print("  heal 后旧主降级为 %s，term=%d"
          % (sim.nodes[old_leader].role, sim.nodes[old_leader].term))

    # 阶段 5：向已降级的旧主打写 —— 必须被拒绝
    sim.add_client_write(175, old_leader, "x=after-heal")
    result, _ = sim.run(190)
    hit = [r for r in result["rejected"]
           if r["value"] == "x=after-heal" and r["to"] == old_leader]
    if not hit:
        raise AssertionError("恢复后旧主写必须被拒绝")
    print("  旧主写 x=after-heal 被拒绝 reason=%s" % hit[0]["reason"])

    # 阶段 6：再次分区制造竞态 —— 旧主在隔离期接受的写恢复后必须被截断
    sim.add_partition(200, [minority, majority])
    sim.add_client_write(210, old_leader, "x=race-accepted")
    sim.run(225)
    # 在恢复的同一时刻向旧主注入写（可能先接受后随降级被截断）
    sim.add_heal(230)
    sim.add_client_write(230, old_leader, "x=race-edge")
    result, _ = sim.run(265)
    assert_safety(result, "split-brain-race")
    assert_traceability(result, "split-brain-race")
    for nd in result["nodes"]:
        for banned in ("x=stale", "x=race-accepted", "x=race-edge"):
            if banned in nd["applied"]:
                raise AssertionError("过期写 %s 不得被应用" % banned)
    print("  竞态写未产生任何已提交/已应用记录")

    # 阶段 7：最终一致性 + 冲突截断轨迹可查
    result = reconverge(sim, "sb")
    assert_safety(result, "split-brain-final")
    assert_traceability(result, "split-brain-final")
    assert_convergence(result, "split-brain-final")
    trunc = [d for d in result["decisions"] if d["action"] == "log_truncate"]
    if not trunc:
        raise AssertionError("应当存在按 (任期,索引) 的冲突截断轨迹")
    term_bumps = [d for d in result["decisions"]
                  if d["action"] == "term_bump" and d["node"] == old_leader
                  and d["detail"].get("new_term") == new_term]
    if not term_bumps:
        raise AssertionError("旧主缺少向 term=%d 的任期跃迁轨迹" % new_term)
    final_applied = result["nodes"][0]["applied"]
    if "x=1" not in final_applied or "x=2" not in final_applied:
        raise AssertionError("已提交的 x=1/x=2 必须保留")
    print("  冲突截断事件 %d 次，最终应用序列=%s"
          % (len(trunc), final_applied))
    print("  最终状态机=%s" % result["nodes"][0]["state"])


def _find_leader_in_group(sim: Simulator, group: List[int]) -> int:
    leaders = [i for i in group if sim.nodes[i].role == LEADER]
    if not leaders:
        raise AssertionError("分组 %s 内无 leader" % group)
    return leaders[0]


# ====================================================================== #
# 同索引双记录专项（乱序放大冲突截断）
# ====================================================================== #
def test_duplicate_index() -> None:
    print("\n[同索引冲突专项] 高乱序 + 多次分区恢复")
    sim = Simulator(n=5, seed=99, duration=320,
                    et_min=16, et_max=28, hb=3,
                    network=dict(delay=(1, 4), drop=0.1, reorder=0.6))
    for i, t in enumerate(range(2, 260, 7)):
        sim.add_client_write(float(t), i % N_NODES, "d%d=%d" % (i, i))
    for t in (40, 110, 180):
        sim.add_partition(float(t), [[0, 4], [1, 2, 3]])
        sim.add_heal(float(t + 35))
    for t in range(45, 290, 30):
        sim.add_checkpoint(float(t))
    result, _ = sim.run()
    assert_safety(result, "dup-index")
    assert_traceability(result, "dup-index")
    result = reconverge(sim, "dup")
    assert_safety(result, "dup-index-reconverge")
    assert_convergence(result, "dup-index")
    # 每个节点日志内索引唯一且连续 —— 同一索引不可能存在两条记录
    for nd in result["nodes"]:
        idxs = [e["index"] for e in nd["log"]]
        if idxs != list(range(1, len(idxs) + 1)):
            raise AssertionError("节点%d 日志索引不连续/重复: %s"
                                 % (nd["id"], idxs))
    print("  截断事件 %d 次，最终日志长度 %d，无同索引双记录"
          % (len([d for d in result["decisions"]
                  if d["action"] == "log_truncate"]),
             len(result["nodes"][0]["log"])))


def main() -> int:
    print("=" * 70)
    print("验收 1：%d 次随机分区/乱序/丢包注入" % N_RUNS)
    print("=" * 70)
    try:
        committed, rejected = test_random_runs()
        test_split_brain()
        test_duplicate_index()
    except (AssertionError, SafetyViolation) as ex:
        print("\n验收失败: %s" % ex)
        return 1
    print("\n" + "=" * 70)
    print("全部验收通过：%d 轮随机场景累计提交 %d 条、拒绝 %d 条；"
          % (N_RUNS, committed, rejected))
    print("已提交条目零覆盖、应用序列全局前缀一致、脑裂恢复后最终一致。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())

