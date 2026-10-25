import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.setrecursionlimit(100000)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from mash.ast import *
from mash.syntax import parse, fmt, SyntaxErr
from mash.types import check, Checker
from mash.binfmt import encode, decode, hashes, BinErr, uleb
from mash.interp import execute
from mash.vm import execute_vm
from mash import astjson
from gen import Gen

EX = os.path.join(ROOT, "examples")
N = int(os.environ.get("MASH_FUZZ", "300"))


def fix_ret(p):
    c = Checker(p)
    c.funcs = {f.name: f for f in p.funcs}
    m = p.funcs[-1]
    m.ret = INT
    c.cur = m
    c.calls[m.name] = set()
    m.ret = c.ex(m.body, {}, "", True)
    return p


def gen_progs(n, seed0=0):
    out = []
    for s in range(n):
        p = fix_ret(Gen(seed0 + s).program())
        out.append(p)
    return out


def norm(r):
    d = {"status": r["status"], "steps": r["steps"], "effects": [e["arg"] for e in r["effects"]]}
    if r["status"] == "ok":
        d["value"] = r["value"]
    else:
        d["trap"] = r["trap"]["code"]
    return d


class RoundTrip(unittest.TestCase):
    def test_examples(self):
        for f in sorted(os.listdir(EX)):
            src = open(os.path.join(EX, f), encoding="utf-8").read()
            p = parse(src)
            self.assertEqual(check(p), [], f)
            self.assertEqual(fmt(p), src, f + " is not canonical")
            b = encode(p)
            self.assertEqual(fmt(decode(b)), src)
            self.assertEqual(encode(decode(b)), b)

    def test_random(self):
        for p in gen_progs(N):
            self.assertEqual(check(p), [])
            t = fmt(p)
            p2 = parse(t)
            self.assertTrue(same(Program(sorted(p.funcs, key=lambda f: f.name)), Program(sorted(p2.funcs, key=lambda f: f.name))), t)
            self.assertEqual(fmt(p2), t)
            b = encode(p)
            self.assertEqual(encode(p2), b)
            self.assertEqual(fmt(decode(b)), t)
            self.assertEqual(encode(decode(b)), b)
            j = json.loads(json.dumps(astjson.prog_j(p2)))
            self.assertEqual(encode(astjson.j_prog(j)), b)

    def test_whitespace_insensitive_same_bytes(self):
        a = parse("fn main() -> Int = 1 + 2 * 3")
        b = parse("fn   main( )->Int\n=\n1+2*3")
        self.assertEqual(encode(a), encode(b))

    def test_semantic_hash_ignores_local_names(self):
        a = hashes(parse("fn f(x: Int) -> Int = let y = x + 1; y * y"))
        b = hashes(parse("fn f(a: Int) -> Int = let b = a + 1; b * b"))
        self.assertNotEqual(a["hash"], b["hash"])
        self.assertEqual(a["semantic_hash"], b["semantic_hash"])

    def test_function_order_canonical(self):
        a = parse("fn b() -> Int = 1 fn a() -> Int = b()")
        b = parse("fn a() -> Int = b() fn b() -> Int = 1")
        self.assertEqual(encode(a), encode(b))


class BinaryStrict(unittest.TestCase):
    def setUp(self):
        self.b = encode(parse(open(os.path.join(EX, "fact.pn")).read()))

    def code(self, data):
        try:
            decode(data)
            return None
        except BinErr as e:
            return e.code

    def test_rejects(self):
        self.assertEqual(self.code(b"XXXX"), "B-MAGIC")
        self.assertEqual(self.code(self.b + b"\x00"), "B-TRAIL")
        self.assertEqual(self.code(self.b[:-3]), self.code(self.b[:-3]) or "x")
        self.assertIsNotNone(self.code(self.b[:-3]))

    def test_nonminimal_leb(self):
        p = parse("fn main() -> Int = 5")
        b = encode(p)
        i = b.index(bytes([0x10, 10]))
        bad = b[:i + 1] + bytes([0x8A, 0x00]) + b[i + 2:]
        self.assertIsNotNone(self.code(bad))

    def test_mutation_fuzz(self):
        import random
        r = random.Random(7)
        for _ in range(3000):
            d = bytearray(self.b)
            for _ in range(r.randrange(1, 4)):
                d[r.randrange(len(d))] = r.randrange(256)
            try:
                p = decode(bytes(d))
                self.assertEqual(encode(p), bytes(d))
            except BinErr:
                pass


