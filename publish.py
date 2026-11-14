"""数仓日分区清单发布。

协议见 README.md：
- 校验每个 JSONL 分区的 SHA-256、行数、行内日期，校验通过的副本以内容寻址
  方式存入仓库 blobs/，发布后的清单只引用仓库内副本，不依赖原始输入路径。
- 一批分区合入同一个不可变清单 manifests/<version>.json，提交时在文件锁内
  复核期望版本并原子切换 CURRENT.json 指针；并发冲突只有一个成功。
- batch_id + expected_version + 按日期排序的 (day, sha256, rows) 构成身份，
  成功批次重放幂等；同批不同身份、期望版本过时均以冲突退出 3。
- stop_after_stage=true 持久化暂存后退出 75；之后用同一身份请求即可仅凭
  仓库暂存完成发布，无需原始文件。
"""

import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

EXIT_OK = 0
EXIT_DATA = 2
EXIT_CONFLICT = 3
EXIT_STAGED = 75

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class PublishError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fsync(fd):
    try:
        os.fsync(fd)
    except OSError:
        pass


def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write(path, data):
    """临时文件 + fsync + 同目录 rename，保证原子落盘。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp." + str(os.getpid()))
    if isinstance(data, str):
        data = data.encode("utf-8")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        _fsync(f.fileno())
    os.replace(str(tmp), str(path))
    _fsync_dir(path.parent)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_request(raw):
    if not isinstance(raw, dict):
        raise PublishError(EXIT_DATA, "请求必须是 JSON 对象")
    batch_id = raw.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise PublishError(EXIT_DATA, "batch_id 必须是非空字符串")
    expected_version = raw.get("expected_version")
    if (isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0):
        raise PublishError(EXIT_DATA, "expected_version 必须是非负整数")
    partitions = raw.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise PublishError(EXIT_DATA, "partitions 必须是非空数组")

    seen = set()
    cleaned = []
    for item in partitions:
        if not isinstance(item, dict):
            raise PublishError(EXIT_DATA, "分区对象必须是 JSON 对象")
        day = item.get("day")
        if not isinstance(day, str) or not DAY_RE.match(day):
            raise PublishError(EXIT_DATA, "day 必须是 YYYY-MM-DD 字符串")
        try:
            date.fromisoformat(day)
        except ValueError:
            raise PublishError(EXIT_DATA, "day 不是合法日期: " + str(day))
        if day in seen:
            raise PublishError(EXIT_DATA, "同一批不可重复 day: " + day)
        seen.add(day)

        rel_path = item.get("path")
        if not isinstance(rel_path, str) or not rel_path:
            raise PublishError(EXIT_DATA, "path 必须是非空字符串")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not SHA_RE.match(digest):
            raise PublishError(EXIT_DATA, "sha256 必须是 64 位小写十六进制")
        rows = item.get("rows")
        if (isinstance(rows, bool) or not isinstance(rows, int)
                or rows < 0):
            raise PublishError(EXIT_DATA, "rows 必须是非负整数")
        cleaned.append({"day": day, "path": rel_path,
                        "sha256": digest, "rows": rows})

    stop = bool(raw.get("stop_after_stage", False))
    return batch_id, expected_version, cleaned, stop


def request_identity(expected_version, partitions):
    """身份：expected_version + 按日期排序的 (day, sha256, rows)。

    用 JSON 字符串规范化，元组/列表差异不影响比较，也便于落盘。
    """
    triples = sorted((p["day"], p["sha256"], p["rows"]) for p in partitions)
    payload = {"expected_version": expected_version, "partitions": triples}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _identity_expected_version(identity):
    try:
        return json.loads(identity)["expected_version"]
    except (ValueError, KeyError, TypeError):
        return None


def find_published(warehouse, batch_id):
    """扫描不可变清单，返回该 batch_id 的 (manifest, 清单相对路径)。"""
    mdir = warehouse / "manifests"
    if not mdir.is_dir():
        return None
    for mp in sorted(mdir.glob("*.json")):
        try:
            manifest = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("batch_id") == batch_id:
            return manifest, mp.relative_to(warehouse).as_posix()
    return None


def stored_manifest_identity(manifest):
    """读取清单持久化的批次身份；兼容早期只存合并分区的清单。"""
    if isinstance(manifest.get("batch_identity"), str):
        return manifest["batch_identity"]
    triples = sorted(
        (day, entry["sha256"], entry["rows"])
        for day, entry in manifest["partitions"].items()
    )
    payload = {"expected_version": manifest["version"] - 1,
               "partitions": triples}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def load_current(warehouse):
    pointer = warehouse / "CURRENT.json"
    if not pointer.exists():
        return None
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        version = data["version"]
        manifest_rel = data["manifest"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise PublishError(EXIT_DATA, "CURRENT.json 不可读: " + str(e))
    if not isinstance(version, int) or not isinstance(manifest_rel, str):
        raise PublishError(EXIT_DATA, "CURRENT.json 字段非法")
    return {"version": version, "manifest": manifest_rel}


def stage_path(warehouse, batch_id):
    safe = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:32]
    return warehouse / "staging" / safe / "stage.json"


def load_stage(warehouse, batch_id):
    sp = stage_path(warehouse, batch_id)
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PublishError(EXIT_DATA, "暂存元数据损坏: " + str(sp))


def blob_rel(digest):
    return "blobs/" + digest


def blob_intact(warehouse, digest):
    p = warehouse / blob_rel(digest)
    if not p.is_file():
        return False
    try:
        return sha256_file(p) == digest
    except OSError:
        return False


def validate_partition_file(source, spec):
    """校验单个 JSONL 分区：整体 SHA-256、行数、每行对象且 day 匹配。"""
    if not source.is_file():
        raise PublishError(EXIT_DATA,
                           "分区 " + spec["day"] + " 的输入文件不存在: "
                           + str(source))
    try:
        raw = source.read_bytes()
    except OSError as e:
        raise PublishError(EXIT_DATA, "读取 " + str(source) + " 失败: " + str(e))

    actual_digest = sha256_bytes(raw)
    if actual_digest != spec["sha256"]:
        raise PublishError(EXIT_DATA,
                           "分区 " + spec["day"] + " SHA-256 不符: 期望 "
                           + spec["sha256"] + " 实际 " + actual_digest)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PublishError(EXIT_DATA,
                           "分区 " + spec["day"] + " 不是 UTF-8: " + str(e))

    lines = text.splitlines()
    if len(lines) != spec["rows"]:
        raise PublishError(EXIT_DATA,
                           "分区 " + spec["day"] + " 行数不符: 期望 "
                           + str(spec["rows"]) + " 实际 " + str(len(lines)))
    for idx, line in enumerate(lines, 1):
        if not line.strip():
            raise PublishError(EXIT_DATA,
                               "分区 " + spec["day"] + " 第 " + str(idx)
                               + " 行为空")
        try:
            obj = json.loads(line)
        except ValueError as e:
            raise PublishError(EXIT_DATA,
                               "分区 " + spec["day"] + " 第 " + str(idx)
                               + " 行不是合法 JSON: " + str(e))
        if not isinstance(obj, dict):
            raise PublishError(EXIT_DATA,
                               "分区 " + spec["day"] + " 第 " + str(idx)
                               + " 行不是 JSON 对象")
        if obj.get("day") != spec["day"]:
            raise PublishError(EXIT_DATA,
                               "分区 " + spec["day"] + " 第 " + str(idx)
                               + " 行 day 字段不符: " + repr(obj.get("day")))
    return raw


def stage_blob(warehouse, digest, data):
    """内容寻址保存已校验副本；已存在且摘要一致则复用，损坏则修复替换。"""
    target = warehouse / blob_rel(digest)
    if target.is_file() and sha256_file(target) == digest:
        return
    atomic_write(target, data)
    if sha256_file(target) != digest:
        raise PublishError(EXIT_DATA, "暂存 blob 校验失败: " + digest)


def persist_stage(warehouse, batch_id, identity, staged_partitions):
    sp = stage_path(warehouse, batch_id)
    payload = {
        "batch_id": batch_id,
        "identity": identity,
        "partitions": staged_partitions,
        "published": False,
    }
    atomic_write(sp, json.dumps(payload, ensure_ascii=False,
                                sort_keys=True) + "\n")


def build_manifest(warehouse, current, batch_id, identity, staged_partitions):
    version = (current["version"] if current else 0) + 1
    if current is None:
        merged = {}
    else:
        prev_path = warehouse / current["manifest"]
        if not prev_path.is_file():
            raise PublishError(EXIT_DATA, "上一版清单丢失: " + str(prev_path))
        try:
            prev = json.loads(prev_path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise PublishError(EXIT_DATA, "上一版清单损坏: " + str(e))
        merged = dict(prev["partitions"])

    # 本批分区与来源关系（batch_id -> 本批提交的 day 及身份要素）。
    batch_sources = {}
    for part in staged_partitions:
        merged[part["day"]] = {
            "batch_id": batch_id,
            "path": part["blob"],
            "sha256": part["sha256"],
            "rows": part["rows"],
        }
        batch_sources[part["day"]] = {
            "sha256": part["sha256"],
            "rows": part["rows"],
            "path": part["blob"],
        }
    return {
        "version": version,
        "batch_id": batch_id,
        "expected_version": _identity_expected_version(identity),
        "batch_identity": identity,
        "batch_sources": batch_sources,
        "partitions": merged,
    }


def commit(warehouse, batch_id, identity, staged_partitions):
    """持锁复核期望版本，写不可变清单并原子切换读者指针。"""
    lock_dir = warehouse / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "publish.lock"
    with lock_file.open("a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)

        # 锁内重查：可能已被其它进程发布（重放/冲突）。
        published = find_published(warehouse, batch_id)
        if published is not None:
            manifest, _ = published
            if stored_manifest_identity(manifest) == identity:
                return manifest, True  # 重放，CURRENT 不变
            raise PublishError(EXIT_CONFLICT,
                               "batch_id " + batch_id + " 已以不同身份发布")

        current = load_current(warehouse)
        cur_version = current["version"] if current else 0
        if cur_version != _identity_expected_version(identity):
            raise PublishError(EXIT_CONFLICT,
                               "期望版本 "
                               + str(_identity_expected_version(identity))
                               + " 已过时，当前版本 " + str(cur_version))

        # 暂存副本在提交前必须仍然完好。
        for part in staged_partitions:
            if not blob_intact(warehouse, part["sha256"]):
                raise PublishError(EXIT_DATA,
                                   "暂存副本丢失或损坏: " + part["sha256"])

        manifest = build_manifest(warehouse, current, batch_id,
                                  identity, staged_partitions)
        manifest_rel = "manifests/%06d.json" % manifest["version"]
        atomic_write(warehouse / manifest_rel,
                     json.dumps(manifest, ensure_ascii=False,
                                sort_keys=True) + "\n")
        pointer = {"version": manifest["version"], "manifest": manifest_rel}
        atomic_write(warehouse / "CURRENT.json",
                     json.dumps(pointer, ensure_ascii=False) + "\n")

        sp = stage_path(warehouse, batch_id)
        if sp.exists():
            try:
                stage = json.loads(sp.read_text(encoding="utf-8"))
                stage["published"] = True
                stage["published_version"] = manifest["version"]
                atomic_write(sp, json.dumps(stage, ensure_ascii=False,
                                            sort_keys=True) + "\n")
            except (OSError, ValueError):
                pass  # 标记失败不影响发布，重放由清单扫描兜底
        return manifest, False


def main(request_path, warehouse_path):
    try:
        try:
            raw = json.loads(Path(request_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise PublishError(EXIT_DATA, "请求文件读取/解析失败: " + str(e))

        batch_id, expected_version, partitions, stop_after_stage = \
            validate_request(raw)
        identity = request_identity(expected_version, partitions)
        warehouse = Path(warehouse_path)
        warehouse.mkdir(parents=True, exist_ok=True)
        request_dir = Path(request_path).resolve().parent

        # 1) 已成功发布：同身份幂等重放（即使已有更高版本），异身份冲突。
        published = find_published(warehouse, batch_id)
        if published is not None:
            manifest, _ = published
            if stored_manifest_identity(manifest) == identity:
                print("batch " + batch_id + " 已发布为版本 "
                      + str(manifest["version"]) + "，重放无操作")
                return EXIT_OK
            raise PublishError(EXIT_CONFLICT,
                               "batch_id " + batch_id + " 已存在且身份不同")

        # 2) 期望版本预检（锁内还会权威复核）。先于暂存判定，使过时版本
        #    残留的旧暂存不会阻碍同 batch_id 的新一轮请求。
        current = load_current(warehouse)
        cur_version = current["version"] if current else 0
        if cur_version != expected_version:
            raise PublishError(EXIT_CONFLICT,
                               "期望版本 " + str(expected_version)
                               + " 过时，当前版本 " + str(cur_version))

        # 3) 已有暂存：同版本同身份可直接复用仓库副本；同版本异身份冲突；
        #    版本不同（理论上已被上面的预检拦截）则丢弃旧暂存重建。
        stage = load_stage(warehouse, batch_id)
        staged_parts = []
        if stage is not None:
            old_identity = stage.get("identity", "")
            old_expected = _identity_expected_version(old_identity)
            if old_expected == expected_version and old_identity != identity:
                raise PublishError(EXIT_CONFLICT,
                                   "batch_id " + batch_id
                                   + " 暂存身份与请求不一致")
            if old_identity == identity:
                staged_parts = stage.get("partitions", [])
                for part in staged_parts:
                    if not blob_intact(warehouse, part["sha256"]):
                        staged_parts = []  # 副本不全，退回重新校验原始文件
                        break

        staged_by_day = {p["day"]: p for p in staged_parts}

        # 4) 校验并暂存每个分区；已被完好暂存覆盖的分区不再读原始路径。
        result_parts = []
        for spec in partitions:
            already = staged_by_day.get(spec["day"])
            if (already and already["sha256"] == spec["sha256"]
                    and already["rows"] == spec["rows"]
                    and blob_intact(warehouse, spec["sha256"])):
                result_parts.append(already)
                continue
            source = request_dir / spec["path"]
            data = validate_partition_file(source, spec)
            stage_blob(warehouse, spec["sha256"], data)
            result_parts.append({
                "day": spec["day"],
                "sha256": spec["sha256"],
                "rows": spec["rows"],
                "blob": blob_rel(spec["sha256"]),
            })

        persist_stage(warehouse, batch_id, identity, result_parts)

        if stop_after_stage:
            print("batch " + batch_id
                  + " 已校验并暂存，按请求中止（CURRENT 未变更）")
            return EXIT_STAGED

        # 5) 持锁原子提交。
        manifest, replayed = commit(warehouse, batch_id, identity,
                                    result_parts)
        if replayed:
            print("batch " + batch_id + " 已发布为版本 "
                  + str(manifest["version"]) + "，重放无操作")
        else:
            print("batch " + batch_id + " 发布成功，新版本 "
                  + str(manifest["version"]) + "，分区数 "
                  + str(len(manifest["partitions"])))
        return EXIT_OK
    except PublishError as e:
        print("发布失败: " + str(e), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python3 publish.py request.json warehouse",
              file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
