"""Crash-safe, resumable snapshot collector over the fixture source transport.

Design summary
--------------
* A *generation* binds one source snapshot token to one cursor walk. Its whole
  temporary state lives under ``gen-<seq>/`` and is written atomically, so a
  process kill at any moment leaves either the old durable state or the new
  one, never a torn file.
* Every validated 200 page payload is staged to disk *before* the next cursor
  becomes visible, which makes the ``after-stage`` / ``before-cursor`` crash
  points safe; recovery reuses the staged payload through an ETag conditional
  read (304) instead of re-mutating source progress.
* Token expiry (410) abandons the whole generation (archived, never resumed),
  and a fresh generation starts from the new token's first page. Cursor rings
  and unknown cursors (404) are hard contract violations.
* Publication validates the complete chain re-read from disk, then writes the
  versioned history and swaps the public view atomically. Readers therefore
  always observe a complete snapshot.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from source import Source


class ContractError(Exception):
    """The source violated the page/token/cursor contract."""


def dump_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


EMPTY_DIFF = {"insert": [], "update": [], "delete": []}


class Collector:
    def __init__(self, spec, root, max_requests, crash=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.src = Source(json.loads(Path(spec).read_text()),
                          self.root / "source-state.json")
        self.budget = max_requests or 100
        self.crash = crash
        self.requests_this_run = 0
        self.meta = load_json(self.root / "meta.json", {"next_gen": 1})
        self.pub_path = self.root / "published.json"
        self.gen = None  # in-memory generation state dict

    # ------------------------------------------------------------------ state
    def published(self):
        return load_json(self.pub_path,
                         {"version": 0, "token": None, "rows": [],
                          "diff": dict(EMPTY_DIFF)})

    def _save_meta(self):
        dump_atomic(self.root / "meta.json", self.meta)

    def _gen_dir(self, seq):
        return self.root / ("gen-%d" % seq)

    def _archive(self, gen_dir):
        if gen_dir.exists():
            dest = self.root / "archive" / gen_dir.name
            dest.parent.mkdir(exist_ok=True)
            if dest.exists():
                stamp = str(int(time.time() * 1000))
                dest = dest.with_name(gen_dir.name + "-" + stamp)
            os.replace(gen_dir, dest)

    def _new_generation(self, token, reason):
        seq = self.meta["next_gen"]
        self.meta["next_gen"] = seq + 1
        self._save_meta()
        gen_dir = self._gen_dir(seq)
        self._archive(gen_dir)
        gen_dir.mkdir(parents=True)
        self.gen = {
            "seq": seq,
            "token": token,
            "cursor": None,
            "done": False,
            "published": False,
            "pages": [],          # [{name,cursor,etag,next,seq}]
            "seen_cursors": [],   # cursors already fetched in this generation
            "rows": {},
            "retry_events": [],
            "etag_304": 0,
            "duplicate_pages": 0,
            "duplicate_rows": 0,
            "etag_seen": {},
            "start_reason": reason,
        }
        self._save_gen()
        return gen_dir

    def _save_gen(self):
        dump_atomic(self._gen_dir(self.gen["seq"]) / "gen.json", self.gen)

    def open_generation(self):
        """Resume a durable generation, or start a new one for latest token."""
        latest = self.src.latest()
        pub = self.published()
        gen_dirs = sorted((p for p in self.root.glob("gen-*") if p.is_dir()),
                          key=lambda p: int(p.name.split("-")[1]))
        # A generation is only resumable while it is bound to the current
        # snapshot token and has not been published yet. A completed-but-
        # unpublished generation (the before-publish crash state) MUST be
        # resumed so its validated chain can be published; only published or
        # foreign-token generations are stale.
        for d in gen_dirs:
            g = load_json(d / "gen.json", None)
            if g is None or g.get("token") != latest or g.get("published"):
                self._archive(d)
        gen_dirs = [p for p in gen_dirs if p.exists()]
        if gen_dirs:
            gen_dir = gen_dirs[-1]
            self.gen = load_json(gen_dir / "gen.json")
            return gen_dir
        if pub["token"] == latest:
            return None  # nothing to do: latest token already fully published
        return self._new_generation(latest, "start")

    # ------------------------------------------------------------------ fetch
    def _record_retry(self, code, reason, cursor):
        ev = {"code": code, "reason": reason,
              "cursor": cursor, "request": self.requests_this_run}
        self.gen["retry_events"].append(ev)
        self._save_gen()
        return ev

    def run(self):
        gen_dir = self.open_generation()
        if gen_dir is None:
            return self._already()
        # Backwards-compatible defaults when resuming an older on-disk state.
        for key, default in (("retry_events", []), ("etag_304", 0),
                             ("duplicate_pages", 0), ("duplicate_rows", 0),
                             ("etag_seen", {}),
                             ("seen_cursors", []), ("rows", {}),
                             ("done", False), ("published", False)):
            self.gen.setdefault(key, default)

        while not self.gen["done"]:
            if self.requests_this_run >= self.budget:
                return self._incomplete()
            cursor = self.gen["cursor"]
            if cursor is not None and cursor in self.gen["seen_cursors"]:
                raise ContractError(
                    "cursor ring detected at cursor %r within token %r"
                    % (cursor, self.gen["token"]))

            page_name = "page-%03d.json" % len(self.gen["pages"])
            staged = gen_dir / page_name
            if_none_match = None
            prev = None
            if staged.exists():
                # Resume after a crash: only reuse a payload validated under
                # this same generation + cursor, via a conditional read.
                prev = load_json(staged)
                if prev["cursor"] != cursor or \
                        prev["token"] != self.gen["token"]:
                    raise ContractError(
                        "staged page is bound to another cursor/token")
                if_none_match = prev["etag"]

            code, payload = self.src.fetch(
                self.gen["token"], cursor, if_none_match)
            self.requests_this_run += 1

            if code == 429:
                self._record_retry(429, "rate_limited", cursor)
                delay = payload.get("retry_after", 0)
                if delay:
                    time.sleep(min(float(delay), 5.0))
                continue
            if code == 410:
                self._record_retry(410, "token_expired", cursor)
                self._save_gen()
                # Contract: discard the whole old staging, start a new
                # generation bound to the replacement token.
                self._archive(gen_dir)
                new_token = self.src.latest()
                if new_token == self.gen["token"]:
                    raise ContractError(
                        "token expired and no replacement token is available")
                gen_dir = self._new_generation(new_token, "token_expired")
                continue
            if code == 404:
                raise ContractError(
                    "unknown cursor %r for token %r"
                    % (cursor, self.gen["token"]))
            if code not in (200, 304):
                raise ContractError("unexpected source status %s" % code)

            etag = payload["etag"]
            if code == 304:
                if prev is None or not staged.exists():
                    raise ContractError(
                        "304 without a validated staged payload")
                if etag != if_none_match:
                    raise ContractError("304 etag mismatch")
                page = prev
                self.gen["etag_304"] += 1
            else:
                page = {"token": self.gen["token"], "cursor": cursor,
                        "etag": etag, "items": payload["items"],
                        "next": payload["next"],
                        "seq": len(self.gen["pages"])}
                self._validate_payload(page)
                # Stage the validated payload first ...
                dump_atomic(staged, page)
                if self.crash in ("after-stage", "before-cursor"):
                    self._crash()
            # Folding rows is idempotent: on a 304 resume the page was staged
            # before the crash, but gen.json may never have recorded its rows,
            # so the durable index must be rebuilt from the validated payload.
            self._ingest(page)
            self.gen["pages"].append({
                "name": page_name, "cursor": page["cursor"],
                "etag": etag, "next": page["next"], "seq": page["seq"]})
            self.gen["seen_cursors"].append(cursor)
            self.gen["cursor"] = page["next"]
            self.gen["done"] = page["next"] is None
            if self.gen["done"] and self.crash == "before-publish":
                self._save_gen()
                self._crash()
            self._save_gen()

        return self._publish(gen_dir)

    def _crash(self):
        # os._exit bypasses cleanup, like a hard process kill; the durable
        # state on disk is the only thing the recovery run may rely on.
        sys.stderr.write("injected crash (%s), exiting 97\n" % self.crash)
        sys.stderr.flush()
        os._exit(97)

    # --------------------------------------------------------------- ingest
    def _validate_payload(self, page):
        """Per-page contract: shape and stable composite sort key (sort,id)."""
        if page["token"] != self.gen["token"]:
            raise ContractError("page token does not match generation token")
        last = None
        for row in page["items"]:
            if set(row) != {"id", "sort", "value"} or \
                    not isinstance(row["id"], str) or \
                    not isinstance(row["sort"], int) or \
                    not isinstance(row["value"], str):
                raise ContractError("malformed row %r" % (row,))
            key = (row["sort"], row["id"])
            if last is not None and key < last:
                raise ContractError(
                    "page %r violates (sort,id) ordering: %r after %r"
                    % (page["cursor"], key, last))
            last = key

    def _ingest(self, page):
        """Fold a fresh 200 page into the generation's row set."""
        etag = page["etag"]
        # Same etag arriving again (a duplicated page under another cursor):
        # the content is already captured, only record the duplication.
        if etag in self.gen["etag_seen"]:
            self.gen["duplicate_pages"] += 1
        else:
            self.gen["etag_seen"][etag] = page["cursor"]
        for row in page["items"]:
            rid = row["id"]
            if rid in self.gen["rows"]:
                # Same token => same id must carry identical content.
                if self.gen["rows"][rid] != row:
                    raise ContractError(
                        "id %r appears with different content under token %r"
                        % (rid, self.gen["token"]))
                self.gen["duplicate_rows"] += 1
            else:
                self.gen["rows"][rid] = row

    # -------------------------------------------------------------- publish
    def _validate_full(self, gen_dir):
        """Re-read every staged page from disk and verify the complete chain."""
        g = load_json(gen_dir / "gen.json")
        if not g["done"] or not g["pages"]:
            raise ContractError("generation is not complete")
        rebuilt = {}
        cursor = None
        for i, meta in enumerate(g["pages"]):
            page = load_json(gen_dir / meta["name"])
            if page["token"] != g["token"]:
                raise ContractError("mixed tokens in one generation")
            if page["cursor"] != cursor or meta["cursor"] != cursor:
                raise ContractError("page chain broken at page %d" % i)
            if meta["etag"] != page["etag"] or meta["next"] != page["next"]:
                raise ContractError("page metadata mismatch at page %d" % i)
            self._validate_payload(page)
            for row in page["items"]:
                if row["id"] in rebuilt and rebuilt[row["id"]] != row:
                    raise ContractError(
                        "conflicting duplicate id %r" % row["id"])
                rebuilt.setdefault(row["id"], row)
            cursor = page["next"]
        if cursor is not None:
            raise ContractError("chain does not end with next=null")
        if rebuilt != g["rows"]:
            raise ContractError("staged rows do not match durable index")
        return rebuilt

    def _publish(self, gen_dir):
        rows_by_id = self._validate_full(gen_dir)
        prev = self.published()
        prev_ids = {r["id"]: r for r in prev["rows"]}
        new_ids = set(rows_by_id)
        old_ids = set(prev_ids)
        diff = {
            "insert": sorted(new_ids - old_ids),
            "delete": sorted(old_ids - new_ids),
            "update": sorted(k for k in new_ids & old_ids
                             if rows_by_id[k] != prev_ids[k]),
        }
        result = {
            "version": prev["version"] + 1,
            "token": self.gen["token"],
            "rows": sorted(rows_by_id.values(), key=lambda r: r["id"]),
            "diff": diff,
        }
        # Versioned history first, then the atomic public-view swap.
        history = self.root / "history"
        dump_atomic(history / ("v%d.json" % result["version"]), result)
        dump_atomic(self.pub_path, result)
        self.gen["published"] = True
        self.gen["published_version"] = result["version"]
        self._save_gen()
        self._archive(gen_dir)
        out = dict(result)
        out["diagnostics"] = self._diagnostics(complete=True)
        return out

    # -------------------------------------------------------------- outputs
    def _diagnostics(self, complete):
        g = self.gen
        return {
            "generation": g["seq"],
            "token": g["token"],
            "complete": complete,
            "requests_this_run": self.requests_this_run,
            "pages_fetched": len(g["pages"]),
            "distinct_rows": len(g["rows"]),
            "etag_304_reuses": g["etag_304"],
            "duplicate_pages": g["duplicate_pages"],
            "duplicate_rows": g["duplicate_rows"],
            "retries": list(g["retry_events"]),
        }

    def _already(self):
        out = self.published()
        out["already_published"] = True
        out["diagnostics"] = {
            "generation": None, "token": self.src.latest(),
            "complete": True, "requests_this_run": self.requests_this_run,
            "pages_fetched": 0, "distinct_rows": len(out["rows"]),
            "etag_304_reuses": 0, "duplicate_pages": 0,
            "duplicate_rows": 0, "retries": []}
        return out

    def _incomplete(self):
        self._save_gen()
        out = self.published()
        out["incomplete"] = True
        out["diagnostics"] = self._diagnostics(complete=False)
        return out


def inspect(root, version):
    root = Path(root)
    if version == 0:
        # Explicit version 0 is the pre-publication initial state.
        return {"version": 0, "token": None, "rows": [],
                "diff": dict(EMPTY_DIFF)}
    if version is None:
        state = root / "published.json"
        if not state.exists():
            return {"version": 0, "token": None, "rows": [],
                    "diff": dict(EMPTY_DIFF)}
        return json.loads(state.read_text())
    path = root / "history" / ("v%d.json" % version)
    if not path.exists():
        raise ContractError("unknown version %d" % version)
    return json.loads(path.read_text())


def collect(spec, root, max_requests, crash):
    return Collector(spec, root, max_requests, crash).run()


def main():
    parser = argparse.ArgumentParser(
        description="Crash-safe resumable snapshot collector")
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
    except ContractError as err:
        parser.exit(2, "contract error: %s\n" % err)


if __name__=="__main__":
    main()
