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
from .quota import Quota, QuotaPaused
from .runlog import ResumeConflict, RunDir
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
    tools, skipped = load_tools(cfg)
    for s in skipped:
        print(f"[skip] {s}")
    models = [] if args.no_llm else list(cfg["models"])
    if models and not os.environ.get("GEMINI_API_KEY"):
        print("[skip] gemini: GEMINI_API_KEY not set, skipping LLM check")
        models = []

    quota = Quota(cfg, ROOT / cfg["quota"]["ledger"])
    plan = {t.name: quota.estimate_tool_units(t.name, None) * len(tasks) for t in tools}
    plan.update({f"gemini:{m}": 1 for m in models})
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
    s.add_argument("--no-llm", action="store_true", help="skip the Gemini check")
    s.set_defaults(func=cmd_smoke)

    sub.add_parser("oracle", help="force each tool on every task").set_defaults(func=_not_yet(3))
    r = sub.add_parser("run", help="free-choice agent run")
    r.add_argument("--model"), r.add_argument("--mode", default="free")
    r.set_defaults(func=_not_yet(3))
    sub.add_parser("ablate", help="ablation runs").set_defaults(func=_not_yet(4))
    sub.add_parser("label", help="hand-label outputs for judge validation").set_defaults(func=_not_yet(4))
    sub.add_parser("report", help="build report.md and charts").set_defaults(func=_not_yet(5))

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
