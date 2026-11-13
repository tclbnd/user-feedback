"""2 x 2 audio convolver with an incomplete callback-local implementation."""
import argparse
import json
from pathlib import Path


def render(data):
    kernels=data["kernels"][data["initial"]]
    result=[]
    for block in data["blocks"]:
        for i,_ in enumerate(block):
            result.append([sum(block[i-j][ch]*v for ch,k in enumerate(paths)
                               for j,v in enumerate(k[:i+1])) for paths in kernels])
    return {"frames":result,"latency":0,"stats":{"fft_count":0,"steady_state_buffer_allocations":0}}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    try:
        result=render(json.loads(a.input.read_text()))
        a.output.write_text(json.dumps(result,allow_nan=False)+"\n")
    except (ValueError,KeyError,TypeError) as error:
        p.exit(2,str(error)+"\n")


if __name__=="__main__":
    main()
