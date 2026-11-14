"""Resumable, crash-safe local object landing.

语义（与 README 对齐）:
- 每个分片声明绑定对象代次(version)、字节半开范围(start,end)与范围摘要(sha256)。
- 片落盘为内容寻址的暂存片；通过范围校验后才登记进每对象范围注册表。
- 重叠范围仅在内容逐字节相等时合并，否则隔离(overlap-conflict)。
- 短读/错误内容最多用 retry 重试一次；版本与范围声明错误不可重试。
- 全对象长度与整对象摘要通过后，才按代次原子发布；旧代次始终可读。
- 重启后复核所有已登记范围的边界与摘要，复用可确认的片，清理未确认暂存片。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量与异常
# ---------------------------------------------------------------------------
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
POINTER = "current.json"
GEN_RESULT = "result.json"
MANIFEST_FILE = "manifest.json"
STATUS_FILE = "status.json"

# 隔离原因码（稳定值，写入持久层与测试断言）
R_VERSION_CONFLICT = "version-conflict"
R_BAD_RANGE = "bad-range"
R_SHORT_READ = "short-read"
R_CHECKSUM = "checksum-mismatch"
R_OVERLAP = "overlap-conflict"
R_MISSING = "missing-range"
R_LENGTH = "length-mismatch"
R_DIGEST = "digest-mismatch"
R_DUPLICATE_ID = "duplicate-id"


class ManifestError(ValueError):
    """清单/声明本身不合法（不可重试）。"""


class QuarantineError(Exception):
    """对象处理中出现需要隔离的确定性故障。"""

    def __init__(self, reason, detail=""):
        super().__init__(QuarantineError._message(reason, detail))
        self.reason = reason
        self.detail = detail

    @staticmethod
    def _message(reason, detail):
        return reason + (": " + detail if detail else "")


class CrashPlan:
    """进程内一次性崩溃注入点，用于验收故障测试。"""

    def __init__(self, point=None):
        self.point = point
        self._fired = False
        self._lock = threading.Lock()

    def arm(self, point):
        """标记崩溃点已到达（可在锁内调用），返回是否应由调用方触发。"""
        if self.point != point:
            return False
        with self._lock:
            if self._fired:
                return False
            self._fired = True
            return True

    def armed(self):
        with self._lock:
            return self._fired

    def fire(self):
        # 显式刷盘由原子写保证；以 97 退出模拟进程被杀死
        os._exit(97)


# ---------------------------------------------------------------------------
# 原子 IO 工具
# ---------------------------------------------------------------------------
def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path, data):
    """同目录临时文件 + fsync + rename，保证读者只见完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp." + str(os.getpid()) + "." +
                         str(threading.current_thread().ident))
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def atomic_write_json(path, obj):
    payload = json.dumps(obj, sort_keys=True) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def safe_segment(value, what):
    if not isinstance(value, str) or not SAFE_SEGMENT.match(value):
        raise ManifestError("unsafe " + what + ": " + repr(value))
    return value


