"""Thin LiteLLM wrapper: quota check, rate limit, 429 backoff, usage + list-price cost.

Only the Gemini models listed in config.toml are called (GEMINI_API_KEY). Other provider
keys in the environment (e.g. OPENAI_API_KEY) are never used.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

# Use LiteLLM's bundled cost map instead of fetching it from GitHub at import (reproducible, offline).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

from .quota import Quota, QuotaPaused, llm_resource  # noqa: E402
from .ratelimit import retry_call  # noqa: E402

litellm.telemetry = False
litellm.suppress_debug_info = True
# The Gemini-3 "temperature is deprecated" warning fires on every call; it's recorded in NOTES.md once.
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
litellm.drop_params = True  # e.g. drop `seed` if the provider doesn't accept it


def _retry_hint(e: Exception) -> float | None:
    """Seconds to wait for a retryable error, 0 for default backoff, None to give up.
    Daily-quota 429s are not retryable: they pause the run instead."""
    if not isinstance(e, (litellm.RateLimitError, litellm.ServiceUnavailableError,
                          litellm.InternalServerError, litellm.APIConnectionError)):
        return None
    msg = str(e)
    if re.search(r"insufficient (?:credit|balance)|out of credits|credits? (?:exhausted|depleted)", msg, re.I):
        raise QuotaPaused(f"provider credits exhausted (server-side): {msg[:300]}") from e
    if isinstance(e, litellm.RateLimitError) and re.search(r"PerDay|per day", msg, re.I):
        raise QuotaPaused(f"Gemini daily quota exhausted (server-side): {msg[:300]}") from e
    m = re.search(r'retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s', msg)
    return float(m.group(1)) if m else 0.0


def api_key(m: dict[str, Any]) -> str | None:
    """The model's API key, or None if unset (then the model is skipped)."""
    names = m.get("api_key_env") or ["GEMINI_API_KEY"]
    for n in ([names] if isinstance(names, str) else names):
        if os.environ.get(n):
            return os.environ[n]
    return None


def complete(name: str, cfg: dict[str, Any], quota: Quota, messages: list[dict],
             tools: list[dict] | None = None, tool_choice: str | None = None,
             retries: int = 4, timeout_s: float | None = None) -> dict[str, Any]:
    """One chat completion. Returns {message, usage: {in, out}, list_usd, model}."""
    m = cfg["models"][name]
    resource = llm_resource(cfg, name)
    extra: dict[str, Any] = {}
    if tools:
        extra["tools"] = tools
        if tool_choice:
            extra["tool_choice"] = tool_choice

    def call():
        quota.check(resource, 1)
        b = quota.bucket(resource)
        if b:
            b.acquire()
        try:
            return litellm.completion(model=m["id"], messages=messages,
                                      temperature=cfg["agent"]["temperature"], seed=cfg["seed"],
                                      num_retries=0, **({"timeout": timeout_s} if timeout_s else {}), **extra)
        finally:
            # Count every attempt, including rejected ones: conservative.
            quota.record(resource, 1, 0.0)

    resp = retry_call(call, _retry_hint, retries=retries)
    u = resp.usage
    tin, tout = (u.prompt_tokens or 0), (u.completion_tokens or 0)
    list_usd = tin / 1e6 * m["usd_per_mtok_in"] + tout / 1e6 * m["usd_per_mtok_out"]
    quota.record(resource, 0, list_usd, {"tokens_in": tin, "tokens_out": tout})
    return {"message": resp.choices[0].message, "usage": {"in": tin, "out": tout},
            "list_usd": list_usd, "model": m["id"]}
