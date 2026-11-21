"""Ticks to seconds, notes and lyrics."""
from fractions import Fraction

from .smf import tracks


def analyse(data):
    fmt, division, trks = tracks(data)
    tempo = 500000
    tempo_map = [{"tick": 0, "us_per_quarter": tempo, "seconds": 0.0}]
    notes, lyrics = [], []

    def seconds(tick):
        return float(Fraction(tick * tempo, division * 1000000))

    for index, events in enumerate(trks):
        open_notes = {}
        for e in events:
            if e.kind == ("meta", 0x51):
                tempo = int.from_bytes(e.data, "big")
                tempo_map.append({"tick": e.tick, "us_per_quarter": tempo, "seconds": round(seconds(e.tick), 6)})
            elif e.kind == ("meta", 0x05):
                lyrics.append({"seconds": round(seconds(e.tick), 6), "text": e.data.decode("latin-1")})
            elif e.kind[0] == "channel" and e.kind[1] == 0x9:
                open_notes[(e.kind[2], e.data[0])] = (e.tick, e.data[1])
            elif e.kind[0] == "channel" and e.kind[1] == 0x8:
                start = open_notes.pop((e.kind[2], e.data[0]), None)
                if start:
                    notes.append({"track": index, "channel": e.kind[2], "pitch": e.data[0], "velocity": start[1],
                                  "start": round(seconds(start[0]), 6), "end": round(seconds(e.tick), 6)})
    notes.sort(key=lambda n: (n["start"], n["track"], n["pitch"]))
    return {"format": fmt, "division": division, "tracks": len(trks), "tempo_map": tempo_map,
            "notes": notes, "lyrics": lyrics}
