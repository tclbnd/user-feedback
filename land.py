"""Local sharded object landing.

Resumable range acquisition bound to (object, version, byte range, sha256),
journal-persisted verified ranges, whole-object validation, and atomic
generation pointer publish. Old generations stay readable; quarantined
objects are never indexed.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CRASH_EXIT = 97

# quarantine reasons
BAD_DECLARATION = "bad-declaration"    # invalid range/version declaration (not retryable)
INVALID_PAYLOAD = "invalid-payload"    # short read / wrong content, retry exhausted
RANGE_CONFLICT = "range-conflict"      # overlapping ranges disagree
MISSING_RANGES = "missing-ranges"      # coverage gaps
DIGEST_MISMATCH = "digest-mismatch"    # whole-object length/sha256 mismatch

RESERVED = {"current.json"}


def _safe_segment(name, what):
    if (not isinstance(name, str) or not name or name in (".", "..")
            or "/" in name or "\\" in name or name.startswith(".")
            or name in RESERVED):
        raise ValueError(f"unsafe {what}: {name!r}")
    return name


def _load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _atomic_write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Journal:
    """Persistent registry of verified ranges for one generation (staging)."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"objects": {}}
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("objects"), dict):
                self.data = loaded
        except (OSError, ValueError):
            pass

    def _flush_locked(self):
        _atomic_write(self.path, json.dumps(self.data, sort_keys=True).encode())

    def register(self, oid, start, end, digest, relpath):
        with self.lock:
            self.data["objects"].setdefault(oid, {"ranges": []})["ranges"].append(
                {"start": start, "end": end, "sha256": digest, "path": relpath})
            self._flush_locked()

    def ranges(self, oid):
        return list(self.data["objects"].get(oid, {}).get("ranges", []))

    def replace_objects(self, objects):
        with self.lock:
            self.data["objects"] = objects
            self._flush_locked()

    def referenced(self):
        return {r["path"] for entry in self.data["objects"].values()
                for r in entry.get("ranges", [])}


def recover_staging(staging):
    """Re-check registered range boundaries; drop unconfirmable ranges and
    remove staging bytes not referenced by the journal."""
    journal = Journal(staging / "journal.json")
    verified = {}
    changed = False
    for oid, entry in journal.data["objects"].items():
        kept = []
        for r in entry.get("ranges", []):
            try:
                raw = (staging / r["path"]).read_bytes()
                ok = (len(raw) == r["end"] - r["start"]
                      and hashlib.sha256(raw).hexdigest() == r["sha256"])
            except (OSError, KeyError, TypeError):
                ok = False
            if ok:
                kept.append(r)
            else:
                changed = True
        if kept:
            verified[oid] = {"ranges": kept}
        else:
            changed = True
    if changed:
        journal.replace_objects(verified)
    referenced = journal.referenced()
    if staging.exists():
        for path in sorted(staging.rglob("*"), reverse=True):
            rel = path.relative_to(staging).as_posix()
            if path.is_file() and rel != "journal.json" and rel not in referenced:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    return journal


class _CrashHook:
    """Fault injector: hard-exits 97 once, at the chosen commit boundary."""

    def __init__(self, point):
        self.point = point
        self.fired = False
        self.lock = threading.Lock()

    def fire(self, point):
        if point != self.point:
            return
        with self.lock:
            if self.fired:
                return
            self.fired = True
        os._exit(CRASH_EXIT)


class _Context:
    def __init__(self, staging, gen_dir, journal, hook):
        self.staging = staging
        self.gen_dir = gen_dir
        self.journal = journal
        self.hook = hook


def _valid_declaration(chunk, version, length):
    """Bind chunk to object version and byte range. Invalid declarations are
    not retryable."""
    try:
        if chunk["version"] != version:
            return None
        start, end, digest = chunk["start"], chunk["end"], chunk["sha256"]
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if not 0 <= start < end <= length:
            return None
        if not isinstance(digest, str):
            return None
        return start, end, digest
    except (KeyError, TypeError):
        return None


