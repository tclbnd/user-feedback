#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时钟回拨安全的单调 ID 生成器（Snowflake 变体，纯标准库，Python 3.9+）。

ID 结构
    id = (ts_ms << 12) | seq          ts_ms 为逻辑时间位, seq 属于 [0, 4095]

三条硬不变量
    1. 严格单调: 后发放的 id 一定大于先发放的 id (全部状态迁移在同一把锁内)。
    2. 全局唯一: 进程内唯一; 经持久化状态跨进程重启后也不重复。
    3. 时间位不倒退: 任意时刻的 ts_ms 都 >= 已发放过的最大 ts_ms。

回拨三级策略 (对应实现要求 1)
    A. 容忍区回拨 (delta <= max_rollback_ms, 默认 500ms):
       "拒绝回退时间位" —— 逻辑时间钉在 last_ts 上继续消耗序号 (src="wait"),
       等物理时钟自己追上来; 每个回拨区间只告警一次 clock_rollback_hold。
    B. 序号耗尽: 同一逻辑毫秒的 4096 个序号用完后阻塞等待下一毫秒 (src="wait"),
       告警 seq_exhausted_wait; 超过 max_wait_ms 仍无进展则强制推进 1ms,
       告警 wait_timeout_forced_advance (仍保持单调/唯一)。
    C. 回拨超过容忍区: 漂移位接管 —— 令 offset = last_ts + 1 - wall,
       逻辑时间 = wall + offset 继续前进 (src="drift"), 告警
       rollback_exceeded_drift_engaged; 再次回拨则延长偏移, 告警 drift_extended;
       物理时钟追平后自动退役偏移, 告警 drift_retired。

持久化 (对应实现要求 2)
    状态文件为带版本 + sha256 校验的 JSON, 采用 tmp + fsync + os.replace 原子写入。
    每进入一个新毫秒先落盘水位线 (closed=false, seq=4095), 崩溃后从 last_ts+1 续发;
    正常 close() 落盘精确的 (last_ts, seq, last_id), 重启后可无缝续发且
    "首 ID 时间位 >= 关闭前最后值"。校验失败 -> StateError(fail-closed),
    绝不静默放宽单调性; 旧版无校验状态按水位线语义恢复并告警。

调用方式
    python3 idgen.py req.json out.json
    req: {"clock":[0,1,2,1.5,1.4,50,49,100],  # 秒; 脚本化时钟, 缺省用系统时钟
          "batch":2, "state":"state.json", "clock_id":"node-a",
          "max_rollback_ms":500, "max_wait_ms":10, "count":8, "meta":true}
    out: {"ids":[...], "meta":[{index,id,ts,seq,src,wall,drift}...],
          "alerts":[...], "stats":{...}, "state":{...}}

批大小/并发无关性 (对应实现要求 3)
    每个 ID 恰好读取一次注入时钟, 且状态迁移组成一个原子步骤;
    take(n) 与 n 次 take(1) 走同一条状态机, 因此输出与批大小、线程数无关。
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import time

STATE_VERSION = 1
SEQ_BITS = 12
MAX_SEQ = (1 << SEQ_BITS) - 1
MAX_ALERTS_KEPT = 4096


class IdGenError(Exception):
    """生成器错误基类。"""


class StateError(IdGenError):
    """持久化状态损坏/不可用; 为避免重发更早 ID, 调用方应视为致命错误。"""


class GeneratorClosed(IdGenError):
    """在 close() 之后继续取号。"""


# --------------------------------------------------------------------------
# 时钟
# --------------------------------------------------------------------------
class SystemClock:
    """墙钟毫秒。NTP 校时可能让它回拨，这正是本模块要防住的输入。"""

    def now_ms(self):
        return time.time_ns() // 1_000_000

    def sleep(self, ms):
        # 真实环境按毫秒睡眠，醒来后重新读钟。
        if ms > 0:
            time.sleep(ms / 1000.0)


class ManualClock:
    """可编程时钟：注入回拨/突跳，或演练跨进程重启。

    sleep() 以“虚拟时间”前进，使等待路径在测试中瞬时完成且可复现。
    """

    def __init__(self, now_ms=0):
        self._ms = int(now_ms)
        self._lock = threading.Lock()

    def now_ms(self):
        with self._lock:
            return self._ms

    def set(self, ms):
        with self._lock:
            self._ms = int(ms)
        return self

    def advance(self, ms=1):
        with self._lock:
            self._ms += int(ms)
        return self

    def sleep(self, ms):
        with self._lock:
            self._ms += max(0, int(ms))


