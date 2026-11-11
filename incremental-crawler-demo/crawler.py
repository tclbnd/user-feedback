#!/usr/bin/env python3
"""Fetch paginated items and persist only changes newer than a saved watermark."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def normalize_timestamp(value):
    """Use fixed-width UTC timestamps for chronological string comparisons."""
    if not isinstance(value, str):
        raise ValueError("updated_at must be an ISO 8601 timestamp")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("updated_at must include a timezone")
    return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_item(item):
    if not isinstance(item, dict):
        raise ValueError("each item must be a JSON object")
    if not {"id", "name", "price", "updated_at"} <= item.keys():
        raise ValueError("an item is missing required fields")
    if type(item["id"]) is not int:
        raise ValueError("id must be an integer")
    if not isinstance(item["name"], str):
        raise ValueError("name must be a string")
    if type(item["price"]) not in (int, float) or not math.isfinite(item["price"]):
        raise ValueError("price must be a finite number")
    return {
        "id": item["id"],
        "name": item["name"],
        "price": item["price"],
        "updated_at": normalize_timestamp(item["updated_at"]),
    }


def fetch_items(base_url, timeout):
    fetched = 0
    unique = {}
    page = 1
    while True:
        request = Request(f"{base_url.rstrip('/')}/items?page={page}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            items = json.load(response)
        if not isinstance(items, list):
            raise ValueError(f"page {page} must contain a JSON list")
        if not items:
            break
        fetched += len(items)
        for raw_item in items:
            item = normalize_item(raw_item)
            previous = unique.get(item["id"])
            if previous is None or item["updated_at"] > previous["updated_at"]:
                unique[item["id"]] = item
        page += 1
    return fetched, unique


def load_watermark(path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict) or "updated_at" not in state:
        raise ValueError("watermark.json must contain an updated_at field")
    value = state["updated_at"]
    return normalize_timestamp(value) if value is not None else None


def save_watermark(path, watermark):
    # Replace atomically so interruption cannot leave a partially written JSON file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"updated_at": watermark}, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def crawl(base_url, db_path, watermark_path, timeout=10):
    db_path, watermark_path = Path(db_path), Path(watermark_path)
    watermark = load_watermark(watermark_path)
    # Finish and validate the entire fetch before changing any persistent state.
    fetched, unique = fetch_items(base_url, timeout)
    new = 0
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            for item in unique.values():
                if watermark is not None and item["updated_at"] <= watermark:
                    continue
                cursor = connection.execute("""
                    INSERT INTO items (id, name, price, updated_at)
                    VALUES (:id, :name, :price, :updated_at)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        price = excluded.price,
                        updated_at = excluded.updated_at
                    WHERE excluded.updated_at > items.updated_at
                """, item)
                new += cursor.rowcount
    # Commit SQLite first: retrying with an older watermark safely replays rows.
    timestamps = [item["updated_at"] for item in unique.values()]
    if watermark is not None:
        timestamps.append(watermark)
    next_watermark = max(timestamps, default=None)
    save_watermark(watermark_path, next_watermark)
    return {"fetched": fetched, "new": new, "skipped": fetched - new,
            "watermark": next_watermark}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", type=Path, default=ROOT / "crawler.db")
    parser.add_argument("--watermark", type=Path, default=ROOT / "watermark.json")
    parser.add_argument("--timeout", type=float, default=10, help="HTTP timeout in seconds")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    try:
        stats = crawl(args.base_url, args.db, args.watermark, args.timeout)
    except (OSError, ValueError, sqlite3.Error, URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(" ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
