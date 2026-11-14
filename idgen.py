#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
时钟回拨安全的 Snowflake 风格 ID 生成器。

ID 布局（与脚手架契约保持一致）:
    id = logical_ts_ms * 2^12 + seq          # seq in [0, 4095]
其中 logical_ts_ms 是“逻辑时间位”，永远单调不减；物理时钟回拨时：
  * 回拨幅度 <= max_backward_ms : 等待策略，等待物理钟追平 last_ts；
  * 回拨幅度 >  max_backward_ms : 漂移位接管，drift_offset 接管时间位，
                                  逻辑时钟继续向前（漂移策略），
                                  直到物理钟追平后恢复。

逐 ID 来源标记 src:
    normal : 常规分配
    wait   : 因时钟回拨等待 / 序号耗尽等待下一毫秒而产生
    drift  : 产生于漂移位接管区间（meta.drift == 1）

CLI:
    python3 idgen.py req.json output.json
    req.json:
      {"clock": [0, 1, 2, 1.5, 1.4, 50, 49, 100],   # 脚本时钟，单位秒
       "batch": 2,                                   # 每个时钟观测点取几个 ID
       "state": "state.json",                        # 持久化文件（重启校验）
       "max_backward_ms": 500}                       # 回拨阈值，默认 500ms
      实时模式: {"count": 1000, "state": "state.json"}
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("idgen")

SEQ_BITS = 12
SEQ_MAX = (1 << SEQ_BITS) - 1          # 4095
SEQ_MOD = 1 << SEQ_BITS                # 4096

# 告警类型（同时 logging 输出，并在结果 alerts 中结构化留存）
ALERT_BACKWARD_WAIT = "CLOCK_BACKWARD_WAIT"          # 回拨未超限 -> 等待
ALERT_SEQ_WAIT = "SEQUENCE_EXHAUSTED_WAIT"           # 序号耗尽 -> 等下一毫秒
ALERT_DRIFT_TAKEOVER = "DRIFT_TAKEOVER"              # 回拨超限 -> 漂移位接管
ALERT_DRIFT_EXTENDED = "DRIFT_EXTENDED"              # 漂移期内物理钟再次回退
ALERT_DRIFT_RECOVER = "DRIFT_RECOVER"                # 物理钟追平 -> 退出漂移
ALERT_STARTUP_WAIT = "STARTUP_BACKWARD_WAIT"         # 重启时钟落后 -> 等待
ALERT_STARTUP_DRIFT = "STARTUP_DRIFT_TAKEOVER"       # 重启落后超限 -> 启动即漂移


class IdResult:
    """单个 ID 的完整解码信息（逐 ID 时间位 / 序号 / 来源标记）。"""
    __slots__ = ("id", "ts", "seq", "src", "drift", "phys")

    def __init__(self, id_value: int, ts: int, seq: int, src: str,
                 drift: int, phys: int):
        self.id = id_value
        self.ts = ts          # 逻辑时间位（ms，单调不减）
        self.seq = seq        # 序号 0..4095
        self.src = src        # normal | wait | drift
        self.drift = drift    # 1 = 漂移位接管中
        self.phys = phys      # 当时观测到的物理时钟（ms）

    def meta(self) -> Dict[str, object]:
        return {"ts": self.ts, "seq": self.seq, "src": self.src,
                "drift": self.drift, "phys": self.phys}


class RealClock:
    """系统 wall-clock；wait_until 按真实时间睡眠等待。"""

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def wait_until(self, target_ms: int) -> None:
        while True:
            remain = target_ms - self.now_ms()
            if remain <= 0:
                return
            time.sleep(min(remain / 1000.0, 0.001))


class VirtualClock:
    """可注入回拨/突跳的脚本时钟；wait_until 直接把虚拟时间推进到目标，
    不做真实睡眠，使测试确定且快速。"""

    def __init__(self, start_ms: int = 0):
        self._t = int(start_ms)
        self.wait_count = 0
        self._lock = threading.Lock()

    def now_ms(self) -> int:
        return self._t

    def set_ms(self, t_ms: int) -> None:
        """注入一个时钟观测值（可以小于当前值，即 NTP 回拨）。"""
        with self._lock:
            self._t = int(t_ms)

    def jump(self, delta_ms: int) -> None:
        with self._lock:
            self._t += int(delta_ms)

    def wait_until(self, target_ms: int) -> None:
        with self._lock:
            if target_ms > self._t:
                self._t = target_ms
                self.wait_count += 1


def _load_state(path: Optional[str]) -> Optional[Dict[str, int]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("last_ts"), int):
            return None
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("状态文件损坏，按无状态启动: %s (%s)", path, exc)
        return None


