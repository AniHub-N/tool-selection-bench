"""Charts for the README. No API calls.

1. charts/can_solve_by_category.png: oracle can-solve rate per tool and category.
2. charts/pick_vs_oracle_best.png: the model's FIRST pick against the oracle, per category, for
   free choice (neutral names) and the anonymized-names ablation.
   oracle-best = the cheapest tool with can=Y for that task, in the order
   plain_fetch (free, no JS) < tinyfish_fetch (free) < firecrawl_scrape (credits) < tinyfish_agent ($/step).
   "other capable" = a can=Y tool but not the cheapest; "can't solve" = can=n; "no verdict" = no
   oracle cell for that (task, tool), e.g. an agent pick on a static task.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .config import ROOT  # noqa: E402
from .freechoice import first_pick, latest, oracle_can  # noqa: E402
from .oracle import CATS, TOOL_ABBR, TOOL_ORDER  # noqa: E402
from .runlog import RunDir  # noqa: E402

COST_ORDER = ["plain_fetch", "tinyfish_fetch", "firecrawl_scrape", "tinyfish_agent"]
# Reference categorical palette (dataviz skill, light mode), slots 1-4 in fixed order.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SURFACE, INK, INK2, GRID, NEUTRAL = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#b5b4ae"
TOOL_LABEL = {"plain_fetch": "plain fetch", "firecrawl_scrape": "Firecrawl scrape",
              "tinyfish_fetch": "TinyFish Fetch", "tinyfish_agent": "TinyFish agent"}
CAT_LABEL = {"static": "static", "js_heavy": "JS-heavy", "pdf": "PDF", "interactive": "interactive"}


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def chart_can_solve(tasks: list[dict], out: Path) -> dict:
    can = oracle_can(tasks)
    cat_of = {t["id"]: t["category"] for t in tasks}
    rates: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for tl in TOOL_ORDER:
        for c in CATS:
            vals = [v for (tid, x), v in can.items() if x == tl and cat_of[tid] == c and v is not None]
            if vals:
                rates[tl][c] = (sum(vals), len(vals))
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150, facecolor=SURFACE)
    _style(ax)
    w = 0.2
    for i, tl in enumerate(TOOL_ORDER):
        for j, c in enumerate(CATS):
            if c not in rates[tl]:
                continue
            k, n = rates[tl][c]
            x = j + (i - 1.5) * w
            ax.bar(x, 100 * k / n, w * 0.9, color=SERIES[i], label=TOOL_LABEL[tl] if j == CATS.index(
                next(cc for cc in CATS if cc in rates[tl])) else None)
            ax.text(x, 100 * k / n + 1.5, f"{k}/{n}", ha="center", va="bottom", fontsize=7, color=INK2)
    ax.set_xticks(range(len(CATS)), [CAT_LABEL[c] for c in CATS], color=INK, fontsize=10)
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_title("Can the tool solve the task? (answer present in raw tool output)", loc="left",
                 fontsize=11, color=INK, pad=24)
    ax.legend(ncol=4, frameon=False, fontsize=8, loc="lower left", bbox_to_anchor=(0, 1.0), labelcolor=INK)
    fig.text(0.01, 0.01, "Oracle sweep: Flash-Lite forced to use one tool, 1 rep, 40 tasks. TinyFish agent was run on "
             "interactive tasks + the 3 tasks no fetch tool solved.", fontsize=6.5, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return rates


def pick_classes(run_path: Path, tasks: list[dict], can: dict) -> dict[str, Counter]:
    by: dict[str, Counter] = defaultdict(Counter)
    for r in latest(RunDir(run_path)):
        fp = first_pick(r)
        capable = [tl for tl in COST_ORDER if can.get((r["task_id"], tl)) is True]
        if fp is None:
            k = "no tool call"
        elif capable and fp == capable[0]:
            k = "oracle-best"
        elif can.get((r["task_id"], fp)) is True:
            k = "other capable tool"
        elif can.get((r["task_id"], fp)) is False:
            k = "can't solve"
        else:
            k = "no verdict"
        by[r["category"]][k] += 1
        by["all"][k] += 1
    return by


KINDS = ["oracle-best", "other capable tool", "can't solve", "no verdict"]
KIND_COLOR = {"oracle-best": SERIES[0], "other capable tool": SERIES[2], "can't solve": SERIES[1],
              "no verdict": NEUTRAL, "no tool call": NEUTRAL}


def chart_pick_vs_best(tasks: list[dict], runs: list[tuple[str, Path]], out: Path) -> dict:
    can = oracle_can(tasks)
    data = {label: pick_classes(p, tasks, can) for label, p in runs}
    rows = ["all"] + CATS
    fig, axes = plt.subplots(1, len(runs), figsize=(max(4.6 * len(runs), 8), 3.8), dpi=150, facecolor=SURFACE,
                             sharey=True, squeeze=False)
    for ax, (label, _) in zip(axes[0], runs):
        _style(ax)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.spines["bottom"].set_visible(False)
        for yi, c in enumerate(rows):
            cnt = data[label][c]
            n = sum(cnt.values()) or 1
            left = 0.0
            for kind in KINDS + ["no tool call"]:
                v = cnt.get(kind, 0)
                if not v:
                    continue
                share = 100 * v / n
                ax.barh(yi, share, left=left, height=0.62, color=KIND_COLOR[kind], edgecolor=SURFACE, linewidth=1.5)
                if share >= 9:
                    ax.text(left + share / 2, yi, str(v), ha="center", va="center", fontsize=7.5,
                            color="white" if kind != "no verdict" else INK)
                left += share
            ax.text(101, yi, f"n={sum(cnt.values())}", va="center", fontsize=7, color=INK2)
        ax.set_yticks(range(len(rows)), ["all tasks"] + [CAT_LABEL[c] for c in CATS], color=INK)
        ax.invert_yaxis()
        ax.set_xlim(0, 112)
        ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
        ax.set_title(label, loc="left", fontsize=10, color=INK)
    handles = [plt.Rectangle((0, 0), 1, 1, color=KIND_COLOR[k]) for k in KINDS]
    fig.legend(handles, ["picked the oracle-best (cheapest capable) tool", "picked another capable tool",
                         "picked a tool that can't solve it", "no oracle verdict for the pick"],
               ncol=2, frameon=False, fontsize=7.5, loc="lower left", bbox_to_anchor=(0.01, 0.0), labelcolor=INK)
    fig.suptitle("Flash-Lite's first tool pick vs the oracle", x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0.12, 1, 0.95))
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return data


def build(tasks: list[dict]) -> dict:
    runs_dir = ROOT / "runs"
    free = max(runs_dir.glob("*_flash-lite_free"), default=None)
    anon = max(runs_dir.glob("*_flash-lite_anon"), default=None)
    runs = [(f"Free choice, neutral names (x{RunDir(free).meta.get('reps')})", free)] if free else []
    # Only chart a finished ablation (every task x rep present); a partial one would mislead.
    if anon and len(latest(RunDir(anon))) >= RunDir(anon).meta.get("n_tasks", 0) * RunDir(anon).meta.get("reps", 1):
        runs.append(("Anonymized tool names (x1)", anon))
    charts = ROOT / "charts"
    rates = chart_can_solve(tasks, charts / "can_solve_by_category.png")
    picks = chart_pick_vs_best(tasks, runs, charts / "pick_vs_oracle_best.png") if runs else {}
    return {"rates": rates, "picks": picks, "runs": runs}
