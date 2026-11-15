#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带快照可见性与水位回收的 MVCC 执行器。

用法:
    python3 mvcc.py input.json output.json

语义要点:
  * begin 与 commit 各自分配一个全局递增时间戳(单调时钟)。
  * read 先查本事务尚未提交的 writes(读己之写), 再在版本链中取
    commit_ts <= begin_ts 的最新版本。未提交的写入永远不会进入版本链,
    因此并行事务不会读到"半提交"状态, 也不会读到更晚的事务。
  * gc 以最老活跃事务的 begin_ts 作为回收水位: 每条版本链保留
    "水位之前最新的一个基准版本" + "水位之后的全部版本", 其余更老的
    悬空版本释放。水位之下的第二个及更老版本对任何快照都不可见。
  * 执行严格按输入顺序遍历, visibility 与 replay 记录了足够的决策信息,
    可以按原交错顺序重放出同一读写结果、同一存储终态与同一统计摘要。
"""

import hashlib
import json
import sys


class Transaction(object):
    __slots__ = ("begin_ts", "writes", "state")

    def __init__(self, begin_ts):
        self.begin_ts = begin_ts
        self.writes = {}
        self.state = "active"  # active | committed | ended


def _rightmost_le(chain, ts):
    """返回版本链中 commit_ts <= ts 的最右下标; 不存在返回 -1。

    版本链的 commit_ts 严格递增(来自全局单调时钟), 因而可二分查找。
    """
    lo, hi = 0, len(chain)
    while lo < hi:
        mid = (lo + hi) // 2
        if chain[mid][0] <= ts:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def _require_tx(txs, txid):
    if txid not in txs:
        raise ValueError("未知事务: %r" % (txid,))
    return txs[txid]


def _require_active(txs, txid, op_name):
    tx = _require_tx(txs, txid)
    if tx.state != "active":
        raise ValueError("事务 %r 在 %s 时已不活跃(%s)" % (txid, op_name, tx.state))
    return tx


def _digest(obj):
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def run_ops(ops):
    """按给定交错顺序执行操作序列, 返回完整结果(可 JSON 序列化)。"""
    clock = 0                    # 分配 begin_ts / commit_ts 的全局时钟
    versions = {}                # k -> [[commit_ts, value], ...], commit_ts 递增
    txs = {}                     # txid -> Transaction
    reads = []                   # 兼容基线口径: [{"tx","k","v"}, ...]
    visibility = []              # 每次读的可见性决策
    gc_log = []                  # 每次 gc 的水位与回收明细
    replay = []                  # 交错执行轨迹, 可重放
    reclaimed_total = 0

    for index, op in enumerate(ops):
        kind = op.get("op")

        if kind == "begin":
            txid = op.get("tx")
            if txid is None:
                raise ValueError("begin 缺少 tx (第 %d 项)" % index)
            old = txs.get(txid)
            if old is not None and old.state == "active":
                raise ValueError("事务 %r 重复 begin (第 %d 项)" % (txid, index))
            clock += 1
            txs[txid] = Transaction(clock)
            replay.append({"i": index, "op": "begin", "tx": txid, "begin_ts": clock})

        elif kind == "write":
            txid = op.get("tx")
            tx = _require_active(txs, txid, "write")
            key = op.get("k")
            tx.writes[key] = op.get("v")
            replay.append({"i": index, "op": "write", "tx": txid, "k": key, "v": op.get("v")})

        elif kind == "read":
            txid = op.get("tx")
            tx = _require_tx(txs, txid)
            key = op.get("k")

            # 1) 读己之写: 本事务的写入优先, 即使其它事务随后提交了更新的值。
            if key in tx.writes:
                value = tx.writes[key]
                source = "own_write"
                version_ts = None
            else:
                # 2) 快照读: commit_ts <= begin_ts 的最新已提交版本。
                chain = versions.get(key, ())
                idx = _rightmost_le(chain, tx.begin_ts)
                if idx >= 0:
                    version_ts, value = chain[idx]
                    source = "snapshot"
                else:
                    version_ts, value = None, None
                    source = "none"

            read_index = len(reads)
            reads.append({"tx": txid, "k": key, "v": value})
            visibility.append({
                "read_index": read_index,
                "tx": txid,
                "k": key,
                "v": value,
                "begin_ts": tx.begin_ts,
                "source": source,
                "version_ts": version_ts,
            })
            replay.append({
                "i": index, "op": "read", "tx": txid, "k": key,
                "read_index": read_index, "source": source, "version_ts": version_ts,
            })

        elif kind == "commit":
            txid = op.get("tx")
            tx = _require_active(txs, txid, "commit")
            clock += 1
            commit_ts = clock
            # 提交是原子的: 同一事务所有键在同一 commit_ts 进入版本链,
            # 同键多次写以最终值为准(每链至多一个本事务版本)。
            for key, value in tx.writes.items():
                versions.setdefault(key, []).append([commit_ts, value])
            tx.state = "committed"
            replay.append({
                "i": index, "op": "commit", "tx": txid,
                "commit_ts": commit_ts, "keys": sorted(tx.writes.keys()),
            })

        elif kind == "end":
            txid = op.get("tx")
            tx = _require_tx(txs, txid)
            # 未提交的 writes 随事务结束丢弃, 从未进入版本链。
            tx.state = "ended"
            replay.append({"i": index, "op": "end", "tx": txid})

        elif kind == "gc":
            live = sorted(tx.begin_ts for tx in txs.values() if tx.state == "active")
            if live:
                watermark = live[0]
                effective = watermark
            else:
                # 无活跃快照: 后续事务 begin_ts 必大于当前时钟, 只可能看到
                # 各键最新版本, 因此水位推进到当前时钟。
                watermark = None
                effective = clock

            by_key = {}
            reclaimed_versions = []
            for key in list(versions.keys()):
                chain = versions[key]
                # baseline 是水位处(含)之前最新的版本, 它对最老快照仍然可见;
                # 再往前的版本对所有快照都不可见, 属于悬空版本。
                baseline = _rightmost_le(chain, effective)
                if baseline > 0:
                    dropped = chain[:baseline]
                    versions[key] = chain[baseline:]
                    by_key[key] = len(dropped)
                    reclaimed_total += len(dropped)
                    for commit_ts, value in dropped:
                        reclaimed_versions.append({"k": key, "commit_ts": commit_ts, "v": value})
                if not versions[key]:
                    del versions[key]

            gc_log.append({
                "i": index,
                "watermark": watermark,
                "effective_watermark": effective,
                "active_snapshots": live,
                "reclaimed": sum(by_key.values()),
                "by_key": by_key,
                "reclaimed_versions": reclaimed_versions,
            })
            replay.append({
                "i": index, "op": "gc", "watermark": watermark,
                "effective_watermark": effective,
                "reclaimed": sum(by_key.values()),
            })

        else:
            raise ValueError("未知操作 %r (第 %d 项)" % (kind, index))

    result = {
        "reads": reads,
        "visibility": visibility,
        "versions": versions,
        "gc_stats": {"reclaimed": reclaimed_total},
        "gc_log": gc_log,
        "replay": replay,
        "digest": hashlib.sha1(str(reads).encode("utf-8")).hexdigest(),
        "state_digest": _digest({"versions": versions,
                                 "gc_stats": {"reclaimed": reclaimed_total},
                                 "reads": reads}),
    }
    return result


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("用法: python3 mvcc.py input.json output.json\n")
        return 2
    with open(argv[1], "r", encoding="utf-8") as handle:
        request = json.load(handle)
    result = run_ops(request["ops"])
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
