#!/usr/bin/env python3
"""分块断点续传下载器：块级持久化 + 块级校验隔离 + 整体校验重组。

设计要点
========
1. 以 chunk 为粒度把「已完成块 + 各自 sha256 + 精确 offset/length」持久化到 state
   文件。续传时先按 sha 复核磁盘上的 .part 文件（绝不盲信 state），只对真正缺失
   或复核失败的块发 Range。
2. 每个 Range 请求都带精确的 64 位 [offset, offset+length) 区间，响应必须回填到
   同一偏移（os.pwrite）。服务端返回的起点不符 / 长度不符 / 摘要不符一律判定该块
   失败：坏数据写入 quarantine 隔离区，随后只重取该块。这样「区间错位 -> 拼接错位」
   从根上被切断。
3. 重组后对整文件做 sha256 校验；失败时按块定位坏块 -> 隔离 -> 重取 -> 再校验，
   只有整体校验通过才把 .part 原子替换成最终输出。
4. 源不支持 Range（伴生 src.bin.norange）或只对头部若干字节支持 Range 时，退化为
   整体重传，输出 mode="full" 并产生告警。
5. 输出逐块状态、重试原因、Range 请求区间与吞吐统计。同一块映射下并发数只影响
   调度顺序：最终文件字节一致，块记录（i / sha / retried）一致。

用法
====
    python3 dl.py req.json output [--concurrency N] [--chunk N] [--state PATH]
                                  [--max-retries N] [--reset] [--quiet]

req.json（除 source 外均可选）::

    {"source": "src.bin",             # 相对 req.json 所在目录
     "chunk": 65536,                  # 块大小（字节，64 位）
     "concurrency": 4,                # 并发数，1/4/8 结果一致
     "state": "state.json",           # 相对 req.json 所在目录
     "fail_at": [0.5, 0.8],           # 模拟断流：进度比例(<=1) 或绝对字节(>1)
     "corrupt_chunks": [3],           # 模拟源端字节翻转（首次投递坏，重取正确）
     "corrupt_times": 1,              # 每个坏块投递几次坏数据
     "misalign_chunks": [],           # 模拟服务端错位区间（锻炼对齐校验）
     "range_head": 1048576,           # 模拟仅头 N 字节支持 Range
     "rate": 0, "latency_ms": 0,      # 可选节流，便于观察吞吐
     "max_retries": 3}

退出码：0 完成且整体校验通过；3 模拟断流（可续跑）；4 校验/重试耗尽；2 用法错误。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import threading
import time
import traceback

STATE_VERSION = 2
INT64_MAX = (1 << 63) - 1
READ_PIECE = 256 << 10
DEFAULT_CHUNK = 1 << 20
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_RETRIES = 3
MAX_VERIFY_ROUNDS = 3
MAX_QUARANTINE_RECORDS = 64
MAX_ALERTS = 200
MAX_RANGES_PER_CHUNK = 8

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 3
EXIT_VERIFY = 4


class UsageError(Exception):
    pass


class Interrupted(Exception):
    """模拟断流：连接在传输中被切断（可续传）。"""


class ChunkFailed(Exception):
    def __init__(self, record):
        super().__init__("chunk %d exhausted retries" % record.index)
        self.record = record


class DegradeNeeded(Exception):
    """服务端无视/压扁 Range，必须退化为整体重传。"""

    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------- #
# 基础工具（偏移/长度全程 64 位）
# --------------------------------------------------------------------------- #
def i64(value, name):
    if isinstance(value, bool):
        raise UsageError("%s must be an integer, got bool" % name)
    if isinstance(value, float):
        if value != int(value):
            raise UsageError("%s must be an integer, got %r" % (name, value))
        value = int(value)
    elif isinstance(value, str):
        try:
            value = int(value.strip(), 10)
        except ValueError:
            raise UsageError("%s must be an integer, got %r" % (name, value))
    if not isinstance(value, int):
        raise UsageError("%s must be an integer, got %r" % (name, value))
    if value < 0 or value > INT64_MAX:
        raise UsageError("%s out of 64-bit range: %r" % (name, value))
    return value


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            piece = f.read(READ_PIECE)
            if not piece:
                break
            h.update(piece)
    return h.hexdigest()


def read_region(path, offset, length):
    try:
        with open(path, "rb") as f:
            f.seek(i64(offset, "offset"))
            return f.read(i64(length, "length"))
    except FileNotFoundError:
        return b""


def plan_chunks(total, chunk):
    """64 位块映射：[(index, offset, length)]。"""
    total = i64(total, "total")
    chunk = i64(chunk, "chunk")
    if chunk <= 0:
        raise UsageError("chunk must be > 0")
    plan = []
    offset = 0
    index = 0
    while offset < total:
        length = chunk if total - offset > chunk else total - offset
        plan.append((index, i64(offset, "offset"), i64(length, "length")))
        offset += length
        index += 1
    return plan


def flip_bytes(data, flips=2):
    """源端字节翻转：在块内均匀位置翻 2 个字节。"""
    buf = bytearray(data)
    if not buf:
        return bytes(buf)
    step = max(1, len(buf) // (flips + 1))
    for k in range(flips):
        pos = min(len(buf) - 1, (k + 1) * step - 1)
        buf[pos] ^= 0xFF
    return bytes(buf)


def atomic_json(path, obj):
    tmp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 源端模拟：可断流 / 可字节翻转 / 可错位 / 可无视 Range
# --------------------------------------------------------------------------- #
class Source:
    """把本地文件包装成一个「有脾气」的 HTTP Range 源。

    range_mode:
      full — 完整支持 Range
      none — 无视 Range（伴生 <src>.norange），返回 200 整份数据
      head — 仅头部 range_head 字节支持 Range，越界同样返回整份数据
    """

    def __init__(self, path, spec, chunk_size):
        self.path = path
        self.spec = spec
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            self.size = i64(f.tell(), "source size")
        self.chunk_size = i64(chunk_size, "chunk")
        self.sha256 = sha256_file(path)

        self.corrupt_chunks = set(i64(v, "corrupt_chunks") for v in (spec.get("corrupt_chunks") or []))
        self.misalign_chunks = set(i64(v, "misalign_chunks") for v in (spec.get("misalign_chunks") or []))
        self.corrupt_times = max(1, i64(spec.get("corrupt_times", 1), "corrupt_times"))
        self.flips = max(1, i64(spec.get("flips", 2), "flips"))
        self._digests = {}
        self._lock = threading.Lock()
        self.requests = 0
        self.served_bytes = 0

        marker = path + ".norange"
        if os.path.exists(marker):
            self.range_mode = "none"
            self.range_head = 0
            self.range_reason = "source does not support Range (%s exists)" % os.path.basename(marker)
        elif spec.get("range_head") is not None:
            head = i64(spec["range_head"], "range_head")
            if head < self.size:
                self.range_mode = "head"
                self.range_head = head
                self.range_reason = "source supports Range only for head %d bytes (%d-byte file)" % (
                    head, self.size)
            else:
                self.range_mode = "full"
                self.range_head = self.size
                self.range_reason = ""
        else:
            self.range_mode = "full"
            self.range_head = self.size
            self.range_reason = ""

        self.rate = float(spec.get("rate", 0) or 0)
        self.latency = float(spec.get("latency_ms", 0) or 0) / 1000.0
        self.fail_at = list(spec.get("fail_at") or [])

    # -- 元信息 ----------------------------------------------------------- #
    def block(self, index):
        offset = i64(index, "index") * self.chunk_size
        return read_region(self.path, offset, self.chunk_size)

    def chunk_digest(self, index):
        if index not in self._digests:
            self._digests[index] = sha256_bytes(self.block(index))
        return self._digests[index]

    def is_transient(self, index, delivery):
        """该块第 delivery 个完整响应是否注入瞬时故障（重取一定拿到好数据）。

        delivery 由调用方从 state 里持久化的「已完成响应数」推出，因此跨进程、
        跨并发都稳定：同一个块无论调度先后，第一次完整响应才是坏的。
        """
        if delivery > self.corrupt_times:
            return None
        if index in self.misalign_chunks:
            return "misalign"
        if index in self.corrupt_chunks:
            return "corrupt"
        return None

    # -- 读取 ------------------------------------------------------------- #
    def _read_at(self, start, count, budget):
        out = bytearray()
        with open(self.path, "rb") as f:
            f.seek(i64(start, "start"))
            remaining = i64(count, "count")
            while remaining > 0:
                if self.latency:
                    time.sleep(self.latency)
                piece = f.read(min(READ_PIECE, remaining))
                if not piece:
                    break
                out.extend(piece)
                remaining -= len(piece)
                if budget is not None:
                    budget.spend(len(piece))
                if self.rate > 0:
                    time.sleep(len(piece) / self.rate)
        with self._lock:
            self.requests += 1
            self.served_bytes += len(out)
        return bytes(out)

    def read_range(self, offset, length, budget=None, chunk_index=None, probe=False):
        """返回 (data, served_offset, full_body)。客户端必须校验 served_offset。"""
        offset = i64(offset, "offset")
        length = i64(length, "length")
        if offset > self.size:
            raise UsageError("range start %d beyond source size %d" % (offset, self.size))
        length = min(length, self.size - offset)

        if self.range_mode == "none":
            if probe:
                return b"", 0, True
            data = self._read_at(0, self.size, budget)      # 200 OK 整份
            return data[offset:offset + length], 0, True
        if self.range_mode == "head" and offset + length > self.range_head:
            if probe:
                return b"", 0, True
            data = self._read_at(0, self.size, budget)      # 越界 -> 同样整份
            return data[offset:offset + length], 0, True

        data = self._read_at(offset, length, budget)
        return data, offset, False

    def read_full(self, budget=None, delivery=1):
        """整份读取（无 Range）。服务端按 corrupt_chunks 在第 delivery 次投递坏数据。"""
        data = self._read_at(0, self.size, budget)
        if delivery > self.corrupt_times:
            return data
        buf = None
        for idx in sorted(self.corrupt_chunks):
            start = i64(idx, "index") * self.chunk_size
            if start >= len(data):
                continue
            if buf is None:
                buf = bytearray(data)
            end = min(len(buf), start + self.chunk_size)
            buf[start:end] = flip_bytes(bytes(buf[start:end]), self.flips)
        return bytes(buf) if buf is not None else data

    def probe(self):
        """探测服务端 Range 能力：请求末尾 1 字节。"""
        if self.size == 0:
            return True, ""
        _, served, full_body = self.read_range(self.size - 1, 1, None, None, probe=True)
        if full_body or served != self.size - 1:
            return False, self.range_reason or "server ignores Range requests"
        return True, ""

    def manifest(self):
        """可信清单（等价于源站 .sha256 / Digest 清单）。"""
        plan = plan_chunks(self.size, self.chunk_size)
        return {
            "size": self.size,
            "chunk": self.chunk_size,
            "sha256": self.sha256,
            "chunks": [{"i": i, "offset": o, "length": ln, "sha": self.chunk_digest(i)}
                       for (i, o, ln) in plan],
        }


class RunBudget:
    """把本次运行的传输预算/断流点做成可被所有 worker 观测的开关。"""

    def __init__(self, limit=None, label=""):
        self.limit = limit
        self.label = label
        self.used = 0
        self.aborted = False
        self.reason = ""
        self._lock = threading.Lock()

    def spend(self, n):
        with self._lock:
            if self.aborted:
                raise Interrupted(self.reason or "run aborted")
            self.used += n
            if self.limit is not None and self.used >= self.limit:
                self.aborted = True
                self.reason = "simulated stream cut at fail_at=%s" % self.label
                raise Interrupted(self.reason)

    def abort(self, reason):
        with self._lock:
            if not self.aborted:
                self.aborted = True
                self.reason = reason


# --------------------------------------------------------------------------- #
# 状态文件：块级持久化（原子写）
# --------------------------------------------------------------------------- #
class StateStore:
    """持久化已完成块与各自校验值。

    续传时绝不盲信本文件：调用方会按记录的 offset/length 重新读 .part 并复核 sha。
    """

    def __init__(self, path, layout):
        self.path = path
        self.layout = layout
        self.lock = threading.RLock()
        self.seed_alerts = []
        self.persisted_alerts = []
        self.data = {
            "version": STATE_VERSION,
            "layout": layout,
            "completed": {},
            "chunks": {},
            "quarantine": [],
            "rounds": [],
            "alerts": [],
            "fail_cursor": 0,
            "totals": {"requests": 0, "served_bytes": 0, "runs": 0},
            "created": round(time.time(), 3),
            "updated": round(time.time(), 3),
        }

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            bad = self.path + ".unsafe." + str(int(time.time()))
            try:
                os.replace(self.path, bad)
            except OSError:
                pass
            self.seed_alerts.append(
                "state file unreadable; moved to " + os.path.basename(bad) + ", restarted empty")
            return
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            self.seed_alerts.append("state version unsupported; restarted empty")
            return
        if raw.get("layout") != self.layout:
            self.seed_alerts.append("state layout mismatch (size/chunk changed); block map rebuilt")
            return
        self.data["completed"] = raw.get("completed") or {}
        self.data["chunks"] = raw.get("chunks") or {}
        self.data["quarantine"] = raw.get("quarantine") or []
        self.data["rounds"] = raw.get("rounds") or []
        self.data["alerts"] = raw.get("alerts") or []
        self.persisted_alerts = list(self.data["alerts"])
        self.data["totals"] = raw.get("totals") or {"requests": 0, "served_bytes": 0, "runs": 0}
        self.data["fail_cursor"] = int(raw.get("fail_cursor", 0) or 0)
        self.data["created"] = raw.get("created", self.data["created"])

    def completed_map(self):
        with self.lock:
            return dict(self.data["completed"])

    def record_chunk(self, record):
        with self.lock:
            self.data["chunks"][str(record.index)] = record.to_state()
            if record.done:
                self.data["completed"][str(record.index)] = {
                    "sha": record.sha,
                    "offset": record.offset,
                    "length": record.length,
                    "verified_at": round(time.time(), 3),
                }
            else:
                self.data["completed"].pop(str(record.index), None)
            self.data["updated"] = round(time.time(), 3)
            self._flush_locked()

    def record_quarantine(self, entry):
        with self.lock:
            self.data["quarantine"].append(entry)
            if len(self.data["quarantine"]) > MAX_QUARANTINE_RECORDS:
                del self.data["quarantine"][: len(self.data["quarantine"]) - MAX_QUARANTINE_RECORDS]
            self.data["updated"] = round(time.time(), 3)
            self._flush_locked()

    def set_alerts(self, alerts):
        with self.lock:
            self.data["alerts"] = list(alerts)[-MAX_ALERTS:]
            self._flush_locked()

    def add_totals(self, requests, served_bytes):
        """把本次运行的传输量累加进 state，使统计覆盖全部续传轮次。"""
        with self.lock:
            totals = self.data.setdefault("totals", {"requests": 0, "served_bytes": 0, "runs": 0})
            totals["requests"] = int(totals.get("requests", 0)) + int(requests)
            totals["served_bytes"] = int(totals.get("served_bytes", 0)) + int(served_bytes)
            totals["runs"] = int(totals.get("runs", 0)) + 1
            self._flush_locked()
            return dict(totals)

    def record_round(self, entry):
        with self.lock:
            self.data["rounds"].append(entry)
            self.data["updated"] = round(time.time(), 3)
            self._flush_locked()

    def _flush_locked(self):
        atomic_json(self.path, self.data)


class ChunkRecord:
    """单个块的运行态记录（最终写进 meta/state 的逐块状态）。"""

    __slots__ = ("index", "offset", "length", "sha", "done", "attempts", "retried",
                 "retry_reasons", "source", "ranges", "status",
                 "quarantined", "error", "delivery")

    def __init__(self, index, offset, length):
        self.index = index
        self.offset = offset
        self.length = length
        self.sha = None
        self.done = False
        self.attempts = 0        # 数据级尝试次数（断流不计入，保证并发无关）
        self.retried = False     # 是否发生过「数据级」重取（坏块/错位/复核失败）
        self.retry_reasons = []
        self.source = "range"    # range | state | full
        self.ranges = []         # 本块发出的精确 [start,end] 区间
        self.status = "pending"  # pending | done | failed
        self.quarantined = 0
        self.error = ""
        self.delivery = 0        # 已完整送达该块的响应数（用于稳定注入故障）

    def add_range(self, offset, length):
        self.ranges.append([offset, offset + length - 1])
        if len(self.ranges) > MAX_RANGES_PER_CHUNK:
            del self.ranges[0]

    def to_state(self):
        return {
            "i": self.index,
            "offset": self.offset,
            "length": self.length,
            "sha": self.sha,
            "status": self.status,
            "attempts": self.attempts,
            "retried": self.retried,
            "reasons": list(self.retry_reasons),
            "source": self.source,
            "quarantined": self.quarantined,
            "error": self.error,
            "delivery": self.delivery,
            "ranges": list(self.ranges),
        }

    def to_meta(self):
        return {
            "i": self.index,
            "offset": self.offset,
            "length": self.length,
            "sha": self.sha,
            "retried": self.retried,
            "attempts": self.attempts,
            "status": self.status,
            "source": self.source,
            "reasons": list(self.retry_reasons),
            "quarantined": self.quarantined,
            "ranges": list(self.ranges),
        }


class AttemptError(Exception):
    def __init__(self, reason, detail="", payload=None, delivered=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.payload = payload
        self.delivered = delivered


# --------------------------------------------------------------------------- #
# 下载器
# --------------------------------------------------------------------------- #
class Downloader:
    def __init__(self, req_path, out_path, options):
        self.req_path = os.path.abspath(req_path)
        self.base_dir = os.path.dirname(self.req_path)
        self.out_path = os.path.abspath(out_path)
        self.options = options
        self.alerts = []
        self.quarantine_dir = self.out_path + ".quarantine"
        self.log_lines = []
        self.stop = threading.Event()
        self.degrade_reason = None
        self.stats_lock = threading.Lock()
        self.stats = {
            "retries": 0, "requests": 0, "served_bytes": 0, "bytes": 0,
            "quarantined": 0, "degraded": False,
        }
        self.started = time.time()
        self._done_bytes = 0
        self._failed = False
        self.manifest_by_index = None
        self.meta = None
        self.rec_locks = {}
        self._load_request()

    # -- 配置 ------------------------------------------------------------- #
    def _load_request(self):
        try:
            with open(self.req_path, "r", encoding="utf-8") as f:
                req = json.load(f)
        except FileNotFoundError:
            raise UsageError("request file not found: " + self.req_path)
        except json.JSONDecodeError as e:
            raise UsageError("request file is not valid JSON: %s" % e)
        if not isinstance(req, dict):
            raise UsageError("request file must contain a JSON object")
        self.req = req
        src = req.get("source")
        if not isinstance(src, str) or not src:
            raise UsageError("request.source must be a non-empty string")
        self.source_path = src if os.path.isabs(src) else os.path.join(self.base_dir, src)
        if not os.path.isfile(self.source_path):
            raise UsageError("source file not found: " + self.source_path)

        chunk = self.options.get("chunk")
        if chunk is None:
            chunk = req.get("chunk")
        if chunk is None:
            chunk = DEFAULT_CHUNK
        self.chunk_size = i64(chunk, "chunk")
        if self.chunk_size <= 0:
            raise UsageError("chunk must be > 0")

        conc = self.options.get("concurrency")
        if conc is None:
            conc = req.get("concurrency", DEFAULT_CONCURRENCY)
        self.concurrency = max(1, min(64, i64(conc, "concurrency")))

        state = self.options.get("state")
        if state is None:
            state = req.get("state")
        if state is None:
            state = self.out_path + ".state.json"
        self.state_path = state if os.path.isabs(state) else os.path.join(self.base_dir, state)

        retries = self.options.get("max_retries")
        if retries is None:
            retries = req.get("max_retries", DEFAULT_MAX_RETRIES)
        self.max_retries = max(1, i64(retries, "max_retries"))

        self.fail_at = []
        for k, v in enumerate(req.get("fail_at") or []):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise UsageError("fail_at[%d] must be a number" % k)
            if v < 0:
                raise UsageError("fail_at[%d] must be >= 0" % k)
            self.fail_at.append(float(v))

    # -- 日志 ------------------------------------------------------------- #
    def note(self, line):
        stamp = "%.3fs" % (time.time() - self.started)
        self.log_lines.append("[%s] %s" % (stamp, line))
        if not self.options.get("quiet"):
            sys.stderr.write("[%s] %s\n" % (stamp, line))
            sys.stderr.flush()

    # -- 主流程 ----------------------------------------------------------- #
    def run(self):
        src = Source(self.source_path, self.req, self.chunk_size)
        self.source = src
        self.size = src.size
        plan = plan_chunks(self.size, self.chunk_size)
        self.plan = plan
        self.layout = {"size": self.size, "chunk": self.chunk_size, "blocks": len(plan)}
        self.part_path = self.out_path + ".part"

        self.state = StateStore(self.state_path, self.layout)
        if self.options.get("reset"):
            for p in (self.state_path, self.part_path, self.out_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.state.load()
        if self.options.get("reset"):
            self.state.persisted_alerts = []
        # 上一轮已发出的告警要随 state 一起保留，续跑后仍能定位问题
        for a in self.state.persisted_alerts:
            if a not in self.alerts:
                self.alerts.append(a)
        self.alerts.extend(self.state.seed_alerts)

        self.log_lines = []
        self.note("source %d bytes, chunk %d, %d block(s), concurrency %d"
                  % (self.size, self.chunk_size, len(plan), self.concurrency))

        if self.alerts:
            for a in self.alerts:
                self.note("ALERT " + a)

        records = {}
        for (i, off, ln) in plan:
            rec = ChunkRecord(i, off, ln)
            st = self.state.data["chunks"].get(str(i))
            if st:
                rec.retry_reasons = list(st.get("reasons") or [])
                rec.quarantined = int(st.get("quarantined") or 0)
                rec.attempts = int(st.get("attempts") or 0)
                rec.retried = bool(st.get("retried"))
                rec.delivery = int(st.get("delivery") or 0)
                seen = st.get("ranges") or []
                rec.ranges = [[i64(a, "range.start"), i64(b, "range.end")]
                              for a, b in seen if isinstance(a, (int, float))
                              and isinstance(b, (int, float))][-MAX_RANGES_PER_CHUNK:]
            records[i] = rec
        self.records = records

        # 幂等重跑：输出已存在且整文件摘要吻合 -> 直接回报完成，不再重传
        if not self.options.get("reset") and self._output_is_complete():
            self._finalize_meta("chunked")
            return EXIT_OK

        # 打开 .part（稀疏文件，按 64 位偏移写入）
        self._prepare_part()

        # 1) 复核 state：绝不盲信，逐块按记录区间重读 + sha 校验
        self._verify_state()

        # 2) Range 能力探测：不支持或仅头部支持 -> 退化整体重传
        supports, why = src.probe()
        if not supports:
            code = self._degrade_full(why)
            self._finalize_meta("full")
            return code

        # 3) 分块下载（运行中若发现 Range 异常，会在内部退化为整体重传）
        code = self._download_chunks()
        mode = "full" if self.stats["degraded"] else "chunked"
        if code != EXIT_OK:
            self._finalize_meta(mode, interrupted=(code == EXIT_INTERRUPTED),
                                failed=(code == EXIT_ERROR))
            return code

        # 4) 重组 + 整体校验（失败则按块定位重取）
        ok = self._assemble_verify()
        if not ok:
            self._finalize_meta("chunked", failed=True)
            return EXIT_VERIFY

        self._finalize_meta("chunked")
        return EXIT_OK

    def _output_is_complete(self):
        if not os.path.isfile(self.out_path):
            return False
        try:
            if sha256_file(self.out_path) != self.source.sha256:
                return False
        except OSError:
            return False
        completed = self.state.completed_map()
        if len(completed) != len(self.plan):
            return False
        for (i, off, ln) in self.plan:
            entry = completed.get(str(i)) or {}
            if not entry.get("sha"):
                return False
        self.note("output already present and whole-file sha256 matches source; nothing to do")
        for (i, off, ln) in self.plan:
            rec = self.records[i]
            rec.sha = self.state.data["chunks"].get(str(i), {}).get("sha")
            rec.done = True
            rec.status = "done"
            rec.source = "state"
        self._done_bytes = self.size
        self.stats["bytes"] = self.size
        self.manifest_by_index = {c["i"]: c for c in self.source.manifest()["chunks"]}
        return True

    # -- 基础文件操作 ----------------------------------------------------- #
    def _prepare_part(self):
        parent = os.path.dirname(self.out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(self.part_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.ftruncate(fd, self.size)
        finally:
            os.close(fd)

    def _write_at(self, offset, data):
        fd = os.open(self.part_path, os.O_RDWR)
        try:
            written = 0
            while written < len(data):
                n = os.pwrite(fd, data[written:], offset + written)
                if n <= 0:
                    raise OSError("short pwrite at offset %d" % (offset + written))
                written += n
            os.fsync(fd)
        finally:
            os.close(fd)

    def _quarantine(self, rec, attempt, data, reason):
        os.makedirs(self.quarantine_dir, exist_ok=True)
        seq = self._next_quarantine_seq()
        path = os.path.join(
            self.quarantine_dir,
            "chunk-%06d-attempt-%02d-seq-%04d.bin" % (rec.index, attempt, seq))
        with open(path, "wb") as f:
            f.write(data if data is not None else b"")
            f.flush()
            os.fsync(f.fileno())
        rec.quarantined += 1
        entry = {
            "chunk": rec.index,
            "attempt": attempt,
            "offset": rec.offset,
            "length": rec.length,
            "reason": reason,
            "file": os.path.relpath(path, os.path.dirname(self.out_path) or "."),
            "sha_received": sha256_bytes(data) if data is not None else None,
        }
        self.state.record_quarantine(entry)
        self.note("quarantine chunk %d attempt %d: %s -> %s"
                  % (rec.index, attempt, reason, os.path.basename(path)))
        return entry

    def _next_quarantine_seq(self):
        with self.stats_lock:
            seq = self.stats["quarantined"] + 1
            self.stats["quarantined"] = seq
            return seq

    def _chunk_lock(self, index):
        with self.stats_lock:
            lock = self.rec_locks.get(index)
            if lock is None:
                lock = threading.Lock()
                self.rec_locks[index] = lock
            return lock

    # -- state 复核 ------------------------------------------------------- #
    def _expected_sha(self, index):
        if self.manifest_by_index is None:
            return None
        item = self.manifest_by_index.get(index)
        return item["sha"] if item else None

    def _verify_state(self):
        manifest = self.source.manifest()
        self.manifest_by_index = {c["i"]: c for c in manifest["chunks"]}
        completed = self.state.completed_map()
        if not completed:
            # state 丢失（首次运行 / 被截断重建）：退一步按可信清单复核 .part，
            # 能救回的块照样复用，绝不无谓重传。
            adopted = self._adopt_from_part()
            if adopted:
                self.note("state empty; adopted %d block(s) already present and valid in .part"
                          % adopted)
                self.alerts.append("state reconstructed from .part: %d block(s) reused" % adopted)
            return
        bad = []
        reused = 0
        for (i, off, ln) in self.plan:
            rec = self.records[i]
            entry = completed.get(str(i))
            if not entry:
                continue
            expected = self._expected_sha(i)
            rec_len = i64(entry.get("length", ln), "state.length")
            rec_off = i64(entry.get("offset", off), "state.offset")
            if rec_off != off or rec_len != ln:
                bad.append((rec, "state range drifted: recorded [%d,%d) vs planned [%d,%d)"
                            % (rec_off, rec_off + rec_len, off, off + ln)))
                continue
            data = read_region(self.part_path, off, ln)
            if len(data) != ln:
                bad.append((rec, "state says done but .part has only %d/%d bytes" % (len(data), ln)))
                continue
            got = sha256_bytes(data)
            if expected is not None and got != expected:
                bad.append((rec, "state verify mismatch: .part sha %s != source %s" % (got[:16], expected[:16])))
                continue
            if entry.get("sha") and entry["sha"] != got:
                bad.append((rec, "state sha %s != .part sha %s" % (entry["sha"][:16], got[:16])))
                continue
            rec.sha = got
            rec.done = True
            rec.status = "done"
            rec.source = "state"
            # 保留历史重取标记与 Range 历史：跨运行的块记录必须完整可复现
            rec.retried = bool(rec.retried or rec.attempts > 1)
            reused += 1
        for rec, why in bad:
            rec.retried = True
            rec.retry_reasons.append(why)
            rec.status = "pending"
            rec.done = False
            rec.source = "range"
            self.state.record_chunk(rec)
            self.note("state FAIL chunk %d: %s (will refetch this block only)" % (rec.index, why))
            if not any("re-verified" in a for a in self.alerts):
                self.alerts.append(
                    "state re-verified at block granularity: %d block(s) refetched" % len(bad))
        adopted = self._adopt_from_part(only_missing=True)
        if adopted:
            reused += adopted
            self.note("adopted %d block(s) whose bytes were already valid in .part" % adopted)
        if reused:
            self.note("resume: %d/%d block(s) re-verified from .part, no refetch"
                      % (reused, len(self.plan)))
        self._done_bytes = sum(self.records[i].length for i in self.records if self.records[i].done)
        self.stats["bytes"] = self._done_bytes

    def _adopt_from_part(self, only_missing=False):
        """把 .part 里已经存在且与可信清单吻合的块直接认领，避免无谓重传。

        只有 sha 与源清单一致的块才会被认领，因此 .part 里的陈旧/垃圾数据不会被误用。
        """
        imported = 0
        for (i, off, ln) in self.plan:
            rec = self.records[i]
            if only_missing and rec.done:
                continue
            expected = self._expected_sha(i)
            if expected is None:
                continue
            data = read_region(self.part_path, off, ln)
            if len(data) != ln:
                continue
            got = sha256_bytes(data)
            if got != expected:
                continue
            rec.sha = got
            rec.done = True
            rec.status = "done"
            rec.source = "state"
            imported += 1
            self.state.record_chunk(rec)
        return imported

    # -- 断流预算 --------------------------------------------------------- #
    def _fail_target(self, point):
        target = int(point * self.size) if point <= 1.0 else int(point)
        return max(0, min(target, self.size))

    def _budget_for_run(self):
        cursor = int(self.state.data.get("fail_cursor", 0) or 0)
        progress = self._done_bytes
        # 跳过已落在进度之后的断流点（压缩重跑/去重点），保证每次真断一处
        while cursor < len(self.fail_at):
            target = self._fail_target(self.fail_at[cursor])
            if target < progress or (target == progress and progress >= self.size):
                self.note("fail_at[%d]=%s already behind progress (%d/%d bytes); skipped"
                          % (cursor, self.fail_at[cursor], progress, self.size))
                cursor += 1
                continue
            remaining = target - progress
            if remaining <= 0:
                cursor += 1
                continue
            return RunBudget(limit=remaining, label=str(self.fail_at[cursor])), cursor
        self.state.data["fail_cursor"] = cursor
        return None, cursor

    # -- 分块下载 --------------------------------------------------------- #
    def _download_chunks(self):
        pending = [r for r in self.records.values() if not r.done]
        pending.sort(key=lambda r: r.index)
        if not pending:
            self.note("nothing to fetch: all %d block(s) already verified" % len(self.plan))
            return EXIT_OK

        budget, cursor = self._budget_for_run()
        if budget is not None:
            self.note("fail_at[%d]=%s: this run will cut the stream after %d more byte(s)"
                      % (cursor, budget.label, budget.limit))

        interrupted = False
        with cf.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {}
            queue = list(pending)
            while queue or futures:
                while queue and len(futures) < self.concurrency and not interrupted and self.degrade_reason is None:
                    rec = queue.pop(0)
                    futures[pool.submit(self._worker, rec, budget)] = rec
                if not futures:
                    break
                done, _ = cf.wait(list(futures.keys()), return_when=cf.FIRST_COMPLETED)
                for fut in done:
                    rec = futures.pop(fut)
                    try:
                        fut.result()
                    except Interrupted as e:
                        interrupted = True
                        self.note("stream cut while fetching block %d: %s" % (rec.index, e))
                    except DegradeNeeded as e:
                        self.degrade_reason = (e.reason, e.detail)
                        self.note("ALERT server Range anomaly: %s %s" % (e.reason, e.detail))
                    except ChunkFailed as e:
                        rec.status = "failed"
                        rec.error = str(e)
                        self.state.record_chunk(rec)
                        self.note("block %d exhausted %d attempt(s): %s"
                                  % (rec.index, rec.attempts, "; ".join(rec.retry_reasons[-3:])))
                        self._failed = True
                    except Exception as e:  # 意外错误：记录并终止调度
                        rec.status = "failed"
                        rec.error = "%s: %s" % (type(e).__name__, e)
                        self.state.record_chunk(rec)
                        self.note("block %d crashed: %s" % (rec.index, rec.error))
                        self._failed = True
                        traceback.print_exc()
                if interrupted:
                    # 不再调度新块；已排队块放回 state（未完成）
                    for rec in queue:
                        if rec.status != "done":
                            self.state.record_chunk(rec)
                    queue = []
                    budget_abort = budget is not None and budget.aborted
                    if budget_abort:
                        self.state.data["fail_cursor"] = cursor + 1
                        self.state.record_round({
                            "kind": "interrupted",
                            "fail_at": budget.label,
                            "progress": self._done_bytes,
                            "size": self.size,
                            "block": None,
                        })
                    break
                if self.degrade_reason is not None:
                    for rec in queue:
                        self.state.record_chunk(rec)
                    queue = []
                    break

        self.stats["retries"] = self._count_retries()

        if self.degrade_reason is not None:
            why, detail = self.degrade_reason
            return self._degrade_full(why, detail)

        if interrupted:
            self._flush_progress()
            return EXIT_INTERRUPTED

        if getattr(self, "_failed", False):
            self._flush_progress()
            return EXIT_ERROR

        missing = [r for r in self.records.values() if not r.done]
        if missing:
            self.note("ALERT %d block(s) still missing after run" % len(missing))
            self._flush_progress()
            return EXIT_ERROR
        return EXIT_OK

    def _count_retries(self):
        return sum(len(r.retry_reasons) for r in self.records.values())

    def _flush_progress(self):
        self._done_bytes = sum(r.length for r in self.records.values() if r.done)
        self.stats["bytes"] = self._done_bytes
        done = sum(1 for r in self.records.values() if r.done)
        self.note("progress %d/%d block(s), %d/%d bytes verified"
                  % (done, len(self.records), self._done_bytes, self.size))

    # -- 单块 worker ------------------------------------------------------ #
    def _worker(self, rec, budget):
        """取一个块。

        关键不变式：
          * 断流（Interrupted）只回退状态、不改数据级计数，因此重跑/并发都无关；
          * 数据级失败（错位/短包/摘要不符）才累加 rec.attempts 并隔离坏数据；
          * 每个块的「第几次完整响应」取自持久化的 rec.delivery，注入稳定可复现。
        """
        if self.stop.is_set():
            raise Interrupted("run stopped")
        lock = self._chunk_lock(rec.index)   # 同一块绝不并发，投递计数才可信
        run_attempts = 0                      # 本进程的数据级尝试预算
        while True:
            if self.stop.is_set():
                raise Interrupted("run stopped")
            if self.degrade_reason is not None:
                raise DegradeNeeded(*self.degrade_reason)
            ok = False
            with lock:
                rec.add_range(rec.offset, rec.length)
                try:
                    data, delivered = self._fetch_once(rec, budget)
                except Interrupted:
                    rec.status = "pending"
                    self.state.record_chunk(rec)
                    raise
                except DegradeNeeded:
                    raise
                except AttemptError as e:
                    rec.attempts += 1
                    run_attempts += 1
                    rec.retried = True
                    rec.retry_reasons.append(e.reason)
                    rec.status = "pending"
                    if e.delivered is not None:
                        rec.delivery = max(rec.delivery, e.delivered)
                    if e.payload is not None:
                        self._quarantine(rec, rec.attempts, e.payload, e.reason)
                    self.state.record_chunk(rec)
                    self.note("retry block %d attempt %d/%d (run): %s"
                              % (rec.index, run_attempts, self.max_retries, e.reason))
                    if run_attempts >= self.max_retries:
                        raise ChunkFailed(rec)
                    backoff = min(0.2, 0.01 * (2 ** (run_attempts - 1)))
                else:
                    backoff = 0.0
                    self._write_at(rec.offset, data)   # 块内串行写，偏移不会互相覆盖
                    rec.delivery = max(rec.delivery, delivered)
                    ok = True
            if not ok:
                time.sleep(backoff)                    # 退避在锁外，不阻塞其它块
                continue
            rec.sha = sha256_bytes(data)
            rec.done = True
            rec.status = "done"
            rec.source = "range"
            rec.error = ""
            with self.stats_lock:
                self._done_bytes += rec.length
                self.stats["bytes"] = self._done_bytes
            self.state.record_chunk(rec)
            self.note("done block %d [%d,%d) sha %s%s"
                      % (rec.index, rec.offset, rec.offset + rec.length - 1,
                         rec.sha[:16], " (data attempts=%d)" % rec.attempts if rec.attempts else ""))
            return True

    def _fetch_once(self, rec, budget):
        # delivered：这次响应是该块的第几次完整送达。只在调用于「成功拿到响应」
        # 之后才提交到 rec.delivery，断流不计入，因此注入跨运行稳定可复现。
        delivered = rec.delivery + 1
        data, served, full_body = self.source.read_range(
            rec.offset, rec.length, budget, rec.index)
        with self.stats_lock:
            self.stats["requests"] = self.source.requests
            self.stats["served_bytes"] = self.source.served_bytes
        if full_body:
            raise DegradeNeeded(
                self.source.range_reason or "server ignored Range request",
                "requested [%d,%d) got a full-body response" % (rec.offset, rec.offset + rec.length - 1))
        # 源端瞬时故障注入（由「这是第几次完整响应」驱动，稳定可复现）
        fault = self.source.is_transient(rec.index, delivered)
        if fault == "corrupt":
            data = flip_bytes(data, self.source.flips)
        elif fault == "misalign":
            served = rec.offset + 1
        if served != rec.offset:
            raise AttemptError(
                "range misaligned: requested start %d, served start %d" % (rec.offset, served),
                payload=data, delivered=delivered)
        if len(data) != rec.length:
            raise AttemptError(
                "short range body: wanted %d byte(s) at %d, got %d"
                % (rec.length, rec.offset, len(data)), payload=data, delivered=delivered)
        expected = self._expected_sha(rec.index)
        got = sha256_bytes(data)
        if expected is not None and got != expected:
            raise AttemptError(
                "block checksum mismatch: got %s expected %s" % (got[:16], expected[:16]),
                payload=data, delivered=delivered)
        return data, delivered

    # -- 退化整体重传 ----------------------------------------------------- #
    def _degrade_full(self, why, detail=""):
        self.stats["degraded"] = True
        msg = "degraded to full transfer: %s" % why
        if detail:
            msg += " (%s)" % detail
        if msg not in self.alerts:
            self.alerts.append(msg)
        self.note("ALERT " + msg)
        # 退化模式同样要遵守 fail_at：整体重传也是一条流，也会被切断
        cursor = int(self.state.data.get("fail_cursor", 0) or 0)
        budget = None
        if cursor < len(self.fail_at):
            point = self.fail_at[cursor]
            budget = RunBudget(limit=max(1, self._fail_target(point)), label=str(point))
            self.note("fail_at[%d]=%s applies to the full transfer too" % (cursor, point))
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            self.note("full transfer attempt %d/%d (%d bytes)" % (attempt, self.max_retries, self.size))
            budget = None if budget is None else RunBudget(
                limit=budget.limit, label=budget.label)   # 每次尝试重置传输预算
            try:
                data = self.source.read_full(budget, delivery=attempt)
            except Interrupted as e:
                self.note("stream cut during full transfer: %s" % e)
                if budget is not None and budget.aborted:
                    self.state.data["fail_cursor"] = cursor + 1
                self.state.record_round({"kind": "interrupted", "mode": "full",
                                         "fail_at": budget.label if budget else None,
                                         "progress": 0, "size": self.size})
                return EXIT_INTERRUPTED
            got = sha256_bytes(data)
            if got == self.source.sha256:
                self.state.data["fail_cursor"] = len(self.fail_at)
                self._install_full(data, attempt)
                self.note("full transfer verified sha %s" % got[:16])
                return EXIT_OK
            last_error = "full transfer sha mismatch: got %s expected %s" % (
                got[:16], self.source.sha256[:16])
            self.alerts.append(last_error)
            self.note("ALERT " + last_error)
            self.state.record_round({"kind": "full-retry", "attempt": attempt,
                                     "reason": last_error, "size": self.size})
        # 重试耗尽
        for rec in self.records.values():
            rec.status = "failed"
            rec.error = last_error
            rec.retried = True
            self.state.record_chunk(rec)
        self.alerts.append("full transfer failed after %d attempt(s); output not written" % self.max_retries)
        return EXIT_VERIFY

    def _install_full(self, data, attempt):
        with open(self.part_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(self.part_path, self.out_path)
        for (i, off, ln) in self.plan:
            rec = self.records[i]
            piece = data[off:off + ln]
            rec.offset = off
            rec.length = ln
            rec.sha = sha256_bytes(piece)
            rec.done = True
            rec.status = "done"
            rec.source = "full"
            rec.attempts = max(rec.attempts, attempt)
            rec.retried = bool(rec.retried or attempt > 1)
            rec.add_range(off, ln)
            self.state.record_chunk(rec)
        self._done_bytes = self.size
        self.stats["bytes"] = self.size
        self.stats["retries"] = self._count_retries() + (attempt - 1)

    # -- 重组 + 整体校验 -------------------------------------------------- #
    def _assemble_verify(self):
        for round_no in range(1, MAX_VERIFY_ROUNDS + 1):
            got = sha256_file(self.part_path)
            if got == self.source.sha256:
                os.replace(self.part_path, self.out_path)
                self.note("assembled %d bytes, whole-file sha256 %s matches source"
                          % (self.size, got[:16]))
                return True
            self.alerts.append("whole-file sha mismatch on round %d: %s != %s"
                               % (round_no, got[:16], self.source.sha256[:16]))
            self.note("ALERT whole-file verify failed (round %d): %s != %s"
                      % (round_no, got[:16], self.source.sha256[:16]))
            bad = self._locate_bad_blocks()
            if not bad:
                self.alerts.append("whole-file mismatch but every block checksum matches; "
                                   "cannot localize bad block")
                return False
            self.state.record_round({"kind": "verify-failure", "round": round_no,
                                     "file_sha": got, "bad_blocks": [r.index for r in bad]})
            self.note("bad block(s) [%s] located; refetching only these"
                      % ", ".join(str(r.index) for r in bad))
            for rec in bad:
                rec.done = False
                rec.status = "pending"
                rec.retried = True
                rec.retry_reasons.append("whole-file verification failed; block isolated")
                rec.source = "range"
                self._quarantine(rec, rec.attempts + 1,
                                 read_region(self.part_path, rec.offset, rec.length),
                                 "whole-file verify failure")
                self.state.record_chunk(rec)
            self._done_bytes = sum(r.length for r in self.records.values() if r.done)
            self.stats["bytes"] = self._done_bytes
            saved_at = self.fail_at
            self.fail_at = []                     # 重组阶段不再模拟断流
            try:
                code = self._download_chunks_for(bad)
            finally:
                self.fail_at = saved_at
            if code != EXIT_OK:
                self.alerts.append("refetch of bad block(s) did not complete")
                return False
        self.alerts.append("whole-file verification still failing after %d rounds" % MAX_VERIFY_ROUNDS)
        return False

    def _download_chunks_for(self, recs):
        """只重取指定块（块级隔离重取的执行器）。"""
        pending = [r for r in recs if not r.done]
        if not pending:
            return EXIT_OK
        failed = False
        with cf.ThreadPoolExecutor(max_workers=min(self.concurrency, len(pending))) as pool:
            futures = {pool.submit(self._worker, rec, None): rec for rec in pending}
            for fut in cf.as_completed(futures):
                rec = futures[fut]
                try:
                    fut.result()
                except Interrupted:
                    return EXIT_INTERRUPTED
                except ChunkFailed:
                    failed = True
                except Exception as e:
                    failed = True
                    self.note("refetch block %d crashed: %s" % (rec.index, e))
        return EXIT_ERROR if failed else EXIT_OK

    def _locate_bad_blocks(self):
        """用可信块清单逐块比对 .part，返回坏块（块级定位，64 位区间）。"""
        manifest = self.source.manifest()
        by_index = {c["i"]: c for c in manifest["chunks"]}
        bad = []
        for (i, off, ln) in self.plan:
            rec = self.records[i]
            item = by_index.get(i)
            if item is None:
                continue
            data = read_region(self.part_path, off, ln)
            if len(data) != ln or sha256_bytes(data) != item["sha"]:
                bad.append(rec)
        return bad

    # -- 汇总输出 --------------------------------------------------------- #
    def _finalize_meta(self, mode, interrupted=False, failed=False):
        elapsed = max(1e-9, time.time() - self.started)
        with self.stats_lock:
            self.stats["requests"] = self.source.requests
            self.stats["served_bytes"] = self.source.served_bytes
            stats = dict(self.stats)
            if mode == "full" and not interrupted:
                stats["bytes"] = self.size
        stats["blocks_done"] = sum(1 for r in self.records.values() if r.done)
        stats["blocks_total"] = len(self.records)
        stats["elapsed_s"] = round(elapsed, 3)
        stats["bytes_per_s"] = round(stats.get("bytes", 0) / elapsed, 1)
        stats["retries"] = self._count_retries()
        stats["quarantined"] = sum(r.quarantined for r in self.records.values())
        run_stats = {"requests": stats["requests"], "served_bytes": stats["served_bytes"],
                     "elapsed_s": stats["elapsed_s"]}
        totals = self.state.add_totals(stats["requests"], stats["served_bytes"])
        stats["run_requests"] = run_stats["requests"]
        stats["run_served_bytes"] = run_stats["served_bytes"]
        stats["requests"] = totals["requests"]
        stats["served_bytes"] = totals["served_bytes"]
        stats["runs"] = totals["runs"]
        total_elapsed = elapsed
        throughput = {
            "bytes_per_s": round(stats.get("bytes", 0) / max(1e-9, total_elapsed), 1),
            "blocks_per_s": round(stats["blocks_done"] / max(1e-9, total_elapsed), 3),
            "elapsed_s": stats["elapsed_s"],
            "concurrency": self.concurrency,
            "interrupted": interrupted,
            "run": dict(run_stats, concurrency=self.concurrency),
        }
        chunks = [self.records[i].to_meta() for i in sorted(self.records)]
        meta = {
            "mode": mode,
            "status": "interrupted" if interrupted else ("failed" if failed else "complete"),
            "exit_code": EXIT_INTERRUPTED if interrupted else (EXIT_VERIFY if failed else EXIT_OK),
            "source": {
                "path": os.path.basename(self.source_path),
                "size": self.size,
                "sha256": self.source.sha256,
                "range_mode": self.source.range_mode,
                "range_head": self.source.range_head,
            },
            "chunk": self.chunk_size,
            "concurrency": self.concurrency,
            "chunks": chunks,
            "stats": stats,
            "throughput": throughput,
            "alerts": list(self.alerts),
            "log": list(self.log_lines)[-200:],
        }
        self.state.set_alerts(self.alerts)
        atomic_json(self.out_path + ".meta.json", meta)
        self.meta = meta
        done = stats["blocks_done"]
        if not interrupted and not failed:
            self.note("complete: %s mode, %d/%d block(s), %d bytes, %.1f B/s, retries=%d, alerts=%d"
                      % (mode, done, stats["blocks_total"], stats["bytes"],
                         stats["bytes_per_s"], stats["retries"], len(self.alerts)))
        elif interrupted:
            self.note("interrupted: %d/%d block(s) persisted, resume will fetch only missing blocks"
                      % (done, stats["blocks_total"]))
        else:
            self.note("failed: see alerts (%d)" % len(self.alerts))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="dl.py",
        description="分块断点续传下载器：块级持久化 + 块级校验隔离 + 整体重组校验")
    p.add_argument("request", help="req.json 路径")
    p.add_argument("output", help="输出文件路径（meta 写到 <output>.meta.json）")
    p.add_argument("--chunk", type=int, default=None, help="块大小（字节，覆盖 req.chunk）")
    p.add_argument("--concurrency", type=int, default=None, help="并发数（覆盖 req.concurrency）")
    p.add_argument("--state", default=None, help="state 文件路径（覆盖 req.state）")
    p.add_argument("--max-retries", type=int, default=None, help="每块最大尝试次数（覆盖 req）")
    p.add_argument("--reset", action="store_true", help="丢弃 state/.part/输出，从头开始")
    p.add_argument("--quiet", action="store_true", help="不向 stderr 打逐行日志")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    options = {
        "chunk": args.chunk,
        "concurrency": args.concurrency,
        "state": args.state,
        "max_retries": args.max_retries,
        "reset": args.reset,
        "quiet": args.quiet,
    }
    dl = None
    try:
        dl = Downloader(args.request, args.output, options)
        code = dl.run()
    except UsageError as e:
        sys.stderr.write("usage error: %s\n" % e)
        return EXIT_USAGE
    except KeyboardInterrupt:
        sys.stderr.write("interrupted by user\n")
        return EXIT_INTERRUPTED
    except Exception:
        traceback.print_exc()
        return EXIT_ERROR
    if not args.quiet and dl is not None and dl.meta:
        summary = {
            "mode": dl.meta["mode"],
            "status": dl.meta["status"],
            "blocks_done": dl.meta["stats"]["blocks_done"],
            "blocks_total": dl.meta["stats"]["blocks_total"],
            "retries": dl.meta["stats"]["retries"],
            "bytes_per_s": dl.meta["stats"]["bytes_per_s"],
            "alerts": dl.meta["alerts"],
        }
        sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())


