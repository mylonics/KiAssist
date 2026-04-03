"""Tests for the S-expression tokenizer and serializer."""

import os
import tempfile

import pytest

from kiassist_utils.kicad_parser.sexpr import (
    parse,
    parse_multi,
    serialize,
    parse_file,
    serialize_to_file,
    find_all,
    find_first,
    get_value,
    set_value,
    remove_by_tag,
    ParseError,
)


class TestParse:
    """Tests for the parse function."""

    def test_simple_list(self):
        result = parse("(hello world)")
        assert result == ["hello", "world"]

    def test_nested_list(self):
        result = parse("(a (b c) (d e))")
        assert result == ["a", ["b", "c"], ["d", "e"]]

    def test_integer(self):
        result = parse("(version 20240215)")
        assert result == ["version", 20240215]
        assert isinstance(result[1], int)

    def test_float(self):
        result = parse("(width 0.254)")
        assert result == ["width", 0.254]
        assert isinstance(result[1], float)

    def test_negative_numbers(self):
        result = parse("(at -10 -20.5)")
        assert result == ["at", -10, -20.5]

    def test_quoted_string(self):
        result = parse('(generator "eeschema")')
        assert result == ["generator", "eeschema"]

    def test_quoted_string_with_spaces(self):
        result = parse('(property "Reference" "R1")')
        assert result == ["property", "Reference", "R1"]

    def test_escape_sequences(self):
        result = parse(r'(text "line1\nline2")')
        assert result == ["text", "line1\nline2"]

    def test_escaped_quote(self):
        result = parse(r'(text "say \"hello\"")')
        assert result == ["text", 'say "hello"']

    def test_escaped_backslash(self):
        result = parse(r'(path "C:\\Users\\test")')
        assert result == ["path", "C:\\Users\\test"]

    def test_empty_string(self):
        result = parse('(name "")')
        assert result == ["name", ""]

    def test_empty_list(self):
        result = parse("(empty)")
        assert result == ["empty"]

    def test_deeply_nested(self):
        result = parse("(a (b (c (d 1))))")
        assert result == ["a", ["b", ["c", ["d", 1]]]]

    def test_whitespace_handling(self):
        result = parse("  (  hello   world  )  ")
        assert result == ["hello", "world"]

    def test_newline_handling(self):
        result = parse("(\n  hello\n  world\n)")
        assert result == ["hello", "world"]

    def test_tab_handling(self):
        result = parse("(\thello\tworld\t)")
        assert result == ["hello", "world"]

    def test_complex_kicad_snippet(self):
        text = '''(kicad_sch
            (version 20240215)
            (generator "eeschema")
            (uuid "12345678-abcd-efgh-ijkl-123456789012")
            (paper "A4"))'''
        result = parse(text)
        assert result[0] == "kicad_sch"
        assert result[1] == ["version", 20240215]
        assert result[2] == ["generator", "eeschema"]
        assert result[3] == ["uuid", "12345678-abcd-efgh-ijkl-123456789012"]
        assert result[4] == ["paper", "A4"]

    def test_bare_keyword_atoms(self):
        result = parse("(in_bom yes)")
        assert result == ["in_bom", "yes"]

    def test_unterminated_string_raises(self):
        with pytest.raises(ParseError):
            parse('(text "unterminated)')

    def test_unterminated_list_raises(self):
        with pytest.raises(ParseError):
            parse("(unclosed (inner)")

    def test_unexpected_close_paren_raises(self):
        with pytest.raises(ParseError):
            parse(")")

    def test_trailing_content_raises(self):
        with pytest.raises(ParseError):
            parse("(a) (b)")

    def test_empty_input_raises(self):
        with pytest.raises(ParseError):
            parse("")

    def test_mixed_types(self):
        result = parse('(item 42 3.14 "text" keyword)')
        assert result == ["item", 42, 3.14, "text", "keyword"]
        assert isinstance(result[1], int)
        assert isinstance(result[2], float)
        assert isinstance(result[3], str)
        assert isinstance(result[4], str)


class TestParseMulti:
    """Tests for parse_multi function."""

    def test_multiple_expressions(self):
        result = parse_multi("(a 1) (b 2)")
        assert result == [["a", 1], ["b", 2]]

    def test_single_expression(self):
        result = parse_multi("(a 1)")
        assert result == [["a", 1]]

    def test_empty_input(self):
        result = parse_multi("")
        assert result == []


class TestSerialize:
    """Tests for the serialize function."""

    def test_simple_list(self):
        result = serialize(["hello", "world"])
        assert "(hello world)" in result

    def test_nested_list(self):
        result = serialize(["a", ["b", "c"]])
        assert "(a\n" in result
        assert "(b c)" in result

    def test_integer(self):
        result = serialize(["version", 20240215])
        assert "(version 20240215)" in result

    def test_float_precision(self):
        result = serialize(["width", 0.254], precision=4)
        assert "0.254" in result

    def test_float_precision_6(self):
        result = serialize(["width", 0.123456], precision=6)
        assert "0.123456" in result

    def test_float_trailing_zeros(self):
        result = serialize(["x", 1.0], precision=4)
        assert "1.0" in result
        # Should not have excessive trailing zeros
        assert "1.0000" not in result

    def test_quoted_string_with_spaces(self):
        result = serialize(["property", "my value"])
        assert '"my value"' in result

    def test_bare_keyword(self):
        result = serialize(["type", "yes"])
        assert "(type yes)" in result

    def test_empty_string_quoted(self):
        result = serialize(["name", ""])
        assert '""' in result

    def test_string_with_quotes_escaped(self):
        result = serialize(["text", 'say "hello"'])
        assert r'say \"hello\"' in result

    def test_string_with_newline_escaped(self):
        result = serialize(["text", "line1\nline2"])
        assert r"line1\nline2" in result


