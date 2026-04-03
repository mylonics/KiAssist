"""API key storage and management using OS keyring with file fallback.

Supports multiple AI providers (Gemini, Claude, OpenAI).  All public methods
accept an optional *provider* parameter.  When omitted the default is
``"gemini"`` for backward compatibility.

Supported provider names and their environment variable mappings:

=========  =====================  ====================
Provider   Keyring key name       Environment variable
=========  =====================  ====================
gemini     kiassist-gemini        GEMINI_API_KEY
claude     kiassist-claude        ANTHROPIC_API_KEY
openai     kiassist-openai        OPENAI_API_KEY
=========  =====================  ====================
"""

import os
import json
import keyring
from pathlib import Path
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------

#: Map provider name → (keyring_key_name, env_variable_name, config_field_name)
_PROVIDER_META: Dict[str, Tuple[str, str, str]] = {
    "gemini": ("kiassist-gemini", "GEMINI_API_KEY", "api_key"),
    "claude": ("kiassist-claude", "ANTHROPIC_API_KEY", "claude_api_key"),
    "openai": ("kiassist-openai", "OPENAI_API_KEY", "openai_api_key"),
}

_DEFAULT_PROVIDER = "gemini"


class ApiKeyStore:
    """Manage API key storage using OS credential store with file fallback.

    Supports multiple AI providers.  All public methods accept an optional
    *provider* keyword argument (``"gemini"``, ``"claude"``, or ``"openai"``).
    Omitting the argument defaults to ``"gemini"`` for backward compatibility.
    """
    
    SERVICE_NAME = "KiAssist"
    # Kept for backward compatibility; new code uses per-provider key names.
    KEY_NAME = "gemini_api_key"
    CONFIG_DIR_NAME = ".kiassist"
    CONFIG_FILE_NAME = "config.json"
    
    def __init__(self):
        """Initialize API key store."""
        self._memory_keys: Dict[str, Optional[str]] = {p: None for p in _PROVIDER_META}
        self._keyring_available: Optional[bool] = None
        # Pre-load from environment variables
        for provider, (_key_name, env_var, _field) in _PROVIDER_META.items():
            env_val = os.environ.get(env_var)
            if env_val:
                self._memory_keys[provider] = env_val

    # ------------------------------------------------------------------
    # Backward-compat single-key shims (Gemini only)
    # ------------------------------------------------------------------

    @property
    def _memory_key(self) -> Optional[str]:
        """Backward-compatible single-key property (Gemini)."""
        return self._memory_keys.get("gemini")

    @_memory_key.setter
    def _memory_key(self, value: Optional[str]) -> None:
        """Backward-compatible single-key setter (Gemini)."""
        self._memory_keys["gemini"] = value
    
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
    
    # ------------------------------------------------------------------
    # Provider helpers
    # ------------------------------------------------------------------

    def _resolve_provider(self, provider: Optional[str]) -> str:
        """Normalise and validate a provider name.

        Args:
            provider: Provider name string or ``None`` (defaults to gemini).

        Returns:
            Validated lower-case provider name.

        Raises:
            ValueError: If the provider name is not supported.
        """
        p = (provider or _DEFAULT_PROVIDER).lower().strip()
        if p not in _PROVIDER_META:
            raise ValueError(
                f"Unknown provider {p!r}. "
                f"Supported providers: {list(_PROVIDER_META)}"
            )
        return p

    def _get_keyring_key(self, provider: str) -> str:
        """Return the keyring *username* for the given provider."""
        return _PROVIDER_META[provider][0]

    def _get_env_var(self, provider: str) -> str:
        """Return the environment variable name for the given provider."""
        return _PROVIDER_META[provider][1]

    def _get_config_field(self, provider: str) -> str:
        """Return the config.json field name for the given provider."""
        return _PROVIDER_META[provider][2]

    # ------------------------------------------------------------------
    # File-based storage (provider-aware)
    # ------------------------------------------------------------------

    def _load_from_file(self, provider: str = _DEFAULT_PROVIDER) -> Optional[str]:
        """Load an API key from file-based storage.

        Args:
            provider: Provider name (default: ``"gemini"``).

        Returns:
            The API key if found, ``None`` otherwise.
        """
        field = self._get_config_field(provider)
        try:
            config_path = self._get_config_path()
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    return config.get(field)
        except (OSError, IOError):
            pass
        except json.JSONDecodeError:
            pass
        except (TypeError, KeyError):
            pass
        return None

    def _save_to_file(self, api_key: str, provider: str = _DEFAULT_PROVIDER) -> bool:
        """Save an API key to file-based storage.

        Args:
            api_key:  The API key to save.
            provider: Provider name (default: ``"gemini"``).

        Returns:
            ``True`` if successful, ``False`` otherwise.
        """
        field = self._get_config_field(provider)
        try:
            config_path = self._ensure_config_dir()
            config: Dict[str, str] = {}

            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                    config = loaded if isinstance(loaded, dict) else {}
                except (json.JSONDecodeError, OSError, IOError):
                    config = {}

            config[field] = api_key

            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)

            try:
                os.chmod(config_path, 0o600)
            except OSError:
                pass  # Ignore permission errors on Windows

            return True
        except (OSError, IOError, TypeError):
            return False

    def _delete_from_file(self, provider: str = _DEFAULT_PROVIDER) -> None:
        """Delete an API key from file-based storage.

        Args:
            provider: Provider name (default: ``"gemini"``).
        """
        field = self._get_config_field(provider)
        try:
            config_path = self._get_config_path()
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)

                if field in config:
                    del config[field]

                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2)
        except (OSError, IOError, json.JSONDecodeError):
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_api_key(self, provider: Optional[str] = None) -> bool:
        """Check if an API key is available for *provider*.

        Args:
            provider: ``"gemini"``, ``"claude"``, ``"openai"``, or ``None``
                      (defaults to ``"gemini"``).

        Returns:
            ``True`` if an API key is available, ``False`` otherwise.
        """
        return self.get_api_key(provider) is not None

    def get_api_key(self, provider: Optional[str] = None) -> Optional[str]:
        """Get the stored API key for *provider*.

        Priority:
        1. Environment variable (e.g. ``GEMINI_API_KEY``)
        2. Memory cache
        3. OS keyring (if available)
        4. File-based config

        Args:
            provider: ``"gemini"``, ``"claude"``, ``"openai"``, or ``None``
                      (defaults to ``"gemini"``).

        Returns:
            The API key if available, ``None`` otherwise.
        """
        p = self._resolve_provider(provider)
        env_var = self._get_env_var(p)
        keyring_key = self._get_keyring_key(p)

        # 1. Environment variable
        env_key = os.environ.get(env_var)
        if env_key:
            return env_key

        # 2. Memory cache
        cached = self._memory_keys.get(p)
        if cached:
            return cached

        # 3. OS keyring
        if self._is_keyring_available():
            try:
                stored_key = keyring.get_password(self.SERVICE_NAME, keyring_key)
                if stored_key:
                    self._memory_keys[p] = stored_key
                    return stored_key
            except (keyring.errors.KeyringError, OSError):
                pass

        # 4. File-based storage
        file_key = self._load_from_file(p)
        if file_key:
            self._memory_keys[p] = file_key
            return file_key

        return None

    def set_api_key(
        self,
        api_key: str,
        provider: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Store an API key for *provider*.

        Args:
            api_key:  The API key to store.
            provider: ``"gemini"``, ``"claude"``, ``"openai"``, or ``None``
                      (defaults to ``"gemini"``).

        Returns:
            Tuple of ``(success, warning_message)`` where *success* is
            ``True`` if the key was stored (at least in memory) and
            *warning_message* is a non-fatal advisory string or ``None``.

        Raises:
            ValueError: If *api_key* is empty or *provider* is unknown.
        """
        if not api_key or not api_key.strip():
            raise ValueError("API key cannot be empty")

        p = self._resolve_provider(provider)
        keyring_key = self._get_keyring_key(p)
        api_key = api_key.strip()

        # Store in memory
        self._memory_keys[p] = api_key

        persisted = False
        warning = None

        # Try keyring
        if self._is_keyring_available():
            try:
                keyring.set_password(self.SERVICE_NAME, keyring_key, api_key)
                persisted = True
            except (keyring.errors.KeyringError, keyring.errors.PasswordSetError, OSError):
                pass

        # Fallback to file
        if not persisted:
            if self._save_to_file(api_key, p):
                persisted = True
            else:
                warning = "API key saved to memory only. It will not persist after restart."

        return (True, warning)

    def clear_api_key(self, provider: Optional[str] = None) -> None:
        """Clear the stored API key for *provider*.

        Args:
            provider: ``"gemini"``, ``"claude"``, ``"openai"``, or ``None``
                      (defaults to ``"gemini"``).
        """
        p = self._resolve_provider(provider)
        keyring_key = self._get_keyring_key(p)

        self._memory_keys[p] = None

        if self._is_keyring_available():
            try:
                keyring.delete_password(self.SERVICE_NAME, keyring_key)
            except (keyring.errors.KeyringError, keyring.errors.PasswordDeleteError, OSError):
                pass

        self._delete_from_file(p)
