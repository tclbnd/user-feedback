#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wm.py — 掩蔽驱动的音频水印嵌入与盲检测 (纯标准库实现)

用法 (与 README 接口兼容):
  嵌入: python3 wm.py req.json out.json
        req.json = {"pcm":"in.wav","bits":[1,0,...],"seed":7,"frame":1024}
  检测: python3 wm.py req.json out.json
        req.json = {"pcm":"wm.wav","detect":true,"seed":7,"frame":1024,"nbits":48}

设计:
  * 帧长 2048 / -hop 1024, sqrt-Hann 分析/综合窗 + 50% OLA (完美重构,
    加窗谱对裁剪造成的帧未对齐鲁棒);
  * Bark 域扩展掩蔽模型 + 绝对听阈(ATH), 得到逐频点可掩蔽幅度;
    水印能量按可掩蔽幅度(平坦化)分配到 1.0-4.5 kHz 嵌入带,
    安静帧 x0.5 / 瞬态帧 x0.45。
  * 每帧(hop)1 bit: 种子化 PN 序列, bit 决定正负号, 以幅度域加减方式嵌入。
  * 每 1024 样本块硬限幅: 水印能量 <= 信号能量 -26dB, 保证 SNR >= 25dB。
  * 同步: 24 帧已知同步头(位置无关 PN) + 4 帧 trailer 抗 ±1 帧错位;
    检测端 8 相位扫描, 容忍任意样本级裁剪起点, 同步误差 <= 1 帧。
  * 逐帧归一化相关检测(谱白化+离群截断), |corr|<TH_CORR 判为缺失。
  * 全部随机量由 seed 派生, 同 seed 嵌入结果逐样本可复现。
