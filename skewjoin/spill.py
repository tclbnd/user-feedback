"""外存暂存：目录布局、句柄受限的文件池、分区路由器。

磁盘布局（staging 目录在目标结果目录之外，发布前目标目录不会被创建）：

    <stage>/
      p<salt>/<side>-part-<pid>.jsonl      常规哈希分区
      p<salt>/<side>-null.jsonl            任一键分量为空(SQL NULL)的行
      skew/p<salt>/<side>-hot-<hid>.jsonl  被剥离的热点键分段
      skew/p<salt>/<side>-rest.jsonl       剥离热点后的残余行

每个文件都是 JSON Lines：一行一个数组 [k1, k2, payload]，空键写为 null。
文件句柄由 CappedFilePool 统一管理，任一时刻打开句柄数不超过上限。
"""
import json
import os
from collections import OrderedDict
from pathlib import Path

from .hashing import key_hash
from .memory import MemoryBudgetExceeded


def encode_row(row: tuple) -> bytes:
    # ensure_ascii=False 保留 UTF-8；separators 压缩字节，便于溢写量统计。
    return (json.dumps(list(row), ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def iter_jsonl(path: Path):
    with path.open("rb") as handle:
        for line in handle:
            if not line.strip():
                continue
            yield tuple(json.loads(line.decode("utf-8")))


class Staging:
    """管理属于某次运行的暂存目录，并负责故障后的安全清理与复跑。"""

    def __init__(self, target: Path, spill_root: Path):
        self.target = target
        # 名称与目标目录强绑定：重跑时只清理“属于该目标”的暂存。
        token = "%s.%s.stage" % (target.name, os.getpid())
        self.root = spill_root / token
        self.bytes_written = 0
        self.bytes_read = 0

    def prepare_fresh(self):
        if self.target.exists():
            raise FileExistsError("结果目录已存在，拒绝覆盖: %s" % self.target)
        self.cleanup_owned_staging()
        self.root.mkdir(parents=True, exist_ok=False)

    def part_dir(self, salt: int) -> Path:
        path = self.root / ("p%d" % salt)
        path.mkdir(exist_ok=True)
        return path

    def skew_dir(self, salt: int) -> Path:
        path = self.root / "skew" / ("p%d" % salt)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_owned_staging(self):
        """删除同一 spill 根下、由该目标名派生的所有暂存（其他目标不动）。"""
        parent = self.root.parent
        prefix = self.target.name + "."
        if not parent.exists():
            return
        for child in parent.iterdir():
            if child.is_dir() and child.name.startswith(prefix) \
                    and child.name.endswith(".stage"):
                _rm_tree(child)

    def destroy(self):
        if self.root.exists():
            _rm_tree(self.root)


def _rm_tree(path: Path):
    for child in path.iterdir():
        if child.is_dir():
            _rm_tree(child)
        else:
            child.unlink()
    path.rmdir()


class CappedFilePool:
    """同时打开的二进制文件句柄数有硬上限的 LRU 文件池。

    写句柄与读句柄分别用 OrderedDict 做 LRU 管理，但共享同一个总句柄预算。
    打开任何新句柄前，先按最久未用顺序回收（写句柄回收时 flush 落盘，读句
    柄直接关闭），直到打开后总数仍不超过上限。这样彻底避免读、写句柄互相
    争用时计数失准或在被回收的句柄上写入而丢数据。
    """

    def __init__(self, max_open: int, staging: Staging):
        self.max_open = max(2, max_open)
        self.staging = staging
        # OrderedDict 按最近访问排序，popitem(last=False) 淘汰最久未用句柄。
        self._writers = OrderedDict()
        self._readers = OrderedDict()
        self.peak_open = 0
        self.open_now = 0

    def _make_room(self):
        """回收最久未用的句柄，直到还能再安全打开一个新句柄。"""
        while self.open_now >= self.max_open:
            if self._writers:
                _, old = self._writers.popitem(last=False)
                old.flush()
                old.close()
            elif self._readers:
                _, old = self._readers.popitem(last=False)
                old.close()
            else:
                break  # 理论上不可达
            self.open_now -= 1

    def writer(self, path: Path):
        if path in self._writers:
            self._writers.move_to_end(path)
            return self._writers[path]
        self._make_room()
        handle = path.open("ab")
        self.open_now += 1
        self.peak_open = max(self.peak_open, self.open_now)
        self._writers[path] = handle
        return handle

    def write(self, path: Path, blob: bytes):
        self.writer(path).write(blob)
        self.staging.bytes_written += len(blob)

    def open_reader(self, path: Path):
        """返回一个上下文管理器；读取句柄即时开关，同样受总上限约束。"""
        return _PooledReader(self, path)

    def register_reader(self, path: Path):
        self._make_room()
        raw = path.open("rb")
        staging = self.staging
        # 包装一层以统计回读字节数（溢写量的读侧计量）。
        class _CountingReader:
            def read(self, *args):
                blob = raw.read(*args)
                staging.bytes_read += len(blob)
                return blob

            def readline(self, *args):
                blob = raw.readline(*args)
                staging.bytes_read += len(blob)
                return blob

            def __iter__(self):
                return self

            def __next__(self):
                blob = next(raw)
                staging.bytes_read += len(blob)
                return blob

            def close(self):
                raw.close()

            def __getattr__(self, name):
                return getattr(raw, name)

        handle = _CountingReader()
        self.open_now += 1
        self.peak_open = max(self.peak_open, self.open_now)
        self._readers[handle] = path
        return handle

    def release_reader(self, handle):
        if handle in self._readers:
            del self._readers[handle]
            handle.close()
            self.open_now -= 1

    def close_all(self):
        while self._writers:
            _, handle = self._writers.popitem(last=False)
            handle.flush()
            handle.close()
            self.open_now -= 1
        while self._readers:
            _, handle = self._readers.popitem(last=False)
            handle.close()
            self.open_now -= 1


class _PooledReader:
    def __init__(self, pool: "CappedFilePool", path: Path):
        self.pool = pool
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.pool.register_reader(self.path)
        return self.handle

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            self.pool.release_reader(self.handle)
            self.handle = None
        return False


class Router:
    """一次分区扫描：把行流按哈希（或 NULL 分桶）写入多个溢写文件。"""

    def __init__(self, pool: CappedFilePool, directory: Path, side: str,
                 num_parts: int, salt: int):
        self.pool = pool
        self.directory = directory
        self.side = side
        self.num_parts = num_parts
        self.salt = salt
        self.part_rows = [0] * num_parts
        self.part_bytes = [0] * num_parts
        self.null_rows = 0
        self.null_bytes = 0
        self._null_path = directory / ("%s-null.jsonl" % side)

    def _part_path(self, pid: int) -> Path:
        return self.directory / ("%s-part-%d.jsonl" % (self.side, pid))

    def send(self, row: tuple):
        blob = encode_row(row)
        if row[0] is None or row[1] is None:
            self.pool.write(self._null_path, blob)
            self.null_rows += 1
            self.null_bytes += len(blob)
            return None
        pid = key_hash((row[0], row[1]), self.salt) % self.num_parts
        self.pool.write(self._part_path(pid), blob)
        self.part_rows[pid] += 1
        self.part_bytes[pid] += len(blob)
        return pid

    def part_path(self, pid: int) -> Path:
        return self._part_path(pid)

    @property
    def null_path(self) -> Path:
        return self._null_path


def estimate_inmem_cost(rows: int, bytes_on_disk: int) -> int:
    """磁盘字节 -> 内存中 Python 结构的保守放大估计。

    JSON 行紧凑，而 Python 字符串/元组/字典槽位开销大，经验上放大约 3 倍；
    再叠加哈希表槽位与键对象，统一按 4 倍上限估计。
    """
    return max(rows * 64, bytes_on_disk * 4)


def fits_budget(estimate: int, budget) -> bool:
    try:
        budget.charge(estimate)
    except MemoryBudgetExceeded:
        return False
    budget.refund(estimate)
    return estimate <= budget.usable
