"""GoPro metadata (GPMF): KLV entries, devices, streams, scaling, sample counts."""
import struct
from fractions import Fraction

SIZES = {"b": 1, "B": 1, "c": 1, "s": 2, "S": 2, "l": 4, "L": 4, "f": 4, "q": 4, "F": 4, "j": 8, "J": 8, "d": 8,
         "Q": 8, "G": 16, "U": 16}
FMT = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "i", "L": "I", "f": "f", "j": "q", "J": "Q", "d": "d"}


class GpmfError(ValueError):
    pass


def entries(data, start, end):
    p = start
    while p + 8 <= end:
        key = data[p:p + 4].decode("latin-1")
        typ, size, repeat = chr(data[p + 4]), data[p + 5], struct.unpack_from(">H", data, p + 6)[0]
        length = size * repeat
        body = p + 8
        if body + length > end:
            raise GpmfError("%s runs past its container" % key)
        yield key, typ, size, repeat, body
        p = body + length


def element_types(typ, size, complex_type):
    unit = SIZES.get(typ)
    if not unit:
        raise GpmfError("unknown type %r" % typ)
    return [typ] * (size // unit)


def value(typ, raw):
    if typ in FMT:
        return struct.unpack("<" + FMT[typ], raw)[0]
    if typ in "cFU":
        return raw.decode("latin-1").rstrip("\0")
    return raw.hex()


def text(data, body, size, repeat):
    return data[body:body + size].decode("latin-1").rstrip("\0")


def fmt(v):
    if isinstance(v, (int, Fraction)) and not isinstance(v, bool):
        return str(Fraction(v))
    if isinstance(v, float):
        return "%.6g" % v
    return v


def parse(data):
    devices = {}
    order = []
    for key, typ, size, repeat, body in entries(data, 0, len(data)):
        if key != "DEVC" or typ != "\0":
            continue
        dev, streams = None, []
        for k, t, s, r, b in entries(data, body, body + size * repeat):
            if k == "DVID":
                dev = str(value(t, data[b:b + s])) if t != "F" else text(data, b, s, r)
            elif k == "DVNM":
                name = text(data, b, s, r)
                streams.append(("name", name))
            elif k == "STRM" and t == "\0":
                streams.append(("strm", b, s * r))
        if dev is None:
            dev = "0"
        d = devices.get(dev)
        if d is None:
            d = devices[dev] = {"name": None, "streams": {}}
            order.append(dev)
        for item in streams:
            if item[0] == "name":
                d["name"] = item[1]
                continue
            _, sb, sl = item
            sticky = {"SCAL": [1], "TYPE": None, "STNM": None, "SIUN": None, "UNIT": None, "TSMP": None}
            for k, t, s, r, b in entries(data, sb, sb + sl):
                if k == "SCAL":
                    one = SIZES[t]
                    sticky["SCAL"] = [value(t, data[b + i * one:b + (i + 1) * one]) for i in range(s * r // one)]
                elif k in ("STNM", "SIUN"):
                    sticky[k] = text(data, b, s, r)
                elif k == "TSMP":
                    sticky["TSMP"] = value(t, data[b:b + s])
                elif k in ("STMP", "TICK", "TOCK", "EMPT", "ORIN", "ORIO", "MTRX"):
                    continue
                else:
                    types = element_types(t, s, sticky["TYPE"])
                    st = d["streams"].setdefault(k, {"name": None, "units": None, "elements": len(types),
                                                     "samples": 0, "first": None, "total_samples": None})
                    st["name"] = sticky["STNM"] or st["name"]
                    st["units"] = sticky["SIUN"] or sticky["UNIT"] or st["units"]
                    st["samples"] = r
                    if sticky["TSMP"] is not None:
                        st["total_samples"] = sticky["TSMP"]
                    if st["first"] is None and r:
                        vals, q = [], b
                        scal = sticky["SCAL"]
                        for i, et in enumerate(types):
                            n = SIZES[et]
                            v = value(et, data[q:q + n])
                            q += n
                            if isinstance(v, (int, float, Fraction)) and not isinstance(v, bool):
                                div = scal[0]
                                if div:
                                    v = Fraction(v) / Fraction(div) if not isinstance(v, float) else v / float(div)
                            vals.append(fmt(v))
                        st["first"] = vals
    return {"devices": [{"id": k, **devices[k]} for k in order]}
