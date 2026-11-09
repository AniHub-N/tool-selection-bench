"""Entry point: `python -m bench <command>`.

Implemented: `smoke`. `oracle`, `run`, `ablate`, `report`, `label` land in later phases.
Exit codes: 0 done, 1 error, 3 paused on a free quota (rerun the same command to resume).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import ROOT, TASKS_DIR, load_config
from .quota import Quota, QuotaPaused, llm_resource
from .runlog import ResumeConflict, RunDir
from .oracle import raw_sink
from .tools import load_tools

EXIT_PAUSED = 3


def load_tasks(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def cmd_smoke(args: argparse.Namespace) -> int:
    """Run every available tool directly on the smoke URLs (no agent), plus one tiny
    request per Gemini model to confirm the key and model IDs work."""
    cfg = load_config()
    tasks = load_tasks(Path(args.tasks))
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    tools, skipped = load_tools(cfg)
    for s in skipped:
        print(f"[skip] {s}")
    from .llm import api_key
    models = [] if args.no_llm else list(cfg["models"])
    for m in list(models):
        if not api_key(cfg["models"][m]):
            print(f"[skip] {m}: no API key set ({cfg['models'][m].get('api_key_env', 'GEMINI_API_KEY')})")
            models.remove(m)

    quota = Quota(cfg, ROOT / cfg["quota"]["ledger"])
    plan = {t.name: quota.estimate_tool_units(t.name, None) * len(tasks) for t in tools}
    for m in models:
        plan[llm_resource(cfg, m)] = plan.get(llm_resource(cfg, m), 0) + 1
    quota.preflight(plan, "smoke")
    try:
        run = RunDir.start("none", "smoke", extra={"tools": [t.name for t in tools], "skipped": skipped},
                           resume=not args.new)
    except ResumeConflict as e:
        print(f"[run] {e}")
        return 1
    done = run.done("calls", lambda r: (r["task_id"], r["tool"]))
    try:
        for task in tasks:
            for tool in tools:
                if (task["id"], tool.name) in done:
                    continue
                res = quota.run_tool(tool, {"url": task["url"], "goal": task["question"]}, task["category"])
                found = task["expected_answer"].lower() in res.content.lower()
                run.log("calls", {"task_id": task["id"], "tool": tool.name, **res.to_dict(),
                                  "content_len": len(res.content), "expected_in_content": found})
                print(f"{'ok ' if res.ok else 'ERR'} {task['id']:<13} {tool.name:<17} {res.latency_ms:>6}ms "
                      f"list=${res.cost_usd:.4f} len={len(res.content):>6} "
                      f"answer_in_content={found} {res.error or ''}")
        llm_done = run.done("llm_calls", lambda r: (r["model"],))
        if models:
            from .llm import complete  # imported lazily: litellm is slow to import
            for m in models:
                if (m,) in llm_done:
                    continue
                try:
                    out = complete(m, cfg, quota, [{"role": "user", "content": "Reply with the single word: OK"}])
                    text, err = (out["message"].content or "").strip(), None
                except QuotaPaused:
                    raise
                except Exception as e:
                    out, text, err = {"usage": {}, "list_usd": 0.0}, "", f"{type(e).__name__}: {str(e)[:300]}"
                run.log("llm_calls", {"model": m, "id": cfg["models"][m]["id"], "reply": text,
                                      "usage": out["usage"], "list_usd": out["list_usd"], "error": err})
                print(f"{'ok ' if not err else 'ERR'} llm {cfg['models'][m]['id']:<32} reply={text!r} {err or ''}")
    except QuotaPaused as e:
        run.set_status("paused", reason=str(e))
        print(f"[quota] PAUSED: {e}\n[quota] rerun the same command to resume.")
        return EXIT_PAUSED
    run.set_status("complete", list_usd=round(quota.session_list_usd, 6))
    print(f"\n[quota] this session: list-price ${quota.session_list_usd:.4f}, actual $0")
    print(f"logs: {run.path}")
    return 0


def _find_answer(task: dict, content: str) -> int:
    """Index of the expected answer in content, or -1. Judge tasks: all comma-separated
    parts must appear (index of the first); single-token judge answers are skipped (-2)."""
    import re
    if task["check_type"] == "regex":
        m = re.search(task["pattern"], content, re.I)
        return m.start() if m else -1
    if task["check_type"] == "judge":
        parts = [p.strip().lower() for p in task["expected_answer"].split(",")]
        if len(parts) < 2 and len(parts[0]) < 6:
            return -2
        idx = [content.lower().find(p) for p in parts]
        return -1 if min(idx) < 0 else min(idx)
    return content.lower().find(task["expected_answer"].lower())


def cmd_tasks_check(args: argparse.Namespace) -> int:
    """Free sanity check (no API quota): fetch every task URL with plain_fetch and report
    whether the expected answer is visible, and where relative to the agent's truncation."""
    cfg = load_config()
    tasks = load_tasks(Path(args.tasks))
    tools, _ = load_tools(cfg, ["plain_fetch"])
    plain = tools[0]
    cut = cfg["agent"]["max_content_chars"]
    rows = []
    for t in tasks:
        r = plain.run({"url": t["url"]})
        i = _find_answer(t, r.content)
        seen = "n/a" if i == -2 else ("yes" if i >= 0 else "no")
        flag = "  <- past truncation" if i >= cut else ""
        rows.append({"id": t["id"], "category": t["category"], "plain_sees_answer": seen,
                     "position": i if i >= 0 else None, "content_len": len(r.content), "error": r.error})
        print(f"{t['id']}  {t['category']:<12} plain_sees_answer={seen:<4} pos={i if i >= 0 else '-':>6} "
              f"len={len(r.content):>6} {r.error or ''}{flag}")
    out = Path(args.tasks).with_name("plain_visibility.json")
    out.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {out}")
    return 0