# ---------------------------------------------------------------------------
# 清单校验
# ---------------------------------------------------------------------------
def _require_int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(what + " must be integer")
    return value


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ManifestError("manifest must be an object")
    generation = safe_segment(data.get("generation"), "generation")
    objs = data.get("objects")
    if not isinstance(objs, list):
        raise ManifestError("objects must be a list")
    seen_ids = set()
    clean = []
    for obj in objs:
        if not isinstance(obj, dict):
            raise ManifestError("object entry must be an object")
        oid = safe_segment(obj.get("id"), "object id")
        if oid in seen_ids:
            raise ManifestError(R_DUPLICATE_ID + ": " + oid)
        seen_ids.add(oid)
        version = safe_segment(obj.get("version"), "object version")
        length = _require_int(obj.get("length"), "length")
        if length < 0:
            raise ManifestError("length must be >= 0")
        digest = obj.get("sha256")
        if not isinstance(digest, str) or not HEX64.match(digest):
            raise ManifestError("object sha256 invalid for " + oid)
        chunks = obj.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            raise ManifestError("chunks must be a non-empty list for " + oid)
        clean_chunks = []
        decl_keys = set()
        for ch in chunks:
            if not isinstance(ch, dict):
                raise ManifestError("chunk entry must be an object")
            cver = safe_segment(ch.get("version"), "chunk version")
            start = _require_int(ch.get("start"), "start")
            end = _require_int(ch.get("end"), "end")
            cdigest = ch.get("sha256")
            if not isinstance(cdigest, str) or not HEX64.match(cdigest):
                raise ManifestError("chunk sha256 invalid for " + oid)
            if not isinstance(ch.get("payload"), str):
                raise ManifestError("chunk payload must be base64 string for " + oid)
            if "retry" in ch and not isinstance(ch["retry"], str):
                raise ManifestError("chunk retry must be base64 string for " + oid)
            key = (start, end, cdigest)
            if key in decl_keys:
                raise ManifestError("duplicate chunk declaration in " + oid +
                                    ": " + repr(key))
            decl_keys.add(key)
            clean_chunks.append({
                "version": cver,
                "start": start,
                "end": end,
                "sha256": cdigest,
                "payload": ch["payload"],
                "retry": ch.get("retry"),
            })
        clean.append({
            "id": oid,
            "version": version,
            "length": length,
            "sha256": digest,
            "chunks": clean_chunks,
        })
    return {"generation": generation, "objects": clean}


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------
class Store:
    """管理 ROOT 下已发布区与暂存区的所有持久化。"""

    def __init__(self, root):
        self.root = Path(root)
        self.staging = self.root / "staging"
        self.pointer_path = self.root / POINTER

    # ---- 指针 -----------------------------------------------------------
    def read_pointer(self):
        if self.pointer_path.exists():
            return read_json(self.pointer_path)
        return {"generation": None, "published": [], "quarantine": [],
                "generations": []}

    def write_pointer(self, pointer):
        atomic_write_json(self.pointer_path, pointer)

    # ---- 代次暂存 -------------------------------------------------------
    def gen_dir(self, generation):
        return self.staging / generation

    def gen_manifest_path(self, generation):
        return self.gen_dir(generation) / MANIFEST_FILE

    def gen_status_path(self, generation):
        return self.gen_dir(generation) / STATUS_FILE

    def gen_result_path(self, generation):
        return self.gen_dir(generation) / GEN_RESULT

    def object_dir(self, generation, oid):
        return self.gen_dir(generation) / "objects" / oid

    def piece_path(self, generation, oid, digest):
        return self.object_dir(generation, oid) / "pieces" / (digest + ".part")

    def range_dir(self, generation, oid):
        return self.object_dir(generation, oid) / "ranges"

    def range_path(self, generation, oid, start, end, digest):
        name = "%012d-%012d-%s.range" % (start, end, digest)
        return self.range_dir(generation, oid) / name

    def published_file(self, generation, oid):
        return self.root / generation / oid

    # ---- 每对象状态 -----------------------------------------------------
    def load_status(self, generation, oid):
        path = self.gen_status_path(generation)
        if path.exists():
            try:
                doc = read_json(path)
                if isinstance(doc, dict) and oid in doc:
                    return doc[oid]
            except (OSError, ValueError):
                pass
        return {"ranges": [], "failed": {}, "state": "open",
                "quarantine": None, "verified": [], "retries": 0}

    def save_status(self, generation, oid, status):
        path = self.gen_status_path(generation)
        doc = read_json(path) if path.exists() else {}
        if not isinstance(doc, dict):
            doc = {}
        doc[oid] = status
        atomic_write_json(path, doc)

    def save_gen_manifest(self, manifest):
        atomic_write_json(self.gen_manifest_path(manifest["generation"]), manifest)

    def load_gen_manifest(self, generation):
        path = self.gen_manifest_path(generation)
        return read_json(path) if path.exists() else None

    # ---- 暂存片 ---------------------------------------------------------
    def write_piece(self, generation, oid, digest, data):
        path = self.piece_path(generation, oid, digest)
        if path.exists() and path.read_bytes() == data:
            return False  # 已存在且内容一致
        atomic_write_bytes(path, data)
        return True

    def write_range(self, generation, oid, start, end, digest, data):
        """范围登记：独立保存已验证数据，不依赖暂存片。

        登记成功后，落盘暂存片即不再被注册表引用，立即清理。
        返回该次是否产生了新的登记（用于 after-range 崩溃判定）。
        """
        rpath = self.range_path(generation, oid, start, end, digest)
        registered = not rpath.exists()
        if registered:
            atomic_write_bytes(rpath, data)
        piece = self.piece_path(generation, oid, digest)
        if piece.exists():
            try:
                piece.unlink()
            except FileNotFoundError:
                pass
        return registered

    def sweep_unreferenced_pieces(self, generation, oid, referenced):
        """清理未被已登记范围引用的暂存片。"""
        pdir = self.object_dir(generation, oid) / "pieces"
        if not pdir.exists():
            return
        for entry in pdir.iterdir():
            if entry.is_file() and entry.name.endswith(".part"):
                if entry.name[:-5] not in referenced:
                    try:
                        entry.unlink()
                    except FileNotFoundError:
                        pass

    def sweep_orphan_pieces(self, generation):
        """重启复核：删除任何没有对应范围登记的暂存片（未确认片）。"""
        objs_dir = self.gen_dir(generation) / "objects"
        if not objs_dir.exists():
            return
        for odir in objs_dir.iterdir():
            if not odir.is_dir():
                continue
            pdir = odir / "pieces"
            rdir = odir / "ranges"
            registered = set()
            if rdir.exists():
                for rf in rdir.iterdir():
                    stem = rf.name[:-6] if rf.name.endswith(".range") else rf.name
                    digest = stem.split("-")[-1]
                    registered.add(digest)
            if pdir.exists():
                for pf in list(pdir.iterdir()):
                    if pf.is_file() and pf.name.endswith(".part"):
                        if pf.name[:-5] not in registered:
                            try:
                                pf.unlink()
                            except FileNotFoundError:
                                pass


