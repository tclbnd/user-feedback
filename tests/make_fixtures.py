# -*- coding: utf-8 -*-
"""Generate deterministic acceptance-test fixtures (12 clip EDL + 2 VTT)."""
import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures"
ROOT.mkdir(parents=True, exist_ok=True)

ALPHA = """WEBVTT

NOTE
alpha source notes

STYLE
::cue(.hot) { color: red; }
::cue(.shared) { font-style: italic; }

REGION
id:top
width:40%
lines:3

a1
00:00:00.050 --> 00:00:02.200 region:top
<c.hot>Alpha one <00:00:00.300>middle <00:00:01.500>late</c>

a2
00:00:00.333 --> 00:00:00.337
Tiny

a3
00:00:01.000 --> 00:00:04.000
<c.shared>Spanning <00:00:01.800>marker <00:00:03.200>tail</c>

a4
00:00:02.100 --> 00:00:02.600
Overlap cue
"""

BETA = """WEBVTT

STYLE
::cue(.shared) { font-weight: bold; }
::cue(.cold) { color: blue; }

REGION
id:top
width:60%
lines:2

b1
00:00:00.000 --> 00:00:02.500 region:top
<c.cold>Beta one <00:00:00.800>point <00:00:02.000>end</c>

b2
00:00:01.200 --> 00:00:03.000
<c.shared>Beta two <00:00:01.900>x</c>

b3
00:00:03.500 --> 00:00:04.500
<c.cold>Beta three</c>
"""

# 12 clips: repeated segments, both sources, hard cuts, speeds in [1/4, 4]
clips = [
    dict(id="k01", source="alpha", start_frame=0,   end_frame=30,  speed=[1, 1]),
    dict(id="k02", source="alpha", start_frame=30,  end_frame=90,  speed=[3, 2]),
    dict(id="k03", source="beta",  start_frame=0,   end_frame=45,  speed=[1, 1]),
    dict(id="k04", source="alpha", start_frame=60,  end_frame=120, speed=[2, 1]),
    dict(id="k05", source="beta",  start_frame=45,  end_frame=120, speed=[1, 2]),
    dict(id="k06", source="alpha", start_frame=30,  end_frame=60,  speed=[1, 4]),
    dict(id="k07", source="beta",  start_frame=90,  end_frame=135, speed=[4, 1]),
    dict(id="k08", source="alpha", start_frame=0,   end_frame=30,  speed=[1, 1]),
    dict(id="k09", source="alpha", start_frame=90,  end_frame=150, speed=[3, 1]),
    dict(id="k10", source="beta",  start_frame=0,   end_frame=45,  speed=[2, 3]),
    dict(id="k11", source="alpha", start_frame=120, end_frame=150),
    dict(id="k12", source="beta",  start_frame=120, end_frame=150, speed=[1, 1]),
]
edl = dict(sources=dict(alpha="alpha.vtt", beta="beta.vtt"), clips=clips)

(ROOT / "alpha.vtt").write_text(ALPHA, encoding="utf-8")
(ROOT / "beta.vtt").write_text(BETA, encoding="utf-8")
(ROOT / "edl.json").write_text(json.dumps(edl, indent=2) + "\n",
                               encoding="utf-8")
print("fixtures written to", ROOT)
