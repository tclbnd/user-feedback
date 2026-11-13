"""分区外部哈希连接引擎（含热点键倾斜处理）。

执行路径由工作内存预算驱动：

  第一遍  两侧流式扫描，按复合键哈希分区；任一空键分量(SQL NULL)进 NULL 桶。
  逐分区  估计可整装入内存 -> 常规哈希连接（构建侧哈希表 + 探测侧流式）。
          否则统计键频次，识别“多对多放大”的热点键：
            * 热点键两侧各自剥离成独立文件，再做分段块连接——任一侧都可以
              被切成多个预算内分段，笛卡尔匹配全程流式输出，不在内存累积；
            * 残余行递归走同样的选择逻辑；
          无法识别倾斜或达到递归深度上限时，退化为有界块嵌套循环连接(BNL)。

NULL 语义：键中任意分量为空即 SQL NULL，NULL 不等于任何值（包括另一个
NULL），因此 NULL 行永不匹配；左外连接中它们作为未匹配行原样输出。
"""
import csv
import json
import os
import resource
import sys
from collections import OrderedDict
from pathlib import Path

from .memory import (
    MemoryBudgetExceeded, row_size, key_size,
    _LIST_BASE, _REF, _INT,
)
from .spill import (
    Staging, CappedFilePool, Router, iter_jsonl, encode_row,
    estimate_inmem_cost,
)
from .output import ResultWriter

HEADER = ["key_a", "key_b", "value"]
MAX_DEPTH = 4
HOT_MIN_PRODUCT = 16
HOT_KEY_CAP = 32
DEFAULT_MAX_OPEN = 16


def read_input(path):
    """CSV -> (k1, k2, payload) 元组流；空键分量规范化为 None。"""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("输入缺少表头: %s" % path)
        if header != HEADER:
            raise ValueError("表头必须为 key_a,key_b,value，实际为 %s" % header)
        for record in reader:
            if not record or all(cell == "" for cell in record):
                continue
            if len(record) != 3:
                raise ValueError("每行必须有 3 列，文件 %s 遇到 %r" % (path, record))
            ka = None if record[0] == "" else record[0]
            kb = None if record[1] == "" else record[1]
            yield (ka, kb, record[2])


