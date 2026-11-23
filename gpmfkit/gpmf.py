"""GoPro metadata (GPMF): KLV entries, devices, streams, scaling, sample counts."""
import struct
from fractions import Fraction

SIZES = {"b": 1, "B": 1, "c": 1, "u": 1, "s": 2, "S": 2, "l": 4, "L": 4, "f": 4, "q": 4, "F": 4, "j": 8,
         "J": 8, "d": 8, "Q": 8, "G": 16, "U": 16}
FMT = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "i", "L": "I", "f": "f", "j": "q", "J": "Q", "d": "d"}
FIXED = {"q": ("i", 1 << 16), "Q": ("q", 1 << 32)}  # 16.16 and 32.32 fixed point
TEXT = "cuFU"  # ASCII, UTF-8, four-character code, UTC date and time
SKIP = {"STMP", "TICK", "TOCK", "ORIO", "MTRX"}
GPS_FIX = {0: "none", 2: "2d", 3: "3d"}
FIELD = {"STNM": "name", "SIUN": "siun", "UNIT": "unit", "ORIN": "orientation"}
GPS_FIELD = {"GPSF": "gpsf", "GPSP": "gpsp"}


class GpmfError(ValueError):
    pass


def pad(n):
    """KLV payloads are padded to a multiple of four bytes."""
    return (n + 3) & ~3


def entries(data, start, end):
    """Walk the KLV entries of one container, honouring the four-byte padding."""
    p = start
    while p + 8 <= end:
        key = data[p:p + 4].decode("latin-1")
        typ, size, repeat = chr(data[p + 4]), data[p + 5], struct.unpack_from(">H", data, p + 6)[0]
        body = p + 8
        # EMPT (type 0) is a header-only entry: repeat is its empty count.
        length = 0 if key == "EMPT" and typ == "\0" else size * repeat
        if body + length > end:
            raise GpmfError("%s runs past its container" % key)
        yield key, typ, size, repeat, body
        p = body + pad(length)


