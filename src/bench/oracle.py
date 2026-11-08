"""Oracle sweep: for each task x tool the agent gets ONLY that tool (neutral description) and
must call it on its first turn (tool_choice="required"). It may make up to max_tool_calls calls,
then answers. One rep.

Two separate verdicts per cell:
- can  (the ORACLE): is the answer present in the tool's raw output (any call in the cell)?
       This is "the tool can solve this task", used later for selection accuracy and regret.
       Computed offline from the saved raw outputs (runs/<run>/raw/), so evidence patterns can
       be refined without re-running anything.
- ext  (model extraction): did Flash-Lite's final answer pass the task check?
A Flash-Lite extraction miss therefore never counts against the tool.

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


def merged_for(tasks: list[dict], runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Oracle rows for the current config and the current question of each task. Matching on the
    question (not the task-file hash) means refining `evidence` patterns or flags afterwards
    never orphans results, while a rewritten question still does."""
    q = {t["id"]: t["question"] for t in tasks}
    return [r for r in merged_rows(experiment_hash(), runs_dir) if q.get(r["task_id"]) == r.get("question")]


def raw_sink(run_path: Path):
    """Content-addressed store for full tool outputs: returns 'raw/<sha16>.txt'."""
    d = run_path / "raw"

    def sink(content: str) -> str:
        d.mkdir(exist_ok=True)
        name = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:16] + ".txt"
        f = d / name
        if not f.exists():
            f.write_text(content, encoding="utf-8", errors="replace")
        return f"raw/{name}"
    return sink


def merged_rows(cfg_hash: str, runs_dir: Path = RUNS_DIR) -> list[dict]:
    """Latest row per (task, tool) across every non-invalid oracle run with this config hash
    (the part before the task-file hash). Each row gets `_run_path` to load its raw outputs."""
    last: dict[tuple, dict] = {}
    for p in sorted(runs_dir.glob("*_oracle")):
        r = RunDir(p)
        if r.meta.get("experiment_hash", "").split("-")[0] != cfg_hash or r.meta.get("status") == "invalid":
            continue
        for row in r.records("results"):
            last[(row["task_id"], row["tool"])] = {**row, "_run_path": str(p)}
    return list(last.values())


def infra_failed(row: dict) -> bool:
    return bool(row.get("error")) and row.get("final_answer") is None


def cells_for(tasks: list[dict], tools: list, cfg: dict) -> list[tuple[dict, Any]]:
    agent_cats = cfg["tools"]["tinyfish_agent"].get("oracle_categories", [])
    return [(t, tool) for t in tasks for tool in tools
            if tool.name != "tinyfish_agent" or t["category"] in agent_cats]


HAND_LABELS = ROOT / "tasks" / "oracle_hand_labels.json"


def hand_label(row: dict, labels: dict | None = None) -> dict | None:
    """A hand label for this cell, valid only if it was made from exactly these raw outputs."""
    if labels is None:
        import json
        labels = json.loads(HAND_LABELS.read_text(encoding="utf-8")) if HAND_LABELS.exists() else {}
    lab = labels.get(row["task_id"], {}).get(row["tool"])
    refs = sorted(c["raw_ref"] for c in row.get("tool_calls") or [] if c.get("raw_ref"))
    if lab and lab.get("hand_labeled") and sorted(lab.get("raw_refs", [])) == refs:
        return lab
    return None


