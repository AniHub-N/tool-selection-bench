"""Free-choice analysis against the oracle. No API calls.

Per row (task x rep):
- first pick: the first tool the model called (the selection decision).
- pick verdict: the oracle `can` for (task, first pick): Y = a tool that can solve it, n = can't,
  ? = no oracle verdict (agent pick on a task outside the agent's oracle cells, or an aggregate
  task without a hand label for that cell). Unknowns are reported, never counted as right/wrong.
- feasible: some oracle tool can solve the task.
- regret: the row did not end correct although a can=Y tool existed. Split into a selection miss
  (first pick was can=n) vs. a miss after a good first pick (extraction / navigation).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .oracle import CATS, TOOL_ABBR, TOOL_ORDER, can_solve, merged_for
from .runlog import RunDir
from .scoring import outcome_of


def latest(run: RunDir) -> list[dict]:
    last: dict[tuple, dict] = {}
    for r in run.records("results"):
        last[(r["task_id"], r["rep"])] = r
    return list(last.values())


def oracle_can(tasks: list[dict]) -> dict[tuple[str, str], bool | None]:
    tid = {t["id"]: t for t in tasks}
    return {(r["task_id"], r["tool"]): can_solve(r, tid[r["task_id"]]) for r in merged_for(tasks)}


def first_pick(row: dict) -> str | None:
    calls = row.get("tool_calls") or []
    return calls[0]["tool"] if calls else None


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "-"


def summarize(run_path: Path, tasks: list[dict]) -> str:
    run = RunDir(run_path)
    rows = sorted(latest(run), key=lambda r: (r["rep"], [t["id"] for t in tasks].index(r["task_id"])))
    tid = {t["id"]: t for t in tasks}
    can = oracle_can(tasks)
    feasible = {t["id"]: any(can.get((t["id"], tl)) is True for tl in TOOL_ORDER) for t in tasks}
    out = [f"# Free choice: {run.meta.get('model')} ({run_path.name})\n",
           f"{len(rows)} rows ({len({r['task_id'] for r in rows})} tasks x up to "
           f"{run.meta.get('reps')} reps). Status: {run.meta.get('status')}.\n"]

    # outcomes
    oc: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        oc[r["category"]][outcome_of(r["score"], r.get("final_answer"))] += 1
    out += ["## Outcomes\n", "| category | correct | wrong | abstained | pending | n |", "|---|---|---|---|---|---|"]
    tot: Counter = Counter()
    for c in CATS:
        k = oc[c]; tot.update(k)
        out.append(f"| {c} | {k['correct']} | {k['wrong']} | {k['abstained']} | {k['pending']} | {sum(k.values())} |")
    out.append(f"| **total** | {tot['correct']} | {tot['wrong']} | {tot['abstained']} | {tot['pending']} | {sum(tot.values())} |\n")

    # pick distribution
    picks: dict[str, Counter] = defaultdict(Counter)
    allcalls: Counter = Counter()
    for r in rows:
        picks[r["category"]][first_pick(r) or "none"] += 1
        for c in r.get("tool_calls") or []:
            allcalls[c["tool"]] += 1
    cols = TOOL_ORDER + ["none"]
    out += ["## First pick by category\n", "| category | " + " | ".join(TOOL_ABBR.get(c, c) for c in cols) + " |",
            "|---|" + "---|" * len(cols)]
    for c in CATS:
        out.append(f"| {c} | " + " | ".join(str(picks[c][x]) for x in cols) + " |")
    out.append("\nAll tool calls (any position): " + ", ".join(f"{TOOL_ABBR[t]} {allcalls[t]}" for t in TOOL_ORDER) + "\n")

    # selection accuracy vs oracle
    sel: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        fp = first_pick(r)
        v = "none" if fp is None else {True: "Y", False: "n", None: "?"}[can.get((r["task_id"], fp))]
        sel[r["category"]][v] += 1
        sel["total"][v] += 1
    out += ["## Selection accuracy (first pick vs oracle `can`)\n",
            "Y = first pick can solve the task, n = it can't, ? = no oracle verdict for that cell (excluded).\n",
            "| category | Y | n | ? | accuracy (Y / (Y+n)) |", "|---|---|---|---|---|"]
    for c in CATS + ["total"]:
        k = sel[c]
        out.append(f"| {c} | {k['Y']} | {k['n']} | {k['?']} | {_pct(k['Y'], k['Y'] + k['n'])} |")

    # outcome conditional on pick verdict
    cond: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        fp = first_pick(r)
        v = "none" if fp is None else {True: "Y", False: "n", None: "?"}[can.get((r["task_id"], fp))]
        cond[v][outcome_of(r["score"], r.get("final_answer"))] += 1
    out += ["\n**Outcome by first-pick verdict**\n", "| first pick | correct | wrong | abstained | pending |", "|---|---|---|---|---|"]
    for v in ("Y", "n", "?", "none"):
        if cond[v]:
            k = cond[v]
            out.append(f"| {v} | {k['correct']} | {k['wrong']} | {k['abstained']} | {k['pending']} |")

    # regret
    sel_miss, after_good, infeasible_fail = [], [], []
    for r in rows:
        o = outcome_of(r["score"], r.get("final_answer"))
        if o in ("correct", "pending"):
            continue
        tag = f"{r['task_id']}r{r['rep']}"
        if not feasible[r["task_id"]]:
            infeasible_fail.append(tag)
            continue
        v = can.get((r["task_id"], first_pick(r) or ""))
        (after_good if v is True else sel_miss).append(f"{tag}({o[:4]})")
    n_feas = sum(feasible[r["task_id"]] for r in rows)
    out += [f"\n## Regret\n",
            f"Rows on feasible tasks (some tool can solve): {n_feas}. Not correct despite a can=Y tool: "
            f"{len(sel_miss) + len(after_good)}.\n",
            f"- after a can=n / unknown first pick (selection): {len(sel_miss)}: {', '.join(sel_miss) or '-'}",
            f"- after a can=Y first pick (extraction, wrong page, gave up): {len(after_good)}: {', '.join(after_good) or '-'}",
            f"- infeasible tasks (no tool can): {', '.join(infeasible_fail) or '-'}\n"]

    # consistency
    by_task: dict[str, list] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(first_pick(r))
    full = {k: v for k, v in by_task.items() if len(v) == run.meta.get("reps")}
    same = sum(len(set(v)) == 1 for v in full.values())
    out.append(f"## Consistency\n\nSame first pick in all reps: {_pct(same, len(full))} tasks.\n")
    varied = [f"{k}: {'/'.join(TOOL_ABBR.get(x, 'none') if x else 'none' for x in v)}" for k, v in full.items() if len(set(v)) > 1]
    if varied:
        out.append("Varied: " + "; ".join(varied) + "\n")

    # cost / latency
    calls = [c for r in rows for c in r.get("tool_calls") or []]
    cred = sum((c.get("meta") or {}).get("credits", 0) or 0 for c in calls if c["tool"] == "firecrawl_scrape")
    steps = sum((c.get("meta") or {}).get("steps", 0) or 0 for c in calls if c["tool"] == "tinyfish_agent")
    usd = sum(c.get("cost_usd") or 0 for c in calls if c["tool"] == "tinyfish_agent")
    lat = sorted(sum(c.get("latency_ms") or 0 for c in r.get("tool_calls") or []) for r in rows)
    out += ["## Cost and latency\n",
            f"- LLM requests {sum(r.get('llm_requests', 0) for r in rows)}, tokens in/out "
            f"{sum(r.get('tokens_in', 0) for r in rows)}/{sum(r.get('tokens_out', 0) for r in rows)}",
            f"- tool calls {len(calls)} (mean {len(calls) / max(len(rows), 1):.2f}/row); Firecrawl credits {cred:g}; "
            f"TinyFish agent {steps} steps, ${usd:.3f}",
            f"- tool time per row: median {lat[len(lat) // 2] / 1000 if lat else 0:.1f} s, max {lat[-1] / 1000 if lat else 0:.1f} s",
            f"- list price of the run: ${sum(r.get('list_usd', 0) for r in rows):.3f} (actual $0)"]
    return "\n".join(out)
