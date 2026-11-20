"""Recording summary: metadata, video frames and timing.

Tag bodies are interpreted per Adobe Video File Format Specification
v10.1 (classic FLV audio/video tag headers, AVC packet types) and the
Enhanced RTMP specification (FourCC-based video and audio, enhanced
packet types).
"""
from . import amf
from .tags import TAG_AUDIO, TAG_SCRIPT, TAG_VIDEO, tags

# Classic FLV video codec IDs (VideoFileData, CodecID).
VIDEO_CODEC_ID = {2: "h263", 4: "vp6", 7: "avc", 12: "hevc"}

# Enhanced RTMP FourCC video codecs (Enhanced RTMP, ExVideoTag).
VIDEO_FOURCC = {b"av01": "av1", b"hvc1": "hevc", b"vp09": "vp9"}

# Classic FLV audio SoundFormat IDs (AudioTagBody, SoundFormat).
AUDIO_SOUND_FORMAT = {2: "mp3", 10: "aac", 11: "speex", 13: "mp3-8k", 14: "mp3-8k16"}

# Enhanced RTMP FourCC audio codecs (Enhanced RTMP, ExAudioTag).
AUDIO_FOURCC = {b"mp4a": "aac", b"fLaC": "flac", b"Opus": "opus"}

# AVC / HEVC video packet types (AVCVIDEOPACKET / HEVCVIDEOPACKET).
AVC_SEQUENCE_HEADER = 0
AVC_NALU = 1
AVC_END_OF_SEQUENCE = 2

# Enhanced RTMP video packet types (ExVideoTagBody).
EX_SEQUENCE_START = 0
EX_CODED_FRAMES = 1
EX_SEQUENCE_END = 2
EX_CODED_FRAMES_X = 3

# Enhanced RTMP audio packet types (ExAudioTagBody).
EX_AUDIO_SEQUENCE_START = 0
EX_AUDIO_CODED_FRAMES = 1
EX_AUDIO_SEQUENCE_END = 2
EX_AUDIO_MULTITRACK = 4

# AAC packet types (AACVIDEOPACKET's audio counterpart, AACAUDIODATA).
AAC_SEQUENCE_HEADER = 0
AAC_RAW = 1

# Classic video frame types (FrameType nibble).
FRAME_KEY = 1
FRAME_INTER = 2
FRAME_INFO = 5  # video info / command frame, not coded video

# Enhanced RTMP markers.
ENHANCED_VIDEO_MARKER = 0x0F
ENHANCED_AUDIO_MARKER = 0x90

# ExFrameType values in the 3-bit field of an ExVideoTag header.
EX_FRAME_KEY = 1
EX_FRAME_INTER = 0


def _s24(value):
    """Interpret a 24-bit unsigned value as a signed integer."""
    return value - (1 << 24) if value & 0x800000 else value


def _parse_video(body):
    """Return (codec, frame_type, packet_type, cts_ms) for a video tag.

    Handles both classic FLV video tags (E.4.3) and Enhanced RTMP
    ExVideoTag headers.  Returns None for an empty body.
    """
    if not body:
        return None
    first = body[0]
    if (first & 0x0F) == ENHANCED_VIDEO_MARKER:
        # ExVideoTag: IsEx(1) | ExFrameType(3) | 0xF(4), FourCC,
        # ExVideoPacketType, then the 24-bit composition timestamp.
        if len(body) < 9:
            return None
        # ExFrameType 0 means inter frames, 1 a keyframe; normalise to the
        # classic FLV FrameType values (2 / 1).
        ex_frame_type = (first >> 4) & 0x07
        frame_type = FRAME_KEY if ex_frame_type == EX_FRAME_KEY else FRAME_INTER
        fourcc = bytes(body[1:5])
        codec = VIDEO_FOURCC.get(fourcc, fourcc.decode("latin-1", "replace"))
        packet_type = body[5]
        cts = _s24(int.from_bytes(body[6:9], "big"))
        return codec, frame_type, packet_type, cts

    frame_type = first >> 4
    codec_id = first & 0x0F
    codec = VIDEO_CODEC_ID.get(codec_id, str(codec_id))
    if codec_id in (7, 12):
        if len(body) < 5:
            return None
        # AVCVIDEOPACKET / HEVCVIDEOPACKET: packet type then 24-bit CTS.
        packet_type = body[1]
        cts = _s24(int.from_bytes(body[2:5], "big"))
    else:
        packet_type = AVC_NALU
        cts = 0
    return codec, frame_type, packet_type, cts


def _parse_audio(body):
    """Return (codec, is_sequence_header) for an audio tag.

    Handles the classic AudioTagBody (E.4.2) and Enhanced RTMP ExAudioTag.
    Returns None for an empty body.
    """
    if not body:
        return None
    first = body[0]
    if first == ENHANCED_AUDIO_MARKER:
        # ExAudioTag: 0x90 marker, FourCC, ExAudioPacketType...
        if len(body) < 6:
            return None
        fourcc = bytes(body[1:5])
        codec = AUDIO_FOURCC.get(fourcc, fourcc.decode("latin-1", "replace"))
        packet_type = body[5]
        is_sequence = packet_type in (EX_AUDIO_SEQUENCE_START, EX_AUDIO_MULTITRACK)
        return codec, is_sequence

    sound_format = first >> 4
    codec = AUDIO_SOUND_FORMAT.get(sound_format, str(sound_format))
    if sound_format == 10:
        # AACAUDIODATA: AACPacketType, 0 = AudioSpecificConfig.
        is_sequence = len(body) > 1 and body[1] == AAC_SEQUENCE_HEADER
    else:
        is_sequence = False
    return codec, is_sequence


def summarise(data):
    info, tag_list = tags(data)
    meta = {}
    frames = 0
    keyframes = 0
    first_pts = []
    audio_codec = None
    audio_tags = 0
    video_codec = None

    for t in tag_list:
        if t.type == TAG_SCRIPT:
            if not t.body:
                continue
            name, p = amf.value(t.body, 0)
            if name == "onMetaData":
                meta, _ = amf.value(t.body, p)
        elif t.type == TAG_VIDEO:
            parsed = _parse_video(t.body)
            if parsed is None:
                continue
            codec, frame_type, packet_type, cts = parsed
            video_codec = codec
            # Video info/command frames (FrameType 5) carry no coded image.
            if frame_type == FRAME_INFO:
                continue
            # AVC/HEVC only expose coded frames in NALU packets: sequence
            # headers and end-of-sequence markers are bookkeeping.
            if codec in ("avc", "hevc") and packet_type not in (
                AVC_NALU, EX_CODED_FRAMES, EX_CODED_FRAMES_X,
            ):
                continue
            frames += 1
            if frame_type == FRAME_KEY:
                keyframes += 1
            # Presentation time is the tag decode time plus the signed
            # 24-bit composition time offset.
            first_pts.append(t.timestamp + cts)
        elif t.type == TAG_AUDIO:
            parsed = _parse_audio(t.body)
            if parsed is None:
                continue
            codec, is_sequence = parsed
            audio_codec = codec
            # The AAC sequence header (AudioSpecificConfig) is not an
            # audio frame and must not be counted.
            if is_sequence:
                continue
            audio_tags += 1

    info.update(
        metadata=meta,
        video_codec=video_codec,
        video_frames=frames,
        keyframes=keyframes,
        first_pts=first_pts[:4],
        max_timestamp=max((t.timestamp for t in tag_list), default=0),
        audio_codec=audio_codec,
        audio_tags=audio_tags,
    )
    return info

