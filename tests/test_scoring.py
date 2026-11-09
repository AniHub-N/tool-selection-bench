from types import SimpleNamespace

from bench.scoring import check, parse_verdict, route_taken, score

T_CONTAINS = {"id": "x", "question": "q?", "expected_answer": "Perth", "check_type": "contains"}
T_REGEX = {"id": "x", "question": "q?", "expected_answer": "247", "check_type": "regex", "pattern": r"\b247\b"}
T_EXACT = {"id": "x", "question": "q?", "expected_answer": "cbow", "check_type": "exact"}
T_JUDGE = {"id": "x", "question": "Tags?", "expected_answer": "life, navigation", "check_type": "judge"}


def test_check_types():
    assert check(T_CONTAINS, "The blacksmith is perth.")
    assert not check(T_CONTAINS, "Ahab")
    assert check(T_REGEX, "GF was 247.") and not check(T_REGEX, "2470")
    assert check(T_EXACT, "CBOW.") and not check(T_EXACT, "CBOW and Skip-gram")
    assert check(T_JUDGE, "anything") is None
    assert check(T_CONTAINS, None) is False and check(T_CONTAINS, "  ") is False


def test_parse_verdict():
    assert parse_verdict('{"verdict": "correct", "reason": "ok"}') == (True, "ok")
    assert parse_verdict('```json\n{"verdict":"incorrect","reason":"no"}\n```') == (False, "no")
    assert parse_verdict("The answer is incorrect.")[0] is False
    assert parse_verdict("Correct.")[0] is True
    assert parse_verdict("???")[0] is None


def _fake_complete(reply):
    calls = []

    def complete(model, cfg, quota, messages, tools=None, tool_choice=None):
        calls.append(messages)
        return {"message": SimpleNamespace(content=reply), "usage": {"in": 1, "out": 1}, "list_usd": 0.0}
    return complete, calls


def test_score_uses_judge_only_for_judge_tasks():
    cfg = {"judge": {"model": "flash"}}
    complete, calls = _fake_complete('{"verdict": "correct", "reason": "both tags"}')
    s = score(T_JUDGE, "life and navigation", cfg, quota=None, complete=complete)
    assert s["correct"] is True and s["method"] == "judge" and len(calls) == 1
    assert "life, navigation" in calls[0][0]["content"]  # reference goes into the fixed rubric
    s = score(T_CONTAINS, "Perth", cfg, quota=None, complete=complete)
    assert s == {"correct": True, "outcome": "correct", "method": "contains"} and len(calls) == 1


def test_score_skips_judge_on_empty_answer():
    complete, calls = _fake_complete("unused")
    s = score(T_JUDGE, "", {"judge": {"model": "flash"}}, quota=None, complete=complete)
    assert s["correct"] is False and not calls


def test_route_taken():
    task = {"url": "https://www.scrapingcourse.com/pagination"}
    assert route_taken({"tool_calls": []}, task) == "no_tool_call"
    same = {"tool": "firecrawl_scrape", "args": {"url": "https://scrapingcourse.com/pagination/"}}
    assert route_taken({"tool_calls": [same]}, task) == "start_url_only"
    other = {"tool": "plain_fetch", "args": {"url": "https://www.scrapingcourse.com/pagination/3"}}
    assert route_taken({"tool_calls": [same, other]}, task) == "url_route"
    agent = {"tool": "tinyfish_agent", "args": {"url": task["url"], "goal": "find"}}
    assert route_taken({"tool_calls": [other, agent]}, task) == "browser_agent"


def test_abstained_is_separate_from_wrong():
    from bench.scoring import outcome_of
    complete, calls = _fake_complete('{"verdict": "incorrect", "reason": "x"}')
    cfg = {"judge": {"model": "flash"}}
    for empty in (None, "", "  \n"):
        for t in (T_JUDGE, T_CONTAINS):
            sc = score(t, empty, cfg, quota=None, complete=complete)
            assert sc["outcome"] == "abstained" and sc["correct"] is False
    assert not calls  # abstentions never reach the judge
    assert score(T_CONTAINS, "Sydney", cfg, quota=None, complete=complete)["outcome"] == "wrong"
    assert score(T_JUDGE, "life only", cfg, quota=None, complete=complete)["outcome"] == "wrong"
    # an explicit "can't find it" is an answer, graded wrong, not an abstention
    assert score(T_CONTAINS, "I could not find this.", cfg, quota=None, complete=complete)["outcome"] == "wrong"
    # old rows without `outcome`
    assert outcome_of({"correct": False, "method": "contains"}, "") == "abstained"
    assert outcome_of({"correct": False, "method": "judge_skipped_empty"}, None) == "abstained"
    assert outcome_of({"correct": False, "method": "contains"}, "Sydney") == "wrong"
    assert outcome_of({"correct": None, "method": "judge_pending"}, "x") == "pending"
