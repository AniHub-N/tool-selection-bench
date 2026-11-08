"""Task success checks, the LLM judge, and per-call route classification."""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from .normalize import loose, value_match

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
    # A correct value in another format (2.1e6 vs 2.1×10^6, 2011-12-12 vs "12th Dec 11") passes.
    if task.get("value") and value_match(task["value"], a):
        return True
    if ct == "exact":
        return a.lower().rstrip(".") == task["expected_answer"].lower()
    if ct == "contains":
        exp = task["expected_answer"]
        if exp.lower() in a.lower():
            return True
        # Accent/punctuation-insensitive for text only ("Andre Gide", "Dr Seuss"); numbers use `value`.
        return not re.search(r"\d", exp) and loose(exp) in loose(a)
    if ct == "regex":
        return re.search(task["pattern"], a, re.I) is not None
    raise ValueError(f"unknown check_type {ct}")


_AUTHOR_MD = re.compile(r"[”\"]\s*by\s+([^\n\[\]]+?)(?=\s*(?:\[\(about\)|\n|$))")
_AUTHOR_JSON = re.compile(r'"name"\s*:\s*"([^"]+)"')


def ordered_authors(content: str) -> list[str]:
    """quotes.toscrape author names in page order, from any tool's rendering: markdown
    ('”by Name [(about)]', '”\\nby Name\\n', '”by Name\\n\\n') or the /api/quotes JSON."""
    c = content or ""
    if c.lstrip().startswith("{"):
        try:
            return [q["author"]["name"] for q in json.loads(c).get("quotes", [])]
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            return _AUTHOR_JSON.findall(c)
    return [m.strip().strip("*").strip() for m in _AUTHOR_MD.findall(c)]


def _position_ok(pos: dict[str, Any], content: str, url: str | None) -> bool:
    if not url or not any(re.search(p, url.strip()) for p in pos["urls"]):
        return False  # not the page the question asks about
    names = ordered_authors(content)
    i = pos["index"]
    idx = i - 1 if i > 0 else i
    return -len(names) <= idx < len(names) and re.search(pos["name"], names[idx]) is not None


def evidence_present(task: dict[str, Any], content: str, tool_name: str | None = None,
                     url: str | None = None) -> bool | None:
    """Oracle definition of "this tool can solve this task": is the answer present in this RAW
    tool output? None = presence can't decide it (aggregate answers -> hand labels, or a judge
    task with no evidence pattern). The browser agent's output is an answer, not a page, so it
    is checked like one. Position tasks also need the right URL and the name at the asked
    position (`url` = the URL this output was fetched from)."""
    ev = task.get("evidence") or {}
    if tool_name == "tinyfish_agent":
        return check(task, content) if task["check_type"] != "judge" else (
            all(re.search(p, content or "", re.I) for p in ev["all"]) if ev.get("all") else None)
    if ev.get("aggregate"):
        return None
    if ev.get("position"):
        return _position_ok(ev["position"], content, url)
    if ev.get("all"):
        return all(re.search(p, content or "", re.I) for p in ev["all"])
    return check(task, content)


def format_suspect(task: dict[str, Any], answer: str | None) -> bool:
    """A failed deterministic check whose answer still contains the reference's letters and
    digits in order (e.g. an unforeseen format). Flagged for hand review, never silently
    counted as a tool failure."""
    core = loose(task["expected_answer"].split("(")[0])
    return bool(core) and core in loose(answer or "")


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
        out: dict[str, Any] = {"correct": det, "method": task["check_type"]}
        if not det and (answer or "").strip():
            out["format_suspect"] = format_suspect(task, answer)
        return out
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
