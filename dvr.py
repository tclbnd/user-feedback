#!/usr/bin/env python3
"""直播 DVR 环形片段缓冲：清单/片段一致性、窗口快照与崩溃恢复。

一致性顺序
----------
1. 片段先落盘（临时文件 + fsync + rename），确认可读后才在原子提交的清单
   中加入引用；窗口滑动时新片段所在代先提交，旧引用才被移除。
2. 清单/窗口状态通过 "临时文件 + fsync + rename + fsync(dir)" 原子提交，
   任何时刻磁盘上的清单要么是旧代要么是新代，不存在半更新视图。
3. 读者获取不可变快照并对快照内片段做引用计数（pin）。窗口滑走后旧片段
   仍被快照 pin 住，因此边界读取不会拿到失效文件；未被 pin 的旧文件还会
   延迟若干代再回收，兼顾按磁盘清单读取的外部读者。
4. 每次提交把窗口、next_seq、confirmed_seq 与条目一起持久化到 state.json。
   崩溃后：清理 *.tmp 残留、删除未被已提交状态引用的孤立片段、校验已提交
   片段的长度/CRC/头，必要时回滚到最后一个有效条目，再继续写入。
"""

from __future__ import annotations

import binascii
import glob
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

MAGIC = b"DVRSEG1\0"
FORMAT = 1
STATE_NAME = "state.json"
MANIFEST_NAME = "manifest.json"
RUNNING_NAME = "RUNNING"
SEG_DIR = "segments"


class SimulatedCrash(RuntimeError):
    """进程在提交点被强行终止（用于回放崩溃场景）。"""


class ConsistencyError(RuntimeError):
    """实现或持久化状态违反一致性约束。"""


class DanglingReference(RuntimeError):
    """读者引用的片段已经不可读。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _crc32(data):
    return binascii.crc32(data) & 0xFFFFFFFF


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path, data):
    """同目录临时文件写入 + fsync + 原子 rename + 目录 fsync。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(directory)


def _seg_rel(seq):
    return "%s/seg-%08d.bin" % (SEG_DIR, seq)


def _encode_segment(seq):
    # 内容确定性，便于读端同时校验长度、CRC 与 seq 头。
    payload = ("dvr-segment-%08d|" % seq).encode("ascii") * (1 + (seq % 5))
    return MAGIC + struct.pack(">Q", seq) + payload


def _segment_seq(data):
    if len(data) < len(MAGIC) + 8 or not data.startswith(MAGIC):
        return None
    return struct.unpack(">Q", data[len(MAGIC):len(MAGIC) + 8])[0]


def _seq_from_name(path):
    """从 seg-00000123.bin 解析 seq；非常规命名返回 None。"""
    name = os.path.basename(path)
    if not (name.startswith("seg-") and name.endswith(".bin")):
        return None
    try:
        return int(name[4:-4])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 持久化模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    seq: int
    file: str
    size: int
    crc32: int


@dataclass(frozen=True)
class CommittedState:
    """一次清单提交的完整状态（与片段引用同代）。"""

    generation: int
    window: int
    next_seq: int
    confirmed_seq: int
    entries: tuple


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _state_to_obj(state):
    return {
        "format": FORMAT,
        "generation": state.generation,
        "window": state.window,
        "next_seq": state.next_seq,
        "confirmed_seq": state.confirmed_seq,
        "entries": [
            {"seq": e.seq, "file": e.file, "size": e.size, "crc32": e.crc32}
            for e in state.entries
        ],
    }


def _write_state(root, state):
    payload = _state_to_obj(state)
    wrapper = {"state": payload, "checksum": _crc32(_canonical(payload))}
    _atomic_write(os.path.join(root, STATE_NAME), _canonical(wrapper))


