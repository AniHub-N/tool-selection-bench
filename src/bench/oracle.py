"""Oracle sweep: for each task x tool the agent gets ONLY that tool (neutral description) and
must call it on its first turn (tool_choice="required"). It may make up to max_tool_calls calls,
then answers. One rep. Shows which tools CAN solve each task; results are cached in
runs/<ts>_<model>_oracle/ and reused by the report (regret, selection accuracy).

Several oracle runs with the same config + task file merge into one table (latest row per
task x tool), so e.g. an agent-only run can be added later.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .config import ROOT, RUNS_DIR
from .quota import Quota, QuotaPaused, llm_resource
from .runlog import ResumeConflict, RunDir, experiment_hash

TOOL_ORDER = ["plain_fetch", "firecrawl_scrape", "tinyfish_fetch", "tinyfish_agent"]
TOOL_ABBR = {"plain_fetch": "plain", "firecrawl_scrape": "firecrawl", "tinyfish_fetch": "tf_fetch",
             "tinyfish_agent": "tf_agent"}
CATS = ["static", "js_heavy", "pdf", "interactive"]
EXIT_PAUSED = 3


def score_or_pending(task: dict, answer: str | None, cfg: dict, quota: Quota) -> dict[str, Any]:
    """Score; if only the judge's quota is out, mark judge_pending instead of stopping the sweep."""
    from .scoring import score
    try:
        return score(task, answer, cfg, quota)
    except QuotaPaused as e:
        if task["check_type"] != "judge":
            raise
        return {"correct": None, "method": "judge_pending", "reason": str(e)[:200]}


def current_hash(tasks_path: Path) -> str:
    return experiment_hash() + "-" + hashlib.sha256(Path(tasks_path).read_bytes()).hexdigest()[:12]


