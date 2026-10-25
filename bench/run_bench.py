import ast
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.setrecursionlimit(100000)

import tiktoken
from mash.syntax import parse, fmt, compact, SyntaxErr
from mash.types import check
from mash.interp import execute
from mash.vm import execute_vm
from mash.grammar import gbnf
from tasks import TASKS

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = open(os.path.join(HERE, "spec_prompt.txt")).read()
URL = os.environ.get("LLM_URL", "http://127.0.0.1:8091/completion")
ENC = {n: tiktoken.get_encoding(n) for n in ("cl100k_base", "o200k_base")}


def toks(s):
    return {n: len(e.encode(s)) for n, e in ENC.items()}


def plain(v):
    if isinstance(v, tuple):
        return [plain(x) for x in v]
    return v


def run_mash(src, tests):
    try:
        p = parse(src)
    except SyntaxErr as e:
        return "syntax", [e.to_json()]
    errs = check(p)
    if errs:
        return "type", errs
    if "solve" not in {f.name for f in p.funcs}:
        return "type", [{"code": "E-ENTRY", "message": "no solve"}]
    for args, want in tests:
        r = execute(p, "solve", args, fuel=200000)
        if r["status"] != "ok" or plain(r["_raw"]) != want:
            return "wrong", [{"args": args, "expected": want, "got": r.get("value"), "trap": r.get("trap")}]
    return "pass", []


def run_py(src, tests):
    try:
        ast.parse(src)
    except SyntaxError as e:
        return "syntax", [str(e)]
    prog = src + "\nimport json,sys\nT=json.loads(sys.argv[1])\nprint(json.dumps([solve(*a) for a, _ in T]))\n"
    try:
        r = subprocess.run([sys.executable, "-c", prog, json.dumps(tests)], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "wrong", ["timeout"]
    if r.returncode:
        return "runtime", [r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "error"]
    got = json.loads(r.stdout)
    for (a, want), g in zip(tests, got):
        if g != want:
            return "wrong", [{"args": a, "expected": want, "got": g}]
    return "pass", []


def llm(prompt, grammar=None, n=256):
    body = {"prompt": prompt, "n_predict": n, "temperature": 0, "cache_prompt": True, "stop": ["<|im_end|>", "\n\n\n", "```\n"]}
    if grammar:
        body["grammar"] = grammar
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return r["content"], r.get("tokens_predicted", 0), time.time() - t


def chat(system, user):
    return "<|im_start|>system\n%s<|im_end|>\n<|im_start|>user\n%s<|im_end|>\n<|im_start|>assistant\n" % (system, user)


def clean(s):
    m = re.search(r"```[a-zA-Z]*\n(.*?)(```|$)", s, re.S)
    return (m.group(1) if m else s).strip() + "\n"


ONE_FUNC = gbnf().replace("root ::= ws func (ws func)* ws", "root ::= func")


def bench_llm(out):
    sysm = "You write code. Output only code, no explanations."
    modes = {}
    for mode in ("mash_free", "mash_grammar", "python"):
        res = []
        for t in TASKS:
            if mode == "python":
                u = "Write a Python function.\n%s\nSignature: %s:\nOutput only the function." % (t["desc"], t["py"])
            else:
                u = "%s\nTask: %s\nSignature: %s\nOutput only the function." % (SPEC, t["desc"], t["sig"])
            prompt = chat(sysm, u)
            text, ntok, dt = llm(prompt, ONE_FUNC if mode == "mash_grammar" else None)
            code = clean(text)
            st, info = (run_py if mode == "python" else run_mash)(code, t["tests"])
            rep = None
            if st != "pass":
                fb = json.dumps(info, ensure_ascii=False)[:800]
                u2 = u + "\n\nYour previous answer:\n" + code + "\nIt failed with: " + fb + "\nFix it. Output only the function."
                text2, ntok2, dt2 = llm(chat(sysm, u2), ONE_FUNC if mode == "mash_grammar" else None)
                code2 = clean(text2)
                st2, _ = (run_py if mode == "python" else run_mash)(code2, t["tests"])
                rep = st2
            res.append({"task": t["id"], "status": st, "after_repair": rep or st, "gen_tokens": ntok, "seconds": round(dt, 1), "code": code})
            print(mode, t["id"], st, rep, flush=True)
        modes[mode] = res
    out["llm"] = modes


def bench_tokens(out):
    rows = []
    for t in TASKS:
        a, ia = run_mash(t["mash"], t["tests"])
        b, ib = run_py(t["pyref"], t["tests"])
        assert a == "pass", (t["id"], ia)
        assert b == "pass", (t["id"], ib)
        rows.append({"task": t["id"], "mash": toks(t["mash"]), "mash_compact": toks(compact(parse(t["mash"]))), "python": toks(t["pyref"]),
                     "mash_bytes": len(t["mash"].encode()), "python_bytes": len(t["pyref"].encode())})
    out["tokens"] = rows


def bench_predict(out):
    from test_core import gen_progs, norm
    total = same_vm = 0
    for t in TASKS:
        p = parse(t["mash"])
        for args, _ in t["tests"]:
            a = norm(execute_vm(p, "solve", args))
            b = norm(execute(p, "solve", args, live_io=False))
            total += 1
            same_vm += a == b
    progs = gen_progs(int(os.environ.get("BENCH_RANDOM", "300")), 90000)
    for p in progs:
        total += 1
        same_vm += norm(execute_vm(p)) == norm(execute(p))
    aot_total = aot_same = 0
    try:
        from test_backends import aot_run, aot_norm
        for p in progs[:40]:
            aot_total += 1
            aot_same += aot_norm(aot_run(p)) == norm(execute_vm(p))
    except Exception as e:
        out["aot_error"] = str(e)
    py = "s = {'alpha', 'beta', 'gamma', 'delta', 'eps'}\nprint(','.join(s))\n"
    outs = set()
    for seed in range(20):
        r = subprocess.run([sys.executable, "-c", py], capture_output=True, text=True, env=dict(os.environ, PYTHONHASHSEED=str(seed)))
        outs.add(r.stdout)
    out["predict"] = {"programs_runs": total, "predict_equals_run": same_vm, "aot_runs": aot_total, "aot_equals_predict": aot_same,
                      "python_set_iteration_distinct_outputs_over_20_hash_seeds": len(outs)}


def main():
    out = {}
    bench_tokens(out)
    bench_predict(out)
    if "--no-llm" not in sys.argv:
        bench_llm(out)
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), ensure_ascii=False, indent=1)
    summarize(out)


