# Free choice: flash-lite (20261109T085011Z_flash-lite_anon)

40 rows (40 tasks x up to 1 reps). Status: paused.

## Outcomes

| category | correct | wrong | abstained | pending | n |
|---|---|---|---|---|---|
| static | 9 | 0 | 0 | 1 | 10 |
| js_heavy | 5 | 1 | 1 | 3 | 10 |
| pdf | 9 | 0 | 0 | 1 | 10 |
| interactive | 8 | 1 | 1 | 0 | 10 |
| **total** | 31 | 2 | 2 | 5 | 40 |

## First pick by category

| category | plain | firecrawl | tf_fetch | tf_agent | none |
|---|---|---|---|---|---|
| static | 10 | 0 | 0 | 0 | 0 |
| js_heavy | 5 | 1 | 4 | 0 | 0 |
| pdf | 10 | 0 | 0 | 0 | 0 |
| interactive | 6 | 0 | 1 | 3 | 0 |

All tool calls (any position): plain 37, firecrawl 6, tf_fetch 7, tf_agent 13

## Selection accuracy (first pick vs oracle `can`)

Y = first pick can solve the task, n = it can't, ? = no oracle verdict for that cell (excluded).

| category | Y | n | ? | accuracy (Y / (Y+n)) |
|---|---|---|---|---|
| static | 10 | 0 | 0 | 10/10 (100%) |
| js_heavy | 5 | 5 | 0 | 5/10 (50%) |
| pdf | 10 | 0 | 0 | 10/10 (100%) |
| interactive | 8 | 2 | 0 | 8/10 (80%) |
| total | 33 | 7 | 0 | 33/40 (82%) |

**Outcome by first-pick verdict**

| first pick | correct | wrong | abstained | pending |
|---|---|---|---|---|
| Y | 29 | 0 | 1 | 3 |
| n | 2 | 2 | 1 | 2 |

## Regret

Rows on feasible tasks (some tool can solve): 40. Not correct despite a can=Y tool: 4.

- after a can=n / unknown first pick (selection): 3: j02r0(abst), j10r0(wron), i09r0(wron)
- after a can=Y first pick (extraction, wrong page, gave up): 1: i10r0(abst)
- infeasible tasks (no tool can): -

## Consistency

Same first pick in all reps: 40/40 (100%) tasks.

## Cost and latency

- LLM requests 103, tokens in/out 280600/3754
- tool calls 63 (mean 1.57/row); Firecrawl credits 10; TinyFish agent 56 steps, $0.896
- tool time per row: median 3.4 s, max 45.6 s
- list price of the run: $1.040 (actual $0)
