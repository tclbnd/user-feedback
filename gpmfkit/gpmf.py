"""GoPro metadata (GPMF): KLV entries, devices, streams, scaling, sample counts."""
import struct
from fractions import Fraction

# One value of a GPMF type, in bytes, and the types that are read as text.
# A 'c' payload is one string of `size * repeat` bytes (names, units); a
# four-character code 'F' is four bytes and a UTC time 'U' sixteen.
SIZES = {"b": 1, "B": 1, "c": 1, "s": 2, "S": 2, "l": 4, "L": 4, "f": 4, "q": 4, "F": 4,
         "j": 8, "J": 8, "d": 8, "Q": 8, "G": 16, "U": 16}
TEXT = ("c", "F", "U")
FMT = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "i", "L": "I", "f": "f", "j": "q", "J": "Q", "d": "d"}
# Fixed-point types: 'q' is Q15.16 (four bytes) and 'Q' is Q31.32 (eight).
FIXED = {"q": (">i", 16), "Q": (">q", 32)}
# Entries that carry structure or stream metadata instead of samples.
DESCRIPTIVE = {"STNM", "SIUN", "UNIT", "TYPE", "SCAL", "ORIN", "TSMP", "EMPT",
               "STMP", "TICK", "TOCK", "ORIO", "MTRX", "DVID", "DVNM"}


class GpmfError(ValueError):
    pass


def looks_like_entry(data, p, end):
    """Whether an entry header can start at p (four printable key characters)."""
    if p + 8 > end:
        return False
    if any(ch < 0x20 or ch > 0x7E for ch in data[p:p + 4]):
        return False
    return p + 8 + data[p + 5] * struct.unpack_from(">H", data, p + 6)[0] <= end


def entries(data, start, end):
    """The KLV entries in [start, end).

    GPMF pads every payload with zero bytes to a multiple of four, and that
    padding has to be stepped over to reach the next key: a name whose length
    is not a multiple of four would otherwise hide everything after it. A
    track that leaves the padding out still reads, by looking for the next
    entry header right after the payload.
    """
    p = start
    while p + 8 <= end:
        key = data[p:p + 4].decode("latin-1")
        if not key.strip("\0"):  # zero fill at the end of a track
            return
        typ, size, repeat = chr(data[p + 4]), data[p + 5], struct.unpack_from(">H", data, p + 6)[0]
        length = size * repeat
        body = p + 8
        if body + length > end:
            raise GpmfError("%s runs past its container" % key)
        yield key, typ, size, repeat, body
        p = body + length
        pad = -length % 4
        if pad and p + pad <= end and (not any(data[p:p + pad]) or not looks_like_entry(data, p, end)):
            p += pad


