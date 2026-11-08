# Tasks (for ground-truth review)

Generated from tasks/tasks.jsonl. Edit tasks/build_tasks.py, not this file.

## static

| id | url | question | expected | check | verified via | memory risk |
|---|---|---|---|---|---|---|
| s01 | https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html | What is the UPC of this book? | **a897fe39b1053632** | contains | Product information table in the raw HTML | low |
| s02 | https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html | How many copies of this book are listed as available? | **20** | regex `\b20\b` | 'In stock (20 available)' in the raw HTML | low |
| s03 | https://www.scrapethissite.com/pages/simple/ | What population does this page list for Andorra? | **84000** | regex `\b84[,. ]?000\b` | Andorra entry in the raw HTML (first country listed) | low |
| s04 | https://www.sqlite.org/limits.html | For SQLite versions after 3.32.0, what is the default value of SQLITE_MAX_VARIABLE_NUMBER? | **32766** | regex `\b32[,. ]?766\b` | 'or 32766 for SQLite versions after 3.32.0' in the raw HTML | low |
| s05 | https://www.sqlite.org/fileformat2.html | In the SQLite database file header, what is stored in the 2-byte field at offset 16? | **The database page size in bytes** | judge | Database header table: offset 16, size 2, 'The database page size in bytes' | medium |
| s06 | https://www.postgresql.org/docs/16/datatype-numeric.html | In PostgreSQL 16, up to how many digits after the decimal point can a numeric value have? | **16383** | regex `\b16[,. ]?383\b` | Numeric types table | low |
| s07 | https://www.postgresql.org/docs/16/datatype-datetime.html | What is the high value (latest date) supported by the PostgreSQL 16 date type? | **5874897 AD** | regex `5[,. ]?874[,. ]?897` | Date/time types table | low |
| s08 | https://www.scrapethissite.com/pages/forms/ | According to this table, how many wins did the Boston Bruins have in 1990? | **44** | regex `\b44\b` | First row of the table on page 1 | low |
| s09 | https://httpbin.org/html | What is the name of the blacksmith in this passage? | **Perth** | contains | Passage text (a Moby-Dick excerpt) | medium |
| s10 | https://www.rfc-editor.org/rfc/rfc2324.html | Which request header does HTCPCP/1.0 define for specifying additions such as milk or syrup? | **Accept-Additions** | contains | Section 2.2.2.1 of the RFC | medium |

## js_heavy

| id | url | question | expected | check | verified via | memory risk |
|---|---|---|---|---|---|---|
| j01 | https://quotes.toscrape.com/js/ | What tags are attached to the Steve Martin quote on this page? | **humor, obvious, simile** | judge | Data array embedded in the page script (rendered by JS) | low |
| j02 | https://quotes.toscrape.com/js-delayed/ | Who is the author of the 7th quote on this page? | **André Gide** | regex `Andr[eé]\s+Gide` | Data array embedded in the page script; rendering is delayed | low |
| j03 | https://quotes.toscrape.com/scroll | What tags are attached to the Douglas Adams quote on this page? | **life, navigation** | judge | /api/quotes?page=2 (quote #16, loaded only on scroll) | low |
| j04 | https://www.scrapingcourse.com/javascript-rendering | What is the price of the Grayson Crewneck Sweatshirt on this page? | **$64** | regex `\$\s?64(\.00)?\b` | /ajax/products/json regular_price=64; products rendered by JS | low |
| j05 | https://www.scrapingcourse.com/infinite-scrolling | What is the price of the Kenobi Trail Jacket on this page? | **$47** | regex `\$\s?47(\.00)?\b` | /ajax/products?offset=12 (second batch, loaded on scroll) | low |
| j06 | https://datatables.net/examples/data_sources/ajax.html | What is Airi Satou's start date in this table? | **2008-11-28** | regex `2008[-/.]11[-/.]28\|28(th)?\s+Nov(ember)?,?\s+2008` | ../ajax/data/arrays.txt; table filled by AJAX | low |
| j07 | https://datatables.net/examples/server_side/simple.html | What is Cedric Kelly's salary in this table? | **$433,060** | regex `433[,. ]?060` | server_processing.php response; Cedric Kelly is in the first 10 rows (sorted by first name) | low |
| j08 | https://petstore.swagger.io/ | According to the API description on this page, what API key can you use to test the authorization filters? | **special-key** | contains | /v2/swagger.json info.description; page rendered by Swagger UI | medium |
| j09 | https://petstore.swagger.io/ | How many operations are listed under the 'store' tag on this page? | **4** | judge | /v2/swagger.json: 4 operations tagged store | medium |
| j10 | https://quotes.toscrape.com/js/page/2/ | Who is the author of the last quote on this page? | **Allen Saunders** | contains | Data array embedded in the /js/page/2/ script | low |

## pdf

