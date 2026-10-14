"""Shared helpers: load the framework, and write or read score records. Stdlib only.

A score record is written twice with the same content: `<name>.yaml` for people to read and
`<name>.json` beside it for the scripts. Python has no YAML parser in its standard library,
so the scripts never read the YAML file.
"""
import json
import re
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent.parent / "references" / "framework.json"
PRACTICES_PHASE = "agentic-ai-practices"


def load_framework():
    return json.loads(FRAMEWORK.read_text(encoding="utf-8"))


def competencies(framework):
    """[{id, phase, name}] in framework order."""
    return [{"id": c["id"], "phase": p["id"], "name": c["name"]} for p in framework["phases"] for c in p["competencies"]]


# ---- YAML writer (block style, for the records this skill produces) ----

_PLAIN = re.compile(r"^[A-Za-z0-9(/~_][^\n]*$")
_RESERVED = re.compile(r"^(true|false|yes|no|on|off|null|~|y|n)$", re.I)
_NUMBER = re.compile(r"^[-+]?(\.?\d[\d_.eE+-]*|\.inf|\.nan|0x[0-9a-fA-F]+|0o[0-7]+)$")


def _scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    plain = (_PLAIN.match(s) and not _RESERVED.match(s) and not _NUMBER.match(s)
             and ": " not in s and " #" not in s and not s.endswith((" ", ":")))
    return s if plain else json.dumps(s, ensure_ascii=False)


def _emit(v, indent, out):
    pad = "  " * indent
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x:
                out.append(f"{pad}{_scalar(k)}:")
                _emit(x, indent + (1 if isinstance(x, dict) else 0), out)
            else:
                out.append(f"{pad}{_scalar(k)}: {_empty(x)}")
    else:
        for x in v:
            if isinstance(x, dict) and x:
                sub = []
                _emit(x, indent + 1, sub)
                out.append(f"{pad}- {sub[0].lstrip()}")
                out.extend(sub[1:])
            elif isinstance(x, list) and x:
                out.append(f"{pad}-")
                _emit(x, indent + 1, out)
            else:
                out.append(f"{pad}- {_empty(x)}")


def _empty(x):
    if isinstance(x, dict):
        return "{}"
    if isinstance(x, list):
        return "[]"
    return _scalar(x)


def to_yaml(data):
    out = []
    _emit(data, 0, out)
    return "\n".join(out) + "\n"


def write_record(record, path):
    path = Path(path)
    path.write_text(to_yaml(record), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_record(path):
    """Read a record from its .json file. A .yaml path is read from the .json beside it."""
    path = Path(path)
    if path.suffix != ".json":
        path = path.with_suffix(".json")
    if not path.is_file():
        raise SystemExit(f"no {path.name} beside the record: score_facets.py writes it with the YAML")
    return json.loads(path.read_text(encoding="utf-8"))
