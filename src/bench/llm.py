"""Thin LiteLLM wrapper: quota check, rate limit, 429 backoff, usage + list-price cost.

Only Gemini models from config.toml are ever called. Other provider keys in the
environment (e.g. OPENAI_API_KEY) are never used.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Use LiteLLM's bundled cost map instead of fetching it from GitHub at import (reproducible, offline).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

from .quota import Quota, QuotaPaused  # noqa: E402
from .ratelimit import retry_call  # noqa: E402

litellm.telemetry = False
litellm.drop_params = True  # e.g. drop `seed` if the provider doesn't accept it


def _retry_hint(e: Exception) -> float | None:
    """Seconds to wait for a retryable error, 0 for default backoff, None to give up.
    Daily-quota 429s are not retryable: they pause the run instead."""
    if not isinstance(e, (litellm.RateLimitError, litellm.ServiceUnavailableError,
                          litellm.InternalServerError, litellm.APIConnectionError)):
        return None
    msg = str(e)
    if isinstance(e, litellm.RateLimitError) and re.search(r"PerDay|per day", msg, re.I):
        raise QuotaPaused(f"Gemini daily quota exhausted (server-side): {msg[:300]}") from e
    m = re.search(r'retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s', msg)
    return float(m.group(1)) if m else 0.0


def complete(name: str, cfg: dict[str, Any], quota: Quota, messages: list[dict],
             tools: list[dict] | None = None) -> dict[str, Any]:
    """One chat completion. Returns {message, usage: {in, out}, list_usd, model}."""
    m = cfg["models"][name]
    resource = f"gemini:{name}"

    def call():
        quota.check(resource, 1)
        b = quota.bucket(resource)
        if b:
            b.acquire()
        try:
            return litellm.completion(model=m["id"], messages=messages, tools=tools,
                                      temperature=cfg["agent"]["temperature"], seed=cfg["seed"])
        finally:
            # Count every attempt, including rejected ones: conservative.
            quota.record(resource, 1, 0.0)

    resp = retry_call(call, _retry_hint)
    u = resp.usage
    tin, tout = (u.prompt_tokens or 0), (u.completion_tokens or 0)
    list_usd = tin / 1e6 * m["usd_per_mtok_in"] + tout / 1e6 * m["usd_per_mtok_out"]
    quota.record(resource, 0, list_usd, {"tokens_in": tin, "tokens_out": tout})
    return {"message": resp.choices[0].message, "usage": {"in": tin, "out": tout},
            "list_usd": list_usd, "model": m["id"]}