class JoinEngine:
    def __init__(self, left_path, right_path, target_dir, mode,
                 memory_mib, crash_before_publish=False,
                 spill_root=None, max_open=None):
        self.left_path = left_path
        self.right_path = right_path
        self.target = target_dir
        self.mode = mode
        self.crash = crash_before_publish
        from .memory import MemoryBudget
        self.budget = MemoryBudget(memory_mib << 20)
        root = Path(spill_root) if spill_root else target_dir.parent
        self.staging = Staging(target_dir, root)
        cap = max_open or int(os.environ.get("ZQ_MAX_OPEN_FDS", DEFAULT_MAX_OPEN))
        self.pool = CappedFilePool(cap, self.staging)
        self.result = None
        self._seq = 0
        self.partitions = 0
        self.hot_keys = 0
        self.segment_passes = 0
        self.null_left = (None, 0)
        self.null_right = (None, 0)

    def _new_dir(self):
        self._seq += 1
        path = self.staging.root / ("rec-%d" % self._seq)
        path.mkdir()
        return path

    def run(self):
        self.staging.prepare_fresh()
        self.budget.start_trace()
        try:
            publish_dir = self.staging.root / "publish"
            publish_dir.mkdir()
            self.result = ResultWriter(publish_dir / "rows.csv")
            try:
                self._phase_partition()
                self._emit_null_rows()
            finally:
                digest_info = self.result.close()
            self.pool.close_all()
            if self.crash:
                # 暂存结果已写完但绝不发布：目标目录不存在，退出码 97。
                os._exit(97)
            metrics = self._build_metrics(digest_info)
            (publish_dir / "_SUCCESS").write_text(
                json.dumps(metrics, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8")
            # 同文件系统内原子发布：目标目录只含 rows.csv 与 _SUCCESS。
            os.replace(publish_dir, self.target)
        finally:
            self.budget.stop_trace()
            # 正常发布后暂存只剩溢写目录；异常路径下负责回收全部暂存。
            self.staging.destroy()
        return metrics

    def _build_metrics(self, digest_info):
        rows, digest = digest_info
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_bytes = rss_kb if sys.platform == "darwin" else rss_kb * 1024
        return dict(
            rows=rows,
            spill_bytes=self.staging.bytes_written,
            spill_read_bytes=self.staging.bytes_read,
            peak_rss_bytes=rss_bytes,
            peak_managed_bytes=self.budget.peak,
            budget_bytes=self.budget.budget,
            peak_python_traced_bytes=self.budget.peak_python,
            peak_open_fds=self.pool.peak_open,
            max_open_fds=self.pool.max_open,
            partitions=self.partitions,
            hot_keys=self.hot_keys,
            segment_passes=self.segment_passes,
            join_type=self.mode,
            multiset_rows=rows,
            multiset_digest=digest,
            output_bytes=self.result.output_bytes,
            sort_key=("输出按多重集校验，不依赖顺序；核对见 multiset_digest。"
                      " 稳定排序键=(key_a,key_b,left_value,right_value)，空键为空串。"),
        )

    def _phase_partition(self):
        # 首遍固定为少量粗分区，刻意不按预算切碎：这样单个热点键的全部行会
        # 集中落入同一个超大分区（“按哈希分区无法继续拆小”的键只能靠识别后
        # 剥离处理），热点分段路径才能真正生效。粗分区若装不进内存：
        #   * 含倾斜 -> 键计数识别热点键并剥离、分段块连接；
        #   * 无倾斜的高基数 -> 递归层 16 路再分区安全细分；
        #   * 极端情况 -> 有界块嵌套循环兜底。
        # 因此固定粗分区不会破坏任何场景下的内存上界。
        num_parts = 4
        directory = self.staging.part_dir(0)
        router_l = Router(self.pool, directory, "left", num_parts, salt=0)
        router_r = Router(self.pool, directory, "right", num_parts, salt=0)
        for row in read_input(self.left_path):
            router_l.send(row)
        for row in read_input(self.right_path):
            router_r.send(row)
        self.pool.close_all()
        self.null_left = (router_l.null_path, router_l.null_rows)
        self.null_right = (router_r.null_path, router_r.null_rows)
        self.partitions = num_parts
        for pid in range(num_parts):
            lp = router_l.part_path(pid) if router_l.part_rows[pid] else None
            rp = router_r.part_path(pid) if router_r.part_rows[pid] else None
            self._process_partition(lp, rp,
                                    router_l.part_bytes[pid],
                                    router_r.part_bytes[pid], depth=0)

    def _emit_null_rows(self):
        # NULL 行之间也不匹配（NULL = NULL 不为真）。
        path, count = self.null_left
        if self.mode == "left" and count and path is not None:
            for row in iter_jsonl(path):
                self.result.emit(row, None)
        # 右侧 NULL 行在任何连接类型下都不产生输出。

    def _process_partition(self, lp, rp, l_bytes, r_bytes, depth):
        if lp is None or rp is None:
            if self.mode == "left" and lp is not None:
                for row in iter_jsonl(lp):
                    self.result.emit(row, None)
            return  # 内连接缺一侧即无输出；左外缺右侧上面已输出未匹配
        total_rows = self._count_rows(lp) + self._count_rows(rp)
        estimate = estimate_inmem_cost(total_rows, l_bytes + r_bytes)
        if estimate <= self.budget.usable:
            try:
                self._join_in_memory(lp, rp)
                return
            except MemoryBudgetExceeded:
                # 估计偏乐观、真实构建越过预算：回退到外存路径。
                pass
        self._disk_join(lp, rp, l_bytes, r_bytes, depth)

    def _disk_join(self, lp, rp, l_bytes, r_bytes, depth):
        if depth >= MAX_DEPTH:
            self._block_nested_loop(lp, rp, l_bytes, r_bytes)
            return
        counted = self._count_keys(lp, rp)
        if counted is None:
            # 键数本身就装不下：基数极高、无明显倾斜，直接 BNL 保证有界。
            self._block_nested_loop(lp, rp, l_bytes, r_bytes)
            return
        counts, charged = counted
        try:
            hot = self._select_hot_keys(counts, l_bytes, r_bytes)
        finally:
            counts.clear()
            self.budget.refund(charged)
        if not hot:
            self._repartition_and_recurse(lp, rp, depth)
            return
        self._strip_and_segment(lp, rp, hot, depth)

    def _count_rows(self, path):
        total = 0
        with self.pool.open_reader(path) as handle:
            for line in handle:
                if line.strip():
                    total += 1
        return total

    def _build_table(self, path):
        table = {}
        charged = 0
        try:
            with self.pool.open_reader(path) as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = tuple(json.loads(line.decode("utf-8")))
                    key = (row[0], row[1])
                    inc = row_size(row)
                    bucket = table.get(key)
                    if bucket is None:
                        table[key] = [row]
                        inc += key_size(key) + _LIST_BASE + _REF
                    else:
                        bucket.append(row)
                        inc += _REF
                    self.budget.charge(inc)
                    charged += inc
            self.budget.check()
        except MemoryBudgetExceeded:
            table.clear()
            self.budget.refund(charged)
            raise
        return table, charged

    def _join_in_memory(self, lp, rp):
        # 左外必须以右侧为构建侧，才能在探测左侧时判断未匹配。
        if self.mode == "left":
            build_path, probe_path, build_side = rp, lp, "right"
        elif lp.stat().st_size <= rp.stat().st_size:
            build_path, probe_path, build_side = lp, rp, "left"
        else:
            build_path, probe_path, build_side = rp, lp, "right"
        table, charged = self._build_table(build_path)
        try:
            with self.pool.open_reader(probe_path) as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = tuple(json.loads(line.decode("utf-8")))
                    matches = table.get((row[0], row[1]))
                    if matches:
                        for other in matches:
                            if build_side == "right":
                                self.result.emit(row, other)
                            else:
                                self.result.emit(other, row)
                    elif self.mode == "left" and build_side == "right":
                        self.result.emit(row, None)
        finally:
            table.clear()
            self.budget.refund(charged)

    def _count_keys(self, lp, rp):
        """两遍有界键频次统计，专门应对“一侧巨大且高度倾斜”。

        第一遍只统计较小一侧的不同键（该侧通常可装入预算）；第二遍流式扫描
        较大一侧时，仅对第一遍发现的候选键累加计数，绝不把大侧的全部不同键
        放进内存。这样即便大侧有数百万个重复的热点键，占用内存的也只是小侧
        的键集合。若小侧自身的键数就装不下预算，说明没有可利用的明显倾斜，
        返回 None 交由 BNL 兜底。
        """
        if lp.stat().st_size <= rp.stat().st_size:
            small_path, big_path, small_slot, big_slot = lp, rp, 0, 1
        else:
            small_path, big_path, small_slot, big_slot = rp, lp, 1, 0
        counts = OrderedDict()
        charged = 0
        try:
            # 第一遍：较小一侧全键计数。新键一次性计入键对象、小列表与两个
            # 计数整数槽；计数递增是不可变整数的原地替换，不额外驻留内存，
            # 因此不逐行重复计费，避免重复键把费用错误累加到超预算。
            with self.pool.open_reader(small_path) as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = tuple(json.loads(line.decode("utf-8")))
                    key = (row[0], row[1])
                    entry = counts.get(key)
                    if entry is None:
                        counts[key] = [0, 0]
                        inc = key_size(key) + _LIST_BASE + 2 * _REF + 2 * _INT
                        charged += inc
                        self.budget.charge(inc)
                        entry = counts[key]
                    entry[small_slot] += 1
            # 第二遍：较大一侧只对候选键计数。扫描过的行不驻留内存，命中候选
            # 键时同样只是整数原地递增，无新增驻留结构，因此不再额外计费。
            with self.pool.open_reader(big_path) as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = tuple(json.loads(line.decode("utf-8")))
                    entry = counts.get((row[0], row[1]))
                    if entry is not None:
                        entry[big_slot] += 1
        except MemoryBudgetExceeded:
            counts.clear()
            self.budget.refund(charged)
            return None
        return counts, charged

    def _select_hot_keys(self, counts, l_bytes, r_bytes):
        l_rows = sum(e[0] for e in counts.values())
        r_rows = sum(e[1] for e in counts.values())
        avg_l = l_bytes / l_rows if l_rows else 0.0
        avg_r = r_bytes / r_rows if r_rows else 0.0
        candidates = [(key, e) for key, e in counts.items()
                      if e[0] * e[1] >= HOT_MIN_PRODUCT]
        candidates.sort(key=lambda item: (item[1][0] * item[1][1],
                                          item[1][0] + item[1][1]),
                        reverse=True)
        res_rows = l_rows + r_rows
        res_bytes = float(l_bytes + r_bytes)
        chosen = []
        for key, entry in candidates[:HOT_KEY_CAP]:
            if estimate_inmem_cost(res_rows, res_bytes) <= self.budget.usable:
                break
            chosen.append(key)
            res_rows -= entry[0] + entry[1]
            res_bytes -= entry[0] * avg_l + entry[1] * avg_r
        return chosen

    def _strip_and_segment(self, lp, rp, hot_keys, depth):
        directory = self._new_dir()
        index = dict((key, i) for i, key in enumerate(hot_keys))
        # 热点键集合很小，但同样计入预算。
        charged = sum(key_size(k) + _INT for k in hot_keys)
        self.budget.charge(charged)
        try:
            for side, path in (("left", lp), ("right", rp)):
                with self.pool.open_reader(path) as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        row = tuple(json.loads(line.decode("utf-8")))
                        idx = index.get((row[0], row[1]))
                        if idx is None:
                            dest = directory / ("%s-rest.jsonl" % side)
                        else:
                            dest = directory / ("%s-hot-%d.jsonl" % (side, idx))
                        self.pool.write(dest, encode_row(row))
            self.pool.close_all()
            for idx, key in enumerate(hot_keys):
                self.hot_keys += 1
                hp_l = directory / ("left-hot-%d.jsonl" % idx)
                hp_r = directory / ("right-hot-%d.jsonl" % idx)
                # 入选条件保证两侧都有该键，文件必然都存在。
                self._segment_join(hp_l, hp_r)
            rest_l = directory / "left-rest.jsonl"
            rest_r = directory / "right-rest.jsonl"
            rl = rest_l if rest_l.exists() else None
            rr = rest_r if rest_r.exists() else None
        finally:
            self.budget.refund(charged)
        if rl is not None or rr is not None:
            lb = rl.stat().st_size if rl else 0
            rb = rr.stat().st_size if rr else 0
            self._process_partition(rl, rr, lb, rb, depth + 1)

    def _segment_join(self, hp_l, hp_r):
        """单个热点键的分段块连接。

        分段文件中只剩同一个键，任意左行与任意右行都匹配，即该键的笛卡尔
        积。取字节较小的一侧按预算切块，每块驻留内存时把另一侧完整流式
        扫描一遍；匹配逐行写出，两侧都可以远大于内存。左外未匹配不会出现
        在这里（该键两侧均存在）。
        """
        if hp_l.stat().st_size <= hp_r.stat().st_size:
            small, big, small_is_left = hp_l, hp_r, True
        else:
            small, big, small_is_left = hp_r, hp_l, False
        small_rows = self._count_rows(small)
        per_row = max(64, int(small.stat().st_size / max(1, small_rows)) * 4)
        cap_rows = max(1, self.budget.usable // per_row)
        chunk, charged = [], 0
        for row in iter_jsonl(small):
            inc = row_size(row) + _REF
            try:
                self.budget.charge(inc)
            except MemoryBudgetExceeded:
                # 切块触顶：先输出已积累的块并归还费用，再为当前行重新计费。
                if chunk:
                    self._emit_cartesian(chunk, big, small_is_left)
                    self.budget.refund(charged)
                    chunk, charged = [], 0
                    self.segment_passes += 1
                self.budget.charge(inc)
            chunk.append(row)
            charged += inc
            if len(chunk) >= cap_rows:
                self._emit_cartesian(chunk, big, small_is_left)
                self.budget.refund(charged)
                chunk, charged = [], 0
                self.segment_passes += 1
        if chunk:
            self._emit_cartesian(chunk, big, small_is_left)
            self.budget.refund(charged)
            self.segment_passes += 1

    def _emit_cartesian(self, chunk, big_path, small_is_left):
        with self.pool.open_reader(big_path) as handle:
            for line in handle:
                if not line.strip():
                    continue
                other = tuple(json.loads(line.decode("utf-8")))
                for small_row in chunk:
                    if small_is_left:
                        self.result.emit(small_row, other)
                    else:
                        self.result.emit(other, small_row)

    def _repartition_and_recurse(self, lp, rp, depth):
        directory = self._new_dir()
        num_parts = 16
        router_l = Router(self.pool, directory, "left", num_parts, depth + 1)
        router_r = Router(self.pool, directory, "right", num_parts, depth + 1)
        for row in iter_jsonl(lp):
            router_l.send(row)
        for row in iter_jsonl(rp):
            router_r.send(row)
        self.pool.close_all()
        for pid in range(num_parts):
            nlp = router_l.part_path(pid) if router_l.part_rows[pid] else None
            nrp = router_r.part_path(pid) if router_r.part_rows[pid] else None
            self._process_partition(nlp, nrp,
                                    router_l.part_bytes[pid],
                                    router_r.part_bytes[pid], depth + 1)

    def _block_nested_loop(self, lp, rp, l_bytes, r_bytes):
        """有界块嵌套循环兜底，覆盖任意基数与左外未匹配。

        外层只按“块行数上限”累积驱动侧的一个块，块的内存只在 flush 内按最终
        哈希表结构精确计费一次、用完即归还，避免同一行被重复计费。块行数上限
        依据保守的单行内存估计反推，使每个块的真实驻留都落在预算内。
        """
        if self.mode == "left":
            drive_path, other_path, drive_is_left = lp, rp, True
        elif l_bytes <= r_bytes:
            drive_path, other_path, drive_is_left = lp, rp, True
        else:
            drive_path, other_path, drive_is_left = rp, lp, False
        drive_rows = self._count_rows(drive_path)
        per_row = max(64, int(drive_path.stat().st_size
                              / max(1, drive_rows)) * 4)
        cap_rows = max(1, int(self.budget.usable * 0.7) // per_row)

        def flush(chunk_rows):
            table = {}
            local_charge = 0
            matched = set()
            try:
                for row in chunk_rows:
                    key = (row[0], row[1])
                    inc = row_size(row) + _REF
                    bucket = table.get(key)
                    if bucket is None:
                        table[key] = [row]
                        inc += key_size(key) + _LIST_BASE
                    else:
                        bucket.append(row)
                    try:
                        self.budget.charge(inc)
                    except MemoryBudgetExceeded:
                        # 估计偏乐观、块在真实建表时越界：归还已计费用，把块
                        # 对半拆成两个更小的块分别处理，严格守住内存上界。
                        self.budget.refund(local_charge)
                        table.clear()
                        mid = len(chunk_rows) // 2
                        if mid == 0:
                            # 单行就越界（极端宽行）：临时提升容忍不可能发生，
                            # 直接抛出由上层判定数据异常；此处先保证状态干净。
                            raise
                        flush(chunk_rows[:mid])
                        flush(chunk_rows[mid:])
                        return
                    local_charge += inc
                with self.pool.open_reader(other_path) as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        other = tuple(json.loads(line.decode("utf-8")))
                        bucket = table.get((other[0], other[1]))
                        if not bucket:
                            continue
                        if self.mode == "left":
                            matched.add((other[0], other[1]))
                            for left_row in bucket:
                                self.result.emit(left_row, other)
                        else:
                            for drive_row in bucket:
                                if drive_is_left:
                                    self.result.emit(drive_row, other)
                                else:
                                    self.result.emit(other, drive_row)
                if self.mode == "left":
                    for left_row in chunk_rows:
                        if (left_row[0], left_row[1]) not in matched:
                            self.result.emit(left_row, None)
            finally:
                table.clear()
                matched.clear()
                self.budget.refund(local_charge)

        chunk = []
        for row in iter_jsonl(drive_path):
            chunk.append(row)
            if len(chunk) >= cap_rows:
                flush(chunk)
                chunk = []
        if chunk:
            flush(chunk)
