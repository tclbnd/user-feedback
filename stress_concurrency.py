"""并发压力：N 个进程以相同 expected_version 同时提交，恰有一个成功。"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PUBLISH = Path(__file__).resolve().parent / "publish.py"
N = 8


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_partition(reqdir, day, value):
    rel = Path("data") / (day + ".jsonl")
    text = json.dumps({"day": day, "v": value}) + "\n"
    p = reqdir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return day, rel.as_posix(), sha256(text.encode()), 1


def write_req(path, batch_id, expected, spec):
    day, rel, digest, rows = spec
    path.write_text(json.dumps({
        "batch_id": batch_id,
        "expected_version": expected,
        "partitions": [{"day": day, "path": rel, "sha256": digest,
                        "rows": rows}],
    }), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="stress-"))
    try:
        reqdir = tmp / "req"
        reqdir.mkdir()
        wh = tmp / "wh"

        init_spec = make_partition(reqdir, "2026-05-01", 0)
        r0 = reqdir / "init.json"
        write_req(r0, "init", 0, init_spec)
        rc = subprocess.run([sys.executable, str(PUBLISH), str(r0), str(wh)],
                            capture_output=True).returncode
        assert rc == 0

        reqs = []
        specs = []
        for i in range(N):
            day = "2026-05-%02d" % (2 + i)
            spec = make_partition(reqdir, day, i)
            specs.append(spec)
            rp = reqdir / ("c%d.json" % i)
            write_req(rp, "concurrent-%d" % i, 1, spec)
            reqs.append(rp)

        procs = [subprocess.Popen([sys.executable, str(PUBLISH), str(rp),
                                   str(wh)],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for rp in reqs]
        codes = sorted(p.wait() for p in procs)
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        print("并发退出码分布:", {c: codes.count(c) for c in set(codes)})
        assert codes.count(0) == 1 and codes.count(3) == N - 1, codes

        pointer = json.loads((wh / "CURRENT.json").read_text())
        assert pointer["version"] == 2, pointer
        manifests = list((wh / "manifests").glob("*.json"))
        assert len(manifests) == 2, manifests
        print("并发后版本:", pointer["version"], "（只有一个新版本）")

        # 失败方按新期望版本依次重试，全部成功且版本严格递增
        for i, spec in enumerate(specs):
            rp = reqdir / ("retry-%d.json" % i)
            write_req(rp, "retry-%d" % i, 2 + i, spec)
            proc = subprocess.run([sys.executable, str(PUBLISH), str(rp),
                                   str(wh)],
                                  capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
        pointer = json.loads((wh / "CURRENT.json").read_text())
        assert pointer["version"] == 2 + N, pointer
        print("顺序重试后版本:", pointer["version"])

        # 最终清单每个分区哈希可复算
        manifest = json.loads((wh / pointer["manifest"]).read_text())
        for day, entry in manifest["partitions"].items():
            data = (wh / entry["path"]).read_bytes()
            assert sha256(data) == entry["sha256"]
        print("最终分区数:", len(manifest["partitions"]), "哈希全部可复算")
        print("压力测试通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()