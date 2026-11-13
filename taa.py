"""A small offline renderer with one foreground quad and a procedural background."""
import argparse
import json
import math
from pathlib import Path


def background(x, y):
    stripe = math.floor(1.75 * x + 0.25 * y) % 2
    return 0.32 + 0.26 * stripe


def render(frame):
    width, height = frame["size"]
    camera_x, camera_y = frame["camera"]
    object_x, object_y = frame["object"]
    jitter_x, jitter_y = frame["jitter"]
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            world_x = x + 0.5 + camera_x + jitter_x
            world_y = y + 0.5 + camera_y + jitter_y
            inside = (object_x <= world_x < object_x + 5
                      and object_y <= world_y < object_y + 4)
            if inside:
                row.append({"id": 1, "z": 0.24, "color": 0.87})
            else:
                row.append({"id": 0, "z": 0.80, "color": background(world_x, world_y)})
        pixels.append(row)
    return pixels


class Temporal:
    def __init__(self):
        self.previous = None

    def step(self, frame, enabled=True):
        samples = render(frame)
        color = [[p["color"] for p in row] for row in samples]
        weights = [[0.0 for p in row] for row in samples]
        reasons = [["disabled" for p in row] for row in samples]
        # Existing behavior blends in screen space and treats equal dimensions as
        # evidence that every pixel has history.
        if enabled and self.previous is not None:
            old = self.previous
            if (len(old), len(old[0])) == (len(color), len(color[0])):
                for y, row in enumerate(color):
                    for x, value in enumerate(row):
                        row[x] = value * 0.2 + old[y][x] * 0.8
                        weights[y][x] = 0.8
                        reasons[y][x] = "accepted"
        self.previous = color
        return {"color": color, "weights": weights, "reasons": reasons}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scene", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.scene.read_text())
    engine = Temporal()
    frames = [engine.step(frame, not args.no_history) for frame in data["frames"]]
    args.output.write_text(json.dumps({"frames": frames}, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
