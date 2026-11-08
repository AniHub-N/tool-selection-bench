"""Task success checks, the LLM judge, and per-call route classification."""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlsplit

JUDGE_PROMPT = """You are grading an answer to a factual question about a specific web page or document.

Question: {question}
Reference answer: {reference}
Candidate answer: {candidate}

Rubric:
- CORRECT if the candidate states the reference answer or an exact equivalent. Ignore formatting,
  capitalisation, units written differently, and extra explanation.
- If the reference is a list, the candidate must name every item and no other items. Order doesn't matter.
- INCORRECT if the candidate gives a different value, offers several conflicting answers, hedges
  between options, or says it cannot access or find the information.
- Judge only against the reference answer. Do not use your own knowledge of the topic.

Reply with JSON only: {{"verdict": "correct" or "incorrect", "reason": "<one sentence>"}}"""


def check(task: dict[str, Any], answer: str | None) -> bool | None:
    """Deterministic check. Returns None for judge tasks."""
    a = (answer or "").strip()
    ct = task["check_type"]
    if ct == "judge":
        return None
    if not a:
        return False
    if ct == "exact":
        return a.lower().rstrip(".") == task["expected_answer"].lower()
    if ct == "contains":
        return task["expected_answer"].lower() in a.lower()
    if ct == "regex":
        return re.search(task["pattern"], a, re.I) is not None
    raise ValueError(f"unknown check_type {ct}")


def parse_verdict(text: str) -> tuple[bool | None, str]:
    """Parse the judge's JSON. Tolerates code fences and surrounding text."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            v = str(d.get("verdict", "")).strip().lower()
            if v in ("correct", "incorrect"):
                return v == "correct", str(d.get("reason", ""))
        except json.JSONDecodeError:
            pass
    low = (text or "").lower()
    if '"incorrect"' in low or re.search(r"\bincorrect\b", low):
        return False, "unparsed: " + (text or "")[:200]
    if re.search(r"\bcorrect\b", low):
        return True, "unparsed: " + (text or "")[:200]
    return None, "unparseable: " + (text or "")[:200]


def judge(task: dict[str, Any], answer: str, cfg: dict[str, Any], quota: Any,
          complete: Callable[..., dict] | None = None) -> dict[str, Any]:
    if complete is None:
        from .llm import complete
    prompt = JUDGE_PROMPT.format(question=task["question"], reference=task["expected_answer"], candidate=answer)
    out = complete(cfg["judge"]["model"], cfg, quota, [{"role": "user", "content": prompt}])
    ok, reason = parse_verdict(out["message"].content or "")
    return {"correct": ok, "reason": reason, "raw": out["message"].content, "list_usd": out["list_usd"]}


def score(task: dict[str, Any], answer: str | None, cfg: dict[str, Any], quota: Any,
          complete: Callable[..., dict] | None = None) -> dict[str, Any]:
    """{"correct": bool|None, "method": ..., "judge": {...}?}. Empty answers fail without a judge call."""
    det = check(task, answer)
    if det is not None:
        return {"correct": det, "method": task["check_type"]}
    if not (answer or "").strip():
        return {"correct": False, "method": "judge_skipped_empty"}
    j = judge(task, answer or "", cfg, quota, complete)
    return {"correct": j["correct"], "method": "judge", "judge": j}


def _norm(url: str) -> str:
    p = urlsplit(url.strip())
    path = p.path.rstrip("/") or "/"
    return f"{p.netloc.lower().removeprefix('www.')}{path}?{p.query}"


def route_taken(record: dict[str, Any], task: dict[str, Any]) -> str:
    """How the agent tried to reach the answer:
    no_tool_call | browser_agent (called tinyfish_agent) | url_route (fetched a URL other
    than the start URL) | start_url_only."""
    calls = record.get("tool_calls") or []
    if not calls:
        return "no_tool_call"
    if any(c.get("tool") == "tinyfish_agent" for c in calls):
        return "browser_agent"
    start = _norm(task["url"])
    if any(_norm(str(c.get("args", {}).get("url", ""))) != start for c in calls):
        return "url_route"
    return "start_url_only"
