"""2 x 2 partitioned-FFT convolver with sample-accurate response switching.

Usage: python3 convolution.py demo.json output.json

Input JSON (see README.md):
  partition  - FFT partition size (power of two, 8..128)
  initial    - name of the response active at sample 0
  kernels    - name -> [output channel][input channel][tap]
  blocks     - interleaved stereo input blocks (arbitrary lengths)
  switches   - [{"at", "to", "fade"}] in non-decreasing absolute positions

The DSP lives in engine.py / partition_convolver.py / partition_fft.py.
JSON decoding/encoding containers are I/O, not DSP workspaces: all DSP arrays
are preallocated at configure() time and no DSP allocation occurs while the
steady-state callbacks run.
"""

import argparse
import json
from pathlib import Path

from engine import RirEngine


def render(data):
    partition = data["partition"]
    if not (isinstance(partition, int) and 8 <= partition <= 128
            and (partition & (partition - 1)) == 0):
        raise ValueError("partition must be a power of two in [8, 128]")
    kernels = data["kernels"]
    initial = data["initial"]
    blocks = data["blocks"]
    switches = data.get("switches", [])

    max_t = max(len(path) for matrix in kernels.values()
                for row in matrix for path in row)
    max_p = (max_t + partition - 1) // partition
    total_input = sum(len(block) for block in blocks)
    # Worst case: initial voice plus every switch alive simultaneously.
    max_voices = 1 + len(switches) + 1

    engine = RirEngine()
    engine.configure(partition=partition, max_partitions=max_p, c_in=2,
                     c_out=2, kernels=kernels, initial=initial,
                     max_voices=max_voices,
                     stream_cap=total_input + max_t + partition + 8)
    for switch in switches:
        engine.schedule_switch(at=switch["at"], to=switch["to"],
                               fade=switch["fade"])

    frames = []
    for block in blocks:
        # A JSON block is already a list of interleaved frames, each frame
        # carrying c_in channel samples -- exactly process()'s callback shape.
        engine.process(block)
        frames.extend(engine.take_output())
    engine.flush()
    frames.extend(engine.take_output())

    stats = engine.stats()
    return {
        "frames": frames,
        "latency": engine.max_intra_block_wait(),
        "stats": {
            "fft_count": stats["fft_count"],
            "steady_state_buffer_allocations":
                stats["steady_state_buffer_allocations"],
            "per_block": stats["per_block"],
            "algorithm_delay_samples": stats["algorithm_delay_samples"],
            "max_intra_block_wait_samples":
                stats["max_intra_block_wait_samples"],
            "tail_samples": stats["tail_samples"],
            "output_samples": stats["output_samples"],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        result = render(json.loads(args.input.read_text()))
        args.output.write_text(json.dumps(result, allow_nan=False) + "\n")
    except (ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(2, "%s\n" % error)


if __name__ == "__main__":
    main()
