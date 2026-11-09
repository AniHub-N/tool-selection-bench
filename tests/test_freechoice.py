"""Free-choice summary: selection verdicts vs the oracle, unknown agent picks, regret split."""

import json

from bench import freechoice

TASKS = [{"id": "s1", "category": "static"}, {"id": "j1", "category": "js_heavy"}]


def row(tid, cat, rep, tools, outcome, answer="x"):
    correct = {"correct": True, "wrong": False, "abstained": False}[outcome]
    return {"task_id": tid, "category": cat, "rep": rep, "final_answer": "" if outcome == "abstained" else answer,
            "score": {"correct": correct, "outcome": outcome},
            "tool_calls": [{"tool": t, "latency_ms": 100, "meta": {}} for t in tools], "llm_requests": 2}


def test_summary(tmp_path, monkeypatch):
    rows = [row("s1", "static", 0, ["plain_fetch"], "correct"),
            row("j1", "js_heavy", 0, ["plain_fetch"], "abstained"),          # can=n pick -> selection regret
            row("j1", "js_heavy", 1, ["firecrawl_scrape"], "wrong"),         # can=Y pick -> extraction regret
            row("s1", "static", 1, ["tinyfish_agent"], "correct")]           # no oracle cell -> "?"
    (tmp_path / "meta.json").write_text(json.dumps({"model": "m", "reps": 2, "status": "complete"}))
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(freechoice, "oracle_can", lambda tasks: {
        ("s1", "plain_fetch"): True, ("j1", "plain_fetch"): False, ("j1", "firecrawl_scrape"): True})
    out = freechoice.summarize(tmp_path, TASKS)
    assert "| total | 2 | 1 | 1 | 2/3 (67%) |" in out
    assert "(selection): 1: j1r0(abst)" in out
    assert "gave up): 1: j1r1(wron)" in out
    assert "Same first pick in all reps: 0/2 (0%)" in out