# ---------------------------------------------------------------------------
# 片读取与范围校验
# ---------------------------------------------------------------------------
def _b64_decode(text):
    if not isinstance(text, str):
        raise QuarantineError(R_SHORT_READ, "payload not a string")
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise QuarantineError(R_SHORT_READ, "bad base64: " + str(exc))


def read_piece(chunk, allow_retry):
    """按声明读取一个片。

    返回 (data, retried)。声明类错误（范围/版本）由上层先行判定；
    这里只处理载荷短读与摘要错误，retry 至多使用一次。
    """
    start, end = chunk["start"], chunk["end"]
    declared = end - start
    data = _b64_decode(chunk["payload"])
    retried = False
    if len(data) != declared or sha256_hex(data) != chunk["sha256"]:
        if allow_retry and chunk.get("retry") is not None:
            data = _b64_decode(chunk["retry"])
            retried = True
        else:
            if len(data) != declared:
                raise QuarantineError(R_SHORT_READ,
                                      "got %d bytes, want %d" % (len(data), declared))
            raise QuarantineError(R_CHECKSUM, "range digest mismatch on payload")
    if len(data) != declared:
        raise QuarantineError(R_SHORT_READ,
                              "retry got %d bytes, want %d" % (len(data), declared))
    if sha256_hex(data) != chunk["sha256"]:
        raise QuarantineError(R_CHECKSUM, "range digest mismatch on retry")
    return data, retried


