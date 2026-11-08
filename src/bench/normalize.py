"""Format-tolerant value matching, so a correct answer written in another format is not
scored as a failure (which would then be misread as a tool failure).

A task may carry `value`, one of:
  {"kind": "number", "value": 2.1e6, "also": [...]}   any listed value counts
  {"kind": "date",   "value": "2011-12-12"}
  {"kind": "range",  "lo": -1000, "hi": 1000}

Numbers: thousands separators (2,100,000 / 2 100 000), scientific notation (2.1e6,
2.1×10^6, 2.1 x 10⁶, 2.1·10^{6}, LaTeX \\times), magnitude words (2.1 million, 2.1M),
small number words (twelve), $ and % stripped, Unicode minus. Matching is exact up to
float rounding (relative 1e-9): this is about format, not precision.
Dates: ISO, d/m/y or m/d/y (either order accepted; both readings are the same day only
when the answer means it), and month-name forms (12th Dec 11, Dec 12, 2011, 12-Dec-2011).
Ranges: both endpoints must appear, with the lower one negative if the reference is.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from typing import Any

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_MAG = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
        "trillion": 1e12}
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

_SIGN = r"(?:(?<![\w.])[-+])?"
_MANT = r"\d+(?:[,   ]\d{3})+(?:\.\d+)?|\d*\.\d+|\d+"
_TIMES = r"(?:[x×*·⋅∙]|\\times|\\cdot)"
_NUM_RE = re.compile(
    rf"(?P<sign>{_SIGN})(?P<mant>{_MANT})"
    rf"(?:\s*{_TIMES}\s*10\s*(?:\^|\*\*)?\s*\{{?\s*(?P<exp1>[-+]?\d+)\s*\}}?"   # 2.1 × 10^6
    rf"|[eE](?P<exp2>[-+]?\d+)\b"                                               # 2.1e6
    rf"|\s*(?P<mag>thousand|million|billion|trillion|bn|mn|[kKmMbB])\b)?",     # 2.1 million
    re.I)
_POW10_RE = re.compile(r"(?<![\d.])10\s*(?:\^|\*\*)\s*\{?\s*(?P<exp>[-+]?\d+)\s*\}?")


def _clean(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(_SUP).replace("−", "-")
    t = re.sub(r"(?<![\w.])[–—](?=\d)", "-", t)     # "–1000": a dash attached to a number is a minus
    t = re.sub(r"[–—]", " to ", t)                   # otherwise it separates a range
    return re.sub(r"\^\s*\(\s*([-+]?\d+)\s*\)", r"^\1", t)  # 10^(6) -> 10^6


def numbers(text: str) -> list[float]:
    """Every number written in `text`, normalised to a float."""
    # NFKC turns ⁶ into 6, so "2.1 × 10⁶" became "2.1 × 106": restore the exponent from the original.
    raw = unicodedata.normalize("NFC", text or "").replace("−", "-")
    raw = re.sub(r"(?<=\d)[   ](?=\d{3}\b)", ",", raw)  # 13 254 (thin space) -> 13,254
    raw = re.sub(r"10([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)", lambda m: "10^" + m.group(1).translate(_SUP), raw)
    t = _clean(raw)
    out: list[float] = []
    for m in _NUM_RE.finditer(t):
        mant = re.sub(r"[,   ]", "", m.group("mant"))
        try:
            v = float(mant)
        except ValueError:
            continue
        exp = m.group("exp1") or m.group("exp2")
        if exp is not None:
            v *= 10.0 ** int(exp)
        elif m.group("mag"):
            v *= _MAG[m.group("mag").lower()]
        if m.group("sign") == "-":
            v = -v
        out.append(v)
    for m in _POW10_RE.finditer(t):           # a bare "10^19"
        if not re.search(rf"{_TIMES}\s*$", t[:m.start()]):
            out.append(10.0 ** int(m.group("exp")))
    for w, n in _WORDS.items():
        if re.search(rf"\b{w}\b", t, re.I):
            out.append(float(n))
    return out


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def _year(y: str) -> int:
    n = int(y)
    return n if len(y) == 4 else (2000 + n if n < 50 else 1900 + n)


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates(text: str) -> list[date]:
    """Every date written in `text`."""
    t = unicodedata.normalize("NFKC", text or "")
    out: list[date | None] = []
    for m in re.finditer(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", t):
        out.append(_mk(int(m[1]), int(m[2]), int(m[3])))
    for m in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b", t):
        y, a, b = _year(m[3]), int(m[1]), int(m[2])
        out += [_mk(y, b, a), _mk(y, a, b)]  # d/m/y and m/d/y
    mon = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    day = r"(\d{1,2})(?:st|nd|rd|th)?"
    yr = r"('?\d{4}|'?\d{2})\b"
    for m in re.finditer(rf"\b{day}(?:\s+|-)(?:of\s+)?{mon},?(?:\s+|-){yr}", t, re.I):
        out.append(_mk(_year(m[3].lstrip("'")), _MONTHS[m[2].lower()], int(m[1])))
    for m in re.finditer(rf"\b{mon}\s+{day},?\s+{yr}", t, re.I):
        out.append(_mk(_year(m[3].lstrip("'")), _MONTHS[m[1].lower()], int(m[2])))
    return [d for d in out if d]


def value_match(spec: dict[str, Any], answer: str | None) -> bool:
    a = answer or ""
    kind = spec["kind"]
    if kind == "number":
        want = [spec["value"], *spec.get("also", [])]
        return any(_close(n, w) for n in numbers(a) for w in want)
    if kind == "date":
        return date.fromisoformat(spec["value"]) in dates(a)
    if kind == "range":
        ns = numbers(a)
        return any(_close(n, spec["lo"]) for n in ns) and any(_close(n, spec["hi"]) for n in ns)
    raise ValueError(f"unknown value kind {kind}")


def loose(text: str) -> str:
    """Lowercase, accents stripped, only letters and digits. For flagging near misses."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]", "", t.lower())