def _chunk_bytes(chunk, want_len, want_sha):
    """Return (validated_bytes, used_retry). An invalid payload is retried at
    most once via the chunk's retry field."""

    def decode(key):
        try:
            return base64.b64decode(chunk[key], validate=True)
        except (KeyError, binascii.Error, ValueError, TypeError):
            return None

    def ok(raw):
        return (raw is not None and len(raw) == want_len
                and hashlib.sha256(raw).hexdigest() == want_sha)

    raw = decode("payload")
    if ok(raw):
        return raw, False
    raw = decode("retry")
    if ok(raw):
        return raw, True
    return None, "retry" in chunk


def _process_object(ctx, obj):
    oid = obj.get("id") if isinstance(obj, dict) else None
    _safe_segment(oid, "object id")
    verified = {}
    fetched = 0
    reused = 0
    detail = {"id": oid, "verified_ranges": [], "retries": 0}

    def done(**kw):
        detail["verified_ranges"] = [[s, e] for (s, e) in sorted(verified)]
        kw.setdefault("detail", detail)
        kw.setdefault("fetched", fetched)
        kw.setdefault("reused", reused)
        return kw

    try:
        version, length, digest = obj["version"], obj["length"], obj["sha256"]
        chunks = obj["chunks"]
        if (not isinstance(length, int) or length < 0
                or not isinstance(digest, str) or not isinstance(chunks, list)):
            raise ValueError("bad object declaration")
    except (KeyError, TypeError, ValueError):
        return done(quarantine={"id": oid, "reason": BAD_DECLARATION})

    journal_ranges = {(r["start"], r["end"]): r for r in ctx.journal.ranges(oid)}
    for chunk in chunks:
        decl = _valid_declaration(chunk, version, length) if isinstance(chunk, dict) else None
        if decl is None:
            return done(quarantine={"id": oid, "reason": BAD_DECLARATION})
        start, end, csha = decl
        key = (start, end)
        if key in verified:
            continue
        jour = journal_ranges.get(key)
        if jour is not None and jour["sha256"] == csha:
            # reuse a confirmed range of the same object version
            verified[key] = (ctx.staging / jour["path"]).read_bytes()
            reused += 1
            continue
        raw, used_retry = _chunk_bytes(chunk, end - start, csha)
        if used_retry:
            detail["retries"] += 1
        if raw is None:
            return done(quarantine={"id": oid, "reason": INVALID_PAYLOAD})
        relpath = "pieces/{}/{:016d}-{:016d}.part".format(oid, start, end)
        dest = ctx.staging / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, raw)
        ctx.hook.fire("after-piece")     # crash: piece on disk, unregistered
        ctx.journal.register(oid, start, end, csha, relpath)
        ctx.hook.fire("after-range")     # crash: range registered
        verified[key] = raw
        fetched += 1

    order = sorted(verified.items())
    # overlapping ranges must agree byte-for-byte
    run = None
    for (s, e), data in order:
        if run is not None and s < run[0][1]:
            (rs, re_), rdata = run
            hi = min(e, re_)
            if rdata[s - rs:hi - rs] != data[0:hi - s]:
                return done(quarantine={"id": oid, "reason": RANGE_CONFLICT})
        if run is None or e > run[0][1]:
            run = ((s, e), data)
    # full coverage of [0, length); uncovered positions are missing shards
    cursor = 0
    for (s, e) in order:
        if e <= cursor:
            continue
        if s > cursor:
            return done(quarantine={"id": oid, "reason": MISSING_RANGES})
        cursor = e
    if cursor < length:
        return done(quarantine={"id": oid, "reason": MISSING_RANGES})
    # assemble and validate the whole object before publish
    out = bytearray()
    cursor = 0
    for (s, e), data in order:
        if e <= cursor:
            continue
        out += data[max(0, cursor - s):]
        cursor = max(cursor, e)
    if len(out) != length or hashlib.sha256(bytes(out)).hexdigest() != digest:
        return done(quarantine={"id": oid, "reason": DIGEST_MISMATCH})
    _atomic_write(ctx.gen_dir / oid, bytes(out))
    return done(published={"id": oid, "length": length, "sha256": digest})


