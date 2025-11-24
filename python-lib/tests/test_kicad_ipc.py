"""Tests for KiCad IPC instance detection module."""

import os
import platform
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from kiassist_utils.kicad_ipc import (
    get_ipc_socket_dir,
    discover_socket_files,
    socket_path_to_uri,
    MAX_WINDOWS_INSTANCES,
)


class TestGetIpcSocketDir:
    """Tests for get_ipc_socket_dir function."""

    def test_linux_socket_dir(self):
        """Test socket directory on Linux."""
        with mock.patch('kiassist_utils.kicad_ipc._CURRENT_PLATFORM', 'Linux'):
            with mock.patch.dict(os.environ, {'HOME': '/home/testuser'}, clear=False):
                # When flatpak path doesn't exist, return default
                result = get_ipc_socket_dir()
                assert result == Path("/tmp/kicad")

    def test_windows_socket_dir(self):
        """Test socket directory on Windows."""
        with mock.patch('kiassist_utils.kicad_ipc._CURRENT_PLATFORM', 'Windows'):
            with mock.patch('kiassist_utils.kicad_ipc.gettempdir', return_value=r'C:\Users\test\AppData\Local\Temp'):
                result = get_ipc_socket_dir()
                # Check the path parts are correct (platform-independent)
                assert str(result).replace('/', '\\').endswith('kicad')
                assert 'AppData' in str(result) or 'Temp' in str(result)

    def test_macos_socket_dir(self):
        """Test socket directory on macOS."""
        with mock.patch('kiassist_utils.kicad_ipc._CURRENT_PLATFORM', 'Darwin'):
            with mock.patch.dict(os.environ, {'HOME': '/Users/testuser'}, clear=False):
                # When flatpak path doesn't exist, return default
                result = get_ipc_socket_dir()
                assert result == Path("/tmp/kicad")


class TestDiscoverSocketFiles:
    """Tests for discover_socket_files function."""

    def test_windows_generates_potential_paths(self):
        """Test that Windows returns generated potential socket paths."""
        with mock.patch('kiassist_utils.kicad_ipc._CURRENT_PLATFORM', 'Windows'):
            with mock.patch('kiassist_utils.kicad_ipc.gettempdir', return_value=r'C:\Temp'):
                result = discover_socket_files()
                
                # Should have MAX_WINDOWS_INSTANCES paths
                assert len(result) == MAX_WINDOWS_INSTANCES
                
                # First should be api.sock
                result_str = str(result[0])
                assert 'api.sock' in result_str
                assert 'kicad' in result_str
                
                # Rest should be api-N.sock
                for i in range(1, MAX_WINDOWS_INSTANCES):
                    result_str = str(result[i])
                    assert f'api-{i}.sock' in result_str

    def test_linux_returns_empty_when_dir_not_exists(self):
        """Test that Linux returns empty list when socket dir doesn't exist."""
        with mock.patch('kiassist_utils.kicad_ipc._CURRENT_PLATFORM', 'Linux'):
            with mock.patch.dict(os.environ, {'HOME': '/nonexistent'}, clear=False):
                result = discover_socket_files()
                assert result == []

    def test_linux_discovers_socket_files(self):
        """Test that Linux discovers actual socket files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_dir = Path(tmpdir) / "kicad"
            socket_dir.mkdir()
            
            # Create some socket files
            (socket_dir / "api.sock").touch()
            (socket_dir / "api-1.sock").touch()
            (socket_dir / "other.txt").touch()  # Should be ignored
            
            with mock.patch('kiassist_utils.kicad_ipc._CURRENT_PLATFORM', 'Linux'):
                with mock.patch('kiassist_utils.kicad_ipc.get_ipc_socket_dir', return_value=socket_dir):
                    result = discover_socket_files()
                    
                    # Should find the two socket files, sorted
                    assert len(result) == 2
                    assert result[0] == socket_dir / "api-1.sock"
                    assert result[1] == socket_dir / "api.sock"


class TestSocketPathToUri:
    """Tests for socket_path_to_uri function."""

    def test_linux_uri_format(self):
        """Test URI format on Linux."""
        socket_file = Path("/tmp/kicad/api.sock")
        result = socket_path_to_uri(socket_file)
        assert result == "ipc:///tmp/kicad/api.sock"

    def test_windows_uri_format(self):
        """Test URI format on Windows."""
        socket_file = Path("C:\\Users\\test\\AppData\\Local\\Temp\\kicad\\api.sock")
        result = socket_path_to_uri(socket_file)
        # On Windows, Path will use backslashes
        assert result.startswith("ipc://")
        assert "kicad" in result
        assert "api.sock" in result
