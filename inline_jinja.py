"""Expand room.jinja macros into inline Jinja for template entity state/availability.

Home Assistant's template integration evaluates entity state in a context where
{% from 'room.jinja' import ... %} does not run (Developer Tools | Template still
works). Generated YAML must contain the macro body inline, not an import.
"""

from __future__ import annotations

import re
from pathlib import Path

_MACRO_RE = re.compile(
    r"\{%-?\s*macro\s+(\w+)\s*\((.*?)\)\s*-?%\}(.*?)\{%-?\s*endmacro\s*-?%\}",
    re.DOTALL,
)
_JOIN_NAMES_START = re.compile(r"\{\{-?\s*join_names\(")
_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_NESTED_MACROS = ("convert_to_canonical",)

ROOM_JINJA = Path(__file__).resolve().parent / "room.jinja"


def jinja_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def load_macros(path: Path = ROOM_JINJA) -> dict[str, tuple[list[str], str]]:
    text = path.read_text(encoding="utf-8")
    macros: dict[str, tuple[list[str], str]] = {}
    for name, params, body in _MACRO_RE.findall(text):
        param_list = [p.strip() for p in params.split(",") if p.strip()]
        macros[name] = (param_list, body.strip())
    return macros


def _join_names_inline(ids_expr: str, join_body: str) -> str:
    """Inline join_names macro body; use a private namespace to avoid clashing with caller `ns`."""
    expanded = re.sub(r"\bentity_ids\b", ids_expr, join_body)
    expanded = expanded.replace("ns = namespace(names=[])", "_jn_ns = namespace(_jn_names=[])")
    expanded = expanded.replace("ns.names", "_jn_ns._jn_names")
    return expanded.strip()


def _macro_call_start(name: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(name)}\s*\(")


def _balanced_close_index(body: str, arg_start: int) -> int:
    """Index after closing ')' for call that opened at arg_start - 1."""
    depth = 1
    j = arg_start
    while j < len(body) and depth:
        if body[j] == "(":
            depth += 1
        elif body[j] == ")":
            depth -= 1
        j += 1
    if depth:
        raise ValueError(f"Unbalanced parentheses near: {body[arg_start : arg_start + 40]!r}")
    return j


def _split_top_level_args(arg_str: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in arg_str:
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _strip_expression_output(body: str) -> str:
    """Macro bodies that are a single {{ expr }} become inline expressions."""
    stripped = body.strip()
    m = re.fullmatch(r"\{\{-?\s*(.*?)\s*-?\}\}", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()
    return stripped


def _inline_macro_call(name: str, arg_str: str, macros: dict[str, tuple[list[str], str]]) -> str:
    param_list, macro_body = macros[name]
    call_args = _split_top_level_args(arg_str)
    expanded = _COMMENT_RE.sub("", macro_body)
    for i, param in enumerate(param_list):
        if i < len(call_args):
            expanded = re.sub(rf"\b{re.escape(param)}\b", call_args[i], expanded)
    expanded = _strip_expression_output(expanded)
    if name in _NESTED_MACROS:
        return f"({expanded})"
    return expanded.strip()


def _expand_nested_macros(body: str, macros: dict[str, tuple[list[str], str]]) -> str:
    """Inline helper macros (e.g. convert_to_canonical) used inside aggregation macros."""
    for name in _NESTED_MACROS:
        if name not in macros:
            continue
        pattern = _macro_call_start(name)
        out: list[str] = []
        i = 0
        while i < len(body):
            match = pattern.search(body, i)
            if not match:
                out.append(body[i:])
                break
            out.append(body[i : match.start()])
            arg_start = match.end()
            arg_str = body[arg_start : _balanced_close_index(body, arg_start) - 1].strip()
            out.append(_inline_macro_call(name, arg_str, macros))
            i = _balanced_close_index(body, arg_start)
        body = "".join(out)
    return body


def _rewrite_join_names(body: str, replace: callable) -> str:
    """Walk every {{ join_names(...) }} call and replace it via `replace(ids_expr)`.

    Handles nested parentheses in the argument expression.
    """
    out: list[str] = []
    i = 0
    while i < len(body):
        match = _JOIN_NAMES_START.search(body, i)
        if not match:
            out.append(body[i:])
            break
        out.append(body[i : match.start()])
        arg_start = match.end()  # position after opening '('
        depth = 1
        j = arg_start
        while j < len(body) and depth:
            if body[j] == "(":
                depth += 1
            elif body[j] == ")":
                depth -= 1
            j += 1
        if depth:
            raise ValueError("Unbalanced parentheses in join_names call")
        ids_expr = body[arg_start : j - 1].strip()
        close = re.match(r"\s*-?\s*\}\}", body[j:])
        if not close:
            raise ValueError(
                f"Expected closing }} after join_names(...) near: {body[j : j + 20]!r}"
            )
        j += close.end()
        out.append(replace(ids_expr))
        i = j
    return "".join(out)


def _expand_join_names(body: str, macros: dict[str, tuple[list[str], str]]) -> str:
    """Replace every {{ join_names(...) }} call, including nested parentheses in the argument."""
    _, join_body = macros["join_names"]
    return _rewrite_join_names(body, lambda ids: _join_names_inline(ids, join_body))


def _count_join_names(body: str) -> str:
    """Replace every {{ join_names(IDS) }} with {{ (IDS) | length }}.

    Used to derive a numeric "how many entities matched" template from any
    existing list macro, so the entity state stays short enough to fit inside
    Home Assistant's 255-character state limit.
    """
    return _rewrite_join_names(body, lambda ids: f"{{{{ ({ids}) | length }}}}")


def _bind_and_flatten(body: str, param_list: list[str], room: str, args: list[str]) -> str:
    bindings: dict[str, str] = {}
    if param_list:
        bindings[param_list[0]] = jinja_str(room)
    for i, arg in enumerate(args):
        if i + 1 < len(param_list):
            bindings[param_list[i + 1]] = jinja_str(arg)
    for param in sorted(bindings, key=len, reverse=True):
        body = re.sub(rf"\b{re.escape(param)}\b", bindings[param], body)
    return "".join(line.strip() for line in body.splitlines() if line.strip())


def expand_macro(macro: str, room: str, args: list[str], *, macros: dict | None = None) -> str:
    """Return inline Jinja for one room.jinja macro call (no {% from %} import)."""
    if macros is None:
        macros = load_macros()
    if macro not in macros:
        raise KeyError(f"Unknown macro {macro!r}")
    param_list, body = macros[macro]
    body = _expand_nested_macros(body, macros)
    body = _expand_join_names(body, macros)
    body = _COMMENT_RE.sub("", body)
    return _bind_and_flatten(body, param_list, room, args)


def render_entity_template(macro: str, room: str, args: list[str]) -> str:
    """Full state/availability template: inlined macro body for state-based templates."""
    return expand_macro(macro, room, args)


def render_count_template(
    macro: str, room: str, args: list[str], *, macros: dict | None = None
) -> str:
    """Inline a list macro but emit `{{ ids | length }}` in place of `join_names(ids)`.

    Companion text sensors would otherwise hit Home Assistant's 255-char state
    cap as soon as an area has many sources; using the count for state keeps
    the entity valid while the full list lives in an attribute.
    """
    if macros is None:
        macros = load_macros()
    if macro not in macros:
        raise KeyError(f"Unknown macro {macro!r}")
    param_list, body = macros[macro]
    body = _expand_nested_macros(body, macros)
    body = _count_join_names(body)
    body = _COMMENT_RE.sub("", body)
    return _bind_and_flatten(body, param_list, room, args)