def _public(current):
    keys = ("generation", "published", "quarantine", "details",
            "fetched_ranges", "reused_ranges")
    return {k: current[k] for k in keys if k in current}


def land(root, data, workers, crash):
    generation = _safe_segment(data["generation"], "generation")
    objects = data["objects"]
    if not isinstance(objects, list):
        raise ValueError("objects must be a list")
    root.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    current = _load_json(root / "current.json")
    history = current.get("history", []) if current else []
    gen_dir = root / generation
    if generation in history:
        # a published generation is immutable: same manifest replays its
        # result, a different one is rejected
        stored = _load_json(gen_dir / "manifest.json")
        if stored is not None and stored.get("canonical") == canonical:
            if current and current["generation"] == generation:
                return _public(current)
            return _load_json(gen_dir / "result.json")
        raise ValueError("generation {!r} already published".format(generation))

    staging_root = root / ".staging"
    staging = staging_root / generation
    staging.mkdir(parents=True, exist_ok=True)
    for child in staging_root.iterdir():  # orphan staging of other generations
        if child.is_dir() and child.name != generation:
            shutil.rmtree(child, ignore_errors=True)
    journal = recover_staging(staging)
    gen_dir.mkdir(exist_ok=True)
    ctx = _Context(staging, gen_dir, journal, _CrashHook(crash))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda o: _process_object(ctx, o), objects))

    published = sorted((r["published"] for r in results if "published" in r),
                       key=lambda d: d["id"])
    quarantine = sorted((r["quarantine"] for r in results if "quarantine" in r),
                        key=lambda d: d["id"])
    result = {
        "generation": generation,
        "published": published,
        "quarantine": quarantine,
        "details": sorted((r["detail"] for r in results), key=lambda d: d["id"]),
        "fetched_ranges": sum(r["fetched"] for r in results),
        "reused_ranges": sum(r["reused"] for r in results),
    }
    _atomic_write(gen_dir / "manifest.json",
                  json.dumps({"canonical": canonical}, sort_keys=True).encode())
    ctx.hook.fire("before-pointer")      # crash: data ready, pointer unwritten
    _atomic_write(gen_dir / "result.json",
                  json.dumps(result, sort_keys=True).encode())
    pointer = dict(result)
    pointer["history"] = history + ([] if generation in history else [generation])
    _atomic_write(root / "current.json", json.dumps(pointer, sort_keys=True).encode())
    shutil.rmtree(staging, ignore_errors=True)  # published data no longer staged
    return result


def state(root, generation=None):
    current = _load_json(root / "current.json")
    if not current:
        if generation is not None:
            raise KeyError(generation)
        return {"generation": None, "published": [], "quarantine": []}
    if generation is None:
        return _public(current)
    if generation not in current.get("history", []):
        raise KeyError(generation)  # unpublished generations are not readable
    stored = _load_json(root / generation / "result.json")
    if stored is None:
        raise KeyError(generation)
    return stored


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    sub = p.add_subparsers(dest="op", required=True)
    load = sub.add_parser("land")
    load.add_argument("manifest", type=Path)
    load.add_argument("--workers", type=int, default=3)
    load.add_argument("--crash", choices=["after-piece", "after-range", "before-pointer"])
    view = sub.add_parser("inspect")
    view.add_argument("--generation")
    a = p.parse_args()
    try:
        if a.op == "land":
            out = land(a.root, json.loads(a.manifest.read_text()), a.workers, a.crash)
        else:
            out = state(a.root, a.generation)
        print(json.dumps(out, sort_keys=True))
    except (OSError, ValueError, KeyError) as err:
        p.exit(2, str(err) + "\n")


if __name__ == "__main__":
    main()