def can_solve(row: dict, task: dict, labels: dict | None = None) -> bool | None:
    """The oracle verdict: answer present in ANY raw output of this cell. None = undecidable
    (aggregate tasks without a valid hand label)."""
    from .scoring import evidence_present
    if (task.get("evidence") or {}).get("aggregate") and row["tool"] != "tinyfish_agent":
        lab = hand_label(row, labels)
        return lab["can"] if lab else None
    verdicts = []
    for c in row.get("tool_calls") or []:
        ref = c.get("raw_ref")
        if not ref or not c.get("ok"):
            continue
        content = (Path(row["_run_path"]) / ref).read_text(encoding="utf-8", errors="replace")
        verdicts.append(evidence_present(task, content, c.get("tool"), str((c.get("args") or {}).get("url", ""))))
    if any(v is True for v in verdicts):
        return True
    if verdicts and all(v is None for v in verdicts):
        return None
    return False


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
    sink = raw_sink(run.path)
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
            rec = run_task(t, model, cfg, quota, {tool.name: tool}, [spec], first_tool_choice="required",
                           raw_sink=sink)
            sc = score_or_pending(t, rec["final_answer"], cfg, quota)
            row = {"task_id": t["id"], "question": t["question"], "category": t["category"], "tool": tool.name,
                   "rep": 0, "model": model, "mode": "oracle", **rec, "score": sc, "route": route_taken(rec, t)}
            run.log("results", row)
            can = can_solve({**row, "_run_path": str(run.path)}, t)
            ext = {True: "PASS", False: "fail", None: "pend"}[sc["correct"]]
            flags = [f"blocked:{c['blocked']}" for c in rec["tool_calls"] if c.get("blocked")]
            flags += [f"err:{(c['error'] or '')[:40]}" for c in rec["tool_calls"] if c.get("error")]
            ans = (rec["final_answer"] or rec["error"] or "").replace("\n", " ")[:60]
            print(f"can={_yn(can)} ext={ext} {t['id']} {tool.name:<17} calls={rec['n_tool_calls']} "
                  f"{' '.join(flags)} | {ans}", flush=True)
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
    all_tasks = load_tasks(tasks_path)
    table = oracle_table(merged_for(all_tasks), all_tasks)
    (ROOT / "runs" / "oracle_table.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(f"\n[quota] this session: list-price ${quota.session_list_usd:.4f}, actual $0\nlogs: {run.path}")
    return EXIT_PAUSED if pending else 0


def _yn(v: bool | None) -> str:
    return {True: "Y", False: "n", None: "?"}[v]


def cell(row: dict | None, task: dict) -> str:
    """'<can> <ext> [flags]', e.g. 'Y PASS', 'Y fail' (extraction miss), 'n PASS' (answered
    without the evidence: memory or guess)."""
    if row is None:
        return "-"
    if infra_failed(row):
        return "ERR"
    ext = {True: "PASS", False: "fail", None: "pend"}[row["score"]["correct"]]
    s = f"{_yn(can_solve(row, task))} {ext}"
    if hand_label(row):
        s += " H"
    calls = row.get("tool_calls") or []
    if any(x.get("blocked") for x in calls):
        s += " B"
    if any(x.get("error") for x in calls):
        s += " E"
    if any(x.get("truncated") for x in calls):
        s += " T"
    if row["score"]["correct"] is False and row["score"].get("format_suspect"):
        s += " F?"
    return s


def oracle_table(rows: list[dict], tasks: list[dict]) -> str:
    """Markdown task x tool table (can + ext per cell), per-tool rates by category, quota used."""
    by = {(r["task_id"], r["tool"]): r for r in rows}
    tid = {t["id"]: t for t in tasks}
    tools = [t for t in TOOL_ORDER if any(k[1] == t for k in by)]
    out = ["Each cell: **can** (oracle: answer present in the raw tool output; Y / n / ? = presence "
           "can't decide) then **ext** (Flash-Lite's final answer PASS / fail).\n",
           "| task | cat | " + " | ".join(TOOL_ABBR[t] for t in tools) + " | flags |",
           "|---|---|" + "---|" * len(tools) + "---|"]
    for t in tasks:
        fl = [name for name, on in (("memorized", t.get("memorized")), ("known_answer", t.get("known_answer")),
                                    (t.get("analysis_flag") or "", t.get("analysis_flag"))) if on]
        if (t.get("evidence") or {}).get("aggregate"):
            fl.append("aggregate")
        if t["category"] == "interactive":
            fl.append("url_nav" if t.get("url_navigable") else "not_url_nav")
        cells = " | ".join(cell(by.get((t["id"], tl)), t) for tl in tools)
        out.append(f"| {t['id']} | {t['category']} | {cells} | {', '.join(fl)} |")
    out.append("\nFlags: H = `can` hand-labeled from the saved raw outputs (tasks/oracle_hand_labels.json), "
               "B = bot/challenge page seen, E = a tool call errored, T = output truncated to "
               "max_content_chars before the model saw it, F? = format_suspect (hand-review).\n")

    def rate(tl, cat, fn):
        rs = [r for (task_id, x), r in by.items() if x == tl and (cat is None or r["category"] == cat)]
        vals = [fn(r) for r in rs]
        known = [v for v in vals if v is not None]
        unk = len(vals) - len(known)
        return f"{sum(known)}/{len(known)}" + (f" (+{unk}?)" if unk else "") if rs else "-"

    can_f = lambda r: can_solve(r, tid[r["task_id"]])  # noqa: E731
    ext_f = lambda r: r["score"]["correct"]  # noqa: E731
    for title, fn in (("can solve (oracle)", can_f), ("Flash-Lite extraction pass", ext_f)):
        out.append(f"**{title}**\n")
        out.append("| tool | " + " | ".join(CATS) + " | total |")
        out.append("|---|" + "---|" * (len(CATS) + 1))
        for tl in tools:
            out.append(f"| {TOOL_ABBR[tl]} | " + " | ".join(rate(tl, c, fn) for c in CATS)
                       + f" | {rate(tl, None, fn)} |")
        out.append("")
    out.append("**Extraction misses** (tool delivered the answer, Flash-Lite still failed): "
               + (", ".join(f"{r['task_id']}/{TOOL_ABBR[r['tool']]}" for r in rows
                            if can_f(r) is True and r["score"]["correct"] is False) or "none"))
    out.append("**Answered without evidence** (can=n but ext=PASS: memory or guess): "
               + (", ".join(f"{r['task_id']}/{TOOL_ABBR[r['tool']]}" for r in rows
                            if can_f(r) is False and r["score"]["correct"] is True) or "none") + "\n")

    out.append("| tool | tool calls | errors | blocked | firecrawl credits | agent steps | agent USD | median latency s |")
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