def layout(typ, size, repeat, complex_type=None):
    """The elements of one sample and the number of samples in a payload.

    For numeric types SIZE is the sample's width and REPEAT the sample count.
    A character payload (c, u) is one run of text, while a four-character
    code or a date (F, U) is one fixed-width value per repeat.
    """
    if typ == "?":
        return complex_types(size, complex_type), repeat
    unit = SIZES.get(typ)
    if not unit:
        raise GpmfError("unknown type %r" % typ)
    if typ in "cu":
        return [(typ, size * repeat)], 1
    if typ in "FU":
        return [(typ, unit)] * repeat, 1
    return [(typ, unit)] * (size // unit), repeat


def complex_types(size, complex_type):
    """For a '?' entry: TYPE lists the element types of one sample.

    A sample is size bytes wide; the pattern repeats when the TYPE string
    describes only part of it.
    """
    pattern = [c for c in (complex_type or "") if c not in "\0 "]
    if not pattern or any(c not in SIZES for c in pattern):
        raise GpmfError("complex sample type without a usable TYPE entry")
    width = sum(SIZES[c] for c in pattern)
    if not size or size % width:
        raise GpmfError("TYPE does not fill the sample size")
    return [(c, SIZES[c]) for c in pattern * (size // width)]


def value(typ, raw):
    fixed = FIXED.get(typ)
    if fixed:
        code, scale = fixed
        return Fraction(struct.unpack(">" + code, raw[:SIZES[typ]])[0], scale)
    if typ in FMT:
        return struct.unpack(">" + FMT[typ], raw)[0]
    if typ in TEXT:
        return raw.decode("latin-1").rstrip("\0")
    return raw.hex()


def text(data, body, size, repeat):
    """A character entry is size*repeat bytes, not just its first element."""
    return data[body:body + size * repeat].decode("latin-1").rstrip("\0")


def values(typ, data, body, size, repeat):
    unit = SIZES.get(typ)
    if not unit:
        raise GpmfError("cannot read values of type %r" % typ)
    n = size * repeat // unit
    return [value(typ, data[body + i * unit:body + (i + 1) * unit]) for i in range(n)]


def fmt(v):
    if isinstance(v, Fraction):
        return str(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return str(Fraction(v))
    if isinstance(v, float):
        return "%.6g" % v
    return v


def whole(v):
    """A count read from a payload, as an int when it is one."""
    try:
        if isinstance(v, float):
            return int(v)
        if isinstance(v, Fraction):
            return int(v) if v.denominator == 1 else v
        return int(v)
    except (TypeError, ValueError):
        return v


def scaled(v, div):
    if not div:
        return v
    if isinstance(v, float) or isinstance(div, float):
        return v / float(div)
    return Fraction(v) / Fraction(div)


def sample(data, types, body, index, scal):
    """The values of one sample, each divided by its SCAL divisor.

    types are the (type, width) elements of one sample, scal the divisors.
    """
    width = sum(n for _, n in types)
    q = body + index * width
    out = []
    for i, (et, n) in enumerate(types):
        v = value(et, data[q:q + n])
        q += n
        if isinstance(v, (int, float, Fraction)) and not isinstance(v, bool):
            v = scaled(v, scal[i] if i < len(scal) else scal[-1])
        out.append(fmt(v))
    return out


def device_id(data, typ, size, repeat, body):
    if typ in SIZES and typ not in TEXT:
        return str(value(typ, data[body:body + SIZES[typ]]))
    return text(data, body, size, repeat)


def new_stream(elements):
    return {"name": None, "units": None, "elements": elements, "samples": 0, "total_samples": None,
            "first": None, "last": None, "orientation": None, "empty": 0,
            "scal": [1], "siun": None, "unit": None, "gpsf": None, "gpsp": None, "ctype": None}


def finish_stream(st):
    out = {"name": st["name"], "units": st["siun"] or st["unit"], "elements": st["elements"],
           "samples": st["samples"], "total_samples": st["total_samples"], "first": st["first"],
           "last": st["last"], "orientation": st["orientation"], "gps_fix": None,
           "gps_precision": None, "empty": st["empty"]}
    if st["gpsf"] is not None:
        out["gps_fix"] = GPS_FIX.get(st["gpsf"], "none")
    if st["gpsp"] is not None:
        out["gps_precision"] = fmt(hundredth(st["gpsp"]))
    return out


def hundredth(v):
    """GPSP dilution of precision, divided by 100 as an exact fraction."""
    if isinstance(v, float):
        return v / 100
    return Fraction(v) / 100


def empty_count(typ, data, body, size, repeat):
    """The EMPT counts carried by one entry, added together.

    EMPT can carry one count per skipped sample, but it may also be a
    header-only entry whose repeat field is the number of empty samples.
    """
    if typ == "\0" or typ not in SIZES or not size * repeat:
        return repeat
    if typ in TEXT or typ == "?":
        raw = data[body:body + size * repeat].decode("latin-1")
        total = 0
        for part in raw.split("\0"):
            part = part.strip()
            if part:
                try:
                    total += int(part)
                except ValueError:
                    pass
        return total
    return sum(whole(v) for v in values(typ, data, body, size, repeat))


def gain(typ, data, body, size, repeat):
    """A divisor entry (SCAL) as a list of numbers, defaulting to 1."""
    found = [v for v in values(typ, data, body, size, repeat) if isinstance(v, (int, float, Fraction))]
    return found or [1]


def scan_stream(data, start, end, streams):
    """Read one STRM container, folding its payloads into the device's streams."""
    aux = {"SCAL": None, "TYPE": None, "STNM": None, "SIUN": None, "UNIT": None, "ORIN": None,
           "TSMP": None, "GPSF": None, "GPSP": None}
    data_key = None
    pending_empty = 0
    touched = []
    for k, t, s, r, b in entries(data, start, end):
        if k == "SCAL":
            aux["SCAL"] = gain(t, data, b, s, r)
        elif k == "TYPE":
            aux["TYPE"] = text(data, b, s, r)
        elif k in FIELD:
            aux[k] = text(data, b, s, r)
        elif k in ("TSMP", "GPSF", "GPSP"):
            found = values(t, data, b, s, r)
            if found:
                aux[k] = whole(found[-1])
            # GPSF/GPSP describe the payload they follow, as well as any
            # payload that follows them.
            if k in GPS_FIELD and data_key is not None and found:
                streams[data_key][GPS_FIELD[k]] = aux[k]
        elif k == "EMPT":
            empty = empty_count(t, data, b, s, r)
            if data_key is None:
                pending_empty += empty
            else:
                streams[data_key]["empty"] += empty
        elif k in SKIP:
            continue
        else:
            st = streams.get(k)
            ctype = aux["TYPE"] or (st["ctype"] if st is not None else None)
            types, count = layout(t, s, r, ctype)
            if st is None:
                st = streams[k] = new_stream(len(types))
            if aux["TYPE"] is not None:
                st["ctype"] = aux["TYPE"]
            # Metadata carries on to later payloads; the last one seen wins.
            if aux["STNM"] is not None:
                st["name"] = aux["STNM"]
            if aux["SIUN"] is not None:
                st["siun"] = aux["SIUN"]
            if aux["UNIT"] is not None:
                st["unit"] = aux["UNIT"]
            if aux["ORIN"] is not None:
                st["orientation"] = aux["ORIN"]
            if aux["GPSF"] is not None:
                st["gpsf"] = aux["GPSF"]
            if aux["GPSP"] is not None:
                st["gpsp"] = aux["GPSP"]
            if aux["SCAL"] is not None:
                st["scal"] = aux["SCAL"]
            st["empty"] += pending_empty
            pending_empty = 0
            st["samples"] += count
            if count:
                if st["first"] is None:
                    st["first"] = sample(data, types, b, 0, st["scal"])
                st["last"] = sample(data, types, b, count - 1, st["scal"])
            if k not in touched:
                touched.append(k)
            if data_key is None:
                data_key = k
    if aux["TSMP"] is not None:
        for k in touched:
            streams[k]["total_samples"] = aux["TSMP"]


def parse(data):
    devices = {}
    order = []
    for key, typ, size, repeat, body in entries(data, 0, len(data)):
        if key != "DEVC" or typ != "\0":
            continue
        dev, name, blocks = None, None, []
        for k, t, s, r, b in entries(data, body, body + size * repeat):
            if k == "DVID":
                dev = device_id(data, t, s, r, b)
            elif k == "DVNM":
                name = text(data, b, s, r)
            elif k == "STRM" and t == "\0":
                blocks.append((b, s * r))
        if dev is None:
            dev = "0"
        d = devices.get(dev)
        if d is None:
            d = devices[dev] = {"name": None, "streams": {}}
            order.append(dev)
        if name is not None:
            d["name"] = name
        for sb, sl in blocks:
            scan_stream(data, sb, sb + sl, d["streams"])
    return {"devices": [{"id": k, "name": devices[k]["name"],
                         "streams": {sk: finish_stream(sv) for sk, sv in devices[k]["streams"].items()}}
                        for k in order]}
