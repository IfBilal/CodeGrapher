import os
import re
import time
from typing import Any

from crewai import LLM
from litellm.exceptions import RateLimitError

_MAX_RATE_LIMIT_RETRIES = 4
_MAX_AUTO_RETRY_WAIT_S = 60  # don't auto-sleep through anything longer than this


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
    the exact wait time ("Please try again in 8.3s", or "20m13.92s" for a
    longer wait) - we parse and honor that instead of guessing a backoff.

    Only short waits are retried automatically (see _MAX_AUTO_RETRY_WAIT_S).
    A per-minute limit clearing in a few seconds is worth sleeping through;
    a daily-quota exhaustion telling us to wait 20+ minutes is not - eating
    that wait inside a synchronous call would hang a Celery worker for the
    duration. Past that threshold we raise immediately so the job fails
    fast and visibly instead of silently stalling.
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
                wait_seconds = _parse_retry_after(str(exc))
                if attempt == _MAX_RATE_LIMIT_RETRIES or wait_seconds is None or wait_seconds > _MAX_AUTO_RETRY_WAIT_S:
                    raise
                time.sleep(wait_seconds)


def _parse_retry_after(error_message: str) -> float | None:
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", error_message)
    if not match:
        return None
    minutes = int(match.group(1)) if match.group(1) else 0
    seconds = float(match.group(2))
    return minutes * 60 + seconds + 1


def groq_llm(model: str = "llama-3.3-70b-versatile", temperature: float = 0.2) -> GroqLLM:
    return GroqLLM(
        model=f"groq/{model}",
        api_key=os.environ["GROQ_API_KEY"],
        temperature=temperature,
    )