def _read_state(root):
    path = os.path.join(root, STATE_NAME)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        wrapper = json.loads(raw.decode("utf-8"))
        payload = wrapper["state"]
        if wrapper.get("checksum") != _crc32(_canonical(payload)):
            raise ConsistencyError("state.json checksum mismatch")
    except ConsistencyError:
        raise
    except Exception as exc:  # 损坏的持久化状态不能静默接受
        raise ConsistencyError("unreadable state.json: %r" % (exc,))

    if payload.get("format") != FORMAT:
        raise ConsistencyError("unsupported state format")
    entries = []
    for item in payload.get("entries", []):
        seq = int(item["seq"])
        expected = _seg_rel(seq)
        if item.get("file") != expected:
            raise ConsistencyError("non-canonical segment path for seq %d" % seq)
        entries.append(
            Entry(seq=seq, file=expected, size=int(item["size"]), crc32=int(item["crc32"]))
        )
    return CommittedState(
        generation=int(payload["generation"]),
        window=int(payload["window"]),
        next_seq=int(payload["next_seq"]),
        confirmed_seq=int(payload["confirmed_seq"]),
        entries=tuple(entries),
    )


def _entry_readable(root, entry):
    path = os.path.join(root, entry.file)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return False
    if len(data) != entry.size or _crc32(data) != entry.crc32:
        return False
    return _segment_seq(data) == entry.seq


def _attach_orphans(root, state, window):
    """状态文件损坏/缺失时的兜底：用完整可读的孤立片段重建窗口与序号。"""
    segdir = os.path.join(root, SEG_DIR)
    good = {}
    for path in glob.glob(os.path.join(segdir, "seg-*.bin")):
        seq = _seq_from_name(path)
        if seq is None:
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if _segment_seq(data) != seq:
            continue
        good[seq] = Entry(seq, _seg_rel(seq), len(data), _crc32(data))

    if not good:
        return state
    high = max(good)
    if window > 0:
        seqs = range(max(0, high - window + 1), high + 1)
    else:
        seqs = ()
    entries = tuple(good[s] for s in seqs if s in good)
    return CommittedState(
        generation=state.generation + 1,
        window=window,
        next_seq=high + 1,
        confirmed_seq=high,
        entries=entries,
    )


def _manifest_obj(state):
    return {
        "format": FORMAT,
        "generation": state.generation,
        "window": state.window,
        "confirmed_seq": state.confirmed_seq,
        "entries": [
            {
                "seq": e.seq,
                "file": e.file,
                "size": e.size,
                "crc32": e.crc32,
                "visible": True,
            }
            for e in state.entries
        ],
    }


def _write_manifest(root, state):
    _atomic_write(os.path.join(root, MANIFEST_NAME), _canonical(_manifest_obj(state)))


# ---------------------------------------------------------------------------
# 环形缓冲区
# ---------------------------------------------------------------------------
class Snapshot:
    """读者持有的不可变一致性快照；生命周期内 pin 住其片段文件。"""

    __slots__ = ("_buf", "generation", "entries", "_released")

    def __init__(self, buf, generation, entries):
        self._buf = buf
        self.generation = generation
        self.entries = entries
        self._released = False

    def read_seqs(self):
        """读取并校验快照内全部片段；任何失效引用立即报错。"""
        seqs = []
        for entry in self.entries:
            if not _entry_readable(self._buf.root, entry):
                raise DanglingReference(
                    "snapshot generation %d references unreadable %s"
                    % (self.generation, entry.file)
                )
            seqs.append(entry.seq)
        return seqs

    def release(self):
        if not self._released:
            self._released = True
            self._buf._unpin(self.entries)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


