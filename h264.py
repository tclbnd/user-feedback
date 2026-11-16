#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    st = d["stream"]; n = len(st); i = 0; nals = []
    while i + 2 < n:
        if st[i] == 0 and st[i + 1] == 0 and st[i + 2] == 1:
            i += 3
        elif i + 3 < n and st[i] == 0 and st[i + 1] == 0 and st[i + 2] == 0 and st[i + 3] == 1:
            i += 4
        else:
            i += 1; continue
        start = i; j = i
        while j + 2 < n and not (st[j] == 0 and st[j + 1] == 0 and
                                 (st[j + 2] == 1 or
                                  (j + 3 < n and st[j + 2] == 0 and st[j + 3] == 1))):
            j += 1
        nal = st[start:j]
        while nal and nal[-1] == 0:
            nal = nal[:-1]
        nals.append((start, nal))
        i = j
    aus = []; cur = []
    for off, nal in nals:
        if not nal:
            continue
        typ = nal[0] & 0x1F
        # BUG: 所有 NAL 一律并入当前 AU（VCL 边界不切分、非VCL不前置归属）
        cur.append((off, nal))
    if cur:
        aus.append(cur)
    out_aus = []; configs = []
    for k, au in enumerate(aus):
        items = []
        for off, nal in au:
            typ = nal[0] & 0x1F
            items.append({"type": typ, "bytes": len(nal),
                          "lenhex": len(nal).to_bytes(4, "big").hex() + nal.hex()})
        # BUG: 每组 SPS/PPS 都发配置事件（未按内容去重）
        for off, nal in au:
            typ = nal[0] & 0x1F
            if typ == 7:
                configs.append({"at_au": k, "sps": bytes(nal).hex()})
            elif typ == 8:
                configs.append({"at_au": k, "pps": bytes(nal).hex()})
        out_aus.append({"index": k, "nals": items})
    json.dump({"aus": out_aus, "configs": configs},
              open(sys.argv[2], "w"), indent=1)

main()
