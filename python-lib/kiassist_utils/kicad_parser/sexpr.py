"""S-Expression tokenizer and serializer for KiCad files.

Parses KiCad S-expressions into nested Python lists and serializes them back
with correct formatting. Designed for round-trip fidelity: parse -> modify -> write
should produce minimal diffs against the original file.

KiCad S-expression rules:
- All strings are double-quoted in v6+ (some legacy unquoted atoms exist)
- Numbers can be integers or floats
- Escape sequences in strings: \\n, \\\\, \\"
- Comments are not used in KiCad files
- Whitespace is significant only as a delimiter
"""

from typing import List, Union, TextIO, Optional
import io
import os

# Type alias for the parsed S-expression tree
SExpr = Union[str, int, float, List["SExpr"]]

# Characters that require quoting in KiCad S-expressions
_NEEDS_QUOTE_CHARS = set(' \t\n\r"\\(){}')

# Keywords that KiCad writes without quotes
_KICAD_BARE_KEYWORDS = frozenset({
    "yes", "no", "true", "false", "none",
    "left", "right", "top", "bottom", "center",
    "mirror", "portrait", "landscape",
    "input", "output", "bidirectional", "tri_state", "passive",
    "free", "unspecified", "power_in", "power_out", "open_collector",
    "open_emitter", "unconnected", "no_connect",
    "line", "inverted", "clock", "inverted_clock", "input_low",
    "clock_low", "output_low", "edge_clock_high", "non_logic",
    "smd", "thru_hole", "connect", "np_thru_hole",
    "circle", "rect", "oval", "trapezoid", "roundrect", "custom",
    "solid", "dash", "dot", "dash_dot", "dash_dot_dot", "default",
    "F.Cu", "B.Cu", "F.Paste", "B.Paste", "F.SilkS", "B.SilkS",
    "F.Mask", "B.Mask", "F.Fab", "B.Fab", "F.CrtYd", "B.CrtYd",
    "Edge.Cuts", "Margin", "Dwgs.User", "Cmts.User", "Eco1.User", "Eco2.User",
    "*.Cu", "*.Paste", "*.Mask", "*.SilkS",
    "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu",
})


class ParseError(Exception):
    """Raised when S-expression parsing encounters invalid syntax."""

    def __init__(self, message: str, position: int = -1, line: int = -1,
                 col: int = -1):
        self.position = position
        self.line = line
        self.col = col
        if line >= 0 and col >= 0:
            message = f"{message} (line {line}, col {col})"
        elif position >= 0:
            message = f"{message} (position {position})"
        super().__init__(message)


class _Tokenizer:
    """Low-level tokenizer for KiCad S-expressions.

    Converts raw text into a stream of tokens: '(', ')', strings, numbers.
    """

    def __init__(self, text: str):
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._line = 1
        self._col = 1

    @property
    def position(self) -> int:
        return self._pos

    @property
    def line(self) -> int:
        return self._line

    @property
    def col(self) -> int:
        return self._col

    def _advance(self, n: int = 1) -> None:
        for _ in range(n):
            if self._pos < self._len:
                if self._text[self._pos] == '\n':
                    self._line += 1
                    self._col = 1
                else:
                    self._col += 1
                self._pos += 1

    def _skip_whitespace(self) -> None:
        while self._pos < self._len and self._text[self._pos] in ' \t\n\r':
            self._advance()

    def _read_quoted_string(self) -> str:
        """Read a double-quoted string, handling escape sequences."""
        assert self._text[self._pos] == '"'
        self._advance()  # skip opening quote
        result = []
        while self._pos < self._len:
            ch = self._text[self._pos]
            if ch == '\\':
                self._advance()
                if self._pos >= self._len:
                    raise ParseError("Unexpected end of input in escape sequence",
                                     self._pos, self._line, self._col)
                esc = self._text[self._pos]
                if esc == 'n':
                    result.append('\n')
                elif esc == 't':
                    result.append('\t')
                elif esc == '\\':
                    result.append('\\')
                elif esc == '"':
                    result.append('"')
                else:
                    # Unknown escape: keep as-is
                    result.append('\\')
                    result.append(esc)
                self._advance()
            elif ch == '"':
                self._advance()  # skip closing quote
                return ''.join(result)
            else:
                result.append(ch)
                self._advance()
        raise ParseError("Unterminated string", self._pos, self._line, self._col)

    def _read_atom(self) -> Union[str, int, float]:
        """Read an unquoted atom (keyword, number, or bare string)."""
        start = self._pos
        while self._pos < self._len:
            ch = self._text[self._pos]
            if ch in ' \t\n\r()':
                break
            self._advance()
        token = self._text[start:self._pos]
        if not token:
            raise ParseError("Empty atom", start, self._line, self._col)
        return _parse_atom(token)

    def next_token(self) -> Optional[Union[str, int, float, tuple]]:
        """Return the next token, or None at end of input.

        Returns:
            - ('(', ) tuple for open paren
            - (')', ) tuple for close paren
            - str, int, or float for atoms
            - None at EOF
        """
        self._skip_whitespace()
        if self._pos >= self._len:
            return None

        ch = self._text[self._pos]
        if ch == '(':
            self._advance()
            return ('(',)
        elif ch == ')':
            self._advance()
            return (')',)
        elif ch == '"':
            return self._read_quoted_string()
        else:
            return self._read_atom()