MODES_READY = {"no_tools", "free", "anon"}

# Ablation "anon": same tools, same order, but generic names, and descriptions with the brand
# names removed. Capability facts are kept word for word (they're not branding). Kept in code, not
# config, so adding it after the oracle didn't change the experiment hash the oracle merges on.
ANON_NAMES = {"plain_fetch": "tool_1", "firecrawl_scrape": "tool_2", "tinyfish_fetch": "tool_3",
              "tinyfish_agent": "tool_4"}
ANON_DESC = {
    "plain_fetch": "Fetch a URL with a plain HTTP GET and return the main text as Markdown. Does not run JavaScript. PDFs are converted to text.",
    "firecrawl_scrape": "Fetch a URL and return the page as Markdown. Runs JavaScript before extracting. PDFs are converted to text.",
    "tinyfish_fetch": "Fetch a URL and return the page as Markdown. Runs JavaScript before extracting.",
    "tinyfish_agent": "Give a browser agent a start URL and a goal in plain language. It can click, scroll, paginate and fill forms (up to 5 steps), then returns what it found.",
}


def latest_results(run: RunDir) -> list[dict]:
    """Last record per (task_id, rep): a retried record supersedes the failed attempt."""
    last: dict[tuple, dict] = {}
    for r in run.records("results"):
        last[(r["task_id"], r["rep"])] = r
    return list(last.values())


