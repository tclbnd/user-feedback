#!/usr/bin/env python3
"""生成测试源文件与 req.json（可复现的伪随机内容）。"""
import json
import os
import random
import sys


def gen(outdir, size, chunk, concurrency, fail_at=None, corrupt_chunks=None,
        misalign_chunks=None, range_head=None, norange=False, seed=7, corrupt_times=1,
        flips=2, extra=None):
    os.makedirs(outdir, exist_ok=True)
    rnd = random.Random(seed)
    data = bytes(rnd.randrange(256) for _ in range(size))
    with open(os.path.join(outdir, "src.bin"), "wb") as f:
        f.write(data)
    marker = os.path.join(outdir, "src.bin.norange")
    if os.path.exists(marker):
        os.remove(marker)
    if norange:
        open(marker, "wb").close()
    req = {"source": "src.bin", "chunk": chunk, "concurrency": concurrency,
           "state": "state.json"}
    if fail_at:
        req["fail_at"] = fail_at
    if corrupt_chunks:
        req["corrupt_chunks"] = corrupt_chunks
    if misalign_chunks:
        req["misalign_chunks"] = misalign_chunks
    if range_head is not None:
        req["range_head"] = range_head
    if corrupt_times != 1:
        req["corrupt_times"] = corrupt_times
    if flips != 2:
        req["flips"] = flips
    if extra:
        req.update(extra)
    with open(os.path.join(outdir, "req.json"), "w") as f:
        json.dump(req, f)
    return data


if __name__ == "__main__":
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 300000
    gen("t1", size, 65536, 4)
    print("fixture t1 size=%d" % size)
