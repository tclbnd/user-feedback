"""流式结果输出。

结果只顺序写一个 rows.csv，绝不把结果集读回内存。同时维护与输出顺序无关
的多重集校验和（见 hashing.multiset_digest），校验和可由任何一方只读取
rows.csv 重新计算，因此排序键不是正确性的前提——我们另外发布稳定排序键
约定，供需要逐行核对的工具使用。
"""
import csv
import io
import json

from .hashing import multiset_digest

HEADER = ["key_a", "key_b", "left_value", "right_value"]


def render_cell(value):
    return "" if value is None else value


def canonical_blob(cells) -> bytes:
    # 规范化只依赖最终写出的 4 个字符串，验证方读取 rows.csv 即可复算。
    return json.dumps(list(cells), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class ResultWriter:
    def __init__(self, rows_path):
        self._handle = rows_path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self._handle)
        self.writer.writerow(HEADER)
        self.rows = 0
        self.output_bytes = self._handle.tell()
        self._digest_acc = 0
        self._modulus = 1 << 256

    def emit(self, left_row, right_row):
        """left_row=(k1,k2,payload)；right_row 为 None 表示左外未匹配。"""
        ka, kb, lval = left_row
        rval = None if right_row is None else right_row[2]
        cells = (render_cell(ka), render_cell(kb),
                 render_cell(lval), render_cell(rval))
        self.writer.writerow(cells)
        import hashlib
        digest = hashlib.blake2b(canonical_blob(cells),
                                 digest_size=32).digest()
        self._digest_acc = (
            self._digest_acc + int.from_bytes(digest, "big")) % self._modulus
        self.rows += 1

    def close(self):
        self._handle.flush()
        self.output_bytes = self._handle.tell()
        self._handle.close()
        return self.rows, "%064x" % self._digest_acc
