"""Answer-format tolerance. The correct value in a different format must PASS; a wrong
value in the right format must FAIL. A formatting miss must never look like a tool failure."""

import json
from pathlib import Path

import pytest

from bench.normalize import dates, numbers
from bench.scoring import check, score

TASKS = {t["id"]: t for t in (json.loads(l) for l in
         open(Path(__file__).parents[1] / "tasks" / "tasks.jsonl", encoding="utf-8"))}

# (task id, answers that must pass, wrong values in a plausible format that must fail)
CASES = [
    ("j07", ["12th Dec 11", "2011-12-12", "December 12, 2011", "12 December 2011", "Dec 12th, 2011",
             "12/12/2011", "12-Dec-2011", "Her start date is Monday 12th December 2011."],
            ["13th Dec 11", "2011-12-13", "December 12, 2012", "2011-11-12", "Dec 2011"]),
    ("j06", ["2008-11-28", "28/11/2008", "11/28/2008", "November 28, 2008", "28th Nov 08"],
            ["2008-11-29", "28/11/2009", "November 27, 2008"]),
    ("i08", ["2012-06-01", "1st June 2012", "June 1, 2012", "01/06/2012", "2012/6/1"],
            ["2012-06-02", "2nd June 2012", "June 1, 2013"]),
    ("s06", ["-1000 to 1000", "from −1000 to 1000", "between -1,000 and +1,000", "[-1000, 1000]",
             "–1000 to 1000", "-1000 – 1000", "The scale may range from -1000 up to 1000."],
            ["0 to 1000", "1000 to 1000", "-1000 to 16383", "-100 to 100", "16383"]),
    ("p09", ["2.1×10^6", "2.1 x 10^6", "2.1e6", "2.1E+06", "2,100,000", "2100000", "2.1 million",
             "2.1M steps", "2.1 · 10⁶", "2.1 \\times 10^{6}", "2.1*10**6"],
            ["2.1×10^7", "2.1e5", "21,000,000", "1.2 million", "3.1 x 10^6", "210,000"]),
    ("p02", ["2.3×10^19", "2.3 x 10^19 FLOPs", "2.3e19", "2.3E+19", "23,000,000,000,000,000,000",
             "2.3 · 10¹⁹"],
            ["2.3×10^18", "3.2e19", "2.3 x 10^20", "1.0 x 10^19"]),
    ("p01", ["0.0009137", "9.137e-4", "9.137 × 10^-4", "9.137×10⁻⁴"],
            ["0.009137", "0.0009173", "9.137e-3"]),
    ("s03", ["13254", "13,254", "13 254", "13254 people"], ["13,245", "132,540", "1325"]),
    ("s07", ["5874897 AD", "5,874,897 AD", "5874897"], ["5874898 AD", "4713 BC"]),
    ("p08", ["1.5 percent", "1.5%", "up 1.5 %"], ["1.3 percent", "15%", "0.15"]),
    ("p05", ["0.9203", "92.03%"], ["0.9230", "92.3%", "0.7756"]),
    ("i09", ["12", "twelve", "There are 12 humor quotes."], ["13", "eleven"]),
    ("j04", ["$64", "$64.00", "64 dollars", "USD 64"], ["$46", "$64.50", "$640"]),
    ("i07", ["7439", "Extension 7439."], ["7493", "74390"]),
]


@pytest.mark.parametrize("tid,good,bad", CASES, ids=[c[0] for c in CASES])
def test_format_variants(tid, good, bad):
    task = TASKS[tid]
    for a in good:
        assert check(task, a), f"{tid}: correct value in another format failed: {a!r}"
    for a in bad:
        assert not check(task, a), f"{tid}: wrong value passed: {a!r}"


def test_every_value_task_is_covered_by_its_own_reference():
    for t in TASKS.values():
        if "value" in t:
            assert check(t, t["expected_answer"]), t["id"]


def test_numbers_parse():
    assert numbers("2.1 × 10^6") == [2.1e6]
    assert numbers("2008-11-28") == [2008, 11, 28]   # date hyphens are not minus signs
    assert -1000 in numbers("from -1000 to 1000") and 1000 in numbers("from -1000 to 1000")


def test_dates_parse():
    from datetime import date
    assert date(2011, 12, 12) in dates("12th Dec 11")
    assert dates("on 31/02/2020") == []               # impossible dates are dropped


def test_format_miss_is_flagged_not_silent():
    """An unforeseen format that still contains the reference is marked for review."""
    t = {"id": "x", "check_type": "regex", "pattern": r"\b13254\b", "expected_answer": "13254"}
    s = score(t, "13.254 (European separators)", {}, None)
    assert s["correct"] is False and s["format_suspect"] is True
    s = score(t, "9999", {}, None)
    assert s["format_suspect"] is False


def test_accent_insensitive_text():
    assert check(TASKS["j02"], "Andre Gide")
    assert check(TASKS["i04"], "Dr Seuss")
