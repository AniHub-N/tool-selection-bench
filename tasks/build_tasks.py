"""Source of truth for tasks/tasks.jsonl. Edit here, then: python tasks/build_tasks.py

Each task: id, category, url, question, expected_answer, check_type (+ pattern for regex).
Extra fields: verified_via (how the ground truth was confirmed, so it can be re-checked),
memory_risk (could a model answer without fetching?), note.

Rules applied to every task (see NOTES.md):
- The expected answer is distinctive, so a wrong or hedging answer can't match by accident.
  Where the natural answer is a small number, the question asks for something else
  (e.g. i02 asks for a film title instead of a count) or the check is `judge`.
- The answer is within the first ~15k chars of extracted content (agent truncates at 20k)
  and, for PDFs, within the first 10 pages (pdf_max_pages).
- Prefer facts a model can't know from memory (product codes, table values, fixture data).
- Stable sources only: scraping sandboxes, versioned docs, immutable RFCs, versioned arXiv
  PDFs, archived government releases.
"""

import json
from collections import Counter
from pathlib import Path

T: list[dict] = []


def t(id, cat, url, q, exp, check, pattern=None, via="", mem="low", note=""):
    d = {"id": id, "category": cat, "url": url, "question": q, "expected_answer": exp, "check_type": check}
    if pattern:
        d["pattern"] = pattern
    d["verified_via"] = via
    d["memory_risk"] = mem
    if note:
        d["note"] = note
    T.append(d)


B = "https://books.toscrape.com"
Q = "https://quotes.toscrape.com"
SC = "https://www.scrapingcourse.com"
DT = "https://datatables.net/examples"

# ---- static: plain HTML -----------------------------------------------------
t("s01", "static", f"{B}/catalogue/a-light-in-the-attic_1000/index.html",
  "What is the UPC of this book?", "a897fe39b1053632", "contains",
  via="Product information table in the raw HTML")
t("s02", "static", f"{B}/catalogue/tipping-the-velvet_999/index.html",
  "How many copies of this book are listed as available?", "20", "regex", r"\b20\b",
  via="'In stock (20 available)' in the raw HTML")
t("s03", "static", "https://www.scrapethissite.com/pages/simple/",
  "What population does this page list for Anguilla?", "13254", "regex", r"\b13[,. ]?254\b",
  via="Anguilla entry in the raw HTML (char ~985 of extracted text)",
  note="The answer is what the page says, not today's real population.")
t("s04", "static", "https://www.sqlite.org/limits.html",
  "For SQLite versions after 3.32.0, what is the default value of SQLITE_MAX_VARIABLE_NUMBER?",
  "32766", "regex", r"\b32[,. ]?766\b",
  via="'or 32766 for SQLite versions after 3.32.0' in the raw HTML")
t("s05", "static", "https://www.sqlite.org/fileformat2.html",
  "In the SQLite database file header, what is stored in the 2-byte field at offset 16?",
  "The database page size in bytes", "judge",
  via="Database header table: offset 16, size 2, 'The database page size in bytes'", mem="medium")
t("s06", "static", "https://www.postgresql.org/docs/16/datatype-numeric.html",
  "In PostgreSQL 16, what range of values is permitted for the scale in a numeric type declaration?",
  "-1000 to 1000", "regex", r"-\s?1,?000\s*(?:to|and|through|–|-|\.\.)\s*\+?1,?000",
  via="Note in section 8.1.2: 'permits the scale ... to be any value in the range -1000 to 1000'")
t("s07", "static", "https://www.postgresql.org/docs/16/datatype-datetime.html",
  "What is the high value (latest date) supported by the PostgreSQL 16 date type?",
  "5874897 AD", "regex", r"5[,. ]?874[,. ]?897", via="Date/time types table")
t("s08", "static", "https://www.scrapethissite.com/pages/forms/",
  "According to this table, how many wins did the Boston Bruins have in 1990?",
  "44", "regex", r"\b44\b", via="First row of the table on page 1")
t("s09", "static", "https://httpbin.org/html",
  "What is the name of the blacksmith in this passage?", "Perth", "contains",
  via="Passage text (a Moby-Dick excerpt)", mem="medium", note="Moby-Dick readers may know this from memory.")
