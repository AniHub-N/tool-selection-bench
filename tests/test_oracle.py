"""Oracle cell selection and table rendering. No network."""

from types import SimpleNamespace

from bench.config import load_config
from bench.oracle import cell, cells_for, oracle_table

TASKS = [{"id": "s1", "category": "static"}, {"id": "i1", "category": "interactive", "url_navigable": True}]


def test_agent_only_on_oracle_categories():
    cfg = load_config()
    tools = [SimpleNamespace(name="plain_fetch"), SimpleNamespace(name="tinyfish_agent")]
    got = [(t["id"], tl.name) for t, tl in cells_for(TASKS, tools, cfg)]
    assert got == [("s1", "plain_fetch"), ("i1", "plain_fetch"), ("i1", "tinyfish_agent")]


def row(tid, tool, correct, cat="static", **kw):
    return {"task_id": tid, "tool": tool, "category": cat, "score": {"correct": correct, **kw.pop("score", {})},
            "final_answer": "a", "llm_requests": 2, "tokens_in": 5, "tokens_out": 1, **kw}


def test_cell_flags():
    assert cell(None) == "-"
    assert cell({"error": "boom", "final_answer": None, "score": {"correct": False}}) == "ERR"
    r = row("s1", "plain_fetch", False, score={"format_suspect": True},
            tool_calls=[{"blocked": "just a moment...", "error": "x", "answer_in_content": True}])
    assert cell(r) == "fail B E F? R"


def test_table_renders():
    rows = [row("s1", "plain_fetch", True, tool_calls=[{"latency_ms": 100}]),
            row("i1", "tinyfish_agent", False, cat="interactive",
                tool_calls=[{"latency_ms": 9000, "cost_usd": 0.048, "meta": {"steps": 3}}])]
    t = oracle_table(rows, TASKS)
    assert "| s1 | static | PASS | - |" in t
    assert "url_nav" in t and "$0.048" in t and "| tf_agent | - | - | - | 0/1 | 0/1 |" in t
