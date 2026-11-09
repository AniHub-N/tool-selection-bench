# Free choice: flash-lite (20261108T172740Z_flash-lite_free)

120 rows (40 tasks x up to 3 reps). Status: paused.

## Outcomes

| category | correct | wrong | abstained | pending | n |
|---|---|---|---|---|---|
| static | 27 | 0 | 0 | 3 | 30 |
| js_heavy | 17 | 0 | 5 | 8 | 30 |
| pdf | 24 | 3 | 0 | 3 | 30 |
| interactive | 27 | 3 | 0 | 0 | 30 |
| **total** | 95 | 6 | 5 | 14 | 120 |

## First pick by category

| category | plain | firecrawl | tf_fetch | tf_agent | none |
|---|---|---|---|---|---|
| static | 30 | 0 | 0 | 0 | 0 |
| js_heavy | 24 | 3 | 3 | 0 | 0 |
| pdf | 30 | 0 | 0 | 0 | 0 |
| interactive | 21 | 3 | 3 | 3 | 0 |

All tool calls (any position): plain 130, firecrawl 27, tf_fetch 7, tf_agent 25

## Selection accuracy (first pick vs oracle `can`)

Y = first pick can solve the task, n = it can't, ? = no oracle verdict for that cell (excluded).

| category | Y | n | ? | accuracy (Y / (Y+n)) |
|---|---|---|---|---|
| static | 30 | 0 | 0 | 30/30 (100%) |
| js_heavy | 18 | 12 | 0 | 18/30 (60%) |
| pdf | 30 | 0 | 0 | 30/30 (100%) |
| interactive | 24 | 6 | 0 | 24/30 (80%) |
| total | 102 | 18 | 0 | 102/120 (85%) |

**Outcome by first-pick verdict**

| first pick | correct | wrong | abstained | pending |
|---|---|---|---|---|
| Y | 86 | 3 | 1 | 12 |
| n | 9 | 3 | 4 | 2 |

## Regret

Rows on feasible tasks (some tool can solve): 120. Not correct despite a can=Y tool: 11.

- after a can=n / unknown first pick (selection): 7: j02r0(abst), i09r0(wron), j02r1(abst), i09r1(wron), j02r2(abst), j03r2(abst), i09r2(wron)
- after a can=Y first pick (extraction, wrong page, gave up): 4: j05r0(abst), p02r0(wron), p02r1(wron), p02r2(wron)
- infeasible tasks (no tool can): -

## Consistency

Same first pick in all reps: 40/40 (100%) tasks.

## Cost and latency

- LLM requests 309, tokens in/out 847300/11699
- tool calls 189 (mean 1.57/row); Firecrawl credits 39; TinyFish agent 121 steps, $1.936
- tool time per row: median 1.9 s, max 51.5 s
- list price of the run: $2.414 (actual $0)
