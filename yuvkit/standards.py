"""Colour standards (BT.601 / BT.709 / BT.2020) with exact rational constants.

The coefficients are stored as :class:`fractions.Fraction` so the whole
conversion chain is reproducible bit-for-bit and independent of host
floating point behaviour.  Kr/Kb are the luma weights of the primary
components; the chroma gains follow the standard definition

    Cb = 0.5 / (1 - Kb) * (B' - Y')        (and Cr with Kr)

Note the sign: the gain is 0.5/(1-K), *not* 0.5/(1+K).  Mixing the two
is one of the bugs this module exists to make impossible - the gain is
always derived from the same Kr/Kb that produced the luma, so a matrix
can never borrow constants from another generation of the standard.
"""

from fractions import Fraction

__all__ = [
    "Standard",
    "STANDARDS",
    "get_standard",
    "canonical_name",
    "UnknownStandard",
]


class UnknownStandard(ValueError):
    """Raised for a standard name that is not part of the registry."""


# Kr / Kb exactly as they appear in the respective recommendations.
_K_CONSTANTS = {
    "bt601": (Fraction(299, 1000), Fraction(114, 1000)),
    "bt709": (Fraction(2126, 10000), Fraction(722, 10000)),
    "bt2020": (Fraction(2627, 10000), Fraction(593, 10000)),
}

_ALIASES = {
    "601": "bt601",
    "rec601": "bt601",
    "recbt601": "bt601",
    "itu601": "bt601",
    "smpte170m": "bt601",
    "smpte170": "bt601",
    "smpte125m": "bt601",
    "ntsc": "bt601",
    "pal": "bt601",
    "709": "bt709",
    "rec709": "bt709",
    "recbt709": "bt709",
    "itu709": "bt709",
    "smpte274m": "bt709",
    "srgb": "bt709",
    "2020": "bt2020",
    "rec2020": "bt2020",
    "recbt2020": "bt2020",
    "itu2020": "bt2020",
}


def canonical_name(name):
    """Return the canonical registry key for *name* (e.g. ``"bt601"``)."""
    if isinstance(name, Standard):
        return name.name
    if not isinstance(name, str):
        raise UnknownStandard("standard name must be a string, got %r" % (name,))
    key = name.strip().lower()
    for sep in (" ", "-", "_", "."):
        key = key.replace(sep, "")
    key = key.replace("bt.", "bt")
    # "ITU-R BT.709", "ITU-R BT709" and "ITU 709" all collapse onto the
    # same key as their Rec. spelling.
    if key.startswith("itur"):
        key = "rec" + key[4:]
    elif key.startswith("itu") and not key.startswith("itu"):
        key = key[3:]
    key = _ALIASES.get(key, key)
    if key not in _K_CONSTANTS:
        raise UnknownStandard("unknown colour standard %r" % (name,))
    return key


class Standard:
    """One colour standard with exact rational matrix constants."""

    __slots__ = ("name", "kr", "kb", "kg")

    def __init__(self, name):
        key = canonical_name(name)
        self.name = key
        self.kr, self.kb = _K_CONSTANTS[key]
        # Green weight follows from the normalisation Kr + Kg + Kb == 1.
        self.kg = 1 - self.kr - self.kb

    @property
    def cb_gain(self):
        """Forward gain for Cb: 0.5 / (1 - Kb)."""
        return 1 / (2 * (1 - self.kb))

    @property
    def cr_gain(self):
        """Forward gain for Cr: 0.5 / (1 - Kr)."""
        return 1 / (2 * (1 - self.kr))

    @property
    def cb_inverse_gain(self):
        """Factor applied to Cb on the way back: 2 * (1 - Kb)."""
        return 2 * (1 - self.kb)

    @property
    def cr_inverse_gain(self):
        """Factor applied to Cr on the way back: 2 * (1 - Kr)."""
        return 2 * (1 - self.kr)

    def luma(self, r, g, b):
        """Y' from normalised R'G'B' (inputs are Fractions or ints)."""
        return self.kr * r + self.kg * g + self.kb * b

    def __repr__(self):
        return "Standard(%r, Kr=%s, Kb=%s, Kg=%s)" % (
            self.name, self.kr, self.kb, self.kg)

    def describe(self):
        """JSON-friendly view of the constants actually in use."""
        return {
            "name": self.name,
            "kr": float(self.kr),
            "kb": float(self.kb),
            "kg": float(self.kg),
            "cb_gain": float(self.cb_gain),
            "cr_gain": float(self.cr_gain),
        }


STANDARDS = {name: Standard(name) for name in _K_CONSTANTS}


def get_standard(name):
    """Return the cached :class:`Standard` for *name* (aliases accepted)."""
    return STANDARDS[canonical_name(name)]

