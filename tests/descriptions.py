"""Small SDP texts for the tests."""


def one_stream(rtpmap="L24/48000/2", ptime="1"):
    return "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0", "m=audio 5004 RTP/AVP 97",
                      "c=IN IP4 10.0.0.2", "a=rtpmap:97 " + rtpmap, "a=ptime:" + ptime]) + "\n"