# ---------------------------------------------------------------------------
# 范围合并：检测重叠冲突与缺片
# ---------------------------------------------------------------------------
def merge_ranges(ranges, total_length):
    """ranges: list[dict(start,end,sha256,data)]，按 start 排序后归并。

    - 部分重叠但内容不同 -> overlap-conflict
    - 重叠且内容相等 -> 合并为同一覆盖
    - 未覆盖位置 -> missing-range
    返回拼好的 bytes（若完整）。
    """
    if not ranges:
        raise QuarantineError(R_MISSING, "no verified range")
    ordered = sorted(ranges, key=lambda r: (r["start"], r["end"]))
    buffer = bytearray()
    cursor = 0
    for rng in ordered:
        start, end = rng["start"], rng["end"]
        if end <= cursor:
            # 完全落在已有覆盖内，内容必须一致
            overlap = bytes(rng["data"][: end - start])
            existing = bytes(buffer[start:end])
            if overlap != existing:
                raise QuarantineError(R_OVERLAP,
                                      "conflicting duplicate range %d:%d" % (start, end))
            continue
        if start > cursor:
            raise QuarantineError(R_MISSING, "gap %d:%d" % (cursor, start))
        if start < cursor:
            # 部分重叠：重叠区逐字节比对
            shared = cursor - start
            if bytes(rng["data"][:shared]) != bytes(buffer[start:cursor]):
                raise QuarantineError(R_OVERLAP,
                                      "conflicting overlap near %d" % cursor)
            buffer.extend(rng["data"][shared:])
        else:
            buffer.extend(rng["data"])
        cursor = end
    if cursor < total_length:
        raise QuarantineError(R_MISSING,
                              "tail gap %d:%d" % (cursor, total_length))
    if cursor > total_length:
        raise QuarantineError(R_LENGTH,
                              "coverage %d > length %d" % (cursor, total_length))
    return bytes(buffer)