def cmd_run(args: argparse.Namespace) -> int:
    """Agent run. --mode no_tools (zero tools; memorization) or free (every enabled tool, in
    config order, with the neutral descriptions; the model picks). Same system prompt as the
    oracle sweep. Judge verdicts that hit the judge quota are stored as pending and re-judged
    (no tool calls) when the same command is rerun."""
    from collections import defaultdict

    from .agent import run_task, tool_spec
    from .oracle import score_or_pending
    from .scoring import outcome_of, route_taken

    if args.mode not in MODES_READY:
        print(f"mode {args.mode!r} lands later in phase 3. Ready: {sorted(MODES_READY)}")
        return 1
    cfg = load_config()
    if args.model not in cfg["models"]:
        print(f"unknown model {args.model!r}; choose from {list(cfg['models'])}")
        return 1
    from .llm import api_key
    if not api_key(cfg["models"][args.model]):
        print(f"{args.model}: no API key set ({cfg['models'][args.model].get('api_key_env', 'GEMINI_API_KEY')})")
        return 1
    tasks = load_tasks(Path(args.tasks))
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    reps = args.reps or (1 if args.mode == "no_tools" else 3)
    tools, specs = {}, None  # no_tools
    if args.mode in ("free", "anon"):
        loaded, skipped = load_tools(cfg)
        if skipped:  # free choice must offer the full tool set, or it isn't the experiment
            print("[run] free choice needs every enabled tool:\n  " + "\n  ".join(skipped))
            return 1
        desc = cfg["tool_descriptions"]["neutral"] if args.mode == "free" else ANON_DESC
        shown = (lambda n: n) if args.mode == "free" else ANON_NAMES.get
        tools = {shown(t.name): t for t in loaded}
        specs = [tool_spec(shown(t.name), desc[t.name], t.parameters) for t in loaded]

    quota = Quota(cfg, ROOT / cfg["quota"]["ledger"])
    plan: dict[str, float] = defaultdict(float)
    max_req = 1 if args.mode == "no_tools" else cfg["agent"]["max_tool_calls"] + 1
    plan[llm_resource(cfg, args.model)] += len(tasks) * reps * max_req
    plan[llm_resource(cfg, cfg['judge']['model'])] += sum(t["check_type"] == "judge" for t in tasks) * reps
    quota.preflight(dict(plan), f"{args.model} {args.mode} x{reps}")
    try:
        run = RunDir.start(args.model, args.mode, resume=not args.new, tasks_path=Path(args.tasks),
                           extra={"model_id": cfg["models"][args.model]["id"], "reps": reps,
                                  "n_tasks": len(tasks), "tools": sorted(tools),
                                  **({"anon_names": ANON_NAMES, "anon_desc": ANON_DESC} if args.mode == "anon" else {})})
    except ResumeConflict as e:
        print(f"[run] {e}")
        return 1
    # Records that died on an infrastructure error (no answer) are retried on resume;
    # judge_pending records are re-judged without calling any tool.
    done = {(r["task_id"], r["rep"]): r for r in latest_results(run)
            if not (r.get("error") and r.get("final_answer") is None)}
    try:
        for rep in range(reps):
            for t in tasks:
                prev = done.get((t["id"], rep))
                if prev:
                    if prev["score"].get("method") == "judge_pending":
                        sc = score_or_pending(t, prev["final_answer"], cfg, quota)
                        if sc["method"] != "judge_pending":
                            run.log("results", {**prev, "score": sc})
                            print(f"{outcome_of(sc)} {t['id']} r{rep} [re-judged]")
                    continue
                rec = run_task(t, args.model, cfg, quota, tools, specs, raw_sink=raw_sink(run.path))
                sc = score_or_pending(t, rec["final_answer"], cfg, quota)
                row = {"task_id": t["id"], "question": t["question"], "category": t["category"], "rep": rep, "model": args.model,
                       "mode": args.mode, **rec, "score": sc, "route": route_taken(rec, t)}
                run.log("results", row)
                ans = (rec["final_answer"] or rec["error"] or "").replace("\n", " ")[:80]
                picks = ",".join(TOOL_SHORT.get(c["tool"], c["tool"]) for c in rec["tool_calls"]) or "-"
                print(f"{outcome_of(sc):<9} {t['id']} r{rep} [{picks}] {ans}", flush=True)
    except QuotaPaused as e:
        run.set_status("paused", reason=str(e))
        print(f"[quota] PAUSED: {e}\n[quota] rerun the same command to resume.")
        return EXIT_PAUSED
    pending = [r for r in latest_results(run) if r["score"].get("method") == "judge_pending"]
    if pending:
        run.set_status("paused", reason=f"{len(pending)} judge verdicts pending (judge quota)")
        print(f"[run] {len(pending)} judge verdicts pending; rerun the same command after the judge quota resets.")
    else:
        run.set_status("complete", list_usd=round(quota.session_list_usd, 6))
    _print_summary(latest_results(run))
    print(f"\n[quota] this session: list-price ${quota.session_list_usd:.4f}, actual $0\nlogs: {run.path}")
    return 0


