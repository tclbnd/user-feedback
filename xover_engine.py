#!/usr/bin/env python3
"""Stateful multi-way crossover engine.

Linkwitz-style complementary first-order IIR edges with:

  * sample-accurate crossover migration events (coefficient or new fc),
    every filter state p and delay-line history is preserved so each
    branch stays continuous across the switch (no clicks / discontinuity);
  * per-driver delay compensation as a true RIGHT shift
    y[n] = x[n-d] with zero-filled head (ring buffer, block independent);
  * per-driver polarity (+1 / -1);
  * multi-channel processing with independent per-channel states;
  * full snapshot/restore of every state needed to rebuild monitoring;
  * deterministic arithmetic: identical absolute sample positions always
    produce identical samples regardless of block length.

Filter model (matches the project README):
    w = 2*pi*fc/sr,  a = w / (1 + w)
    LP: p = a*x + (1-a)*p ; y = p
    HP: p = a*x + (1-a)*p ; y = x - p     => LP(x) + HP(x) = x
"""

import math


def lp_alpha(fc, sr):
    """One-pole coefficient from cutoff frequency."""
    w = 2.0 * math.pi * fc / sr
    return w / (1.0 + w)


def delay_samples(ms, sr):
    """Integer sample delay, same rounding rule as the original tool."""
    return int(round(ms * sr / 1000.0))


class Edge:
    """One crossover point: a complementary LP/HP pair, one shared state.

    Per sample the engine calls update(u) exactly once:
        p   <- a*u + (1-a)*p
        LP  = p,  HP = u - p     => LP(u) + HP(u) = u
    The lower branch takes LP, the upper branch continues with HP, so
    band outputs telescope back to the input sample by sample.
    A coefficient migration keeps p, so both branches stay continuous.
    """

    def __init__(self, a, fc=None, p=0.0):
        self.state = float(p)
        self.a = float(a)
        self.fc = None if fc is None else float(fc)
        self._lp = 0.0
        self._hp = 0.0

    def update(self, u):
        self.state = self.a * u + (1.0 - self.a) * self.state
        self._lp = self.state
        self._hp = u - self.state

    @property
    def lp_out(self):
        return self._lp

    @property
    def hp_out(self):
        return self._hp

    def set_alpha(self, a):
        self.a = float(a)

    def snapshot(self):
        return {"a": self.a, "fc": self.fc, "p": self.state}

    @classmethod
    def restore(cls, snap):
        return cls(snap["a"], snap["fc"], snap["p"])


class DelayLine:
    """Right-shift delay line: y[n] = x[n-d], head zero-filled.

    A ring buffer keeps full history so delay length can migrate at
    runtime without losing previously buffered samples.
    """

    def __init__(self, cap):
        # one extra slot: reading d samples behind the write head needs
        # d+1 live slots without overwriting the value being read
        self.cap = max(1, int(cap)) + 1
        self.buf = [0.0] * self.cap
        self.pos = 0
        self.count = 0

    def push_pop(self, x, d):
        # d-sample RIGHT shift: y[n] = x[n-d]; outputs n = 0..d-1 are
        # zero-filled and y[d] = x[0]. After writing x at pos and
        # advancing, x[n-d] is d+1 slots behind the new head.
        self.buf[self.pos] = x
        self.pos = (self.pos + 1) % self.cap
        self.count += 1
        d = int(d)
        if d <= 0:
            return x
        if self.count <= d:
            return 0.0
        return self.buf[(self.pos - 1 - d) % self.cap]

    def snapshot(self):
        k = min(self.count, self.cap)
        history = [self.buf[(self.pos - k + i) % self.cap]
                   for i in range(k)]
        return {"cap": self.cap, "count": self.count, "history": history}

    @classmethod
    def restore(cls, snap):
        dl = cls(snap["cap"])
        for v in snap["history"]:
            dl.buf[dl.pos] = v
            dl.pos = (dl.pos + 1) % dl.cap
        dl.count = int(snap["count"])
        return dl


class Branch:
    """One routing branch: edge-cascade output -> polarity -> delay.

    Edge filtering is performed once per sample by Engine.evaluate_tree
    (shared edge states); a branch only applies polarity and its driver
    delay line to the already computed band sample.
    """

    def __init__(self, name, driver, stage_specs, polarity, delay,
                 delay_cap):
        self.name = name
        self.driver = driver
        # stage_specs: list of (edge_index, kind) in cascade order
        self.stage_specs = [(e, k) for (e, k) in stage_specs]
        self.polarity = int(polarity)
        self.delay = int(delay)
        self.dline = DelayLine(max(delay_cap, self.delay))

    def emit(self, band_sample):
        v = band_sample * self.polarity
        return self.dline.push_pop(v, self.delay)

    def snapshot(self):
        return {
            "name": self.name,
            "driver": self.driver,
            "polarity": self.polarity,
            "delay": self.delay,
            "stages": [{"edge": e, "kind": k}
                       for (e, k) in self.stage_specs],
            "dline": self.dline.snapshot(),
        }

    def restore_state(self, snap):
        self.polarity = int(snap["polarity"])
        self.delay = int(snap["delay"])
        self.dline = DelayLine.restore(snap["dline"])


