"""Recording summary: metadata, video frames and timing."""
from . import amf
from .tags import tags

VIDEO = {2: "h263", 4: "vp6", 7: "avc", 12: "hevc"}
AUDIO = {2: "mp3", 10: "aac", 11: "speex"}


def summarise(data):
    info, tag_list = tags(data)
    meta, frames, keys, pts, audio_codec, audio_tags = {}, 0, 0, [], None, 0
    for t in tag_list:
        if t.type == 18:
            name, p = amf.value(t.body, 0)
            if name == "onMetaData":
                meta, _ = amf.value(t.body, p)
        elif t.type == 9 and t.body:
            frame_type, codec = t.body[0] >> 4, t.body[0] & 0xF
            info["video_codec"] = VIDEO.get(codec, str(codec))
            frames += 1
            keys += frame_type == 1
            cts = int.from_bytes(t.body[2:5], "big") if codec == 7 else 0
            pts.append(t.timestamp + cts)
        elif t.type == 8 and t.body:
            audio_codec = AUDIO.get(t.body[0] >> 4, str(t.body[0] >> 4))
            audio_tags += 1
    info.update(metadata=meta, video_frames=frames, keyframes=keys, first_pts=pts[:4],
                max_timestamp=max((t.timestamp for t in tag_list), default=0),
                audio_codec=audio_codec, audio_tags=audio_tags)
    return info