t("s10", "static", "https://www.rfc-editor.org/rfc/rfc2324.html",
  "Which request header does HTCPCP/1.0 define for specifying additions such as milk or syrup?",
  "Accept-Additions", "contains", via="Section 2.2.2.1 of the RFC", mem="medium")

# ---- js_heavy: content only after JS runs -------------------------------------
t("j01", "js_heavy", f"{Q}/js/",
  "What tags are attached to the Jane Austen quote on this page?", "aliteracy, books, classic, humor", "judge",
  via="Data array embedded in the page script (rendered by JS)")
t("j02", "js_heavy", f"{Q}/js-delayed/",
  "Who is the author of the 7th quote on this page?", "André Gide", "regex", r"Andr[eé]\s+Gide",
  via="Data array embedded in the page script; rendering is delayed",
  note="Content renders after a delay; tests whether tools wait.")
t("j03", "js_heavy", f"{Q}/scroll",
  "What tags are attached to the Douglas Adams quote on this page?", "life, navigation", "judge",
  via="/api/quotes?page=2 (quote #16, loaded only on scroll)")
t("j04", "js_heavy", f"{SC}/javascript-rendering",
  "What is the price of the Grayson Crewneck Sweatshirt on this page?", "$64", "regex", r"\$\s?64(\.00)?\b",
  via="/ajax/products/json regular_price=64; products rendered by JS")
t("j05", "js_heavy", f"{SC}/infinite-scrolling",
  "What is the price of the Kenobi Trail Jacket on this page?", "$47", "regex", r"\$\s?47(\.00)?\b",
  via="/ajax/products?offset=12 (second batch, loaded on scroll)")
t("j06", "js_heavy", f"{DT}/data_sources/ajax.html",
  "What is Airi Satou's start date in this table?", "2008-11-28", "regex",
  r"2008[-/.]11[-/.]28|28(th)?\s+Nov(ember)?,?\s+2008", via="../ajax/data/arrays.txt; table filled by AJAX")
t("j07", "js_heavy", f"{DT}/server_side/simple.html",
  "What is Caesar Vance's start date in this table?", "12th Dec 11 (2011-12-12)", "regex",
  r"12(?:th)?\s+Dec(?:ember)?,?\s+(?:20)?11\b|2011[-/.]12[-/.]12",
  via="server_processing.php response (rendered as '12th Dec 11'); row 8 of the first page sorted by first name")
t("j08", "js_heavy", "https://petstore.swagger.io/",
  "According to the API description on this page, what API key can you use to test the authorization filters?",
  "special-key", "contains", via="/v2/swagger.json info.description; page rendered by Swagger UI", mem="medium")
t("j09", "js_heavy", "https://petstore.swagger.io/",
  "How many operations are listed under the 'store' tag on this page?", "4", "judge",
  via="/v2/swagger.json: 4 operations tagged store", mem="medium")
t("j10", "js_heavy", f"{Q}/js/page/2/",
  "Who is the author of the last quote on this page?", "Allen Saunders", "contains",
  via="Data array embedded in the /js/page/2/ script")

# ---- pdf: answer inside a PDF -------------------------------------------------
t("p01", "pdf", "https://bitcoin.org/bitcoin.pdf",
  "In the paper's table of attacker success probabilities for q=0.1, what is P when z=5?",
  "0.0009137", "contains", via="pypdf page 8")
t("p02", "pdf", "https://arxiv.org/pdf/1706.03762v7",
  "According to Table 2, what is the training cost in FLOPs of the Transformer (big) model on EN-DE?",
  "2.3 x 10^19", "regex", r"2\.3\s*(?:[·x×*⋅]|\\cdot|\\times)?\s*10\s*\^?\s*\{?19\}?|2\.3\s*e\+?19",
  via="pypdf page 8 (superscript flattened to '1019')")
t("p03", "pdf", "https://arxiv.org/pdf/1810.04805v2",
  "In Table 1, what score did BERT-LARGE get on CoLA?", "60.5", "contains", via="pypdf page 6", mem="medium")
t("p04", "pdf", "https://arxiv.org/pdf/1512.03385v1",
  "In Table 2, what top-1 error (10-crop testing) did the 34-layer plain network get?", "28.54", "contains",
  via="pypdf page 5 (Table 2: plain 34 layers 28.54, ResNet 25.03)")