TOOL_SHORT = {"plain_fetch": "plain", "firecrawl_scrape": "fc", "tinyfish_fetch": "tff", "tinyfish_agent": "agent"}


def _print_summary(rows: list[dict]) -> None:
    from collections import Counter, defaultdict

    from .scoring import outcome_of
    by: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by[r["category"]][outcome_of(r["score"], r.get("final_answer"))] += 1
    print("\ncategory      correct  wrong  abstained  pending")
    tot: Counter = Counter()
    for c, k in by.items():
        tot.update(k)
        print(f"{c:<13} {k['correct']:>3}/{sum(k.values()):<4} {k['wrong']:>5} {k['abstained']:>10} {k['pending']:>8}")
    print(f"{'total':<13} {tot['correct']:>3}/{sum(tot.values()):<4} {tot['wrong']:>5} {tot['abstained']:>10} {tot['pending']:>8}")
    empty = sum(not (r.get("final_answer") or "").strip() for r in rows)
    if empty:
        print(f"WARNING: {empty}/{len(rows)} empty final answers; check the raw logs before trusting these numbers.")


def cmd_memorized(args: argparse.Namespace) -> int:
    """Flag tasks a main-pair model answers correctly with zero tools.

    Merges every complete no_tools run per model (newest first). A result only counts for a
    task if it was for the task's *current* question: records carry the question text; older
    records without it count only for tasks that were never rewritten."""
    import runpy
    from collections import defaultdict

    from .config import RUNS_DIR
    from .scoring import check
    cfg = load_config()
    tasks = load_tasks(TASKS_DIR / "tasks.jsonl")
    by_id = {t["id"]: t for t in tasks}
    models = cfg.get("experiment", {}).get("main_models", list(cfg["models"]))
    verdict: dict[str, dict[str, bool]] = defaultdict(dict)   # task -> model -> correct
    used = []
    for m in models:
        runs = [RunDir(p) for p in sorted(RUNS_DIR.glob(f"*_{m}_no_tools"), reverse=True)]
        runs = [r for r in runs if r.meta.get("status") == "complete"]
        for r in runs:
            took = 0
            for rec in latest_results(r):
                t = by_id.get(rec["task_id"])
                if t is None or m in verdict[t["id"]]:
                    continue
                same_q = rec.get("question") == t["question"] if "question" in rec else "rewritten_from" not in t
                if same_q:
                    # Re-check deterministic tasks with the current (format-tolerant) scorer; free.
                    det = check(t, rec.get("final_answer"))
                    verdict[t["id"]][m] = bool(rec["score"]["correct"] if det is None else det)
                    took += 1
            if took:
                used.append(f"{r.id} ({took})")
    missing = [(t["id"], m) for t in tasks for m in models if m not in verdict[t["id"]]]
    if missing:
        print(f"[memorized] {len(missing)} task/model pairs have no no_tools result for the current question, e.g. "
              f"{missing[:6]}. Run: python -m bench run --model <m> --mode no_tools --task <id> ...")
    out = {}
    for t in tasks:
        ms = [m for m, ok in verdict[t["id"]].items() if ok]
        out[t["id"]] = {"memorized": bool(ms), "models": ms, "checked": sorted(verdict[t["id"]])}
    (TASKS_DIR / "memorized.json").write_text(json.dumps({**out, "_runs": used, "_models": models}, indent=2) + "\n")
    runpy.run_path(str(TASKS_DIR / "build_tasks.py"), run_name="__main__")
    per_cat = defaultdict(list)
    for t in tasks:
        if out[t["id"]]["memorized"] and not t.get("known_answer"):
            per_cat[t["category"]].append(f"{t['id']}({','.join(out[t['id']]['models'])})")
    print(f"[memorized] models={models} from runs: {used}")
    print("  memorized, excluding the deliberate known_answer subset:")
    for c in ["static", "js_heavy", "pdf", "interactive"]:
        flag = "  <- more than 1" if len(per_cat[c]) > 1 else ""
        print(f"  {c:<12} {len(per_cat[c])}: {' '.join(per_cat[c]) or '-'}{flag}")
    return 1 if missing else 0


