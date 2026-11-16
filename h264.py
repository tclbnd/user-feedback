#!/usr/bin/env python3
import json, sys

def scan_nals(st):
    """Split an Annex B byte stream into NAL units.

    Returns (nals, truncated_at):
      nals         -- list of (offset, nal_bytes) with trailing zero bytes
                      stripped; empty NALs (adjacent start codes) dropped.
      truncated_at -- byte offset of the incomplete trailing NAL when the
                      stream ends mid-NAL (a start code with no NAL header
                      following it), else None.
    """
    n = len(st)
    i = 0
    nals = []
    truncated_at = None
    while i < n:
        # 3-byte (00 00 01) or 4-byte (00 00 00 01) start code.
        if i + 2 < n and st[i] == 0 and st[i + 1] == 0 and st[i + 2] == 1:
            sc = 3
        elif i + 3 < n and st[i] == 0 and st[i + 1] == 0 and st[i + 2] == 0 and st[i + 3] == 1:
            sc = 4
        else:
            i += 1
            continue
        start = i + sc
        j = start
        while j < n:
            if (j + 2 < n and st[j] == 0 and st[j + 1] == 0 and
                    (st[j + 2] == 1 or
                     (j + 3 < n and st[j + 2] == 0 and st[j + 3] == 1))):
                break
            j += 1
        nal = bytes(st[start:j])
        # Trailing zero bytes between start code and NAL header are ignored.
        while nal and nal[-1] == 0:
            nal = nal[:-1]
        if nal:
            nals.append((start, nal))
        elif j == n:
            # Start code reached end of stream with no NAL header after it.
            truncated_at = start
        i = j
    return nals, truncated_at

def group_aus(nals):
    """Group NAL units into access units.

    A new AU starts when a VCL NAL (type 1..5) appears AND the current AU
    already contains a VCL NAL; non-VCL NALs (AUD/SPS/PPS/SEI/...) attach
    to the FOLLOWING AU -- so once the current AU holds a VCL NAL, any
    further NAL (VCL or non-VCL) begins the next AU.
    """
    aus = []
    cur = []
    has_vcl = False
    for off, nal in nals:
        typ = nal[0] & 0x1F
        is_vcl = 1 <= typ <= 5
        if has_vcl:
            aus.append(cur)
            cur = []
            has_vcl = False
        cur.append((off, nal))
        if is_vcl:
            has_vcl = True
    if cur:
        aus.append(cur)
    return aus

def main():
    d = json.load(open(sys.argv[1]))
    st = d["stream"]

    nals, truncated_at = scan_nals(st)
    if truncated_at is not None:
        json.dump({"error": "truncated", "offset": truncated_at},
                  open(sys.argv[2], "w"), indent=1)
        return

    aus = group_aus(nals)

    out_aus = []
    configs = []
    active_sps = None
    active_pps = None
    for k, au in enumerate(aus):
        items = []
        sps = None
        pps = None
        for off, nal in au:
            typ = nal[0] & 0x1F
            items.append({"type": typ, "bytes": len(nal),
                          "lenhex": len(nal).to_bytes(4, "big").hex() + nal.hex()})
            if typ == 7:
                sps = nal.hex()
            elif typ == 8:
                pps = nal.hex()
        # A config event fires only when the active (sps, pps) payload pair
        # actually changes; repeated identical parameter sets never re-fire.
        if sps is not None or pps is not None:
            new_sps = sps if sps is not None else active_sps
            new_pps = pps if pps is not None else active_pps
            if (new_sps, new_pps) != (active_sps, active_pps):
                configs.append({"at_au": k, "sps": new_sps, "pps": new_pps})
                active_sps, active_pps = new_sps, new_pps
        out_aus.append({"index": k, "nals": items})

    json.dump({"aus": out_aus, "configs": configs},
              open(sys.argv[2], "w"), indent=1)

main()
