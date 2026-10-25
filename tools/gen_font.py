import sys

W, H = 6, 12


def main(path):
    glyphs, cur, bm = {}, None, None
    for line in open(path, encoding="latin-1"):
        p = line.split()
        if not p:
            continue
        if p[0] == "ENCODING":
            cur = int(p[1])
        elif p[0] == "BITMAP":
            bm = []
        elif p[0] == "ENDCHAR":
            if cur is not None and (cur < 0x500 or 0x2010 <= cur <= 0x2026 or cur == 0x2116) and len(bm) == H:
                glyphs[cur] = tuple(int(r, 16) >> (len(r) * 4 - W) for r in bm)
            bm = None
        elif bm is not None:
            bm.append(p[0])
    lines = ["GLYPH_W = %d" % W, "GLYPH_H = %d" % H, "GLYPHS = {"]
    for c in sorted(glyphs):
        lines.append("    %r: %r," % (chr(c), glyphs[c]))
    lines.append("}")
    open("mash/font_data.py", "w").write("\n".join(lines) + "\n")


main(sys.argv[1])
