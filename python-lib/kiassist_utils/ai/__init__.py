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
from .gemini import GeminiProvider
from .claude import ClaudeProvider
from .openai import OpenAIProvider
from .tool_executor import ToolExecutor

__all__ = [
    # Data types
    "AIChunk",
    "AIMessage",
    "AIProvider",
    "AIResponse",
    "AIToolCall",
    "AIToolResult",
    "ToolSchema",
    # Providers
    "GeminiProvider",
    "ClaudeProvider",
    "OpenAIProvider",
    # Executor
    "ToolExecutor",
]