def _round_struct(obj, ndigits):
    if isinstance(obj, list):
        return [_round_struct(v, ndigits) for v in obj]
    return round(float(obj), ndigits)


class Engine:
    """Sample-accurate, block-size independent crossover engine."""

    def __init__(self, sr, bands, xovers, delay_ms=None, polarity=None,
                 events=None, channels=1):
        self.sr = int(sr)
        self.bands_cfg = bands
        self.events = sorted(
            [dict(e) for e in (events or [])],
            key=lambda e: e["at"])
        self._event_i = 0
        self.n = 0
        self.channels = int(channels)

        # ---- edge (crossover point) coefficient template -------------
        edge_templates = []
        for xo in xovers:
            if isinstance(xo, dict):
                a = xo.get("a")
                fc = xo.get("fc")
            else:
                a, fc = None, xo
            if a is None:
                a = lp_alpha(fc, self.sr)
            edge_templates.append(
                (float(a), None if fc is None else float(fc)))

        # ---- per-driver delays (scan events to size ring buffers) ----
        self.delay_ms = {k: float(v) for k, v in (delay_ms or {}).items()}
        max_delay = {drv: delay_samples(ms, self.sr)
                     for drv, ms in self.delay_ms.items()}
        for ev in self.events:
            for drv, ms in (ev.get("delay_ms") or {}).items():
                d = delay_samples(ms, self.sr)
                max_delay[drv] = max(max_delay.get(drv, 0), d)
        self.polarity_cfg = {k: int(v)
                             for k, v in (polarity or {}).items()}

        # ---- per-channel edge + branch bundles -----------------------
        # Each channel owns independent edge states (filter memory must
        # never leak between channels); coefficients stay identical.
        self.edge_ch = []
        self.ch = []
        for _c in range(self.channels):
            edges = [Edge(a, fc) for (a, fc) in edge_templates]
            self.edge_ch.append(edges)
            branches = []
            for bi, b in enumerate(bands):
                lo, hi = b["range"]
                specs = []
                if lo > 0:
                    specs.append((bi - 1, "hp"))
                if hi < self.sr / 2.0:
                    specs.append((bi, "lp"))
                drv = b["driver"]
                d = delay_samples(self.delay_ms.get(drv, 0.0), self.sr)
                br = Branch(
                    b["name"], drv, specs,
                    self.polarity_cfg.get(drv, 1), d,
                    max_delay.get(drv, 0))
                branches.append(br)
            self.ch.append(branches)

    @property
    def edges(self):
        """Channel-0 edges; used for coefficient/fc reporting."""
        return self.edge_ch[0]

    def evaluate_tree(self, x, c):
        """Compute raw band samples with one edge update per sample.

        The bands form an ordered split by contiguous edges. Each edge
        update yields an LP output (closes the current band) and an HP
        remainder (continues upward); the top band is the HP remainder
        above the last edge with no closing LP, exactly the README chain.
        """
        edges = self.edge_ch[c]
        rem = x
        raw = []
        for e in edges:
            e.update(rem)
            raw.append(e.lp_out)
            rem = e.hp_out
        raw.append(rem)  # top band: HP output of the final edge
        return raw

    # ------------------------------------------------------------------
    # event handling
    # ------------------------------------------------------------------
    def _apply_event(self, ev):
        if "xover" in ev or "edge" in ev:
            ei = ev.get("xover", ev.get("edge"))
            if "a" in ev:
                a = float(ev["a"])
                fc = None
            else:
                fc = float(ev["fc"])
                a = lp_alpha(fc, self.sr)
            # migrate coefficient on every channel sharing this edge,
            # keeping state p -> every branch stays continuous
            for edges in self.edge_ch:
                edges[ei].set_alpha(a)
                if fc is not None:
                    edges[ei].fc = fc

        for drv, pol in (ev.get("polarity") or {}).items():
            for branches in self.ch:
                for br in branches:
                    if br.driver == drv:
                        br.polarity = int(pol)

        for drv, ms in (ev.get("delay_ms") or {}).items():
            d = delay_samples(ms, self.sr)
            self.delay_ms[drv] = float(ms)
            for branches in self.ch:
                for br in branches:
                    if br.driver == drv:
                        # ring history retained: only the read head moves
                        br.delay = d

    def _consume_events(self):
        while (self._event_i < len(self.events)
               and self.events[self._event_i]["at"] <= self.n):
            ev = self.events[self._event_i]
            if ev["at"] != self.n:
                raise AssertionError(
                    "event at sample %d reached late at %d"
                    % (ev["at"], self.n))
            self._apply_event(ev)
            self._event_i += 1

    # ------------------------------------------------------------------
    # block processing
    # ------------------------------------------------------------------
    def process_block(self, block):
        """Feed one block. Mono: list; multi: list of per-channel lists.

        Returns (bands_out, mix_out), indexed [channel][band][n].
        """
        if self.channels == 1:
            # accept either a plain mono list or a channel-major [list]
            if block and isinstance(block[0], list):
                block = block[0]
            outs = [[] for _ in self.ch[0]]
            mix = []
            for x in block:
                self._consume_events()
                raw = self.evaluate_tree(x, 0)
                vals = [br.emit(v) for br, v in zip(self.ch[0], raw)]
                for o, v in zip(outs, vals):
                    o.append(v)
                mix.append(sum(vals))
                self.n += 1
            return [outs], [mix]

        t_len = len(block[0])
        for ch_sig in block:
            if len(ch_sig) != t_len:
                raise ValueError("all channels must share block length")
        outs = [[[] for _ in branches] for branches in self.ch]
        mixes = [[] for _ in self.ch]
        for i in range(t_len):
            self._consume_events()
            for c, branches in enumerate(self.ch):
                raw = self.evaluate_tree(block[c][i], c)
                vals = [br.emit(v) for br, v in zip(branches, raw)]
                for o, v in zip(outs[c], vals):
                    o.append(v)
                mixes[c].append(sum(vals))
            self.n += 1
        return outs, mixes

    # ------------------------------------------------------------------
    # monitoring state
    # ------------------------------------------------------------------
    def snapshot(self):
        return {
            "n": self.n,
            "edges": [[e.snapshot() for e in edges]
                      for edges in self.edge_ch],
            "delay_ms": dict(self.delay_ms),
            "channels": [[br.snapshot() for br in branches]
                         for branches in self.ch],
            "events_remaining": [dict(e)
                                 for e in self.events[self._event_i:]],
        }

    def restore_state(self, snap):
        # adopt the snapshot cursor: the rebuilt engine continues exactly
        # where the monitored engine stopped
        self.n = int(snap["n"])
        self.edge_ch = [[Edge.restore(es) for es in edge_snaps]
                        for edge_snaps in snap["edges"]]
        self.delay_ms = dict(snap["delay_ms"])
        for c, branches in enumerate(self.ch):
            for br, bs in zip(branches, snap["channels"][c]):
                br.restore_state(bs)
        self.events = [dict(e) for e in snap["events_remaining"]]
        self._event_i = 0

    # ------------------------------------------------------------------
    # one-shot convenience runner
    # ------------------------------------------------------------------
    def run(self, signals, block_size=None):
        """Process the whole signal, optionally in fixed-size blocks."""
        if signals and isinstance(signals[0], list):
            signals = list(signals)  # already channel-major
        else:
            signals = [signals]     # mono -> single channel
        if len(signals) != self.channels:
            raise ValueError("channel count mismatch")
        total = len(signals[0])
        bs = total if block_size is None else max(1, int(block_size))
        outs = [[[] for _ in branches] for branches in self.ch]
        mixes = [[] for _ in self.ch]
        for start in range(0, total, bs):
            end = min(start + bs, total)
            block = [sig[start:end] for sig in signals]
            b_out, b_mix = self.process_block(block)
            for c in range(self.channels):
                for bi, o in enumerate(b_out[c]):
                    outs[c][bi].extend(o)
                mixes[c].extend(b_mix[c])
        if self._event_i != len(self.events):
            raise AssertionError("not all events were reached")
        return outs, mixes