# ---------------------------------------------------------------------------
# 单对象落地
# ---------------------------------------------------------------------------
class ObjectLander:
    # 串行化“片落盘 -> 范围登记”，保证并发下崩溃点状态确定，
    # 同时避免不同对象在崩溃窗口内交叉写入未确认片
    _io_turnstile = threading.Lock()

    def __init__(self, store, manifest_obj, generation, crash,
                 counters, counter_lock):
        self.store = store
        self.obj = manifest_obj
        self.generation = generation
        self.oid = manifest_obj["id"]
        self.crash = crash
        self.counters = counters
        self.counter_lock = counter_lock

    def _bump(self, key, amount=1):
        with self.counter_lock:
            self.counters[key] = self.counters.get(key, 0) + amount

    # -- 声明级校验（不可重试） ------------------------------------------
    def _validate_declarations(self):
        if self.obj["length"] < 0:
            raise QuarantineError(R_BAD_RANGE, "negative length")
        for ch in self.obj["chunks"]:
            if ch["version"] != self.obj["version"]:
                raise QuarantineError(
                    R_VERSION_CONFLICT,
                    "chunk version %s != object %s" % (ch["version"],
                                                       self.obj["version"]))
            start, end = ch["start"], ch["end"]
            if isinstance(start, bool) or isinstance(end, bool) or start < 0 \
                    or end <= start or end > self.obj["length"]:
                raise QuarantineError(R_BAD_RANGE,
                                      "illegal range %d:%d" % (start, end))

    # -- 重启复核已登记范围 ----------------------------------------------
    def _reload_verified_ranges(self):
        rdir = self.store.range_dir(self.generation, self.oid)
        verified = []
        if rdir.exists():
            for rf in sorted(rdir.iterdir()):
                if not rf.is_file() or not rf.name.endswith(".range"):
                    continue
                data = rf.read_bytes()
                stem = rf.name[:-6]
                parts = stem.split("-")
                digest = parts[-1]
                start = int(parts[0])
                end = int(parts[1])
                if sha256_hex(data) != digest or len(data) != end - start:
                    # 复核失败的登记片：删除，等待重新获取
                    rf.unlink()
                    continue
                verified.append({"start": start, "end": end,
                                 "sha256": digest, "data": data})
        return verified

    def run(self):
        oid = self.oid
        status = self.store.load_status(self.generation, oid)
        # 已隔离对象：直接返回持久化结论，重启不再重复获取/重试
        if status.get("state") == "quarantined":
            return {"outcome": "quarantined",
                    "id": oid,
                    "reason": status["quarantine"]["reason"],
                    "detail": status["quarantine"].get("detail", ""),
                    "verified": status.get("verified", [])}

        try:
            self._validate_declarations()
        except QuarantineError as err:
            return self._quarantine(status, err)

        # 重启：复核边界与摘要，复用可确认的已登记范围
        verified = self._reload_verified_ranges()
        have_keys = {(v["start"], v["end"], v["sha256"]) for v in verified}
        if verified:
            self._bump("reused_ranges", len(verified))

        failed = dict(status.get("failed", {}))
        retries = int(status.get("retries", 0))

        # 仍需获取的声明片
        todo = [ch for ch in self.obj["chunks"]
                if (ch["start"], ch["end"], ch["sha256"]) not in have_keys]

        for ch in todo:
            decl_key = "%d:%d:%s" % (ch["start"], ch["end"], ch["sha256"])
            allow_retry = decl_key not in failed and ch.get("retry") is not None
            try:
                data, used_retry = read_piece(ch, allow_retry)
            except QuarantineError as err:
                failed[decl_key] = {"reason": err.reason, "detail": err.detail}
                status["failed"] = failed
                self.store.save_status(self.generation, oid, status)
                return self._quarantine(status, err)
            if used_retry:
                retries += 1
                self._bump("retried_chunks")
            # 临界区覆盖“片落盘 -> 范围登记 -> 暂存片清理”。
            # 崩溃在锁内标记、锁外触发：
            #  - after-piece 标记后，其他线程在临界区入口即停止落盘，
            #    磁盘上恰有 1 个已落盘未登记的未确认片；
            #  - after-range 在登记并清理后标记，磁盘上 0 个未确认片。
            pending_after_piece = False
            pending_after_range = False
            with ObjectLander._io_turnstile:
                if self.crash.armed():
                    # 已有崩溃待触发，不再制造新的片/登记
                    break
                wrote = self.store.write_piece(
                    self.generation, oid, ch["sha256"], data)
                if wrote and self.crash.arm("after-piece"):
                    pending_after_piece = True
                else:
                    registered = self.store.write_range(
                        self.generation, oid, ch["start"], ch["end"],
                        ch["sha256"], data)
                    if registered and self.crash.arm("after-range"):
                        pending_after_range = True
            if pending_after_piece:
                # after-piece：首个分片落盘后、范围登记之前
                self.crash.fire()
            if pending_after_range:
                # after-range：首个范围登记完成后
                self.crash.fire()
            verified.append({"start": ch["start"], "end": ch["end"],
                             "sha256": ch["sha256"], "data": data})
            have_keys.add((ch["start"], ch["end"], ch["sha256"]))
            self._bump("fetched_ranges")
            status["ranges"] = sorted(
                [list(k) for k in have_keys],
                key=lambda t: (t[0], t[1], t[2]))
            status["retries"] = retries
            self.store.save_status(self.generation, oid, status)

        # 清理本对象未确认的暂存片（其摘要不在已登记范围内）
        self.store.sweep_unreferenced_pieces(
            self.generation, oid, set(v["sha256"] for v in verified))

        # 全对象长度 + 整对象摘要校验
        try:
            assembled = merge_ranges(verified, self.obj["length"])
        except QuarantineError as err:
            return self._quarantine(status, err)
        if len(assembled) != self.obj["length"]:
            return self._quarantine(
                status, QuarantineError(
                    R_LENGTH,
                    "%d != %d" % (len(assembled), self.obj["length"])))
        if sha256_hex(assembled) != self.obj["sha256"]:
            return self._quarantine(status,
                                    QuarantineError(R_DIGEST, "object digest mismatch"))

        status["state"] = "verified"
        status["verified"] = [
            {"start": v["start"], "end": v["end"], "sha256": v["sha256"]}
            for v in sorted(verified, key=lambda v: (v["start"], v["end"]))]
        status["retries"] = retries
        self.store.save_status(self.generation, oid, status)
        return {"outcome": "published", "id": oid, "data": assembled,
                "length": len(assembled), "sha256": self.obj["sha256"],
                "verified": status["verified"], "retries": retries}

    def _quarantine(self, status, err):
        status["state"] = "quarantined"
        status["quarantine"] = {"reason": err.reason, "detail": err.detail}
        self.store.save_status(self.generation, self.oid, status)
        return {"outcome": "quarantined", "id": self.oid,
                "reason": err.reason, "detail": err.detail,
                "verified": status.get("verified", [])}


