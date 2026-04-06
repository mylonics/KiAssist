"""AI provider package for KiAssist.

Exposes the abstract :class:`~kiassist_utils.ai.base.AIProvider` interface and
all concrete provider implementations so callers only need to import from this
package.

Example::

    from kiassist_utils.ai import GeminiProvider, AIMessage, AIToolCall

    provider = GeminiProvider(api_key="...", model="3-flash")
    response = provider.chat([AIMessage(role="user", content="Hello")])
    print(response.content)
"""

from .base import (
    AIChunk,
    AIMessage,
    AIProvider,
    AIResponse,
    AIToolCall,
    AIToolResult,
    ToolSchema,
)
from .tool_executor import ToolExecutor

# Provider implementations are lazily imported to avoid pulling in heavy
# optional dependencies (anthropic, openai) at package-load time.  This
# prevents debugger/pydantic crashes when a dependency isn't needed yet.

_PROVIDER_MAP = {
    "GeminiProvider": (".gemini", "GeminiProvider"),
    "ClaudeProvider": (".claude", "ClaudeProvider"),
    "OpenAIProvider": (".openai", "OpenAIProvider"),
    "OllamaProvider": (".ollama", "OllamaProvider"),
}


def __getattr__(name: str):
    if name in _PROVIDER_MAP:
        module_path, attr = _PROVIDER_MAP[name]
        import importlib
        mod = importlib.import_module(module_path, __package__)
        val = getattr(mod, attr)
        # Cache on the module so __getattr__ isn't called again
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Data types
    "AIChunk",
    "AIMessage",
    "AIProvider",
    "AIResponse",
    "AIToolCall",
    "AIToolResult",
    "ToolSchema",
    # Providers (lazy)
    "GeminiProvider",
    "ClaudeProvider",
    "OpenAIProvider",
    "OllamaProvider",
    # Executor
    "ToolExecutor",
]
