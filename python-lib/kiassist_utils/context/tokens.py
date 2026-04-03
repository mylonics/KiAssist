"""Token counting and context-window management.

:class:`ContextWindowManager` tracks cumulative token usage across a
conversation and automatically triggers a summarisation step when token usage
reaches a configurable fraction of the model's context window.

It also provides :meth:`~ContextWindowManager.trim_tool_result` which
truncates over-long tool outputs so they do not fill up the context window with
raw data that is rarely needed verbatim.

Example::

    from kiassist_utils.context.tokens import ContextWindowManager
    from kiassist_utils.ai.base import AIMessage

    manager = ContextWindowManager(
        context_window=128_000,
        summarize_threshold=0.8,
        result_max_chars=4_000,
        protected_tail=5,
    )

    # After each AI response, record reported usage:
    manager.track_usage({"input_tokens": 512, "output_tokens": 128})

    # Before each request, check and potentially summarise:
    messages = manager.maybe_summarize(messages, provider, system_prompt)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from ..ai.base import AIMessage, AIResponse, AIProvider

logger = logging.getLogger(__name__)

# Sentinel appended to truncated tool results so the AI knows there is more.
_TRUNCATION_SUFFIX = "\n...[truncated — full result saved to disk]"


class ContextWindowManager:
    """Tracks token usage and keeps conversations within the model's context window.

    Args:
        context_window:      Maximum tokens the model can process in total.
                             Obtained from :meth:`AIProvider.get_context_window`.
        summarize_threshold: Fraction of *context_window* at which
                             summarisation is triggered (default 0.8 = 80 %).
        result_max_chars:    Maximum character length of a single tool result
                             before it is truncated.  Defaults to 4 000.
        protected_tail:      Number of most-recent messages that are never
                             included in the summarisation prompt (they are
                             appended after the summary).  Defaults to 5.
    """

    def __init__(
        self,
        context_window: int,
        summarize_threshold: float = 0.8,
        result_max_chars: int = 4_000,
        protected_tail: int = 5,
    ) -> None:
        if context_window <= 0:
            raise ValueError("context_window must be positive")
        if not 0.0 < summarize_threshold <= 1.0:
            raise ValueError("summarize_threshold must be in (0, 1]")
        if result_max_chars <= 0:
            raise ValueError("result_max_chars must be positive")
        if protected_tail < 0:
            raise ValueError("protected_tail must be >= 0")

        self._context_window = context_window
        self._summarize_threshold = summarize_threshold
        self._result_max_chars = result_max_chars
        self._protected_tail = protected_tail
        self._total_tokens: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def from_provider(
        cls,
        provider: AIProvider,
        summarize_threshold: float = 0.8,
        result_max_chars: int = 4_000,
        protected_tail: int = 5,
    ) -> "ContextWindowManager":
        """Construct a manager sized to *provider*'s context window.

        Args:
            provider: An :class:`~kiassist_utils.ai.base.AIProvider` instance.
            summarize_threshold: See :class:`ContextWindowManager`.
            result_max_chars:    See :class:`ContextWindowManager`.
            protected_tail:      See :class:`ContextWindowManager`.

        Returns:
            A configured :class:`ContextWindowManager`.
        """
        return cls(
            context_window=provider.get_context_window(),
            summarize_threshold=summarize_threshold,
            result_max_chars=result_max_chars,
            protected_tail=protected_tail,
        )

    @property
    def total_tokens(self) -> int:
        """Cumulative token count recorded via :meth:`track_usage`."""
        return self._total_tokens

    @property
    def context_window(self) -> int:
        """Model's maximum context size in tokens."""
        return self._context_window

    def track_usage(self, usage: Dict[str, int]) -> None:
        """Record token usage reported by the provider for a single turn.

        The *usage* dict may contain any of the provider-specific keys such as
        ``input_tokens``, ``output_tokens``, ``total_tokens``, ``prompt_tokens``,
        ``completion_tokens``, etc.  The method adds the largest plausible total
        it can derive from the dict.

        Args:
            usage: Token-usage dict from :attr:`AIResponse.usage`.
        """
        # Prefer an explicit total if present
        if "total_tokens" in usage:
            self._total_tokens += int(usage["total_tokens"])
            return
        # Otherwise sum input + output
        self._total_tokens += int(usage.get("input_tokens", 0)) + int(
            usage.get("output_tokens", 0)
        )
        # OpenAI naming
        self._total_tokens += int(usage.get("prompt_tokens", 0)) + int(
            usage.get("completion_tokens", 0)
        )

    def reset(self) -> None:
        """Reset cumulative token counter to zero."""
        self._total_tokens = 0

    def is_near_limit(self) -> bool:
        """Return ``True`` if token usage has reached the summarise threshold."""
        return self._total_tokens >= self._context_window * self._summarize_threshold

    def trim_tool_result(self, content: str) -> str:
        """Truncate *content* if it exceeds :attr:`result_max_chars`.

        Args:
            content: Raw tool-result string.

        Returns:
            Original string if within budget; otherwise the first
            ``result_max_chars`` characters followed by a truncation notice.
        """
        if len(content) <= self._result_max_chars:
            return content
        return content[: self._result_max_chars] + _TRUNCATION_SUFFIX

    def maybe_summarize(
        self,
        messages: List[AIMessage],
        provider: AIProvider,
        system_prompt: Optional[str] = None,
    ) -> List[AIMessage]:
        """Summarise the conversation if token usage is near the limit.

        When :meth:`is_near_limit` returns ``True``, the messages older than
        ``protected_tail`` are replaced with a single assistant summary message.
        The :attr:`total_tokens` counter is reset after summarisation.

        Args:
            messages:      Current conversation history.
            provider:      AI provider used to generate the summary.
            system_prompt: Optional system prompt forwarded to the provider.

        Returns:
            Updated message list (either the original or summarised version).
        """
        if not self.is_near_limit():
            return messages

        tail_count = min(self._protected_tail, len(messages))
        head = messages[: len(messages) - tail_count]
        tail = messages[len(messages) - tail_count :]

        if not head:
            # Nothing to summarise — conversation is very short
            return messages

        logger.info(
            "Context window at %.0f%% (%d / %d tokens). Summarising %d messages.",
            100 * self._total_tokens / self._context_window,
            self._total_tokens,
            self._context_window,
            len(head),
        )

        # Build a summarisation prompt
        summary_request = AIMessage(
            role="user",
            content=(
                "Please provide a concise summary of our conversation so far, "
                "capturing the key design decisions, tool results, and any "
                "unresolved questions.  This summary will replace the full "
                "history in your context window."
            ),
        )

        try:
            response: AIResponse = provider.chat(
                head + [summary_request],
                system_prompt=system_prompt,
            )
            summary_text = response.content or "(No summary generated.)"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Summarisation failed: %s — keeping original messages.", exc)
            return messages

        summary_message = AIMessage(
            role="assistant",
            content=f"[Conversation summary]\n{summary_text}",
        )

        self.reset()
        return [summary_message] + list(tail)
