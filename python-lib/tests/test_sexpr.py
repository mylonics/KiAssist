"""Tests for the S-expression tokenizer, parser, and serializer."""

from __future__ import annotations

import pytest

from kiassist_utils.kicad_parser.sexpr import QStr, parse, serialize


class TestTokenizerAndParser:
    """Tests for the parse() function."""

    def test_simple_list(self):
        """Parse a flat list with tag and atoms."""
        result = parse('(version 20231120)')
        assert result == ["version", 20231120]

    def test_quoted_string(self):
        """Quoted strings are returned as QStr instances."""
        result = parse('(generator "eeschema")')
        assert result[0] == "generator"
        assert isinstance(result[1], QStr)
        assert result[1] == "eeschema"

    def test_nested_list(self):
        """Parse nested S-expressions."""
        result = parse('(wire (pts (xy 0 0) (xy 2.54 0)))')
        assert result[0] == "wire"
        pts = result[1]
        assert pts[0] == "pts"
        assert pts[1] == ["xy", 0, 0]
        assert pts[2] == ["xy", 2.54, 0.0]

    def test_float_values(self):
        """Floating-point numbers are parsed as float."""
        result = parse('(at 78.74 50.8 90)')
        assert result[0] == "at"
        assert isinstance(result[1], float)
        assert result[1] == pytest.approx(78.74)
        assert result[2] == pytest.approx(50.8)
        assert result[3] == 90

    def test_integer_values(self):
        """Integer numbers are parsed as int."""
        result = parse('(version 20231120)')
        assert isinstance(result[1], int)
        assert result[1] == 20231120

    def test_unquoted_identifiers(self):
        """Unquoted identifiers are returned as plain str."""
        result = parse('(type default)')
        assert isinstance(result[1], str)
        assert not isinstance(result[1], QStr)
        assert result[1] == "default"

    def test_escape_sequences_in_string(self):
        """Escape sequences inside quoted strings are decoded."""
        result = parse(r'(text "line1\nline2")')
        assert result[1] == "line1\nline2"

    def test_escaped_quote_in_string(self):
        """Escaped double-quote inside a string is decoded."""
        result = parse(r'(text "say \"hello\"")')
        assert result[1] == 'say "hello"'

    def test_boolean_keywords(self):
        """Boolean keywords remain as plain str (not bool)."""
        result = parse('(in_bom yes)')
        assert result[1] == "yes"
        assert isinstance(result[1], str)
        assert not isinstance(result[1], bool)

    def test_negative_float(self):
        """Negative floating-point numbers are parsed correctly."""
        result = parse('(size -1.016 2.54)')
        assert result[1] == pytest.approx(-1.016)
        assert result[2] == pytest.approx(2.54)

    def test_empty_list(self):
        """An empty pair of parentheses is an empty list."""
        result = parse('()')
        assert result == []

    def test_deeply_nested(self):
        """Deeply nested structure is parsed correctly."""
        text = '(a (b (c (d 1))))'
        result = parse(text)
        assert result == ["a", ["b", ["c", ["d", 1]]]]

    def test_missing_close_paren_raises(self):
        """Missing closing parenthesis raises ValueError."""
        with pytest.raises(ValueError):
            parse("(version 1")

    def test_extra_close_paren_raises(self):
        """Unexpected closing parenthesis raises ValueError."""
        with pytest.raises(ValueError):
            parse("(version 1))")

    def test_multiline_input(self):
        """Whitespace including newlines is ignored."""
        text = """(kicad_sch
  (version 20231120)
  (generator "eeschema")
)"""
        result = parse(text)
        assert result[0] == "kicad_sch"
        assert result[1] == ["version", 20231120]
        assert result[2] == ["generator", QStr("eeschema")]

    def test_uuid_string_parsed_as_qstr(self):
        """UUID strings are parsed as QStr."""
        result = parse('(uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")')
        assert isinstance(result[1], QStr)
        assert result[1] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_multiple_children(self):
        """Lists with many children parse correctly."""
        text = '(color 0 0 0 0)'
        result = parse(text)
        assert result == ["color", 0, 0, 0, 0]


class TestSerialiser:
    """Tests for the serialize() function."""

    def test_simple_list_serialised(self):
        """Simple list is serialised to a single line."""
        tree = ["version", 20231120]
        assert serialize(tree) == "(version 20231120)"

    def test_qstr_is_quoted(self):
        """QStr values are wrapped in double quotes."""
        tree = ["generator", QStr("eeschema")]
        assert serialize(tree) == '(generator "eeschema")'

    def test_plain_str_is_unquoted(self):
        """Plain str values are written without quotes."""
        tree = ["type", "default"]
        assert serialize(tree) == "(type default)"

    def test_float_precision(self):
        """Float values use the requested decimal precision."""
        tree = ["at", 78.74, 50.8]
        result = serialize(tree, number_precision=4)
        assert result == "(at 78.74 50.8)"

    def test_float_trailing_zero(self):
        """Float values strip trailing zeros but keep at least one decimal."""
        tree = ["at", 0.0, 2.0]
        result = serialize(tree, number_precision=4)
        assert result == "(at 0.0 2.0)"

    def test_nested_short_list_single_line(self):
        """Short nested lists are kept on a single line."""
        tree = ["pts", ["xy", 0.0, 0.0], ["xy", 2.54, 0.0]]
        result = serialize(tree)
        assert "\n" not in result
        assert result == "(pts (xy 0.0 0.0) (xy 2.54 0.0))"

    def test_qstr_escape_sequences(self):
        """Special characters in QStr values are properly escaped."""
        tree = ["text", QStr('say "hello"')]
        result = serialize(tree)
        assert result == r'(text "say \"hello\"")'

    def test_qstr_newline_escaped(self):
        """Newline in QStr is escaped as \\n."""
        tree = ["text", QStr("line1\nline2")]
        result = serialize(tree)
        assert result == r'(text "line1\nline2")'

    def test_empty_list(self):
        """Empty list serialises to ()."""
        assert serialize([]) == "()"

    def test_boolean_keywords_unquoted(self):
        """Boolean keywords are not quoted."""
        tree = ["in_bom", "yes"]
        assert serialize(tree) == "(in_bom yes)"

    def test_negative_number(self):
        """Negative numbers are serialised correctly."""
        tree = ["size", -1.016, 2.54]
        result = serialize(tree, number_precision=4)
        assert result == "(size -1.016 2.54)"


