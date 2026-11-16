#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    W = d["win"]; out = []; alerts = []
    frozen = False; used = 0.0
    for i, f in enumerate(d["frames"]):
        past = [g for g in d["frames"] if f["t"] - W <= g["t"] <= f["t"]]
        est = 0.7 * max((g["farend"] for g in past), default=0)
        ser = f["mic"] / max(est, 1e-9)
        if ser >= 0.5:
            frozen = True                      # BUG 1: 无滞回——nearend 掉线立即解冻逻辑缺失
        else:
            frozen = False
        if frozen:
            state = "frozen"                   # BUG 2: 冻结期仍用当帧 est 更新 used
            used = est
        else:
            state = "adapting"; used = est
        out.append({"t": f["t"], "state": state,
                    "used_est": round(used, 2), "ser": round(ser, 3)})
    json.dump({"frames": out, "alerts": alerts}, open(sys.argv[2], "w"), indent=1)

main()
