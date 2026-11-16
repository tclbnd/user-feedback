"""Empirically map which codecs accept/reject candidate byte patterns."""

CODECS = ["utf-8", "gb18030", "shift_jis", "cp1252", "ascii", "utf-16-le",
          "utf-16-be", "utf-32-le"]

PATTERNS = {
    "lone_continuation_run": b"head\xff\xfe\x80tail",
    "lone_80_81_82": b"\x80\x81\x82 start \xff end",
    "trunc2_c3": b"abc\xc3",
    "trunc3_e2_82": b"x\xe2\x82y",
    "trunc4_f0_9f": b"z\xf0\x9f",
    "cp1252_euro_smart": b"cost \x80 100 \x93quote\x94 \x96 dash",
    "cp1252_0x81": b"a\x81b",
    "utf16le_odd": b"\x41\x00\x42",
    "utf16be_odd": b"\x00\x41\x42",
    "ff_fe_80_mix": b"\xff\xfe\x80 mid \xc0\x80",
    "overlong_c080": b"\xc0\x80 bad",
    "surrogate_eda080": b"\xed\xa0\x80 bad",
    "trunc_then_valid": b"ab\xe2\x82cd\xf0\x9fgh",
    "fb_candidate_1": b"\x81\x80\x93\x94\x96",
    "fb_candidate_2": b"\x81\x82\x80\x93",
    "fb_candidate_3": b"\x80\x93\x94\x96\x85\x97",
    "fb_candidate_4": b"\x81\x40\x80\x93\x94",
    "fb_candidate_5": b"\x82\x40\x80\x93\x94",
    "fb_candidate_6": b"\x82\x81\x80\x93\x94\x96",
    "fb_candidate_7": b"\xff\x80\x93\x94\x96",
    "recover_d39_a": b"ab\xe2\x82cd\xf0\x9fgh\x80",
    "recover_d39_b": b"ab\xe2\x82cd\xf0\x9fgh\x81",
    "recover_d39_c": b"ab\xe2\x82cd\xf0\x9fgh\xff",
}

for name, raw in PATTERNS.items():
    row = []
    for codec in CODECS:
        try:
            raw.decode(codec, "strict")
            row.append("%s=OK" % codec)
        except UnicodeDecodeError:
            row.append("%s=BAD" % codec)
    print("%-22s %s" % (name, "  ".join(row)))