"""
import json, sys, os, struct, wave, random, math, cmath

# ---------------- 参数 ----------------
SYNC_LEN       = 24                 # 同步头帧数
TRAIL          = [1, 0, 1, 1]       # 同步尾校验序列(抗 ±1 帧错位)
BAND_HZ        = (1000.0, 4500.0)   # 嵌入频带
SNR_CLAMP_DB   = 26.0               # 每帧水印能量 <= 帧能量 -26dB (>=25dB 留 1dB 余量)
TH_CORR        = 0.20               # 逐帧相关判决阈值
SYNC_ACCEPT    = 0.70               # 同步接受门限(加权一致度)
SYNC_MINABS    = 0.12               # 同步窗平均 |corr| 下限(抗内容偶合假同步)
MASK_OFFSET_DB = 10.0               # 掩蔽阈值保守偏移(噪声掩蔽者)
ALPHA          = {"normal": 1.0, "quiet": 0.5, "transient": 0.7}
PHASES         = 8                  # 检测端相位扫描数
WHITE_W        = 12                 # 谱白化局部均值半窗(bin)
CHIP_W         = 2                  # 每个 chip 占据的频点数(抗加窗谱平滑)
REP            = 3                  # 每比特重复帧数(检测端软合并)
TMP_F1, TMP_F2, TMP_B1 = 10**-0.8, 10**-1.6, 10**-2.0   # 时域掩蔽泄漏

# ---------------- FFT (迭代 radix-2) ----------------
_plan_cache = {}
def _plan(n):
    p = _plan_cache.get(n)
    if p: return p
    rev = [0]*n; j = 0
    for i in range(1, n):
        b = n >> 1
        while j & b: j ^= b; b >>= 1
        j |= b; rev[i] = j
    tw = [cmath.exp(-2j*math.pi*k/n) for k in range(n//2)]
    _plan_cache[n] = (rev, tw)
    return rev, tw

def fft(a):
    n = len(a); rev, tw = _plan(n)
    a = [complex(a[rev[i]]) for i in range(n)]
    L = 2
    while L <= n:
        h = L >> 1; step = n // L
        for i in range(0, n, L):
            k = 0
            for j in range(i, i+h):
                w = tw[k]; u = a[j]; v = a[j+h]*w
                a[j] = u+v; a[j+h] = u-v
                k += step
        L <<= 1
    return a

def ifft(a):
    n = len(a)
    return [z.conjugate()/n for z in fft([z.conjugate() for z in a])]

# ---------------- WAV I/O ----------------
def read_wav(p):
    w = wave.open(p, 'rb')
    sr = w.getframerate(); n = w.getnframes()
    ch = w.getnchannels(); sw = w.getsampwidth()
    raw = w.readframes(n); w.close()
    if sw != 2: raise ValueError("need 16-bit pcm")
    d = struct.unpack("<%dh" % (len(raw)//2), raw)
    if ch > 1: d = d[::ch]
    return [v/32768.0 for v in d], sr

def write_wav(p, x, sr):
    w = wave.open(p, 'wb')
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(struct.pack("<%dh" % len(x),
        *[max(-32768, min(32767, int(round(v*32767)))) for v in x]))
    w.close()

# ---------------- 心理声学模型 ----------------
def _bark(f):
    return 13.0*math.atan(0.00076*f) + 3.5*math.atan((f/7500.0)**2)

def _invbark(z):
    lo, hi = 0.0, 20000.0
    for _ in range(60):
        mid = (lo+hi)/2
        if _bark(mid) < z: lo = mid
        else: hi = mid
    return (lo+hi)/2

def _ath_db(f):
    """绝对听阈 (dB SPL), Terhardt 近似"""
    fk = max(f, 1.0)/1000.0
    return 3.64*fk**-0.8 - 6.5*math.exp(-0.6*(fk-3.3)**2) + 0.001*fk**4

def _sf(dz):
    """Johnston 扩展函数 (dB), dz = 被掩蔽处 bark - 掩蔽者 bark"""
    return 15.81 + 7.5*(dz+0.474) - 17.5*math.sqrt(1.0+(dz+0.474)**2)

class MaskModel:
    """Bark 子带掩蔽模型: 输入逐 bin 等效正弦幅度谱, 输出逐 bin 可掩蔽幅度"""
    def __init__(self, sr, N, nb=32):
        self.sr = sr; self.N = N; self.nb = nb
        f_lo, f_hi = 300.0, min(7200.0, sr*0.475)
        z0, z1 = _bark(f_lo), _bark(f_hi)
        self.zc = [z0+(z1-z0)*(i+0.5)/nb for i in range(nb)]
        self.fc = [_invbark(z) for z in self.zc]
        self.bin2band = [0]*(N//2+1)
        for k in range(1, N//2+1):
            b = int((_bark(k*sr/N)-z0)/(z1-z0)*nb)
            self.bin2band[k] = min(nb-1, max(0, b))
        self.cnt = [0]*nb
        for k in range(1, N//2+1):
            self.cnt[self.bin2band[k]] += 1
        # 0 dBFS 满幅正弦 ≈ 96 dB SPL
        self.ath_pow = [(10.0**((_ath_db(self.fc[b])-96.0)/20.0))**2 for b in range(nb)]
        off = 10.0**(-MASK_OFFSET_DB/10.0)
        self.S = [[10.0**(_sf(self.zc[b]-self.zc[j])/10.0)*off
                   for j in range(nb)] for b in range(nb)]

    def maskable_amp(self, amp):
        nb = self.nb; b2 = self.bin2band
        bp = [0.0]*nb
        for k in range(1, len(amp)):
            a = amp[k]; bp[b2[k]] += a*a
        th = [0.0]*nb
        for b in range(nb):
            m = 0.0; row = self.S[b]
            for j in range(nb):
                pj = bp[j]
                if pj: m += pj*row[j]
            th[b] = m if m > self.ath_pow[b] else self.ath_pow[b]
        out = [0.0]*len(amp)
        for k in range(1, len(amp)):
            b = b2[k]
            out[k] = math.sqrt(th[b]/self.cnt[b])
        return out

# ---------------- PN / 同步 ----------------
def _pn(seed, tag, L):
    rng = random.Random((int(seed) << 20) ^ int(tag))
    return [1.0 if rng.random() < 0.5 else -1.0 for _ in range(L)]

def _chips(seed, tag, L):
    """生成长度 L 的扩频码片序列, 每 chip 占 CHIP_W 个频点"""
    nc = (L+CHIP_W-1)//CHIP_W
    base = _pn(seed, tag, nc)
    return [base[i//CHIP_W] for i in range(L)]

def _sync_bits(seed):
    rng = random.Random((int(seed) << 20) ^ 2)
    return [rng.randint(0, 1) for _ in range(SYNC_LEN)]

# ---------------- 帧分类 ----------------
def _classify(energies):
    se = sorted(energies); med = se[len(se)//2] if se else 0.0
    cls = []
    for i, e in enumerate(energies):
        r = math.sqrt(e)
        if e < 1e-12 or r < 0.25*math.sqrt(med) or r < 10**(-40/20):
            cls.append("quiet")
        elif i > 0 and e > 6.0*energies[i-1]:
            cls.append("transient")
        else:
            cls.append("normal")
    return cls

# ---------------- 逐帧相关 ----------------
def _frame_corr(fr, chips, klo, khi, win, model=None, swin=1.0):
    """加窗帧的白化(局部均值去除+离群截断)幅度谱与 PN 的归一化相关。
    给定 model 时按接收帧的可掩蔽幅度做匹配模板加权。"""
    M = len(fr)
    X = fft([fr[t]*win[t] for t in range(M)])
    L = khi-klo
    mag = [abs(X[k]) for k in range(klo, khi)]
    ps = [0.0]
    for v in mag: ps.append(ps[-1]+v)
    W = WHITE_W
    w = [0.0]*L
    for i in range(L):
        a = i-W if i > W else 0
        b = i+W+1 if i+W+1 <= L else L
        w[i] = mag[i] - (ps[b]-ps[a])/(b-a)
    # 离群截断: 抑制强谐波残留对相关量的支配
    aw = sorted(abs(v) for v in w)
    cap = 5.0*aw[L//2] + 1e-12
    tw = None
    if model is not None:
        amp = [abs(X[k])*2.0/swin for k in range(M//2+1)]
        mk = model.maskable_amp(amp)
        ab = [mk[klo+i] for i in range(L)]
        amed = sorted(ab)[L//2] + 1e-18
        # 有界匹配加权 [0.6,1.2]: 既利用掩蔽形状又避免模板塌缩致误报
        tw = [0.6+0.4*min(a, 1.5*amed)/amed for a in ab]
    s = 0.0; ss = 0.0; st = 0.0
    for i in range(L):
        v = w[i]
        if v > cap: v = cap
        elif v < -cap: v = -cap
        t = chips[i]*(tw[i] if tw is not None else 1.0)
        s += v*t; ss += v*v; st += t*t
    return s/(math.sqrt(ss*st)+1e-12)

# ---------------- 嵌入 ----------------
def embed(x, bits, seed=7, N=1024, sr=16000):
    """N = hop(帧移), 分析帧长 2N, sqrt-Hann + 50% OLA"""
    M = 2*N
    win = [math.sin(math.pi*(t+0.5)/M) for t in range(M)]
    S = sum(win)
    klo = max(1, int(BAND_HZ[0]*M/sr)); khi = min(M//2, int(BAND_HZ[1]*M/sr))
    L = khi-klo
    model = MaskModel(sr, M)
    sbits = _sync_bits(seed)
    schips = _chips(seed, 1, L)
    stream = list(TRAIL) + [b for b in bits for _ in range(REP)]
    total = SYNC_LEN + len(stream)
    nfr = min(total, max(0, (len(x)-M)//N + 1))
    segs = [x[i*N:i*N+M] for i in range(nfr)]
    energies = [sum(v*v for v in s) for s in segs]
    cls = _classify(energies)
    clamp = 10.0**(-SNR_CLAMP_DB/10.0)
    # 第一遍: 逐帧掩蔽阈值(可掩蔽幅度谱)
    mks = []
    for i in range(nfr):
        if energies[i] < 1e-12:
            mks.append(None); continue
        X = fft([segs[i][t]*win[t] for t in range(M)])
        amp = [abs(X[k])*2.0/S for k in range(M//2+1)]
        mks.append(model.maskable_amp(amp))
    # 时域掩蔽: 前向(强)/后向(弱)泄漏, 提升瞬态邻域帧的可嵌能量
    mke = [list(m) if m is not None else None for m in mks]
    for i in range(nfr):
        if mks[i] is None: continue
        mi = mke[i]
        for d, kappa in ((-2, TMP_F2), (-1, TMP_F1), (1, TMP_B1)):
            j = i+d
            if 0 <= j < nfr and mks[j] is not None:
                mj = mks[j]
                for k in range(klo, khi):
                    mi[k] = math.sqrt(mi[k]*mi[k] + kappa*mj[k]*mj[k])
    # 第二遍: 逐帧嵌入
    delta = [0.0]*len(x)
    log = []
    for i in range(nfr):
        seg = segs[i]
        Ex = energies[i]
        if i < SYNC_LEN:
            bit = sbits[i]; chips = schips; kind = "sync"
        else:
            j = i-SYNC_LEN
            bit = stream[j]; chips = _chips(seed, 1000+j, L)
            kind = "trail" if j < len(TRAIL) else "payload"
        if Ex < 1e-12:
            log.append({"frame": i, "kind": kind, "bit": bit, "cls": cls[i],
                        "skipped": True, "energy": 0.0})
            continue
        alpha = ALPHA[cls[i]]
        sgn = 1.0 if bit else -1.0
        X = fft([seg[t]*win[t] for t in range(M)])
        mk = mke[i]
        # 可掩蔽能量(时域/帧)与平坦化分配权重: 避免能量过度集中于谐波峰
        ab = [alpha*mk[klo+bi] for bi in range(L)]
        allowed = sum(a*a for a in ab)*(S*S/4.0)/M
        amed = sorted(ab)[L//2]
        acap = 3.0*amed
        wgt = [a if a < acap else acap for a in ab]
        E_target = allowed
        et = Ex*clamp                   # 块能量由相邻两帧各贡献一半, 无需额外预留
        if et < E_target: E_target = et
        sw = sum(v*v for v in wgt)
        g = math.sqrt(E_target*M/sw) if sw > 0 else 0.0
        dX = [0j]*M
        for bi in range(L):
            k = klo+bi
            d = g*wgt[bi]
            capk = ab[bi]*(S/2.0)       # 逐 bin 不超过可掩蔽幅度
            if d > capk: d = capk
            d *= sgn*chips[bi]
            m = abs(X[k])
            if m < 1e-12:
                if d > 0: dX[k] = complex(d, 0.0); dX[M-k] = complex(d, 0.0)
            else:
                nm = m+d
                f = ((nm if nm > 0 else 0.0)-m)/m
                dX[k] = X[k]*f; dX[M-k] = X[M-k]*f
        dw = [z.real*win[t] for t, z in enumerate(ifft(dX))]
        Ew = sum(v*v for v in dw)
        off = i*N
        for t in range(M):
            if off+t < len(delta): delta[off+t] += dw[t]
        log.append({"frame": i, "kind": kind, "bit": bit, "cls": cls[i],
                    "alpha": alpha, "energy": Ew,
                    "snr_db": 10*math.log10(Ex/max(Ew, 1e-18)),
                    "margin_db": 10*math.log10(allowed/max(Ew, 1e-18)),
                    "band_hz": list(BAND_HZ)})
    # 安全限幅: 每 1024 样本块水印能量 <= 信号能量 -26dB
    y = list(x)
    nb = len(x)//N
    for b in range(nb):
        o = b*N
        Ed = sum(v*v for v in delta[o:o+N])
        Exb = sum(v*v for v in x[o:o+N])
        if Ed > Exb*clamp and Ed > 0:
            sc = math.sqrt(Exb*clamp/Ed)
            for t in range(N): delta[o+t] *= sc
    for i in range(len(x)):
        y[i] = x[i]+delta[i]
    return y, log

# ---------------- 检测 ----------------
def detect(x, seed=7, N=1024, sr=16000, nbits=None):
    M = 2*N
    win = [math.sin(math.pi*(t+0.5)/M) for t in range(M)]
    swin = sum(win)
    model = MaskModel(sr, M)
    klo = max(1, int(BAND_HZ[0]*M/sr)); khi = min(M//2, int(BAND_HZ[1]*M/sr))
    L = khi-klo
    sbits = _sync_bits(seed)
    schips = _chips(seed, 1, L)
    step = N//PHASES
    best = None
    for p in range(0, N, step):
        corrs = []
        f = 0
        while p+f*N+M <= len(x):
            c = _frame_corr(x[p+f*N:p+f*N+M], schips, klo, khi, win, model, swin)
            corrs.append(c)
            m = min(SYNC_LEN, f+1)
            if m >= 12:     # 后缀对齐: 允许同步头前部被裁掉
                num = 0.0; den = 0.0; base = SYNC_LEN-m
                for t in range(m):
                    c = corrs[f-m+1+t]
                    num += c*(1.0 if sbits[base+t] else -1.0)
                    den += abs(c)
                sc = num/(den+1e-12)
                ma = den/m              # 窗口平均 |corr|: 真实水印 ~0.5, 偶发内容 ~0.1
                sc2 = sc if ma >= SYNC_MINABS else 0.0
                if best is None or sc2 > best[0]:
                    best = (sc2, p, f, sc, ma)
            f += 1
        if best and best[0] >= 0.92:
            break
    if best is None or best[0] < SYNC_ACCEPT:
        return {"sync_found": False,
                "sync_score": best[3] if best else 0.0}
    score, p, e = best[0], best[1], best[2]
    # trailer 消歧: 抗 ±1 帧错位
    def tscore(ee):
        s = 0.0
        for j in range(len(TRAIL)):
            fi = ee+1+j
            if fi >= 0 and p+fi*N+M <= len(x):
                c = _frame_corr(x[p+fi*N:p+fi*N+M], _chips(seed, 1000+j, L),
                                klo, khi, win, model, swin)
                s += c*(1.0 if TRAIL[j] else -1.0)
        return s
    e = max(((tscore(ee), ee) for ee in (e-1, e, e+1) if ee >= 0))[1]
    nfr_av = max(0, (len(x)-p-M)//N + 1)
    if nbits is None:
        nbits = max(0, (nfr_av-(e+1+len(TRAIL)))//REP)
    bits = []; conf = []; missing = []
    for j in range(nbits):
        vals = []
        for r in range(REP):
            fi = e+1+len(TRAIL)+j*REP+r
            if fi < nfr_av:
                vals.append(_frame_corr(x[p+fi*N:p+fi*N+M],
                                        _chips(seed, 1000+len(TRAIL)+j*REP+r, L),
                                        klo, khi, win, model, swin))
        good = [c for c in vals if abs(c) >= TH_CORR]
        if not good:
            bits.append(None)
            conf.append(max((abs(c) for c in vals), default=0.0))
            missing.append(j)
        else:
            c = sum(good)/len(good)
            bits.append(1 if c > 0 else 0)
            conf.append(abs(c))
    return {"sync_found": True, "sync_score": score, "phase": p,
            "sync_end_frame": e,
            "payload_start_sample": p+(e+1+len(TRAIL))*N,
            "bits": bits, "conf": conf, "missing": missing}

def frame_corr_stream(x, seed, N, sr, tag=1000):
    """对全部相位/帧计算与指定 PN 的相关值 (用于误报率统计)"""
    M = 2*N
    win = [math.sin(math.pi*(t+0.5)/M) for t in range(M)]
    klo = max(1, int(BAND_HZ[0]*M/sr)); khi = min(M//2, int(BAND_HZ[1]*M/sr))
    chips = _chips(seed, tag, khi-klo)
    swin = sum(win)
    model = MaskModel(sr, M)
    step = N//PHASES
    out = []
    for p in range(0, N, step):
        f = 0
        while p+f*N+M <= len(x):
            out.append(_frame_corr(x[p+f*N:p+f*N+M], chips, klo, khi, win, model, swin))
            f += 1
    return out

# ---------------- CLI ----------------
def main():
    req = json.load(open(sys.argv[1]))
    d = os.path.dirname(os.path.abspath(sys.argv[1]))
    od = os.path.dirname(os.path.abspath(sys.argv[2]))
    x, sr = read_wav(os.path.join(d, req["pcm"]))
    N = int(req.get("frame", 1024)); seed = req.get("seed", 7)
    if req.get("detect"):
        res = detect(x, seed=seed, N=N, sr=sr, nbits=req.get("nbits"))
        json.dump({"detect": res}, open(sys.argv[2], "w"))
        return
    bits = req["bits"]
    y, log = embed(x, bits, seed=seed, N=N, sr=sr)
    outp = os.path.join(od, "wm.wav")
    write_wav(outp, y, sr)
    det = detect(y, seed=seed, N=N, sr=sr, nbits=len(bits))
    json.dump({"out": "wm.wav", "seed": seed, "frame": N,
               "embed": log, "detect": det}, open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