class CrashInjector:
    """在指定片段/时刻模拟进程死亡，并可留下部分写入文件残留。

    hard=True 时用 os._exit 直接终止进程（不做任何清理、不落 close 标记），
    用于验证真实的"进程被 kill"场景，而不只是异常路径。
    """

    HARD_EXIT_CODE = 70

    def __init__(self, at, phase, leave_partial=True, hard=False):
        self.at = int(at)
        self.phase = phase
        self.leave_partial = leave_partial
        self.hard = hard

    def hit(self, point, seq, buf):
        if self.phase == point and seq == self.at:
            if self.leave_partial:
                # 模拟写入下一片段时被中断，留下未完成的临时文件。
                partial = os.path.join(buf.segdir, "seg-%08d.bin.tmp" % (seq + 1))
                try:
                    with open(partial, "wb") as fh:
                        fh.write(MAGIC + b"partial-write")
                        fh.flush()
                except OSError:
                    pass
            if self.hard:
                os._exit(self.HARD_EXIT_CODE)
            raise SimulatedCrash("crash at seq=%d phase=%s" % (seq, point))


class DvrBuffer:
    def __init__(self, root, window, state, recovered, grace):
        self.root = root
        self.segdir = os.path.join(root, SEG_DIR)
        self.window = max(0, int(window))
        self._state = state
        self.recovered = recovered
        self.grace = max(0, int(grace))
        self._lock = threading.RLock()
        self._pins = {}          # file -> pin count
        self._removed_at = {}    # file -> generation when it left the window
        self._closed = False
        self.crash = None

    # -- 打开/恢复 -------------------------------------------------------
    @classmethod
    def open_fresh(cls, root, window, grace, reset=True):
        root = os.path.abspath(root)
        if reset and os.path.isdir(root):
            shutil.rmtree(root)
        os.makedirs(os.path.join(root, SEG_DIR), exist_ok=True)
        state = CommittedState(0, max(0, int(window)), 0, -1, ())
        _write_state(root, state)
        _write_manifest(root, state)
        marker = os.path.join(root, RUNNING_NAME)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_dir(root)
        return cls(root, window, state, recovered=False, grace=grace)

    @classmethod
    def open_recover(cls, root, window, grace):
        root = os.path.abspath(root)
        segdir = os.path.join(root, SEG_DIR)
        os.makedirs(segdir, exist_ok=True)
        marker = os.path.join(root, RUNNING_NAME)
        unclean = os.path.exists(marker)

        # 1) 中断写入留下的临时文件（片段、清单、状态）一律丢弃。
        for pattern in (
            os.path.join(segdir, "*.tmp*"),
            os.path.join(root, "*.tmp*"),
        ):
            for path in glob.glob(pattern):
                try:
                    os.unlink(path)
                except OSError:
                    pass

        # 2) 读取上一次已提交状态；状态文件损坏时按"未提交"处理并由片段兜底。
        damaged = False
        try:
            state = _read_state(root)
        except ConsistencyError:
            state = None
            damaged = True
        if state is None:
            state = CommittedState(0, max(0, int(window)), 0, -1, ())
            # 状态文件缺失但磁盘上有片段：按损坏处理，改用片段兜底，
            # 避免把已确认的片段当成孤儿删除。
            if glob.glob(os.path.join(segdir, "seg-*.bin")):
                damaged = True

        if damaged:
            # 仅当 state.json 缺失/损坏时才用磁盘片段兜底重建；正常的空窗口
            # （例如首次启动或 window=0）不能被"抬升"为已确认片段。
            adopted = _attach_orphans(root, state, max(0, int(window)))
            if adopted.entries or adopted.generation != state.generation:
                state = adopted

        # 3) 校验已提交片段；遇到损坏/断链则回滚到最后一个有效条目。
        repaired = False
        entries = list(state.entries)
        if len(entries) > state.window:
            entries = entries[-state.window:] if state.window > 0 else []
            repaired = True
        valid = []
        for entry in entries:
            if valid and entry.seq != valid[-1].seq + 1:
                repaired = True
                break
            if not _entry_readable(root, entry):
                repaired = True
                break
            valid.append(entry)

        if repaired:
            if valid:
                next_seq = entries[len(valid)].seq
            elif entries:
                next_seq = entries[0].seq
            else:
                next_seq = 0
            state = CommittedState(
                generation=state.generation + 1,
                window=state.window,
                next_seq=next_seq,
                confirmed_seq=next_seq - 1,
                entries=tuple(valid),
            )
        else:
            # 已提交状态自洽性修复（正常路径不会触发）。
            last = state.entries[-1].seq if state.entries else -1
            confirmed = max(state.confirmed_seq, last)
            next_seq = max(state.next_seq, confirmed + 1)
            if (confirmed, next_seq) != (state.confirmed_seq, state.next_seq):
                state = CommittedState(
                    state.generation + 1, state.window, next_seq,
                    confirmed, state.entries,
                )
                repaired = True

        # 若重启时请求的窗口与持久化窗口不同，以请求值为准收缩窗口；
        # 已确认序号不变，被移出窗口的片段随后按孤儿清理。
        effective = max(0, int(window))
        if state.window != effective:
            entries = list(state.entries)
            if effective == 0:
                entries = []
            elif len(entries) > effective:
                entries = entries[-effective:]
            state = CommittedState(
                generation=state.generation + 1,
                window=effective,
                next_seq=state.next_seq,
                confirmed_seq=state.confirmed_seq,
                entries=tuple(entries),
            )
            repaired = True

        live = {os.path.abspath(os.path.join(root, e.file)) for e in state.entries}
        # 4) 删除未被已提交状态引用的孤立/残留片段。
        for path in glob.glob(os.path.join(segdir, "seg-*.bin")):
            if os.path.abspath(path) not in live:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        _fsync_dir(segdir)

        # 5) 用恢复后的状态重建清单视图并落盘。
        _write_state(root, state)
        _write_manifest(root, state)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_dir(root)
        return cls(root, state.window, state,
                   recovered=(unclean or repaired or damaged),
                   grace=grace)

    @classmethod
    def open_resume(cls, root, window, grace):
        root = os.path.abspath(root)
        if os.path.exists(os.path.join(root, STATE_NAME)):
            return cls.open_recover(root, window, grace)
        return cls.open_fresh(root, window, grace, reset=False)

    # -- 属性 -------------------------------------------------------------
    @property
    def generation(self):
        with self._lock:
            return self._state.generation

    @property
    def next_seq(self):
        with self._lock:
            return self._state.next_seq

    @property
    def confirmed_seq(self):
        with self._lock:
            return self._state.confirmed_seq

    @property
    def committed_entries(self):
        with self._lock:
            return self._state.entries

    # -- 写入 -------------------------------------------------------------
    def append(self, seq):
        """按 seq 连续追加：片段可读 -> 清单原子提交 -> 发布新快照。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("buffer closed")
            state = self._state
            if seq != state.next_seq:
                raise ValueError("expected seq %d, got %d" % (state.next_seq, seq))

            data = _encode_segment(seq)
            rel = _seg_rel(seq)
            _atomic_write(os.path.join(self.root, rel), data)

            if self.crash is not None:
                self.crash.hit("after_segment", seq, self)

            entry = Entry(seq=seq, file=rel, size=len(data), crc32=_crc32(data))
            entries = list(state.entries)
            entries.append(entry)
            removed = []
            while len(entries) > self.window:
                removed.append(entries.pop(0))

            new_state = CommittedState(
                generation=state.generation + 1,
                window=self.window,
                next_seq=seq + 1,
                confirmed_seq=seq,
                entries=tuple(entries),
            )

            # 崩溃点：片段已落盘，但清单尚未提交 -> 新片段成为孤立文件，
            # 恢复时删除并重写；读者拿到的仍是旧代完整清单。
            if self.crash is not None:
                self.crash.hit("before_manifest", seq, self)

            _write_state(self.root, new_state)       # 提交点

            # 崩溃点：状态已提交、物化清单尚未刷新 -> 磁盘清单是上一代，
            # 但它引用的片段仍被保留（GC 在提交之后），因此依然完整可读。
            if self.crash is not None:
                self.crash.hit("after_state", seq, self)

            _write_manifest(self.root, new_state)    # 物化视图

            self._state = new_state
            for old in removed:
                self._removed_at[old.file] = new_state.generation

            # 崩溃点：清单已提交 -> 恢复后窗口即包含该片段。
            if self.crash is not None:
                self.crash.hit("after_manifest", seq, self)

            self._gc_locked()

    # -- 快照 -------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            state = self._state
            for entry in state.entries:
                self._pins[entry.file] = self._pins.get(entry.file, 0) + 1
            return Snapshot(self, state.generation, state.entries)

    def _unpin(self, entries):
        with self._lock:
            for entry in entries:
                left = self._pins.get(entry.file, 0) - 1
                if left > 0:
                    self._pins[entry.file] = left
                else:
                    self._pins.pop(entry.file, None)
            self._gc_locked()

    def _gc_locked(self):
        """延迟回收离开窗口且无人 pin 的旧片段。"""
        live = {entry.file for entry in self._state.entries}
        generation = self._state.generation
        untied = False
        for rel in list(self._removed_at):
            if rel in live:
                del self._removed_at[rel]
                continue
            if generation - self._removed_at[rel] < self.grace:
                continue
            if self._pins.get(rel, 0) > 0:
                continue
            try:
                os.unlink(os.path.join(self.root, rel))
            except FileNotFoundError:
                pass
            except OSError:
                continue
            del self._removed_at[rel]
            untied = True
        if untied:
            _fsync_dir(self.segdir)

    # -- 关闭 -------------------------------------------------------------
    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        marker = os.path.join(self.root, RUNNING_NAME)
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass
        _fsync_dir(self.root)


# ---------------------------------------------------------------------------
# 读者
# ---------------------------------------------------------------------------
def _readers_snapshot(buf, count):
    count = max(0, int(count))
    if count == 0:
        return {}
    barrier = threading.Barrier(count)
    results = {}
    errors = {}
    guard = threading.Lock()

    def worker(idx):
        snap = None
        try:
            barrier.wait(timeout=10.0)
            snap = buf.snapshot()
            seqs = snap.read_seqs()
            with guard:
                results[idx] = seqs
        except BaseException as exc:  # noqa: BLE001 - 收集给主线程
            with guard:
                errors[idx] = repr(exc)
        finally:
            if snap is not None:
                snap.release()

    threads = [
        threading.Thread(target=worker, args=(i,), name="dvr-reader-%d" % i)
        for i in range(count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        raise ConsistencyError("reader snapshot failed: %r" % (errors,))
    seq_lists = [results[i] for i in range(count)]
    if any(seqs != seq_lists[0] for seqs in seq_lists[1:]):
        raise ConsistencyError("concurrent readers observed different snapshots")
    return {str(i): seq_lists[i] for i in range(count)}


def _boundary_stress(window, segs, readers, speed, base_delay, grace):
    """2 倍速窗口滑动 + 多读者持续取快照，验证边界无失效引用且快照一致。"""
    root = tempfile.mkdtemp(prefix="dvr-stress-")
    buf = DvrBuffer.open_fresh(root, window, grace, reset=True)
    stop = threading.Event()
    guard = threading.Lock()
    violations = []
    seen = {}            # generation -> {reader: tuple(seqs)}
    reads = [0]
    delay = base_delay / max(speed, 1.0) if base_delay > 0 else 0.0
    delay = max(delay, 0.0002)

    def reader(idx):
        while not stop.is_set():
            snap = None
            try:
                snap = buf.snapshot()
                seqs = tuple(snap.read_seqs())
                with guard:
                    reads[0] += 1
                    seen.setdefault(snap.generation, {})[idx] = seqs
            except DanglingReference as exc:
                with guard:
                    violations.append(str(exc))
            finally:
                if snap is not None:
                    snap.release()
            time.sleep(0)

    threads = [
        threading.Thread(target=reader, args=(i,), name="dvr-stress-%d" % i)
        for i in range(max(1, readers))
    ]
    for thread in threads:
        thread.start()
    try:
        for seq in range(segs):
            buf.append(seq)
            time.sleep(delay)
        # 写者停下后，等所有读者都观测到最后一代，确保共享快照判定确定。
        deadline = time.time() + 2.0
        final_gen = buf.generation
        while time.time() < deadline:
            with guard:
                if len(seen.get(final_gen, {})) >= len(threads):
                    break
            time.sleep(0.001)
    finally:
        stop.set()
        for thread in threads:
            thread.join()
        buf.close()
        shutil.rmtree(root, ignore_errors=True)

    with guard:
        shared = sum(
            1
            for readers_at_gen in seen.values()
            if len(readers_at_gen) == len(threads)
            and len(set(readers_at_gen.values())) == 1
        )
    if violations:
        raise ConsistencyError("boundary readers saw dangling refs: %r" % violations[:5])
    if shared < 1:
        raise ConsistencyError("no concurrent shared snapshot observed")
    return reads[0], shared


# ---------------------------------------------------------------------------
# 场景驱动
# ---------------------------------------------------------------------------
PHASES = ("after_segment", "before_manifest", "after_state", "after_manifest")
_ALIASES = {
    "segment": "after_segment",
    "after_segment": "after_segment",
    "write": "after_segment",
    "before_manifest": "before_manifest",
    "pre_manifest": "before_manifest",
    "after_state": "after_state",
    "state": "after_state",
    "after_manifest": "after_manifest",
    "post_manifest": "after_manifest",
}


def _normalize_phase(value):
    key = str(value).strip().lower()
    if key in ("none", "off", "no"):
        return "none"
    if key not in _ALIASES:
        raise ValueError("unknown crash_phase: %r" % (value,))
    return _ALIASES[key]


def _write_range(buf, start, end, delay):
    for seq in range(start, end):
        buf.append(seq)
        if delay > 0:
            time.sleep(delay)


def _final_seqs(buf):
    with buf.snapshot() as snap:
        return snap.read_seqs()


def _run_case(req, crash_at, phase, data_dir, resume=False):
    """运行一次写入场景，返回 (buffer, recovered, final_seqs, resumed_from)。"""
    segs = max(0, int(req.get("segs", 0)))
    window = max(0, int(req.get("window", 8)))
    grace = max(0, int(req.get("grace", window)))
    speed = float(req.get("speed", 1.0) or 1.0)
    base_delay = float(req.get("segment_delay", 0.0))
    delay = base_delay / max(speed, 1.0) if base_delay > 0 else 0.0

    if resume:
        buf = DvrBuffer.open_resume(data_dir, window, grace)
        start = buf.next_seq
        _write_range(buf, start, segs, delay)
        return buf, bool(buf.recovered), _final_seqs(buf), start

    if req.get("hard_crash") and crash_at is not None and phase not in (None, "none"):
        # 真实进程死亡：子进程在崩溃点直接 os._exit，父进程随后恢复。
        child_req = dict(req)
        child_req["state_dir"] = data_dir
        child_req["crash_at"] = crash_at
        child_req["crash_phase"] = phase
        child_req["_write_only"] = True
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="dvr-child-", delete=False
        ) as fh:
            json.dump(child_req, fh)
            child_path = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, "-B", os.path.abspath(__file__), "--write", child_path],
                capture_output=True, text=True, timeout=120,
            )
        finally:
            try:
                os.unlink(child_path)
            except OSError:
                pass
        if proc.returncode == 0:
            raise ConsistencyError(
                "hard crash at seq=%d phase=%s never triggered" % (crash_at, phase)
            )
        if proc.returncode != CrashInjector.HARD_EXIT_CODE:
            raise ConsistencyError(
                "hard crash child failed abnormally: rc=%d stderr=%r"
                % (proc.returncode, proc.stderr[-500:])
            )
        buf = DvrBuffer.open_recover(data_dir, window, grace)
        resumed_from = buf.next_seq
        _write_range(buf, resumed_from, segs, delay)
        return buf, bool(buf.recovered), _final_seqs(buf), resumed_from

    buf = DvrBuffer.open_fresh(data_dir, window, grace, reset=True)
    if crash_at is not None and phase not in (None, "none"):
        buf.crash = CrashInjector(crash_at, phase)
    try:
        _write_range(buf, 0, segs, delay)
    except SimulatedCrash:
        buf.crash = None
        buf._closed = True          # 模拟进程死亡：不做清理、不写 close 标记。
        buf = DvrBuffer.open_recover(data_dir, window, grace)
        resumed_from = buf.next_seq
        _write_range(buf, resumed_from, segs, delay)
        return buf, bool(buf.recovered), _final_seqs(buf), resumed_from

    if buf.next_seq != segs:
        raise ConsistencyError("next_seq %d != segs %d after run" % (buf.next_seq, segs))
    return buf, False, _final_seqs(buf), 0


def _checked_manifest(root):
    """从磁盘清单读取，并逐个验证片段可读。"""
    path = os.path.join(root, MANIFEST_NAME)
    with open(path, "rb") as fh:
        obj = json.loads(fh.read().decode("utf-8"))
    result = []
    dangling = 0
    for item in obj.get("entries", []):
        seq = int(item["seq"])
        entry = Entry(
            seq=seq,
            file=_seg_rel(seq),
            size=int(item["size"]),
            crc32=int(item["crc32"]),
        )
        readable = _entry_readable(root, entry)
        result.append({"seq": seq, "visible": readable})
        if not readable:
            dangling += 1
    return result, dangling


def _coerce_crash_at(value, segs):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value < 0 or value >= segs:
        return None
    return value


def run(req):
    segs = max(0, int(req.get("segs", 0)))
    window = max(0, int(req.get("window", 8)))
    readers = max(0, int(req.get("readers", 3)))
    speed = float(req.get("speed", 1.0) or 1.0)
    grace = max(0, int(req.get("grace", window)))
    crash_at = _coerce_crash_at(req.get("crash_at"), segs)

    data_dir = req.get("state_dir") or os.path.join(os.getcwd(), ".dvr_state")
    data_dir = os.path.abspath(data_dir)

    # 无崩溃基线：用于校验任意崩溃点恢复后的窗口条目完全一致。
    baseline_dir = tempfile.mkdtemp(prefix="dvr-baseline-")
    baseline_buf, _, baseline_seqs, _ = _run_case(req, None, None, baseline_dir)
    baseline_buf.close()
    shutil.rmtree(baseline_dir, ignore_errors=True)

    # 选择崩溃点集合。默认覆盖三种时刻：片段写完后、清单更新前、更新后。
    explicit_phases = req.get("crash_phases")
    explicit_phase = req.get("crash_phase")
    if req.get("resume") or crash_at is None:
        phases = ["none"]
    elif explicit_phases:
        if isinstance(explicit_phases, str):
            explicit_phases = [explicit_phases]
        phases = [_normalize_phase(p) for p in explicit_phases]
    elif explicit_phase is not None:
        phases = [_normalize_phase(explicit_phase)]
    else:
        phases = list(PHASES)

    main_phase = phases[0]
    buf, recovered, final_seqs, resumed_from = _run_case(
        req, crash_at, main_phase, data_dir, resume=bool(req.get("resume"))
    )
    if req.get("resume") and not 0 <= resumed_from <= segs:
        # 续写起点必须落在 [0, segs]，不能重放已确认片段或越过目标。
        raise ConsistencyError("bad resume point %d" % resumed_from)

    if final_seqs != baseline_seqs:
        raise ConsistencyError(
            "recovered window %r != baseline %r" % (final_seqs, baseline_seqs)
        )
    if crash_at is not None and main_phase != "none" and not recovered:
        raise ConsistencyError("expected a recovery after simulated crash")

    # 其余崩溃点在同配置的临时目录逐项验证，最终条目必须与基线相同。
    for phase in phases[1:]:
        if phase == "none":
            continue
        extra_dir = tempfile.mkdtemp(prefix="dvr-crash-")
        try:
            extra_buf, extra_recovered, extra_seqs, _ = _run_case(
                req, crash_at, phase, extra_dir
            )
            extra_buf.close()
            if extra_seqs != baseline_seqs:
                raise ConsistencyError(
                    "crash phase %s window %r != baseline %r"
                    % (phase, extra_seqs, baseline_seqs)
                )
            if not extra_recovered:
                raise ConsistencyError("crash phase %s did not recover" % phase)
        finally:
            shutil.rmtree(extra_dir, ignore_errors=True)

    # 验收 2：2 倍速滑动 200 个片段，多读者在窗口边界持续读取。
    if req.get("stress_segs") is not None:
        stress_segs = max(1, int(req.get("stress_segs")))
    else:
        stress_segs = max(200, segs)
    stress_segs = min(stress_segs, max(segs, 200))
    if req.get("stress", True) and stress_segs > 0 and window > 0:
        _boundary_stress(
            window=window,
            segs=stress_segs,
            readers=max(1, readers),
            speed=speed,
            base_delay=float(req.get("stress_delay", 0.0005)),
            grace=grace,
        )

    # 终态并发读者：共享同一代一致快照。
    snapshots = _readers_snapshot(buf, readers)
    for snapshot_seqs in snapshots.values():
        if snapshot_seqs != final_seqs:
            raise ConsistencyError(
                "reader snapshot %r != final window %r"
                % (snapshot_seqs, final_seqs)
            )

    manifest, dangling = _checked_manifest(buf.root)
    if dangling != 0:
        raise ConsistencyError("manifest exposes %d unreadable entries" % dangling)
    if [item["seq"] for item in manifest] != final_seqs:
        raise ConsistencyError("manifest on disk != committed window")

    buf.close()
    return {
        "manifest": manifest,
        "dangling": dangling,
        "recovered": bool(recovered),
        "snapshots": snapshots,
    }


def write_only(req):
    """子进程入口：只按顺序写入，崩溃由 CrashInjector 用 os._exit 触发。"""
    segs = max(0, int(req.get("segs", 0)))
    window = max(0, int(req.get("window", 8)))
    grace = max(0, int(req.get("grace", window)))
    data_dir = os.path.abspath(
        req.get("state_dir") or os.path.join(os.getcwd(), ".dvr_state")
    )
    crash_at = req.get("crash_at")
    phase = _normalize_phase(req.get("crash_phase", "none"))

    if req.get("resume"):
        buf = DvrBuffer.open_resume(data_dir, window, grace)
    else:
        buf = DvrBuffer.open_fresh(data_dir, window, grace, reset=True)
    if crash_at is not None and phase != "none":
        buf.crash = CrashInjector(crash_at, phase,
                                  hard=bool(req.get("hard_crash", True)))
    speed = float(req.get("speed", 1.0) or 1.0)
    base_delay = float(req.get("segment_delay", 0.0))
    delay = base_delay / max(speed, 1.0) if base_delay > 0 else 0.0
    _write_range(buf, buf.next_seq, segs, delay)
    buf.close()
    return 0


def main(argv):
    if len(argv) == 3 and argv[1] == "--write":
        with open(argv[2], "r", encoding="utf-8") as fh:
            return write_only(json.load(fh))
    if len(argv) != 3:
        sys.stderr.write("usage: python3 dvr.py req.json output.json\n")
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        req = json.load(fh)
    result = run(req)
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
