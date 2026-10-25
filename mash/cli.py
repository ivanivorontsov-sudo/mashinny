import argparse
import json
import os
import sys
import threading

from .syntax import parse, fmt, SyntaxErr
from .types import check
from .binfmt import encode, decode, hashes, BinErr
from .interp import execute, DEFAULT_FUEL
from .vm import execute_vm
from . import astjson

VERSION = "0.1.0"


def out(obj, code=0):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    return code


def load(path):
    if path.endswith(".pnb"):
        return decode(open(path, "rb").read())
    if path.endswith(".json"):
        return astjson.j_prog(json.load(open(path, encoding="utf-8")))
    return parse(open(path, encoding="utf-8").read())


def load_checked(path):
    try:
        prog = load(path)
    except SyntaxErr as e:
        return None, [e.to_json()]
    except BinErr as e:
        return None, [e.to_json()]
    except (ValueError, KeyError, TypeError) as e:
        return None, [{"code": "E-AST", "message": str(e)}]
    errs = check(prog)
    return prog, errs


def size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


def strip(r):
    r = dict(r)
    r.pop("_raw", None)
    return r


def ui_predict(prog, a, engine):
    from .ui import Layout, png
    sizes = [size(s) for s in a.ui]
    results = []
    for w, h in sizes:
        r = engine(prog, a.entry or "view", [w, h], a.fuel)
        item = {"screen": [w, h], "status": r["status"], "steps": r["steps"]}
        if r["status"] != "ok":
            item["trap"] = r.get("trap") or r.get("error")
            results.append(item)
            continue
        lay = Layout(r["_raw"], w, h)
        rep, fb = lay.report(with_map=a.map)
        item.update(rep)
        if a.hit:
            item["hits"] = [{"x": x, "y": y, "id": lay.hit(x, y)} for x, y in (map(int, p.split(",")) for p in a.hit)]
        if a.png_dir:
            os.makedirs(a.png_dir, exist_ok=True)
            fn = os.path.join(a.png_dir, "screen_%dx%d.png" % (w, h))
            open(fn, "wb").write(png(w, h, fb))
            item["png"] = fn
        results.append(item)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mash", description="Машинный: язык для ИИ")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("check", "fmt", "to-bin", "from-bin", "prove", "hash", "run", "predict", "to-json", "aot", "to-c"):
        p = sub.add_parser(n)
        p.add_argument("file")
        if n in ("run", "predict"):
            p.add_argument("--entry")
            p.add_argument("--args", default="[]")
            p.add_argument("--fuel", type=int, default=DEFAULT_FUEL)
            p.add_argument("--engine", choices=["vm", "interp"], default="vm")
            p.add_argument("--json", action="store_true")
            p.add_argument("--ui", action="append", default=[])
            p.add_argument("--hit", action="append", default=[])
            p.add_argument("--png-dir")
            p.add_argument("--map", action="store_true")
        if n in ("to-bin", "from-bin", "fmt", "aot", "to-c", "to-json"):
            p.add_argument("-o", "--output")
        if n == "fmt":
            p.add_argument("--check", action="store_true")
        if n == "prove":
            p.add_argument("--timeout", type=int, default=5000)
        if n in ("aot", "to-c"):
            p.add_argument("--entry", default="main")
            p.add_argument("--target", choices=["native", "windows"], default="native")
    g = sub.add_parser("grammar")
    g.add_argument("--format", choices=["gbnf", "json-schema"], default="gbnf")
    a = ap.parse_args(argv)

    if a.cmd == "grammar":
        from .grammar import gbnf, json_schema
        sys.stdout.write(gbnf() if a.format == "gbnf" else json.dumps(json_schema(), indent=1) + "\n")
        return 0

    prog, errs = load_checked(a.file)
    if prog is None or errs:
        return out({"ok": False, "errors": errs}, 1)

    if a.cmd == "check":
        return out({"ok": True, "errors": [], **hashes(prog)})
    if a.cmd == "hash":
        return out(hashes(prog))
    if a.cmd == "fmt":
        text = fmt(prog)
        if a.check:
            same = open(a.file, encoding="utf-8").read() == text
            return out({"ok": same, "canonical": same}, 0 if same else 1)
        if a.output:
            open(a.output, "w", encoding="utf-8").write(text)
        else:
            sys.stdout.write(text)
        return 0
    if a.cmd == "from-bin":
        text = fmt(prog)
        if a.output:
            open(a.output, "w", encoding="utf-8").write(text)
        else:
            sys.stdout.write(text)
        return 0
    if a.cmd == "to-bin":
        data = encode(prog)
        o = a.output or os.path.splitext(a.file)[0] + ".pnb"
        open(o, "wb").write(data)
        return out({"ok": True, "output": o, **hashes(prog)})
    if a.cmd == "to-json":
        s = json.dumps(astjson.prog_j(prog), ensure_ascii=False)
        if a.output:
            open(a.output, "w", encoding="utf-8").write(s)
        else:
            sys.stdout.write(s + "\n")
        return 0
    if a.cmd == "prove":
        from .prove import prove
        r = prove(prog, a.timeout)
        return out(r, 0 if r["ok"] else 1)
    if a.cmd in ("aot", "to-c"):
        from .aot import to_c, build
        if a.cmd == "to-c":
            src = to_c(prog, a.entry)
            if a.output:
                open(a.output, "w").write(src)
            else:
                sys.stdout.write(src)
            return 0
        o = a.output or os.path.splitext(a.file)[0] + (".exe" if a.target == "windows" else "")
        build(prog, o, a.entry, target=a.target if a.target == "windows" else None)
        return out({"ok": True, "output": o})

    engine = execute_vm if a.engine == "vm" else execute
    args = json.loads(a.args)
    if a.cmd == "predict":
        if a.ui:
            return out({"mode": "predict", "exact": True, "ui": ui_predict(prog, a, engine)})
        r = strip(engine(prog, a.entry or "main", args, a.fuel, False))
        r["mode"] = "predict"
        r["exact"] = True
        return out(r, 0 if r["status"] == "ok" else 2)
    if a.ui:
        res = ui_predict(prog, a, engine)
        return out({"mode": "run", "ui": res})
    r = strip(engine(prog, a.entry or "main", args, a.fuel, not a.json))
    if a.json:
        return out(r, 0 if r["status"] == "ok" else 2)
    if r["status"] != "ok":
        sys.stderr.write(json.dumps(r.get("trap") or r.get("error"), ensure_ascii=False) + "\n")
        return 2
    return 0


def entry():
    sys.setrecursionlimit(200000)
    threading.stack_size(512 * 1024 * 1024)
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("rc", main()))
    t.start()
    t.join()
    sys.exit(box.get("rc", 1))
