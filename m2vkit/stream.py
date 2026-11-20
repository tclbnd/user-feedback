"""Walk start codes of a video elementary stream and summarise it."""
from fractions import Fraction

from . import headers


class StreamError(ValueError):
    pass


# Start codes (ISO/IEC 13818-2 table 6-1 / 11172-2).
SEQUENCE_HEADER = 0xB3
EXTENSION_START = 0xB5
GOP_HEADER = 0xB8
PICTURE_START = 0x00
SEQUENCE_END = 0xB7

# extension_start_code_identifier values.
SEQ_EXT_ID = 1
DISPLAY_EXT_ID = 2
PIC_CODING_EXT_ID = 8


def units(data):
    """Yield ``(start_code_value, payload)`` for every start code."""
    pos = data.find(b"\x00\x00\x01")
    while 0 <= pos < len(data) - 3:
        nxt = data.find(b"\x00\x00\x01", pos + 3)
        end = len(data) if nxt < 0 else nxt
        yield data[pos + 3], data[pos + 4:end]
        pos = nxt


def _ratio(value):
    """Render a Fraction as lowest-term ``w:h``."""
    return "%d:%d" % (value.numerator, value.denominator)


def _coded_size(seq, seq_ext):
    width = seq["width"] | (seq_ext.get("horizontal_size_extension", 0) << 12)
    height = seq["height"] | (seq_ext.get("vertical_size_extension", 0) << 12)
    return width, height


def _frame_rate(seq, seq_ext):
    """Table 6-3 rate, optionally multiplied by (n+1)/(d+1)."""
    base = headers.FRAME_RATES.get(seq["frame_rate_code"])
    if base is None:
        raise StreamError("reserved frame rate code")
    n = seq_ext.get("frame_rate_extension_n", 0)
    d = seq_ext.get("frame_rate_extension_d", 0)
    return base * Fraction(n + 1, d + 1)


def _bit_rate(seq, seq_ext):
    """bit_rate in bit/s; 0x3FFFF means variable bit rate (null).

    13818-2 6.3.3: bit_rate_extension is the high part of the 30-bit
    bit_rate integer, bit_rate_value the low 18 bits.
    """
    value = seq["bit_rate_value"]
    if value == 0x3FFFF:
        return None
    extended = (seq_ext.get("bit_rate_extension", 0) << 18) | value
    return extended * 400


def _vbv_size(seq, seq_ext):
    """vbv_buffer_size in bits (16 kbit units)."""
    value = (seq_ext.get("vbv_buffer_size_extension", 0) << 10) | seq["vbv_value"]
    return value * 16 * 1024


def _aspects(standard, seq, display_width, display_height):
    """Return (display aspect ratio, sample aspect ratio) as Fractions."""
    code = seq["aspect_code"]
    width, height = seq["width"], seq["height"]
    if standard == "MPEG-1":
        # 11172-2: the code describes the sample aspect ratio directly.
        sar = headers.SAMPLE_ASPECT_RATIOS_MPEG1.get(code)
        if sar is None:
            return None, None
        dar = sar * Fraction(width, height)
        return dar, sar
    # 13818-2: the code describes the display aspect ratio.
    dar = headers.DISPLAY_ASPECT_RATIOS.get(code)
    if dar is None:
        return None, None
    sar = dar * Fraction(display_height, display_width)
    return dar, sar


def summary(data):
    seq = None
    seq_ext = {}
    display_ext = {}
    saw_extension = False
    pictures = {"I": 0, "P": 0, "B": 0, "D": 0}
    gops = 0
    field_pictures = 0
    # Display time accumulated in field periods (half a frame period).
    duration_field_periods = 0
    pending_pce = None
    picture_open = False

    def finalize_picture(pce):
        """Account for one completed picture (13818-2 6.1.1.10 timing)."""
        nonlocal field_pictures, duration_field_periods
        if pce is None:
            # MPEG-1 picture, or MPEG-2 frame picture without an extension:
            # always one frame period.
            duration_field_periods += 2
            return
        structure = pce["picture_structure"]
        if structure in (1, 2):       # field picture
            field_pictures += 1
            duration_field_periods += 1 + pce["repeat_first_field"]
        else:                        # frame picture
            if pce["repeat_first_field"]:
                duration_field_periods += 3
            else:
                duration_field_periods += 2

    for code, payload in units(data):
        if code == SEQUENCE_HEADER:
            if seq is None:
                seq = headers.sequence_header(payload)
        elif code == EXTENSION_START:
            if not payload:
                continue
            saw_extension = True
            ext_id = payload[0] >> 4
            if ext_id == SEQ_EXT_ID and not seq_ext:
                seq_ext = headers.sequence_extension(payload)
            elif ext_id == DISPLAY_EXT_ID and not display_ext:
                display_ext = headers.sequence_display_extension(payload)
            elif ext_id == PIC_CODING_EXT_ID:
                pending_pce = headers.picture_coding_extension(payload)
        elif code == GOP_HEADER:
            gops += 1
        elif code == PICTURE_START:
            if picture_open:
                finalize_picture(pending_pce)
            pending_pce = None
            picture_open = True
            pic = headers.picture_header(payload)
            pictures[pic["type"]] = pictures.get(pic["type"], 0) + 1
        elif code == SEQUENCE_END:
            pass
    if picture_open:
        finalize_picture(pending_pce)

    if seq is None:
        raise StreamError("no sequence header")

    standard = "MPEG-2" if saw_extension else "MPEG-1"
    width, height = _coded_size(seq, seq_ext)
    display_width = display_ext.get("display_horizontal_size", width)
    display_height = display_ext.get("display_vertical_size", height)
    dar, sar = _aspects(standard, seq, display_width, display_height)

    rate = _frame_rate(seq, seq_ext)
    frame_count = field_pictures // 2 + (
        sum(pictures.values()) - field_pictures
    )
    duration = Fraction(duration_field_periods, 2) / rate

    pli = seq_ext.get("profile_and_level_indication")
    if pli is not None:
        # 13818-2 8.1.1: high nibble profile, low nibble level; the 4-bit
        # profile field includes the 4:2:2 profile (value 10), so no mask.
        profile = headers.PROFILES.get(pli >> 4)
        level = headers.LEVELS.get(pli & 15)
    else:
        profile = level = None
    if standard == "MPEG-1":
        chroma = "4:2:0"
        progressive = True
    else:
        chroma = headers.CHROMA_FORMATS.get(seq_ext.get("chroma_format"))
        progressive = bool(seq_ext.get("progressive_sequence", 0))

    return {
        "standard": standard,
        "width": width,
        "height": height,
        "display_width": display_width,
        "display_height": display_height,
        "dar": _ratio(dar) if dar is not None else None,
        "sar": _ratio(sar) if sar is not None else None,
        "frame_rate": "%d/%d" % (rate.numerator, rate.denominator),
        "bit_rate": _bit_rate(seq, seq_ext),
        "vbv_buffer_size": _vbv_size(seq, seq_ext),
        "profile": profile,
        "level": level,
        "chroma_format": chroma,
        "progressive_sequence": progressive,
        "pictures": pictures,
        "frames": frame_count,
        "gops": gops,
        "duration": str(duration),
    }