def cmd_oracle(args: argparse.Namespace) -> int:
    """Oracle sweep (see oracle.py): the agent gets one tool at a time and must use it."""
    from .llm import api_key
    from .oracle import run_oracle
    cfg = load_config()
    model = args.model or cfg["experiment"]["main_models"][0]
    if not api_key(cfg["models"][model]):
        print(f"{model}: no API key set")
        return 1
    tasks_path = Path(args.tasks)
    tasks = load_tasks(tasks_path)
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    tools, skipped = load_tools(cfg, args.tool)
    for s in skipped:
        print(f"[skip] {s}")
    agent_ids: set[str] = set()
    if args.agent_gaps:
        from .oracle import agent_gap_ids, merged_for
        agent_ids = set(agent_gap_ids(load_tasks(tasks_path), merged_for(load_tasks(tasks_path))))
        tasks = [t for t in tasks if t["id"] in agent_ids]
        tools = [t for t in tools if t.name == "tinyfish_agent"]
        print(f"[oracle] agent gap tasks (no fetch tool can solve): {sorted(agent_ids) or 'none'}")
    return run_oracle(cfg, model, tasks, tools, tasks_path, resume=not args.new, agent_ids=agent_ids)


def cmd_report(args: argparse.Namespace) -> int:
    from .report import build
    out = build(load_tasks(Path(args.tasks)))
    for label, kinds in out["picks"].items():
        print(label, dict(kinds["all"]))
    print("wrote charts/can_solve_by_category.png, charts/pick_vs_oracle_best.png")
    return 0


