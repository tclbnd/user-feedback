"""验收测试：ACK 丢失、租约切换、连续位点与摘要冲突。

直接以子进程方式驱动 outbox.py，覆盖：
  1. 四个关键持久化点 + ACK 传递点逐点中断，重放后与无故障参考 sink 比对；
  2. 双消费者租约代次切换、旧实例围栏、UNKNOWN 恢复、缺口缓存与回压、
     INVALID / DIGEST_CONFLICT 独立审计。
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTBOX = HERE / "outbox.py"
CRASH_POINTS = ["after-outbox", "after-sink", "after-checkpoint", "after-ack"]


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sha256(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def run(root, *args):
    proc = subprocess.run(
        [sys.executable, str(OUTBOX), str(root), *args],
        capture_output=True, text=True)
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out, proc.stderr


def write_batch(root, name, events):
    path = Path(root) / name
    path.write_text(json.dumps({"events": events}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def lease(root, partition, generation):
    return run(root, "lease", partition, str(generation))


def apply(root, path, generation, crash=None):
    args = ["apply", str(path), "--lease", str(generation)]
    if crash:
        args += ["--crash", crash]
    return run(root, *args)


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def sink_map(view):
    return {item["key"]: item["sha256"] for item in view["sink"]}


# ---------------------------------------------------------------------------
# 测试 1：四个持久化点逐点中断，重放与无故障参考 sink 完全一致
# ---------------------------------------------------------------------------

BATCH = [
    {"partition": "p", "offset": 1, "key": "a", "payload": {"x": 1}},
    {"partition": "p", "offset": 2, "key": "b", "payload": [2]},
    {"partition": "p", "offset": 3, "key": "c", "invalid": True},
    {"partition": "q", "offset": 1, "key": "d", "payload": {"y": "中"}},
    {"partition": "q", "offset": 2, "key": "e", "payload": None},
]
EXPECTED_SINK = {
    "p#1": sha256({"x": 1}),
    "p#2": sha256([2]),
    "q#1": sha256({"y": "中"}),
    "q#2": sha256(None),
}
EXPECTED_REJECTS = [{"partition": "p", "offset": 3, "reason": "INVALID"}]


def test_crash_points():
    print("test 1: 四个持久化点逐点中断 + ACK 传递点")
    ref_dir = tempfile.mkdtemp(prefix="d07_ref_", dir=str(HERE / "run"))
    ref = Path(ref_dir)
    lease(ref, "p", 1)
    lease(ref, "q", 1)
    b = write_batch(ref, "batch.json", BATCH)
    code, view, err = apply(ref, b, 1)
    expect(code == 0, "无故障参考运行退出 0")
    expect(sink_map(view) == EXPECTED_SINK, "参考 sink 事件键与摘要正确")
    expect(view["checkpoints"] == {"p": 3, "q": 2}, "参考检查点覆盖连续前缀（含 INVALID 越位）")
    expect(view["acks"] == {"p": 3, "q": 2}, "参考 acks 与检查点一致")
    expect(sorted(view["rejects"], key=lambda r: r["offset"]) == EXPECTED_REJECTS,
           "INVALID 行进入独立拒收")
    expect(view["outbox"] == {}, "参考运行后待发送记录已清理")

    for point in CRASH_POINTS:
        d = tempfile.mkdtemp(prefix="d07_%s_" % point, dir=str(HERE / "run"))
        root = Path(d)
        lease(root, "p", 1)
        lease(root, "q", 1)
        batch = write_batch(root, "batch.json", BATCH)

        code, view, err = apply(root, batch, 1, crash=point)
        expect(code == 97, "%s：首次 apply 在边界中断退出 97" % point)

        # 中断点的中间不变量
        if point == "after-outbox":
            expect(any(r["status"] == "UNCOMMITTED"
                       for r in view["outbox"].values()),
                   "%s：存在 UNCOMMITTED 待发送记录" % point)
            expect(sink_map(view) == {}, "%s：sink 尚未提交" % point)
        elif point == "after-sink":
            expect(any(r["status"] in ("COMMITTED", "CONFLICT")
                       for r in view["outbox"].values()),
                   "%s：sink 已提交但本地待确认" % point)
            expect(view["acks"] == {}, "%s：源确认尚未保存" % point)
        elif point == "after-checkpoint":
            expect(view["checkpoints"] != {}, "%s：检查点已持久化" % point)
            expect(view["acks"] == {}, "%s：ack 未超过检查点（尚未保存）" % point)
            expect(sink_map(view) == EXPECTED_SINK,
                   "%s：ACK 丢失前 sink 已完整" % point)
        elif point == "after-ack":
            expect(view["acks"] == view["checkpoints"],
                   "%s：ack 已保存且不超过检查点" % point)

        # 不带 --crash 重放同一批：协议必须自愈且幂等
        code2, view2, err2 = apply(root, batch, 1)
        expect(code2 == 0, "%s：重放同一批退出 0" % point)
        expect(sink_map(view2) == EXPECTED_SINK,
               "%s：重放后 sink 与无故障参考完全一致" % point)
        expect(view2["checkpoints"] == view["checkpoints"] or
               view2["checkpoints"] == {"p": 3, "q": 2},
               "%s：检查点只覆盖已持久化前缀" % point)
        expect(view2["checkpoints"] == {"p": 3, "q": 2},
               "%s：最终检查点与参考一致" % point)
        expect(view2["acks"] == {"p": 3, "q": 2},
               "%s：最终 acks 与参考一致" % point)
        expect(sorted(view2["rejects"], key=lambda r: r["offset"]) ==
               EXPECTED_REJECTS, "%s：拒收明细与参考一致" % point)
        expect(view2["outbox"] == {}, "%s：待发送记录最终清理" % point)

        # 再次重放：重复请求不得产生重复提交
        code3, view3, _ = apply(root, batch, 1)
        before = view2["stats"]
        after = view3["stats"]
        expect(code3 == 0 and sink_map(view3) == EXPECTED_SINK,
               "%s：二次重放 sink 仍无重复" % point)
        expect(after["sink_accepts"] == before["sink_accepts"],
               "%s：sink_accepts 不增加（幂等去重）" % point)
        expect(len(view3["sink"]) == len(EXPECTED_SINK),
               "%s：sink 条目数等于唯一事件键数" % point)

        # 独立接收端文件与 inspect 视图一致
        sink_disk = json.loads((root / "sink.json").read_text())
        expect(sorted(sink_disk) == sorted(EXPECTED_SINK),
               "%s：sink.json 独立持久化内容正确" % point)
        shutil.rmtree(d)
    shutil.rmtree(ref_dir)


# ---------------------------------------------------------------------------
# 测试 2：双消费者租约切换、围栏、UNKNOWN 恢复、缺口/回压/冲突
# ---------------------------------------------------------------------------

def test_lease_fencing_and_dual_consumer():
    print("test 2: 双消费者租约切换与旧实例围栏")
    d = tempfile.mkdtemp(prefix="d07_dual_", dir=str(HERE / "run"))
    root = Path(d)
    lease(root, "p", 1)

    b1 = write_batch(root, "b1.json", [
        {"partition": "p", "offset": 1, "key": "a", "payload": {"v": 1}},
        {"partition": "p", "offset": 2, "key": "b", "payload": {"v": 2}},
    ])
    code, view, _ = apply(root, b1, 1, crash="after-sink")
    expect(code == 97, "消费者 A 在 after-sink 中断（ACK 丢失）")
    expect(sink_map(view) != {}, "sink 已有提交但检查点未推进")
    expect(any(r["status"] == "COMMITTED" for r in view["outbox"].values()),
           "中断后存在已提交待确认记录")

    # 旧实例 A 暂停期间，租约切换给消费者 B（代次 +1）
    code, _, _ = lease(root, "p", 2)
    expect(code == 0, "租约代次单调递增到 2")

    # 旧实例 A 苏醒后用旧代次重放：必须被围栏拒绝，退出 3，无副作用
    before = json.loads((root / "state.json").read_text())
    code, view, err = apply(root, b1, 1)
    after = json.loads((root / "state.json").read_text())
    expect(code == 3, "旧代次 apply 被围栏拒绝退出 3")
    expect(before == after, "围栏拒绝不推进任何状态")
    expect("lease fence" in err, "围栏原因写入 stderr")

    # 同代次重复 lease 无副作用；倒退代次也退出 3
    code, _, _ = lease(root, "p", 2)
    expect(code == 0, "重复同代 lease 无副作用")
    code, _, err = lease(root, "p", 1)
    expect(code == 3, "倒退租约代次退出 3 且无副作用")

    # 消费者 B 用新代次重放缓存消息，恢复 UNKNOWN 并完成协议
    code, view, _ = apply(root, b1, 2)
    expect(code == 0, "新消费者代次 2 重放成功")
    expect(view["checkpoints"] == {"p": 2}, "检查点只覆盖已持久化前缀 1-2")
    expect(view["acks"] == {"p": 2}, "ack 跟随检查点")
    expect(sink_map(view) == {"p#1": sha256({"v": 1}),
                              "p#2": sha256({"v": 2})},
           "UNKNOWN 恢复未产生重复提交")
    expect(view["stats"]["sink_accepts"] == 2, "sink 仅两次真实提交")
    expect(view["outbox"] == {}, "恢复后待发送记录清理")
    shutil.rmtree(d)


def test_gap_watermark_and_invalid_replay():
    print("测试 3: 缺口缓存、连续前缀、水位与 INVALID 独立审计")
    d = tempfile.mkdtemp(prefix="d07_gap_", dir=str(HERE / "run"))
    root = Path(d)
    lease(root, "p", 1)

    # offset 1/2 有效，offset 3 缺失，offset 4/5 缓存，offset 6 格式错误
    gapped = write_batch(root, "gap.json", [
        {"partition": "p", "offset": 1, "key": "a", "payload": 1},
        {"partition": "p", "offset": 2, "key": "b", "payload": 2},
        {"partition": "p", "offset": 4, "key": "d", "payload": 4},
        {"partition": "p", "offset": 5, "key": "e", "payload": 5},
        {"partition": "p", "offset": 6, "key": "f", "invalid": True},
    ])
    code, view, _ = apply(root, gapped, 1)
    expect(code == 0, "含缺口批次正常退出")
    expect(view["checkpoints"] == {"p": 2}, "检查点只到连续前缀 2，不越过缺口")
    expect(view["acks"] == {"p": 2}, "ack 不超过检查点")
    buffered = {(r["partition"], r["offset"]) for r in view["buffered"]}
    expect(buffered == {("p", 4), ("p", 5)}, "缺口后有效行被缓存")
    expect(view["watermarks"] == {"p": 6}, "水位记录观察到的最高位点 6")
    expect(view["rejects"] == [{"partition": "p", "offset": 6,
                                "reason": "INVALID"}],
           "缺口后的 INVALID 行独立审计（不阻塞，可越位）")

    # 补上缺口 offset 3，缓存消息按顺序契约重放
    fill = write_batch(root, "fill.json", [
        {"partition": "p", "offset": 3, "key": "c", "payload": 3},
    ])
    code, view, _ = apply(root, fill, 1)
    expect(code == 0, "补齐缺口后退出 0")
    expect(view["checkpoints"] == {"p": 6}, "连续前缀推进到 6（缓存按序重放）")
    expect(view["buffered"] == [], "缓存全部排空")
    expect(sink_map(view) == {"p#1": sha256(1), "p#2": sha256(2),
                              "p#3": sha256(3), "p#4": sha256(4),
                              "p#5": sha256(5)},
           "重放有效行的事件键与摘要完整且有序")
    # 顺序契约：sink 落盘顺序即 offset 顺序
    keys = [item["key"] for item in view["sink"]]
    expect(keys == ["p#1", "p#2", "p#3", "p#4", "p#5"],
           "sink 条目按分区位点顺序排列")
    shutil.rmtree(d)


def test_digest_conflict():
    print("测试 4: 同事件键重写导致摘要冲突，冲突独立记录且不覆盖 sink")
    d = tempfile.mkdtemp(prefix="d07_conf_", dir=str(HERE / "run"))
    root = Path(d)
    lease(root, "p", 1)
    first = write_batch(root, "first.json", [
        {"partition": "p", "offset": 1, "key": "a", "payload": {"v": 1}},
        {"partition": "p", "offset": 2, "key": "b", "payload": {"v": 2}},
    ])
    code, view, _ = apply(root, first, 1)
    expect(code == 0 and view["checkpoints"] == {"p": 2}, "首批提交完成")
    original_digest = sink_map(view)["p#1"]

    # 采集器重写同一事件（相同稳定事件键 p#1），payload 摘要不同
    lease(root, "p", 2)
    rewritten = write_batch(root, "rewrite.json", [
        {"partition": "p", "offset": 1, "key": "a", "payload": {"v": 999}},
        {"partition": "p", "offset": 2, "key": "b", "payload": {"v": 2}},
    ])
    code, view, _ = apply(root, rewritten, 2)
    expect(code == 0, "冲突批次仍正常退出（冲突被拒收而非崩溃）")
    expect(sink_map(view)["p#1"] == original_digest,
           "摘要冲突不覆盖接收端原值")
    expect(sink_map(view)["p#2"] == sha256({"v": 2}),
           "同摘要重放无副作用，其他事件不受影响")
    expect({"partition": "p", "offset": 1,
            "reason": "DIGEST_CONFLICT"} in view["rejects"],
           "摘要冲突进入独立拒收记录")
    expect(view["stats"]["digest_conflicts"] >= 1, "统计记录冲突次数")
    shutil.rmtree(d)


def test_backpressure():
    print("测试 5: 缺口缓存超水位触发回压并给出失败明细")
    d = tempfile.mkdtemp(prefix="d07_bp_", dir=str(HERE / "run"))
    root = Path(d)
    lease(root, "p", 1)
    # 制造大缺口：offset 1 后直接给到 70+，缓存超过容量 64
    events = [{"partition": "p", "offset": 1, "key": "a", "payload": 1}]
    events += [{"partition": "p", "offset": off, "key": "k%d" % off,
                "payload": off} for off in range(10, 10 + 70)]
    big = write_batch(root, "big.json", events)
    code, view, err = apply(root, big, 1)
    expect(code == 4, "超水位回压退出 4")
    expect("BACKPRESSURE" in err, "stderr 给出回压失败明细")
    expect(view["checkpoints"] == {"p": 1}, "回压前连续前缀仍已确认")
    detail = json.loads(err.strip().split("\n")[-1])
    expect(detail["capacity"] == 64 and detail["buffered"] > 64,
           "失败明细包含容量与缓存量")
    shutil.rmtree(d)


def test_unknown_recovery_without_input():
    print("测试 6: after-sink 崩溃后无输入重启，UNKNOWN 自动收敛")
    d = tempfile.mkdtemp(prefix="d07_rec_", dir=str(HERE / "run"))
    root = Path(d)
    lease(root, "p", 1)
    b = write_batch(root, "b.json", [
        {"partition": "p", "offset": 1, "key": "a", "payload": {"z": 1}},
        {"partition": "p", "offset": 2, "key": "b", "payload": {"z": 2}},
    ])
    code, view, _ = apply(root, b, 1, crash="after-sink")
    expect(code == 97, "模拟 after-sink 崩溃")
    statuses = {r["event_key"]: r["status"] for r in view["outbox"].values()}
    expect("p#1" in statuses, "崩溃后待发送记录保留在磁盘")

    # 进程重启（全新 Store 实例），重放同一输入走恢复路径
    code, view, _ = apply(root, b, 1)
    expect(code == 0, "重启重放完成")
    expect(view["checkpoints"] == {"p": 2}, "UNKNOWN 经查 sink 收敛后推进检查点")
    expect(view["stats"]["sink_accepts"] == 2,
           "恢复不重复提交（已在 sink 的事件被识别）")
    shutil.rmtree(d)


def test_backpressure_recovery():
    print("测试 7: 回压后补齐缺口，连续缓存被冲刷且不丢顺序")
    d = tempfile.mkdtemp(prefix="d07_bpr_", dir=str(HERE / "run"))
    root = Path(d)
    lease(root, "p", 1)
    events = [{"partition": "p", "offset": 1, "key": "a", "payload": 1}]
    events += [{"partition": "p", "offset": off, "key": "k%d" % off,
                "payload": off} for off in range(10, 10 + 70)]
    big = write_batch(root, "big.json", events)
    code, view, err = apply(root, big, 1)
    expect(code == 4, "首次回压退出 4")
    expect(view["checkpoints"] == {"p": 1}, "回压前前缀 1 已确认")
    expect(len(view["buffered"]) == 70, "70 条缺口后行全部在缓存")

    # 补齐缺口 offset 2..9，连续后缓存应被冲刷，回压解除
    fill = write_batch(root, "fill.json", [
        {"partition": "p", "offset": off, "key": "f%d" % off, "payload": off}
        for off in range(2, 10)])
    code, view, _ = apply(root, fill, 1)
    expect(code == 0, "补齐缺口后退出 0")
    expect(view["buffered"] == [], "缓存全部排空，回压解除")
    expect(view["checkpoints"] == {"p": 79}, "连续前缀推进到最高位点 79")
    expect(len(view["sink"]) == 79, "79 个唯一事件键全部持久化")
    keys = [item["key"] for item in view["sink"]]
    expect(keys == ["p#%d" % i for i in range(1, 80)],
           "冲刷严格保持 offset 顺序契约")
    shutil.rmtree(d)


def main():
    (HERE / "run").mkdir(exist_ok=True)
    test_crash_points()
    test_lease_fencing_and_dual_consumer()
    test_gap_watermark_and_invalid_replay()
    test_digest_conflict()
    test_backpressure()
    test_unknown_recovery_without_input()
    test_backpressure_recovery()
    print("\n全部验收测试通过。")


if __name__ == "__main__":
    main()