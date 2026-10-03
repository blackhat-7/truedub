"""Generate icons/{16,32,48,128}.png: white voice bars on a red rounded square.

Stdlib only. Run from the extension directory: python3 dev/make_icons.py
"""

import struct
import zlib

RED = (255, 0, 51)
WHITE = (255, 255, 255)
BARS = [0.32, 0.6, 0.32]  # bar heights, as a fraction of the icon
SS = 8  # supersampling per axis


def inside_round_rect(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def color_at(x, y):
    """Colour at a point in unit coordinates, or None for transparent."""
    if not inside_round_rect(x, y, 0, 0, 1, 1, 0.22):
        return None
    w, gap = 0.15, 0.1
    left = 0.5 - (len(BARS) * w + (len(BARS) - 1) * gap) / 2
    for i, h in enumerate(BARS):
        x0 = left + i * (w + gap)
        if inside_round_rect(x, y, x0, 0.5 - h / 2, x0 + w, 0.5 + h / 2, w / 2):
            return WHITE
    return RED


def render(size):
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    c = color_at((px + (sx + 0.5) / SS) / size, (py + (sy + 0.5) / SS) / size)
                    if c:
                        acc[0] += c[0]
                        acc[1] += c[1]
                        acc[2] += c[2]
                        acc[3] += 1
            n = acc[3]
            row += bytes([acc[0] // n, acc[1] // n, acc[2] // n] if n else [0, 0, 0])
            row.append(round(255 * n / SS**2))
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


for size in (16, 32, 48, 128):
    with open(f"icons/{size}.png", "wb") as f:
        f.write(render(size))
