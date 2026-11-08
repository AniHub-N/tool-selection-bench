"""Static checks on tasks/tasks.jsonl. No network."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

TASKS = [json.loads(l) for l in open(Path(__file__).parents[1] / "tasks" / "tasks.jsonl", encoding="utf-8")]
REQUIRED = {"id", "category", "url", "question", "expected_answer", "check_type"}
# Plausible wrong or hedging answers that must not pass a deterministic check.
DECOYS = ["I don't know.", "None", "The page did not load, so I can't tell.",
          "It is 1, 2, 3, 10 or 100.", "Albert Einstein", "$0.00", "2024-01-01", "0.5"]


def matches(task, answer):
    if task["check_type"] == "contains":
        return task["expected_answer"].lower() in answer.lower()
    if task["check_type"] == "regex":
        return re.search(task["pattern"], answer, re.I) is not None
    raise ValueError(task["check_type"])


def test_shape():
    assert len(TASKS) == 40
    assert len({t["id"] for t in TASKS}) == 40
    assert Counter(t["category"] for t in TASKS) == {"static": 10, "js_heavy": 10, "pdf": 10, "interactive": 10}
    for t in TASKS:
        assert REQUIRED <= t.keys(), t["id"]
        assert t["check_type"] in {"exact", "contains", "regex", "judge"}
        assert t["url"].startswith("https://")
        if t["check_type"] == "regex":
            re.compile(t["pattern"])


@pytest.mark.parametrize("task", [t for t in TASKS if t["check_type"] != "judge"], ids=lambda t: t["id"])
def test_expected_answer_passes_its_own_check(task):
    assert matches(task, task["expected_answer"])
    assert matches(task, f"According to the page, the answer is {task['expected_answer']}.")


@pytest.mark.parametrize("task", [t for t in TASKS if t["check_type"] != "judge"], ids=lambda t: t["id"])
def test_decoys_fail(task):
    for d in DECOYS:
        assert not matches(task, d), f"{task['id']} accepts decoy {d!r}"
