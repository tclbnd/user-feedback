"""手动 CLI 演练：在临时目录里走一遍首发/重放/冲突/暂存恢复，并打印仓库结构。"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PUBLISH = Path(__file__).resolve().parent / "publish.py"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_jsonl(path, day, values):
    text = "".join(json.dumps({"day": day, "v": v}) + "\n" for v in values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return sha256(text.encode("utf-8")), len(values)


def run(req, wh):
    proc = subprocess.run([sys.executable, str(PUBLISH), str(req), str(wh)],
                          capture_output=True, text=True)
    print("  stdout:", proc.stdout.strip())
    if proc.stderr.strip():
        print("  stderr:", proc.stderr.strip())
    print("  exit:", proc.returncode)
    return proc.returncode


def make_request(path, batch_id, expected, specs, stop=False):
    path.write_text(json.dumps({
        "batch_id": batch_id,
        "expected_version": expected,
        "stop_after_stage": stop,
        "partitions": [
            {"day": d, "path": rel, "sha256": h, "rows": n}
            for d, rel, h, n in specs],
    }), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="demo-"))
    try:
        reqdir = tmp / "req"
        datadir = reqdir / "data"
        wh = tmp / "wh"

        s1 = write_jsonl(datadir / "2026-03-01.jsonl", "2026-03-01", [1, 2])
        s2 = write_jsonl(datadir / "2026-03-02.jsonl", "2026-03-02", [3])
        s3 = write_jsonl(datadir / "2026-03-03.jsonl", "2026-03-03", [4, 5, 6])

        r1 = reqdir / "r1.json"
        make_request(r1, "init", 0, [
            ("2026-03-01", "data/2026-03-01.jsonl", *s1),
            ("2026-03-02", "data/2026-03-02.jsonl", *s2),
            ("2026-03-03", "data/2026-03-03.jsonl", *s3),
        ])

        print("== 首发 ==")
        assert run(r1, wh) == 0
        print("== 同身份重放（期望 0，无变化）==")
        assert run(r1, wh) == 0

        bad = reqdir / "bad.json"
        make_request(bad, "init", 0, [
            ("2026-03-01", "data/2026-03-01.jsonl", "f" * 64, s1[1]),
        ])
        print("== 同批不同身份 -> 3 ==")
        assert run(bad, wh) == 3

        b1 = write_jsonl(reqdir / "bf" / "2026-03-01.jsonl",
                         "2026-03-01", [100, 200])
        b2 = write_jsonl(reqdir / "bf" / "2026-03-02.jsonl",
                         "2026-03-02", [300, 400])
        stage_req = reqdir / "stage.json"
        make_request(stage_req, "backfill", 1, [
            ("2026-03-01", "bf/2026-03-01.jsonl", *b1),
            ("2026-03-02", "bf/2026-03-02.jsonl", *b2),
        ], stop=True)
        print("== 暂存中止 -> 75 ==")
        assert run(stage_req, wh) == 75

        shutil.move(str(reqdir / "bf"), str(tmp / "bf-moved"))
        resume = reqdir / "resume.json"
        make_request(resume, "backfill", 1, [
            ("2026-03-01", "bf/2026-03-01.jsonl", *b1),
            ("2026-03-02", "bf/2026-03-02.jsonl", *b2),
        ])
        print("== 移走原始文件后同身份恢复 -> 0 ==")
        assert run(resume, wh) == 0
        print("== 恢复后重放 -> 0 ==")
        assert run(resume, wh) == 0

        print("\n仓库文件:")
        for p in sorted(wh.rglob("*")):
            if p.is_file():
                print("  ", p.relative_to(wh))
        print("\nCURRENT.json:")
        print((wh / "CURRENT.json").read_text())
        print("最新清单:")
        pointer = json.loads((wh / "CURRENT.json").read_text())
        print((wh / pointer["manifest"]).read_text())

        manifests = sorted((wh / "manifests").glob("*.json"))
        assert len(manifests) == 2, manifests
        latest = json.loads((wh / pointer["manifest"]).read_text())
        # 未回填的 day-03 保留首批来源
        assert latest["partitions"]["2026-03-03"]["batch_id"] == "init"
        assert latest["partitions"]["2026-03-01"]["batch_id"] == "backfill"
        # 所有分区可从仓库副本复算
        for day, entry in latest["partitions"].items():
            data = (wh / entry["path"]).read_bytes()
            assert sha256(data) == entry["sha256"]
            assert len(data.decode().splitlines()) == entry["rows"]
        print("\n演练全部断言通过；临时目录:", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()