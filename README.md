# Pitch-shift resynthesis from partials

Run: `python3 pitch.py input.json output.json`

Input `{"partials":[{"freq","amp","phase"}...],"ratio":R,"sr":SR,
"frames":N}` — tracked sine partials of one note; resynthesize at
pitch ratio R (freq multiplied, amp/phase preserved), render N
samples at sample rate SR.

Per output sample i (i = 0..N-1), time `t = i/SR`:
`y[i] = sum over partials of amp * cos(2*pi*(R*freq)*t + phase)`.
Then apply a **half-hann edge fade**: for i < F where F=N//4,
`y[i] *= 0.5 - 0.5*cos(pi*(i+1)/F)`; for i >= N-F,
`y[i] *= 0.5 - 0.5*cos(pi*(N-i)/F)`. DC removal last:
subtract mean(y) once.
Output `{"samples":[...]}` each rounded 6 decimals, plus
`{"peak":max(|y|) 6 decimals, "freqs":[R*freq...]}` preserving input
partial order, freqs rounded 3 decimals.