def summarize(out):
    lines = []
    T = out["tokens"]
    for enc in ("cl100k_base", "o200k_base"):
        m = sum(r["mash"][enc] for r in T); c = sum(r["mash_compact"][enc] for r in T); p = sum(r["python"][enc] for r in T)
        lines.append("tokens %s: mash canonical %d (%.2fx), mash compact %d (%.2fx), python %d" % (enc, m, m / p, c, c / p, p))
    lines.append("bytes: mash %d, python %d" % (sum(r["mash_bytes"] for r in T), sum(r["python_bytes"] for r in T)))
    pr = out["predict"]
    lines.append("predict==run: %d/%d; aot==predict: %d/%d; python set-order distinct outputs over 20 seeds: %d" % (
        pr["predict_equals_run"], pr["programs_runs"], pr["aot_equals_predict"], pr["aot_runs"], pr["python_set_iteration_distinct_outputs_over_20_hash_seeds"]))
    for mode, res in out.get("llm", {}).items():
        n = len(res)
        c = lambda k, v: sum(1 for r in res if r[k] == v)
        valid = sum(1 for r in res if r["status"] not in ("syntax",))
        typed = sum(1 for r in res if r["status"] not in ("syntax", "type"))
        lines.append("%s: syntax-valid %d/%d, type-valid %d/%d, pass %d/%d, pass after 1 repair %d/%d, gen tokens %d" % (
            mode, valid, n, typed, n, c("status", "pass"), n, c("after_repair", "pass"), n, sum(r["gen_tokens"] for r in res)))
    s = "\n".join(lines)
    open(os.path.join(HERE, "results.txt"), "w").write(s + "\n")
    print(s)


if __name__ == "__main__":
    main()