def merged_rows(cfg_hash: str, runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Latest row per (task, tool) across every oracle run with this exact config + task file."""
    last: dict[tuple, dict] = {}
    for p in sorted(runs_dir.glob("*_oracle")):
        r = RunDir(p)
        if r.meta.get("experiment_hash") != cfg_hash:
            continue
        for row in r.records("results"):
            last[(row["task_id"], row["tool"])] = row
    return list(last.values())


def infra_failed(row: dict) -> bool:
    return bool(row.get("error")) and row.get("final_answer") is None


def cells_for(tasks: list[dict], tools: list, cfg: dict) -> list[tuple[dict, Any]]:
    agent_cats = cfg["tools"]["tinyfish_agent"].get("oracle_categories", [])
    return [(t, tool) for t in tasks for tool in tools
            if tool.name != "tinyfish_agent" or t["category"] in agent_cats]


def run_oracle(cfg: dict, model: str, tasks: list[dict], tools: list, tasks_path: Path,
               resume: bool = True) -> int:
    from .agent import run_task, tool_spec
    from .scoring import route_taken

    cells = cells_for(tasks, tools, cfg)
    desc = cfg["tool_descriptions"]["neutral"]
    max_calls = cfg["agent"]["max_tool_calls"]
    quota = Quota(cfg, ROOT / cfg["quota"]["ledger"])
    plan: dict[str, float] = {}
    for t, tool in cells:  # 1 forced call per cell (the minimum); the agent may make up to max_calls
        plan[tool.name] = plan.get(tool.name, 0) + quota.estimate_tool_units(tool.name, t["category"])
    plan[llm_resource(cfg, model)] = len(cells) * (max_calls + 1)
    jr = llm_resource(cfg, cfg["judge"]["model"])
    plan[jr] = plan.get(jr, 0) + sum(t["check_type"] == "judge" for t, _ in cells)
    quota.preflight(plan, f"oracle {model}: {len(cells)} task x tool cells")
    try:
        run = RunDir.start(model, "oracle", resume=resume, tasks_path=tasks_path,
                           extra={"model_id": cfg["models"][model]["id"], "tools": [t.name for t in tools],
                                  "n_cells": len(cells)})
    except ResumeConflict as e:
        print(f"[run] {e}")
        return 1
    last = {(r["task_id"], r["tool"]): r for r in run.records("results")}
    try:
        for t, tool in cells:
            prev = last.get((t["id"], tool.name))
            if prev and not infra_failed(prev):
                if prev["score"].get("method") == "judge_pending":  # re-judge only; no tool calls
                    sc = score_or_pending(t, prev["final_answer"], cfg, quota)
                    if sc["method"] != "judge_pending":
                        run.log("results", {**prev, "score": sc})
                        print(f"{'PASS' if sc['correct'] else 'fail'} {t['id']} {tool.name} [re-judged]")
                continue
            spec = tool_spec(tool.name, desc[tool.name], tool.parameters)
            rec = run_task(t, model, cfg, quota, {tool.name: tool}, [spec], first_tool_choice="required")
            sc = score_or_pending(t, rec["final_answer"], cfg, quota)
            row = {"task_id": t["id"], "question": t["question"], "category": t["category"], "tool": tool.name,
                   "rep": 0, "model": model, "mode": "oracle", **rec, "score": sc, "route": route_taken(rec, t)}
            run.log("results", row)
            mark = {True: "PASS", False: "fail", None: "pend"}[sc["correct"]]
            flags = [f"blocked:{c['blocked']}" for c in rec["tool_calls"] if c.get("blocked")]
            flags += [f"err:{(c['error'] or '')[:40]}" for c in rec["tool_calls"] if c.get("error")]
            ans = (rec["final_answer"] or rec["error"] or "").replace("\n", " ")[:70]
            print(f"{mark} {t['id']} {tool.name:<17} calls={rec['n_tool_calls']} {' '.join(flags)} | {ans}",
                  flush=True)
    except QuotaPaused as e:
        run.set_status("paused", reason=str(e))
        print(f"[quota] PAUSED: {e}\n[quota] rerun the same command to resume.")
        return EXIT_PAUSED
    rows = {(r["task_id"], r["tool"]): r for r in run.records("results")}
    pending = [k for k, r in rows.items() if r["score"].get("method") == "judge_pending"]
    if pending:
        run.set_status("paused", reason=f"{len(pending)} judge verdicts pending (judge quota)")
        print(f"[oracle] {len(pending)} judge verdicts pending; rerun after the judge quota resets.")
    else:
        run.set_status("complete", list_usd=round(quota.session_list_usd, 6))
    from .cli import load_tasks
    table = oracle_table(merged_rows(current_hash(tasks_path)), load_tasks(tasks_path))
    (ROOT / "runs" / "oracle_table.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(f"\n[quota] this session: list-price ${quota.session_list_usd:.4f}, actual $0\nlogs: {run.path}")
    return EXIT_PAUSED if pending else 0


def cell(row: dict | None) -> str:
    if row is None:
        return "-"
    if infra_failed(row):
        return "ERR"
    c = row["score"]["correct"]
    s = {True: "PASS", False: "fail", None: "pend"}[c]
    calls = row.get("tool_calls") or []
    if any(x.get("blocked") for x in calls):
        s += " B"
    if any(x.get("error") for x in calls):
        s += " E"
    if c is False and row["score"].get("format_suspect"):
        s += " F?"
    if c is False and any(x.get("answer_in_content") for x in calls):
        s += " R"
    return s


def oracle_table(rows: list[dict], tasks: list[dict]) -> str:
    """Markdown task x tool table, per-tool pass rates by category, and quota used."""
    by = {(r["task_id"], r["tool"]): r for r in rows}
    tools = [t for t in TOOL_ORDER if any(k[1] == t for k in by)]
    out = ["| task | cat | " + " | ".join(TOOL_ABBR[t] for t in tools) + " | flags |",
           "|---|---|" + "---|" * len(tools) + "---|"]
    for t in tasks:
        fl = [name for name, on in (("memorized", t.get("memorized")), ("known_answer", t.get("known_answer")),
                                    (t.get("analysis_flag") or "", t.get("analysis_flag"))) if on]
        if t["category"] == "interactive":
            fl.append("url_nav" if t.get("url_navigable") else "not_url_nav")
        cells = " | ".join(cell(by.get((t["id"], tl))) for tl in tools)
        out.append(f"| {t['id']} | {t['category']} | {cells} | {', '.join(fl)} |")
    out.append("\nB = bot/challenge page seen, E = a tool call errored, F? = format_suspect (hand-review), "
               "R = answer was in the raw tool output but the final answer missed it (noisy for small numbers).\n")
    out.append("| tool | " + " | ".join(CATS) + " | total |")
    out.append("|---|" + "---|" * (len(CATS) + 1))
    for tl in tools:
        parts, k_all, n_all = [], 0, 0
        for c in CATS:
            rs = [r for (_, x), r in by.items() if x == tl and r["category"] == c]
            k = sum(r["score"]["correct"] is True for r in rs)
            k_all, n_all = k_all + k, n_all + len(rs)
            parts.append(f"{k}/{len(rs)}" if rs else "-")
        out.append(f"| {TOOL_ABBR[tl]} | " + " | ".join(parts) + f" | {k_all}/{n_all} |")
    out.append("\n| tool | tool calls | errors | blocked | firecrawl credits | agent steps | agent USD | median latency s |")
    out.append("|---|---|---|---|---|---|---|---|")
    for tl in tools:
        calls = [c for (_, x), r in by.items() if x == tl for c in (r.get("tool_calls") or [])]
        lat = sorted(c.get("latency_ms") or 0 for c in calls)
        med = lat[len(lat) // 2] / 1000 if lat else 0.0
        cred = sum((c.get("meta") or {}).get("credits", 0) or 0 for c in calls)
        steps = sum((c.get("meta") or {}).get("steps", 0) or 0 for c in calls)
        usd = sum(c.get("cost_usd") or 0 for c in calls)
        is_fc, is_ag = tl == "firecrawl_scrape", tl == "tinyfish_agent"
        out.append(f"| {TOOL_ABBR[tl]} | {len(calls)} | {sum(bool(c.get('error')) for c in calls)} | "
                   f"{sum(bool(c.get('blocked')) for c in calls)} | {cred if is_fc else '-'} | "
                   f"{steps if is_ag else '-'} | {('$%.3f' % usd) if is_ag else '-'} | {med:.1f} |")
    out.append(f"\nLLM requests (agent, excl. judge): {sum(r.get('llm_requests', 0) for r in rows)}; tokens in/out: "
               f"{sum(r.get('tokens_in', 0) for r in rows)}/{sum(r.get('tokens_out', 0) for r in rows)}")
    return "\n".join(out)
