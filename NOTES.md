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

## Phase 2: task drafting (2026-11-08)

Ground truth was verified only with free requests (httpx + the plain-fetch extractor, plus each
JS page's underlying data source: embedded script arrays, JSON endpoints, swagger.json). No
Gemini, Firecrawl or TinyFish quota was used. `tasks/build_tasks.py` is the source of truth; each
task records `verified_via` so it can be re-checked by hand, and `memory_risk` for answers a
model might know without fetching. `python -m bench tasks-check` reruns the free visibility
check and writes `tasks/plain_visibility.json`.

- **Truncation was silently making tasks unanswerable.** Table values in arXiv PDFs sit 20–31k
  characters into the extracted text, past the agent's 20k cutoff, so every tool would fail them
  equally. `agent.max_content_chars` is raised to 50k (about 12k tokens), which is fine for Gemini.
  The same cutoff applies to every tool.
- **Accidental matches on the start page.** The visibility check flagged i03 ("252" is also
  Philadelphia's 1990 GF on page 1) and i05 ("19" is an item number on the home page). Both were
  replaced: Toronto GF 247, and Nonfiction = 110 books.
- **The "scanned" PDF had a text layer.** EWD215 is scanned, but page 1 is a typed copyright
  notice that contains the title. The first draft asked for the title, which needed no OCR.
  It now asks for a detail found only in the scanned body (Copenhagen, verified from the archive's
  transcription).
- **Small-number answers.** Where the natural answer is a small number that a wrong answer could
  contain, the question asks for something else (i02 asks for a film title, not a count) or uses
  `judge`. `tests/test_tasks.py` checks that each expected answer passes its own check and that
  a set of decoy wrong answers fails every deterministic check.
- **Wikipedia was dropped.** The Wikimedia API returns 403 to generic bot User-Agents; their
  policy requires contact details in the UA, and we don't put the user's email in requests.
  Article pages did load, but given the policy, every Wikipedia task was swapped out.
- **Memory risk.** Questions favour fixture data (product codes, sandbox tables, paper tables).
  Nine tasks are marked `memory_risk: medium`; the analysis should check whether the agent
  answered those without calling a tool.
- **PDF URLs are direct links** to the PDF (versioned arXiv `vN` URLs, archived BLS release).
  The spec's "question answered inside a linked PDF" could also mean an HTML page linking to the
  PDF; open question for review.
- **scrapingcourse.com loads a Cloudflare Turnstile script.** It's a scraping sandbox, but
  bot-protection may affect some tools. Worth watching in the oracle sweep.
- **Petstore is used twice** (j08, j09). There were few stable, fully client-rendered public pages.
- Dropped candidates: the webscraper.io static laptops page (product names no longer in the
  HTML); the Redoc demo (spec and asset hashes change with releases); the httpbin Swagger UI (no
  stable version field); ECMA-404 and RFC 1149 (too well known, so answerable from memory);
  the W3C CSS2 property index (the answer "2" isn't distinctive).

## Phase 3 prep: no_tools mode (2026-11-08)

- **The neutral system prompt with zero tools makes Gemini return nothing.** The first
  Flash-Lite no_tools run returned empty content on 39 of 40 tasks (finish_reason=stop,
  0 output tokens, only a thought signature). It isn't temperature: the default temperature was
  also empty. With no system prompt, or "Answer the question.", it answers normally. So no_tools
  uses `agent.system_prompt_no_tools = "Answer the question."`; everything else about the agent
  is unchanged. The broken run is kept, marked `status: invalid`. Run summaries now warn when
  any final answer is empty.
- **Partial memorization of fixture data.** From memory, Flash-Lite gave the s01 UPC as
  `a897fe39a106848d`; the real value is `a897fe39b1053632`. The first 8 characters match, so
  books.toscrape is in its training data, but not exactly. The deterministic check correctly
  fails it.
- **Infra errors were being logged as results.** A Windows `PermissionError` (WinError 5) while
  replacing the quota ledger killed one task (s03) and it was recorded as a failed answer, so
  resume would have skipped it. Fixes: the ledger save retries on PermissionError; resume
  retries records that have an error and no answer; aggregation uses the latest record per
  (task, rep).
- **url_navigable.** i02 is false (year links are `href="#"`; the endpoint only appears in
  external JS). i07 and i08 are true: DataTables example pages print their own source code,
  including `ajax: '../ajax/data/objects.txt'` / `arrays.txt`, and those files hold the answer.
  The user's initial read had i07 false; flagged for review. The same applies to the js_heavy
  DataTables tasks (j06, j07): a fetch-only agent could read the data file directly.
- **i10 tight call budget.** Page 1 links to /pagination/2, /3 and /13. A sequential scan
  reaches the answer on the 3rd call (exactly the cap); jumping straight to /3 takes 2. It's
  flagged `analysis_flag: tight_call_budget` and reported separately.
- **Flash's real free-tier RPD is about 20, not 250.** After 21 Flash requests on 2026-11-07 (Pacific
  day; smoke + judge + 9 no_tools tasks), the server returned 429 "You exceeded your current
  quota". The daily-quota detector paused the run cleanly, and it resumes after midnight Pacific.
  `quota.gemini.flash.rpd` is now set to 20. At about 20 requests/day the Flash plan (no_tools 45,
  free choice up to 480, ablations more) takes weeks, which needs a decision.
- **Is Flash browsing, or did it memorize?** Before pausing, Flash answered all 9 no_tools tasks it
  reached (s01–s09), quoting page text verbatim (exact UPC, "In stock (20 available)"). Input
  tokens stayed around 48, so no page content was added to the prompt, but Gemini bills built-in
  URL fetching separately, so that doesn't settle it. These toscrape strings are also reproduced
  in many scraping tutorials. Planned test: ask Flash about a random.org `rnd=id.<seed>` URL
  (deterministic per seed, checkable locally, can't be memorized).
- Flash-Lite no_tools (complete): 12/40 correct from memory, so memorization is not rare.

## Model change and memorization rewrites (2026-11-08)

- **`url_navigable` is classified by data reachability, not UI intent** (user decision). A task is
  `url_navigable: true` if a fetch-only agent can reach the answer by fetching some URL that is
  visible in what a fetch tool returns: pagination, tag, category or query-param links, or a data
  file named in printed example code (DataTables i07/i08). It's `false` only when no such URL is
  visible (i01 Load More XHR; i02 href="#" links whose endpoint lives only in external JS).
- **Llama 4 Scout is gone from Groq.** Shut down 2026-07-17 (deprecation page; suggested replacements
  `openai/gpt-oss-120b` or `qwen/qwen3.6-27b`, and the models page now lists `qwen/qwen3.8-27b`).
  Groq's Developer-plan limits for those models are **8K TPM**, and free-plan limits aren't public
  (they're on the logged-in limits page). 8K TPM can't take one request with a 50k-char tool
  result (~12k tokens), let alone a 3-call conversation where earlier tool results are re-sent.
  Model choice is pending user input. Cerebras' free trial lists gpt-oss-120b and qwen-3.8-27b at
  5 RPM, 30K uncached TPM / 90K total TPM, 1M TPD, which would fit.
- **LiteLLM crashed on any request with `tools`**: `ModuleNotFoundError: No module named 'mcp'`
  (litellm 1.108.3 imports its MCP handler lazily on the tools path). Every tool-calling run would
  have died. Fixed by adding `mcp==2.3.0`. This was found by the offline request-capture check, not
  by a live run.
- **Verified: LiteLLM never attaches built-in Gemini tools.** A local fake Gemini endpoint captured
  the exact request bodies for both models in three shapes (no tools / function tools /
  tool_choice none). There was no `url_context`, `google_search` or `code_execution`; only our
  function declarations, plus `functionCallingConfig: NONE` for tool_choice none. Now a regression
  test (`tests/test_llm_requests.py`). So any "browsing" by Flash in no_tools would be
  server-side and unrequested; the random.org test checks that.
- **Memorized-task rewrites.** Flash-Lite's 12 memorized tasks: 4 kept unchanged as the
  `known_answer: true` subset (s04, j08, p03, i08; one per category), 8 rewritten to a
  less-cited detail on the same URL (s03, s06, j01, j07, p04, p08, p09, i07), each with
  `rewritten_from`. Ground truth was re-verified with free requests, and all rewrites passed the
  self-tests and the plain-fetch visibility check.
- Git Bash's `TZ=America/Los_Angeles date` silently prints UTC on Windows. Python's zoneinfo
  (used by the quota) is correct. Don't trust shell `date` for quota reset times here.
- **random.org test: Gemini does not fetch.** For a random.org URL with a fixed seed (truth 161246307,
  fetched twice locally), both Flash and Flash-Lite replied that they can't access URLs
  (`runs/fetchtest_20261108.json`). With the request-capture test, this means no_tools results for
  Gemini are valid, and Flash's 9/9 on s01–s09 was **memorization**: it knows books.toscrape,
  scrapethissite and the PostgreSQL/SQLite pages verbatim. Flash is a side run, so its
  memorization doesn't drive rewrites; it will be reported alongside its results. Flash's no_tools
  run is still partial (9/40).
- **After the rewrites (Flash-Lite no_tools on the 8 new questions): 6 of 8 are no longer
  memorized.** Two still are: j07 (Flash-Lite appears to know the whole DataTables sample dataset,
  its third hit after i07/i08) and p08 (December 2023 motor-insurance CPI was widely reported).
  Both are left as `memorized: true` (1 per category, within the threshold) and reported
  separately. They are not part of the deliberate `known_answer` subset.
- **Resume now also hashes the task file**, so editing questions blocks resuming a run started
  with the old ones. Result records now carry the question text, and `bench memorized` only
  credits a result to a task if it was for the task's current question.
- A quoting slip in a scripted edit put a literal newline into a string in cli.py, which only
  showed up when the CLI was run. Added `tests/test_cli.py` (imports the CLI and parses every
  subcommand) so a broken CLI fails the test suite.

## Answer-format tolerance (2026-11-08)

- Many references have several valid formats (j07 "12th Dec 11" = 2011-12-12, p09 2.1×10^6 =
  2.1e6 = 2,100,000, s06 "-1000 to 1000"). A format miss would be scored as a failure and read
  as a tool failure. Fix: `src/bench/normalize.py` plus a `value` field on 26 numeric/date/range
  tasks. A task passes if the value matches in any format **or** the original regex/contains
  passes, so this can only remove false fails. tests/test_normalize.py checks both directions
  (right value in another format passes; wrong value in the right format fails).
- Number matching is exact up to float rounding: it tolerates format, not precision.
- Text `contains` checks ignore accents and punctuation ("Andre Gide", "Dr Seuss"), but only for
  references without digits: stripping the "." would make 60.5 match 6.05.
- A failed deterministic check whose answer still contains the reference's letters and digits is
  marked `format_suspect` in the score. Those rows get a hand review (label CLI) and are never
  silently counted as tool failures.
- Found while writing the tests: the money regexes (`\$\s?64(\.00)?\b`) accepted "$64.50".
  Tightened to `(?:\.00)?(?!\.?\d)`.
- **Re-scoring the existing no_tools runs (free, deterministic) changed one verdict.** Flash-Lite
  answered j06 "**28th Nov 08**", the site's own format, which the old ISO-only regex rejected.
  So j06 is memorized as well, and js_heavy now has 2 non-known_answer memorized tasks (j06, j07,
  both from the DataTables sample data, which Flash-Lite appears to know wholesale).

## Second main model: NVIDIA build (2026-11-08)

- Mistral's free Experiment plan now requires payment, so the decision moved to NVIDIA
  build.nvidia.com trial credits (key in .env as `NVIDIA_KEY`; `NVIDIA_API_KEY` also accepted).
- `GET /v1/models` (free) lists 84 models. **Neither Llama 3.3 70B nor gpt-oss-120b is listed.**
  Only `openai/gpt-oss-20b` is from the gpt-oss family.
- First pick was `nvidia/nemotron-3-super-120b-a12b` (largest listed, card says "Function
  Calling: Supported"). The one tool-check request returned **404 "Function ... Not found for
  account"**: the model is listed but not deployed for this account. Listing ≠ access.
- Credits: the ledger counts 1 credit per request *attempt* (conservative; NVIDIA has no balance
  API we can call), shared by every provider="nvidia" model, all-time, stop at 900 of 1,000.
- NVIDIA models are called with an explicit `api_base` and `api_key` (`llm.provider_kwargs`), so
  LiteLLM never picks up another provider's key from the environment.
- Reduced plan for the NVIDIA model: no_tools + free ×2 + one ablation (anonymized names).
  Rough budget: 40 + 2×40×≤4 + 40×≤4 = ≤520 requests (incl. retries well under 900).
- **Outcome: NVIDIA unusable on 2026-11-08, so the fallback applies.** 7 attempts, 0 answers:
  nemotron-3-super-120b-a12b, kimi-k2.6, mistral-large-2-instruct → 404 not found for account;
  deepseek-v4.1-flash, gpt-oss-20b → 504, then no reply within 150 s. All 7 are counted in the
  ledger. **Flash-Lite is the only main model; the README must say "Gemini family only".** The
  NVIDIA wiring stays (pointed at gpt-oss-20b) so `python -m bench tool-check --model nemotron`
  can retry it; if it passes, it joins on the reduced plan.
- **Dropped (2026-11-08).** The one permitted retry (`tool-check --model nemotron`, gpt-oss-20b)
  got no reply and was killed after 240 s: 8 attempts, 0 answers in total. Per the decision, NVIDIA
  is removed from config.toml, quota.py, llm.py and the tests; nothing NVIDIA-specific remains
  wired. runs/quota.json keeps the 8 attempts as history. Gemini family only.

## Memorization: final state before the oracle (2026-11-08)

- **The DataTables sample dataset (datatables.net/examples) is fully memorized by Flash-Lite**:
  j06 (Airi Satou start date, found by the format-tolerant re-score) and j07 (Caesar Vance start
  date, even after the rewrite). Both stay `memorized: true` and are reported separately.
  (i08 on the same dataset is in the known_answer subset; i07, Zenaida Frank's extension, was not
  answered correctly from memory.)
- **js_heavy therefore has 8 clean tasks** (10 minus j06 and j07); j08 is known_answer, so 7
  non-memorized, non-known_answer js_heavy tasks. The one-per-category rule is relaxed to allow
  this. No more rewrites.

## Oracle sweep design (2026-11-08)

- Each cell = the real agent (Flash-Lite, same system prompt, same 3-call cap) offered ONLY that
  tool, with its neutral description, and `tool_choice="required"` on the first turn so it can't
  answer from memory. Later turns are free (auto), then a forced final answer at the cap. One rep.
  This measures "can this tool, as used by this model, solve the task"; regret and selection
  accuracy compare free-choice picks against it.
- TinyFish agent: interactive tasks only (`oracle_categories`). Beta confirmed active 2026-11-08
  (1 smoke request: 3 steps, $0.048, correct answer).
- Each logged tool call also records `blocked` (challenge-page phrases only: scrapingcourse's
  own nav lists its "Cloudflare"/"CAPTCHA" practice pages, so those words are not markers) and
  `answer_in_content` (deterministic check on the raw output the model saw; noisy for small
  numbers, diagnostic only).
- Judge verdicts that hit the Flash judge quota are stored as `judge_pending`; rerunning
  `oracle` re-judges them without calling tools again.
- Oracle runs with the same config + task file merge into one table (`oracle-table`), so a
  separate agent-only run would merge in.
- Neutral tool descriptions live in config.toml `[tool_descriptions.neutral]`. Facts behind them:
  smoke showed plain fetch gets 60 chars on quotes/js while Firecrawl and TinyFish Fetch get the
  rendered quotes.

## Oracle verdict redefined: answer present in raw tool output (2026-11-08)

- **"Tool can solve this task" = the answer is present in the tool's raw output** (any call in the
  cell). Flash-Lite's extraction pass/fail is a **separate** column. Otherwise a Flash-Lite
  extraction miss would count against the tool, and selection accuracy would later be scored
  against the wrong oracle.
- Every tool call's full, untruncated output is saved content-addressed in `runs/<run>/raw/`
  (gitignored: it's third-party page and PDF text), so the verdict is recomputed offline by
  `oracle-table` and evidence patterns can be refined without re-running anything.
- Evidence (`tasks/build_tasks.py` EVIDENCE, `scoring.evidence_present`):
  - default: the task's own check run on the raw output; fine for distinctive answers.
  - context patterns for small numbers and prices on pages full of them (e.g. the first price
    after "Grayson Crewneck Sweatshirt"; "In stock (20 available)"; the Boston Bruins 1990 row).
  - `aggregate` (i09 humor count, j09 store-op count): never appears verbatim, so presence is
    undecidable and the oracle reports `?` for fetch tools.
  - the TinyFish agent's output is an answer, not a page, so it's checked like an answer.
  - Known limit: position questions (j02 7th author, j10 last author on page 2, i04 first author
    on page 4) count as "present" if the name appears in the fetched output; the right page is
    implied, not verified.
- The first sweep was stopped after 12 cells (s01-s04 on 3 fetch tools) because it saved only the
  first 300 chars of each output; that run and the s01 test cell are marked `invalid`.
- Oracle rows merge on the config hash + each task's current question, not the task-file hash,
  so editing evidence patterns afterwards doesn't orphan results.
- Patterns are validated by hand against the saved raw outputs once the sweep is done.
- **Counting tasks are hand-labeled, not left as `?`** (i09, j09 × the 3 fetch tools = 6 cells).
  `can` comes from reading the saved raw outputs; labels live in tasks/oracle_hand_labels.json with
  `hand_labeled: true`, a note, and the exact `raw_refs` they were made from. If a cell is re-run
  (different raw outputs) the label no longer applies and the cell reverts to `?`. Cells show `H`.
- **Position tasks tightened** (j02, j10, i04): `can` = the right page was fetched (the call's URL)
  AND the name sits at the asked position in that output's ordered author list
  (`scoring.ordered_authors` handles plain/Firecrawl/TinyFish markdown and the /api/quotes JSON).
  "this page" (j02, j10) = exactly the task URL; "page 4 of this site" (i04) = any view of page 4
  (HTML, /js/, /js-delayed/, `/api/quotes?page=4`). Seen in the raw outputs: on j02 several cells
  fetched `/` or `/api/quotes`, which hold the same page-1 data, and the old presence check would
  have credited them. Now they get no credit.
- Run resume hashes only result-affecting task fields (`runlog.TASK_CORE`: id, category, url,
  question, expected_answer, check_type, pattern, value). Annotation edits (evidence, flags) no
  longer block resuming.

## Oracle sweep results (2026-11-08, run 20261108T125742Z_flash-lite_oracle)

- 130 cells complete; p10 × 3 have `can`=Y but the judge verdict is pending (Flash judge hit its
  18/day stop). Rerun `python -m bench oracle` after midnight Pacific: it only re-judges.
- Crash mid-sweep: a Flash judge call raised `litellm.Timeout`, which the retry logic didn't
  treat as retryable, and the exception killed the run at cell 58. Fixed: Timeout is retryable,
  LLM calls default to a 120 s timeout, and judge failures become `judge_pending`. Resumed; one
  cell (j09/firecrawl) was re-run. The run's stored hash was migrated to the new
  annotation-insensitive task hash (recorded in meta.json `hash_migrated`).
- Evidence pattern fix after reading raw outputs: the DataTables patterns assumed one table row
  per line, but `arrays.txt` (the AJAX source all three tools reached) puts each field on its own
  line. Now `REC`: same line or same JSON record (stops at "]"). j06 and i08 flipped n -> Y.
- **Content loss inside tools, seen in the raw outputs (not model errors):**
  - plain_fetch (readability): drops quote blocks on quotes.toscrape: tag/humor 7 of 10,
    tag/humor/page/2 1 of 2, /page/2/ 5 of 10. It made Flash-Lite answer 8 on i09 and "Douglas
    Adams" on j10 (from /page/2/). This is our baseline's extraction step, not the site.
  - tinyfish_fetch: /js/page/2/ returned 3 of 10 quotes; books.toscrape listing pages come back
    as prices only (no titles); the Swagger UI's store operations are collapsed. Not blocked; the
    output is just partial.
- **No bot/Cloudflare blocks detected** on any call (0 challenge-page markers), including
  scrapingcourse.com.
- Routes that differ from the start URL but count under data reachability: p07 (plain and
  TinyFish found the EWD215.html transcription; Firecrawl OCR'd the scanned PDF itself), j05
  (product page), j06/i08 (arrays.txt), j09 (swagger.json), j01 plain (the non-JS homepage holds
  the same quote and tags).
- Empty final answers: in every cell that hit the 3-call cap without the tool delivering the
  answer, Flash-Lite's forced final turn (tool_choice="none") returned empty text instead of "I
  don't know". Scored as a fail; it never coincides with can=Y.
- Quota this sweep: Firecrawl 141 credits (live 1,399 -> 1,258); TinyFish Fetch 78 URLs (free
  1,000/day); TinyFish agent 12 calls, 53 steps, $0.848 (ledger total $0.896 of the $6 cap; wallet
  $10.95 -> $10.10); Flash-Lite 376 requests today (stop at 900); Flash 18/18 (judge). Actual
  spend $0; list price ~$1.61.

## 2026-11-08: agent gap cells, abstentions, tool-settings audit

- **Agent oracle on gap tasks.** `oracle --agent-gaps` runs the TinyFish agent (same 5-step cap)
  on tasks outside its oracle categories where no fetch tool has can=Y: j02, j03, j07. All three
  can=Y (j02 André Gide from /js-delayed/, j03 "life, navigation" from /scroll, j07 2011-12-12).
  j03's extraction verdict is judge-pending. 3 calls, 15 steps, ~$0.24 list. The config hash
  is unchanged (the gap set is a CLI selection, not config), so the cells merge into the same
  oracle table. Agent can-solve is now 12/13 (9/10 interactive + 3/3 gaps).
- **`abstained` outcome.** score() now returns `outcome`: correct | wrong | abstained | pending.
  Abstained = None or empty final text. `correct` stays False for it, so accuracy numbers don't
  move; abstentions are just reported apart from wrong answers. An explicit "I couldn't find it"
  is a non-empty answer and grades as wrong (deliberately narrow, no phrase matching). Old oracle
  rows get the outcome recomputed from their stored answer. In the oracle: 14 abstained cells
  (9 of them tinyfish_fetch), all with can=n. System prompt unchanged; oracle and free choice
  share it.
- **Tool-settings audit (docs read 2026-11-08). Neither tool ran on pure defaults.**
  - Firecrawl (docs.firecrawl.dev scrape): formats=[markdown], onlyMainContent=true, and
    timeout=60000 are all the defaults. **Non-default:** `parsers`. We send
    `[{type: pdf, maxPages: 10}]`, while the default is `[pdf, image]` with no page cap. Two effects: PDFs are
    capped at 10 pages (the same cap as plain fetch, chosen for credits), and the image parser is
    off. Measured impact on the oracle is none: every PDF task is can=Y on Firecrawl (the
    answers sit inside the first 10 pages, and six PDFs had more), and no task URL is an image. maxAge,
    proxy, waitFor, blockAds and the other options were left at their defaults.
  - TinyFish Fetch (docs.tinyfish.ai/fetch-api/reference): format=markdown is the default.
    **Non-default:** `per_url_timeout_ms=60000`; the default leaves it unset, which allows up to
    110 s. Impact is none: no Fetch call timed out, and every call took under 30 s.
    Fetch has no PDF page cap, so it read the full PDFs, while plain fetch and Firecrawl were
    capped at 10 pages.
    **Documented options we did not use:** `include_selectors`/`exclude_selectors` (CSS scoping;
    include bypasses automatic boilerplate removal, the only documented lever that might
    recover the collapsed or partial content, but it needs per-site selectors, so it is tuning,
    not a default); `ttl` (left unset, which accepts any cached copy, as Firecrawl's maxAge does;
    `ttl: 0` forces a live fetch, and a cached partial render can't be ruled out); format
    `html`/`json`; `links`/`image_links`/`iframe_links`; `purpose`; `highlights` (beta).
    The docs list no render, wait, scroll or max-length option.
  - The settings were NOT changed (reported for a decision). Free choice runs on the same settings
    as the oracle, so the two stay comparable. The "tools ran on documented defaults" line was
    not added, because it isn't true.

## 2026-11-08/09: free choice (Flash-Lite x3) run log

- Setup: all four tools offered in config order (plain, firecrawl, tf_fetch, tf_agent), neutral
  descriptions, the oracle's system prompt, max 3 tool calls. The judge is stored as pending when
  its quota is out, and re-judged on resume without calling any tool.
- First session died silently after 39 rows (17:41 UTC; exit 4, no traceback, the wrapping shell
  was killed too, so most likely the host session, not the bench). Resumed cleanly.
- **Flash-Lite's real free RPD is ~500, not 1,000.** Google returned a server-side 429 "exceeded your
  current quota" at ~498 requests on 2026-11-08 PT. config: rpd 500, stop at 95%. The run paused
  at 45/120 rows and resumes after the 08:00 UTC reset.
- Judge budget: 15 free-choice judge calls (5 judge tasks x 3) + 4 pending oracle cells = 19
  against the Flash stop at 18, so one verdict may roll to the following day.

- Free choice finished 2026-11-09 08:50 UTC: 120 rows, 14 judge-pending (the Flash judge quota ran
  out again right after the reset). The anon ablation (`--mode anon`, names tool_1..4, brand-free
  descriptions kept in code so the experiment hash is unchanged) finished at 40/40 with 5 judge-pending.
  Rerun both commands after the next reset to fill in the judge verdicts.
