import os
import re
import time
from typing import Any

from crewai import LLM
from litellm.exceptions import RateLimitError

_MAX_RATE_LIMIT_RETRIES = 4


class GroqLLM(LLM):
    """LLM wired to Groq via CrewAI's generic LiteLLM path.

    Workaround for a crewai 1.15.x gap: CrewAI's agent executor tags every
    system/user message with a `cache_breakpoint` flag meant for providers
    with native prompt-caching support. Only the native Anthropic completion
    class strips that flag before sending the request; the generic LiteLLM
    path (which every non-native provider, including Groq, goes through)
    forwards it as-is, and Groq's OpenAI-compatible API rejects the unknown
    field. We strip the flag here, right before the request leaves the
    process. Safe to remove once upstream fixes this for LiteLLM providers.

    Also retries on Groq's tokens-per-minute rate limit: a multi-agent
    pipeline fires several calls in quick succession, and Groq's free tier
    (12k TPM) is easy to exceed within one rolling minute even when each
    individual call is well within limits. Groq's error message includes
    the exact wait time ("Please try again in 8.3s") - we parse and honor
    that instead of guessing a backoff.
    """

    def _handle_non_streaming_response(
        self, params: dict[str, Any], *args: Any, **kwargs: Any
    ) -> str | Any:
        for message in params.get("messages", []):
            message.pop("cache_breakpoint", None)

        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return super()._handle_non_streaming_response(params, *args, **kwargs)
            except RateLimitError as exc:
                if attempt == _MAX_RATE_LIMIT_RETRIES:
                    raise
                wait_seconds = _parse_retry_after(str(exc)) or 10
                time.sleep(wait_seconds)


def _parse_retry_after(error_message: str) -> float | None:
    match = re.search(r"try again in ([\d.]+)s", error_message)
    return float(match.group(1)) + 1 if match else None


def groq_llm(model: str = "llama-3.3-70b-versatile", temperature: float = 0.2) -> GroqLLM:
    return GroqLLM(
        model=f"groq/{model}",
        api_key=os.environ["GROQ_API_KEY"],
        temperature=temperature,
    )