class Types(unittest.TestCase):
    def codes(self, src):
        return [e["code"] for e in check(parse(src))]

    def test_errors(self):
        self.assertEqual(self.codes("fn f() -> Int = true"), ["E-TYPE"])
        self.assertEqual(self.codes("fn f() -> Int = g()"), ["E-UNBOUND"])
        self.assertEqual(self.codes("fn f() -> Unit = print(\"x\")"), ["E-EFFECT"])
        self.assertEqual(self.codes("fn f(n: Int) -> Int = f(n)"), ["E-NODEC"])
        self.assertEqual(self.codes("fn f(n: Int) -> Int dec n = g(n) fn g(n: Int) -> Int dec n = f(n)"), ["E-MUTREC", "E-MUTREC"])
        self.assertEqual(self.codes("fn f() -> Int = len([])"), ["E-EMPTYLIST"])
        self.assertEqual(self.codes("fn f(result: Int) -> Int = 1"), ["E-RESERVED"])
        self.assertEqual(self.codes("fn f() -> Int = min(1)"), ["E-ARITY"])

    def test_fix_suggestion(self):
        e = check(parse("fn f(count: Int) -> Int = coutn + 1"))[0]
        self.assertEqual(e["fix"], "replace with `count`")
        self.assertEqual(e["at"], {"line": 1, "col": 27})

    def test_syntax_error_json(self):
        with self.assertRaises(SyntaxErr) as c:
            parse("fn f( -> Int = 1")
        self.assertEqual(c.exception.to_json()["code"], "E-PARSE")


class Semantics(unittest.TestCase):
    def test_vm_equals_interp(self):
        for p in gen_progs(N, 10000):
            a, b = norm(execute(p)), norm(execute_vm(p))
            self.assertEqual(a, b, fmt(p))

    def test_floor_division(self):
        r = execute(parse("fn main() -> [Int] = [-7 / 2, -7 % 2, 7 / -2, 7 % -2]"))
        self.assertEqual(r["value"], [-4, 1, -4, -1])

    def test_traps(self):
        self.assertEqual(execute(parse("fn main() -> Int = 1 / 0"))["trap"]["code"], "T-DIV0")
        self.assertEqual(execute(parse("fn main() -> Int = get([1], 3)"))["trap"]["code"], "T-INDEX")
        self.assertEqual(execute(parse("fn f(n: Int) -> Int pre n > 0 = n fn main() -> Int = f(0)"))["trap"]["code"], "T-PRE")
        r = execute(parse("fn main() -> Int = len(range(0, 100000000))"), fuel=1000)
        self.assertEqual((r["trap"]["code"], r["steps"]), ("T-FUEL", 1000))
        r = execute(parse("fn f(n: Int) -> Int dec n = f(n + 1) fn main() -> Int = f(0)"), fuel=5000)
        self.assertEqual(r["status"], "trap")
        self.assertEqual(r, execute(parse("fn f(n: Int) -> Int dec n = f(n + 1) fn main() -> Int = f(0)"), fuel=5000))

    def test_big_and_wrap(self):
        r = execute(parse("fn main() -> [Int] = [wrap64(9223372036854775807 + 1), wrap64(-1)]"))
        self.assertEqual(r["value"], [{"int": "-9223372036854775808"}, -1])
        r = execute(parse("fn p(n: Int) -> Int pre n >= 0 dec n = if n == 0 then 1 else 2 * p(n - 1) fn main() -> Int = p(5000)"))
        self.assertEqual(r["trap"]["code"], "T-BIG")

    def test_predict_no_live_io(self):
        r = execute(parse("fn main() -> Int !io = let u = print(\"a\"); let v = print(\"b\"); 3"))
        self.assertEqual([e["arg"] for e in r["effects"]], ["a", "b"])


class Prove(unittest.TestCase):
    def test_examples(self):
        from mash.prove import prove
        for f in ("fact.pn", "gcd.pn", "stats.pn", "fizzbuzz.pn"):
            r = prove(parse(open(os.path.join(EX, f)).read()))
            self.assertTrue(r["ok"], (f, r))

    def test_counterexample(self):
        from mash.prove import prove
        r = prove(parse("fn f(x: Int) -> Int post result > x = x * 2"))
        o = r["obligations"][0]
        self.assertEqual(o["status"], "failed")
        x = int(o["counterexample"]["x"])
        self.assertFalse(x * 2 > x)

    def test_bad_measure(self):
        from mash.prove import prove
        r = prove(parse("fn f(n: Int) -> Int dec n = if n > 100 then 0 else f(n + 1)"))
        self.assertEqual(r["termination"]["f"], "fuel-bounded")


if __name__ == "__main__":
    unittest.main()
