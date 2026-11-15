#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量回填的多分片完成屏障:计数-到达屏障 + 超时一致收敛。

CLI: python3 barrier.py input.json output.json

输入事件模型 (事件带时间戳 t,是一个事件集合,与投递顺序无关):
  arrive  : 某分片报告到达某批次
  lost    : 某分片在某批次被观测到失联(仅注解,不剥夺之后的有效到达)
  timeout : 某批次屏障定时器触发,触发后按 policy 统一收敛

修复基线的三类缺陷:
  1. 失联/缺分片不再永久 pending —— 批次一旦 timeout,按 policy 一次性收敛为
     released(放行)或整批失败(fail),没有分片会无限等待;
  2. 裁决只在批次级别产生一次,所有分片对同一批次的 verdict 完全相同,消灭分裂;
  3. 迟到 arrive 不阻塞已关闭批次,滚动计入下一个仍开放的批次(下一批次);
  4. 事件先去重(重试幂等)再按 (t, 类型次序, shard, batch) 规范化排序,
     乱序投递 / 重复投递下输出稳定,digest 一致。

同一时刻 t 的确定性次序: arrive -> lost -> timeout
(即在超时点上瞬间到达的 arrive 仍算作准时,超时随后如已凑齐则被忽略)。
"""
import hashlib
import json
import sys

# 同一时间戳内的处理次序:到达优先,失联注解次之,超时最后
TYPE_RANK = {"arrive": 0, "lost": 1, "timeout": 2}
# timeout 触发时,policy -> 批次裁决
POLICY_VERDICT = {"release": "released", "fail": "fail"}
ALLOWED_VERDICTS = {"pass", "fail", "released", "pending"}


def canonicalize(events):
    """去重 + 规范化排序,使处理过程只依赖事件集合而与投递顺序无关。"""
    seen = set()
    canon = []
    for e in events or []:
        typ = e.get("type")
        if typ not in TYPE_RANK:
            continue
        t = e["t"]
        b = e.get("batch")
        s = e.get("shard", -1)
        # timeout 可能被多个分片重复观测,按 (t, batch) 只认一次;
        # arrive/lost 按 (t, batch, shard) 去重 —— 重试消息天然幂等。
        key = (t, b, typ) if typ == "timeout" else (t, b, typ, s)
        if key in seen:
            continue
        seen.add(key)
        canon.append({"t": t, "batch": b, "shard": s, "type": typ})
    canon.sort(key=lambda x: (x["t"], TYPE_RANK[x["type"]], x["shard"], x["batch"]))
    return canon


def _new_state():
    return {
        "arrived": set(),   # 本批次已有效到达的分片(含上一批滚动来的迟到分片)
        "lost": set(),      # 本批次被观测到失联的分片(历史注解)
        "open": True,       # 屏障是否仍开放
        "verdict": None,    # 唯一批次级裁决
        "closed_at": None,  # 关闭时间
        "reason": None,     # 关闭/挂起原因
    }


def simulate(req):
    """对一次回填请求做确定性屏障模拟,返回批次结果、逐事件决策与摘要。"""
    shards = req["shards"]
    batches = req["batches"]
    timeout_verdict = POLICY_VERDICT[req.get("policy", "release")]
    state = {b: _new_state() for b in range(1, batches + 1)}
    trace = []

    def close(b, verdict, t, reason):
        st = state[b]
        st["open"] = False
        st["verdict"] = verdict
        st["closed_at"] = t
        st["reason"] = reason

    for e in canonicalize(req.get("events", [])):
        t, b, s, typ = e["t"], e["batch"], e["shard"], e["type"]

        if b is None or b < 1 or b > batches:
            trace.append(dict(e, action="ignored_out_of_range"))
            continue

        st = state[b]

        if typ == "lost":
            if st["open"]:
                st["lost"].add(s)
                trace.append(dict(e, action="marked_lost"))
            else:
                trace.append(dict(e, action="ignored_batch_closed"))

        elif typ == "timeout":
            if st["open"]:
                # 唯一的批次级收敛点:放行或整批失败,结论对所有分片一致
                close(b, timeout_verdict, t, "timeout")
                trace.append(dict(e, action="timeout_" + timeout_verdict))
            else:
                trace.append(dict(e, action="timeout_ignored_already_closed"))

        else:  # arrive
            if s in st["arrived"]:
                # 同一分片的重复到达(重试/重发),幂等忽略,绝不滚动到下一批
                trace.append(dict(e, action="retry_ignored"))
            elif st["open"]:
                st["arrived"].add(s)
                if len(st["arrived"]) == shards:
                    # 计数-到达屏障凑齐 S 个分片 -> pass,屏障立即关闭
                    close(b, "pass", t, "all_arrived")
                    trace.append(dict(e, action="arrived_and_pass"))
                else:
                    trace.append(dict(e, action="arrived"))
            else:
                # 迟到分片:目标批次已关闭,不阻塞它,滚动加入下一个开放批次
                target = next((nb for nb in range(b + 1, batches + 1)
                               if state[nb]["open"]), None)
                if target is None:
                    trace.append(dict(e, action="late_unrouted"))
                else:
                    ts = state[target]
                    if s not in ts["arrived"]:
                        ts["arrived"].add(s)
                        if len(ts["arrived"]) == shards:
                            close(target, "pass", t, "all_arrived_rollover")
                    trace.append(dict(e, action="late_rollover",
                                      target_batch=target))

    # 收尾:流结束时仍开放的批次(没有 timeout 事件、也没凑齐)如实报 pending,
    # 但只要出现 timeout / 失联后超时,批次必然已收敛,不会永久挂死。
    batch_results = []
    for b in range(1, batches + 1):
        st = state[b]
        if st["open"]:
            if len(st["arrived"]) >= shards:
                close(b, "pass", st["closed_at"], "all_arrived")
            else:
                st["verdict"] = "pending"
                st["reason"] = "in_flight_no_timeout"
        assert st["verdict"] in ALLOWED_VERDICTS
        batch_results.append({
            "id": b,
            "result": st["verdict"],
            "arrived": sorted(st["arrived"]),
            "missing": sorted(set(range(shards)) - st["arrived"]),
            "lost": sorted(st["lost"]),
            "closed_at": st["closed_at"],
            "reason": st["reason"],
        })

    # 每个分片的结论直接取自唯一批次级裁决 -> 同批同分,不可能分裂
    decisions = [
        {"shard": s, "batch": b, "verdict": state[b]["verdict"]}
        for b in range(1, batches + 1) for s in range(shards)
    ]

    digest_payload = {
        "batches": [{"id": x["id"], "result": x["result"]} for x in batch_results],
        "decisions": decisions,
    }
    digest = hashlib.sha1(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()

    return {"batches": batch_results, "decisions": decisions,
            "trace": trace, "digest": digest}


def main(argv):
    if len(argv) != 3:
        print("usage: python3 barrier.py input.json output.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        req = json.load(f)
    result = simulate(req)
    with open(argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
