"""Name -> implementation, as the SQL layer calls them."""
from . import core

FUNCTIONS = {
    "+": core.add, "-": core.sub, "*": core.mul, "/": core.divide, "%": core.modulo,
    "abs": core.abs_, "sign": core.sign, "round": core.round_, "trunc": core.trunc, "ceil": core.ceil,
    "floor": core.floor, "div": core.div, "mod": core.modulo, "sqrt": core.sqrt, "ln": core.ln, "log": core.log,
    "gcd": core.gcd, "lcm": core.lcm, "factorial": core.factorial, "width_bucket": core.width_bucket,
    "scale": core.scale, "min_scale": core.min_scale, "trim_scale": core.trim_scale,
}