| id | url | question | expected | check | verified via | memory risk |
|---|---|---|---|---|---|---|
| p01 | https://bitcoin.org/bitcoin.pdf | In the paper's table of attacker success probabilities for q=0.1, what is P when z=5? | **0.0009137** | contains | pypdf page 8 | low |
| p02 | https://arxiv.org/pdf/1706.03762v7 | According to Table 2, what is the training cost in FLOPs of the Transformer (big) model on EN-DE? | **2.3 x 10^19** | regex `2\.3\s*(?:[·x×*⋅]\|\\cdot\|\\times)?\s*10\s*\^?\s*\{?19\}?\|2\.3\s*e\+?19` | pypdf page 8 (superscript flattened to '1019') | low |
| p03 | https://arxiv.org/pdf/1810.04805v2 | In Table 1, what score did BERT-LARGE get on CoLA? | **60.5** | contains | pypdf page 6 | medium |
| p04 | https://arxiv.org/pdf/1512.03385v1 | In Table 4, what is the single-model top-5 error rate of ResNet-152? | **4.49** | contains | pypdf page 6 | medium |
| p05 | https://arxiv.org/pdf/1505.04597v1 | What IOU did u-net achieve on the PhC-U373 dataset? | **0.9203** | contains | pypdf page 7 (segmentation results table) | low |
| p06 | https://arxiv.org/pdf/1406.2661v1 | In Table 1, what Parzen window-based log-likelihood estimate did adversarial nets get on MNIST? | **225 ± 2** | regex `\b225\b` | pypdf page 6 | low |
| p07 | https://www.cs.utexas.edu/~EWD/ewd02xx/EWD215.PDF | According to this manuscript, in which city was the pre-ALGOL meeting where Heinz Zemanek expressed doubts about the go to statement? | **Copenhagen** | contains | Scanned pages; confirmed from the archive's transcription (EWD215.html, last paragraph) | low |
| p08 | https://www.bls.gov/news.release/archives/cpi_01112024.pdf | By what percentage did the food at home index rise over the 12 months to December 2023? | **1.3 percent** | regex `\b1\.3\s*(%\|percent)` | pypdf page 3 | low |
| p09 | https://arxiv.org/pdf/1502.03167v3 | What maximum accuracy did the BN-x30 model reach, per the table in Figure 3? | **74.8%** | contains | pypdf page 7 | low |
| p10 | https://arxiv.org/pdf/1301.3781v3 | In Table 3, which architecture had the highest syntactic accuracy? | **CBOW** | judge | pypdf page 7: CBOW 64 vs Skip-gram 59, NNLM 53, RNNLM 36 | low |

## interactive

| id | url | question | expected | check | verified via | memory risk |
|---|---|---|---|---|---|---|
| i01 | https://www.scrapingcourse.com/button-click | What is the price of the Lando Gym Jacket on this page? | **$99** | regex `\$\s?99(\.00)?\b` | /ajax/products?offset=12; loaded only after clicking 'Load More' | low |
| i02 | https://www.scrapethissite.com/pages/ajax-javascript/ | Which 2010 film on this page had 3 nominations and 2 awards? | **Alice in Wonderland** | contains | ?ajax=true&year=2010; data appears after clicking the year | medium |
| i03 | https://www.scrapethissite.com/pages/forms/ | Using the team search, how many goals for (GF) did the Toronto Maple Leafs have in 1995? | **247** | regex `\b247\b` | ?q=Toronto results: 1995 row W34 L36 GF247 GA252 | low |
| i04 | https://quotes.toscrape.com/ | Who is the author of the first quote on page 4 of this site? | **Dr. Seuss** | contains | /page/4/ | low |
| i05 | https://books.toscrape.com/ | How many books are in the Nonfiction category on this site? | **110** | regex `\b110\b` | Nonfiction category page: '110 results' | low |
| i06 | https://books.toscrape.com/ | What is the UPC of the book 'Sharp Objects' on this site? | **e00eb4fd7b871a48** | contains | Sharp Objects product page | low |
| i07 | https://datatables.net/examples/api/row_details.html | What is Ashton Cox's extension number? It is shown when you expand his row. | **1562** | regex `\b1562\b` | ../ajax/data/objects.txt; the extension is only shown in the expanded child row | low |
| i08 | https://datatables.net/examples/data_sources/ajax.html | What is Zorita Serrano's start date in this table? | **2012-06-01** | regex `2012[-/.]0?6[-/.]0?1\b\|1(st)?\s+Jun(e)?,?\s+2012` | arrays.txt; she is the last of 57 rows, so you must page or search | low |
| i09 | https://quotes.toscrape.com/ | How many quotes on this site are tagged 'humor' in total? | **12** | regex `\b12\b\|\btwelve\b` | /api/quotes, all pages: 12. The tag page shows 10 on page 1 and 2 on page 2. | low |
| i10 | https://www.scrapingcourse.com/pagination | What is the price of the Geo Insulated Jogging Pant on this site? | **$51** | regex `\$\s?51(\.00)?\b` | /pagination/3 | low |
