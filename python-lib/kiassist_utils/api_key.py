"""API key storage and management using OS keyring."""

import os
import keyring
from typing import Optional


class ApiKeyStore:
    """Manage API key storage using OS credential store."""
    
    SERVICE_NAME = "KiAssist"
    KEY_NAME = "gemini_api_key"
    
    def __init__(self):
        """Initialize API key store."""
        self._memory_key: Optional[str] = None
        # Try to load from environment variable first
        self._memory_key = os.environ.get("GEMINI_API_KEY")
    
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
        3. OS keyring
        
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
        try:
            stored_key = keyring.get_password(self.SERVICE_NAME, self.KEY_NAME)
            if stored_key:
                self._memory_key = stored_key
                return stored_key
        except Exception as e:
            print(f"Warning: Could not access keyring: {e}")
        
        return None
    
    def set_api_key(self, api_key: str) -> None:
        """Store an API key.
        
        Args:
            api_key: The API key to store
            
        Raises:
            Exception: If storing the key fails
        """
        if not api_key or not api_key.strip():
            raise ValueError("API key cannot be empty")
        
        # Store in memory
        self._memory_key = api_key.strip()
        
        # Try to persist to keyring
        try:
            keyring.set_password(self.SERVICE_NAME, self.KEY_NAME, self._memory_key)
        except Exception as e:
            # If keyring fails, we still have it in memory
            print(f"Warning: Could not save to keyring: {e}")
            # Don't raise - we can still use the in-memory key
    
    def clear_api_key(self) -> None:
        """Clear the stored API key."""
        self._memory_key = None
        try:
            keyring.delete_password(self.SERVICE_NAME, self.KEY_NAME)
        except Exception:
            pass  # Ignore errors when clearing
