from PIL import Image, ImageDraw, ImageFont

SRC = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
CHARS = [chr(c) for c in range(32, 127)] + [chr(c) for c in range(0x410, 0x450)] + ["Ё", "ё", "№", "—", "«", "»", "…"]
W, H, S = 6, 10, 8


def glyph(font, ch):
    img = Image.new("L", (W * S, H * S), 0)
    d = ImageDraw.Draw(img)
    d.text((W * S // 2, H * S * 0.8), ch, font=font, fill=255, anchor="ms")
    rows = []
    for y in range(H):
        bits = 0
        for x in range(W):
            tot = sum(img.getpixel((x * S + i, y * S + j)) for i in range(S) for j in range(S))
            if tot > 255 * S * S * 0.42:
                bits |= 1 << (W - 1 - x)
        rows.append(bits)
    return rows


def main():
    font = ImageFont.truetype(SRC, int(H * S * 0.9))
    lines = ["GLYPH_W = %d" % W, "GLYPH_H = %d" % H, "GLYPHS = {"]
    for ch in CHARS:
        lines.append("    %r: %r," % (ch, tuple(glyph(font, ch))))
    lines.append("}")
    open("mash/font_data.py", "w").write("\n".join(lines) + "\n")


main()
