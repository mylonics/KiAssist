"""API key storage and management using OS keyring with file fallback."""

import os
import json
import keyring
from pathlib import Path
from typing import Optional, Tuple


class ApiKeyStore:
    """Manage API key storage using OS credential store with file fallback."""
    
    SERVICE_NAME = "KiAssist"
    KEY_NAME = "gemini_api_key"
    CONFIG_DIR_NAME = ".kiassist"
    CONFIG_FILE_NAME = "config.json"
    
    def __init__(self):
        """Initialize API key store."""
        self._memory_key: Optional[str] = None
        self._keyring_available: Optional[bool] = None
        # Try to load from environment variable first
        self._memory_key = os.environ.get("GEMINI_API_KEY")
    
    def _get_config_path(self) -> Path:
        """Get the path to the config file.
        
        Returns:
            Path to the config file
        """
        # Use user's home directory for cross-platform compatibility
        home = Path.home()
        config_dir = home / self.CONFIG_DIR_NAME
        return config_dir / self.CONFIG_FILE_NAME
    
    def _ensure_config_dir(self) -> Path:
        """Ensure the config directory exists.
        
        Returns:
            Path to the config directory
        """
        config_path = self._get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        return config_path
    
    def _is_keyring_available(self) -> bool:
        """Check if keyring backend is available and functional.
        
        Returns:
            True if keyring is available, False otherwise
        """
        if self._keyring_available is not None:
            return self._keyring_available
        
        # The most reliable way to check keyring availability is to try it
        # This avoids fragile string matching on backend names
        test_key = "__kiassist_test__"
        try:
            keyring.set_password(self.SERVICE_NAME, test_key, "test")
            result = keyring.get_password(self.SERVICE_NAME, test_key)
            try:
                keyring.delete_password(self.SERVICE_NAME, test_key)
            except keyring.errors.PasswordDeleteError:
                pass  # Ignore delete errors, test is complete
            self._keyring_available = (result == "test")
        except (keyring.errors.KeyringError, keyring.errors.PasswordSetError):
            self._keyring_available = False
        except OSError:
            # Handle OS-level errors like dbus connection issues
            self._keyring_available = False
        except Exception:
            # Catch any other unexpected errors to avoid crashing
            self._keyring_available = False
        
        return self._keyring_available
    
    def _load_from_file(self) -> Optional[str]:
        """Load API key from file-based storage.
        
        Returns:
            The API key if found, None otherwise
        """
        try:
            config_path = self._get_config_path()
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    return config.get('api_key')
        except (OSError, IOError) as e:
            # File system errors
            pass
        except json.JSONDecodeError:
            # Invalid JSON in config file
            pass
        except (TypeError, KeyError):
            # Unexpected config structure
            pass
        return None
    
    def _save_to_file(self, api_key: str) -> bool:
        """Save API key to file-based storage.
        
        Args:
            api_key: The API key to save
            
        Returns:
            True if successful, False otherwise
        """
        try:
            config_path = self._ensure_config_dir()
            config = {}
            
            # Load existing config if present
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                except (json.JSONDecodeError, OSError, IOError):
                    # Start fresh if existing config is invalid
                    config = {}
            
            config['api_key'] = api_key
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            
            # Set restrictive permissions on the file (owner read/write only)
            try:
                os.chmod(config_path, 0o600)
            except OSError:
                pass  # Ignore permission errors on Windows
            
            return True
        except (OSError, IOError):
            # File system errors (permissions, disk full, etc.)
            return False
    
    def _delete_from_file(self) -> None:
        """Delete API key from file-based storage."""
        try:
            config_path = self._get_config_path()
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                if 'api_key' in config:
                    del config['api_key']
                
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2)
        except (OSError, IOError, json.JSONDecodeError):
            pass  # Ignore errors when deleting
    
    def has_api_key(self) -> bool:
        """Check if an API key is available.
        
        Returns:
            True if API key is available, False otherwise
        """
        return self.get_api_key() is not None
    
    def get_api_key(self) -> Optional[str]:
        """Get the stored API key.
        
        Priority:
        1. Environment variable (GEMINI_API_KEY)
        2. Memory cache
        3. OS keyring (if available)
        4. File-based config
        
        Returns:
            The API key if available, None otherwise
        """
        # Check environment variable first
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            return env_key
        
        # Check memory cache
        if self._memory_key:
            return self._memory_key
        
        # Try to load from keyring
        if self._is_keyring_available():
            try:
                stored_key = keyring.get_password(self.SERVICE_NAME, self.KEY_NAME)
                if stored_key:
                    self._memory_key = stored_key
                    return stored_key
            except (keyring.errors.KeyringError, OSError):
                # Keyring access failed, will fall back to file
                pass
        
        # Fallback to file-based storage
        file_key = self._load_from_file()
        if file_key:
            self._memory_key = file_key
            return file_key
        
        return None
    
    def set_api_key(self, api_key: str) -> Tuple[bool, Optional[str]]:
        """Store an API key.
        
        Args:
            api_key: The API key to store
            
        Returns:
            Tuple of (success, warning_message)
            success is True if key was stored (at least in memory)
            warning_message contains any non-fatal warnings
            
        Raises:
            ValueError: If API key is empty
        """
        if not api_key or not api_key.strip():
            raise ValueError("API key cannot be empty")
        
        api_key = api_key.strip()
        
        # Store in memory
        self._memory_key = api_key
        
        # Track if we successfully persisted
        persisted = False
        warning = None
        
        # Try to persist to keyring first
        if self._is_keyring_available():
            try:
                keyring.set_password(self.SERVICE_NAME, self.KEY_NAME, api_key)
                persisted = True
            except (keyring.errors.KeyringError, keyring.errors.PasswordSetError, OSError):
                # Keyring save failed, will fall back to file
                pass
        
        # If keyring failed or unavailable, try file-based storage
        if not persisted:
            if self._save_to_file(api_key):
                persisted = True
            else:
                warning = "API key saved to memory only. It will not persist after restart."
        
        return (True, warning)
    
    def clear_api_key(self) -> None:
        """Clear the stored API key."""
        self._memory_key = None
        
        # Clear from keyring
        if self._is_keyring_available():
            try:
                keyring.delete_password(self.SERVICE_NAME, self.KEY_NAME)
            except (keyring.errors.KeyringError, keyring.errors.PasswordDeleteError, OSError):
                pass  # Ignore errors when clearing
        
        # Clear from file
        self._delete_from_file()
