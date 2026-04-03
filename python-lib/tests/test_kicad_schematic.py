"""Tests for KiCad schematic manipulation module."""

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_schematic import (
    is_schematic_api_available,
    get_schematic_path_for_project,
    find_existing_schematic,
    inject_test_note,
)


class TestIsSchematicApiAvailable:
    """Tests for is_schematic_api_available function."""

    def test_returns_boolean(self):
        """Test that function returns a boolean."""
        result = is_schematic_api_available()
        assert isinstance(result, bool)

    def test_always_available(self):
        """kicad_parser is a built-in module — API is always available."""
        assert is_schematic_api_available() is True


class TestGetSchematicPathForProject:
    """Tests for get_schematic_path_for_project function."""

    def test_returns_none_for_empty_path(self):
        """Test that empty path returns None."""
        assert get_schematic_path_for_project('') is None
        assert get_schematic_path_for_project(None) is None

    def test_returns_none_for_nonexistent_directory(self):
        """Test that nonexistent directory returns None."""
        result = get_schematic_path_for_project('/nonexistent/path/project.kicad_pro')
        assert result is None

    def test_returns_path_for_valid_project_file(self):
        """Test that valid project file returns correct schematic path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'myproject.kicad_pro'
            project_file.touch()
            
            result = get_schematic_path_for_project(str(project_file))
            
            expected = str(Path(tmpdir) / 'myproject.kicad_sch')
            assert result == expected

    def test_returns_path_for_directory(self):
        """Test that directory path returns correct schematic path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / 'myproject'
            project_dir.mkdir()
            
            result = get_schematic_path_for_project(str(project_dir))
            
            expected = str(project_dir / 'myproject.kicad_sch')
            assert result == expected


class TestFindExistingSchematic:
    """Tests for find_existing_schematic function."""

    def test_returns_none_for_empty_path(self):
        """Test that empty path returns None."""
        assert find_existing_schematic('') is None
        assert find_existing_schematic(None) is None

    def test_returns_none_for_nonexistent_directory(self):
        """Test that nonexistent directory returns None."""
        result = find_existing_schematic('/nonexistent/path/project.kicad_pro')
        assert result is None

    def test_returns_none_when_no_schematic_exists(self):
        """Test that directory without schematic returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'myproject.kicad_pro'
            project_file.touch()
            
            result = find_existing_schematic(str(project_file))
            
            assert result is None

    def test_finds_schematic_with_project_name(self):
        """Test that schematic with project name is found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'myproject.kicad_pro'
            project_file.touch()
            schematic_file = Path(tmpdir) / 'myproject.kicad_sch'
            schematic_file.touch()
            
            result = find_existing_schematic(str(project_file))
            
            assert result == str(schematic_file)

    def test_finds_other_schematic_if_no_matching_name(self):
        """Test that any schematic is found if no matching name exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'myproject.kicad_pro'
            project_file.touch()
            schematic_file = Path(tmpdir) / 'other_schematic.kicad_sch'
            schematic_file.touch()
            
            result = find_existing_schematic(str(project_file))
            
            assert result == str(schematic_file)


class TestInjectTestNote:
    """Tests for inject_test_note function."""

    def test_returns_error_for_empty_path(self):
        """Test that empty path returns error."""
        result = inject_test_note('')
        
        assert result['success'] is False
        assert 'error' in result
        assert 'No project path' in result['error']

    def test_returns_error_for_none_path(self):
        """Test that None path returns error."""
        result = inject_test_note(None)
        
        assert result['success'] is False
        assert 'error' in result

    def test_returns_error_for_nonexistent_directory(self):
        """Test that nonexistent directory returns error."""
        result = inject_test_note('/nonexistent/path/project.kicad_pro')
        
        assert result['success'] is False
        assert 'error' in result
        assert 'does not exist' in result['error']

    def test_creates_new_schematic_when_none_exists(self):
        """Test that a new schematic is created when none exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            project_file.touch()
            
            result = inject_test_note(str(project_file))
            
            assert result['success'] is True
            assert 'schematic_path' in result
            assert result.get('created_new') is True
            assert Path(result['schematic_path']).exists()

    def test_modifies_existing_schematic(self):
        """Test that existing schematic is modified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            project_file.touch()

            # Create a minimal schematic using kicad_parser
            from kiassist_utils.kicad_parser.schematic import Schematic
            sch = Schematic()
            sch.generator = "test"
            sch.version = 20231120
            schematic_path = Path(tmpdir) / 'test_project.kicad_sch'
            sch.save(str(schematic_path))
            
            result = inject_test_note(str(project_file))
            
            assert result['success'] is True
            assert 'schematic_path' in result
            assert result.get('created_new') is False

    def test_custom_note_text(self):
        """Test that custom note text is used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            project_file.touch()
            
            custom_text = "My Custom Test Note"
            result = inject_test_note(str(project_file), note_text=custom_text)
            
            assert result['success'] is True
            assert custom_text in result.get('message', '')

    def test_note_text_written_to_file(self):
        """Test that the note text actually appears in the saved schematic file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            project_file.touch()

            result = inject_test_note(str(project_file), note_text="CheckNote")

            assert result['success'] is True
            content = Path(result['schematic_path']).read_text(encoding='utf-8')
            assert "CheckNote" in content
