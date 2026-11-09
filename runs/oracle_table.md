Each cell: **can** (oracle: answer present in the raw tool output; Y / n / ? = presence can't decide) then **ext** (Flash-Lite's final answer: PASS / fail = wrong answer / abst = abstained, no answer at all / pend = judge pending).

| task | cat | plain | firecrawl | tf_fetch | tf_agent | flags |
|---|---|---|---|---|---|---|
| s01 | static | Y PASS | Y PASS | Y PASS | - |  |
| s02 | static | Y PASS | Y PASS | Y PASS | - |  |
| s03 | static | Y PASS | Y PASS | Y PASS | - |  |
| s04 | static | Y PASS | Y PASS | Y PASS | - | memorized, known_answer |
| s05 | static | Y PASS T | Y PASS T | Y PASS T | - |  |
| s06 | static | Y PASS | Y PASS | Y PASS | - |  |
| s07 | static | Y PASS | Y PASS | Y PASS | - |  |
| s08 | static | Y PASS | Y PASS | Y PASS | - |  |
| s09 | static | Y PASS | Y PASS | Y PASS | - |  |
| s10 | static | Y PASS | Y PASS | Y PASS | - |  |
| j01 | js_heavy | Y PASS | Y PASS | n abst | - |  |
| j02 | js_heavy | n PASS | n PASS | n PASS | Y PASS |  |
| j03 | js_heavy | n abst | n abst | n abst | Y pend |  |
| j04 | js_heavy | n abst E | Y PASS | Y PASS | - |  |
| j05 | js_heavy | Y PASS | Y PASS | n abst | - |  |
| j06 | js_heavy | Y PASS | Y PASS | Y PASS | - | memorized |
| j07 | js_heavy | n PASS | n PASS | n PASS | Y PASS | memorized |
| j08 | js_heavy | Y PASS E | Y PASS | Y PASS | - | memorized, known_answer |
| j09 | js_heavy | Y PASS H E | Y PASS H | Y PASS H | - | aggregate |
| j10 | js_heavy | n fail | Y PASS | n fail | - |  |
| p01 | pdf | Y PASS | Y PASS | Y PASS | - |  |
| p02 | pdf | Y PASS | Y PASS | Y PASS | - |  |
| p03 | pdf | Y PASS | Y PASS | Y PASS T | - | memorized, known_answer |
| p04 | pdf | Y PASS | Y PASS T | Y PASS T | - |  |
| p05 | pdf | Y PASS | Y PASS | Y PASS | - |  |
| p06 | pdf | Y PASS | Y PASS | Y PASS | - |  |
| p07 | pdf | Y PASS E | Y PASS | Y PASS | - |  |
| p08 | pdf | Y PASS | Y PASS | Y PASS E T | - | memorized |
| p09 | pdf | Y PASS | Y PASS T | Y PASS | - |  |
| p10 | pdf | Y pend | Y pend | Y PASS | - |  |
| i01 | interactive | Y PASS | Y PASS | n abst | Y PASS | not_url_nav |
| i02 | interactive | n abst E | n abst E | n abst E | Y PASS | not_url_nav |
| i03 | interactive | Y PASS | Y PASS | Y PASS | Y PASS | url_nav |
| i04 | interactive | Y PASS | Y PASS | Y PASS | Y PASS | url_nav |
| i05 | interactive | Y PASS | Y PASS | n abst | Y PASS | url_nav |
| i06 | interactive | Y PASS | Y PASS | n abst E | Y PASS | url_nav |
| i07 | interactive | Y PASS | Y PASS | Y PASS | Y PASS | url_nav |
| i08 | interactive | Y PASS | Y PASS | n abst E | Y PASS | memorized, known_answer, url_nav |
| i09 | interactive | n fail H | Y PASS H | Y fail H | n fail | aggregate, url_nav |
| i10 | interactive | Y PASS | Y PASS | n abst | Y PASS | tight_call_budget, url_nav |

Flags: H = `can` hand-labeled from the saved raw outputs (tasks/oracle_hand_labels.json), B = bot/challenge page seen, E = a tool call errored, T = output truncated to max_content_chars before the model saw it, F? = format_suspect (hand-review).

**can solve (oracle)**

| tool | static | js_heavy | pdf | interactive | total |
|---|---|---|---|---|---|
| plain | 10/10 | 5/10 | 10/10 | 8/10 | 33/40 |
| firecrawl | 10/10 | 7/10 | 10/10 | 9/10 | 36/40 |
| tf_fetch | 10/10 | 4/10 | 10/10 | 4/10 | 28/40 |
| tf_agent | - | 3/3 | - | 9/10 | 12/13 |

**Flash-Lite extraction pass**

| tool | static | js_heavy | pdf | interactive | total |
|---|---|---|---|---|---|
| plain | 10/10 | 7/10 | 9/9 (+1?) | 8/10 | 34/39 (+1?) |
| firecrawl | 10/10 | 9/10 | 9/9 (+1?) | 9/10 | 37/39 (+1?) |
| tf_fetch | 10/10 | 6/10 | 10/10 | 3/10 | 29/40 |
| tf_agent | - | 2/2 (+1?) | - | 9/10 | 11/12 (+1?) |

**Extraction misses** (tool delivered the answer, Flash-Lite still failed): i09/tf_fetch
**Abstained** (empty final answer; not correct, reported apart from wrong): j01/tf_fetch, j03/plain, j03/firecrawl, j03/tf_fetch, j04/plain, j05/tf_fetch, i01/tf_fetch, i02/plain, i02/firecrawl, i02/tf_fetch, i05/tf_fetch, i06/tf_fetch, i08/tf_fetch, i10/tf_fetch
**Answered without evidence** (can=n but ext=PASS: memory or guess): j02/plain, j02/firecrawl, j02/tf_fetch, j07/plain, j07/firecrawl, j07/tf_fetch

| tool | tool calls | errors | blocked | firecrawl credits | agent steps | agent USD | median latency s |
|---|---|---|---|---|---|---|---|
| plain | 66 | 5 | 0 | - | - | - | 1.0 |
| firecrawl | 56 | 1 | 0 | 137.0 | - | - | 1.3 |
| tf_fetch | 72 | 5 | 0 | - | - | - | 1.3 |
| tf_agent | 15 | 0 | 0 | - | 68 | $1.088 | 23.6 |

LLM requests (agent, excl. judge): 342; tokens in/out: 824635/12208