def _parse_atom(token: str) -> Union[str, int, float]:
    """Parse an unquoted atom into the appropriate Python type."""
    # Try integer first
    try:
        return int(token)
    except ValueError:
        pass
    # Try float
    try:
        return float(token)
    except ValueError:
        pass
    # It's a bare string/keyword
    return token


def parse(text: str) -> SExpr:
    """Parse a KiCad S-expression string into a nested Python structure.

    Args:
        text: The S-expression text to parse.

    Returns:
        A nested list structure where each S-expression list becomes a Python list,
        and atoms become str, int, or float values.

    Raises:
        ParseError: If the input contains invalid syntax.

    Example:
        >>> parse('(kicad_sch (version 20240215) (generator "eeschema"))')
        ['kicad_sch', ['version', 20240215], ['generator', 'eeschema']]
    """
    tokenizer = _Tokenizer(text)
    result = _parse_expr(tokenizer)
    # Check for trailing content
    tokenizer._skip_whitespace()
    if tokenizer.position < len(text):
        raise ParseError("Unexpected content after top-level expression",
                         tokenizer.position, tokenizer.line, tokenizer.col)
    return result


def parse_multi(text: str) -> List[SExpr]:
    """Parse text containing multiple top-level S-expressions.

    Args:
        text: The S-expression text to parse.

    Returns:
        A list of parsed S-expressions.
    """
    tokenizer = _Tokenizer(text)
    results = []
    while True:
        tokenizer._skip_whitespace()
        if tokenizer.position >= len(text):
            break
        results.append(_parse_expr(tokenizer))
    return results


def _parse_expr(tokenizer: _Tokenizer) -> SExpr:
    """Parse a single S-expression from the tokenizer."""
    token = tokenizer.next_token()
    if token is None:
        raise ParseError("Unexpected end of input",
                         tokenizer.position, tokenizer.line, tokenizer.col)
    if isinstance(token, tuple):
        if token[0] == '(':
            return _parse_list(tokenizer)
        elif token[0] == ')':
            raise ParseError("Unexpected closing parenthesis",
                             tokenizer.position, tokenizer.line, tokenizer.col)
    # Atom (str, int, float)
    return token


def _parse_list(tokenizer: _Tokenizer) -> List[SExpr]:
    """Parse the contents of a list (after the opening paren)."""
    items = []
    while True:
        tokenizer._skip_whitespace()
        if tokenizer.position >= len(tokenizer._text):
            raise ParseError("Unterminated list",
                             tokenizer.position, tokenizer.line, tokenizer.col)

        # Peek at the next character
        ch = tokenizer._text[tokenizer.position]
        if ch == ')':
            tokenizer._advance()
            return items
        items.append(_parse_expr(tokenizer))


