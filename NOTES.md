# NOTES

Design decisions and anything odd observed.

## Design decisions

- **Layout.** `uv init --lib` gives `src/bench/`. Modules: `tools/`, `cli.py`, `config.py`,
  `quota.py`, `ratelimit.py`, `llm.py`, `runlog.py` (later: `agent.py`, `scoring.py`, `report.py`).
  Task data lives in `tasks/` at the repo root.
- **Pinning.** Direct dependencies are pinned with `==` (`uv add --bounds exact`); `uv.lock` pins
  everything transitive. Python is pinned to `==3.11.*`. `tzdata` is a dependency because Windows
  has no system timezone database, and Gemini quotas reset at midnight Pacific.
- **One interface.** `run(task_input) -> ToolResult{content, ok, latency_ms, cost_usd, error, meta}`.
  `run()` never raises. `cost_usd` is the **list price** of the call, not money actually spent.
- **What `ok` means.** `ok=True` only means the tool returned non-empty content without an error.
  Example: plain fetch on `quotes.toscrape.com/js/` returns 60 characters of page chrome and
  `ok=True`. Whether a tool *solved* a task is decided by scoring, not by `ok`.
- **Missing keys.** `load_tools` skips tools whose env var is unset and prints why.
- **`runs/` is committed.** The raw logs are the evidence.

## Budget: $0, free tiers only

The cap is on free-quota units, not dollars. `quota.py` keeps a ledger in `runs/quota.json`
(written after every call) and raises `QuotaPaused` *before* any call that would cross a line:

| Resource | Unit | Window | Pauses when |
|---|---|---|---|
| `gemini:<model>` | requests | Pacific day | requests today > `rpd * stop_frac` (0.9) |
| `firecrawl_scrape` | credits | month | > 800 used (of 1,000), or the live balance would drop below the 200-credit reserve |
| `tinyfish_fetch` | URLs | UTC day | > 900 (of 1,000 free). Past the allowance, Fetch bills the wallet, which would eat the agent budget. |
| `tinyfish_agent` | USD | all time | agent spend + worst-case next call > $6, or the live wallet would drop below $0.50 |

- **Live balances.** At preflight, the ledger is checked against the real numbers from
  `GET /v2/team/credit-usage` (Firecrawl) and `GET /v1/wallet` (TinyFish). The wallet also returns
  the live per-step rate, which overrides the config value. Both calls are best-effort; if they
  fail we fall back to the local ledger.
- **Pessimistic estimates.** A Firecrawl PDF scrape is assumed to cost `2 * pdf_max_pages` credits
  before the call; the actual credits are recorded after. An agent call is assumed to cost
  `max_steps * rate` = 5 × $0.016 = $0.08.
- **Rate limiting.** Each resource has a token bucket with burst 1, so calls are spaced evenly at
  60/rpm seconds. 429/503 responses are retried with exponential backoff (2, 4, 8, ... up to 60s)
  and honour `Retry-After` / Gemini's `retryDelay`. A Gemini 429 that names a *per-day* quota
  isn't retried; it pauses the run.
- **Resume.** `RunDir.start()` reuses the newest unfinished run with the same model and mode if the
  experiment config hash matches (the hash covers config.toml minus `[quota]`, so editing limits
  doesn't block a resume). Work units already in the JSONL logs are skipped. Each session's git
  commit is appended to `meta.json`. Exit code 3 means paused; rerun the same command to resume.
- **List price.** Every call records what it would cost at list price (Gemini tokens × Standard
  tier prices, Firecrawl credits × $0.005, TinyFish agent steps × rate, Fetch $0). This keeps the
  cost charts meaningful even though actual spend is $0.
- **Gemini counts every attempt**, including rejected ones. That's conservative: it may pause a
  little early.
- **No other LLM providers.** `llm.py` only calls models from `[models]` in config.toml, all of
  them Gemini. `OPENAI_API_KEY` is set in the user's shell but is never read.

## Models (checked 2026-11-08, ai.google.dev pricing page)

- `gemini/gemini-3.8-flash` (current Flash) and `gemini/gemini-3.5-flash-lite` (current Flash-Lite).
  Both are listed "Free of charge" on the free tier.
- Avoided `gemini-2.5-flash`: it's deprecated and from 2026-12-01 its requests are rerouted to
  3.5-flash-lite, which would silently change the model mid-benchmark.
- **The free-tier RPM/RPD numbers are not public.** The docs page says they are "viewable in Google
  AI Studio" (https://aistudio.google.com/rate-limit). The config values (Flash 10 RPM / 250 RPD,
  Flash-Lite 15 / 1,000) are conservative placeholders until the user copies the real numbers.
  Limits are per Google Cloud project, not per key.
- Free-tier Gemini traffic may be used by Google to improve its products. The prompts here are
  public web pages and questions, so this is acceptable, but it's worth stating.
- Flash prices rise on 2027-01-01 ($0.75→$1.50 in, $3.75→$7.50 out per 1M). List-price figures
  use the 2026 rates.

## Tool wrappers: API facts (docs checked 2026-11-08)

