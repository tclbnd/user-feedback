"""LOAS / LATM (ISO/IEC 14496-3 subpart 1) with AAC payloads."""
from fractions import Fraction

from .bits import Reader

RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)
# channelConfiguration 1..7 -> output channel count (7 is 7.1, i.e. 8 channels)
CHANNELS = (0, 1, 2, 3, 4, 5, 6, 8)
SYNCWORD = 0x2B7            # AudioSyncStream() syncword, 11 bits
SYNC_EXTENSION = 0x2B7      # syncExtensionType that announces SBR signalling
PS_EXTENSION = 0x548        # syncExtensionType that carries psPresentFlag


class LatmError(ValueError):
    pass


def _rate(r):
    """samplingFrequency: 4-bit index, 0xF escapes to an explicit 24-bit rate."""
    idx = r.u(4)
    if idx == 0xF:
        return r.u(24)
    if idx >= len(RATES):
        raise LatmError("reserved sampling frequency index")
    return RATES[idx]


def _aot(r):
    """audioObjectType: 5 bits, value 31 escapes to 32 + 6 bits."""
    a = r.u(5)
    return 32 + r.u(6) if a == 31 else a


def _ga_specific_config(r, aot, chconf):
    """GASpecificConfig(); returns the frame length in samples."""
    frame_len_flag = r.u(1)
    if r.u(1):                          # dependsOnCoreCoder
        r.u(14)                         # coreCoderDelay
    ext_flag = r.u(1)
    if not chconf:
        raise LatmError("program_config_element not supported")
    if aot in (6, 20):
        r.u(3)                          # layerNr
    if ext_flag:
        if aot == 22:
            r.u(5)                      # numOfSubFrame
            r.u(11)                     # layer_length
        if aot in (17, 19, 20, 23):
            r.u(3)                      # resilience flags
        r.u(1)                          # extensionFlag3
    return 960 if frame_len_flag else 1024


def _sync_extension(r, state):
    """Optional syncExtensionType tail of an AudioSpecificConfig.

    Implicit SBR/PS signalling: the extension can only start right where the
    AudioSpecificConfig proper ends, so a mismatch is not retried further into
    the bit stream and the reader is put back where it was.
    """
    if r.bits_left() < 16:
        return
    mark = r.pos
    if r.u(11) != SYNC_EXTENSION:
        r.pos = mark
        return
    try:
        ext_aot = r.u(5)                # extensionAudioObjectType
        if ext_aot == 5:
            if r.u(1):                  # sbrPresentFlag
                state["ext_sample_rate"] = _rate(r)
                ps_mark = r.pos
                if r.bits_left() >= 12 and r.u(11) == PS_EXTENSION:
                    state["ps"] = bool(r.u(1))      # psPresentFlag
                else:
                    r.pos = ps_mark
                return
        elif ext_aot == 22:
            if r.u(1):                  # sbrPresentFlag
                r.u(4)                  # extensionChannelConfiguration
                return
            return
    except EOFError:
        pass
    r.pos = mark                       # not a usable extension after all


def audio_specific_config(r, end=None):
    """AudioSpecificConfig() of ISO/IEC 14496-3, 1.6.2.1.

    ``end`` bounds the config to the bits announced by ascLen, so trailing
    padding is not mistaken for config syntax.
    """
    if end is not None:
        r = Reader(r.data, r.pos, end)
    state = {"ps": False, "ext_sample_rate": None}
    aot = _aot(r)
    state["sample_rate"] = _rate(r)     # samplingFrequency = core coder rate
    chconf = r.u(4)
    explicit_sbr = aot in (5, 29)
    if explicit_sbr:
        # Hierarchical SBR signalling: audioObjectType 5/29 means the SBR (or
        # PS) tool wraps another audioObjectType, extensionSamplingFrequency is
        # the SBR output rate and the following audioObjectType describes the
        # core coder.  Report the core object type and keep both rates.
        state["ps"] = aot == 29
        state["ext_sample_rate"] = _rate(r)
        aot = _aot(r)
        if aot == 22:
            r.u(4)                      # extensionChannelConfiguration
    if aot not in (1, 2, 3, 4, 6, 7, 17, 19, 20, 21, 22, 23):
        raise LatmError("unsupported audio object type %d" % aot)
    state["object_type"] = aot
    state["frame_length"] = _ga_specific_config(r, aot, chconf)
    state["channels"] = CHANNELS[chconf] if chconf < len(CHANNELS) else None
    if not explicit_sbr:
        _sync_extension(r, state)
    return state


def latm_value(r):
    """latmGetValue(): 2-bit field holding the byte count minus one, then value."""
    n = r.u(2) + 1
    v = 0
    for _ in range(n):
        v = (v << 8) | r.u(8)
    return v


