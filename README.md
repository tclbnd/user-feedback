# Multi-crossover speaker band router

Run: `python3 xover.py input.json output.json`

Input `{"xovers":[X1,X2],"bands":[{"name","range":[lo,hi],
"driver"}...],"delay_ms":{driver:ms},"sr":SR,"signal":[...]}` —
Linkwitz-Riley style 3-way split.

Per input sample n, band b owns frequencies [lo,hi) of a linear
filter model approximated here as first-order IIR per edge:
`y_lo[n] = a_lo * x[n] + (1-a_lo) * y_lo[n-1]` with
`a_lo = 2*pi*hi/SR / (1 + 2*pi*hi/SR)` (hi edge lowpass) and
`y_hp[n] = x[n] - z[n]` where `z[n] = a_hp*x[n]+(1-a_hp)*z[n-1]`,
`a_hp = 2*pi*lo/SR / (1 + 2*pi*lo/SR)` — band output is
`y_lo` of the cascaded chain `z` for lo>0 else y_lo of x.
Compute each band by filtering x twice: first highpass at lo
(skip when lo==0), then lowpass at hi (skip when hi>=SR/2).
Apply `delay_ms` per driver: shift output RIGHT by
round(ms*SR/1000) samples, zero-fill the head.
Output `{"bands":[{"driver","name","samples":[...]"}...]}` in input
band order, samples rounded 5 decimals; plus
`{"energy_in","energy_out"}` = sum of squares, 4 decimals.
