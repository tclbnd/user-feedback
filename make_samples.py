"""Generate deterministic local sample input for decode.py (>=30 documents).

Covers: conflicting HTTP vs <meta> charset, BOM precedence, UTF-16 LE/BE and
UTF-32 endianness, codec aliases, truncated multibyte tails, unknown labels,
duplicate canonical codecs and the final recovery path.
"""
import json

# Probe intentionally has no single-byte cp1252/latin codec, so cp1252-only
# bytes can only be rescued from the fallback tier.  Neither fallback set
# contains a "total" codec (such as iso-8859-1), otherwise the recovery branch
# could never be reached.
PROBE = ["utf-8", "x-probe-mystery", "gb18030", "shift_jis", "big5", "euc-jp"]
FALLBACK_A = ["unknown-99", "windows-1252", "utf-16-le", "utf-8"]
FALLBACK_B = ["x-no-such-codec", "ascii", "utf-16-le", "utf-32-be"]

# Real non-ASCII payloads reused across encodings.
CN = "<html><head><title>迁移站点测试</title></head><body>采集入仓乱码</body></html>"
JP = "<html><body>文字化けテスト：エンコーディング</body></html>"
RU = "<html><body>Проверка кодировки сайта</body></html>"
EURO = "<html><body>café résumé naïve – €100 „Straße“</body></html>"
# Strict ISO-8859-1 has no en-dash/euro/„ quotes: keep this payload latin-1 safe.
LATIN = "<html><body>café résumé naïve © Straße · £100</body></html>"
BIG5_TXT = "檔案編碼測試：繁體中文與空白"


def H(raw):
    return raw.hex()


def doc(did, raw, http=None, meta=None):
    return {
        "id": did,
        "body_hex": H(raw),
        "http_charset": http,
        "meta_charset": meta,
    }


documents = []

# --- Conflicting hints: HTTP is stale/wrong, <meta> is authoritative -------
documents.append(doc("d01", CN.encode("gb18030"), http="utf-8", meta="gb18030"))
documents.append(doc("d02", JP.encode("shift_jis"), http="windows-1252", meta="shift_jis"))
documents.append(doc("d03", EURO.encode("cp1252"), http="utf-8", meta="windows-1252"))
documents.append(doc("d04", RU.encode("koi8-r"), http="iso-8859-1", meta="koi8-r"))
documents.append(doc("d05", BIG5_TXT.encode("big5"), http="utf-8", meta="big5"))
documents.append(doc("d06", CN.encode("gbk"), http="latin-1", meta="gbk"))
documents.append(doc("d07", JP.encode("euc_jp"), http="utf-8", meta="euc-jp"))

# --- HTTP is authoritative: <meta> carries a wrong label -------------------
documents.append(doc("d08", CN.encode("gb18030"), http="gb18030", meta="utf-8"))
documents.append(doc("d09", EURO.encode("cp1252"), http="cp1252", meta="iso-8859-1"))
documents.append(doc("d10", RU.encode("koi8-r"), http="koi8-r", meta="utf-8"))

# --- Aliases: raw labels differ but canonicalize to the same codec --------
documents.append(doc("d11", CN.encode("gb18030"), http="GB18030", meta="gbk"))
documents.append(doc("d12", LATIN.encode("latin-1"), http="LATIN1", meta="iso-8859-1"))
documents.append(doc("d13", JP.encode("shift_jis"), http="sjis", meta="shift_jis"))
documents.append(doc("d14", EURO.encode("cp1252"), http="windows-1252", meta="cp1252"))
documents.append(doc("d15", b"plain ascii text only\n", http="US-ASCII", meta="ascii"))

# --- Unknown labels are skipped and logged --------------------------------
documents.append(doc("d16", CN.encode("gb18030"), http="x-unknown-charset", meta="weird-9999"))
documents.append(doc("d17", EURO.encode("cp1252"), http="totally-made-up", meta="windows-1252"))
documents.append(doc("d18", JP.encode("shift_jis"), http=None, meta="bogus-label"))

# --- Null / empty hints: must fall through to probe/fallback ---------------
documents.append(doc("d19", CN.encode("gb18030"), http=None, meta=None))
documents.append(doc("d20", EURO.encode("cp1252"), http="", meta=""))
documents.append(doc("d21", "こんにちは世界".encode("utf-8"), http=None, meta=""))

