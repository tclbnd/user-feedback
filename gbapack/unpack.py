"""GBA/NDS BIOS-compressed blobs (.lz): header, LZ77, LZ11, Huffman, RLE and difference filters."""
import zlib

TYPES = {
    0x10: "lz77",
    0x11: "lz11",
    0x28: "huffman8",
    0x30: "rle",
    0x81: "diff8",
    0x82: "diff16",
}


class PackError(ValueError):
    pass


class _Short(Exception):
    pass


def _u8(src, p):
    if p >= len(src):
        raise _Short
    return src[p], p + 1


def _copy_match(out, distance, length, size):
    if distance <= 0 or distance > len(out):
        raise PackError("reference before start of data")
    for _ in range(length):
        if len(out) >= size:
            break
        out.append(out[-distance])


def _lz77(src, p, size):
    out = bytearray()
    while len(out) < size:
        flags, p = _u8(src, p)
        for mask in (0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01):
            if len(out) >= size:
                break
            if flags & mask:
                if p + 1 >= len(src):
                    raise _Short
                b0, b1 = src[p], src[p + 1]
                p += 2
                length = (b0 >> 4) + 3
                distance = ((b0 & 0x0F) << 8 | b1) + 1
                _copy_match(out, distance, length, size)
            else:
                value, p = _u8(src, p)
                out.append(value)
    return out


# Older private name used by callers/tests.
_lz = _lz77


def _lz11(src, p, size):
    out = bytearray()
    while len(out) < size:
        flags, p = _u8(src, p)
        for mask in (0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01):
            if len(out) >= size:
                break
            if not (flags & mask):
                value, p = _u8(src, p)
                out.append(value)
                continue

            b0, p = _u8(src, p)
            high = b0 >> 4
            if high == 0:
                if p + 1 >= len(src):
                    raise _Short
                b1, b2 = src[p], src[p + 1]
                p += 2
                length = ((b0 & 0x0F) << 4 | b1 >> 4) + 0x11
                distance = ((b1 & 0x0F) << 8 | b2) + 1
            elif high == 1:
                if p + 2 >= len(src):
                    raise _Short
                b1, b2, b3 = src[p], src[p + 1], src[p + 2]
                p += 3
                length = (
                    (b0 & 0x0F) << 12
                    | b1 << 4
                    | b2 >> 4
                ) + 0x111
                distance = ((b2 & 0x0F) << 8 | b3) + 1
            else:
                b1, p = _u8(src, p)
                length = high + 1
                distance = ((b0 & 0x0F) << 8 | b1) + 1

            _copy_match(out, distance, length, size)
    return out


def _rle(src, p, size):
    out = bytearray()
    while len(out) < size:
        flags, p = _u8(src, p)
        if flags & 0x80:
            value, p = _u8(src, p)
            length = (flags & 0x7F) + 3
            out += bytes([value]) * length
        else:
            length = (flags & 0x7F) + 1
            if p + length > len(src):
                raise _Short
            out += src[p:p + length]
            p += length
    return out[:size]


def _diff8(src, p, size):
    if p + size > len(src):
        raise _Short
    out = bytearray(size)
    previous = 0
    for i in range(size):
        previous = (previous + src[p + i]) & 0xFF
        out[i] = previous
    return out


def _diff16(src, p, size):
    if p + size > len(src):
        raise _Short
    out = bytearray()
    previous = 0
    for i in range(0, size - 1, 2):
        delta = src[p + i] | src[p + i + 1] << 8
        previous = (previous + delta) & 0xFFFF
        out += bytes((previous & 0xFF, previous >> 8))
    if size & 1:
        # BIOS half-word streams are expected to have an even header size.
        # Keep a trailing byte rather than making the summary shorter.
        out.append(src[p + size - 1])
    return out


def _huff_parent_layout(tree, left_leaf_bit, right_leaf_bit, offset_mode):
    """Validate the pre-order tree used by the BIOS Huffman decoder."""
    length = len(tree)

    def right_child(i):
        offset = tree[i] & 0x3F
        if offset_mode == 0:
            return i + 1 + offset
        if offset_mode == 1:
            return i + offset
        if offset_mode == 2:
            return i + 1 + (offset << 1)
        if offset_mode == 3:
            return i + (offset << 1)
        if offset_mode == 4:
            return i + 2 + offset
        if offset_mode == 5:
            return i + 2 + (offset << 1)
        if offset_mode == 6:
            return i + 1 + (offset << 2)
        return i + 2 + (offset << 2)

    def visit(i):
        if i < 0 or i >= length:
            raise PackError("bad Huffman tree")
        control = tree[i]

        left = i + 1
        if left >= length:
            raise PackError("bad Huffman tree")
        if control & left_leaf_bit:
            next_pos = left + 1
        else:
            next_pos = visit(left)

        right = right_child(i)
        if right <= i or right >= length or right != next_pos:
            raise PackError("bad Huffman tree")
        if control & right_leaf_bit:
            end_pos = right + 1
        else:
            end_pos = visit(right)
        return end_pos

    if visit(0) != length:
        raise PackError("bad Huffman tree")
    return right_child


