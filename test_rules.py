"""README 规则的单元测试：回拨、重复 tick、乱序、缺标、外推 null。"""
import sys
sys.path.insert(0, ".")
from clockcal import model


def make_sync(seq0, tick0, ref0, n=8, step=10.0):
    return [dict(seq=seq0 + i, tick=tick0 + step * i,
                 reference=ref0 + step * i) for i in range(n)]


def test_regression_creates_epoch():
    sync = make_sync(0, 10.0, 0.0)
    # 重启后本地时钟回到 5 开始，seq 继续递增
    sync += make_sync(8, 5.0, 80.0)
    probes = [dict(seq=3, tick=40.0, id="a"),
              dict(seq=9, tick=15.0, id="b")]
    out = model.align(dict(devices=dict(dev=dict(sync=sync, probes=probes))))
    epochs = sorted(set(m["epoch"] for m in out["mapped"]))
    assert epochs == [0, 1], epochs
    assert out["anomalies"]["dev"]["regressions"] == 1
    # 两个 epoch 都应校准且映射到正确的全局 R
    ref = dict((m["id"], m["reference"]) for m in out["mapped"])
    assert abs(ref["a"] - 30.0) < 0.01
    assert abs(ref["b"] - 90.0) < 0.01
    print("test_regression_creates_epoch OK")


def test_long_gap_is_not_restart():
    # 单个 epoch 内有一个 120 s 的自然长间隔，但 tick 单调不减
    sync = make_sync(0, 0.0, 0.0, n=4)
    sync += make_sync(4, 240.0, 120.0, n=4)  # 从 tick 240 继续
    probes = [dict(seq=5, tick=250.0, id="x")]
    out = model.align(dict(devices=dict(dev=dict(sync=sync, probes=probes))))
    assert all(m["epoch"] == 0 for m in out["mapped"])
    assert out["anomalies"]["dev"]["regressions"] == 0
    assert out["mapped"][0]["reference"] is not None
    print("test_long_gap_is_not_restart OK")


def test_duplicate_tick_and_reorder():
    sync = make_sync(0, 0.0, 0.0)
    sync.append(dict(seq=3, tick=30.001, reference=30.0))  # 近重复 tick
    probes = [dict(seq=2, tick=25.0, id="p")]
    out = model.align(dict(devices=dict(dev=dict(sync=sync, probes=probes))))
    assert out["anomalies"]["dev"]["dup_tick_groups"] >= 1
    assert out["mapped"][0]["reference"] is not None
    print("test_duplicate_tick_and_reorder OK")


def test_mismatch_rejected():
    sync = make_sync(0, 0.0, 0.0)
    # 注入两个错配标记（误差 0.2 s 以上）
    sync.append(dict(seq=20, tick=200.0, reference=200.3))
    sync.append(dict(seq=21, tick=210.0, reference=209.7))
    out = model.align(dict(devices=dict(
        dev=dict(sync=sync, probes=[dict(seq=1, tick=10.0, id="q")]))))
    ep = out["epochs"][0]
    assert ep["rejected_marks"] >= 1 or ep["anchor_count"] >= 5
    assert ep["residual_ms"] is not None and ep["residual_ms"] < 5.0
    print("test_mismatch_rejected OK")


def test_extrapolation_null():
    sync = make_sync(0, 100.0, 100.0, n=6)  # 覆盖 tick 100..150
    # 远超 30 s 外推上限的 probe 必须为 null；区间内的正常映射
    probes = [dict(seq=2, tick=300.0, id="far"),
              dict(seq=1, tick=105.0, id="near")]
    out = model.align(dict(devices=dict(dev=dict(sync=sync, probes=probes))))
    ref = dict((m["id"], m["reference"]) for m in out["mapped"])
    assert ref["far"] is None
    assert ref["near"] is not None
    print("test_extrapolation_null OK")


def test_anchorless_epoch_null():
    # 第二个 epoch 没有任何 sync，不能沿用旧模型
    sync = make_sync(0, 0.0, 0.0)
    sync += [dict(seq=8, tick=5.0, reference=80.0)]  # 仅 1 个，不足 5 个
    probes = [dict(seq=9, tick=15.0, id="z")]
    out = model.align(dict(devices=dict(dev=dict(sync=sync, probes=probes))))
    z = next(m for m in out["mapped"] if m["id"] == "z")
    assert z["epoch"] == 1 and z["reference"] is None
    ep1 = next(e for e in out["epochs"] if e["epoch"] == 1)
    assert ep1["slope"] is None and ep1["coverage"] is None
    print("test_anchorless_epoch_null OK")


def test_rate_change_segmentation():
    # 单 epoch 内一次连续速率变化（不跳变），应分成两个 segment
    pts = []
    s1, s2, knot = 1.0, 1.0004, 500.0
    tk = knot / s1
    off2 = knot - s2 * tk
    for R in range(30, 1000, 30):
        t = R / s1 if R <= knot else (R - off2) / s2
        pts.append((t, R))
    res = model.fit_epoch([dict(seq=i, tick=t, reference=R)
                           for i, (t, R) in enumerate(pts)])
    assert len(res["segments"]) == 2, len(res["segments"])
    assert abs(res["segments"][0]["slope"] - s1) < 1e-4
    assert abs(res["segments"][1]["slope"] - s2) < 1e-4
    print("test_rate_change_segmentation OK")


if __name__ == "__main__":
    test_regression_creates_epoch()
    test_long_gap_is_not_restart()
    test_duplicate_tick_and_reorder()
    test_mismatch_rejected()
    test_extrapolation_null()
    test_anchorless_epoch_null()
    test_rate_change_segmentation()
    print("全部规则单元测试通过。")