def parse_file(path: str) -> SExpr:
    """Parse a KiCad file into an S-expression tree.

    Args:
        path: Path to the KiCad file.

    Returns:
        Parsed S-expression tree.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ParseError: If the file contains invalid syntax.
    """
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    return parse(text)


def serialize(expr: SExpr, precision: int = 4, indent: int = 0,
              indent_str: str = "\t") -> str:
    """Serialize a parsed S-expression tree back to text.

    Args:
        expr: The S-expression tree to serialize.
        precision: Number of decimal places for floats
                   (4 for schematic/symbol, 6 for PCB).
        indent: Starting indentation level.
        indent_str: String used for each level of indentation (default: tab).

    Returns:
        The serialized S-expression text.
    """
    buf = io.StringIO()
    _serialize_to_buf(expr, buf, precision, indent, indent_str, is_top=True)
    result = buf.getvalue()
    # Ensure trailing newline for file output
    if result and not result.endswith('\n'):
        result += '\n'
    return result


def serialize_to_file(expr: SExpr, path: str, precision: int = 4,
                      indent_str: str = "\t") -> None:
    """Serialize an S-expression tree and write it to a file.

    Args:
        expr: The S-expression tree to serialize.
        path: Output file path.
        precision: Number of decimal places for floats.
        indent_str: String used for each level of indentation.
    """
    text = serialize(expr, precision=precision, indent_str=indent_str)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _serialize_to_buf(expr: SExpr, buf: TextIO, precision: int,
                      indent: int, indent_str: str,
                      is_top: bool = False) -> None:
    """Recursively serialize an S-expression to a buffer."""
    if isinstance(expr, list):
        _serialize_list(expr, buf, precision, indent, indent_str, is_top)
    elif isinstance(expr, float):
        buf.write(_format_float(expr, precision))
    elif isinstance(expr, int):
        buf.write(str(expr))
    elif isinstance(expr, str):
        buf.write(_quote_string(expr))
    else:
        buf.write(str(expr))


def _serialize_list(items: List[SExpr], buf: TextIO, precision: int,
                    indent: int, indent_str: str,
                    is_top: bool = False) -> None:
    """Serialize a list S-expression with KiCad-compatible formatting."""
    if not items:
        buf.write("()")
        return

    # Determine if this list should be written on a single line or multi-line
    tag = items[0] if items and isinstance(items[0], str) else None
    has_sublists = any(isinstance(item, list) for item in items[1:])
    is_simple = not has_sublists and len(items) <= 6

    # Single-line simple expressions (like (version 20240215) or (at 100 50 0))
    if is_simple and not is_top:
        buf.write("(")
        for i, item in enumerate(items):
            if i > 0:
                buf.write(" ")
            _serialize_to_buf(item, buf, precision, indent, indent_str)
        buf.write(")")
        return

    # Multi-line complex expressions
    prefix = indent_str * indent
    child_prefix = indent_str * (indent + 1)

    buf.write("(")
    # Write the tag and any simple leading atoms on the first line
    first_line_items = []
    rest_items = []
    collecting_first_line = True
    for i, item in enumerate(items):
        if collecting_first_line and not isinstance(item, list):
            first_line_items.append(item)
        else:
            collecting_first_line = False
            rest_items.append(item)

    for i, item in enumerate(first_line_items):
        if i > 0:
            buf.write(" ")
        _serialize_to_buf(item, buf, precision, indent, indent_str)

    for item in rest_items:
        buf.write("\n")
        buf.write(child_prefix)
        _serialize_to_buf(item, buf, precision, indent + 1, indent_str)

    if rest_items:
        buf.write("\n")
        buf.write(prefix)
    buf.write(")")


def _format_float(value: float, precision: int) -> str:
    """Format a float with the specified precision, removing trailing zeros.

    KiCad uses fixed precision but strips unnecessary trailing zeros.
    """
    formatted = f"{value:.{precision}f}"
    # Remove trailing zeros after decimal point, but keep at least one decimal
    if '.' in formatted:
        formatted = formatted.rstrip('0')
        if formatted.endswith('.'):
            formatted += '0'
    return formatted


