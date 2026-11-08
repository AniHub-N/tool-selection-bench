"""Oracle: cell selection, the can-solve verdict (answer present in raw output) kept separate
from model extraction, and table rendering. No network."""

import json
from pathlib import Path
from types import SimpleNamespace

from bench.config import load_config
from bench.oracle import can_solve, cell, cells_for, oracle_table, raw_sink
from bench.scoring import evidence_present

TASKS_FILE = {t["id"]: t for t in (json.loads(l) for l in
              open(Path(__file__).parents[1] / "tasks" / "tasks.jsonl", encoding="utf-8"))}
S1 = {"id": "s1", "category": "static", "check_type": "contains", "expected_answer": "a897fe39"}
I1 = {"id": "i1", "category": "interactive", "url_navigable": True, "check_type": "regex",
      "pattern": r"\$\s?99\b", "expected_answer": "$99"}


def test_agent_only_on_oracle_categories():
    cfg = load_config()
    tools = [SimpleNamespace(name="plain_fetch"), SimpleNamespace(name="tinyfish_agent")]
    got = [(t["id"], tl.name) for t, tl in cells_for([S1, I1], tools, cfg)]
    assert got == [("s1", "plain_fetch"), ("i1", "plain_fetch"), ("i1", "tinyfish_agent")]


def make_row(tmp_path, task, tool, contents, correct):
    sink = raw_sink(tmp_path)
    calls = [{"tool": tool, "ok": True, "raw_ref": sink(c), "latency_ms": 100} for c in contents]
    return {"task_id": task["id"], "tool": tool, "category": task["category"], "final_answer": "x",
            "score": {"correct": correct}, "tool_calls": calls, "llm_requests": 2, "tokens_in": 1,
            "tokens_out": 1, "_run_path": str(tmp_path)}


def test_extraction_miss_does_not_count_against_tool(tmp_path):
    r = make_row(tmp_path, S1, "plain_fetch", ["UPC a897fe39b1053632"], correct=False)
    assert can_solve(r, S1) is True and cell(r, S1) == "Y fail"


def test_answer_without_evidence(tmp_path):
    r = make_row(tmp_path, S1, "plain_fetch", ["nothing here"], correct=True)
    assert can_solve(r, S1) is False and cell(r, S1) == "n PASS"


def test_any_call_in_cell_counts(tmp_path):
    r = make_row(tmp_path, S1, "plain_fetch", ["page 1", "page 3: a897fe39"], correct=True)
    assert can_solve(r, S1) is True


def test_aggregate_task_is_undecidable_for_fetch_but_checked_for_agent(tmp_path):
    t = TASKS_FILE["i09"]
    assert can_solve(make_row(tmp_path, t, "plain_fetch", ["12 quotes"], True), t) is None
    assert can_solve(make_row(tmp_path, t, "tinyfish_agent", ['{"result": "There are 12."}'], True), t) is True


def test_context_evidence_for_prices_and_small_numbers():
    j04 = TASKS_FILE["j04"]
    page = "Abominable Hoodie $69 ... Grayson Crewneck Sweatshirt ![img](x.png) $64 ... Other $12"
    assert evidence_present(j04, page) is True
    assert evidence_present(j04, "Grayson Crewneck Sweatshirt $46 ... Other $64") is False
    s02 = TASKS_FILE["s02"]
    assert evidence_present(s02, "Tipping the Velvet ... In stock (20 available)") is True
    assert evidence_present(s02, "20 results, page 1 of 20") is False
    s08 = TASKS_FILE["s08"]
    assert evidence_present(s08, "| Boston Bruins | 1990 | 44 | 24 |") is True
    assert evidence_present(s08, "| Boston Bruins | 1991 | 36 | 44 |") is False


def test_table_shows_both_verdicts(tmp_path):
    rows = [make_row(tmp_path, S1, "plain_fetch", ["a897fe39"], False),
            make_row(tmp_path, I1, "tinyfish_agent", ['{"result": "$99"}'], True)]
    rows[1]["tool_calls"][0].update(cost_usd=0.048, meta={"steps": 3})
    t = oracle_table(rows, [S1, I1])
    assert "| s1 | static | Y fail | - |" in t and "| i1 | interactive | - | Y PASS | url_nav |" in t
    assert "s1/plain" in t.split("Extraction misses")[1].splitlines()[0]
    assert "$0.048" in t
