"""Python value -> Lua literal that holds only tables, strings, integers and booleans.

The bridge reads release batches with load(text, name, "t", {}) as pure data, so the text must never be able to
carry code: every string byte outside printable ASCII (and the quote and backslash) is written as a 3-digit decimal
escape, table keys are written bare (`batch_id = ...`) when they are plain identifiers and not Lua keywords, otherwise
as ["..."], and anything that is not dict / list / str / int / bool is refused.
"""
import re

KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if", "in", "local",
            "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

MAX_DEPTH = 12
MAX_INT = 2 ** 53


def _string(s):
    out = ['"']
    for b in s.encode("utf-8"):
        if b == 0x22:
            out.append('\\"')
        elif b == 0x5C:
            out.append("\\\\")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append("\\%03d" % b)
    out.append('"')
    return "".join(out)


def lua(value, _depth=0):
    if _depth > MAX_DEPTH:
        raise ValueError("lua literal nested too deeply")
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) >= MAX_INT:
            raise ValueError(f"integer out of range: {value}")
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(lua(v, _depth + 1) for v in value) + "}"
    if isinstance(value, dict):
        parts = []
        for k in sorted(value, key=lambda k: (not isinstance(k, str), str(k))):
            if not isinstance(k, str):
                raise ValueError(f"table key must be a string: {k!r}")
            key = k if _IDENT.match(k) and k not in KEYWORDS else f"[{_string(k)}]"
            parts.append(f"{key}={lua(value[k], _depth + 1)}")
        return "{" + ",".join(parts) + "}"
    raise ValueError(f"not representable as a Lua literal: {type(value).__name__}")


def chunk(value):
    """A loadable chunk: `return <literal>`."""
    return "return " + lua(value)