# --- BOM precedence over conflicting text labels --------------------------
documents.append(doc("d22", b"\xef\xbb\xbf" + CN.encode("utf-8"), http="gbk", meta="big5"))
documents.append(doc("d23", b"\xff\xfe" + JP.encode("utf-16-le"), http="utf-8", meta="gb18030"))
documents.append(doc("d24", b"\xfe\xff" + JP.encode("utf-16-be"), http="iso-8859-1", meta="x-bad"))
documents.append(doc("d25", b"\xff\xfe\x00\x00" + CN.encode("utf-32-le"), http="utf-8", meta=None))
documents.append(doc("d26", b"\x00\x00\xfe\xff" + CN.encode("utf-32-be"), http=None, meta="utf-8"))
# UTF-16 BOM with an odd-length truncated tail: BOM codec must fail strict.
documents.append(doc("d27", b"\xff\xfe\x41\x00\x42", http="utf-8", meta="utf-16-le"))
# UTF-8 BOM but bytes are actually invalid UTF-8: BOM attempt invalid, then meta wins.
documents.append(doc("d28", b"\xef\xbb\xbf" + CN.encode("gb18030"), http=None, meta="gb18030"))

# --- Truncated multibyte tails (no BOM) ------------------------------------
documents.append(doc("d29", "abcé".encode("utf-8")[:-1], http="utf-8", meta=None))
documents.append(doc("d30", "x€y".encode("utf-8")[:-1], http="utf-8", meta=None))
documents.append(doc("d31", "z😀".encode("utf-8")[:-2], http=None, meta=None))
documents.append(doc("d32", CN.encode("gb18030")[:-1], http="gb18030", meta="utf-8"))

# --- Raw invalid / lone continuation bytes --------------------------------
documents.append(doc("d33", b"head\xff\xfe\x80tail", http="utf-8", meta="windows-1252"))
documents.append(doc("d34", b"\x80\x81\x82 start \xff end", http="utf-8", meta=None))

# --- Duplicate canonical codec across the ordered candidate chain ----------
# utf-8 appears in HTTP, probe and fallback A; only one utf-8 attempt logged.
documents.append(doc("d35", "utf8 文本 ok".encode("utf-8"), http="UTF8", meta="x-meta-missing"))
# iso-8859-1 in meta (alias) and fallback A (canonical): one attempt only.
documents.append(doc("d36", LATIN.encode("latin-1"), http="x-bogus", meta="LATIN1"))

# --- Fallback-only success --------------------------------------------------
# Verified empirically: 0x82 0x40 0x80 0x93 0x94 is rejected by utf-8,
# gb18030 and shift_jis (so the whole probe list fails) and by utf-16/utf-32,
# but is legal cp1252; the odd length also blocks utf-16/utf-32.  Under
# fallback A cp1252 is in the fallback list -> source "fallback"; under
# fallback B (no cp1252) the identical bytes reach recovery instead.
documents.append(doc(
    "d37", b"\x82@\x80\x93\x94 cp1252 only \x96 dash",
    http=None, meta=None))

# --- Guaranteed recovery: lone continuation bytes rejected by every codec --
documents.append(doc("d38", b"\x80\x81\x82 start \xff end", http="utf-8", meta=None))
# Multiple invalid spans separated by valid text: fragments survive and each
# bad byte becomes one U+FFFD.  The trailing 0x81 is undefined even in cp1252
# and the length is odd, so every codec in either fallback set rejects this.
documents.append(doc("d39", b"ab\xe2\x82cd\xf0\x9fgh\x81", http=None, meta=None))
# Overlong and surrogate encodings are invalid UTF-8 and, with the leading
# 0x80, rejected by the multibyte probe codecs as well.
documents.append(doc(
    "d40", b"\x80 \xed\xa0\x80 surrogate \xc0\x80 overlong", http=None, meta=None))
# Odd-length UTF-16 payload without a BOM: utf-16 attempts invalid -> recovery.
documents.append(doc("d41", b"\x41\x00\x42 odd tail\xff", http="utf-16-le", meta="utf-16-be"))
# Empty body: decodes trivially via the first known codec with no bad bytes.
documents.append(doc("d42", b"", http="utf-8", meta=None))
# Probe list has no codec that accepts these cp1252-only bytes, but fallback A
# contains cp1252 -> source "fallback" under A; fallback B lacks it -> recovery.
# 0x82 0x40 ... rejects gb18030/shift_jis; odd length rejects utf-16/utf-32;
# cp1252 accepts the whole body (verified via byte_probe.py).
documents.append(doc(
    "d43", b"\x82@\x80\x93\x94 cp1252 fallback only", http=None, meta=None))


def build(fallback):
    return {
        "probe": PROBE,
        "fallback": fallback,
        "documents": documents,
    }


if __name__ == "__main__":
    with open("input_a.json", "w", encoding="utf-8") as f:
        json.dump(build(FALLBACK_A), f, ensure_ascii=False, indent=2)
    with open("input_b.json", "w", encoding="utf-8") as f:
        json.dump(build(FALLBACK_B), f, ensure_ascii=False, indent=2)
    print("documents:", len(documents))