# ---------------------------------------------------------------------------
# 代次发布
# ---------------------------------------------------------------------------
def publish_generation(store, generation, object_results):
    """全部对象校验完成后原子发布；旧代次目录与指针保留可读。"""
    pub_dir = store.root / generation
    pub_dir.mkdir(parents=True, exist_ok=True)
    published, quarantine = [], []
    for res in sorted(object_results, key=lambda r: r["id"]):
        if res["outcome"] == "published":
            target = store.published_file(generation, res["id"])
            atomic_write_bytes(target, res["data"])
            published.append({"id": res["id"], "length": res["length"],
                              "sha256": res["sha256"]})
        else:
            quarantine.append({"id": res["id"], "reason": res["reason"],
                               "detail": res.get("detail", "")})
    result = {"generation": generation,
              "published": published,
              "quarantine": quarantine}
    # 代次结果先落盘（旧代次独立可读，不依赖当前指针）
    atomic_write_json(store.gen_result_path(generation), result)

    pointer = store.read_pointer()
    generations = list(pointer.get("generations", []))
    if generation not in generations:
        generations.append(generation)
    new_pointer = {"generation": generation,
                   "published": published,
                   "quarantine": quarantine,
                   "generations": generations}
    store.write_pointer(new_pointer)
    return result


# ---------------------------------------------------------------------------
# land 主流程
# ---------------------------------------------------------------------------
def land(root, raw_manifest, workers, crash_point=None):
    manifest = validate_manifest(raw_manifest)
    generation = manifest["generation"]
    store = Store(root)
    store.root.mkdir(parents=True, exist_ok=True)
    store.staging.mkdir(parents=True, exist_ok=True)
    crash = CrashPlan(crash_point)
    workers = max(1, int(workers))

    pointer = store.read_pointer()
    existing = store.load_gen_manifest(generation)
    if existing is not None:
        if json.dumps(existing, sort_keys=True) != json.dumps(manifest,
                                                                sort_keys=True):
            raise ManifestError(
                "generation " + generation +
                " already landed with a different manifest")
    else:
        store.save_gen_manifest(manifest)

    # 已发布代次：同清单重复 land 幂等，直接返回持久化结果
    if generation in pointer.get("generations", []):
        result = read_json(store.gen_result_path(generation))
        result["fetched_ranges"] = 0
        result["reused_ranges"] = 0
        result["retried_chunks"] = 0
        return result

    # 重启复核：清理所有未确认暂存片
    store.sweep_orphan_pieces(generation)

    counters = {"fetched_ranges": 0, "reused_ranges": 0, "retried_chunks": 0}
    counter_lock = threading.Lock()
    landers = [ObjectLander(store, obj, generation, crash, counters, counter_lock)
               for obj in manifest["objects"]]

    results = []
    if workers == 1 or len(landers) <= 1:
        for lander in landers:
            results.append(lander.run())
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for res in pool.map(lambda l: l.run(), landers):
                results.append(res)

    # before-pointer：所有对象均已校验、数据与代次结果就绪，指针未写
    if crash.arm("before-pointer"):
        crash.fire()
    result = publish_generation(store, generation, results)
    result.update(counters)
    return result


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------
def inspect(root, generation=None):
    store = Store(root)
    pointer = store.read_pointer()
    published_gens = pointer.get("generations", [])
    if generation is None:
        return {"generation": pointer.get("generation"),
                "published": pointer.get("published", []),
                "quarantine": pointer.get("quarantine", [])}
    if generation not in published_gens:
        # 未公开代次不可读
        raise ManifestError("generation not published: " + generation)
    result = read_json(store.gen_result_path(generation))
    return {"generation": result["generation"],
            "published": result["published"],
            "quarantine": result["quarantine"]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Resumable local object landing")
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)
    load = sub.add_parser("land")
    load.add_argument("manifest", type=Path)
    load.add_argument("--workers", type=int, default=3)
    load.add_argument("--crash",
                      choices=["after-piece", "after-range", "before-pointer"])
    view = sub.add_parser("inspect")
    view.add_argument("--generation")
    a = p.parse_args()
    try:
        if a.op == "land":
            data = json.loads(a.manifest.read_text())
            out = land(a.root, data, a.workers, a.crash)
        else:
            out = inspect(a.root, a.generation)
        print(json.dumps(out, sort_keys=True))
    except (OSError, ValueError, ManifestError) as err:
        p.exit(2, str(err) + "\n")


if __name__ == "__main__":
    main()
