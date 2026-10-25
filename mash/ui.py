import hashlib
import struct
import zlib
from .font_data import GLYPHS, GLYPH_W, GLYPH_H
from .interp import Trap

MAXDIM = 1 << 15
BTN_BG, BTN_FG, BTN_BORDER, BTN_PAD = 0x3366CC, 0xFFFFFF, 0x1A3366, 6
MISSING = (0x3F, 0x21, 0x21, 0x21, 0x21, 0x21, 0x21, 0x3F, 0, 0)


def glyph(ch):
    return GLYPHS.get(ch, MISSING)


def measure(v):
    k = v[1]
    a = v[2:]
    if k == "rect":
        return max(0, a[1]), max(0, a[2])
    if k == "text":
        sc = max(1, a[3])
        return GLYPH_W * len(a[1]) * sc, GLYPH_H * sc
    if k == "button":
        return GLYPH_W * len(a[1]) + 2 * BTN_PAD, GLYPH_H + 2 * BTN_PAD
    if k in ("col", "row"):
        gap, pad, _, kids = a
        ms = [measure(c) for c in kids]
        g = gap * max(0, len(kids) - 1)
        if k == "col":
            return max([w for w, _ in ms], default=0) + 2 * pad, sum(h for _, h in ms) + g + 2 * pad
        return sum(w for w, _ in ms) + g + 2 * pad, max([h for _, h in ms], default=0) + 2 * pad
    if k == "stack":
        ms = [measure(c) for c in a[0]]
        return max([w for w, _ in ms], default=0), max([h for _, h in ms], default=0)
    if k == "at":
        w, h = measure(a[2])
        return max(0, a[0] + w), max(0, a[1] + h)
    if k == "clip":
        return max(0, a[1]), max(0, a[2])
    raise Trap("T-UI", "unknown ui node " + k)


def inter(r, c):
    x0, y0 = max(r[0], c[0]), max(r[1], c[1])
    x1, y1 = min(r[0] + r[2], c[0] + c[2]), min(r[1] + r[3], c[1] + c[3])
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