class VirtualClock(ManualClock):
    """确定性压测时钟：每 calls_per_ms 次读取自动前进 1ms。

    calls_per_ms=0 表示只按显式 set/advance/sleep 前进。
    """

    def __init__(self, now_ms=0, calls_per_ms=0):
        ManualClock.__init__(self, now_ms)
        self._per_ms = max(0, int(calls_per_ms))
        self._calls = 0
        self._clock_lock = threading.Lock()

    def now_ms(self):
        with self._clock_lock:
            value = ManualClock.now_ms(self)
            if self._per_ms:
                self._calls += 1
                if self._calls >= self._per_ms:
                    self._calls = 0
                    ManualClock.advance(self, 1)
                    value = ManualClock.now_ms(self)
            return value


# --------------------------------------------------------------------------
# 状态文件 I/O
# --------------------------------------------------------------------------
def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _checksum(payload):
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _fsync_dir(path):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class StateStore:
    """原子、可校验、带水位线的状态存储。

    磁盘格式: {"version":1, "state":{...}, "checksum": sha256(canonical(state))}
    写入走 tmp + fsync + os.replace, 断电时要么是旧文件要么是新文件。
    """

    def __init__(self, path, fsync=True):
        self.path = path
        self.fsync_enabled = bool(fsync)
        self._lock_path = path + ".lock"
        self._lock_file = None

    # ---- 进程间互斥 ----
    def acquire(self):
        """同一状态文件只允许一个活着的生成器, 否则重启语义失效。"""
        self._lock_file = open(self._lock_path, "a+")
        try:
            import fcntl
        except ImportError:              # 非 POSIX 平台退化为仅进程内保护
            return self
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise StateError(
                "状态文件已被另一个生成器进程占用: %s (%s)" % (self.path, exc))
        return self

    def release(self):
        if self._lock_file is not None:
            try:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self._lock_file.close()
            self._lock_file = None

    # ---- 读 ----
    def load(self):
        """返回 (state_dict, status)。status: missing|ok|legacy"""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return None, "missing"
        except (OSError, ValueError) as exc:
            raise StateError("状态文件无法解析: %s (%s)" % (self.path, exc))

        if not isinstance(raw, dict):
            raise StateError("状态文件结构非法: %s" % self.path)
        if "state" not in raw:
            # 旧版: 基线版写的是 {"last": ms}, 无校验和也无 close 标记
            if "last" in raw and isinstance(raw["last"], int):
                return _normalize_legacy(raw), "legacy"
            if not _valid_state_fields(raw):
                raise StateError("旧版状态文件字段非法: %s" % self.path)
            return _normalize_legacy(raw), "legacy"

        state = raw.get("state")
        if not isinstance(state, dict) or not _valid_state_fields(state):
            raise StateError("状态文件字段非法: %s" % self.path)
        if raw.get("version") != STATE_VERSION:
            raise StateError("状态文件版本不支持: %r (需要 %d)"
                             % (raw.get("version"), STATE_VERSION))
        if raw.get("checksum") != _checksum(state):
            raise StateError("状态文件校验和不匹配 (疑似截断/篡改): %s" % self.path)
        return dict(state), "ok"

    # ---- 写 ----
    def save(self, state):
        payload = {"version": STATE_VERSION, "state": state,
                   "checksum": _checksum(state)}
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".idgen-state.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                if self.fsync_enabled:
                    os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            if self.fsync_enabled:
                _fsync_dir(self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _valid_state_fields(state):
    if not all(isinstance(state.get(key), int)
               for key in ("last_ts", "seq", "last_id")):
        return False
    if not 0 <= state["seq"] <= MAX_SEQ:
        return False
    if state["last_ts"] < 0:
        # 空状态哨兵: 从未发放过任何 ID (last_ts=-1, last_id=-1)
        return state["last_ts"] == -1 and state["last_id"] == -1
    return state["last_id"] >= 0


def _normalize_legacy(state):
    """旧版 {last: ms} 或 {last_ts,seq} 统一成完整状态(按水位线语义恢复)。"""
    if "last_ts" not in state:
        last_ts = int(state.get("last", 0))
        seq = MAX_SEQ                    # 视为此前整毫秒已用尽 -> 从下一 ms 起
    else:
        last_ts = int(state["last_ts"])
        seq = int(state.get("seq", MAX_SEQ))
    return {
        "last_ts": last_ts,
        "seq": seq,
        "last_id": int(state.get("last_id", (last_ts << SEQ_BITS) + seq)),
        "drift": int(state.get("drift", 0)),
        "clock_id": state.get("clock_id"),
        "closed": bool(state.get("closed", False)),
    }


# --------------------------------------------------------------------------
# 生成器
# --------------------------------------------------------------------------
DEFAULT_MAX_ROLLBACK_MS = 500
DEFAULT_MAX_WAIT_MS = 10


def compose_id(ts_ms, seq):
    return (ts_ms << SEQ_BITS) | seq


class ClockRollbackIdGenerator:
    """单调、唯一、可跨进程重启续发的 ID 生成器。

    参数
        clock             需提供 now_ms(); 真实环境可另提供 sleep(ms)
        state_path        持久化路径; None 表示纯内存(不跨进程)
        clock_id          物理时钟标识; 与状态文件记录不一致时不继承旧下限
        max_rollback_ms   容忍区宽度, 超出即启用漂移位接管
        max_wait_ms       序号耗尽/回拨持有时, 单次等待上限(超限强制推进)
        fsync             是否对状态文件 fsync
    """

    def __init__(self, clock=None, state_path=None, clock_id=None,
                 max_rollback_ms=DEFAULT_MAX_ROLLBACK_MS,
                 max_wait_ms=DEFAULT_MAX_WAIT_MS, fsync=True):
        self.clock = clock if clock is not None else SystemClock()
        self.clock_id = clock_id
        self.max_rollback_ms = max(0, int(max_rollback_ms))
        self.max_wait_ms = max(0, int(max_wait_ms))
        if self.max_wait_ms <= 0:
            raise ValueError("max_wait_ms 必须为正, 否则序号耗尽无法取得下一毫秒")

        self._lock = threading.Lock()
        self._closed = False
        self._alerts = []
        self._alert_ids = set()

        self._store = None
        if state_path:
            self._store = StateStore(state_path, fsync=fsync)
            self._store.acquire()

        # 逻辑时钟状态
        self._last_ts = -1         # 已发放的最大时间位(-1 = 尚未发放任何 ID)
        self._seq = MAX_SEQ        # 该时间位内已发放的最大序号(-1 毫秒视作已满)
        self._last_id = -1
        self._drift = 0            # 漂移偏移: 逻辑时间 = wall + drift
        self._in_rollback = None   # 当前回拨区间锚点(wall), 用于告警去重
        self._persisted_watermark = -1   # 已落盘水位线(闭区间)
        self._durable_last_id = -1

        self.stats = {
            "issued": 0,
            "normal": 0,
            "wait": 0,
            "drift": 0,
            "wait_seq_exhausted": 0,
            "drift_extensions": 0,
            "forced_advance": 0,
            "max_step_wait_ms": 0,
        }

        try:
            self._restore()
        except BaseException:
            # 构造失败必须释放 flock, 否则整个进程生命周期内都占着状态文件
            if self._store is not None:
                self._store.release()
            raise

    # ---- 生命周期 ----
    def _restore(self):
        if self._store is None:
            return
        state, status = self._store.load()
        if status == "missing":
            return
        if status == "legacy":
            self._alert("legacy_state_adopted",
                        "旧版状态缺少校验与 close 标记, 按水位线语义保守恢复")
        if state is None:
            return

        stored_clock = state.get("clock_id")
        if (self.clock_id is not None and stored_clock is not None
                and stored_clock != self.clock_id):
            self._alert("clock_id_mismatch",
                        "状态文件属于另一个物理时钟 (%r != %r), 不继承其时间下限"
                        % (stored_clock, self.clock_id))
            return

        last_ts = int(state["last_ts"])
        seq = int(state["seq"])
        self._persisted_watermark = last_ts
        self._durable_last_id = int(state["last_id"])
        if state.get("closed"):
            # 正常关闭: 精确续发, 保证重启后首 ID > 关闭前 last_id
            self._last_ts = last_ts
            self._seq = seq
            self._last_id = max(self._durable_last_id, compose_id(last_ts, seq))
            # 关键: 已消费的中段毫秒必须重新变成写前水位线, 否则重启后
            # 再次崩溃会按 closed=true 的 seq 重放, 造成重复 ID。
            self._rearm_watermark()
        else:
            # 崩溃恢复: 水位线内可能已发放, 从下一毫秒起重新计数
            self._last_ts = last_ts
            self._seq = MAX_SEQ
            self._last_id = max(self._durable_last_id, compose_id(last_ts, MAX_SEQ))
            self._alert("unclean_shutdown_recovery",
                        "检测到未正常关闭的状态, 已从时间位 %d 的下一毫秒续发"
                        % last_ts)

    def _rearm_watermark(self):
        """把当前毫秒整体注册为已用尽, 使崩溃后从下一毫秒续发。"""
        if self._store is None or self._last_ts < 0:
            return
        self._persist(closed=False, seq=MAX_SEQ,
                      last_id=compose_id(self._last_ts, MAX_SEQ))
        self._persisted_watermark = self._last_ts
        self._durable_last_id = compose_id(self._last_ts, MAX_SEQ)

    def close(self):
        """落盘精确状态并释放跨进程锁; 幂等。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._persist(closed=True)
            if self._store is not None:
                self._store.release()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    @property
    def alerts(self):
        with self._lock:
            return list(self._alerts)

    def drain_alerts(self):
        with self._lock:
            alerts = list(self._alerts)
            self._alerts = []
            self._alert_ids = set()
            return alerts

    # 这些是"状态条件"而非"一次性事件": 整条生命周期内只需告警一次,
    # 否则持续停滞/持续回拨时会按 ID 数量制造告警风暴。
    ONCE_CODES = frozenset({
        "clock_id_mismatch",
        "legacy_state_adopted",
        "unclean_shutdown_recovery",
        "seq_exhausted_wait",
        "drift_extended",
        "wait_timeout_forced_advance",
    })

    # ---- 告警 ----
    def _alert(self, code, message, **detail):
        """去重: 条件类(code 在 ONCE_CODES)每码一次, 事件类按 (code,message) 一次。"""
        key = (code, "") if code in self.ONCE_CODES else (code, message)
        if key in self._alert_ids:
            return
        self._alert_ids.add(key)
        entry = {"code": code, "message": message}
        if detail:
            entry["detail"] = detail
        self._alerts.append(entry)
        if len(self._alerts) > MAX_ALERTS_KEPT:      # 防御性上限
            self._alerts = self._alerts[-MAX_ALERTS_KEPT:]

    # ---- 持久化 ----
    def _persist(self, closed=False, seq=None, last_id=None):
        if self._store is None:
            return
        state = {
            "last_ts": self._last_ts,
            "seq": self._seq if seq is None else seq,
            "last_id": self._last_id if last_id is None else last_id,
            "drift": self._drift,
            "clock_id": self.clock_id,
            "closed": bool(closed),
        }
        self._store.save(state)
        if closed:
            self._persisted_watermark = self._last_ts
            self._durable_last_id = state["last_id"]

    # ---- 状态机: 调用方必须持 self._lock ----------------------------------
    def _begin_new_ms(self, ts):
        """进入新毫秒: 先落盘水位线, 崩溃后才能从下一毫秒安全续发。"""
        self._last_ts = ts
        self._seq = 0
        if self._store is not None and ts > self._persisted_watermark:
            self._last_id = compose_id(ts, MAX_SEQ)
            self._persist(closed=False, seq=MAX_SEQ)
            self._persisted_watermark = ts
            self._durable_last_id = self._last_id

    def _emit(self, ts, seq, src, wall):
        ident = compose_id(ts, seq)
        self._last_id = ident
        order = self.stats["issued"]        # 全局发放序(锁内递增, 唯一)
        self.stats["issued"] += 1
        self.stats[src] = self.stats.get(src, 0) + 1
        return {"order": order, "id": ident, "ts": ts, "seq": seq,
                "src": src, "wall": wall, "drift": self._drift}

    def _sleep_until_progress(self):
        """等待物理时钟越过逻辑时间位; 返回 (wall, 是否已越过)。"""
        slept = 0
        while slept < self.max_wait_ms:
            step = min(1, self.max_wait_ms - slept)
            self._sleep(step)
            slept += step
            self.stats["max_step_wait_ms"] = max(self.stats["max_step_wait_ms"], slept)
            wall = self.clock.now_ms()
            if wall + self._drift > self._last_ts:
                return wall, True
        return self.clock.now_ms(), False

    def _acquire(self):
        """单步状态迁移: 决定本 ID 的 (ts, seq, src)。所有策略在此汇合。"""
        wall = self.clock.now_ms()

        # ---- C. 漂移位已激活 ---------------------------------------------
        if self._drift:
            if wall >= self._last_ts:
                # 物理时钟追平逻辑前沿: 偏移退役
                self._drift = 0
                self._alert("drift_retired",
                            "物理时钟已追平逻辑时间位 %d, 漂移位退役" % self._last_ts)
                if wall > self._last_ts:
                    self._begin_new_ms(wall)
                    return self._emit(self._last_ts, 0, "normal", wall)
                # wall == last_ts: 落到下方同毫秒序号逻辑
            elif wall + self._drift > self._last_ts:
                self._begin_new_ms(wall + self._drift)
                return self._emit(self._last_ts, 0, "drift", wall)
            else:
                # 漂移期内物理时钟再次倒退: 延长偏移以保持前进
                new_ts = self._last_ts + 1
                self._drift = new_ts - wall      # 先更新偏移, 再落盘水位线
                self._begin_new_ms(new_ts)
                self.stats["drift_extensions"] += 1
                self._alert("drift_extended",
                            "漂移位期内物理时钟再次落后, 已延长偏移",
                            drift=self._drift)
                return self._emit(self._last_ts, 0, "drift", wall)

        # ---- A. 物理时间领先: 正常路径 -----------------------------------
        elif wall > self._last_ts:
            self._in_rollback = None
            self._begin_new_ms(wall)
            return self._emit(self._last_ts, 0, "normal", wall)

        # ---- B. 回拨 / 停滞: 拒绝回退时间位 ------------------------------
        if wall < self._last_ts:
            delta = self._last_ts - wall
            if delta > self.max_rollback_ms:
                # C. 超出容忍区 -> 漂移位接管(不再原地等待)
                self._begin_new_ms(self._last_ts + 1)
                self._drift = self._last_ts - wall
                self._alert("rollback_exceeded_drift_engaged",
                            "时钟回拨 %dms 超过容忍区 %dms, 漂移位接管(偏移 %dms)"
                            % (delta, self.max_rollback_ms, self._drift),
                            delta=delta, drift=self._drift)
                return self._emit(self._last_ts, 0, "drift", wall)
            if self._in_rollback is None:
                self._in_rollback = wall
                self._alert("clock_rollback_hold",
                            "检测到时钟回拨 %dms, 冻结时间位 %d 等待物理时钟追平"
                            % (delta, self._last_ts), delta=delta)
        else:
            self._in_rollback = None             # 恰好同一毫秒 = 同毫秒并发波动

        # 同一逻辑毫秒内还有序号可用
        if self._seq < MAX_SEQ:
            self._seq += 1
            return self._emit(self._last_ts, self._seq,
                              "wait" if wall < self._last_ts else "normal", wall)

        # ---- B'. 序号耗尽: 等待(而非回退)下一毫秒 -------------------------
        self.stats["wait_seq_exhausted"] += 1
        self._alert("seq_exhausted_wait",
                    "时间位 %d 的序号耗尽, 等待下一毫秒" % self._last_ts)
        wall, progressed = self._sleep_until_progress()
        if progressed:
            self._begin_new_ms(wall + self._drift)
            return self._emit(self._last_ts, 0, "wait", wall)
        # 等不到新毫秒 -> 强制推进 1ms(仍单调/唯一), 并明确告警
        self._begin_new_ms(self._last_ts + 1)
        self.stats["forced_advance"] += 1
        self._alert("wait_timeout_forced_advance",
                    "序号耗尽后等待 %dms 仍无新毫秒, 强制推进时间位到 %d"
                    % (self.max_wait_ms, self._last_ts))
        return self._emit(self._last_ts, 0, "wait", wall)

    def _sleep(self, ms):
        sleeper = getattr(self.clock, "sleep", None)
        if sleeper is not None:
            sleeper(ms)
        else:
            time.sleep(ms / 1000.0)

    # ---- 对外取号 --------------------------------------------------------
    def take(self, n=1):
        """批量取号; 结果与 n 次 take(1) 完全一致(批大小无关)。"""
        n = int(n)
        if n < 0:
            raise ValueError("n 不能为负")
        if n == 0:
            return []
        with self._lock:
            if self._closed:
                raise GeneratorClosed("生成器已关闭, 不能再取号")
            return [self._acquire()["id"] for _ in range(n)]

    def take_detailed(self, n=1):
        """取号并返回逐 ID 明细: 时间位/序号/来源标记/wall/漂移偏移。"""
        n = int(n)
        if n < 0:
            raise ValueError("n 不能为负")
        if n == 0:
            return []
        with self._lock:
            if self._closed:
                raise GeneratorClosed("生成器已关闭, 不能再取号")
            return [self._acquire() for _ in range(n)]

    def next_id(self):
        return self.take(1)[0]

    def snapshot(self):
        with self._lock:
            return {
                "last_ts": self._last_ts,
                "seq": self._seq,
                "last_id": self._last_id,
                "drift": self._drift,
                "clock_id": self.clock_id,
                "issued": self.stats["issued"],
                "alerts": list(self._alerts),
            }


# --------------------------------------------------------------------------
# CLI（保持 `python3 idgen.py req.json out.json` 的原接口）
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# CLI（保持 `python3 idgen.py req.json out.json` 的原接口）
# --------------------------------------------------------------------------
USAGE = """用法: python3 idgen.py req.json out.json

req.json 字段:
  clock            时钟采样序列(单位: 秒); 缺省用系统时钟
  batch            每批取号数量(仅分组, 不影响输出序列)
  count            总共发放多少个 ID; 缺省 = len(clock)(每个采样点一个)
  state            状态文件路径; 缺省不持久化
  clock_id         物理时钟标识(用于跨进程校验)
  max_rollback_ms  容忍区宽度, 超出则启用漂移位(默认 500)
  max_wait_ms      序号耗尽时的单次等待上限(默认 10)
  fsync            是否对状态文件 fsync(默认 true)

输出: {"ids":[...], "meta":[{index,id,ts,seq,src,wall,drift}],
       "alerts":[...], "stats":{...}, "state":{...}}
"""


def _clock_from_req(req):
    if "clock" not in req:
        return SystemClock()
    values = req["clock"]
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("req.clock 必须是非空数组(单位为秒)")
    # 脚本化时钟：按列表逐次推进，最后一项之后按最后一项重复
    return ScriptedClock([int(round(float(v) * 1000)) for v in values])


class ScriptedClock:
    """按 req.clock 逐次返回毫秒时间（多次读取同一批内保持不前进）。"""

    def __init__(self, values_ms):
        self._values = list(values_ms)
        self._pos = 0
        self._lock = threading.Lock()

    def now_ms(self):
        with self._lock:
            value = self._values[min(self._pos, len(self._values) - 1)]
            self._pos += 1
            return value

    def sleep(self, ms):
        # 脚本化时钟不额外跳样本: 等待期间由后续 now_ms() 自然推进脚本
        return None


def run(req):
    """执行一次生成任务, 返回与 README 兼容的结果字典。"""
    batch = int(req.get("batch", 1) or 1)
    clock = _clock_from_req(req)
    state_path = req.get("state")

    gen = ClockRollbackIdGenerator(
        clock=clock,
        state_path=state_path,
        clock_id=req.get("clock_id"),
        max_rollback_ms=int(req.get("max_rollback_ms", DEFAULT_MAX_ROLLBACK_MS)),
        max_wait_ms=int(req.get("max_wait_ms", DEFAULT_MAX_WAIT_MS)),
        fsync=bool(req.get("fsync", True)),
    )

    if "count" in req:
        count = int(req["count"])
    elif req.get("clock"):
        # 与基线语义一致: 每个时钟采样点发放 1 个 ID
        count = len(req["clock"])
    else:
        count = batch
    count = max(0, count)

    detailed = []
    try:
        done = 0
        while done < count:
            chunk = min(batch, count - done)
            detailed.extend(gen.take_detailed(chunk))
            done += chunk
        alerts = gen.alerts
        snap = gen.snapshot()
        stats = dict(gen.stats)
    finally:
        gen.close()

    ids = [item["id"] for item in detailed]
    meta = [{
        "index": i,
        "id": item["id"],
        "ts": item["ts"],
        "seq": item["seq"],
        "src": item["src"],
        "wall": item["wall"],
        "drift": item["drift"],
    } for i, item in enumerate(detailed)]

    result = {"ids": ids, "meta": meta, "alerts": alerts,
              "stats": stats, "state": snap}
    # 自检：任何一次违反单调/唯一都视为实现缺陷
    if any(b <= a for a, b in zip(ids, ids[1:])):
        result["alerts"].append({"code": "invariant_violation",
                                 "message": "输出序列非严格递增"})
    if len(set(ids)) != len(ids):
        result["alerts"].append({"code": "invariant_violation",
                                 "message": "输出序列存在重复 ID"})
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    if len(argv) != 2:
        sys.stderr.write(USAGE)
        return 2
    with open(argv[0], "r", encoding="utf-8") as handle:
        req = json.load(handle)
    result = run(req)
    with open(argv[1], "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

