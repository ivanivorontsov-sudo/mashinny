from .types import BUILTIN_NAMES

GBNF = r'''root ::= ws func (ws func)* ws
func ::= "fn" sp ident ws "(" ws params? ws ")" ws "->" ws type (ws "!io")? (sp "pre" sp expr)? (sp "post" sp expr)? (sp "dec" sp expr)? ws "=" ws expr
params ::= param (ws "," ws param)*
param ::= ident ws ":" ws type
type ::= "Int" | "Bool" | "Str" | "Unit" | "Ui" | "[" ws type ws "]"
expr ::= let | if | lam | or
let ::= "let" sp ident ws "=" ws expr ws ";" ws expr
if ::= "if" sp expr sp "then" sp expr sp "else" sp expr
lam ::= "|" ws params ws "|" ws expr
or ::= and (ws "||" ws and)*
and ::= cmp (ws "&&" ws cmp)*
cmp ::= cat (ws cmpop ws cat)*
cmpop ::= "==" | "!=" | "<=" | ">=" | "<" | ">"
cat ::= add (ws "++" ws add)*
add ::= mul (ws [-+] ws mul)*
mul ::= un (ws [*/%] ws un)*
un ::= [-!] ws un | post
post ::= atom (ws "[" ws expr ws "]")*
atom ::= int | str | "true" | "false" | "unit" | "(" ws expr ws ")" | "[" ws (expr (ws "," ws expr)*)? ws "]" | ident ws "(" ws (expr (ws "," ws expr)*)? ws ")" | ident
int ::= "0" | [1-9] [0-9]{0,30}
str ::= "\"" ([^"\\\n] | "\\" ["\\nt])* "\""
ident ::= [A-Za-z_] [A-Za-z0-9_]{0,30}
ws ::= [ \t\n]{0,12}
sp ::= [ \t\n]{1,12}
'''


def gbnf():
    return GBNF


def _ref(n):
    return {"$ref": "#/$defs/" + n}


def json_schema():
    e = _ref("expr")
    t = _ref("type")
    node = lambda tag, props, req: {"type": "object", "properties": dict({"k": {"const": tag}}, **props), "required": ["k"] + req, "additionalProperties": False}
    ident = {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}
    param = {"type": "array", "prefixItems": [ident, t], "minItems": 2, "maxItems": 2}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "mashinny program AST",
        "type": "object",
        "properties": {"funcs": {"type": "array", "items": _ref("func")}},
        "required": ["funcs"],
        "additionalProperties": False,
        "$defs": {
            "type": {"anyOf": [{"enum": ["Int", "Bool", "Str", "Unit", "Ui"]}, {"type": "object", "properties": {"list": t}, "required": ["list"], "additionalProperties": False}]},
            "func": {"type": "object", "properties": {"name": ident, "params": {"type": "array", "items": param}, "ret": t,
                                                       "effects": {"type": "array", "items": {"enum": ["io"]}, "uniqueItems": True},
                                                       "pre": e, "post": e, "dec": e, "body": e},
                     "required": ["name", "params", "ret", "effects", "body"], "additionalProperties": False},
            "expr": {"anyOf": [
                node("int", {"v": {"type": "integer"}}, ["v"]),
                node("bool", {"v": {"type": "boolean"}}, ["v"]),
                node("str", {"v": {"type": "string"}}, ["v"]),
                node("unit", {}, []),
                node("var", {"name": ident}, ["name"]),
                node("call", {"fn": ident, "args": {"type": "array", "items": e}}, ["fn", "args"]),
                node("bin", {"op": {"enum": ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "&&", "||", "++"]}, "a": e, "b": e}, ["op", "a", "b"]),
                node("un", {"op": {"enum": ["-", "!"]}, "a": e}, ["op", "a"]),
                node("if", {"c": e, "t": e, "e": e}, ["c", "t", "e"]),
                node("let", {"name": ident, "val": e, "body": e}, ["name", "val", "body"]),
                node("list", {"items": {"type": "array", "items": e}}, ["items"]),
                node("lam", {"params": {"type": "array", "items": param, "minItems": 1}, "body": e}, ["params", "body"]),
            ]},
        },
        "x-builtins": BUILTIN_NAMES,
    }
