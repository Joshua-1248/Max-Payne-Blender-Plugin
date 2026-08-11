"""Minimal GIF87a/GIF89a decoder.

Max Payne LDB texture type 1 is GIF -- a gap in the reference SDK's type table
(0=tga, 2=scx, 3=pcx, 4=jpg, 5=dds), which raises "Unknown texture file type 1"
on levels that use them. They are typically small paletted alpha masks
(black.gif, Lamp11_128x64_alpha.gif).

Blender cannot load GIF natively, so the first frame is decoded here into raw
RGBA floats suitable for bpy.types.Image.pixels. Only the first frame is
decoded; animation is irrelevant for texture use.
"""

import struct


def _lzw_decode(min_code_size, data):
    """Decode GIF's variable-width LZW stream into a list of palette indices."""
    clear_code = 1 << min_code_size
    end_code = clear_code + 1
    code_size = min_code_size + 1
    next_code = end_code + 1
    max_code = (1 << code_size) - 1

    table = {i: [i] for i in range(clear_code)}
    out = []
    prev = None

    bitpos = 0
    total_bits = len(data) * 8

    while bitpos + code_size <= total_bits:
        # GIF packs codes little-endian, least significant bit first.
        byte_index = bitpos >> 3
        bit_offset = bitpos & 7
        chunk = data[byte_index:byte_index + 3]
        while len(chunk) < 3:
            chunk += b'\x00'
        value = chunk[0] | (chunk[1] << 8) | (chunk[2] << 16)
        code = (value >> bit_offset) & ((1 << code_size) - 1)
        bitpos += code_size

        if code == clear_code:
            table = {i: [i] for i in range(clear_code)}
            code_size = min_code_size + 1
            max_code = (1 << code_size) - 1
            next_code = end_code + 1
            prev = None
            continue
        if code == end_code:
            break

        if code in table:
            entry = table[code]
        elif prev is not None:
            entry = prev + [prev[0]]
        else:
            break

        out.extend(entry)

        if prev is not None and next_code < 4096:
            table[next_code] = prev + [entry[0]]
            next_code += 1
            if next_code > max_code and code_size < 12:
                code_size += 1
                max_code = (1 << code_size) - 1

        prev = entry

    return out


def _read_blocks(data, pos):
    """Read a GIF sub-block chain, returning (bytes, new_position)."""
    out = bytearray()
    while pos < len(data):
        size = data[pos]
        pos += 1
        if size == 0:
            break
        out += data[pos:pos + size]
        pos += size
    return bytes(out), pos


def decode_gif(data):
    """Decode the first frame. Returns (width, height, rgba_float_list).

    Rows are emitted bottom-to-top to match Blender's image origin."""
    if len(data) < 13 or data[:3] != b'GIF':
        raise ValueError("not a GIF file")

    screen_w, screen_h, packed, _bg, _aspect = struct.unpack_from('<HHBBB', data, 6)
    pos = 13

    global_palette = None
    if packed & 0x80:
        size = 2 << (packed & 0x07)
        global_palette = data[pos:pos + size * 3]
        pos += size * 3

    transparent_index = None

    while pos < len(data):
        block = data[pos]
        pos += 1

        if block == 0x3B:                       # trailer
            break

        if block == 0x21:                       # extension
            label = data[pos]
            pos += 1
            if label == 0xF9:                   # graphic control
                size = data[pos]
                flags = data[pos + 1]
                if flags & 0x01:
                    transparent_index = data[pos + 4]
                pos += size + 1
                # consume terminator chain
                _skip, pos = _read_blocks(data, pos)
            else:
                _skip, pos = _read_blocks(data, pos)
            continue

        if block != 0x2C:                       # not an image descriptor
            continue

        # Image descriptor
        left, top, width, height, ipacked = struct.unpack_from('<HHHHB', data, pos)
        pos += 9

        palette = global_palette
        if ipacked & 0x80:
            size = 2 << (ipacked & 0x07)
            palette = data[pos:pos + size * 3]
            pos += size * 3
        if palette is None:
            raise ValueError("GIF has no colour table")

        interlaced = bool(ipacked & 0x40)

        min_code_size = data[pos]
        pos += 1
        raw, pos = _read_blocks(data, pos)
        indices = _lzw_decode(min_code_size, raw)

        if width <= 0 or height <= 0:
            raise ValueError("bad GIF frame dimensions")

        needed = width * height
        if len(indices) < needed:
            indices = indices + [0] * (needed - len(indices))

        # De-interlace if required.
        if interlaced:
            rows = [None] * height
            src = 0
            for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
                for y in range(start, height, step):
                    rows[y] = indices[src * width:(src + 1) * width]
                    src += 1
            ordered = []
            for r in rows:
                ordered.extend(r if r else [0] * width)
            indices = ordered

        inv = 1.0 / 255.0
        pixels = [0.0] * (width * height * 4)
        npal = len(palette) // 3
        for y in range(height):
            dst_y = height - 1 - y          # Blender rows run bottom-to-top
            row_off = y * width
            dst_row = dst_y * width * 4
            for x in range(width):
                idx = indices[row_off + x]
                d = dst_row + x * 4
                if idx < npal:
                    p = idx * 3
                    pixels[d] = palette[p] * inv
                    pixels[d + 1] = palette[p + 1] * inv
                    pixels[d + 2] = palette[p + 2] * inv
                pixels[d + 3] = 0.0 if (transparent_index is not None
                                        and idx == transparent_index) else 1.0

        return width, height, pixels

    raise ValueError("no image frame found in GIF")
