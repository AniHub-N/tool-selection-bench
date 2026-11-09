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


# ---- position tasks: right URL AND the name at the asked position ---------------

MD_PAGE = ("“q1”\nby Albert Einstein\n[(about)](/author/A)\n\n“q2”by J.K. Rowling [(about)](x)\n\n"
           "“q3”by Jane Austen\n\nTags: x\n")


def test_ordered_authors_all_renderings():
    from bench.scoring import ordered_authors
    assert ordered_authors(MD_PAGE) == ["Albert Einstein", "J.K. Rowling", "Jane Austen"]
    js = '{"quotes":[{"author":{"name":"Albert Einstein"}},{"author":{"name":"Andr\u00e9 Gide"}}]}'
    assert ordered_authors(js) == ["Albert Einstein", "André Gide"]


def _page(names):
    return "\n\n".join(f"“q{i}”by {n}" for i, n in enumerate(names)) + "\n"


def test_position_needs_right_url_and_position():
    j02 = TASKS_FILE["j02"]
    names = ["A", "B", "A", "C", "D", "A", "André Gide", "E"]
    good = "https://quotes.toscrape.com/js-delayed/"
    assert evidence_present(j02, _page(names), "firecrawl_scrape", good) is True
    # same data fetched from the homepage: wrong page, no credit
    assert evidence_present(j02, _page(names), "firecrawl_scrape", "https://quotes.toscrape.com/") is False
    # right page, but the name is elsewhere (6th, not 7th)
    names2 = ["A", "B", "A", "C", "D", "André Gide", "X", "E"]
    assert evidence_present(j02, _page(names2), "firecrawl_scrape", good) is False
    # right page that never rendered the quotes
    assert evidence_present(j02, "# Quotes to Scrape\n\nLogin", "plain_fetch", good) is False


def test_position_last_and_first():
    j10, i04 = TASKS_FILE["j10"], TASKS_FILE["i04"]
    p2 = "https://quotes.toscrape.com/js/page/2/"
    assert evidence_present(j10, _page(["X", "Y", "Allen Saunders"]), "tinyfish_fetch", p2) is True
    assert evidence_present(j10, _page(["Allen Saunders", "Y"]), "tinyfish_fetch", p2) is False
    for url in ("https://quotes.toscrape.com/page/4/", "https://quotes.toscrape.com/js/page/4",
                "https://quotes.toscrape.com/api/quotes?page=4"):
        content = (_page(["Dr. Seuss", "Z"]) if "api" not in url
                   else '{"quotes":[{"author":{"name":"Dr. Seuss"}}]}')
        assert evidence_present(i04, content, "plain_fetch", url) is True, url
    assert evidence_present(i04, _page(["Dr. Seuss"]), "plain_fetch", "https://quotes.toscrape.com/page/3/") is False


def test_can_solve_passes_call_url(tmp_path):
    j10 = TASKS_FILE["j10"]
    r = make_row(tmp_path, j10, "firecrawl_scrape", [_page(["X", "Allen Saunders"])], correct=True)
    r["tool_calls"][0]["args"] = {"url": "https://quotes.toscrape.com/js/"}  # page 1, wrong
    assert can_solve(r, j10) is False
    r["tool_calls"][0]["args"] = {"url": "https://quotes.toscrape.com/js/page/2/"}
    assert can_solve(r, j10) is True


# ---- hand labels for aggregate tasks ---------------------------------------------

def test_hand_label_used_only_for_the_exact_raw_outputs(tmp_path):
    t = TASKS_FILE["i09"]
    r = make_row(tmp_path, t, "firecrawl_scrape", ["humor page 1", "humor page 2"], True)
    refs = [c["raw_ref"] for c in r["tool_calls"]]
    labels = {"i09": {"firecrawl_scrape": {"can": True, "hand_labeled": True, "raw_refs": refs, "note": "x"}}}
    assert can_solve(r, t, labels) is True
    stale = {"i09": {"firecrawl_scrape": {"can": True, "hand_labeled": True, "raw_refs": ["raw/old.txt"]}}}
    assert can_solve(r, t, stale) is None   # cell was re-run: label no longer applies


def test_judge_error_becomes_pending_not_crash():
    from bench.oracle import score_or_pending

    def boom(*a, **k):
        raise TimeoutError("judge timed out")
    import bench.scoring as sc
    orig = sc.judge
    sc.judge = boom
    try:
        t = {"id": "j", "check_type": "judge", "question": "q", "expected_answer": "4"}
        assert score_or_pending(t, "four", {}, None)["method"] == "judge_pending"
    finally:
        sc.judge = orig


def test_datatables_record_spans_lines_but_not_rows():
    j06 = TASKS_FILE["j06"]
    arrays = '[\n "Airi Satou",\n "Accountant",\n "Tokyo",\n "5407",\n "2008-11-28",\n "$162,700"\n],'
    assert evidence_present(j06, arrays) is True
    assert evidence_present(j06, "| Airi Satou | Accountant | Tokyo | 28th Nov 08 |") is True
    other = '[\n "Airi Satou",\n "Accountant",\n "2009-01-01"\n],\n[\n "X",\n "2008-11-28"\n]'
    assert evidence_present(j06, other) is False   # the date belongs to the next record


def test_agent_gap_tasks_get_agent_cells(tmp_path):
    from bench.oracle import agent_gap_ids
    cfg = load_config()
    J = {"id": "j1", "category": "js_heavy", "check_type": "contains", "expected_answer": "zebra"}
    rows = [make_row(tmp_path, J, tl, ["no answer here"], False)
            for tl in ("plain_fetch", "firecrawl_scrape", "tinyfish_fetch")]
    rows += [make_row(tmp_path, S1, tl, ["a897fe39"], True) for tl in ("plain_fetch", "firecrawl_scrape")]
    # S1 lacks a tinyfish_fetch cell, and one tool can solve it anyway: not a gap
    assert agent_gap_ids([J, S1], rows) == ["j1"]
    tools = [SimpleNamespace(name="tinyfish_agent")]
    assert [t["id"] for t, _ in cells_for([J, S1], tools, cfg, {"j1"})] == ["j1"]
    assert cells_for([J, S1], tools, cfg) == []


def test_abstained_cell(tmp_path):
    r = make_row(tmp_path, S1, "plain_fetch", ["nothing"], False)
    r["final_answer"] = ""
    assert cell(r, S1) == "n abst"
    assert "s1/plain" in oracle_table([r], [S1]).split("**Abstained**")[1].splitlines()[0]