def cmd_free_summary(args: argparse.Namespace) -> int:
    from .freechoice import summarize
    run = Path(args.run) if args.run else max((ROOT / "runs").glob("*_free"), default=None)
    if run is None:
        print("no free-choice run found")
        return 1
    text = summarize(run, load_tasks(Path(args.tasks)))
    (run / "summary.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def cmd_oracle_table(args: argparse.Namespace) -> int:
    """Print and save the merged oracle table for the current config + task file. No API calls."""
    from .oracle import merged_for, oracle_table
    tasks_path = Path(args.tasks)
    rows = merged_for(load_tasks(tasks_path))
    if not rows:
        print("no oracle results for the current config + task file")
        return 1
    table = oracle_table(rows, load_tasks(tasks_path))
    (ROOT / "runs" / "oracle_table.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


def cmd_tool_check(args: argparse.Namespace) -> int:
    """Exactly ONE request: offer a single fetch tool and check the model emits a well-formed
    tool call (right name, a url argument). Confirms tool calling before any real run."""
    from .agent import tool_spec, user_message
    from .llm import api_key, complete
    from .tools.base import Tool
    cfg = load_config()
    m = args.model
    if m not in cfg["models"] or not api_key(cfg["models"][m]):
        print(f"{m}: unknown model or no API key")
        return 1
    quota = Quota(cfg, ROOT / cfg["quota"]["ledger"])
    quota.preflight({llm_resource(cfg, m): 1}, f"tool-check {m}")
    task = {"question": "What is the title of this page?", "url": "https://quotes.toscrape.com/"}
    msgs = [{"role": "system", "content": cfg["agent"]["system_prompt"]},
            {"role": "user", "content": user_message(task)}]
    spec = tool_spec("fetch_page", "Fetch a web page and return its text.", Tool.parameters)
    try:
        out = complete(m, cfg, quota, msgs, tools=[spec], retries=0, timeout_s=180)  # exactly one attempt
    except QuotaPaused as e:
        print(f"[quota] PAUSED: {e}")
        return EXIT_PAUSED
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {str(e)[:300]}")
        return 1
    msg = out["message"]
    calls = getattr(msg, "tool_calls", None) or []
    ok = False
    for c in calls:
        try:
            a = json.loads(c.function.arguments or "{}")
        except json.JSONDecodeError:
            a = {}
        print(f"tool_call: name={c.function.name!r} args={a}")
        ok = ok or (c.function.name == "fetch_page" and str(a.get("url", "")).startswith("http"))
    if not calls:
        print(f"no tool call; content={(msg.content or '')[:300]!r}")
    print(f"usage={out['usage']} list=${out['list_usd']:.6f}")
    print("PASS: tool calling works" if ok else "FAIL: no well-formed tool call")
    run_dir = ROOT / "runs" / "tool_checks.jsonl"
    with open(run_dir, "a", encoding="utf-8") as f:
        f.write(json.dumps({"model": m, "id": cfg["models"][m]["id"], "pass": ok,
                            "calls": [{"name": c.function.name, "args": c.function.arguments} for c in calls],
                            "content": msg.content, "usage": out["usage"]}) + "\n")
    return 0 if ok else 1


def _not_yet(phase: int):
    def handler(args: argparse.Namespace) -> int:
        print(f"`{args.command}` lands in phase {phase}.")
        return 1
    return handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bench")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("smoke", help="run each tool directly on the smoke URLs, plus a Gemini ping")
    s.add_argument("--tasks", default=str(TASKS_DIR / "smoke.jsonl"))
    s.add_argument("--new", action="store_true", help="start a fresh run instead of resuming")
    s.add_argument("--task", action="append", help="only run this task id (repeatable)")
    s.add_argument("--no-llm", action="store_true", help="skip the Gemini check")
    s.set_defaults(func=cmd_smoke)

    tk = sub.add_parser("tool-check", help="ONE request: does the model emit a well-formed tool call?")
    tk.add_argument("--model", required=True)
    tk.set_defaults(func=cmd_tool_check)

    tc = sub.add_parser("tasks-check", help="free check: is each answer visible to plain_fetch?")
    tc.add_argument("--tasks", default=str(TASKS_DIR / "tasks.jsonl"))
    tc.set_defaults(func=cmd_tasks_check)

    o = sub.add_parser("oracle", help="force each tool on every task (agent gets one tool at a time)")
    o.add_argument("--model", help="default: first of experiment.main_models")
    o.add_argument("--tool", action="append", help="only this tool (repeatable); default: all enabled")
    o.add_argument("--tasks", default=str(TASKS_DIR / "tasks.jsonl"))
    o.add_argument("--task", action="append", help="only this task id (repeatable)")
    o.add_argument("--new", action="store_true", help="start a fresh run instead of resuming")
    o.add_argument("--agent-gaps", action="store_true",
                   help="run only the TinyFish agent, on tasks outside its oracle categories that no fetch tool can solve")
    o.set_defaults(func=cmd_oracle)
    ot = sub.add_parser("oracle-table", help="print the merged oracle table (no API calls)")
    ot.add_argument("--tasks", default=str(TASKS_DIR / "tasks.jsonl"))
    ot.set_defaults(func=cmd_oracle_table)
    r = sub.add_parser("run", help="agent run (modes: no_tools, free, anon)")
    r.add_argument("--model", required=True)
    r.add_argument("--mode", default="free")
    r.add_argument("--reps", type=int, help="repeats per task (default: 1 for no_tools, 3 otherwise)")
    r.add_argument("--tasks", default=str(TASKS_DIR / "tasks.jsonl"))
    r.add_argument("--task", action="append", help="only run this task id (repeatable)")
    r.add_argument("--new", action="store_true", help="start a fresh run instead of resuming")
    r.set_defaults(func=cmd_run)
    fs = sub.add_parser("free-summary", help="free-choice picks vs the oracle (no API calls)")
    fs.add_argument("--run", help="run dir (default: latest *_free run)")
    fs.add_argument("--tasks", default=str(TASKS_DIR / "tasks.jsonl"))
    fs.set_defaults(func=cmd_free_summary)
    sub.add_parser("memorized",help="flag tasks answered correctly in no_tools runs").set_defaults(func=cmd_memorized)
    sub.add_parser("ablate", help="ablation runs").set_defaults(func=_not_yet(4))
    sub.add_parser("label", help="hand-label outputs for judge validation").set_defaults(func=_not_yet(4))
    rp = sub.add_parser("report", help="build the README charts (no API calls)")
    rp.add_argument("--tasks", default=str(TASKS_DIR / "tasks.jsonl"))
    rp.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
