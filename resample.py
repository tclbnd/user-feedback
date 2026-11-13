"""JSON replay front end for a block-based PCM converter."""
import argparse
import json
import math
from pathlib import Path


class Converter:
    def __init__(self, in_rate, out_rate, channels):
        if type(in_rate) is not int or type(out_rate) is not int or min(in_rate,out_rate) < 1:
            raise ValueError("invalid sample rate")
        if type(channels) is not int or not 1 <= channels <= 8:
            raise ValueError("invalid channel count")
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.channels = channels

    def push(self, frames):
        for frame in frames:
            if len(frame) != self.channels or not all(math.isfinite(x) for x in frame):
                raise ValueError("invalid sample frame")
        # Current implementation restarts a nearest-neighbor clock on every block.
        count = len(frames) * self.out_rate // self.in_rate
        return [frames[i * self.in_rate // self.out_rate] for i in range(count)]

    def finish(self):
        return []

    def reset(self):
        pass

    def status(self):
        return {"buffered_frames": 0}


def replay(data):
    converter = Converter(data["in_rate"],data["out_rate"],data["channels"])
    results = []
    for command in data["commands"]:
        try:
            op = command["op"]
            if op == "push":
                frames = converter.push(command["frames"])
            elif op == "finish":
                frames = converter.finish()
            elif op == "reset":
                converter.reset()
                frames = []
            else:
                raise ValueError("invalid operation")
            result = {"frames":frames, **converter.status()}
        except (ValueError, TypeError) as error:
            result = {"frames":[],"error":str(error),**converter.status()}
        results.append(result)
    return {"results":results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    result = replay(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result,allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