class Layout:
    def __init__(self, root, width, height):
        if not (0 < width <= MAXDIM and 0 < height <= MAXDIM):
            raise Trap("T-UI", "screen size out of range")
        self.w, self.h = width, height
        self.elems = []
        self.place(root, 0, 0, (0, 0, width, height), "0")

    def add(self, kind, eid, path, box, clip, extra):
        vis = inter(box, clip)
        self.elems.append({"z": len(self.elems), "id": eid or "#" + path, "kind": kind, "box": list(box), "clip": list(vis), **extra})

    def place(self, v, x, y, clip, path):
        k, a = v[1], v[2:]
        w, h = measure(v)
        if k == "rect":
            self.add(k, a[0], path, (x, y, w, h), clip, {"color": a[3] & 0xFFFFFF})
        elif k == "text":
            self.add(k, a[0], path, (x, y, w, h), clip, {"text": a[1], "color": a[2] & 0xFFFFFF, "scale": max(1, a[3])})
        elif k == "button":
            self.add(k, a[0], path, (x, y, w, h), clip, {"text": a[1], "color": BTN_BG})
        elif k in ("col", "row"):
            gap, pad, bg, kids = a
            if bg >= 0:
                self.add("box", "", path, (x, y, w, h), clip, {"color": bg & 0xFFFFFF})
            cx, cy = x + pad, y + pad
            for i, c in enumerate(kids):
                cw, ch = measure(c)
                self.place(c, cx, cy, clip, path + "." + str(i))
                if k == "col":
                    cy += ch + gap
                else:
                    cx += cw + gap
        elif k == "stack":
            for i, c in enumerate(a[0]):
                self.place(c, x, y, clip, path + "." + str(i))
        elif k == "at":
            self.place(a[2], x + a[0], y + a[1], clip, path + ".0")
        elif k == "clip":
            nc = inter((x, y, w, h), clip)
            self.add("clip", a[0], path, (x, y, w, h), clip, {})
            self.place(a[3], x, y, nc, path + ".0")

    def painted(self):
        return [e for e in self.elems if e["kind"] != "clip"]

    def owner_map(self):
        own = [-1] * (self.w * self.h)
        for e in self.painted():
            x, y, w, h = e["clip"]
            z = e["z"]
            for yy in range(y, y + h):
                base = yy * self.w
                own[base + x: base + x + w] = [z] * w
        return own

    def render(self):
        fb = bytearray(b"\xff\xff\xff\xff" * (self.w * self.h))
        for e in self.painted():
            cx, cy, cw, ch = e["clip"]
            if cw == 0 or ch == 0:
                continue
            col = e["color"]
            px = bytes(((col >> 16) & 255, (col >> 8) & 255, col & 255, 255))
            bx, by, bw, bh = e["box"]
            if e["kind"] in ("rect", "box", "button"):
                for yy in range(cy, cy + ch):
                    o = (yy * self.w + cx) * 4
                    fb[o:o + cw * 4] = px * cw
            if e["kind"] == "button":
                bp = bytes(((BTN_BORDER >> 16) & 255, (BTN_BORDER >> 8) & 255, BTN_BORDER & 255, 255))
                for xx in range(bx, bx + bw):
                    for yy in (by, by + bh - 1):
                        self.dot(fb, xx, yy, bp, e["clip"])
                for yy in range(by, by + bh):
                    for xx in (bx, bx + bw - 1):
                        self.dot(fb, xx, yy, bp, e["clip"])
                self.text(fb, e["text"], bx + BTN_PAD, by + BTN_PAD, 1, BTN_FG, e["clip"])
            if e["kind"] == "text":
                self.text(fb, e["text"], bx, by, e["scale"], col, e["clip"])
        return bytes(fb)

    def dot(self, fb, x, y, px, c):
        if c[0] <= x < c[0] + c[2] and c[1] <= y < c[1] + c[3]:
            o = (y * self.w + x) * 4
            fb[o:o + 4] = px

    def text(self, fb, s, x, y, sc, col, c):
        px = bytes(((col >> 16) & 255, (col >> 8) & 255, col & 255, 255))
        for i, ch in enumerate(s):
            rows = glyph(ch)
            gx = x + i * GLYPH_W * sc
            for ry, bits in enumerate(rows):
                for rx in range(GLYPH_W):
                    if bits >> (GLYPH_W - 1 - rx) & 1:
                        for dy in range(sc):
                            for dx in range(sc):
                                self.dot(fb, gx + rx * sc + dx, y + ry * sc + dy, px, c)

    def hit(self, x, y):
        for e in reversed(self.painted()):
            cx, cy, cw, ch = e["clip"]
            if cx <= x < cx + cw and cy <= y < cy + ch:
                return e["id"]
        return None

    def report(self, with_map=False):
        own = self.owner_map()
        counts = {}
        for z in own:
            counts[z] = counts.get(z, 0) + 1
        pts = self.painted()
        byz = {e["z"]: e for e in pts}
        for e in pts:
            x, y, w, h = e["clip"]
            hidden_by = set()
            for yy in range(y, y + h):
                row = own[yy * self.w + x: yy * self.w + x + w]
                for z in set(row):
                    if z != e["z"]:
                        hidden_by.add(byz[z]["id"])
            e["visible_pixels"] = counts.get(e["z"], 0)
            e["covered_by"] = sorted(hidden_by)
        fb = self.render()
        out = {"screen": [self.w, self.h], "elements": self.elems,
               "framebuffer_sha256": hashlib.sha256(struct.pack(">II", self.w, self.h) + fb).hexdigest()}
        if with_map:
            out["owner_map_rle"] = rle(own, self.w, {e["z"]: e["id"] for e in pts})
        return out, fb


def rle(own, w, names):
    rows = []
    for y in range(len(own) // w):
        row, runs, start = own[y * w:(y + 1) * w], [], 0
        for x in range(1, w + 1):
            if x == w or row[x] != row[start]:
                runs.append([names.get(row[start]), x - start])
                start = x
        rows.append(runs)
    return rows


def png(w, h, fb):
    raw = b"".join(b"\x00" + fb[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def read_png(data):
    i, idat, w, h = 8, b"", 0, 0
    while i < len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        t, d = data[i + 4:i + 8], data[i + 8:i + 8 + ln]
        if t == b"IHDR":
            w, h = struct.unpack(">II", d[:8])
        if t == b"IDAT":
            idat += d
        i += 12 + ln
    raw = zlib.decompress(idat)
    st = w * 4 + 1
    return w, h, b"".join(raw[y * st + 1:(y + 1) * st] for y in range(h))
