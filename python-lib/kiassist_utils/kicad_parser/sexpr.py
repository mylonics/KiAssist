"""Generic S-expression tokenizer, parser, and serializer for KiCad files.

KiCad file format notes (v6+):
  - All user-visible strings are double-quoted.
  - Escape sequences: \\\\, \\", \\n, \\t.
  - Numbers use '.' as decimal separator.
  - Schematic / symbol coordinates: 4 decimal places.
  - PCB / footprint coordinates: 6 decimal places.
  - Unquoted atoms are keywords / boolean flags: yes, no, default, italic …

The parser returns a nested list where each S-expression becomes a Python list:

    (wire (pts (xy 0 0) (xy 2.54 0)))

becomes::

    ["wire", ["pts", ["xy", 0.0, 0.0], ["xy", 2.54, 0.0]]]

Quoted strings are returned as :class:`QStr` instances so the serializer can
distinguish them from unquoted identifiers when writing back.
"""

from __future__ import annotations

from typing import Iterator, List, Union


class QStr(str):
    """A string that was originally double-quoted in the S-expression source.

    Serialized back with surrounding double-quotes and proper escaping.
    """


# Public type alias for an S-expression node.
SExpr = Union["QStr", str, int, float, List]

# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    '"': '"',
    "\\": "\\",
    "r": "\r",
}


def _tokenize(text: str) -> Iterator[Union[QStr, str, int, float]]:
    """Yield atomic tokens from *text*.

    Token types (by Python type / class):
      * ``QStr``  — a double-quoted string
      * ``str``   — an unquoted identifier / keyword (e.g. ``yes``, ``no``)
      * ``int``   — an integer literal
      * ``float`` — a floating-point literal
      * ``"("``   — open parenthesis (plain str)
      * ``")"``   — close parenthesis (plain str)
    """
    i = 0
    n = len(text)
    while i < n:
        c = text[i]

        # Whitespace
        if c in " \t\n\r":
            i += 1
            continue

        # Parentheses
        if c == "(":
            yield "("
            i += 1
            continue
        if c == ")":
            yield ")"
            i += 1
            continue

        # Double-quoted string
        if c == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    esc_char = text[i + 1]
                    buf.append(_ESCAPES.get(esc_char, "\\" + esc_char))
                    i += 2
                elif ch == '"':
                    i += 1
                    break
                else:
                    buf.append(ch)
                    i += 1
            yield QStr("".join(buf))
            continue

        # Unquoted atom (identifier or number)
        j = i
        while j < n and text[j] not in " \t\n\r()\"":
            j += 1
        atom = text[i:j]
        i = j

        # Try to interpret as number
        try:
            if "." in atom or (
                "e" in atom.lower() and atom.lower().lstrip("-").replace("e", "", 1).replace(".", "", 1).isdigit()
            ):
                yield float(atom)
            else:
                yield int(atom)
        except ValueError:
            yield atom  # plain str — unquoted identifier


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse(text: str) -> List[SExpr]:
    """Parse an S-expression string and return the root list.

    Args:
        text: Full content of a KiCad file or any S-expression string.

    Returns:
        A nested list representing the S-expression tree.  The first element
        of each list is always the tag string (e.g. ``"kicad_sch"``).

    Raises:
        ValueError: If the input is not a valid S-expression.
    """
    tokens = list(_tokenize(text))
    pos = [0]

    def _parse_one() -> SExpr:
        if pos[0] >= len(tokens):
            raise ValueError("Unexpected end of S-expression input")
        tok = tokens[pos[0]]
        if tok == "(":
            pos[0] += 1
            result: List[SExpr] = []
            while pos[0] < len(tokens):
                if tokens[pos[0]] == ")":
                    pos[0] += 1
                    return result
                result.append(_parse_one())
            raise ValueError("Missing closing parenthesis in S-expression")
        if tok == ")":
            raise ValueError("Unexpected closing parenthesis")
        pos[0] += 1
        return tok

    result = _parse_one()
    if pos[0] < len(tokens):
        raise ValueError(
            f"Unexpected token after end of S-expression: {tokens[pos[0]]!r}"
        )
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Serialiser
# ---------------------------------------------------------------------------

_NEEDS_QUOTING_CHARS = set(" \t\n\r\"()\\")


def _needs_quoting(s: str) -> bool:
    """Return True if *s* must be surrounded by double-quotes."""
    if not s:
        return True
    return any(ch in _NEEDS_QUOTING_CHARS for ch in s)


def _fmt_atom(value: SExpr, precision: int) -> str:
    """Format a scalar atom as a string token."""
    if isinstance(value, QStr):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'
    if isinstance(value, float):
        raw = f"{value:.{precision}f}"
        # Strip trailing zeros but keep at least one digit after the dot
        if "." in raw:
            raw = raw.rstrip("0")
            if raw.endswith("."):
                raw += "0"
        return raw
    if isinstance(value, int):
        return str(value)
    # Plain str (unquoted identifier)
    if _needs_quoting(value):
        # Fallback: quote it so the file stays valid
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def serialize(tree: SExpr, indent: int = 0, number_precision: int = 4) -> str:
    """Serialise a nested list back to a formatted S-expression string.

    Args:
        tree:             The S-expression tree (as returned by :func:`parse`).
        indent:           Current indentation level in spaces (used internally
                          for recursive calls).
        number_precision: Decimal places for floating-point numbers.  Use 4
                          for schematic/symbol files and 6 for PCB/footprint
                          files.

    Returns:
        A properly indented S-expression string.
    """
    if not isinstance(tree, list):
        return _fmt_atom(tree, number_precision)

    if not tree:
        return "()"

    # Collect inline prefix: tag + leading non-list atoms
    inline: list[str] = []
    i = 0
    while i < len(tree) and not isinstance(tree[i], list):
        inline.append(_fmt_atom(tree[i], number_precision))
        i += 1

    prefix = "(" + " ".join(inline)

    if i >= len(tree):
        # No child lists — everything fits on one line
        return prefix + ")"

    # There are child lists.  First try a single-line compact form.
    child_parts = [serialize(tree[j], 0, number_precision) for j in range(i, len(tree))]
    single = prefix + " " + " ".join(child_parts) + ")"
    if len(single) + indent <= 120 and "\n" not in single:
        return single

    # Multi-line form: each child list on its own indented line.
    child_indent = indent + 2
    pad = " " * child_indent
    child_lines = [pad + serialize(tree[j], child_indent, number_precision) for j in range(i, len(tree))]
    close_pad = " " * indent
    return prefix + "\n" + "\n".join(child_lines) + "\n" + close_pad + ")"
