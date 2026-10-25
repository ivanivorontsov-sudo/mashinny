import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.setrecursionlimit(100000)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from mash.syntax import parse, fmt
from mash.interp import execute
from mash.vm import execute_vm
from mash.aot import build
from mash.ui import Layout, png, read_png, from_show
from test_core import gen_progs, norm

EX = os.path.join(ROOT, "examples")
N_AOT = int(os.environ.get("MASH_AOT", "60"))
GOLDEN = os.path.join(ROOT, "tests", "golden_ui.json")
SIZES = [(360, 240), (640, 360), (240, 320), (1024, 600)]


def aot_run(prog, entry="main", args=(), fuel=None):
    d = tempfile.mkdtemp()
    try:
        exe = build(prog, os.path.join(d, "p"), entry)
        cmd = [exe, "--json"] + [str(a) for a in args] + (["--fuel", str(fuel)] if fuel else [])
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        return json.loads(out)
    finally:
        shutil.rmtree(d)


def aot_norm(r):
    d = {"status": r["status"], "steps": r["steps"], "effects": r["effects"]}
    if r["status"] == "ok":
        d["value"] = r["value"]
    else:
        d["trap"] = r["trap"]
    return d


@unittest.skipUnless(shutil.which("gcc"), "gcc not found")
class AOT(unittest.TestCase):
    def test_examples(self):
        for f in ("fact.pn", "gcd.pn", "fizzbuzz.pn", "stats.pn"):
            p = parse(open(os.path.join(EX, f)).read())
            self.assertEqual(aot_norm(aot_run(p)), norm(execute(p)), f)

    def test_random_equal(self):
        for p in gen_progs(N_AOT, 50000):
            self.assertEqual(aot_norm(aot_run(p)), norm(execute(p)), fmt(p))

    def test_fuel_trap(self):
        p = parse("fn main() -> Int = sum(range(0, 1000000))")
        self.assertEqual(aot_norm(aot_run(p, fuel=500)), norm(execute(p, fuel=500)))


def layout_for(prog, w, h, engine):
    r = engine(prog, "view", [w, h])
    return Layout(r["_raw"], w, h), r["steps"]


class UI(unittest.TestCase):
    def setUp(self):
        self.p = parse(open(os.path.join(EX, "form.pn")).read())

    def test_vm_interp_identical(self):
        for w, h in SIZES:
            a, sa = layout_for(self.p, w, h, execute)
            b, sb = layout_for(self.p, w, h, execute_vm)
            self.assertEqual(sa, sb)
            self.assertEqual(a.report()[0], b.report()[0])

    @unittest.skipUnless(shutil.which("gcc"), "gcc not found")
    def test_aot_ui_tree_renders_same(self):
        for w, h in SIZES:
            r = aot_run(self.p, "view", [w, h])
            lay = Layout(from_show(r["value"]), w, h)
            ref, _ = layout_for(self.p, w, h, execute)
            self.assertEqual(lay.render(), ref.render())
            self.assertEqual(lay.report()[0]["framebuffer_sha256"], ref.report()[0]["framebuffer_sha256"])

    def test_boxes_match_pixels(self):
        for w, h in SIZES:
            lay, _ = layout_for(self.p, w, h, execute)
            rep, fb = lay.report()
            own = lay.owner_map()
            for e in lay.painted():
                x, y, cw, ch = e["clip"]
                if cw == 0 or ch == 0:
                    continue
                if e["kind"] in ("rect", "box"):
                    c = e["color"]
                    px = bytes(((c >> 16) & 255, (c >> 8) & 255, c & 255, 255))
                    for yy in range(y, y + ch):
                        for xx in range(x, x + cw):
                            if own[yy * w + xx] == e["z"]:
                                self.assertEqual(fb[(yy * w + xx) * 4:(yy * w + xx) * 4 + 4], px, (e["id"], xx, yy))
                if e["kind"] == "text":
                    c = e["color"]
                    px = bytes(((c >> 16) & 255, (c >> 8) & 255, c & 255, 255))
                    ink = [(xx, yy) for yy in range(h) for xx in range(w) if own[yy * w + xx] == e["z"] and fb[(yy * w + xx) * 4:(yy * w + xx) * 4 + 4] == px]
                    self.assertTrue(ink, e["id"])
                    self.assertTrue(all(x <= a < x + cw and y <= b < y + ch for a, b in ink))

    def test_hit_matches_owner_map(self):
        lay, _ = layout_for(self.p, 360, 240, execute)
        own = lay.owner_map()
        ids = {e["z"]: e["id"] for e in lay.painted()}
        for yy in range(0, 240, 3):
            for xx in range(0, 360, 3):
                self.assertEqual(lay.hit(xx, yy), ids.get(own[yy * 360 + xx]))
        self.assertEqual(lay.hit(245, 75), "popup_bg")
        self.assertEqual(lay.hit(30, 100), "password")

    def test_png_roundtrip_and_golden(self):
        golden = json.load(open(GOLDEN)) if os.path.exists(GOLDEN) else {}
        got = {}
        for w, h in SIZES:
            lay, _ = layout_for(self.p, w, h, execute)
            rep, fb = lay.report()
            ww, hh, back = read_png(png(w, h, fb))
            self.assertEqual((ww, hh, back), (w, h, fb))
            got["%dx%d" % (w, h)] = {"framebuffer_sha256": rep["framebuffer_sha256"], "boxes": {e["id"]: e["clip"] for e in rep["elements"]}}
        if not golden:
            json.dump(got, open(GOLDEN, "w"), indent=1, ensure_ascii=False)
        else:
            self.assertEqual(got, golden)

    def test_cli_predict(self):
        d = tempfile.mkdtemp()
        out = subprocess.run([sys.executable, "-m", "mash", "predict", os.path.join(EX, "form.pn"), "--ui", "360x240", "--hit", "245,75", "--png-dir", d], capture_output=True, text=True, cwd=ROOT).stdout
        r = json.loads(out)["ui"][0]
        self.assertEqual(r["hits"][0]["id"], "popup_bg")
        w, h, fb = read_png(open(r["png"], "rb").read())
        import struct
        self.assertEqual(hashlib.sha256(struct.pack(">II", w, h) + fb).hexdigest(), r["framebuffer_sha256"])
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
