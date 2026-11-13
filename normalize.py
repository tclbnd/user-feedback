"""Peak-based WAV normalizer."""
import argparse
from array import array
import json
import math
from pathlib import Path
import wave


def normalize(source,target,layout,goal,limit,block):
    with wave.open(str(source),"rb") as w:
        params=w.getparams()
        values=array("h",w.readframes(params.nframes))
    peak=max(map(abs,values),default=0)/32768
    gain=10**(limit/20)/peak if peak else 1
    quantized=array("h",(min(32767,max(-32768,round(s*gain))) for s in values))
    with wave.open(str(target),"wb") as out:
        out.setparams(params)
        out.writeframes(quantized.tobytes())
    return {"layout":layout,"gain_db":20*math.log10(gain),
            "input":{"lufs":None,"dbtp":20*math.log10(peak) if peak else None},
            "output":{"lufs":None,"dbtp":limit if peak else None},"reason":"sample_peak"}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("report",type=Path)
    p.add_argument("--layout",default="stereo")
    p.add_argument("--target",type=float,default=-23)
    p.add_argument("--ceiling",type=float,default=-1)
    p.add_argument("--block",type=int,default=4096)
    a=p.parse_args()
    result=normalize(a.input,a.output,a.layout,a.target,a.ceiling,a.block)
    a.report.write_text(json.dumps(result,sort_keys=True,allow_nan=False)+"\n")


if __name__=="__main__":
    main()
