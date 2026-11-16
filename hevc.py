#!/usr/bin/env python3
"""HEVC leading-picture filter.

Fixes random-access entry handling so that leading pictures that cannot
be decoded after a seek are no longer emitted:

  * offline dependency index: leading pictures (RADL/RASL) stored
    immediately before an IDR in decode order are located up front and
    never emitted (reason "pre-idr");
  * entry-point selection: IDR entries flush everything stored before
    them and emit their whole slice; CRA entries drop RASL AUs and keep
    a RADL AU only when its poc is greater than the CRA's poc and
    smaller than the poc of the first following TRAIL;
  * display list: per entry, the emitted pocs in decode order plus the
    skipped AUs with their removal reason.
"""
import json, sys

LEADING = ("RADL", "RASL")


def find_pre_idr(aus):
    """Indices of leading pictures stored immediately before an IDR.

    They belong to the IDR's segment but precede it in decode order, so
    no entry whose slice contains them can decode them correctly; they
    are discarded unconditionally.
    """
    marked = set()
    for j, au in enumerate(aus):
        if au["type"] == "IDR":
            k = j - 1
            while k >= 0 and aus[k]["type"] in LEADING:
                marked.add(k)
                k -= 1
    return marked


def first_following_trail_poc(aus, j):
    """Poc of the first TRAIL AU after index j, or None."""
    for au in aus[j + 1:]:
        if au["type"] == "TRAIL":
            return au["poc"]
    return None


def main():
    d = json.load(open(sys.argv[1]))
    aus = d["access_units"]
    entries = d["entries"]
    pre_idr = find_pre_idr(aus)
    out = []
    for k, e in enumerate(entries):
        i = e["index"]
        if i < 0 or i >= len(aus) or aus[i]["type"] not in ("IDR", "CRA"):
            out.append({"entry": i, "error": "not-irap"})
            continue
        end = entries[k + 1]["index"] if k + 1 < len(entries) else len(aus)
        end = min(end, len(aus))
        entry_type = aus[i]["type"]
        cra_poc = aus[i]["poc"]
        emitted, skipped = [], []
        for j in range(i, end):
            au = aus[j]
            t = au["type"]
            # Leading pictures stored before an IDR: never decodable.
            if j in pre_idr:
                skipped.append({"poc": au["poc"], "reason": "pre-idr"})
                continue
            # IDR entry: emit the entry AU and everything after it.
            if entry_type == "IDR":
                emitted.append(au["poc"])
                continue
            # CRA entry.
            if j == i or t in ("IDR", "CRA", "TRAIL"):
                emitted.append(au["poc"])
                continue
            if t == "RASL":
                skipped.append({"poc": au["poc"], "reason": "rasl"})
                continue
            if t == "RADL":
                trail_poc = first_following_trail_poc(aus, j)
                if au["poc"] > cra_poc and trail_poc is not None \
                        and au["poc"] < trail_poc:
                    emitted.append(au["poc"])
                else:
                    skipped.append({"poc": au["poc"],
                                    "reason": "radl-poc-out-of-range"})
                continue
        out.append({"entry": i, "poc": aus[i]["poc"],
                    "output": emitted, "skipped": skipped})
    json.dump({"entries": out}, open(sys.argv[2], "w"))


main()
