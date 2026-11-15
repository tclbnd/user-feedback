#!/usr/bin/env python3
"""
acceptance.py -- 验收测试

T1: 3 个断流点 + 2 个字节翻转注入 -> 最终文件与源逐字节一致;
    坏块重取精确到块级; 续传只对缺失块发 Range (FETCH 计数验证)
T2: 并发 1/4/8 复跑 -> 最终文件一致, 块记录一致, 完成时间与块记录自洽
T3: 仅头 1MB 支持 Range 的源 -> 正确退化为整体重传并告警
T4: 完全不支持 Range 的源 -> 同样退化并告警
"""
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys

WORK = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(WORK, "accept_tmp")
DL = os.path.join(WORK, "dl.py")
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'} - {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def run_dl(req_path, out):
    return subprocess.run([sys.executable, DL, req_path, out],
                          capture_output=True, text=True)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_case(name, size, seed):
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    data = random.Random(seed).randbytes(size)
    with open(os.path.join(d, "src.bin"), "wb") as f:
        f.write(data)
    return d, data


def write_req(d, **kw):
    p = os.path.join(d, "req.json")
    with open(p, "w") as f:
        json.dump(kw, f)
    return p


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)

    # ---------------------------------------------------------- T1
    print("\n### T1: 3 处断流 + 2 处字节翻转, 断点续传 + 块级坏块重取")
    chunk = 1 << 18  # 256 KiB
    size = 5 * (1 << 20) + 12345  # 不对齐的尾块, 验证 64 位偏移边界
    d, data = make_case("t1", size, seed=1)
    nchunks = (size + chunk - 1) // chunk
    corrupt = [5, 13]
    req = write_req(d, source="src.bin", chunk=chunk, concurrency=4,
                    state="state.json", fail_at=[0.3, 0.6, 0.8],
                    corrupt_chunks=corrupt)
    out = os.path.join(d, "out.bin")
    rcs, fetches = [], {}
    for _ in range(12):
        p = run_dl(req, out)
        rcs.append(p.returncode)
        for line in p.stdout.splitlines():
            if line.startswith("FETCH "):
                i = int(line.split()[1])
                fetches[i] = fetches.get(i, 0) + 1
        if p.returncode == 0:
            break
    else:
        check("T1 最终完成", False, f"rcs={rcs}\n{p.stdout}\n{p.stderr}")
        return summary()

    check("T1 断流退出码3出现3次后成功", rcs.count(3) == 3 and rcs[-1] == 0,
          f"rcs={rcs}")
    got = open(out, "rb").read()
    check("T1 最终文件与源逐字节一致", got == data,
          f"sha={sha(got)[:16]}… vs {sha(data)[:16]}…")

    meta = json.load(open(out + ".meta.json"))
    check("T1 meta mode=chunked", meta["mode"] == "chunked")
    retried = sorted(c["i"] for c in meta["chunks"] if c["retried"])
    check("T1 坏块重取精确到块级", retried == corrupt,
          f"retried={retried} 期望={corrupt}")
    check("T1 重试次数=2(仅坏块)", meta["stats"]["retries"] == 2,
          f"retries={meta['stats']['retries']}")
    miss = [i for i in range(nchunks) if i not in corrupt and fetches.get(i, 0) != 1]
    extra = [i for i in corrupt if fetches.get(i, 0) != 2]
    check("T1 续传只对缺失块发Range(普通块恰好取1次)", miss == [],
          f"异常块={miss}")
    check("T1 坏块恰好取2次(首次损坏+隔离重取)", extra == [],
          f"counts={dict((i, fetches.get(i)) for i in corrupt)}")

    # ---------------------------------------------------------- T2
    print("\n### T2: 并发 1/4/8 复跑, 结果与块记录一致")
    records, walls = [], []
    for conc in (1, 4, 8):
        d, data2 = make_case(f"t2_c{conc}", size, seed=2)
        req = write_req(d, source="src.bin", chunk=chunk, concurrency=conc,
                        state="state.json", fail_at=[], corrupt_chunks=[])
        out = os.path.join(d, "out.bin")
        p = run_dl(req, out)
        check(f"T2 并发{conc} 成功", p.returncode == 0, p.stderr[-300:])
        check(f"T2 并发{conc} 文件一致", open(out, "rb").read() == data2)
        m = json.load(open(out + ".meta.json"))
        records.append([(c["i"], c["sha"]) for c in m["chunks"]])
        walls.append(m["stats"]["wall_sec"])
        ts = [c["t"] for c in m["chunks"]]
        check(f"T2 并发{conc} 块记录时间自洽",
              max(ts) - min(ts) <= m["stats"]["wall_sec"] + 1.0,
              f"span={max(ts)-min(ts):.3f}s wall={m['stats']['wall_sec']}s "
              f"吞吐={m['stats']['throughput_Bps']}B/s")
    check("T2 块映射与块记录不随并发变化",
          records[0] == records[1] == records[2])
    print(f"    完成时间: 并发1={walls[0]}s 并发4={walls[1]}s 并发8={walls[2]}s")

    # ---------------------------------------------------------- T3
    print("\n### T3: 仅头 1MB 支持 Range -> 退化整体重传并告警")
    d, data3 = make_case("t3", 3 * (1 << 20) + 777, seed=3)
    with open(os.path.join(d, "src.bin.rangelimit"), "w") as f:
        f.write(str(1 << 20))
    req = write_req(d, source="src.bin", chunk=chunk, concurrency=4,
                    state="state.json", fail_at=[], corrupt_chunks=[])
    out = os.path.join(d, "out.bin")
    p = run_dl(req, out)
    m = json.load(open(out + ".meta.json"))
    check("T3 成功完成", p.returncode == 0, p.stderr[-300:])
    check("T3 退化为整体重传 mode=full", m["mode"] == "full")
    check("T3 文件逐字节一致", open(out, "rb").read() == data3)
    check("T3 告警明确(仅头1MB支持Range/退化)",
          any("退化" in a and "1048576" in a for a in m["alerts"]),
          f"alerts={m['alerts']}")

    # ---------------------------------------------------------- T4
    print("\n### T4: 完全不支持 Range -> 退化整体重传并告警")
    d, data4 = make_case("t4", 2 * (1 << 20) + 1, seed=4)
    open(os.path.join(d, "src.bin.norange"), "w").close()
    req = write_req(d, source="src.bin", chunk=chunk, concurrency=8,
                    state="state.json", fail_at=[], corrupt_chunks=[])
    out = os.path.join(d, "out.bin")
    p = run_dl(req, out)
    m = json.load(open(out + ".meta.json"))
    check("T4 成功完成", p.returncode == 0, p.stderr[-300:])
    check("T4 mode=full", m["mode"] == "full")
    check("T4 文件逐字节一致", open(out, "rb").read() == data4)
    check("T4 告警明确", any("退化" in a for a in m["alerts"]),
          f"alerts={m['alerts']}")

    return summary()


def summary():
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"验收失败 {len(FAILURES)} 项: {FAILURES}")
        return 1
    print("全部验收项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

