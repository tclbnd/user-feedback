#!/usr/bin/env python3
"""Serve a deterministic, paginated JSON dataset using the standard library."""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def make_item(item_id):
    return {
        "id": item_id,
        "name": f"Item {item_id}",
        "price": round(10 + item_id * 1.25, 2),
        "updated_at": f"2026-01-01T00:00:{item_id:02d}Z",
    }


# Adjacent pages overlap, so a complete fetch includes 15 rows and 13 IDs.
PAGES = {
    1: [make_item(item_id) for item_id in range(1, 6)],
    2: [make_item(item_id) for item_id in range(5, 10)],
    3: [make_item(item_id) for item_id in range(9, 14)],
}


class ItemsHandler(BaseHTTPRequestHandler):
    pages = PAGES

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/items":
            self.send_json(404, {"error": "not found"})
            return
        try:
            page = int(parse_qs(parsed.query, keep_blank_values=True).get("page", ["1"])[0])
        except ValueError:
            self.send_json(400, {"error": "page must be an integer"})
            return
        self.send_json(200, self.pages.get(page, []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        with ThreadingHTTPServer((args.host, args.port), ItemsHandler) as server:
            print(f"Serving http://{args.host}:{server.server_port}/items?page=1", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