def run_pipeline(inp, block_size=None):
    """Full engine entry point used by the CLI and by the test harness."""
    sr = inp["sr"]
    bands = inp["bands"]
    xovers = inp.get("xovers", [])
    raw = inp["signal"]
    if raw and isinstance(raw[0], list):
        channels = len(raw)       # channel-major: [L, R]
        signals = raw
    else:
        channels = 1
        signals = raw             # mono frame list
    eng = Engine(
        sr, bands, xovers,
        delay_ms=inp.get("delay_ms"),
        polarity=inp.get("polarity"),
        events=inp.get("events"),
        channels=channels)
    outs, mixes = eng.run(signals, block_size=block_size)

    band_records = []
    for bi, b in enumerate(bands):
        br0 = eng.ch[0][bi]
        samples = [outs[c][bi] for c in range(channels)]
        if channels == 1:
            samples = samples[0]
        band_records.append({
            "driver": b["driver"],
            "name": b["name"],
            "polarity": br0.polarity,
            "delay_samples": br0.delay,
            "stages": [{"edge": e, "kind": k}
                       for (e, k) in br0.stage_specs],
            "samples": _round_struct(samples, 5),
        })

    mix_out = mixes[0] if channels == 1 else mixes
    rounded_mix = _round_struct(mix_out, 5)

    flat_in = [v for sig in (signals if channels > 1 else [signals])
               for v in sig]
    rounded_bands = [_round_struct(outs[c][bi], 5)
                     for c in range(channels)
                     for bi in range(len(bands))]
    energy_in = round(sum(v * v for v in flat_in), 4)
    energy_out = round(
        sum(v * v for seq in rounded_bands for v in seq), 4)

    return {
        "bands": band_records,
        "mix": rounded_mix,
        "energy_in": energy_in,
        "energy_out": energy_out,
        "edges": [{"index": i, "a": e.a, "fc": e.fc}
                  for i, e in enumerate(eng.edges)],
        "state": eng.snapshot(),
    }

