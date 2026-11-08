"""Free-quota tracking (the budget is $0).

Tracked resources and when they pause a run:
- gemini:<model>    requests per Pacific day   > rpd * stop_frac
- firecrawl_scrape  credits per month          > stop_at (800 of 1,000), also checked against
                                                 the live remaining balance
- tinyfish_fetch    URLs per UTC day           > stop_at (900 of the free 1,000)
- tinyfish_agent    USD, all-time              > cap_usd ($6), or live wallet < min_wallet_usd

A pause raises QuotaPaused *before* the call that would cross the line. Runs are
resumable, so rerunning the same command later picks up where it stopped.
The ledger (runs/quota.json) is written after every call so a crash loses nothing.
Every call also accumulates `list_usd`: what it would have cost at list price.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .ratelimit import TokenBucket
from .tools.base import Tool, ToolResult

PT = ZoneInfo("America/Los_Angeles")


class QuotaPaused(RuntimeError):
    """Stop cleanly. Rerun the same command later to resume."""


def pt_day() -> str:
    return datetime.now(PT).date().isoformat()


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class Quota:
    def __init__(self, cfg: dict[str, Any], ledger: Path) -> None:
        self.cfg = cfg
        self.q = cfg["quota"]
        self.ledger = ledger
        self.data: dict[str, Any] = json.loads(ledger.read_text()) if ledger.exists() else {}
        for k in ("gemini", "firecrawl_scrape", "tinyfish_fetch"):
            self.data.setdefault(k, {})
        self.data.setdefault("tinyfish_agent", {"usd": 0.0, "steps": 0, "calls": 0})
        self.data.setdefault("list_usd_total", 0.0)
        self.live: dict[str, float] = {}       # live balances at preflight
        self.session: dict[str, float] = {}    # units used since preflight
        self.session_list_usd = 0.0
        self.agent_usd_per_step = cfg["tools"]["tinyfish_agent"]["usd_per_step"]
        self._buckets: dict[str, TokenBucket] = {}

    # ---- bookkeeping ---------------------------------------------------------

    def _limits_key(self, resource: str) -> dict[str, Any]:
        if resource.startswith("gemini:"):
            return self.q["gemini"][resource.split(":", 1)[1]]
        return self.q.get(resource, {})

    def bucket(self, resource: str) -> TokenBucket | None:
        rpm = self._limits_key(resource).get("rpm")
        if not rpm:
            return None
        if resource not in self._buckets:
            self._buckets[resource] = TokenBucket(rpm)
        return self._buckets[resource]

    def used(self, resource: str) -> float:
        if resource.startswith("gemini:"):
            rec = self.data["gemini"].get(resource.split(":", 1)[1], {})
            return rec.get("requests", 0) if rec.get("day") == pt_day() else 0
        if resource == "firecrawl_scrape":
            return self.data["firecrawl_scrape"].get(utc_month(), 0)
        if resource == "tinyfish_fetch":
            return self.data["tinyfish_fetch"].get(utc_day(), 0)
        if resource == "tinyfish_agent":
            return self.data["tinyfish_agent"]["usd"]
        return 0

    def limit(self, resource: str) -> float | None:
        lim = self._limits_key(resource)
        if resource.startswith("gemini:"):
            return int(lim["rpd"] * lim.get("stop_frac", 1.0))
        if resource in ("firecrawl_scrape", "tinyfish_fetch"):
            return lim["stop_at"]
        if resource == "tinyfish_agent":
            return lim["cap_usd"]
        return None  # plain_fetch: unlimited

    def check(self, resource: str, est_units: float) -> None:
        """Raise QuotaPaused if spending est_units would cross a limit."""
        lim = self.limit(resource)
        used = self.used(resource)
        if lim is not None and used + est_units > lim:
            raise QuotaPaused(f"{resource}: used {used:g} + next ~{est_units:g} would exceed "
                              f"the stop limit {lim:g}. {self._reset_hint(resource)}")
        if resource == "firecrawl_scrape" and "firecrawl_remaining" in self.live:
            reserve = self.q["firecrawl_scrape"]["free_credits"] - self.q["firecrawl_scrape"]["stop_at"]
            left = self.live["firecrawl_remaining"] - self.session.get(resource, 0)
            if left - est_units < reserve:
                raise QuotaPaused(f"firecrawl_scrape: live balance ~{left:g} credits would drop below "
                                  f"the {reserve:g}-credit reserve.")
        if resource == "tinyfish_agent" and "tinyfish_wallet" in self.live:
            left = self.live["tinyfish_wallet"] - self.session.get(resource, 0)
            if left - est_units < self.q["tinyfish_agent"]["min_wallet_usd"]:
                raise QuotaPaused(f"tinyfish_agent: wallet ~${left:.2f} would drop below "
                                  f"${self.q['tinyfish_agent']['min_wallet_usd']:.2f}.")

    @staticmethod
    def _reset_hint(resource: str) -> str:
        if resource.startswith("gemini:"):
            return "Resets at midnight Pacific; rerun the same command then to resume."
        if resource == "tinyfish_fetch":
            return "Resets 00:00 UTC; rerun then to resume."
        if resource == "firecrawl_scrape":
            return "Resets with the Firecrawl billing month."
        return "This cap does not reset. Raise it in config.toml only if you mean to spend more."

    def record(self, resource: str, units: float, list_usd: float, extra: dict | None = None) -> None:
        if resource.startswith("gemini:"):
            name = resource.split(":", 1)[1]
            rec = self.data["gemini"].get(name, {})
            if rec.get("day") != pt_day():
                rec = {"day": pt_day(), "requests": 0, "tokens_in": 0, "tokens_out": 0}
            rec["requests"] += units
            for k, v in (extra or {}).items():
                rec[k] = rec.get(k, 0) + v
            self.data["gemini"][name] = rec
        elif resource == "firecrawl_scrape":
            m = utc_month()
            self.data[resource][m] = self.data[resource].get(m, 0) + units
        elif resource == "tinyfish_fetch":
            d = utc_day()
            self.data[resource][d] = self.data[resource].get(d, 0) + units
        elif resource == "tinyfish_agent":
            a = self.data["tinyfish_agent"]
            a["usd"] = round(a["usd"] + units, 6)
            a["steps"] += (extra or {}).get("steps", 0)
            a["calls"] += 1
        self.session[resource] = self.session.get(resource, 0) + units
        self.session_list_usd += list_usd
        self.data["list_usd_total"] = round(self.data["list_usd_total"] + list_usd, 6)
        self.save()

    def save(self) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        # Windows: os.replace can fail with WinError 5 while another process (indexer,
        # antivirus, editor) briefly holds the file. Retry before giving up.
        for attempt in range(10):
            try:
                os.replace(tmp, self.ledger)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1 * (attempt + 1))

    # ---- tools -------------------------------------------------------------

    def estimate_tool_units(self, tool_name: str, category: str | None = None) -> float:
        """Pessimistic per-call quota units."""
        if tool_name == "firecrawl_scrape":
            # PDFs bill per page (docs disagree on 1 vs 2 per page; assume 2)
            return 2 * self.cfg["tools"]["pdf_max_pages"] if category == "pdf" else 1
        if tool_name == "tinyfish_fetch":
            return 1
        if tool_name == "tinyfish_agent":
            return self.cfg["tools"]["tinyfish_agent"]["max_steps"] * self.agent_usd_per_step
        return 0

    def run_tool(self, tool: Tool, task_input: dict[str, Any], category: str | None = None) -> ToolResult:
        """Check quota, rate-limit, run, record. Raises QuotaPaused before crossing a limit."""
        self.check(tool.name, self.estimate_tool_units(tool.name, category))
        b = self.bucket(tool.name)
        if b:
            b.acquire()
        res = tool.run(task_input)
        if tool.name == "firecrawl_scrape":
            units = res.meta.get("credits", 0)
        elif tool.name == "tinyfish_agent":
            res.cost_usd = res.meta.get("steps", 0) * self.agent_usd_per_step  # live rate
            units = res.cost_usd
        elif tool.name == "tinyfish_fetch":
            units = 1 if res.meta.get("billed", True) else 0
        else:
            units = 0
        self.record(tool.name, units, res.cost_usd, {"steps": res.meta.get("steps", 0)})
        return res

    # ---- live balances -------------------------------------------------------

    def sync_live(self) -> list[str]:
        """Read real balances where the provider exposes them. Never raises."""
        notes = []
        fc = os.environ.get("FIRECRAWL_API_KEY")
        if fc:
            try:
                r = httpx.get("https://api.firecrawl.dev/v2/team/credit-usage",
                              headers={"Authorization": f"Bearer {fc}"}, timeout=20)
                d = r.json().get("data") or {}
                if "remainingCredits" in d:
                    self.live["firecrawl_remaining"] = float(d["remainingCredits"])
                    notes.append(f"firecrawl live: {d['remainingCredits']} credits remaining "
                                 f"(plan {d.get('planCredits')}, period ends {d.get('billingPeriodEnd')})")
                else:
                    notes.append(f"firecrawl live balance unavailable (HTTP {r.status_code})")
            except Exception as e:
                notes.append(f"firecrawl live balance unavailable ({type(e).__name__})")
        tf = os.environ.get("TINYFISH_API_KEY")
        if tf:
            try:
                r = httpx.get("https://agent.tinyfish.ai/v1/wallet", headers={"X-API-Key": tf}, timeout=20)
                d = r.json()
                if "available_balance" in d:
                    self.live["tinyfish_wallet"] = float(d["available_balance"])
                    notes.append(f"tinyfish live: wallet ${float(d['available_balance']):.2f}")
                    for m in ((d.get("rates") or {}).get("meters") or []):
                        if m.get("per") == "step":
                            rate = float(m["unit_amount"])
                            if abs(rate - self.agent_usd_per_step) > 1e-9:
                                notes.append(f"tinyfish live step rate ${rate} differs from config "
                                             f"${self.agent_usd_per_step}; using live rate")
                            self.agent_usd_per_step = rate
                else:
                    notes.append(f"tinyfish wallet unavailable (HTTP {r.status_code})")
            except Exception as e:
                notes.append(f"tinyfish wallet unavailable ({type(e).__name__})")
        return notes

    def preflight(self, plan: dict[str, float], label: str) -> None:
        """Print estimated quota use vs what's left. Does not abort: runs pause and resume."""
        print(f"[quota] preflight for {label}")
        for n in self.sync_live():
            print(f"[quota]   {n}")
        print(f"[quota]   {'resource':<22}{'estimate':>10}{'used':>10}{'stop at':>10}")
        for res, est in plan.items():
            lim = self.limit(res)
            used = self.used(res)
            flag = ""
            if lim is not None and used + est > lim:
                flag = "  <- will pause partway; rerun the same command to resume"
            print(f"[quota]   {res:<22}{est:>10.3g}{used:>10.3g}{(lim if lim is not None else '-'):>10}{flag}")
        print(f"[quota]   list-price total so far: ${self.data['list_usd_total']:.4f} (actual spend: $0)")
