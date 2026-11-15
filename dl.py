#!/usr/bin/env python3
"""
dl.py -- 分块下载断点续传与校验重组 (规格见 README.md)

用法: python3 dl.py req.json output
req.json: {"source":"src.bin","chunk":1024,"concurrency":4,
           "state":"state.json","fail_at":[0.5,0.8],"corrupt_chunks":[3]}

相对缺陷基线的修复:
  * 断流后从头重下      -> 按块粒度持久化(块号->sha256/重试记录), 续传只对缺失块发"Range"读
  * 无块级校验          -> 逐块 sha256 校验; 校验失败隔离重取该块(重取精确到块级)
  * corrupt 不检测      -> 源端注入的字节翻转会被块校验捕获并重取
  * Range 不支持不退化  -> 首/末块 Range 探测失败时退化为整体重传, mode="full" 并告警
附加保证:
  * 重组后整体 sha256 校验; 失败时按块重算哈希定位坏块, 只重取坏块后复检
  * 块边界/偏移全程 64 位整数; 块->偏移映射只由 (size, chunk) 决定, 与并发数无关
  * 输出逐块状态、重试原因与吞吐统计; meta 写入 output+".meta.json"
  * 模拟断流: 进度达 fail_at 各点时持久化状态并以退出码 3 退出, 重跑续传
"""
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

EXIT_DISCONNECT = 3


class RangeUnsupported(Exception):
    pass


class ChecksumMismatch(Exception):
    pass


class SimulatedDisconnect(Exception):
    pass


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Source:
    """模拟源端: read_chunk(i) 相当于一次 Range 请求, read_all() 相当于整体 GET。

    corrupt_chunks 中的块在首次被读取时由"源端"翻转 1 字节(一次性瞬态损坏),
    重取该块得到正确数据。Range 支持度由伴生文件决定:
      src.norange            -> 完全不支持 Range
      src.rangelimit (整数N) -> 仅偏移 < N 的区间支持 Range (如仅头 1MB)
    """

    def __init__(self, path, chunk, corrupt_chunks, corrupt_served):
        self.path = path
        self.chunk = int(chunk)
        with open(path, "rb") as f:
            self.data = f.read()
        self.size = len(self.data)  # 64-bit
        self.corrupt = set(corrupt_chunks)
        self.corrupt_served = corrupt_served  # set[int], 持久化在 state 中
        self.range_limit = None  # None = 完整支持 Range
        if os.path.exists(path + ".norange"):
            self.range_limit = 0
        rl = path + ".rangelimit"
        if os.path.exists(rl):
            with open(rl) as f:
                self.range_limit = int(f.read().strip() or "0")
        self.file_sha = sha256(self.data)
        self.chunk_shas = [sha256(self.data[o:o + self.chunk])
                           for o in range(0, self.size, self.chunk)]

    def read_chunk(self, i):
        off = i * self.chunk  # 64-bit 偏移
        if self.range_limit is not None and off >= self.range_limit:
            raise RangeUnsupported(
                f"Range 起点 {off} 超出源支持范围 (limit={self.range_limit})")
        buf = bytearray(self.data[off:off + self.chunk])
        if i in self.corrupt and i not in self.corrupt_served:
            buf[0] ^= 0xFF  # 源端字节翻转(一次性)
            self.corrupt_served.add(i)
        return bytes(buf)

    def read_all(self):
        buf = bytearray(self.data)
        for i in sorted(self.corrupt):
            if i not in self.corrupt_served:
                off = i * self.chunk
                if off < len(buf):
                    buf[off] ^= 0xFF
                    self.corrupt_served.add(i)
        return bytes(buf)


def load_state(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {"mode": None, "chunks": {}, "fail": [], "corrupt_served": [],
            "alerts": [], "stats": {"retries": 0, "bytes": 0, "wall_sec": 0.0}}


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, path)  # 原子落盘


