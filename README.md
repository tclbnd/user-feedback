# Echo-canceller double-talk freezer

Run: `python3 aec.py input.json output.json`

Input `{"frames":[{"t","farend","nearend","mic"}...],"win":W}` —
per-frame signal energies; `win` ms look-back for estimates.

Per frame compute `est = echo estimate = 0.7 * max(farend) over the
last W ms (inclusive)` and `ser = mic / max(est, 1e-9)`. Rules:
- `ser >= 0.5` → double-talk: freeze adaptation (state `"frozen"`,
  hold previous `est` value for output `used_est`).
- else → adapting (`state":"adapting"`, `used_est = est`).
- A frozen period persists while `nearend > 0.3 * est_prev` — only
  when nearend drops below that does adaptation resume on the NEXT
  frame (hysteresis).
- Delay change: if farend jumps so `est` differs from `used_est_prev`
  by > 40% while not frozen, emit `{"t","kind":"delay-shift"}` in
  `alerts` and resync `used_est` immediately (state stays adapting).
- Output `{"frames":[{"t","state","used_est","ser"}...],
  "alerts":[...]}`; ser rounded 3 decimals, used_est 2 decimals.
