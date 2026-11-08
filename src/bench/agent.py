"""Single tool-calling agent. Same loop for every mode; modes differ only in which
tools (and which names/descriptions) are offered.

- At most `agent.max_tool_calls` tool calls per task. Extra calls in the same turn
  get an error result instead of running.
- Once the limit is reached, one final completion is forced with tool_choice="none".
- Every LLM request and tool call is logged in the returned record.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .quota import Quota
from .tools.base import Tool

LIMIT_MSG = "Tool call limit reached. Answer with the information you already have."

# Phrases from challenge/interstitial pages only. Not "captcha"/"cloudflare": scrapingcourse.com's
# own navigation lists its Cloudflare and CAPTCHA practice pages.
_BLOCK_MARKERS = ("just a moment...", "cf-chl-", "attention required! | cloudflare", "verify you are human",
                  "enable javascript and cookies to continue", "checking your browser before accessing",
                  "performing security verification")


def blocked_page(content: str) -> str | None:
    """A bot-protection / challenge marker found in the tool output, if any."""
    low = (content or "")[:20000].lower()
    return next((m for m in _BLOCK_MARKERS if m in low), None)


def user_message(task: dict[str, Any]) -> str:
    return f"{task['question']}\n\nURL: {task['url']}"


def tool_spec(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def _msg_to_dict(msg: Any) -> dict[str, Any]:
    """Assistant message back into history. model_dump keeps provider fields (e.g. Gemini
    thought signatures) that multi-turn tool calling needs."""
    if hasattr(msg, "model_dump"):
        d = msg.model_dump(exclude_none=True)
    else:
        d = {"content": getattr(msg, "content", None),
             "tool_calls": [{"id": tc.id, "type": "function",
                             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in (getattr(msg, "tool_calls", None) or [])]}
    d["role"] = "assistant"
    return d


def run_task(task: dict[str, Any], model: str, cfg: dict[str, Any], quota: Quota,
             tools: dict[str, Tool] | None = None, specs: list[dict] | None = None,
             complete: Callable[..., dict] | None = None,
             first_tool_choice: str | None = None) -> dict[str, Any]:
    """Run one task. `tools` maps the name shown to the model -> Tool (names can be
    anonymized); `specs` are the tool definitions shown to the model.
    first_tool_choice="required" forces a tool call on the first turn (oracle sweep)."""
    if complete is None:
        from .llm import complete  # lazy: litellm import is slow
    tools = tools or {}
    max_calls = cfg["agent"]["max_tool_calls"]
    max_chars = cfg["agent"]["max_content_chars"]
    # With zero tools, the tools-oriented prompt makes Gemini return empty output (NOTES.md),
    # so no_tools uses its own minimal prompt.
    system = cfg["agent"]["system_prompt"] if tools else cfg["agent"].get(
        "system_prompt_no_tools", cfg["agent"]["system_prompt"])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message(task)},
    ]
    calls: list[dict[str, Any]] = []
    rec: dict[str, Any] = {"llm_requests": 0, "tokens_in": 0, "tokens_out": 0, "list_usd": 0.0}
    final, error = None, None

    def llm(tool_choice: str | None) -> Any:
        out = complete(model, cfg, quota, messages, tools=specs if tools else None, tool_choice=tool_choice)
        rec["llm_requests"] += 1
        rec["tokens_in"] += out["usage"].get("in", 0)
        rec["tokens_out"] += out["usage"].get("out", 0)
        rec["list_usd"] += out["list_usd"]
        return out["message"]

    try:
        while True:
            limit_hit = len(calls) >= max_calls
            if tools and limit_hit:
                choice = "none"
            elif tools and not calls and rec["llm_requests"] == 0:
                choice = first_tool_choice
            else:
                choice = None
            msg = llm(choice)
            tcs = getattr(msg, "tool_calls", None) or []
            if not tcs or not tools or limit_hit:
                final = msg.content or ""
                break
            messages.append(_msg_to_dict(msg))
            for tc in tcs:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                entry: dict[str, Any] = {"shown_name": name, "args": args}
                if len(calls) >= max_calls:
                    content = LIMIT_MSG
                    entry.update(tool=None, skipped="limit")
                elif name not in tools:
                    content = f"Unknown tool: {name}"
                    entry.update(tool=None, skipped="unknown_tool")
                else:
                    tool = tools[name]
                    res = quota.run_tool(tool, args, task.get("category"))
                    content = res.content[:max_chars] if res.ok else f"Error: {res.error}"
                    entry.update(tool=tool.name, ok=res.ok, error=res.error, latency_ms=res.latency_ms,
                                 cost_usd=res.cost_usd, content_len=len(res.content),
                                 truncated=len(res.content) > max_chars, meta=res.meta,
                                 content_head=res.content[:300], blocked=blocked_page(res.content))
                    if task.get("check_type") in ("exact", "contains", "regex") and res.ok:
                        # Diagnostic only (noisy for small numbers): did the raw output contain the answer?
                        from .scoring import check
                        entry["answer_in_content"] = bool(check(task, res.content[:max_chars]))
                if entry.get("tool"):
                    calls.append(entry)
                else:
                    rec.setdefault("rejected_calls", []).append(entry)
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": content})
    except Exception as e:  # QuotaPaused propagates; anything else is logged as a failed task
        from .quota import QuotaPaused
        if isinstance(e, QuotaPaused):
            raise
        error = f"{type(e).__name__}: {str(e)[:500]}"

    rec.update(final_answer=final, error=error, tool_calls=calls, n_tool_calls=len(calls),
               list_usd=round(rec["list_usd"] + sum(c.get("cost_usd", 0) for c in calls), 6))
    return rec
