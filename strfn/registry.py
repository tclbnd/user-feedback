"""Name -> implementation, as the SQL layer calls them."""
from . import basic, quoting, regex

FUNCTIONS = {
    "concat": basic.concat, "concat_ws": basic.concat_ws, "||": basic.cat, "length": basic.length,
    "octet_length": basic.octet_length, "upper": basic.upper, "lower": basic.lower, "initcap": basic.initcap,
    "reverse": basic.reverse, "repeat": basic.repeat, "left": basic.left, "right": basic.right,
    "substr": basic.substr, "strpos": basic.strpos, "split_part": basic.split_part, "lpad": basic.lpad,
    "rpad": basic.rpad, "btrim": basic.btrim, "ltrim": basic.ltrim, "rtrim": basic.rtrim,
    "replace": basic.replace, "translate": basic.translate, "overlay": basic.overlay, "chr": basic.chr_,
    "ascii": basic.ascii_, "starts_with": basic.starts_with, "to_hex": basic.to_hex,
    "quote_ident": quoting.quote_ident, "quote_literal": quoting.quote_literal,
    "quote_nullable": quoting.quote_nullable, "format": quoting.format_, "regexp_replace": regex.regexp_replace,
}