def _huff_parent_decode(src, bit_p, size, tree, right_child,
                        left_leaf_bit, right_leaf_bit, lsb_first):
    out = bytearray()
    bit_pos = 0
    while len(out) < size:
        node = 0
        while True:
            byte_pos = bit_p + bit_pos // 8
            if byte_pos >= len(src):
                raise _Short
            bits = src[byte_pos]
            if lsb_first:
                bit = (bits >> (bit_pos % 8)) & 1
            else:
                bit = (bits >> (7 - bit_pos % 8)) & 1
            bit_pos += 1

            control = tree[node]
            if bit == 0:
                child = node + 1
                leaf_bit = left_leaf_bit
            else:
                child = right_child(node)
                leaf_bit = right_leaf_bit

            if child < 0 or child >= len(tree):
                raise PackError("bad Huffman node")
            if control & leaf_bit:
                out.append(tree[child])
                break
            node = child
    return out


def _huffman8(src, p, size):
    if size == 0:
        return bytearray()

    info, p = _u8(src, p)
    tree_length = 2 * info + 1
    if p + tree_length > len(src):
        raise _Short
    tree = src[p:p + tree_length]

    layouts = []
    for left_bit, right_bit in ((0x40, 0x80), (0x80, 0x40)):
        for mode in range(8):
            try:
                right_child = _huff_parent_layout(
                    tree, left_bit, right_bit, mode
                )
            except PackError:
                continue
            layouts.append((left_bit, right_bit, right_child))

    short = False
    # The odd tree is followed immediately by the bitstream.  Try the
    # half-word padded interpretation as well for interoperability.
    for bit_p in (p + tree_length, p + tree_length + 1):
        for lsb_first in (True, False):
            for left_bit, right_bit, right_child in layouts:
                try:
                    return _huff_parent_decode(
                        src, bit_p, size, tree, right_child,
                        left_bit, right_bit, lsb_first
                    )
                except _Short:
                    short = True
                except PackError:
                    pass

    # Compatibility fallback for the marker-byte tree variant used in some
    # third-party GBA/NDS tools.  Its leaf symbols are seven-bit values.
    padded_length = 2 * (info + 1)
    marker_tree = src[p:p + padded_length]
    for bit_p in (p + tree_length, p + padded_length):
        for lsb_first in (True, False):
            for add_one in (True, False):
                for leaf_next in (False, True):
                    try:
                        out = bytearray()
                        node = 0
                        bit_pos = 0
                        while len(out) < size:
                            byte_pos = bit_p + bit_pos // 8
                            if byte_pos >= len(src):
                                raise _Short
                            bits = src[byte_pos]
                            if lsb_first:
                                bit = (bits >> (bit_pos % 8)) & 1
                            else:
                                bit = (bits >> (7 - bit_pos % 8)) & 1
                            bit_pos += 1

                            current = node
                            if bit:
                                node += (marker_tree[current] & 0x3F)
                                if add_one:
                                    node += 1
                            else:
                                node += 1
                            if node <= current or node >= len(marker_tree):
                                raise PackError("bad marker tree")
                            value = marker_tree[node]
                            if value & 0x80:
                                if leaf_next:
                                    if node + 1 >= len(marker_tree):
                                        raise PackError("bad marker leaf")
                                    out.append(marker_tree[node + 1])
                                else:
                                    out.append(value & 0x7F)
                                node = 0
                            elif not leaf_next and value & 0x40:
                                # Alternate eight-bit leaf form: marker byte
                                # followed by the literal symbol byte.
                                if node + 1 >= len(marker_tree):
                                    raise PackError("bad marker leaf")
                                out.append(marker_tree[node + 1])
                                node = 0
                        return out
                    except _Short:
                        short = True
                    except (PackError, IndexError):
                        pass

    if short:
        raise _Short
    raise PackError("invalid Huffman data")


_huffman = _huffman8


def parse(data):
    if len(data) < 4:
        raise PackError("too short")
    t = data[0]
    if t not in TYPES:
        raise PackError("unknown compression type 0x%02x" % t)
    size = data[1] | data[2] << 8 | data[3] << 16
    p = 4
    complete = True
    try:
        if t == 0x10:
            out = _lz77(data, p, size)
        elif t == 0x11:
            out = _lz11(data, p, size)
        elif t == 0x28:
            out = _huffman8(data, p, size)
        elif t == 0x30:
            out = _rle(data, p, size)
        elif t == 0x81:
            out = _diff8(data, p, size)
        else:
            out = _diff16(data, p, size)
    except _Short:
        out, complete = None, False
    res = {"type": TYPES[t], "size": size, "complete": complete, "crc32": None, "tiles": None, "blank_tiles": None}
    if out is not None:
        res["crc32"] = "%08x" % zlib.crc32(bytes(out))
        res["tiles"] = len(out) // 32
        res["blank_tiles"] = sum(1 for i in range(len(out) // 32) if not any(out[32 * i:32 * i + 32]))
    return res