t("p05", "pdf", "https://arxiv.org/pdf/1505.04597v1",
  "What IOU did u-net achieve on the PhC-U373 dataset?", "0.9203", "contains",
  via="pypdf page 7 (segmentation results table)")
t("p06", "pdf", "https://arxiv.org/pdf/1406.2661v1",
  "In Table 1, what Parzen window-based log-likelihood estimate did adversarial nets get on MNIST?",
  "225 ± 2", "regex", r"\b225\b", via="pypdf page 6")
t("p07", "pdf", "https://www.cs.utexas.edu/~EWD/ewd02xx/EWD215.PDF",
  "According to this manuscript, in which city was the pre-ALGOL meeting where Heinz Zemanek "
  "expressed doubts about the go to statement?", "Copenhagen", "contains",
  via="Scanned pages; confirmed from the archive's transcription (EWD215.html, last paragraph)",
  note="Page 1 is a typed copyright notice (the only text layer pypdf finds, 241 chars). The answer "
       "is only in the scanned pages, so OCR is needed. The title was rejected: it's on the typed page.")
t("p08", "pdf", "https://www.bls.gov/news.release/archives/cpi_01112024.pdf",
  "By what percentage did the motor vehicle insurance index increase in December 2023 (month over month)?",
  "1.5 percent", "regex", r"\b1\.5\s*(%|percent)",
  via="pypdf page 4: 'The motor vehicle insurance index increased 1.5 percent in December'")
t("p09", "pdf", "https://arxiv.org/pdf/1502.03167v3",
  "Per the table in Figure 3, how many training steps did BN-x5 need to reach Inception's maximum accuracy (72.2%)?",
  "2.1 x 10^6", "regex",
  r"2\.1\s*(?:[·x×*⋅]|\\cdot|\\times)?\s*10\s*\^?\s*\{?6\}?|2\.1\s*(?:million|M\b)|2,100,000",
  via="pypdf page 7 (superscript flattened to '106')")
t("p10", "pdf", "https://arxiv.org/pdf/1301.3781v3",
  "In Table 3, which architecture had the highest syntactic accuracy?", "CBOW", "judge",
  via="pypdf page 7: CBOW 64 vs Skip-gram 59, NNLM 53, RNNLM 36")

# ---- interactive: needs a click, search, or pagination ------------------------
t("i01", "interactive", f"{SC}/button-click",
  "What is the price of the Lando Gym Jacket on this page?", "$99", "regex", r"\$\s?99(\.00)?\b",
  via="/ajax/products?offset=12; loaded only after clicking 'Load More'")
t("i02", "interactive", "https://www.scrapethissite.com/pages/ajax-javascript/",
  "Which 2010 film on this page had 3 nominations and 2 awards?", "Alice in Wonderland", "contains",
  via="?ajax=true&year=2010; data appears after clicking the year", mem="medium")
t("i03", "interactive", "https://www.scrapethissite.com/pages/forms/",
  "Using the team search, how many goals for (GF) did the Toronto Maple Leafs have in 1995?",
  "247", "regex", r"\b247\b", via="?q=Toronto results: 1995 row W34 L36 GF247 GA252",
  note="GA (252) was rejected: 252 also appears on page 1 (Philadelphia 1990 GF).")
t("i04", "interactive", f"{Q}/",
  "Who is the author of the first quote on page 4 of this site?", "Dr. Seuss", "contains", via="/page/4/")
t("i05", "interactive", f"{B}/",
  "How many books are in the Nonfiction category on this site?", "110", "regex", r"\b110\b",
  via="Nonfiction category page: '110 results'",
  note="Poetry (19) was rejected: '19' appears on the home page as an item number.")
t("i06", "interactive", f"{B}/",
  "What is the UPC of the book 'Sharp Objects' on this site?", "e00eb4fd7b871a48", "contains",
  via="Sharp Objects product page")
t("i07", "interactive", f"{DT}/api/row_details.html",
  "What is Zenaida Frank's extension number? It is shown when you expand her row.", "7439", "regex", r"\b7439\b",
  via="../ajax/data/objects.txt; row 56 of 57 (needs paging or search), only shown in the expanded child row")
t("i08", "interactive", f"{DT}/data_sources/ajax.html",
  "What is Zorita Serrano's start date in this table?", "2012-06-01", "regex",
  r"2012[-/.]0?6[-/.]0?1\b|1(st)?\s+Jun(e)?,?\s+2012",
  via="arrays.txt; she is the last of 57 rows, so you must page or search")
