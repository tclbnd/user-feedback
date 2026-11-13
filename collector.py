"""Snapshot paging collector.

A collection *generation* is permanently bound to one source snapshot token.
Page payloads, the consume cursor and row accumulator are persisted under the
staging directory before any progress becomes visible, so retries, rate-limit
responses, conditional (ETag) reads and process crashes can resume without
losing data. A fully collected token is validated and only then atomically
published; readers always see the previous complete snapshot until then.
"""
import argparse
import copy
import json
import os
import tempfile
from pathlib import Path

from source import Source

INITIAL = {"version": 0, "token": None, "rows": [],
           "diff": {"insert": [], "update": [], "delete": []}}
CRASH_EXIT = 97


# ---------------------------------------------------------------- storage --
def _atomic_write(path, payload):
    """Durable temp-file + os.replace write (crash-safe on POSIX)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            os.fsync(dfd)
            os.close(dfd)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path, default):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def _ckey(cursor):
    return "initial" if cursor is None else cursor


def _meta_path(root):
    return root / "staging" / "meta.json"


def _load_meta(root):
    path = _meta_path(root)
    return _read_json(path, None) if path.exists() else None


def _save_meta(root, meta):
    _atomic_write(_meta_path(root), meta)


def _gen_dir(root, gen_id):
    return root / "staging" / ("gen-%04d" % gen_id)


def _page_path(root, gen_id, key):
    safe = key.replace(os.sep, "_")
    return _gen_dir(root, gen_id) / ("page-%s.json" % safe)


def _default_diag():
    return {"events": [], "duplicate_rows": 0, "duplicate_pages": 0,
            "retries": {"429": 0, "410": 0, "404": 0, "304": 0}}


def _read_diagnostics(root):
    return _read_json(root / "diagnostics.json", _default_diag())


def _append_event(root, event):
    diag = _read_diagnostics(root)
    diag["events"].append(event)
    diag["events"] = diag["events"][-500:]
    _atomic_write(root / "diagnostics.json", diag)
    return diag


def _bump_diag(root, reason):
    diag = _read_diagnostics(root)
    diag["retries"][reason] = diag["retries"].get(reason, 0) + 1
    _atomic_write(root / "diagnostics.json", diag)
    return diag


# ------------------------------------------------------------- generation --
def _new_generation(root, token, gen_id=None):
    """Create (or reset) a staging generation bound to one snapshot token."""
    stage = root / "staging"
    stage.mkdir(parents=True, exist_ok=True)
    if gen_id is None:
        counter = _read_json(root / "gen-counter.json", {"next": 1})
        gen_id = counter["next"]
        _atomic_write(root / "gen-counter.json", {"next": gen_id + 1})
    gdir = _gen_dir(root, gen_id)
    if gdir.exists():
        for child in gdir.iterdir():
            child.unlink()
    gdir.mkdir(parents=True, exist_ok=True)
    meta = {"generation": gen_id, "token": token, "cursor": None,
            "seen_cursors": [], "seen_etags": [], "done": False,
            "page_count": 0, "row_count": 0, "status": "active",
            "requests": 0, "duplicate_rows": 0, "duplicate_pages": 0}
    _save_meta(root, meta)
    _atomic_write(gdir / "rows.json", {})
    marker = stage / "crash.armed"
    if marker.exists():
        marker.unlink()
    return meta


def _discard_generation(root, reason):
    """Drop all staged state for an expired token (fresh restart contract)."""
    stage = root / "staging"
    marker = stage / "crash.armed"
    if marker.exists():
        marker.unlink()
    if stage.exists():
        for child in sorted(stage.iterdir(), reverse=True):
            if child.is_dir():
                for f in child.iterdir():
                    f.unlink()
                child.rmdir()
            else:
                child.unlink()
        stage.rmdir()
    _append_event(root, {"kind": "generation-discarded", "reason": reason})


def _validate_generation(root, meta):
    """Recompute the snapshot from durable pages and cross-check the rows."""
    gdir = _gen_dir(root, meta["generation"])
    rows = {}
    seen = set()
    for key in meta["seen_cursors"]:
        page = _read_json(_page_path(root, meta["generation"], key), None)
        if page is None:
            raise ValueError("missing staged page for cursor " + repr(key))
        etag = page.get("etag")
        if not isinstance(etag, str):
            raise ValueError("page " + repr(key) + " has no valid etag")
        for item in page.get("items", []):
            rid = item["id"]
            if rid in seen:
                # Repeated id inside one token must carry identical content.
                if rows[rid] != item:
                    raise ValueError("token %s mixes versions for id %s"
                                     % (meta["token"], rid))
                continue
            seen.add(rid)
            rows[rid] = item
    staged_rows = _read_json(gdir / "rows.json", {})
    if staged_rows != rows:
        raise ValueError("staged row accumulator is inconsistent with pages")
    return rows


def _publish(root, meta):
    """Validate then atomically publish; idempotent for an unchanged token."""
    published_path = root / "published.json"
    prev = _read_json(published_path, INITIAL)
    rows_map = _validate_generation(root, meta)
    rows = sorted(rows_map.values(), key=lambda r: r["id"])

    # Re-collecting the same completed token never creates a new version.
    if prev["version"] > 0 and prev["token"] == meta["token"]:
        if prev["rows"] == rows:
            meta["status"] = "published"
            _save_meta(root, meta)
            _discard_generation(root, "idempotent-recollect")
            return prev, True

    prev_rows = {r["id"]: r for r in prev["rows"]}
    new_ids, old_ids = set(rows_map), set(prev_rows)
    inserts = sorted(new_ids - old_ids)
    deletes = sorted(old_ids - new_ids)
    updates = sorted(i for i in (new_ids & old_ids)
                     if rows_map[i] != prev_rows[i])
    result = {"version": prev["version"] + 1, "token": meta["token"],
              "rows": rows,
              "diff": {"insert": inserts, "update": updates,
                       "delete": deletes}}
    hist = root / "history"
    _atomic_write(hist / ("v%04d.json" % result["version"]), result)
    _atomic_write(published_path, result)  # readers switch atomically
    _append_event(root, {"kind": "published", "token": meta["token"],
                         "version": result["version"]})
    _discard_generation(root, "published-v%d" % result["version"])
    return result, False


# ---------------------------------------------------------------- collect --
def collect(spec, root, max_requests, crash):
    root.mkdir(parents=True, exist_ok=True)
    src = Source(json.loads(spec.read_text(encoding="utf-8")),
                 root / "source-state.json")
    budget = max_requests or 100
    requests = 0
    marker = root / "staging" / "crash.armed"

    meta = _load_meta(root)
    if meta is not None:
        latest_token = src.latest()
        if meta["token"] != latest_token and not meta.get("done"):
            # Staged work belongs to an expired source version: the contract
            # says discard it and start a fresh generation at the new start.
            _append_event(root, {"kind": "stale-staging",
                                 "old_token": meta["token"],
                                 "new_token": latest_token})
            _discard_generation(root, "token-expired-on-resume")
            meta = None

    if meta is None:
        meta = _new_generation(root, src.latest())
        _append_event(root, {"kind": "generation-started",
                             "generation": meta["generation"],
                             "token": meta["token"]})
    gen_id = meta["generation"]
    gdir = _gen_dir(root, gen_id)

    # A completed staging set only needs the atomic publish step.
    if meta.get("done"):
        result, idem = _publish(root, meta)
        diag = _read_diagnostics(root)
        diag["duplicate_rows"] = meta.get("duplicate_rows", 0)
        diag["duplicate_pages"] = meta.get("duplicate_pages", 0)
        _atomic_write(root / "diagnostics.json", diag)
        return _build_result(result, "published", requests, root, idem)

    rows = _read_json(gdir / "rows.json", {})
    duplicate_rows = meta.get("duplicate_rows", 0)
    duplicate_pages = meta.get("duplicate_pages", 0)

    while requests < budget:
        cursor = meta["cursor"]
        key = _ckey(cursor)

        # A cursor must never revisit a page already consumed in this
        # generation: revisiting means the source graph is a cursor loop.
        if key in meta["seen_cursors"]:
            raise ValueError("cursor loop detected at " + repr(key))

        staged = _read_json(_page_path(root, gen_id, key), None)
        if_none_match = staged["etag"] if staged is not None else None
        code, page = src.fetch(meta["token"], cursor, if_none_match)
        requests += 1

        if code == 429:
            _bump_diag(root, "429")
            _append_event(root, {"kind": "retry", "reason": 429,
                                 "token": meta["token"], "cursor": key,
                                 "retry_after": page.get("retry_after")})
            continue
        if code == 410:
            # Token expired: discard every staged page and open a new
            # generation bound to the replacement token, per contract.
            _bump_diag(root, "410")
            _append_event(root, {"kind": "token-expired",
                                 "old_token": meta["token"],
                                 "new_token": src.latest()})
            _discard_generation(root, "410-expired")
            meta = _new_generation(root, src.latest())
            gen_id = meta["generation"]
            gdir = _gen_dir(root, gen_id)
            rows = _read_json(gdir / "rows.json", {})
            duplicate_rows = meta.get("duplicate_rows", 0)
            duplicate_pages = meta.get("duplicate_pages", 0)
            continue
        if code == 404:
            _bump_diag(root, "404")
            raise ValueError("404 for token=%s cursor=%r"
                             % (meta["token"], key))
        if code == 304:
            # 304 is only valid when this generation already holds the
            # verified payload for this exact cursor.
            if staged is None:
                raise ValueError("304 without staged payload at " + repr(key))
            _bump_diag(root, "304")
            _append_event(root, {"kind": "conditional-read", "status": 304,
                                 "token": meta["token"], "cursor": key,
                                 "etag": page.get("etag")})
            page = staged
        else:
            if code != 200:
                raise ValueError("unexpected source status %s" % code)
            if not isinstance(page, dict) or "etag" not in page \
                    or "items" not in page or "next" not in page:
                raise ValueError("malformed 200 payload at " + repr(key))
            # 1) payload first, fully durable ...
            _atomic_write(_page_path(root, gen_id, key), page)

        items = page["items"]
        for item in items:
            rid = item["id"]
            if rid in rows:
                if rows[rid] != item:
                    raise ValueError(
                        "token %s mixes versions for id %s"
                        % (meta["token"], rid))
                duplicate_rows += 1
                continue
            rows[rid] = item

        nxt = page["next"]
        nkey = _ckey(nxt)
        if nxt is not None and (nkey in meta["seen_cursors"]
                                or nkey == key):
            raise ValueError("cursor loop detected via next=" + repr(nxt))

        # Crash point: the first successful page is durable but the consume
        # position is not yet public. Restart resumes on the same cursor and
        # the conditional read may legitimately return 304.
        if crash in ("after-stage", "before-cursor") and not marker.exists():
            _atomic_write(gdir / "rows.json", rows)
            meta["requests"] = meta.get("requests", 0) + requests
            _save_meta(root, meta)
            _atomic_write(marker, {"armed": True})
            _append_event(root, {"kind": "crash", "point": crash,
                                 "cursor": key})
            raise SystemExit(CRASH_EXIT)

        # 2) ... then the row accumulator ...
        _atomic_write(gdir / "rows.json", rows)
        # 3) ... finally publish the consume cursor/position.
        # A duplicate page is a distinct cursor serving the same payload
        # (same etag). A 304 replay of an unconsumed cursor is recovery of an
        # already-durable page, not a source-side duplicate.
        etag = page["etag"]
        seen_etags = meta.setdefault("seen_etags", [])
        replay_via_304 = code == 304
        if etag in seen_etags and not replay_via_304:
            duplicate_pages += 1
        elif etag not in seen_etags:
            seen_etags.append(etag)
        meta["seen_cursors"].append(key)
        meta["cursor"] = nxt
        meta["page_count"] += 1
        meta["row_count"] = len(rows)
        meta["duplicate_rows"] = duplicate_rows
        meta["duplicate_pages"] = duplicate_pages
        meta["requests"] = meta.get("requests", 0) + 1
        _save_meta(root, meta)

        if nxt is None:
            meta["done"] = True
            meta["status"] = "complete"
            _save_meta(root, meta)
            # Crash point: last page durable, complete version not yet visible.
            if crash == "before-publish" and not marker.exists():
                _atomic_write(marker, {"armed": True})
                _append_event(root, {"kind": "crash", "point": crash})
                raise SystemExit(CRASH_EXIT)
            result, idem = _publish(root, meta)
            diag = _read_diagnostics(root)
            diag["duplicate_rows"] = duplicate_rows
            diag["duplicate_pages"] = duplicate_pages
            _atomic_write(root / "diagnostics.json", diag)
            return _build_result(result, "published", requests, root, idem)

    # Budget exhausted: staging persists, the public view stays on the old
    # complete version; the next invocation safely resumes.
    meta["status"] = "incomplete"
    _save_meta(root, meta)
    diag = _read_diagnostics(root)
    diag["duplicate_rows"] = duplicate_rows
    diag["duplicate_pages"] = duplicate_pages
    _atomic_write(root / "diagnostics.json", diag)
    return _build_result(None, "incomplete", requests, root)


def _build_result(result, status, requests, root, idempotent=False):
    meta = _load_meta(root)
    diag = _read_diagnostics(root)
    out = {"status": status, "requests_used": requests,
           "idempotent": idempotent, "diagnostics": diag}
    if result is not None:
        out.update(result)
    else:
        out.update(_read_json(root / "published.json", INITIAL))
        out["progress"] = {
            "generation": meta["generation"], "token": meta["token"],
            "cursor": meta["cursor"],
            "pages_consumed": len(meta["seen_cursors"]),
            "rows_staged": meta["row_count"], "done": meta["done"]}
    return out


# ---------------------------------------------------------------- inspect --
def inspect(root, version):
    if version is None:
        return _read_json(root / "published.json", INITIAL)
    path = root / "history" / ("v%04d.json" % version)
    if not path.exists():
        raise ValueError("unknown version %d" % version)
    return json.loads(path.read_text(encoding="utf-8"))


# -------------------------------------------------------------------- cli --
def main():
    parser = argparse.ArgumentParser(description="Snapshot paging collector")
    parser.add_argument("root", type=Path)
    sub = parser.add_subparsers(dest="op", required=True)
    sync = sub.add_parser("collect")
    sync.add_argument("spec", type=Path)
    sync.add_argument("--max-requests", type=int)
    sync.add_argument("--crash",
                      choices=["after-stage", "before-cursor",
                               "before-publish"])
    view = sub.add_parser("inspect")
    view.add_argument("--version", type=int)
    args = parser.parse_args()
    try:
        if args.op == "collect":
            result = collect(args.spec, args.root, args.max_requests,
                             args.crash)
        else:
            result = inspect(args.root, args.version)
        print(json.dumps(result, sort_keys=True))
    except SystemExit:
        raise
    except (ValueError, KeyError, OSError) as err:
        parser.exit(2, str(err) + "\n")


if __name__ == "__main__":
    main()
