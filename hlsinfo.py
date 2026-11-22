"""Summarise HLS playlists (.m3u8) as JSON.

    python3 hlsinfo.py [--base URL] playlist.m3u8 [more files ...]

Segment and variant URIs are resolved against --base (the URL the playlist was
fetched from); without it they are printed as written.
"""
import json
import sys

from hlskit.hls import HlsError, parse


def main(args):
    base = ""
    if args[:1] == ["--base"]:
        base, args = args[1], args[2:]
    out = []
    for path in args:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                out.append({"file": path, **parse(fh.read(), base)})
        except (OSError, HlsError, ValueError, KeyError, ArithmeticError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
