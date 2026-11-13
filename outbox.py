"""ACK 丢失下的可恢复批采集客户端。

协议把四个持久化边界串成可恢复流水线：

    after-outbox      待发送记录（稳定事件键 + 批次键 + payload 摘要）落盘
    after-sink        幂等接收端已提交，但本地尚未记录确认（UNKNOWN 窗口）
    after-checkpoint  本地检查点已持久化，源确认（ack）尚未保存
    after-ack         源确认已保存，清理待发送记录

待发送记录状态：
    UNCOMMITTED  已落盘、尚未向 sink 提交
    UNKNOWN      已尝试提交，因故障无法确认接收端结果
    COMMITTED    sink 已确认持久化（key + 摘要）
    CONFLICT     同一稳定事件键的 payload 摘要与接收端冲突，独立拒收

租约代次（lease generation）单调递增，约束检查点推进；旧代次请求以退出码 3
被隔离，不推进任何状态。所有源、接收端与故障均为本地文件模拟，写入采用
临时文件 + 原子 rename + fsync。
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# ---- 协议常量 -------------------------------------------------------------

OUT_UNCOMMITTED = "UNCOMMITTED"
OUT_UNKNOWN = "UNKNOWN"
OUT_COMMITTED = "COMMITTED"
OUT_CONFLICT = "CONFLICT"

REASON_INVALID = "INVALID"
REASON_DIGEST_CONFLICT = "DIGEST_CONFLICT"

CRASH_POINTS = ("after-outbox", "after-sink", "after-checkpoint", "after-ack")
DEFAULT_BUFFER_CAP = 64          # 跨分区缺口缓存的水位容量
EXIT_OLD_GENERATION = 3          # 旧/未知代次租约
EXIT_BACKPRESSURE = 4            # 水位回压
EXIT_CRASH = 97                  # 模拟故障中断


def canonical(obj):
    """canonical JSON：sort_keys、ensure_ascii=False、紧凑分隔符。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def digest(obj):
    """对 canonical JSON UTF-8 计算 sha256。"""
    return hashlib.sha256(canonical(obj)).hexdigest()


def event_key_of(partition, offset):
    """稳定事件键：分区 + 源位点，重写/重放同一事件保持不变。"""
    return str(partition) + "#" + str(int(offset))


# ---- 持久化 ---------------------------------------------------------------

