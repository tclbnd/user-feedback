#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_wm.py — 验收评估:
  1) 10 个攻击样本: 真值位置窗内检出率 100%, 未水印/错种子误报率 <= 1/1000 帧,
     嵌入前后各帧 SNR >= 25dB;
  2) 4 个随机种子复跑嵌入, 逐帧水印比特一致; 裁剪起点未知时同步定位误差 <= 1 帧。
运行: python3 eval_wm.py
"""
import os, math, random, json
import wm

SR = 16000
N = 1024
SEED = 7
NBITS = 40
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

# ---------------- 测试信号 ----------------
def gen_signal_A(sr=SR, dur=11.0):
    """混合内容: 音乐段 / 安静段 / 瞬态突发段 / 音乐段"""
    n = int(sr*dur); x = [0.0]*n
    rng = random.Random(42)
    for i in range(n):
        t = i/sr
        if t < 2.0 or t >= 5.0:      # 音乐: 和弦 + 弱噪声
            v = 0.20*math.sin(2*math.pi*220*t) + 0.15*math.sin(2*math.pi*330*t) \
              + 0.12*math.sin(2*math.pi*440*t) + 0.06*math.sin(2*math.pi*880*t) \
              + 0.05*math.sin(2*math.pi*1320*t*(1+0.002*math.sin(2*math.pi*5*t))) \
              + 0.004*rng.gauss(0, 1)
        elif t < 4.0:                # 安静段 (-34 dB 左右)
            v = 0.015*math.sin(2*math.pi*440*t) + 0.0008*rng.gauss(0, 1)
        else:                        # 打击乐段: 音乐底 + 衰减噪声鼓点 (每 0.25s)
            tt = t-4.0; ph = tt % 0.25
            v = 0.15*math.sin(2*math.pi*220*t) + 0.1*math.sin(2*math.pi*440*t) \
              + 0.35*math.exp(-ph*30.0)*rng.gauss(0, 1) + 0.003*rng.gauss(0, 1)
        x[i] = v
    return x

def gen_signal_B(sr=SR, dur=7.0):
    """低电平安静纹理 (-30 dBFS), 验证安静段可嵌可检"""
    n = int(sr*dur); rng = random.Random(7)
    x = []
    for i in range(n):
        t = i/sr
        x.append(0.02*math.sin(2*math.pi*523*t) + 0.012*math.sin(2*math.pi*1567*t)
                 + 0.006*rng.gauss(0, 1))
    return x

# ---------------- 攻击 ----------------
def atk_clean(x, sr):
    return list(x), 0

def atk_crop_start(x, sr):
    return x[5920:], 5920            # 非帧对齐裁剪 0.37s

def atk_crop_both(x, sr):
    return x[8000:len(x)-6400], 8000

def atk_codec(x, sr):
    """模拟感知编码: Bark 带 scalefactor 量化 (带内 -48dB 量化噪声 + 小系数置零)"""
    F = 2048; hop = 1024
    win = [0.5-0.5*math.cos(2*math.pi*i/F) for i in range(F)]
    model = wm.MaskModel(sr, F)
    b2 = model.bin2band; nb = model.nb
    out = [0.0]*len(x); ws = [0.0]*len(x)
    pos = 0
    while pos+F <= len(x):
        X = wm.fft([x[pos+i]*win[i] for i in range(F)])
        mags = [abs(X[k]) for k in range(F//2+1)]
        peak = [1e-12]*nb
        for k in range(1, F//2+1):
            b = b2[k]
            if mags[k] > peak[b]: peak[b] = mags[k]
        for k in range(F//2+1):
            b = b2[k]
            if mags[k] < peak[b]/1024.0:
                v = 0j
            else:
                st = peak[b]/256.0
                v = complex(round(X[k].real/st)*st, round(X[k].imag/st)*st)
            X[k] = v
            if k: X[F-k] = v.conjugate()
        w = [z.real for z in wm.ifft(X)]
        for i in range(F):
            out[pos+i] += w[i]*win[i]; ws[pos+i] += win[i]*win[i]
        pos += hop
    for i in range(len(x)):
        out[i] = out[i]/ws[i] if ws[i] > 1e-9 else x[i]
    return out, 0

def atk_lowpass(x, sr):
    """5 kHz 低通 (整段 FFT 理想滤波)"""
    n = 1
    while n < len(x): n <<= 1
    X = wm.fft(list(x)+[0.0]*(n-len(x)))
    kc = int(5000.0*n/sr)
    for k in range(kc, n-kc):
        X[k] = 0j
    y = [z.real for z in wm.ifft(X)]
    return y[:len(x)], 0

def atk_resample(x, sr):
    """16k -> 12k -> 16k 线性插值重采样"""
    def rs(x, s1, s2):
        m = int(len(x)*s2/s1); r = s1/s2; o = []
        for i in range(m):
            p = i*r; i0 = int(p); f = p-i0
            o.append(x[i0]*(1-f)+(x[i0+1] if i0+1 < len(x) else x[i0])*f)
        return o
    return rs(rs(x, sr, 12000), 12000, sr), 0

def atk_noise(x, sr):
    """局部包络跟随噪声 (-28 dB, 模拟编码噪声的随信号特性)"""
    rng = random.Random(1234)
    n = len(x); W = 2048
    ps = [0.0]
    for v in x: ps.append(ps[-1]+v*v)
    na = 10**(-28/20)
    out = []
    for i in range(n):
        a = i-W if i > W else 0
        b = i+W if i+W < n else n
        rms = math.sqrt((ps[b]-ps[a])/max(1, b-a))
        out.append(x[i] + na*rms*rng.gauss(0, 1))
    return out, 0

def atk_gain(x, sr):
    return [v*0.35 for v in x], 0

def atk_quant8(x, sr):
    """G.711 mu-law 8bit 压扩 (电话转码)"""
    MU = 255.0
    def enc(v):
        v = max(-1.0, min(1.0, v))
        s = 1.0 if v >= 0 else -1.0
        return s*math.log(1.0+MU*abs(v))/math.log(1.0+MU)
    def dec(q):
        s = 1.0 if q >= 0 else -1.0
        return s*((1.0+MU)**abs(q)-1.0)/MU
    return [dec(round(enc(v)*127)/127.0) for v in x], 0

def atk_mid_replace(x, sr):
    """中段 1.0s 被异质内容替换 -> 对应帧应判为缺失"""
    y = list(x); rng = random.Random(55)
    for i in range(48000, min(64000, len(y))):
        y[i] = 0.3*rng.gauss(0, 1)
    return y, 0

ATTACKS = [("clean", atk_clean), ("crop_start", atk_crop_start),
           ("crop_both", atk_crop_both), ("codec_sim", atk_codec),
           ("lowpass5k", atk_lowpass), ("resample12k", atk_resample),
           ("noise28db", atk_noise), ("gain0.35", atk_gain),
           ("mulaw8bit", atk_quant8), ("mid_replace", atk_mid_replace)]

# ---------------- 工具 ----------------
def frame_snr_min(x, y, n=N):
    worst = 99.0
    for off in range(0, min(len(x), len(y))-n+1, n):
        ex = sum(v*v for v in x[off:off+n])
        ew = sum((y[off+i]-x[off+i])**2 for i in range(n))
        if ex > 1e-12 and ew > 0:
            worst = min(worst, 10*math.log10(ex/ew))
    return worst

def main():
    report = {"attacks": [], "criteria": {}}
    bits = [random.Random(999).getrandbits(1) for _ in range(NBITS)]
    xA = gen_signal_A(); xB = gen_signal_B()
    wm.write_wav(os.path.join(OUT, "A.wav"), xA, SR)
    wm.write_wav(os.path.join(OUT, "B.wav"), xB, SR)

    print("== 嵌入信号 A (seed=%d) ==" % SEED)
    yA, logA = wm.embed(xA, bits, seed=SEED, N=N, sr=SR)
    wm.write_wav(os.path.join(OUT, "A_wm.wav"), yA, SR)
    json.dump(logA, open(os.path.join(OUT, "A_embed_log.json"), "w"))
    snr_min = frame_snr_min(xA, yA)
    ncls = {}
    for e in logA:
        if e.get("cls"): ncls[e["cls"]] = ncls.get(e["cls"], 0)+1
    margins = [e["margin_db"] for e in logA if "margin_db" in e]
    print("逐帧 SNR 最小值: %.2f dB (要求 >=25)" % snr_min)
    print("帧分类统计: %s" % ncls)
    print("掩蔽余量 min/mean: %.1f / %.1f dB" % (min(margins), sum(margins)/len(margins)))
    report["criteria"]["snr_min_db"] = snr_min
    report["criteria"]["snr_ok"] = snr_min >= 25.0

    # ---------- 10 个攻击样本 ----------
    print("\n== 10 个攻击样本检测 ==")
    det_ok = 0; sync_ok = 0; midrep = None
    for name, fn in ATTACKS:
        xa, crop = fn(yA, SR)
        wm.write_wav(os.path.join(OUT, "att_%s.wav" % name), xa, SR)
        r = wm.detect(xa, seed=SEED, N=N, sr=SR, nbits=NBITS)
        truth_start = (wm.SYNC_LEN+len(wm.TRAIL))*N - crop
        serr = abs(r["payload_start_sample"]-truth_start)/N if r["sync_found"] else 99
        # 真值缺失集 (mid_replace: 两个重复帧中心都落在 [48000,64000) 的比特)
        repl = set()
        if name == "mid_replace":
            for j in range(NBITS):
                cen = [(wm.SYNC_LEN+len(wm.TRAIL)+j*wm.REP+r)*N+N
                       for r in range(wm.REP)]
                if all(48000 <= c < 64000 for c in cen): repl.add(j)
        avail = [j for j in range(NBITS) if j not in repl]
        got = r["bits"] if r["sync_found"] else [None]*NBITS
        correct = sum(1 for j in avail if j < len(got) and got[j] == bits[j])
        miss_flag = sum(1 for j in repl if j in r.get("missing", [])) if r["sync_found"] else 0
        rate = correct/len(avail)
        ok = r["sync_found"] and rate == 1.0
        det_ok += ok; sync_ok += (r["sync_found"] and serr <= 1.0)
        if name == "mid_replace":
            midrep = (miss_flag, len(repl))
        print("%-12s sync=%s score=%.2f sync_err=%.2f帧 检出=%d/%d%s" % (
            name, r["sync_found"], r.get("sync_score", 0), serr, correct, len(avail),
            (" 缺失标记=%d/%d" % (miss_flag, len(repl))) if repl else ""))
        report["attacks"].append({"name": name, "sync_found": r["sync_found"],
                                  "sync_err_frames": serr, "det_rate": rate,
                                  "missing_flagged": miss_flag if repl else None})
    report["criteria"]["detection_ok"] = (det_ok == len(ATTACKS))
    report["criteria"]["sync_ok"] = (sync_ok == len(ATTACKS))
    print("检出率 100%% 的攻击样本: %d/10, 同步误差<=1帧: %d/10" % (det_ok, sync_ok))
    if midrep: print("短段缺失判定: %d/%d 帧被正确标记缺失" % midrep)

    # ---------- 误报率 ----------
    print("\n== 误报率 (未水印 + 错种子) ==")
    fp = 0; tot = 0
    for sig, nm in [(xA, "A"), (xB, "B")]:
        for aname, fn in [("clean", atk_clean), ("codec", atk_codec), ("noise", atk_noise)]:
            xa, _ = fn(sig, SR)
            cs = wm.frame_corr_stream(xa, SEED, N, SR)
            fp += sum(1 for c in cs if abs(c) >= wm.TH_CORR); tot += len(cs)
            r = wm.detect(xa, seed=SEED, N=N, sr=SR, nbits=NBITS)
            print("未水印%s/%s: sync_found=%s (score=%.2f)" % (nm, aname, r["sync_found"], r.get("sync_score", 0)))
            fp += 1 if r["sync_found"] else 0
    r = wm.detect(yA, seed=SEED+1000, N=N, sr=SR, nbits=NBITS)   # 错种子
    print("错种子检测水印信号: sync_found=%s (score=%.2f)" % (r["sync_found"], r.get("sync_score", 0)))
    fp += 1 if r["sync_found"] else 0
    cs = wm.frame_corr_stream(yA, SEED+1000, N, SR)
    fp += sum(1 for c in cs if abs(c) >= wm.TH_CORR); tot += len(cs)
    fpr = fp/tot
    print("逐帧误报: %d/%d = %.2e (要求 <=1e-3)" % (fp, tot, fpr))
    report["criteria"]["false_positive_rate"] = fpr
    report["criteria"]["fp_ok"] = fpr <= 1e-3

    # ---------- 可复现性 (4 种子 x 2 次) ----------
    print("\n== 可复现性 (4 种子复跑) ==")
    repro_ok = True
    for s in [11, 22, 33, 44]:
        y1, l1 = wm.embed(xB, bits, seed=s, N=N, sr=SR)
        y2, l2 = wm.embed(xB, bits, seed=s, N=N, sr=SR)
        same_y = (y1 == y2)
        same_b = ([e.get("bit") for e in l1] == [e.get("bit") for e in l2])
        print("seed=%d 样本级一致=%s 逐帧比特一致=%s" % (s, same_y, same_b))
        repro_ok = repro_ok and same_y and same_b
    report["criteria"]["repro_ok"] = repro_ok

    # ---------- 汇总 ----------
    allok = all(report["criteria"][k] for k in
                ["snr_ok", "detection_ok", "sync_ok", "fp_ok", "repro_ok"])
    report["all_pass"] = allok
    json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1)
    print("\n==== 总评: %s ====" % ("全部通过" if allok else "存在未达标项"))
    return 0 if allok else 1

if __name__ == "__main__":
    raise SystemExit(main())
