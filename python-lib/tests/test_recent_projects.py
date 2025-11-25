"""Tests for recent projects management module."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock
from datetime import datetime

import pytest

from kiassist_utils.recent_projects import (
    get_config_dir,
    RecentProjectsStore,
    validate_kicad_project_path,
    find_file_in_dir,
)


class TestGetConfigDir:
    """Tests for get_config_dir function."""

    def test_linux_config_dir(self):
        """Test config directory on Linux."""
        with mock.patch('os.name', 'posix'):
            with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': '/home/user/.config'}, clear=False):
                with tempfile.TemporaryDirectory() as tmpdir:
                    with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': tmpdir}):
                        result = get_config_dir()
                        assert 'kiassist' in str(result)

    @pytest.mark.skipif(os.name != 'nt', reason="Windows-only test")
    def test_windows_config_dir(self):
        """Test config directory on Windows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {'APPDATA': tmpdir}):
                result = get_config_dir()
                assert 'KiAssist' in str(result)


class TestRecentProjectsStore:
    """Tests for RecentProjectsStore class."""

    def test_add_project(self):
        """Test adding a project to recent list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('kiassist_utils.recent_projects.get_config_dir', return_value=Path(tmpdir)):
                # Create a mock project file
                project_file = Path(tmpdir) / 'test_project.kicad_pro'
                project_file.touch()
                
                store = RecentProjectsStore()
                store.add_project(str(project_file))
                
                projects = store.get_recent_projects()
                assert len(projects) == 1
                assert projects[0]['name'] == 'test_project'

    def test_add_duplicate_project_moves_to_top(self):
        """Test that adding a duplicate project moves it to the top."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('kiassist_utils.recent_projects.get_config_dir', return_value=Path(tmpdir)):
                # Create mock project files
                project1 = Path(tmpdir) / 'project1.kicad_pro'
                project2 = Path(tmpdir) / 'project2.kicad_pro'
                project1.touch()
                project2.touch()
                
                store = RecentProjectsStore()
                store.add_project(str(project1))
                store.add_project(str(project2))
                store.add_project(str(project1))  # Add project1 again
                
                projects = store.get_recent_projects()
                assert len(projects) == 2
                assert projects[0]['name'] == 'project1'  # Should be at top now

    def test_remove_project(self):
        """Test removing a project from recent list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('kiassist_utils.recent_projects.get_config_dir', return_value=Path(tmpdir)):
                # Create a mock project file
                project_file = Path(tmpdir) / 'test_project.kicad_pro'
                project_file.touch()
                
                store = RecentProjectsStore()
                store.add_project(str(project_file))
                store.remove_project(str(project_file))
                
                projects = store.get_recent_projects()
                assert len(projects) == 0

    def test_max_recent_projects_limit(self):
        """Test that max recent projects limit is enforced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('kiassist_utils.recent_projects.get_config_dir', return_value=Path(tmpdir)):
                store = RecentProjectsStore()
                
                # Add more than MAX_RECENT_PROJECTS
                for i in range(15):
                    project_file = Path(tmpdir) / f'project{i}.kicad_pro'
                    project_file.touch()
                    store.add_project(str(project_file))
                
                projects = store.get_recent_projects()
                assert len(projects) <= RecentProjectsStore.MAX_RECENT_PROJECTS

    def test_filters_nonexistent_projects(self):
        """Test that nonexistent projects are filtered out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('kiassist_utils.recent_projects.get_config_dir', return_value=Path(tmpdir)):
                store = RecentProjectsStore()
                
                # Manually add a nonexistent project to the internal list
                store._recent_projects = [
                    {'path': '/nonexistent/project.kicad_pro', 'name': 'nonexistent', 'last_opened': '2024-01-01'}
                ]
                store._save()
                
                projects = store.get_recent_projects()
                assert len(projects) == 0

    def test_clear(self):
        """Test clearing all recent projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('kiassist_utils.recent_projects.get_config_dir', return_value=Path(tmpdir)):
                # Create a mock project file
                project_file = Path(tmpdir) / 'test_project.kicad_pro'
                project_file.touch()
                
                store = RecentProjectsStore()
                store.add_project(str(project_file))
                store.clear()
                
                projects = store.get_recent_projects()
                assert len(projects) == 0


class TestValidateKicadProjectPath:
    """Tests for validate_kicad_project_path function."""

    def test_valid_kicad_pro_file(self):
        """Test validation of a valid .kicad_pro file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            project_file.touch()
            
            result = validate_kicad_project_path(str(project_file))
            assert result['valid'] is True
            assert result['project_name'] == 'test_project'

    def test_valid_directory_with_kicad_pro(self):
        """Test validation of a directory containing a .kicad_pro file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            project_file.touch()
            
            result = validate_kicad_project_path(tmpdir)
            assert result['valid'] is True
            assert result['project_name'] == 'test_project'

    def test_invalid_nonexistent_path(self):
        """Test validation of a nonexistent path."""
        result = validate_kicad_project_path('/nonexistent/path')
        assert result['valid'] is False
        assert 'error' in result

    def test_invalid_directory_without_kicad_pro(self):
        """Test validation of a directory without .kicad_pro file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_kicad_project_path(tmpdir)
            assert result['valid'] is False

    def test_finds_pcb_and_schematic_files(self):
        """Test that PCB and schematic files are found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_file = Path(tmpdir) / 'test_project.kicad_pro'
            pcb_file = Path(tmpdir) / 'test_project.kicad_pcb'
            sch_file = Path(tmpdir) / 'test_project.kicad_sch'
            
            project_file.touch()
            pcb_file.touch()
            sch_file.touch()
            
            result = validate_kicad_project_path(str(project_file))
            assert result['valid'] is True
            assert result['pcb_path'] is not None
            assert result['schematic_path'] is not None


class TestFindFileInDir:
    """Tests for find_file_in_dir function."""

    def test_finds_file_with_extension(self):
        """Test finding a file with specific extension."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / 'test.kicad_pcb'
            test_file.touch()
            
            result = find_file_in_dir(Path(tmpdir), '.kicad_pcb')
            assert result is not None
            assert result.endswith('.kicad_pcb')

    def test_returns_none_when_not_found(self):
        """Test returning None when file is not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_file_in_dir(Path(tmpdir), '.kicad_pcb')
            assert result is None
