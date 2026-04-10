"""KiAssist exception hierarchy.

All KiAssist-specific exceptions inherit from :class:`KiAssistError` so
callers can catch a single base class when they want to handle *any*
KiAssist failure, or catch specific subclasses for targeted handling.
"""


class KiAssistError(Exception):
    """Base exception for all KiAssist errors."""


class SchematicParseError(KiAssistError):
    """Raised when a KiCad schematic file cannot be parsed."""


class FootprintParseError(KiAssistError):
    """Raised when a KiCad footprint file cannot be parsed."""


class SymbolLibParseError(KiAssistError):
    """Raised when a KiCad symbol library file cannot be parsed."""


class PCBParseError(KiAssistError):
    """Raised when a KiCad PCB file cannot be parsed."""


class APIKeyError(KiAssistError):
    """Raised for API key retrieval or validation failures."""


class IPCError(KiAssistError):
    """Raised when KiCad IPC communication fails."""


class ProviderError(KiAssistError):
    """Raised when an AI provider call fails."""


class ConfigError(KiAssistError):
    """Raised for configuration read/write failures."""


class ImportError_(KiAssistError):
    """Raised when a component import operation fails.

    Named with a trailing underscore to avoid shadowing the builtin
    ``ImportError``.
    """


class PathValidationError(KiAssistError):
    """Raised when a user-supplied path fails safety validation."""