class TestRoundTrip:
    """Round-trip tests: parse → serialize → parse → verify equality."""

    def _round_trip(self, text: str) -> list:
        tree = parse(text)
        serialised = serialize(tree)
        return parse(serialised)

    def test_simple_atom_round_trip(self):
        """Simple atom round-trips correctly."""
        tree = parse('(version 20231120)')
        assert self._round_trip('(version 20231120)') == tree

    def test_quoted_string_round_trip(self):
        """Quoted string round-trips with same QStr type."""
        original = parse('(generator "eeschema")')
        result = self._round_trip('(generator "eeschema")')
        assert result == original
        assert isinstance(result[1], QStr)

    def test_nested_round_trip(self):
        """Nested S-expression round-trips correctly."""
        text = '(wire (pts (xy 0 0) (xy 2.54 0)))'
        assert self._round_trip(text) == parse(text)

    def test_uuid_round_trip(self):
        """UUID strings survive a round-trip."""
        text = '(uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")'
        assert self._round_trip(text) == parse(text)

    def test_float_precision_round_trip(self):
        """Float values serialised with 4 d.p. round-trip correctly."""
        text = '(at 78.74 50.8 90)'
        tree = parse(text)
        serialised = serialize(tree, number_precision=4)
        result = parse(serialised)
        assert result[1] == pytest.approx(78.74)
        assert result[2] == pytest.approx(50.8)
        assert result[3] == 90

    def test_escape_round_trip(self):
        """Escape sequences survive a full round-trip."""
        text = r'(text "line1\nline2")'
        tree = parse(text)
        result = parse(serialize(tree))
        assert result[1] == "line1\nline2"

    def test_multiline_schematic_round_trip(self):
        """A small multi-line S-expression round-trips correctly."""
        text = """\
(kicad_sch (version 20231120) (generator "eeschema")
  (uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
  (paper "A4")
)"""
        original = parse(text)
        result = self._round_trip(text)
        assert result[0] == "kicad_sch"
        assert result[1] == ["version", 20231120]
        assert result[2][1] == "eeschema"
        # Check uuid and paper survived
        uuids = [item for item in result if isinstance(item, list) and item and item[0] == "uuid"]
        papers = [item for item in result if isinstance(item, list) and item and item[0] == "paper"]
        assert uuids and uuids[0][1] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert papers and papers[0][1] == "A4"

    def test_special_chars_in_string(self):
        """Strings with special characters survive round-trip."""
        text = r'(descr "Resistor SMD 0402 (1005 Metric)")'
        result = self._round_trip(text)
        assert result[1] == "Resistor SMD 0402 (1005 Metric)"


class TestTokenizerEdgeCases:
    """Tests for edge cases in the tokenizer."""

    def test_unterminated_string_raises(self):
        """An unterminated double-quoted string raises ValueError."""
        with pytest.raises(ValueError, match="Unterminated string"):
            parse('(text "unterminated')

    def test_unterminated_string_with_escape_raises(self):
        """Unterminated string after escape sequence raises ValueError."""
        with pytest.raises(ValueError, match="Unterminated string"):
            parse('(text "foo\\')

    def test_scientific_notation_positive_exponent(self):
        """Scientific notation with positive exponent parses as float."""
        result = parse('(cap 1e3)')
        assert isinstance(result[1], float)
        assert result[1] == pytest.approx(1000.0)

    def test_scientific_notation_negative_exponent(self):
        """Scientific notation with negative exponent parses as float."""
        result = parse('(cap 1e-3)')
        assert isinstance(result[1], float)
        assert result[1] == pytest.approx(0.001)

    def test_scientific_notation_signed_coefficient(self):
        """Negative scientific notation parses as float."""
        result = parse('(at -2.5e+6)')
        assert isinstance(result[1], float)
        assert result[1] == pytest.approx(-2500000.0)

    def test_scientific_notation_uppercase_e(self):
        """Scientific notation with uppercase E parses as float."""
        result = parse('(size 1E-6)')
        assert isinstance(result[1], float)
        assert result[1] == pytest.approx(1e-6)

    def test_integer_stays_int(self):
        """Plain integer (no dot, no e) parses as int not float."""
        result = parse('(version 20231120)')
        assert isinstance(result[1], int)

    def test_empty_string_is_qstr(self):
        """Empty quoted string parses as empty QStr."""
        result = parse('(text "")')
        assert isinstance(result[1], QStr)
        assert result[1] == ""
