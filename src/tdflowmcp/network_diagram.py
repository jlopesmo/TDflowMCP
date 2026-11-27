"""Pure renderer for a bounded, already authorized network snapshot."""

import math
import struct
import time
import zlib

VERIFIED_BUILD = "2025.32440"
WIDTH, HEIGHT = 960, 640
MAX_NODES, MAX_EDGES = 64, 128
MAX_PNG_BYTES = 192 * 1024
FAMILY_COLORS = {
    "COMP": (147, 195, 252),
    "TOP": (204, 181, 248),
    "CHOP": (151, 218, 182),
    "DAT": (250, 221, 144),
    "SOP": (248, 167, 196),
    "MAT": (253, 193, 144),
    "POP": (151, 219, 220),
}
BACKGROUND = (248, 250, 252)
INK = (23, 37, 56)
WIRE = (75, 99, 121)
LEADER = (110, 134, 158)
GRID = (231, 236, 242)
MARKER_WIDTH, MARKER_HEIGHT = 36, 24
MARKER_OFFSETS = (
    (0, 0), (-40, 0), (40, 0), (0, -28), (0, 28),
    (-40, -28), (40, -28), (-40, 28), (40, 28),
    (-80, 0), (80, 0), (0, -56), (0, 56),
)
DIGITS = (
    (14, 17, 19, 21, 25, 17, 14),
    (4, 12, 4, 4, 4, 4, 14),
    (14, 17, 1, 2, 4, 8, 31),
    (30, 1, 1, 14, 1, 1, 30),
    (2, 6, 10, 18, 31, 2, 2),
    (31, 16, 16, 30, 1, 1, 30),
    (14, 16, 16, 30, 17, 17, 14),
    (31, 1, 2, 4, 8, 8, 8),
    (14, 17, 17, 14, 17, 17, 14),
    (14, 17, 17, 15, 1, 1, 14),
)


class RenderLimit(ValueError):
    """The diagram exceeded a fixed rendering budget."""


def _chunk(kind, data):
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


class _Canvas:
    def __init__(self):
        self.pixels = bytearray(bytes(BACKGROUND) * (WIDTH * HEIGHT))

    def rect(self, x, y, width, height, rgb):
        left, right = max(0, x), min(WIDTH, x + width)
        if left >= right:
            return
        row = bytes(rgb) * (right - left)
        for line in range(max(0, y), min(HEIGHT, y + height)):
            start = 3 * (line * WIDTH + left)
            self.pixels[start:start + len(row)] = row

    def line(self, start, end, rgb):
        x, y = start
        x2, y2 = end
        dx, dy = abs(x2 - x), -abs(y2 - y)
        step_x, step_y = (1 if x < x2 else -1), (1 if y < y2 else -1)
        error = dx + dy
        color = bytes(rgb)
        while True:
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                index = 3 * (y * WIDTH + x)
                self.pixels[index:index + 3] = color
            if (x, y) == end:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x += step_x
            if twice <= dx:
                error += dx
                y += step_y

    def number(self, x, y, number):
        letters = str(number)
        start_x = x - (len(letters) * 12 - 2) // 2
        for index, digit in enumerate(letters):
            for row, mask in enumerate(DIGITS[int(digit)]):
                for column in range(5):
                    if mask & (16 >> column):
                        self.rect(start_x + index * 12 + column * 2, y - 7 + row * 2, 2, 2, INK)

    def png(self):
        stride = 3 * WIDTH
        rows = b"".join(b"\x00" + self.pixels[n:n + stride] for n in range(0, len(self.pixels), stride))
        header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
                + _chunk(b"IDAT", zlib.compress(rows, 3)) + _chunk(b"IEND", b""))


def _project(nodes):
    xmin, xmax = min(n["x"] for n in nodes), max(n["x"] for n in nodes)
    ymin, ymax = min(n["y"] for n in nodes), max(n["y"] for n in nodes)
    scale = min((WIDTH - 200) / max(1, xmax - xmin), (HEIGHT - 150) / max(1, ymax - ymin), 1.0)
    mid_x, mid_y = (xmin + xmax) / 2, (ymin + ymax) / 2
    return {n["id"]: (round(WIDTH / 2 + (n["x"] - mid_x) * scale),
                      round(HEIGHT / 2 - (n["y"] - mid_y) * scale)) for n in nodes}


def _wire(canvas, start, end):
    canvas.line(start, end, WIRE)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 38:
        return
    ux, uy = dx / length, dy / length
    tip = (round(end[0] - 23 * ux), round(end[1] - 23 * uy))
    for side in (-1, 1):
        tail = (round(tip[0] - 9 * ux + side * 5 * uy),
                round(tip[1] - 9 * uy - side * 5 * ux))
        canvas.line(tail, tip, WIRE)


def render(nodes, edges):
    """Render IDs and families at their observed positions without drawing project text."""
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise RenderLimit("The diagram exceeds the node or edge limit.")
    stop = time.monotonic() + 0.25
    canvas = _Canvas()
    for x in range(32, WIDTH, 32):
        canvas.rect(x, 0, 1, HEIGHT, GRID)
    for y in range(32, HEIGHT, 32):
        canvas.rect(0, y, WIDTH, 1, GRID)
    if nodes:
        points = _project(nodes)
        for edge in edges:
            _wire(canvas, points[edge["source"]], points[edge["target"]])
            if time.monotonic() > stop:
                raise RenderLimit("The diagram exceeds the rendering time limit.")
        _markers(canvas, nodes, points)
    png = canvas.png()
    if len(png) > MAX_PNG_BYTES or time.monotonic() > stop:
        raise RenderLimit("The diagram exceeds the image size or rendering time limit.")
    return png


def _markers(canvas, nodes, points):
    labels = []
    for node in nodes:
        point = points[node["id"]]
        for x, y in _label_centers(point):
            if not (MARKER_WIDTH // 2 <= x < WIDTH - MARKER_WIDTH // 2
                    and MARKER_HEIGHT // 2 <= y < HEIGHT - MARKER_HEIGHT // 2):
                continue
            if all(abs(x - ox) >= MARKER_WIDTH or abs(y - oy) >= MARKER_HEIGHT for ox, oy, _ in labels):
                break
        else:
            raise RenderLimit("The diagram could not place all node labels.")
        labels.append((x, y, node))
        if (x, y) != point:
            canvas.line(point, (x, y), LEADER)
            canvas.rect(point[0] - 2, point[1] - 2, 5, 5, INK)
    for x, y, node in labels:
        left, top = x - MARKER_WIDTH // 2, y - MARKER_HEIGHT // 2
        canvas.rect(left, top, MARKER_WIDTH, MARKER_HEIGHT, INK)
        canvas.rect(left + 2, top + 2, MARKER_WIDTH - 4, MARKER_HEIGHT - 4,
                    FAMILY_COLORS.get(node["family"], (217, 225, 233)))
        canvas.number(x, y, node["id"])


def _label_centers(point):
    x, y = point
    for dx, dy in MARKER_OFFSETS:
        yield x + dx, y + dy
    for y in range(20, HEIGHT - 20, MARKER_HEIGHT + 4):
        for x in range(24, WIDTH - 24, MARKER_WIDTH + 4):
            yield x, y
