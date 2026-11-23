"""Small SDP texts for the tests."""


def one_stream(rtpmap="L24/48000/2", ptime="1", connection="IN IP4 10.0.0.2"):
    return "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0", "m=audio 5004 RTP/AVP 97",
                      "c=" + connection, "a=rtpmap:97 " + rtpmap, "a=ptime:" + ptime]) + "\n"


def session_connection(connection="IN IP4 239.69.21.4/32"):
    return "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "c=" + connection, "t=0 0",
                      "a=sendonly", "a=ts-refclk:ptp=IEEE1588-2008:00-1D-C1-FF-FE-41-2A-07:0",
                      "m=audio 5004 RTP/AVP 98", "a=rtpmap:98 L24/48000/8", "a=ptime:1"]) + "\n"


def two_media():
    return "\n".join(["v=0", "o=- 1 1 IN IP4 10.0.0.1", "s=Test", "t=0 0",
                      "c=IN IP4 239.69.30.17/15", "a=recvonly",
                      "m=audio 5004/2 RTP/AVP 96", "a=rtpmap:96 L24/48000/2", "a=ptime:0.125",
                      "a=mediaclk:direct=1820480",
                      "m=audio 5006 RTP/AVP 97", "c=IN IP4 239.69.30.18", "a=inactive",
                      "a=rtpmap:97 L16/48000", "a=ptime:4",
                      "a=source-filter: incl IN IP4 239.69.30.18 10.0.0.9"]) + "\n"