def stream_mux_config(r, skip_other_payload=True):
    """StreamMuxConfig() of ISO/IEC 14496-3, 1.7.3.

    Leaves the reader at the first payloadLengthInfo, i.e. past the optional
    otherData and crcCheckSum that follow the configuration.  Some encoders
    write otherDataLenBits without the otherData bits it announces, so the
    caller may ask for the length field alone.
    """
    version = r.u(1)                    # audioMuxVersion
    version_a = r.u(1) if version == 1 else 0
    if version_a:
        raise LatmError("audioMuxVersionA 1 not supported")
    if version == 1:
        latm_value(r)                   # taraBufferFullness
    same_framing = r.u(1)               # allStreamsSameTimeFraming
    subframes = r.u(6) + 1              # numSubFrames
    programs = r.u(4) + 1               # numProgram
    layers = r.u(3) + 1                 # numLayer
    if programs != 1 or layers != 1:
        raise LatmError("multiple programs or layers not supported")
    if version == 0:
        asc = audio_specific_config(r)
    else:
        # audioMuxVersion 1 wraps the AudioSpecificConfig in an explicit bit
        # count so it can be bypassed; skip whatever padding that span holds.
        asc_len = latm_value(r)
        start = r.pos
        asc = audio_specific_config(r, end=start + asc_len)
        r.pos = start + asc_len
    frame_type = r.u(3)                 # frameLengthType
    if frame_type != 0:
        raise LatmError("frameLengthType %d not supported" % frame_type)
    r.u(8)                              # latmBufferFullness
    other_bits = None
    if r.u(1):                          # otherDataPresent
        if version == 1:
            other_bits = latm_value(r)
        else:
            other_bits = 0
            while True:                 # otherDataLenEsc extends the count
                esc = r.u(1)
                other_bits = (other_bits << 8) | r.u(8)
                if not esc:
                    break
        if skip_other_payload:
            r.skip(other_bits)          # otherData
    crc = bool(r.u(1))                  # crcCheckPresent
    if crc:
        r.u(8)                          # crcCheckSum
    return {"version": version, "crc": crc, "same_framing": same_framing,
            "subframes": subframes, "layers": layers, "programs": programs,
            "other_bits": other_bits, "skip_other": skip_other_payload, **asc}


def payload_length(r):
    """PayloadLengthInfo() byte count: 255s accumulate until a shorter byte."""
    total = 0
    while True:
        part = r.u(8)
        total += part
        if part != 255:
            return total


def _read_payloads(r, cfg):
    """Consume one frame's payloads; rejects bodies that are not exact."""
    lens = []
    for _ in range(cfg["subframes"] * cfg["layers"]):
        n = payload_length(r)
        r.skip(8 * n)
        lens.append(n)
    if r.bits_left() >= 8:
        # A frame body is only zero-padded up to the next byte, so spare whole
        # bytes mean a field was misread and this is not a real frame.
        raise LatmError("frame body not fully consumed")
    return lens


def _next_sync(data, start):
    """Offset of the next AudioSyncStream() syncword at or after ``start``."""
    last = len(data) - 3
    pos = start
    while pos <= last:
        if ((data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]) >> 13 == SYNCWORD:
            return pos
        pos += 1
    return -1


def scan(data):
    """Summarise a LOAS/LATM byte stream."""
    pos, frames, aus, payload, configs = 0, 0, 0, 0, 0
    cfg = None
    while True:
        # Recordings may open with arbitrary bytes before the first syncword,
        # and a corrupt frame can interrupt the stream: always resynchronise.
        pos = _next_sync(data, pos)
        if pos < 0:
            break
        head = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
        end = pos + 3 + (head & 0x1FFF)
        if end > len(data):
            pos += 1                    # syncword-like bytes, or truncated tail
            continue
        body = data[pos + 3:end]
        new_cfg = cfg
        lens = None
        try:
            r = Reader(body)
            if not r.u(1):              # useSameStreamMux
                new_cfg = stream_mux_config(r)
            if new_cfg is None:
                pos += 1                # no config yet: treat as junk and resync
                continue
            lens = _read_payloads(r, new_cfg)
        except (EOFError, LatmError):
            # Some encoders omit the otherData payload while still writing its
            # length field, which only shows up as a misaligned frame body.
            if new_cfg is not None and new_cfg.get("other_bits") and new_cfg["skip_other"]:
                try:
                    r = Reader(body)
                    if not r.u(1):
                        new_cfg = stream_mux_config(r, skip_other_payload=False)
                    lens = _read_payloads(r, new_cfg)
                except (EOFError, LatmError):
                    lens = None
            if lens is None:
                pos += 1                # false sync or corrupt frame: resync
                continue
        if cfg is not new_cfg:
            cfg = new_cfg
            configs += 1
        frames += 1                     # counted from the first config-carrying frame
        aus += len(lens)
        payload += sum(lens)
        pos = end
    if cfg is None:
        raise LatmError("no StreamMuxConfig found")
    return {
        "mux_version": cfg["version"], "object_type": cfg["object_type"], "sample_rate": cfg["sample_rate"],
        "ext_sample_rate": cfg["ext_sample_rate"], "ps": cfg["ps"], "channels": cfg["channels"],
        "frame_length": cfg["frame_length"],
        "loas_frames": frames, "access_units": aus, "configs": configs, "payload_bytes": payload,
        "duration": str(Fraction(aus * cfg["frame_length"], cfg["sample_rate"])),
    }