def _quote_string(s: str) -> str:
    """Quote a string for KiCad S-expression output.

    Strings that are valid bare atoms (no spaces, parens, quotes)
    are written unquoted. All others are double-quoted with escaping.
    """
    if not s:
        return '""'

    # Check if it can be written as a bare atom
    needs_quote = False
    for ch in s:
        if ch in _NEEDS_QUOTE_CHARS:
            needs_quote = True
            break

    # Also check if it looks like a number (would be ambiguous without quotes)
    if not needs_quote:
        try:
            int(s)
            needs_quote = True
        except ValueError:
            try:
                float(s)
                needs_quote = True
            except ValueError:
                pass

    if not needs_quote:
        return s

    # Escape and quote
    escaped = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


# Utility functions for working with parsed S-expressions

def find_all(expr: SExpr, tag: str) -> List[List[SExpr]]:
    """Find all sublists in an S-expression that start with the given tag.

    Args:
        expr: The S-expression tree to search.
        tag: The tag name to search for.

    Returns:
        List of matching sublists.

    Example:
        >>> tree = parse('(root (wire (pts (xy 0 0))) (wire (pts (xy 1 1))))')
        >>> find_all(tree, 'wire')
        [['wire', ['pts', ['xy', 0, 0]]], ['wire', ['pts', ['xy', 1, 1]]]]
    """
    results = []
    if isinstance(expr, list):
        if len(expr) > 0 and isinstance(expr[0], str) and expr[0] == tag:
            results.append(expr)
        for item in expr:
            if isinstance(item, list):
                results.extend(find_all(item, tag))
    return results


def find_first(expr: SExpr, tag: str) -> Optional[List[SExpr]]:
    """Find the first sublist with the given tag.

    Args:
        expr: The S-expression tree to search.
        tag: The tag name to search for.

    Returns:
        The first matching sublist, or None.
    """
    if isinstance(expr, list):
        if len(expr) > 0 and isinstance(expr[0], str) and expr[0] == tag:
            return expr
        for item in expr:
            if isinstance(item, list):
                result = find_first(item, tag)
                if result is not None:
                    return result
    return None


def get_value(expr: List[SExpr], tag: str,
              default: Optional[SExpr] = None) -> Optional[SExpr]:
    """Get the value (second element) of a tagged sublist.

    Searches immediate children of `expr` for a sublist starting with `tag`
    and returns the second element.

    Args:
        expr: Parent S-expression list.
        tag: Tag to search for.
        default: Value to return if not found.

    Returns:
        The value (second element) of the matched sublist, or default.

    Example:
        >>> tree = ['kicad_sch', ['version', 20240215], ['generator', 'eeschema']]
        >>> get_value(tree, 'version')
        20240215
    """
    if not isinstance(expr, list):
        return default
    for item in expr:
        if (isinstance(item, list) and len(item) >= 2 and
                isinstance(item[0], str) and item[0] == tag):
            return item[1]
    return default


def set_value(expr: List[SExpr], tag: str, value: SExpr) -> None:
    """Set the value (second element) of a tagged sublist.

    If the tag exists, updates the value. If not, appends a new sublist.

    Args:
        expr: Parent S-expression list.
        tag: Tag to set.
        value: New value.
    """
    for item in expr:
        if (isinstance(item, list) and len(item) >= 2 and
                isinstance(item[0], str) and item[0] == tag):
            item[1] = value
            return
    expr.append([tag, value])


def remove_by_tag(expr: List[SExpr], tag: str, first_only: bool = False) -> int:
    """Remove sublists with the given tag from a parent expression.

    Args:
        expr: Parent S-expression list.
        tag: Tag to remove.
        first_only: If True, remove only the first match.

    Returns:
        Number of items removed.
    """
    to_remove = []
    for i, item in enumerate(expr):
        if (isinstance(item, list) and len(item) > 0 and
                isinstance(item[0], str) and item[0] == tag):
            to_remove.append(i)
            if first_only:
                break
    for i in reversed(to_remove):
        del expr[i]
    return len(to_remove)
