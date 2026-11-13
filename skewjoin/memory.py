"""工作内存预算与峰值计量。

预算只统计由引擎自己持有的 Python 数据（哈希表、分区缓冲、分段列表等），
不包含解释器与操作系统页缓存；进程真实驻留内存另以 ru_maxrss 记录在
peak_rss_bytes 中。所有托管对象都按“上限估计”收费，宁可高估也不低估，
这样 64 MiB 预算对托管工作内存是硬上界。
"""
import sys
import tracemalloc

# 各种 Python 对象的保守字节估计。CPython 64 位下的真实值与此接近。
_OBJ = sys.getsizeof(object())
_TUPLE_BASE = sys.getsizeof(())
_LIST_BASE = sys.getsizeof([])
_DICT_BASE = sys.getsizeof({})
_SET_BASE = sys.getsizeof(set())
_INT = sys.getsizeof(0)
_SLOT = 8  # dict/set 表中单个槽位的保守估计
_REF = 8   # 容器里每个引用


def str_size(value: str) -> int:
    try:
        return sys.getsizeof(value)
    except TypeError:  # pragma: no cover - 防御性
        return len(value) + 49


def row_size(row: tuple) -> int:
    """一行 (k1, k2, payload) 的保守字节数（含字符串本身）。"""
    total = _TUPLE_BASE + 2 * _REF
    total += sum(str_size(v) if v is not None else 0 for v in row[:2])
    payload = row[2]
    total += str_size(payload) if payload is not None else 0
    return total


def key_size(key: tuple) -> int:
    total = _TUPLE_BASE + len(key) * _REF
    total += sum(str_size(v) if v is not None else 0 for v in key)
    return total


class MemoryBudget:
    """托管工作内存的记账器。

    charge/refund 维护当前托管字节数，check() 在可能超预算时抛出
    MemoryBudgetExceeded，调用方据此放弃内存路径、落盘处理。
    peak_managed_bytes 始终保存历史峰值。
    """

    def __init__(self, budget_bytes: int):
        if budget_bytes < 1 << 20:
            raise ValueError("memory-mib 至少为 1 MiB")
        self.budget = budget_bytes
        self.current = 0
        self.peak = 0
        # 预留 15% 给解析中的瞬时对象、输出缓冲等无法逐笔记账的部分。
        self.usable = int(budget_bytes * 0.85)
        self.trace_started = False
        self.peak_python = 0

    def start_trace(self):
        if not self.trace_started:
            tracemalloc.start()
            self.trace_started = True

    def stop_trace(self):
        if self.trace_started:
            _, self.peak_python = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.trace_started = False

    def charge(self, n: int):
        # 预判式计费：若本次增量会越过可用上界则不实际增加，直接抛出，由调用
        # 方刷出/拆分当前结构后重试。这样托管峰值严格不超过 usable（预算的
        # 85%），为解析瞬时对象预留的余量也不会被结构性占用侵蚀。
        if self.current + n > self.usable:
            raise MemoryBudgetExceeded(self.current + n, self.usable)
        self.current += n
        if self.current > self.peak:
            self.peak = self.current

    def refund(self, n: int):
        self.current -= n
        if self.current < 0:  # pragma: no cover - 记账一致性保护
            self.current = 0

    def check(self):
        if self.current > self.usable:
            raise MemoryBudgetExceeded(self.current, self.usable)


class MemoryBudgetExceeded(RuntimeError):
    """托管工作内存超过预算的可用上界，调用方应切换到外存路径。"""

    def __init__(self, used: int, usable: int):
        super().__init__(
            "托管工作内存 %d 字节超过可用预算 %d 字节" % (used, usable))
        self.used = used
        self.usable = usable