- **Firecrawl**: `POST https://api.firecrawl.dev/v2/scrape`, Bearer auth. Body `{url,
  formats:["markdown"], onlyMainContent:true, timeout, parsers:[{type:"pdf", maxPages:10}]}`.
  Passing `parsers` as a list also turns image OCR off. The target's own HTTP status is in
  `data.metadata.statusCode`; >=400 is treated as an error. `totalPages > numPages` means a PDF was
  truncated. Free plan: 10 scrapes/min, 2 concurrent.
- **TinyFish Fetch**: `POST https://api.fetch.tinyfish.ai`, `X-API-Key`. `results[]`/`errors[]`,
  both returned with HTTP 200. There is no JS toggle. Client timeout >= 150s per the docs.
- **TinyFish Agent**: `POST https://agent.tinyfish.ai/v1/automation/run` (sync), `{url, goal,
  browser_profile:"lite", agent_config:{max_steps:5, max_duration_seconds:120}}`. COMPLETED does
  not mean the goal succeeded, so scoring decides. Sync runs can't be cancelled; a 429 means the
  run never started, so retrying it is safe.
- **Agent scope.** In the oracle sweep the agent runs on `interactive` tasks only
  (`oracle_categories`). In free-choice runs it's offered everywhere, under the $6 global cap. So
  for non-interactive tasks the oracle has no data on whether the agent *could* have solved them;
  selection accuracy treats an agent pick on those tasks as "unknown", not "wrong". (To implement
  in Phase 3.)

## Pricing facts

- TinyFish agent: $0.016/step (pricing page and the wallet API's example meter). The user has the
  $8 signup credit only.
- TinyFish Fetch: free, 1,000 URLs/day (resets 00:00 UTC).
- Firecrawl: 1,000 free credits/month. **The docs contradict each other on PDF cost**: the scrape
  reference says 1 credit per page, the billing page says +1 per page on top of the base. We assume
  2 per page unless the API reports `creditsUsed`.

## Observations

- **readability-lxml loses most of docs.python.org pages.** On `library/json.html` it kept only
  the `JSONEncoder` section (4.8k chars) and dropped `json.dumps`. The first smoke check still
  "passed" because the expected string ("None") appears in a type table. That's a false positive.
  Fixes: (1) plain fetch falls back to full-body markdown when readability keeps less than 25% of
  the body text (34k chars on that page); (2) the smoke check uses a phrase only the `dumps` docs
  contain.
- **Rule for Phase 2 (all tasks):** a `contains`/`exact`/`regex` expected answer must be
  distinctive enough that it can't appear on the page, or in an unrelated part of it, by accident.
  Each task is checked by searching the page for the expected string: it should appear only in
  the answer location. Otherwise use a longer phrase, a regex anchored to context, or `judge`.
- **Plain fetch now parses PDFs** with pypdf (detected by content-type or `%PDF-` magic bytes),
  capped at the same `pdf_max_pages` as Firecrawl, so the baseline is a fair fight on PDF tasks.
- **Same-second run collisions.** Two runs started in the same second got the same directory name.
  Fixed with a `-2`, `-3` suffix.
- uv was not installed. It was installed with `pip install --user uv` (0.13.7), and Python 3.11
  was installed through uv.

## Open concern: Gemini request volume

Worst case is 4 LLM requests per task-run (3 tool calls + final answer).
- Free choice: 40 tasks × 3 reps × 4 = **480 requests per model**.
- Ablations at 3 reps: shuffle, neutral, salesy and anonymized = 4 × 480 = **1,920 per model**.
- Plus judge calls.

At the placeholder Flash limit (225 usable/day) that is about 11 days for Flash alone. Options to
decide in Phase 3: 1 rep for ablations, ablate only Flash-Lite, or use the real (possibly higher)
RPD from AI Studio. Resume makes multi-day runs workable either way.

## First live smoke (2026-11-08, 1 URL: quotes.toscrape.com/js/)

Kept to one URL to save quota.

- plain_fetch: 60 chars of page chrome, no quotes (expected: no JS).
- firecrawl_scrape: 1,505 chars, quotes present, 1 credit.
- tinyfish_fetch: 1,100 chars, quotes present. Fetch renders JS too.
- **tinyfish_agent: refused with HTTP 403 `FORBIDDEN: Your account is not enabled for custom max
  steps yet. Join the beta program at /beta`.** Setting `agent_config.max_steps` is a beta
  feature. No steps were run and the wallet was not charged. Unresolved; see below.
- Both Gemini models replied "OK". Flash used 59 output tokens for a one-word reply (thinking
  tokens), while Flash-Lite used 1. Thinking tokens will dominate Flash's list-price cost; consider
  a low reasoning effort for the agent loop.
- LiteLLM warns that `temperature` is deprecated for Gemini 3+ ("planned for removal"). It still
  works. If it's removed, determinism will rest on the seed and repeated runs only.
- Live balances: Firecrawl reports 1,400 remaining credits (plan 1,000; presumably signup
  bonus credits). The local 800-credit stop still applies. The TinyFish wallet shows $9.00, not $8.
- Quota ledger day for Gemini is the Pacific date (2026-11-07 at 05:41 UTC), as intended.