def download(req_path, out):
    req = json.load(open(req_path))
    d = os.path.dirname(os.path.abspath(req_path))
    src_path = req["source"]
    if not os.path.isabs(src_path):
        src_path = os.path.join(d, src_path)
    state_path = req.get("state", "state.json")
    if not os.path.isabs(state_path):
        state_path = os.path.join(d, state_path)
    chunk = int(req["chunk"])
    conc = int(req.get("concurrency", 4))
    fail_at = list(req.get("fail_at", []))
    corrupt = list(req.get("corrupt_chunks", []))

    t0 = time.time()
    st = load_state(state_path)
    st.setdefault("alerts", [])
    stats = st["stats"]
    src = Source(src_path, chunk, corrupt, set(st.get("corrupt_served", [])))
    total = src.size  # 64-bit
    nchunks = (total + chunk - 1) // chunk if total else 0
    part = out + ".part"

    def persist():
        st["corrupt_served"] = sorted(src.corrupt_served)
        save_state(state_path, st)

    disconnect_event = threading.Event()  # 每次运行最多触发一个断流点

    def check_disconnect(done_bytes):
        """进度(已完成字节/总字节)到达未触发的 fail_at 点 -> 模拟断流。"""
        if disconnect_event.is_set():
            raise SimulatedDisconnect("断流已触发, 停止取新块")
        for f in fail_at:
            if f not in st["fail"] and done_bytes >= f * total:
                st["fail"].append(f)
                disconnect_event.set()
                st["alerts"].append(f"断流: 进度 {done_bytes}/{total} 到达断流点 {f}")
                persist()
                raise SimulatedDisconnect(st["alerts"][-1])

    # ---------------------------------------------------------- 整体重传
    def full_mode(reason):
        st["mode"] = "full"
        alert = f"ALERT: {reason}, 退化为整体重传(无断点续传, 失败需整体重来)"
        if alert not in st["alerts"]:
            st["alerts"].append(alert)
        log(alert)
        persist()
        while True:
            pending_fails = [f for f in fail_at if f not in st["fail"]]
            if pending_fails:
                f = min(pending_fails)
                st["fail"].append(f)
                st["alerts"].append(f"断流: 整体重传中途到达断流点 {f}, 本次传输作废")
                persist()
                raise SimulatedDisconnect(st["alerts"][-1])
            data = src.read_all()
            stats["bytes"] += len(data)
            if sha256(data) != src.file_sha:
                stats["retries"] += 1
                st["alerts"].append("整体校验失败且无法定位坏块, 整体重传 "
                                    f"(第 {stats['retries']} 次重试)")
                log(st["alerts"][-1])
                persist()
                continue
            with open(part, "wb") as fp:
                fp.write(data)
            os.replace(part, out)
            log(f"整体重传完成 ({len(data)} 字节), 整体校验通过")
            break

    # ---------------------------------------------------------- 分块下载
    def chunked_mode():
        st["mode"] = "chunked"
        done = {int(k) for k in st["chunks"]}
        pending = [i for i in range(nchunks) if i not in done]
        log(f"块映射: size={total} chunk={chunk} 共 {nchunks} 块; 已完成 {len(done)} 块; "
            f"本次仅请求缺失块 {pending} (并发 {conc})")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT)
        lock = threading.Lock()
        done_bytes = [sum(min(chunk, total - i * chunk) for i in done)]

        def work(i):
            off = i * chunk  # 64-bit 偏移
            exp = src.chunk_shas[i]
            retried = False
            with lock:
                check_disconnect(done_bytes[0])  # 取块前: 已越断流点则不再取新块
            data = src.read_chunk(i)
            with lock:
                stats["bytes"] += len(data)
            print(f"FETCH {i}", flush=True)
            if sha256(data) != exp:
                with lock:
                    stats["retries"] += 1
                    st["alerts"].append(f"chunk {i} 块级校验失败, 隔离重取该块")
                log(st["alerts"][-1])
                retried = True
                data = src.read_chunk(i)  # 只重取这个块
                with lock:
                    stats["bytes"] += len(data)
                print(f"FETCH {i}", flush=True)
                if sha256(data) != exp:
                    raise ChecksumMismatch(f"chunk {i} 重取后仍校验失败")
            os.pwrite(fd, data, off)  # 按 64-bit 偏移写入, 线程安全
            with lock:
                st["chunks"][str(i)] = {"sha": exp, "retried": retried,
                                        "t": time.time()}
                done_bytes[0] += len(data)
                persist()
                log(f"chunk {i} 完成 off={off} len={len(data)} "
                    f"sha={exp[:12]}… retried={retried}")
                check_disconnect(done_bytes[0])

        ex = ThreadPoolExecutor(max_workers=conc)
        try:
            futs = [ex.submit(work, i) for i in pending]
            for fu in as_completed(futs):
                fu.result()  # 传播 SimulatedDisconnect / ChecksumMismatch
        except BaseException:
            # 断流: 取消尚未开始的块(保持缺失, 续传时再取), 等待进行中的块落盘
            ex.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            ex.shutdown(wait=True)
        finally:
            os.close(fd)

        # 重组后整体校验; 失败则按块重算哈希定位坏块, 只重取坏块
        with open(part, "rb") as f:
            pdata = f.read()
        if sha256(pdata) != src.file_sha:
            log("整体校验失败, 按块重算哈希定位坏块")
            bad = [i for i in range(nchunks)
                   if sha256(pdata[i * chunk:(i + 1) * chunk]) != src.chunk_shas[i]]
            for i in bad:
                log(f"定位到坏块 {i}, 隔离重取")
                data = src.read_chunk(i)
                stats["bytes"] += len(data)
                stats["retries"] += 1
                print(f"FETCH {i}", flush=True)
                if sha256(data) != src.chunk_shas[i]:
                    raise ChecksumMismatch(f"chunk {i} 重取后仍校验失败")
                with open(part, "r+b") as f:
                    f.seek(i * chunk)
                    f.write(data)
                st["chunks"][str(i)] = {"sha": src.chunk_shas[i], "retried": True,
                                        "t": time.time()}
                st["alerts"].append(f"重组后定位坏块 {i}, 已重取")
            persist()
            with open(part, "rb") as f:
                if sha256(f.read()) != src.file_sha:
                    raise ChecksumMismatch("坏块重取后整体校验仍失败")
            log("坏块修复后整体校验通过")
        else:
            log("重组后整体 sha256 校验通过")
        os.replace(part, out)

    # ---------------------------------------------------------- 主流程
    try:
        if total == 0:
            st["mode"] = "chunked"
            with open(out, "wb"):
                pass
        else:
            ranged = True
            if src.range_limit is not None:
                try:  # 首块与末块都必须可 Range, 否则整体退化
                    src.read_chunk(0)
                    src.read_chunk(nchunks - 1)
                except RangeUnsupported:
                    ranged = False
            if not ranged:
                reason = ("源不支持 Range" if src.range_limit == 0 else
                          f"源仅前 {src.range_limit} 字节支持 Range")
                full_mode(reason)
            else:
                chunked_mode()
    except SimulatedDisconnect as e:
        stats["wall_sec"] += time.time() - t0
        persist()
        log(f"{e}; 已完成块已持久化, 重跑仅取缺失块 (退出码 {EXIT_DISCONNECT})")
        return EXIT_DISCONNECT

    stats["wall_sec"] += time.time() - t0
    persist()

    chunks_meta = []
    if st["mode"] == "chunked":
        for i in range(nchunks):
            c = st["chunks"][str(i)]
            chunks_meta.append({"i": i, "sha": c["sha"], "retried": c["retried"],
                                "t": c["t"]})
    wall = stats["wall_sec"]
    meta = {
        "mode": st["mode"],
        "chunks": chunks_meta,
        "stats": {"retries": stats["retries"], "bytes": stats["bytes"],
                  "wall_sec": round(wall, 3),
                  "throughput_Bps": int(stats["bytes"] / max(wall, 1e-9))},
        "alerts": st["alerts"],
    }
    with open(out + ".meta.json", "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)

    print("=" * 72)
    print(f"完成: mode={meta['mode']} size={total} 块数={nchunks} "
          f"重试={stats['retries']} 流量={stats['bytes']}B "
          f"耗时={wall:.3f}s 吞吐={int(stats['bytes'] / max(wall, 1e-9))}B/s")
    for c in chunks_meta:
        print(f"  chunk {c['i']:3d} sha={c['sha'][:12]}… retried={c['retried']}")
    for a in st["alerts"]:
        print(f"  alert: {a}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(download(sys.argv[1], sys.argv[2]))