def element_types(typ, size, complex_type, repeat=1):
    """The (type, width) pairs making up one sample of a payload.

    A numeric payload holds `size // width` values per sample. Character data
    is one string sample. A complex '?' payload is described by the stream's
    TYPE entry, where a digit repeats the type after it: "6s" is six int16
    values and "lllll" five int32 ones for a twenty-byte GPS5 sample.
    """
    if typ == "\0":
        raise GpmfError("container used as a sample")
    if typ == "?":
        if not complex_type:
            raise GpmfError("complex sample without a TYPE entry")
        types, count = [], ""
        for ch in complex_type:
            if ch.isdigit():
                count += ch
                continue
            if ch not in SIZES:
                raise GpmfError("unknown type %r in TYPE %r" % (ch, complex_type))
            types.extend([(ch, SIZES[ch])] * (int(count) if count else 1))
            count = ""
        if count or not types:
            raise GpmfError("bad TYPE %r" % complex_type)
        if sum(w for _, w in types) != size:
            raise GpmfError("TYPE %r does not describe %d-byte samples" % (complex_type, size))
        return types
    if typ == "c":
        return [(typ, size * repeat)]
    width = SIZES.get(typ)
    if not width:  # an opaque type: one value of the whole sample size
        return [(typ, size)]
    if size % width:
        raise GpmfError("%d-byte samples are not whole %r values" % (size, typ))
    return [(typ, width)] * (size // width)


def samples_of(typ, size, repeat):
    """How many samples a payload holds, and the stride between them.

    A 'c' payload is one string, however long it is; every other type holds
    `repeat` samples of `size` bytes each.
    """
    if not size or not repeat:
        return 0, 0
    if typ == "c":
        return 1, size * repeat
    return repeat, size


def value(typ, raw):
    if typ in TEXT:
        return raw.decode("latin-1").rstrip("\0")
    if typ in FIXED:
        code, bits = FIXED[typ]
        return Fraction(struct.unpack(code, raw)[0], 1 << bits)
    if typ in FMT:
        return struct.unpack(">" + FMT[typ], raw)[0]
    return raw.hex()


def text(data, body, size, repeat):
    """A string payload; its padding is zero, so trailing NULs are dropped."""
    return data[body:body + size * repeat].decode("latin-1").rstrip("\0")


def numbers(data, typ, body, size, repeat):
    """Every value of a metadata payload, in order."""
    if typ == "c":
        return [text(data, body, size, repeat)]
    types = element_types(typ, size, None)
    out, p = [], body
    for _ in range(repeat):
        for et, width in types:
            out.append(value(et, data[p:p + width]))
            p += width
    return out


def orientation(data, typ, body, size, repeat):
    """ORIN: the camera's orientation, as a list of values (null without one)."""
    return [plain(v) for v in numbers(data, typ, body, size, repeat)] or None


def divisor(scal, index):
    """SCAL holds one divisor for every element, or one per element."""
    if not scal:
        return 1
    if len(scal) == 1:
        return scal[0]
    return scal[index] if index < len(scal) else scal[-1]


def scaled(v, div):
    if isinstance(v, bool) or not isinstance(v, (int, float, Fraction)):
        return v
    if not div:
        return v
    if isinstance(v, float) or isinstance(div, float):
        return v / float(div)
    return Fraction(v) / Fraction(div)


def fmt(v):
    """A sample value as text: a fraction, six significant digits, or text."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, Fraction)):
        return str(Fraction(v))
    if isinstance(v, float):
        return "%.6g" % v
    return v


def plain(v):
    """A value as JSON carries it: fractions become their exact text."""
    return str(Fraction(v)) if isinstance(v, Fraction) else v


def sample(types, data, offset, scal):
    """One sample's values, each divided by its SCAL divisor."""
    vals, p = [], offset
    for i, (et, width) in enumerate(types):
        vals.append(fmt(scaled(value(et, data[p:p + width]), divisor(scal, i))))
        p += width
    return vals


def device_id(data, typ, body, size, repeat):
    """DVID: a decimal device number, or a name for a character payload."""
    vals = numbers(data, typ, body, size, repeat)
    return str(vals[0]) if vals else "0"


def new_stream(elements):
    return {"name": None, "units": None, "elements": elements, "samples": 0,
            "total_samples": None, "first": None, "last": None, "orientation": None, "empty": 0}


def zero_state():
    """The metadata a payload inherits from the entries before it."""
    return {"SCAL": None, "TYPE": None, "STNM": None, "SIUN": None, "UNIT": None,
            "ORIN": None, "TSMP": None}


def stream_of(device, key):
    st = device["streams"].get(key)
    if st is None:
        st = device["streams"][key] = new_stream(0)
    return st


def parse_payload(device, key, data, typ, size, repeat, body, sticky):
    """Add one payload of `key` samples to its stream under the device."""
    state = device["states"].setdefault(key, zero_state())
    for field, v in sticky.items():
        if v is not None:
            state[field] = v
    st = stream_of(device, key)
    if state["STNM"] is not None:
        st["name"] = state["STNM"]
    st["units"] = state["SIUN"] or state["UNIT"] or st["units"]
    if state["ORIN"] is not None:
        st["orientation"] = state["ORIN"]
    if state["TSMP"] is not None:
        st["total_samples"] = state["TSMP"]
    if not size or not repeat:  # an entry that only sets the metadata
        return
    types = element_types(typ, size, state["TYPE"], repeat)
    count, stride = samples_of(typ, size, repeat)
    st["elements"] = len(types)
    st["samples"] += count
    if count:
        if st["first"] is None:
            st["first"] = sample(types, data, body, state["SCAL"])
        st["last"] = sample(types, data, body + (count - 1) * stride, state["SCAL"])


def parse_entries(device, registry, data, start, end, sticky=None):
    """Walk the entries of a DEVC or STRM payload.

    STNM, SIUN/UNIT, SCAL, TYPE, ORIN and TSMP describe the payloads that
    follow them and stay in force until replaced, so a later payload may
    leave them out. EMPT counts the samples its payload left out, and so
    belongs to the stream of the key its payload carries.
    """
    sticky = dict(sticky or zero_state())
    empty = 0
    last = None
    for key, typ, size, repeat, body in entries(data, start, end):
        if key == "SCAL":
            sticky["SCAL"] = numbers(data, typ, body, size, repeat) or None
        elif key in ("STNM", "SIUN", "UNIT", "TYPE"):
            sticky[key] = text(data, body, size, repeat)
        elif key == "ORIN":
            sticky["ORIN"] = orientation(data, typ, body, size, repeat)
        elif key == "TSMP":
            vals = numbers(data, typ, body, size, repeat)
            if vals:
                sticky["TSMP"] = plain(vals[0])
        elif key == "EMPT":
            empty += sum(v for v in numbers(data, typ, body, size, repeat)
                         if isinstance(v, (int, Fraction)))
        elif typ == "\0":
            if key == "STRM":  # a stream, or a stream nested in one
                parse_entries(device, registry, data, body, body + size * repeat, sticky)
            elif key == "DEVC":  # a device inside a device
                parse_device(registry, data, body, body + size * repeat, device)
        elif key in DESCRIPTIVE:
            continue
        else:
            last = key
            parse_payload(device, key, data, typ, size, repeat, body, sticky)
            if empty:
                stream_of(device, key)["empty"] += empty
                empty = 0
    if empty and last is not None:
        stream_of(device, last)["empty"] += empty


def parse_device(registry, data, start, end, parent=None):
    """Read one DEVC payload: its identity, its streams and nested devices."""
    children = list(entries(data, start, end))
    ident = None
    for k, t, s, r, b in children:
        if k == "DVID":
            ident = device_id(data, t, b, s, r)
            break
    if ident is None:
        ident = parent["id"] if parent is not None else "0"
    dev = registry.get(ident)
    if dev is None:
        dev = registry[ident] = {"id": ident, "name": None, "streams": {}, "states": {}}
    name = None
    for k, t, s, r, b in children:
        if k == "DVNM":
            name = text(data, b, s, r)
            break
    if name is not None:
        dev["name"] = name
    parse_entries(dev, registry, data, start, end)
    return dev


def parse(data):
    registry = {}
    for key, typ, size, repeat, body in entries(data, 0, len(data)):
        if key == "DEVC" and typ == "\0":
            parse_device(registry, data, body, body + size * repeat)
    return {"devices": [{"id": k, "name": v["name"], "streams": v["streams"]}
                        for k, v in registry.items()]}