t("i09", "interactive", f"{Q}/",
  "How many quotes on this site are tagged 'humor' in total?", "12", "regex", r"\b12\b|\btwelve\b",
  via="/api/quotes, all pages: 12. The tag page shows 10 on page 1 and 2 on page 2.")
t("i10", "interactive", f"{SC}/pagination",
  "What is the price of the Geo Insulated Jogging Pant on this site?", "$51", "regex", r"\$\s?51(\.00)?\b",
  via="/pagination/3")

# ---- interactive: is the answer reachable by constructing/following a URL, without clicking?
URL_NAVIGABLE = {
    "i01": (False, "Load More button fires an XHR; no URL for the second batch is visible on the page."),
    "i02": (False, "Year links are href='#'; the ?ajax=true&year= endpoint only appears in external JS."),
    "i03": (True, "Search is a GET form: ?q=Toronto."),
    "i04": (True, "Pagination links: /page/4/."),
    "i05": (True, "Category links on the home page."),
    "i06": (True, "Product links on the home page."),
    "i07": (True, "The page prints its example code, including ajax: '../ajax/data/objects.txt', which "
                  "contains the extension. Reachable by URL even though the UI expects a click."),
    "i08": (True, "The page prints ajax: '../ajax/data/arrays.txt' (all 57 rows) in its code listing."),
    "i09": (True, "Tag pages: /tag/humor/ and /tag/humor/page/2/."),
    "i10": (True, "Page 1 links to /pagination/2, /3 and /13."),
}
# Memorized in no_tools (Flash-Lite, 2026-11-08) -> question rewritten to a less-cited detail on the same page.
REWRITTEN_FROM = {
    "s03": "What population does this page list for Andorra? -> 84000",
    "s06": "In PostgreSQL 16, up to how many digits after the decimal point can a numeric value have? -> 16383",
    "j01": "What tags are attached to the Steve Martin quote on this page? -> humor, obvious, simile",
    "j07": "What is Cedric Kelly's salary in this table? -> $433,060",
    "p04": "In Table 4, what is the single-model top-5 error rate of ResNet-152? -> 4.49",
    "p08": "By what percentage did the food at home index rise over the 12 months to December 2023? -> 1.3 percent",
    "p09": "What maximum accuracy did the BN-x30 model reach, per the table in Figure 3? -> 74.8%",
    "i07": "What is Ashton Cox's extension number? -> 1562",
}
# Memorized in no_tools but deliberately kept unchanged: does the agent still call tools
# when it could answer from memory?
KNOWN_ANSWER = {"s04", "j08", "p03", "i08"}

ANALYSIS_FLAGS = {
    "i10": ("tight_call_budget",
            "The answer is on /pagination/3. A page-by-page scan (1, 2, 3) uses exactly the 3-call cap; jumping "
            "straight from page 1 to the /3 link takes 2. No margin, so report it separately."),
}


def apply_annotations(tasks: list[dict]) -> None:
    for d in tasks:
        if d["id"] in URL_NAVIGABLE:
            d["url_navigable"], d["url_route"] = URL_NAVIGABLE[d["id"]]
        if d["id"] in REWRITTEN_FROM:
            d["rewritten_from"] = REWRITTEN_FROM[d["id"]]
        d["known_answer"] = d["id"] in KNOWN_ANSWER
        if d["id"] in ANALYSIS_FLAGS:
            d["analysis_flag"], d["analysis_note"] = ANALYSIS_FLAGS[d["id"]]
    # memorized.json is written by `python -m bench memorized` from the no_tools runs.
    mem_path = Path(__file__).with_name("memorized.json")
    if mem_path.exists():
        mem = json.loads(mem_path.read_text(encoding="utf-8"))
        for d in tasks:
            m = mem.get(d["id"], {"memorized": False, "models": [], "checked": []})
            d["memorized"], d["memorized_by"] = m["memorized"], m["models"]
            d["memorized_checked_on"] = m.get("checked", [])


if __name__ == "__main__":
    apply_annotations(T)
    out = Path(__file__).with_name("tasks.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for d in T:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(len(T), dict(Counter(d["category"] for d in T)), dict(Counter(d["check_type"] for d in T)))
