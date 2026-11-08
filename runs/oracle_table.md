| task | cat | plain | flags |
|---|---|---|---|
| s01 | static | PASS |  |
| s02 | static | - |  |
| s03 | static | - |  |
| s04 | static | - | memorized, known_answer |
| s05 | static | - |  |
| s06 | static | - |  |
| s07 | static | - |  |
| s08 | static | - |  |
| s09 | static | - |  |
| s10 | static | - |  |
| j01 | js_heavy | - |  |
| j02 | js_heavy | - |  |
| j03 | js_heavy | - |  |
| j04 | js_heavy | - |  |
| j05 | js_heavy | - |  |
| j06 | js_heavy | - | memorized |
| j07 | js_heavy | - | memorized |
| j08 | js_heavy | - | memorized, known_answer |
| j09 | js_heavy | - |  |
| j10 | js_heavy | - |  |
| p01 | pdf | - |  |
| p02 | pdf | - |  |
| p03 | pdf | - | memorized, known_answer |
| p04 | pdf | - |  |
| p05 | pdf | - |  |
| p06 | pdf | - |  |
| p07 | pdf | - |  |
| p08 | pdf | - | memorized |
| p09 | pdf | - |  |
| p10 | pdf | - |  |
| i01 | interactive | - | not_url_nav |
| i02 | interactive | - | not_url_nav |
| i03 | interactive | - | url_nav |
| i04 | interactive | - | url_nav |
| i05 | interactive | - | url_nav |
| i06 | interactive | - | url_nav |
| i07 | interactive | - | url_nav |
| i08 | interactive | - | memorized, known_answer, url_nav |
| i09 | interactive | - | url_nav |
| i10 | interactive | - | tight_call_budget, url_nav |

B = bot/challenge page seen, E = a tool call errored, F? = format_suspect (hand-review), R = answer was in the raw tool output but the final answer missed it (noisy for small numbers).

| tool | static | js_heavy | pdf | interactive | total |
|---|---|---|---|---|---|
| plain | 1/1 | - | - | - | 1/1 |

| tool | tool calls | errors | blocked | firecrawl credits | agent steps | agent USD | median latency s |
|---|---|---|---|---|---|---|---|
| plain | 1 | 0 | 0 | - | - | - | 1.0 |

LLM requests (agent, excl. judge): 2; tokens in/out: 706/77