class TestRoundTrip:
    """Tests for parse -> serialize -> parse round-trip fidelity."""

    def _round_trip(self, text):
        """Parse, serialize, and re-parse, checking structural equivalence."""
        tree1 = parse(text)
        serialized = serialize(tree1)
        tree2 = parse(serialized)
        assert tree1 == tree2, f"Round-trip failed:\n  Original: {tree1}\n  After: {tree2}"
        return tree2

    def test_simple_round_trip(self):
        self._round_trip("(version 20240215)")

    def test_nested_round_trip(self):
        self._round_trip("(a (b 1) (c 2))")

    def test_complex_round_trip(self):
        text = '''(kicad_sch
            (version 20240215)
            (generator "eeschema")
            (uuid "12345678-abcd-efgh")
            (paper "A4")
            (wire
                (pts (xy 100.0 50.0) (xy 150.0 50.0))
                (stroke (width 0) (type default))
                (uuid "abcd-1234")))'''
        self._round_trip(text)

    def test_quoted_strings_round_trip(self):
        self._round_trip('(property "Reference" "R1")')

    def test_escape_sequences_round_trip(self):
        self._round_trip(r'(text "line1\nline2")')

    def test_numbers_round_trip(self):
        self._round_trip("(at -10.5 20.0 90)")

    def test_symbol_round_trip(self):
        text = '''(symbol
            (lib_id "Device:R")
            (at 100.0 50.0 0)
            (unit 1)
            (in_bom yes)
            (on_board yes)
            (uuid "sym-uuid-1234")
            (property "Reference" "R1"
                (at 100.0 48.0 0)
                (effects (font (size 1.27 1.27))))
            (property "Value" "10k"
                (at 100.0 52.0 0)
                (effects (font (size 1.27 1.27)))))'''
        self._round_trip(text)


class TestFileIO:
    """Tests for file-based parse and serialize."""

    def test_parse_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.kicad_sch',
                                         delete=False) as f:
            f.write('(kicad_sch (version 20240215) (generator "test"))')
            f.flush()
            path = f.name

        try:
            result = parse_file(path)
            assert result[0] == "kicad_sch"
            assert result[1] == ["version", 20240215]
        finally:
            os.unlink(path)

    def test_serialize_to_file(self):
        tree = ["kicad_sch", ["version", 20240215], ["generator", "test"]]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.kicad_sch',
                                         delete=False) as f:
            path = f.name

        try:
            serialize_to_file(tree, path)
            result = parse_file(path)
            assert result == tree
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_file("/nonexistent/file.kicad_sch")


class TestFindAll:
    """Tests for find_all utility."""

    def test_find_multiple(self):
        tree = parse("(root (wire 1) (other) (wire 2))")
        results = find_all(tree, "wire")
        assert len(results) == 2
        assert results[0] == ["wire", 1]
        assert results[1] == ["wire", 2]

    def test_find_none(self):
        tree = parse("(root (a 1) (b 2))")
        results = find_all(tree, "wire")
        assert len(results) == 0

    def test_find_nested(self):
        tree = parse("(root (outer (inner 42)))")
        results = find_all(tree, "inner")
        assert len(results) == 1
        assert results[0] == ["inner", 42]


class TestFindFirst:
    """Tests for find_first utility."""

    def test_find_existing(self):
        tree = parse("(root (version 1) (generator test))")
        result = find_first(tree, "version")
        assert result == ["version", 1]

    def test_find_nonexistent(self):
        tree = parse("(root (a 1))")
        result = find_first(tree, "missing")
        assert result is None


class TestGetValue:
    """Tests for get_value utility."""

    def test_get_existing(self):
        tree = ["root", ["version", 42], ["name", "test"]]
        assert get_value(tree, "version") == 42
        assert get_value(tree, "name") == "test"

    def test_get_default(self):
        tree = ["root", ["version", 42]]
        assert get_value(tree, "missing") is None
        assert get_value(tree, "missing", "default") == "default"


class TestSetValue:
    """Tests for set_value utility."""

    def test_set_existing(self):
        tree = ["root", ["version", 1]]
        set_value(tree, "version", 2)
        assert get_value(tree, "version") == 2

    def test_set_new(self):
        tree = ["root"]
        set_value(tree, "version", 42)
        assert get_value(tree, "version") == 42


class TestRemoveByTag:
    """Tests for remove_by_tag utility."""

    def test_remove_all(self):
        tree = ["root", ["wire", 1], ["other"], ["wire", 2]]
        count = remove_by_tag(tree, "wire")
        assert count == 2
        assert tree == ["root", ["other"]]

    def test_remove_first_only(self):
        tree = ["root", ["wire", 1], ["wire", 2]]
        count = remove_by_tag(tree, "wire", first_only=True)
        assert count == 1
        assert tree == ["root", ["wire", 2]]

    def test_remove_none(self):
        tree = ["root", ["a", 1]]
        count = remove_by_tag(tree, "missing")
        assert count == 0
