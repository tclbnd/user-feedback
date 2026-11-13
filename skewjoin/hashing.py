"""稳定、可跨进程复现的哈希工具。

不使用 Python 内建 hash()：它对 str 默认带进程随机化种子，分区结果无法
跨重跑核对。这里用带前缀的 BLAKE2b 摘要构造 64 位稳定哈希。
"""
import hashlib

_MASK64 = (1 << 64) - 1


def stable_hash(value: str) -> int:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def key_hash(key: tuple, salt: int) -> int:
    """复合键的稳定 64 位哈希；salt 用于再分区时打散重哈希。"""
    h = hashlib.blake2b(digest_size=8)
    h.update(b"salt=%d\0" % salt)
    for part in key:  # 调用方保证各分量均非 None
        h.update(b"\x1f")
        h.update(part.encode("utf-8"))
    h.update(b"\x1e")
    return int.from_bytes(h.digest(), "big") & _MASK64


def multiset_digest(canonical_rows) -> tuple:
    """对输出多重集做流式校验：返回 (行数, 累加和哈希 hex)。

    对每行规范化字节取 BLAKE2b(256bit)，把摘要解释为 256 位无符号整数后
    取模累加。加法满足交换律/结合律，因此结果只取决于“行 -> 出现次数”的
    多重集，与输出顺序无关，可直接与参考实现交叉核对。
    """
    modulus = 1 << 256
    acc = 0
    count = 0
    for blob in canonical_rows:
        digest = hashlib.blake2b(blob, digest_size=32).digest()
        acc = (acc + int.from_bytes(digest, "big")) % modulus
        count += 1
    return count, "%064x" % acc