def atomic_write_json(path, obj):
    """临时文件 + fsync + 原子 rename，并 fsync 目录，模拟崩溃安全落盘。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def initial_state():
    return {
        # partition -> 当前有效租约代次
        "leases": {},
        # partition -> 已验证持久化的连续位点前缀（缺省按 0）
        "checkpoints": {},
        # partition -> 最后一次源确认位点，永远 <= checkpoint
        "acks": {},
        # 拒收明细：partition/offset/reason 列表
        "rejects": [],
        # 本地待发送记录：event_key -> record
        "outbox": {},
        # 缺口后暂存的有效输入行：event_key -> 规范行
        "buffered": {},
        # 每分区观察到的最高源位点（水位）
        "watermarks": {},
        # 已触发的故障点（同一次 apply 只在首个有效边界中断一次）
        "crashed": [],
        # 统计：sink 提交次数 / 幂等命中次数 / 冲突次数
        "stats": {"sink_accepts": 0, "sink_idempotent_hits": 0,
                  "digest_conflicts": 0},
    }


class Store:
    """客户端状态 + 独立 sink 存储。"""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.sink_path = self.root / "sink.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = initial_state()
        if self.sink_path.exists():
            self.sink = json.loads(self.sink_path.read_text(encoding="utf-8"))
        else:
            self.sink = {}

    def save_state(self):
        atomic_write_json(self.state_path, self.state)

    def save_sink(self):
        # sink 是独立持久化边界：与客户端状态分开落盘
        atomic_write_json(self.sink_path, self.sink)


# ---- 幂等接收端 -----------------------------------------------------------

class Sink:
    """按全局稳定事件键去重的幂等接收端。

    返回 (accepted, item, kind)：
      accepted=True  -> 新提交（accepted）或重复请求的幂等命中（idempotent）
      accepted=False -> 摘要冲突（conflict），调用方不得覆盖接收端
    """

    def __init__(self, store):
        self.store = store

    def commit(self, key, sha):
        sink = self.store.sink
        existing = sink.get(key)
        if existing is None:
            sink[key] = {"key": key, "sha256": sha}
            self.store.save_sink()
            self.store.state["stats"]["sink_accepts"] += 1
            return True, sink[key], "accepted"
        if existing["sha256"] == sha:
            # 相同 key 相同摘要：重复请求无副作用
            self.store.state["stats"]["sink_idempotent_hits"] += 1
            return True, existing, "idempotent"
        # 同 key 不同摘要：冲突，不覆盖接收端
        self.store.state["stats"]["digest_conflicts"] += 1
        return False, existing, "conflict"

    def lookup(self, key):
        return self.store.sink.get(key)


# ---- 协议辅助 -------------------------------------------------------------

def normalized_row(row):
    """把输入行规范成稳定结构（仅保留协议字段）。"""
    out = {
        "partition": str(row["partition"]),
        "offset": int(row["offset"]),
        "key": row["key"],
        # 格式错误行允许缺失 payload；其在摘要计算前即被独立拒收
        "payload": row.get("payload"),
    }
    if row.get("invalid") is True:
        out["invalid"] = True
    return out


def batch_key_of(rows):
    """批次键：对本批规范行的 canonical 序列取摘要；同输入重放保持稳定。"""
    compact = [[r["partition"], r["offset"], r["key"], r["payload"],
                bool(r.get("invalid"))] for r in rows]
    return hashlib.sha256(canonical(compact)).hexdigest()


def add_reject(state, partition, offset, reason):
    item = {"partition": partition, "offset": offset, "reason": reason}
    if item not in state["rejects"]:
        state["rejects"].append(item)


def recover_unknown(store, sink):
    """启动恢复：查清所有 UNKNOWN 待发送记录在 sink 的真实提交结果。

    兼容“已发送但确认未保存”的批次——sink 已持久化则收敛为 COMMITTED，
    绝不重复提交；sink 没有则退回 UNCOMMITTED 重新投递。
    """
    state = store.state
    for key in sorted(state["outbox"]):
        rec = state["outbox"][key]
        if rec["status"] != OUT_UNKNOWN:
            continue
        item = sink.lookup(key)
        if item is None:
            rec["status"] = OUT_UNCOMMITTED
        elif item["sha256"] == rec["sha256"]:
            rec["status"] = OUT_COMMITTED
        else:
            rec["status"] = OUT_CONFLICT
    store.save_state()


def advance_prefix(store, sink, crash, bkey):
    """重放/继续处理 outbox 中仍未决的连续前缀，推进到检查点边界。

    返回模拟故障退出码，或 None 表示未中断。
    """
    state = store.state
    # 恢复检查点已持久化但源确认未保存的分区：acks 只可追到 checkpoint，
    # 永远不会越过它。
    ack_changed = False
    for partition, cp in state["checkpoints"].items():
        ack = int(state["acks"].get(partition, 0))
        if ack < int(cp):
            state["acks"][partition] = int(cp)
            ack_changed = True
    if ack_changed:
        store.save_state()

    partitions = sorted({rec["partition"] for rec in state["outbox"].values()})
    interrupted = None
    for partition in partitions:
        cp = int(state["checkpoints"].get(partition, 0))
        while True:
            key = event_key_of(partition, cp + 1)
            rec = state["outbox"].get(key)
            if rec is None:
                break  # 连续前缀在此结束（缺口或已清理）

            if rec["status"] == OUT_UNCOMMITTED:
                # 进入 UNKNOWN 窗口：先持久化“提交中”状态，再调用 sink，
                # after-sink 崩溃重启后可查询真实结果而非盲目重发。
                rec["status"] = OUT_UNKNOWN
                store.save_state()
                accepted, _item, _kind = sink.commit(key, rec["sha256"])
                if not accepted:
                    rec["status"] = OUT_CONFLICT
                    add_reject(state, partition, rec["offset"],
                               REASON_DIGEST_CONFLICT)
                else:
                    rec["status"] = OUT_COMMITTED
                store.save_state()
                if interrupted is None and crash == "after-sink" and \
                        bkey is not None and bkey in rec.get("batch_keys", []):
                    interrupted = EXIT_CRASH
                    # 状态已落盘，但检查点尚未推进

            if rec["status"] == OUT_COMMITTED:
                cp += 1
                state["checkpoints"][partition] = cp
                store.save_state()
            elif rec["status"] == OUT_CONFLICT:
                # 冲突作为可审计拒收，允许连续前缀越过它继续推进
                add_reject(state, partition, rec["offset"],
                           REASON_DIGEST_CONFLICT)
                cp += 1
                state["checkpoints"][partition] = cp
                store.save_state()
            else:  # pragma: no cover - UNKNOWN 已在恢复阶段收敛
                break
    return interrupted


# ---- 子命令 ---------------------------------------------------------------

def flush_buffered_prefix(store, sink, crash, bkey):
    """冲刷缓存中已连续的有效行，直到缺口为止。

    用于上轮回压后、本次输入未直接覆盖某分区的场景，保证已缓存的连续
    前缀不被饿死。返回 EXIT_CRASH 或 None。
    """
    state = store.state
    rejected_index = {(r["partition"], r["offset"])
                      for r in state["rejects"]}
    partitions = sorted({r["partition"] for r in state["buffered"].values()})
    new_checkpoints = {}
    for partition in partitions:
        cp = int(state["checkpoints"].get(partition, 0))
        advanced = 0
        while True:
            key = event_key_of(partition, cp + 1)
            row = state["buffered"].get(key)
            if row is None:
                if (partition, cp + 1) in rejected_index:
                    cp += 1
                    advanced += 1
                    continue
                break
            sha = digest(row["payload"])
            rec = state["outbox"].get(key)
            if rec is not None and rec["status"] != OUT_UNCOMMITTED:
                break
            if rec is None:
                rec = {
                    "event_key": key,
                    "partition": partition,
                    "offset": row["offset"],
                    "business_key": row["key"],
                    "sha256": sha,
                    "batch_keys": [],
                    "status": OUT_UNCOMMITTED,
                }
                state["outbox"][key] = rec
            if bkey is not None and bkey not in rec["batch_keys"]:
                rec["batch_keys"].append(bkey)
            rec["sha256"] = sha
            rec["status"] = OUT_UNCOMMITTED
            store.save_state()
            if crash == "after-outbox" and bkey is not None and \
                    bkey in rec["batch_keys"]:
                return EXIT_CRASH

            rec["status"] = OUT_UNKNOWN
            store.save_state()
            accepted, _item, _kind = sink.commit(key, sha)
            if not accepted:
                rec["status"] = OUT_CONFLICT
                add_reject(state, partition, row["offset"],
                           REASON_DIGEST_CONFLICT)
            else:
                rec["status"] = OUT_COMMITTED
            del state["buffered"][key]
            store.save_state()
            if crash == "after-sink" and bkey is not None and \
                    bkey in rec["batch_keys"]:
                return EXIT_CRASH
            cp += 1
            advanced += 1
        if advanced:
            new_checkpoints[partition] = cp

    if not new_checkpoints:
        return None
    for partition, cp in new_checkpoints.items():
        state["checkpoints"][partition] = cp
    store.save_state()
    if crash == "after-checkpoint" and bkey is not None:
        return EXIT_CRASH
    for partition, cp in new_checkpoints.items():
        ack = int(state["acks"].get(partition, 0))
        state["acks"][partition] = max(ack, cp)
    store.save_state()
    if crash == "after-ack" and bkey is not None:
        return EXIT_CRASH
    for key in sorted(state["outbox"]):
        rec = state["outbox"][key]
        cp = int(state["checkpoints"].get(rec["partition"], 0))
        if rec["offset"] <= cp and rec["status"] in (
                OUT_COMMITTED, OUT_CONFLICT):
            del state["outbox"][key]
    store.save_state()
    return None


def cmd_lease(store, args):
    state = store.state
    partition = str(args.partition)
    gen = int(args.generation)
    current = int(state["leases"].get(partition, 0))
    if gen < current:
        # 旧代次租约：无副作用
        sys.stderr.write(
            "stale lease generation: partition=%s got=%d current=%d\n"
            % (partition, gen, current))
        return EXIT_OLD_GENERATION
    if gen == current:
        # 重复同代 lease 命令无副作用
        return 0
    state["leases"][partition] = gen
    store.save_state()
    return 0


def cmd_apply(store, sink, args):
    state = store.state
    bkey = None
    crash = args.crash

    # ---- 1. 载入并规范输入 ----------------------------------------------
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("cannot read input: " + str(exc) + "\n")
        return 2
    rows = [normalized_row(r) for r in data.get("events", [])]
    if rows:
        bkey = batch_key_of(rows)

    # ---- 2. 租约代次校验（本批出现的每个分区都须持有匹配的有效租约） ----
    touched = sorted({r["partition"] for r in rows})
    for partition in touched:
        current = int(state["leases"].get(partition, 0))
        if args.lease != current:
            sys.stderr.write(
                "lease fence: partition=%s requested=%d current=%d\n"
                % (partition, args.lease, current))
            return EXIT_OLD_GENERATION

    # ---- 3. 恢复未决协议（UNKNOWN 查 sink，未决批次继续推进） -----------
    interrupted = advance_prefix(store, sink, crash, bkey)
    if interrupted is not None:
        return interrupted

    # ---- 4. 合并新输入：缓存旧 offset/缺口后行，更新水位 ----------------
    by_part = {}
    for row in rows:
        partition, offset = row["partition"], row["offset"]
        by_part.setdefault(partition, {})[offset] = row
        wm = int(state["watermarks"].get(partition, 0))
        if offset > wm:
            state["watermarks"][partition] = offset

    # 本次输入中高于检查点的行并入缓存（缓存可携带旧 offset 或缺口）。
    # 单行格式错误不依赖连续性：首次见到即独立审计拒收，不进入缺口缓存，
    # 从而可被单独重放审计而不改变其他有效行的顺序契约。
    for partition, offmap in by_part.items():
        cp = int(state["checkpoints"].get(partition, 0))
        for offset, row in offmap.items():
            if offset <= cp:
                # 已确认前缀的重放：对接收端做摘要复核。同键同摘要幂等无
                # 副作用；同键不同摘要说明采集器重写了事件，记独立冲突拒收
                # 且绝不覆盖接收端。
                if row.get("invalid") is True:
                    add_reject(state, partition, offset, REASON_INVALID)
                    continue
                key = event_key_of(partition, offset)
                item = sink.lookup(key)
                sha = digest(row["payload"])
                if item is None:
                    # 检查点已越过但接收端无记录（异常），重新纳入待发送
                    state["buffered"][key] = row
                elif item["sha256"] != sha:
                    add_reject(state, partition, offset,
                               REASON_DIGEST_CONFLICT)
                    state["stats"]["digest_conflicts"] += 1
                else:
                    state["stats"]["sink_idempotent_hits"] += 1
                continue
            if row.get("invalid") is True:
                add_reject(state, partition, offset, REASON_INVALID)
                continue
            state["buffered"][event_key_of(partition, offset)] = row
    store.save_state()

    # ---- 5. 逐分区处理连续前缀（顺序契约：按 offset 升序） --------------
    buffered_parts = {r["partition"] for r in state["buffered"].values()}
    rejected_index = {(r["partition"], r["offset"])
                      for r in state["rejects"]}
    new_checkpoints = {}
    for partition in sorted(set(list(by_part.keys()) + list(buffered_parts))):
        cp = int(state["checkpoints"].get(partition, 0))
        advanced = 0
        while True:
            key = event_key_of(partition, cp + 1)
            row = state["buffered"].get(key)
            if row is None:
                if (partition, cp + 1) in rejected_index:
                    # 已独立审计的拒收位点（如 INVALID）允许前缀越位
                    cp += 1
                    advanced += 1
                    continue
                break  # 缺口：只能确认连续前缀，缺口后保留缓存

            # 5a. 单行格式错误：独立审计，不影响其他行，前缀可越过它
            if row.get("invalid") is True:
                add_reject(state, partition, row["offset"], REASON_INVALID)
                del state["buffered"][key]
                cp += 1
                advanced += 1
                continue

            sha = digest(row["payload"])

            # 5b. 绑定稳定事件键 + 批次键 + 摘要，落盘待发送记录。
            # 已处于 UNKNOWN/COMMITTED/CONFLICT 的记录由恢复路径负责，
            # 绝不允许在新事件循环里被重置或覆盖摘要。
            rec = state["outbox"].get(key)
            if rec is not None and rec["status"] != OUT_UNCOMMITTED:
                break
            if rec is None:
                rec = {
                    "event_key": key,
                    "partition": partition,
                    "offset": row["offset"],
                    "business_key": row["key"],
                    "sha256": sha,
                    "batch_keys": [],
                    "status": OUT_UNCOMMITTED,
                }
                state["outbox"][key] = rec
            if bkey is not None and bkey not in rec["batch_keys"]:
                rec["batch_keys"].append(bkey)
            rec["sha256"] = sha
            rec["status"] = OUT_UNCOMMITTED
            store.save_state()  # after-outbox 边界：待发状态已落盘

            if crash == "after-outbox" and bkey is not None and \
                    bkey in rec["batch_keys"]:
                return EXIT_CRASH

            # 5c. UNKNOWN 窗口 + 幂等提交
            rec["status"] = OUT_UNKNOWN
            store.save_state()
            accepted, _item, _kind = sink.commit(key, sha)
            if not accepted:
                rec["status"] = OUT_CONFLICT
                add_reject(state, partition, row["offset"],
                           REASON_DIGEST_CONFLICT)
            else:
                rec["status"] = OUT_COMMITTED
            del state["buffered"][key]
            store.save_state()  # after-sink 边界：sink 已提交，本地待确认

            if crash == "after-sink" and bkey is not None and \
                    bkey in rec["batch_keys"]:
                return EXIT_CRASH

            cp += 1
            advanced += 1

        if advanced:
            new_checkpoints[partition] = cp

    # ---- 6. 检查点：只覆盖已验证持久化的连续前缀 ------------------------
    for partition, cp in new_checkpoints.items():
        state["checkpoints"][partition] = cp
    store.save_state()  # after-checkpoint 边界
    if crash == "after-checkpoint" and bkey is not None:
        return EXIT_CRASH

    # ---- 7. 源确认：acks 不得超过 checkpoint ----------------------------
    for partition, cp in new_checkpoints.items():
        ack = int(state["acks"].get(partition, 0))
        state["acks"][partition] = max(ack, cp)
    store.save_state()  # after-ack 边界：源确认已保存
    if crash == "after-ack" and bkey is not None:
        return EXIT_CRASH

    # ---- 8. 水位回压：连续前缀已安全落盘后，缺口缓存超容量则拒绝继续 ----
    # 先冲刷上轮回压遗留、当前已连续的缓存行（即使本批输入为空）。
    flushed = flush_buffered_prefix(store, sink, crash, bkey)
    if flushed == EXIT_CRASH:
        return EXIT_CRASH
    if len(state["buffered"]) > DEFAULT_BUFFER_CAP:
        detail = sorted(
            ({"partition": r["partition"], "offset": r["offset"]}
             for r in state["buffered"].values()),
            key=lambda d: (d["partition"], d["offset"]))
        sys.stderr.write(json.dumps({
            "error": "BACKPRESSURE",
            "buffered": len(state["buffered"]),
            "capacity": DEFAULT_BUFFER_CAP,
            "details": detail,
        }, ensure_ascii=False) + "\n")
        return EXIT_BACKPRESSURE

    # ---- 9. 清理已确认的待发送记录（COMMITTED / CONFLICT 均可清除） -----
    for key in sorted(state["outbox"]):
        rec = state["outbox"][key]
        cp = int(state["checkpoints"].get(rec["partition"], 0))
        if rec["offset"] <= cp and rec["status"] in (
                OUT_COMMITTED, OUT_CONFLICT):
            del state["outbox"][key]
    if crash is not None and bkey is not None and crash not in state["crashed"]:
        state["crashed"].append(crash)
    store.save_state()
    return 0


def inspect_view(store):
    state = store.state
    # sink 读取独立持久化的接收端存储，按分区 + 数值位点排序，保持顺序契约
    def sink_order(k):
        partition, _, offset = k.rpartition("#")
        try:
            return partition, int(offset)
        except ValueError:
            return partition, 0

    sink_keys = sorted(store.sink, key=sink_order)
    sink_sorted = [store.sink[k] for k in sink_keys]
    rejects_sorted = sorted(state["rejects"],
                            key=lambda r: (r["partition"], r["offset"]))
    return {
        "leases": {k: state["leases"][k] for k in sorted(state["leases"])},
        "checkpoints": {k: state["checkpoints"][k]
                        for k in sorted(state["checkpoints"])},
        "acks": {k: state["acks"][k] for k in sorted(state["acks"])},
        "sink": sink_sorted,
        "rejects": rejects_sorted,
        "outbox": {k: state["outbox"][k] for k in sorted(state["outbox"])},
        "buffered": sorted(
            ({"partition": r["partition"], "offset": r["offset"],
              "key": r["key"], "sha256": digest(r["payload"])}
             for r in state["buffered"].values()),
            key=lambda d: (d["partition"], d["offset"])),
        "watermarks": {k: state["watermarks"][k]
                       for k in sorted(state["watermarks"])},
        "stats": state["stats"],
        "crashed": state["crashed"],
    }


def main():
    p = argparse.ArgumentParser(description="ACK 丢失下的可恢复批采集客户端")
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)

    a = sub.add_parser("apply", help="应用一批事件")
    a.add_argument("input", type=Path)
    a.add_argument("--lease", type=int, required=True)
    a.add_argument("--crash", choices=list(CRASH_POINTS))

    q = sub.add_parser("lease", help="登记/切换分区租约代次")
    q.add_argument("partition")
    q.add_argument("generation", type=int)

    sub.add_parser("inspect", help="输出完整本地状态")

    args = p.parse_args()
    store = Store(args.root)
    sink = Sink(store)

    if args.op == "lease":
        code = cmd_lease(store, args)
    elif args.op == "apply":
        code = cmd_apply(store, sink, args)
    else:
        code = 0

    print(json.dumps(inspect_view(store), sort_keys=True,
                     ensure_ascii=False, indent=2))
    sys.exit(code)


if __name__ == "__main__":
    main()
