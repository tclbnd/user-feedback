"""Check HTTP handling and persistence; also run live HTTP when permitted."""

from contextlib import closing
from copy import deepcopy
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

import crawler
from mock_server import ItemsHandler, PAGES


class QuietItemsHandler(ItemsHandler):
    def log_message(self, format, *args):
        pass


class MemorySocket:
    """Supply HTTP bytes to the real request handler without opening a port."""

    def __init__(self, incoming):
        self.incoming = BytesIO(incoming)
        self.outgoing = BytesIO()

    def makefile(self, mode, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.write(data)


def item(item_id, timestamp, name="example", price=12.5):
    return {"id": item_id, "name": name, "price": price, "updated_at": timestamp}


class CrawlerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.db = root / "crawler.db"
        self.watermark = root / "watermark.json"
        self.handler = type("TestHandler", (QuietItemsHandler,), {"pages": deepcopy(PAGES)})
        self.url = "http://127.0.0.1:8000"
        self.transport = patch("crawler.urlopen", side_effect=self.open_url)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def open_url(self, request, timeout=10):
        url = request.full_url if hasattr(request, "full_url") else request
        parsed = urlsplit(url)
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        sock = MemorySocket(f"GET {target} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode("ascii"))
        self.handler(sock, ("127.0.0.1", 12345), object())
        response = HTTPResponse(MemorySocket(sock.outgoing.getvalue()))
        response.begin()
        if response.status >= 400:
            raise HTTPError(url, response.status, response.reason, response.headers, response)
        return response

    def crawl(self):
        return crawler.crawl(self.url, self.db, self.watermark)

    def rows(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return connection.execute("SELECT id, name, price, updated_at FROM items ORDER BY id").fetchall()

    def test_two_runs_are_incremental(self):
        first = self.crawl()
        self.assertEqual((first["fetched"], first["new"], first["skipped"]), (15, 13, 2))
        original_rows = self.rows()
        self.assertEqual(len(original_rows), 13)
        self.assertEqual(first["watermark"], "2026-01-01T00:00:13.000000Z")
        second = self.crawl()
        self.assertEqual((second["fetched"], second["new"], second["skipped"]), (15, 0, 15))
        self.assertEqual(self.rows(), original_rows)
        self.assertEqual(json.loads(self.watermark.read_text())["updated_at"], first["watermark"])

    def test_newer_versions_update_and_old_or_equal_records_are_skipped(self):
        self.handler.pages = {1: [item(1, "2026-01-01T00:00:00Z"), item(2, "2026-01-02T00:00:00Z")]}
        self.assertEqual(self.crawl()["new"], 2)
        self.handler.pages = {
            1: [item(1, "2026-01-03T00:00:00Z", "updated", 99), item(2, "2026-01-02T00:00:00Z")],
            2: [item(1, "2026-01-01T00:00:00Z"), item(3, "2026-01-04T00:00:00Z"),
                item(4, "2026-01-02T00:00:00Z")],
        }
        stats = self.crawl()
        self.assertEqual((stats["fetched"], stats["new"], stats["skipped"]), (5, 2, 3))
        rows = self.rows()
        self.assertEqual([row[0] for row in rows], [1, 2, 3])
        self.assertEqual(rows[0][1:3], ("updated", 99))
        self.assertEqual(stats["watermark"], "2026-01-04T00:00:00.000000Z")
        self.assertEqual(self.crawl()["new"], 0)

    def test_duplicate_id_keeps_latest_regardless_of_page_order(self):
        older = item(1, "2026-01-01T00:00:00Z", "older")
        newer = item(1, "2026-01-02T00:00:00Z", "newer")
        for pages in ({1: [older], 2: [newer]}, {1: [newer], 2: [older]}):
            with self.subTest(pages=pages):
                self.handler.pages = pages
                stats = self.crawl()
                self.assertEqual(stats["fetched"], 2)
                self.assertEqual(self.rows()[0][1], "newer")
                self.db.unlink()
                self.watermark.unlink()

    def test_watermark_compares_instants_and_subsecond_precision(self):
        self.handler.pages = {1: [item(1, "2026-01-01T12:00:00+08:00", "original")]}
        self.assertEqual(self.crawl()["watermark"], "2026-01-01T04:00:00.000000Z")
        self.handler.pages = {1: [item(1, "2026-01-01T04:00:00Z", "same instant")]}
        self.assertEqual(self.crawl()["new"], 0)
        self.assertEqual(self.rows()[0][1], "original")
        self.handler.pages = {1: [item(1, "2026-01-01T04:00:00.000001Z", "newer")]}
        self.assertEqual(self.crawl()["new"], 1)
        self.assertEqual(self.rows()[0][1], "newer")

    def test_empty_dataset_does_not_regress_watermark(self):
        self.handler.pages = {}
        self.assertEqual(self.crawl(), {"fetched": 0, "new": 0, "skipped": 0, "watermark": None})
        self.assertEqual(self.rows(), [])
        self.handler.pages = deepcopy(PAGES)
        first = self.crawl()
        self.handler.pages = {}
        empty = self.crawl()
        self.assertEqual(empty["watermark"], first["watermark"])
        self.assertEqual(len(self.rows()), 13)

    def test_invalid_later_page_does_not_change_persistent_state(self):
        self.crawl()
        original_rows, original_watermark = self.rows(), self.watermark.read_bytes()
        self.handler.pages = {
            1: [item(99, "2026-02-01T00:00:00Z")],
            2: {"error": "not a list"},
        }
        with self.assertRaises(ValueError):
            self.crawl()
        self.assertEqual(self.rows(), original_rows)
        self.assertEqual(self.watermark.read_bytes(), original_watermark)

    def test_watermark_write_failure_can_be_retried_without_duplicates(self):
        with patch("crawler.os.replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                self.crawl()
        self.assertEqual(len(self.rows()), 13)
        self.assertFalse(self.watermark.exists())
        self.assertEqual(list(self.watermark.parent.glob(".watermark.json.*")), [])
        stats = self.crawl()
        self.assertEqual((stats["fetched"], stats["new"], stats["skipped"]), (15, 0, 15))
        self.assertEqual(json.loads(self.watermark.read_text())["updated_at"], stats["watermark"])

    def test_server_contract(self):
        for page in (1, 2, 3):
            with self.open_url(f"{self.url}/items?page={page}") as response:
                payload = json.load(response)
            self.assertGreaterEqual(len(payload), 5)
            for record in payload:
                self.assertEqual(set(record), {"id", "name", "price", "updated_at"})
        for page in (-1, 0, 4, 999):
            with self.open_url(f"{self.url}/items?page={page}") as response:
                self.assertEqual(json.load(response), [])
        for path, status in (("/items?page=bad", 400), ("/unknown", 404)):
            with self.assertRaises(HTTPError) as raised:
                self.open_url(self.url + path)
            self.assertEqual(raised.exception.code, status)
            raised.exception.close()


class LiveAcceptanceTests(unittest.TestCase):
    def test_real_http_and_two_cli_runs(self):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuietItemsHandler)
        except PermissionError as error:
            self.skipTest(f"environment forbids listening on a local port: {error}")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = [sys.executable, str(crawler.ROOT / "crawler.py"),
                           "--base-url", f"http://127.0.0.1:{server.server_port}",
                           "--db", str(root / "crawler.db"),
                           "--watermark", str(root / "watermark.json")]
                first = subprocess.run(command, capture_output=True, text=True, timeout=15)
                second = subprocess.run(command, capture_output=True, text=True, timeout=15)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("fetched=15 new=13 skipped=2", first.stdout)
                self.assertIn("fetched=15 new=0 skipped=15", second.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