def _atomic_save_state(path: str, state: Dict[str, object]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:  # 目录 fsync 为 best-effort
            os.fsync(os.open(directory, os.O_RDONLY))
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class SnowflakeGenerator:
    """线程安全的单调 ID 生成器。

    不变量（任何并发度 / 批大小下成立）：
      1. id 严格按生成序单调递增且全局唯一；
      2. 逻辑时间位 last_ts 单调不减，回拨期内新 ID 时间位 >= 回拨前；
      3. 同一毫秒序号 0..4095，耗尽则等待下一毫秒；
      4. 回拨超阈值由漂移位接管，全程告警。
    """

    def __init__(self,
                 state_path: Optional[str] = None,
                 max_backward_ms: int = 500,
                 clock: Optional[object] = None,
                 listener: Optional[Callable[[IdResult], None]] = None,
                 persist_every: int = 256):
        self.state_path = state_path
        self.max_backward_ms = int(max_backward_ms)
        self.clock = clock or RealClock()
        self._listener = listener
        self._persist_every = max(1, int(persist_every))

        self._lock = threading.Lock()
        self.last_ts = 0          # 上一个 ID 的逻辑时间位
        self.seq = -1             # 下一个同毫秒 ID 将取 seq+1
        self.in_drift = False     # 是否处于漂移位接管区间
        self.drift_offset = 0     # logical = phys + drift_offset
        self.alerts: List[Dict[str, object]] = []
        self._since_persist = 0
        self._closed = False
        self._waited = False      # 本次分配是否经历过等待（用于 src=wait 标记）

        self._startup_check()

    # ------------------------------------------------------------------ #
    # 持久化与启动校验
    # ------------------------------------------------------------------ #
    def _startup_check(self) -> None:
        state = _load_state(self.state_path)
        if not state:
            return
        persisted_ts = int(state["last_ts"])
        persisted_seq = int(state.get("last_seq", -1))
        phys = self.clock.now_ms()
        self.last_ts = persisted_ts
        self.seq = persisted_seq

        if phys < persisted_ts:
            delta = persisted_ts - phys
            if delta <= self.max_backward_ms:
                # 重启后小幅落后：等待物理钟追平持久化时间位，绝不发更早的 ID
                self._alert(ALERT_STARTUP_WAIT, delta=delta,
                            persisted_ts=persisted_ts, phys=phys,
                            level=logging.WARNING)
                self.clock.wait_until(persisted_ts)
            else:
                # 重启后大幅落后：漂移位直接接管，逻辑时间位从持久化值继续
                self.in_drift = True
                self.drift_offset = persisted_ts - phys
                self._alert(ALERT_STARTUP_DRIFT, delta=delta,
                            persisted_ts=persisted_ts, phys=phys,
                            offset=self.drift_offset, level=logging.ERROR)
        logger.info("启动校验完成 last_ts=%s seq=%s drift=%s",
                    self.last_ts, self.seq, self.in_drift)

    def _persist_locked(self) -> None:
        if not self.state_path:
            return
        _atomic_save_state(self.state_path, {
            "version": 1,
            "last_ts": self.last_ts,
            "last_seq": self.seq,
            "last_id": self.last_ts * SEQ_MOD + max(self.seq, 0),
            "in_drift": self.in_drift,
            "drift_offset": self.drift_offset,
            "updated_phys_ms": self.clock.now_ms(),
            "alerts_total": len(self.alerts),
        })
        self._since_persist = 0

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._persist_locked()
            self._closed = True

    def __enter__(self) -> "SnowflakeGenerator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 告警
    # ------------------------------------------------------------------ #
    def _alert(self, kind: str, level: int = logging.WARNING, **fields) -> None:
        record = {"type": kind, "at_phys_ms": self.clock.now_ms(),
                  "last_ts": self.last_ts}
        record.update(fields)
        self.alerts.append(record)
        logger.log(level, "ALERT %s %s", kind, record)
        # 时钟异常事件立即落盘，保证重启后仍可追溯且不回退
        if kind in (ALERT_DRIFT_TAKEOVER, ALERT_STARTUP_DRIFT,
                    ALERT_BACKWARD_WAIT, ALERT_STARTUP_WAIT):
            self._persist_locked()

    # ------------------------------------------------------------------ #
    # 核心分配
    # ------------------------------------------------------------------ #
    def next_id_detail(self) -> IdResult:
        with self._lock:
            if self._closed:
                raise RuntimeError("generator is closed")
            self._waited = False
            prev_id = (self.last_ts * SEQ_MOD + self.seq) if self.seq >= 0 else -1

            while True:
                src = "normal"
                phys = self.clock.now_ms()

                # ---------- 漂移位接管区间 ---------- #
                if self.in_drift:
                    if phys >= self.last_ts:
                        # 物理钟已追平逻辑时间位：退出漂移，交还时间位
                        self.in_drift = False
                        self.drift_offset = 0
                        logical = phys
                        self._alert(ALERT_DRIFT_RECOVER,
                                    phys=phys, logical=logical,
                                    level=logging.WARNING)
                        src = "normal"
                    else:
                        candidate = phys + self.drift_offset
                        if candidate <= self.last_ts:
                            # 漂移期内物理钟再次回退：加大偏移，继续接管
                            new_offset = self.last_ts - phys
                            if new_offset != self.drift_offset:
                                self._alert(ALERT_DRIFT_EXTENDED,
                                            old_offset=self.drift_offset,
                                            new_offset=new_offset, phys=phys,
                                            level=logging.WARNING)
                            self.drift_offset = new_offset
                            candidate = self.last_ts
                        logical = candidate
                        src = "drift"
                # ---------- 常规区间 ---------- #
                elif phys >= self.last_ts:
                    logical = phys
                else:
                    delta = self.last_ts - phys
                    if delta <= self.max_backward_ms:
                        # 回拨未超限：拒绝回退时间位，等待物理钟追平
                        self._alert(ALERT_BACKWARD_WAIT, delta=delta,
                                    phys=phys, level=logging.WARNING)
                        self.clock.wait_until(self.last_ts)
                        self._waited = True
                        # 醒来后重新采样时钟
                        continue
                    # 回拨超限：漂移位接管，逻辑时间位从 last_ts 继续，绝不回退
                    self.in_drift = True
                    self.drift_offset = delta
                    logical = self.last_ts
                    self._alert(ALERT_DRIFT_TAKEOVER, delta=delta, phys=phys,
                                offset=self.drift_offset, level=logging.ERROR)
                    src = "drift"

                # ---------- 序号分配 ---------- #
                if logical == self.last_ts:
                    if self.seq >= SEQ_MAX:
                        if self.in_drift:
                            # 漂移期内不能依赖物理钟等待：逻辑时间位直接走 1ms
                            logical = self.last_ts + 1
                            self.drift_offset = logical - self.clock.now_ms()
                            self.seq = 0
                            src = "drift"
                        else:
                            # 序号耗尽：等待策略，阻塞到下一毫秒后重新采样
                            self._alert(ALERT_SEQ_WAIT,
                                        wait_until=self.last_ts + 1,
                                        level=logging.WARNING)
                            self.clock.wait_until(self.last_ts + 1)
                            self._waited = True
                            continue
                    else:
                        self.seq += 1
                else:
                    # 防御：逻辑时间位绝不允许变小
                    if logical < self.last_ts:
                        logger.error("内部异常 logical(%s)<last_ts(%s)，强制夹取",
                                     logical, self.last_ts)
                        logical = self.last_ts
                    self.seq = 0

                # 等待来源只在常规区间生效；漂移接管/跳毫秒保持 drift
                if self._waited and src == "normal" and not self.in_drift:
                    src = "wait"
                drift_flag = 1 if self.in_drift else 0
                value = logical * SEQ_MOD + self.seq
                assert value > prev_id, "ID 非单调: %s -> %s" % (prev_id, value)
                self.last_ts = logical

                self._since_persist += 1
                if self._since_persist >= self._persist_every:
                    self._persist_locked()

                result = IdResult(value, logical, self.seq, src,
                                  drift_flag, phys)
                if self._listener:
                    self._listener(result)
                return result

    def next_id(self) -> int:
        return self.next_id_detail().id


# ---------------------------------------------------------------------- #
# 请求执行 / CLI
# ---------------------------------------------------------------------- #
def run_request(req: Dict[str, object]) -> Dict[str, object]:
    state_path = req.get("state")
    threshold = int(req.get("max_backward_ms", 500))
    batch = max(1, int(req.get("batch", 1)))

    ids: List[int] = []
    meta: List[Dict[str, object]] = []
    by_src = {"normal": 0, "wait": 0, "drift": 0}
    started = time.time()

    if "clock" in req:
        # 脚本时钟模式：clock 为秒序列，可注入回拨与突跳
        clock = VirtualClock()
        samples = [int(round(float(t) * 1000)) for t in req["clock"]]
        gen = SnowflakeGenerator(state_path, max_backward_ms=threshold,
                                 clock=clock)
        try:
            for t_ms in samples:
                clock.set_ms(t_ms)  # 注入观测点（允许回拨）
                for _ in range(batch):
                    r = gen.next_id_detail()
                    ids.append(r.id)
                    meta.append(r.meta())
                    by_src[r.src] += 1
        finally:
            alerts = list(gen.alerts)
            gen.close()
    else:
        # 实时模式
        count = int(req.get("count", 1))
        gen = SnowflakeGenerator(state_path, max_backward_ms=threshold)
        try:
            for _ in range(count):
                r = gen.next_id_detail()
                ids.append(r.id)
                meta.append(r.meta())
                by_src[r.src] += 1
        finally:
            alerts = list(gen.alerts)
            gen.close()

    return {
        "ids": ids,
        "meta": meta,
        "alerts": alerts,
        "stats": {"total": len(ids), "by_src": by_src,
                  "alert_count": len(alerts),
                  "elapsed_ms": round((time.time() - started) * 1000, 3)},
    }


def main(argv: List[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if len(argv) != 3:
        print("usage: python3 idgen.py req.json output.json", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="utf-8") as f:
        req = json.load(f)
    result = run_request(req)
    with open(argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
