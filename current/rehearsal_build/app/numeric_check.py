"""Deterministic numeric reconciliation for Claim-Evidence verification.

A researcher's sentence is compared with an analysis results table without any
model involved. Every number the sentence states is located with its exact
span and a kind, then compared with the table's cells at the precision the
sentence itself uses. Cells are read by the same parser, so a claim and a cell
are never compared in different units by accident.

`reconcile` is a pure function of its two arguments: no file, database or
socket is touched, and the same arguments always give the same answer,
including the order of `matches` and which cell becomes `nearest`.

What is read from the claim text:

    integers, decimals, thousands separators (1,204 and 1 204), negatives and
    the unicode minus, percents (5.2%, 5.2 percent, 5.2퍼센트), percentage
    points (5.2%p, 5.2 pp, 5.2 p.p., 5.2 percentage points, kind "other"),
    p-values (p < 0.05, p값 < 0.05, 유의확률 < 0.05), money ($1.2M, 1.2 million
    USD), scientific notation (1.2e-3) and ranges.

A range has two positive bounds of one kind. The separating dash may follow a
number, follow its unit or percent sign, or follow one unit word: "3-5%",
"3%-5%", "3%–5%", "5%-6", "$1.2M–$1.5M", "5 pp–6 pp", "5 km–6 km" and
"5 months–6 months" are each two numbers, never a number and a minus. A dash
after a word that is not a unit is still a sign, so "fell to -5%" and "from 3%
to -5%" keep their negative.

A written multiplier scales a number: K and k always mean a thousand, and
"thousand", M, "million"/"mn", B, "bn" and "billion" always scale too, with or
without a currency. A multiplier makes money only when a currency is actually
written: "$1.2M" and "1.2 million dollars" are money, while "1.2 million" is a
decimal of 1,200,000, "5K respondents" is a count of 5,000 and "5k simulations"
is a count of 5,000.

A lowercase "m" may be metres or millions, so it is settled by what surrounds
it. A currency written on either side makes it millions of money: "$5m",
"$1.5m", "£5m", "€1.2m", "5m USD", "5m dollars" and "5m euros" are all money.
With no currency, only a following count noun makes it millions of things. That
set is closed — people, persons, users, customers, firms, companies, businesses,
households, respondents, participants, patients, cases, viewers, visitors,
voters, residents, citizens, workers, employees, students, children, adults,
members, subscribers, downloads, views, units, permits, transactions, records,
observations, tokens, parameters, deaths, infections and jobs, singular or
plural, plus the Korean counters 명, 개, 건, 가구 and 회 — so "20m people",
"5m users", "12m viewers", "1.5m cases" and "3m people", "5m 명" are counts in
millions. Every other bare "m" — "5m", "a span of 5m", "5m long", "a 5m gap",
"5m of pipe", "beam 5m", "5m 폭" — is genuinely ambiguous: it is reported with
kind "other", a null value, status not_found, no nearest cell, and the reason
"ambiguous unit: m may mean metres or million". It is never matched or
near-missed, because a value a million times off is worse than no answer. A
closed list is used in this direction on purpose: the opposite rule ("millions
unless the next word looks spatial") silently turns every unlisted measure into
millions, which is how "a 5m gap" became 5,000,000.

What is deliberately not read: standalone years, meaning a bare four-digit
literal 1900 to 2100 written with no separator, decimal, multiplier, unit or
percent sign ("2 thousand firms" and "2,024 permits" are therefore quantities);
numbers inside citation brackets ([12]); a bare parenthesised integer "(12)"
where it is citation-like ("see (12)", "firms (12).") but not where a lower-case
noun follows ("(12) participants", "enrolled (12) firms", which are counts);
numbers labelled as a section, table, figure, line or footnote ("Table 2",
"Figure 3", "No. 4", "Nos. 4-6", "#4") — a reference needs "No." or "Nos." with
its dot, or "#", because a sentence-initial "No"/"NO" opens a clause about as
often as it labels one: "No 5.2% change was found", "No 12 firms responded" and
"There was no 5% increase" all keep their figures. The trade-off is that "No 4
shows it" reads its 4 as a quantity; a claim that means a reference writes its
dot. Also not read: a label naming several numbers at once
("Tables 2 and 3", "Tables 2-3", "Figures 4 and 5", "Appendix 2 and 3", "표 2,
3 and 4"), where a labelled list continues only through explicit list syntax and
only through bare integers, so "In Table 2, 1,204 firms were surveyed" keeps
1,204; page references ("p. 12", "pp. 12-14"), the Korean section labels
표·그림·부록·절·쪽, a footnote marker glued to a word, ordinals, dates, and dotted
section or version numbers.

The residual ambiguity in "(12)" is unavoidable without reading the sentence: a
parenthesised integer glued to a word or at a clause end is read as a citation,
and the same integer followed by a lower-case word is read as a count. A
capitalised noun after the parenthesis ("(12) Participants") is read as a count
too, since the citation form is available as "Participants (12)".

A thousands-separated number is never read as a year, so "In 2,024 the policy
was adopted" keeps 2,024 as a count. That is the deliberate choice: a claim
that writes a comma is writing a quantity, and a year written with a comma is
far rarer than a figure is.

Matching rules, all decided by the claim's written precision:

    "5.2%" carries a half step of 0.05, so it matches a cell in [5.15, 5.25)
    and the equivalent fraction 0.052 at one hundredth of that step; the
    percent-to-fraction equivalence works in both directions. A count such as
    "1,204" matches only an equal value. A p-value claim "p < 0.05" matches a
    cell below 0.05 in a p-like column and is not_found otherwise. Nothing
    matches across a change of sign.

A percent claim is compared with a cell through that cell's *column scale*. A
column is percent-scale when its header names %, percent, pct, 퍼센트, (%) or
pp, and fraction-scale when its header names fraction, share of 1, proportion,
ratio, 비율, 비중 or (0-1). Otherwise the scale is inferred from the column's own
values: when every value lies within [-1, 1] and at least one is not an integer
the column holds fractions, and every other column holds percents — which is
what a results table actually writes, so "The rate was 4.2%" matches a plain
"Unemployment" column holding 4.2 instead of being read as a hundredfold
confusion. Each match and each near miss reports the scale it used as
`column_scale` and how that scale was decided as `scale_source`, either "header"
or "inferred from values" — but only when a scale was actually used, meaning a
percent claim was compared, or the header itself declares a percent or a
fraction scale. A count or a money column used no scale, so those two fields are
null there rather than a "percent" that would only say "not fractions".

A value-inferred scale is a property of the column's current rows, so it can
change when sibling rows change: adding one row outside [-1, 1] to a column of
fractions makes that column percent-scale, and the same claim against the same
cell can then be read differently. That is acceptable because the provenance is
exposed — `scale_source` says the scale was inferred rather than declared — and
because the alternative, treating every plain column as fractions, is what made
"The rate was 4.2%" a mismatch 415.8 away from its own cell. A header that
declares its scale is stable and is the reliable form.

A percent claim against a cell of the other *known* scale is never a match: the
same digits there are a hundred times apart, so it is a near_mismatch whose
reason says the scale differs. That holds whether the scale came from the header
or was inferred from the values, so a claim of "0.3%" against a fraction column
holding 0.3 — labelled or not — reports the difference rather than matching.
A percentage point claim (kind "other") is read at its own digits, so "5.2 pp"
matches a cell of 5.2 in a pp, percent or plainly-headered column.

A p-value is compared only with a p-like column, and a p-like column only with a
p-value claim: neither matching nor a near miss crosses that line, so "5.2%" is
not nudged towards a p column's 0.05. A percent claim and a money amount are
different units for the same reason.

A number that is not finite or that overflows a double (1e999, 1e2147483648)
is reported with a null value and status not_found, so an answer is always
JSON-safe and no input can raise.

A number with no match becomes near_mismatch when a cell of the same kind lies
within 25 percent of the claim, differs only in sign, or differs only by
rounding direction; the cell, its column and the difference are then reported.
Every number entry carries a `reason`, which is null except for a unit that
could not be settled, such as the ambiguous lowercase "m".
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

#: A claim reports at most this many matching cells, in the order described
#: below; a repeated value in a large table therefore cannot flood the answer.
MAX_MATCHES = 200
#: Relative difference at which a cell of the same kind counts as a near miss.
NEAR_RELATIVE = Decimal("0.25")
#: The reason a bare lowercase "m" is never matched: it is metres in one
#: sentence and millions in the next, and nothing in the digits says which.
AMBIGUOUS_METRE = "ambiguous unit: m may mean metres or million"

_CURRENCY = "$\u20ac\u00a3\u00a5\u20a9"
_SIGN_CHARS = "-\u2212\u2013\u2014+"
_SPACES = (",", "\u00a0", "\u202f", "\u2009", " ")
_NUMERAL = r"(?:\d{1,3}(?:[,\u00a0\u202f\u2009 ]\d{3})+|\d+)(?:\.\d+)?|\.\d+"
_NUMBER = re.compile(_NUMERAL)
_EXPONENT = re.compile(r"[eE]([+-]?\d+)")
#: An exponent outside this range is not a quantity a results table carries; it
#: would also overflow a double when reported as JSON. Bounding it here keeps
#: the parser total: no input can raise and no infinity is ever produced.
MIN_EXPONENT = -30
MAX_EXPONENT = 30
#: Largest magnitude reported. Beyond this a value stops being a measurement
#: and stops surviving `float()`, so it is refused rather than reported as inf.
MAX_MAGNITUDE = Decimal(10) ** 30
_P_VALUE = re.compile(
    r"(?<![A-Za-z\uac00-\ud7a3])(?:[pP](?:[\s\-]?(?:value|val|\uac12))?"
    r"|\uc720\uc758\ud655\ub960|\uc720\uc758\uc218\uc900)"
    r"\s*(?P<op><=|>=|<|>|=|\u2264|\u2265)\s*(?P<num>(?:" + _NUMERAL + r"))")
_POINTS = re.compile(
    r"\s*(?:%p(?![A-Za-z-])|p\.?p\.?(?![A-Za-z.])"
    r"|percent(?:age)?\s+points?(?![A-Za-z])|\ud37c\uc13c\ud2b8\s*\ud3ec\uc778\ud2b8)",
    re.I)
_PERCENT = re.compile(
    r"\s*(?:%|\uff05|percentage(?![A-Za-z])|percent(?![A-Za-z])|per\s+cent(?![A-Za-z])"
    r"|\ud37c\uc13c\ud2b8)", re.I)
_MULTIPLIER_WORD = re.compile(r"\s*(thousand|million|billion|trillion|mn|bn|tn)(?![A-Za-z])", re.I)
#: A letter multiplier directly after the digits. Every letter here scales; the
#: lowercase "m" is handled apart, because it is metres rather than millions.
_MULTIPLIER_LETTER = re.compile(r"([KMBkmb])(?![A-Za-z0-9])")
_MULTIPLIER_SCALE = {"thousand": 3, "million": 6, "billion": 9, "trillion": 12,
                     "mn": 6, "bn": 9, "tn": 12, "k": 3, "m": 6, "b": 9}
_CURRENCY_CODE = re.compile(r"(?:usd|eur|krw|gbp|jpy)\s*$", re.I)
#: A currency code or verb written just after the number's unit: "1.2M USD".
_CURRENCY_AFTER = re.compile(r"\s*(?:usd|eur|krw|gbp|jpy|dollars?|won|euros?|pounds?)"
                             r"(?![A-Za-z])", re.I)
#: The same, written just before the number: "USD 5m", "dollars 5m". A currency
#: on either side settles what a lowercase "m" means.
_CURRENCY_BEFORE = re.compile(r"(?:(?:usd|eur|krw|gbp|jpy|dollars?|won|euros?|pounds?)\s*$)", re.I)
_CURRENCY_WORD = re.compile(r"\s*(?:dollars?|won|euros?|pounds?)(?![A-Za-z])", re.I)
_CURRENCY_WORD_BEFORE = re.compile(r"(?:dollars?|won|euros?|pounds?)\s*$", re.I)
#: The closed set of nouns after which a bare lowercase "m" counts millions, in
#: singular and plural English forms plus the Korean counters. The list is
#: deliberately closed rather than "any word that is not spatial", because the
#: open direction counts metres as millions: "a 5m gap", "5m of pipe" and
#: "5m 폭" would all become five million of something. A word outside this list
#: leaves the "m" ambiguous instead of guessing.
_COUNT_NOUN = re.compile(
    r"\s+(?:" + "|".join((
        "people", "persons", "person",
        "users", "user", "customers", "customer", "firms", "firm",
        "companies", "company", "businesses", "business",
        "households", "household", "respondents", "respondent",
        "participants", "participant", "patients", "patient", "cases", "case",
        "viewers", "viewer", "visitors", "visitor", "voters", "voter",
        "residents", "resident", "citizens", "citizen", "workers", "worker",
        "employees", "employee", "students", "student", "children", "child",
        "adults", "adult", "members", "member", "subscribers", "subscriber",
        "downloads", "download", "views", "view", "units", "unit",
        "permits", "permit", "transactions", "transaction", "records", "record",
        "observations", "observation", "tokens", "token",
        "parameters", "parameter", "deaths", "death",
        "infections", "infection", "jobs", "job",
    )) + r")(?![A-Za-z])"
    # Korean writes the counter glued to the numeral ("5m명이"), and a particle
    # may follow it. Only a particle, punctuation, space or the end may follow,
    # so "5m건물" (a building) or "5m개월" (months) stay ambiguous.
    r"|\s*(?:\uac00\uad6c|\uba85|\uac1c|\uac74|\ud68c)"
    r"(?=$|[^\w]|(?:\uc785\ub2c8\ub2e4|\uc5d0\uc11c|\ubd80\ud130|\uae4c\uc9c0|\uc73c\ub85c|\uc774\ub2e4"
    r"|[\uc774\uac00\uc744\ub97c\uc740\ub294\uc758\uc5d0\uacfc\uc640\ub3c4\ub9cc\uc529\ub85c])(?![\uac00-\ud7a3]))",
    re.I)

#: A word that labels a number as a reference rather than a quantity. English
#: and Korean, because a Korean claim labels its sections the Korean way. Only
#: words that really name a document structure are here: "no", "notes" and
#: "parts" are deliberately absent, because "There was no 5% increase" asserts
#: a figure and must keep it.
_REFERENCE_WORD = (
    r"tables?|figures?|figs?|sections?|secs?|appendi(?:x|ces)|chapters?|chaps?"
    r"|panels?|columns?|cols?|rows?|equations?|eqs?|charts?|exhibits?|footnotes?"
    r"|lines?|items?|models?|steps?|questions?|q"
    r"|\ud45c|\uadf8\ub9bc|\ubd80\ub85d|\uc808|\ucabd|\ubc94\ub840|\uc0ac\ub840"
)
#: A number reference needs "No." or "Nos." with its dot, or "#". A bare
#: "No"/"NO" is not a label: "No 5.2% change was found" and "No 12 firms
#: responded" both assert a figure, and the sentence-initial capital says
#: nothing about whether the word labels a reference. The trade-off is that
#: "No 4 shows it" reads its 4 as a quantity; writing "No. 4" says what is
#: meant, and a claim that spells a reference is expected to spell it fully.
_REF_NO = r"(?i:nos?\.)|#|(?i:numbers?)"
#: The rest of a reference number may not carry a unit, a percent sign, a
#: decimal or a thousands separator: where a quantity begins, the label ends.
_NOT_A_QUANTITY = r"(?!\d|\.\d|,\d)(?!\s*(?:%|\uff05|pp|percent|pct|\ud37c\uc13c\ud2b8))"
#: The label word and the number after it. The whole span is masked, so the
#: first number of a labelled list disappears with the rest instead of leaking
#: through as a quantity. The reference words are case-insensitive; "No" and
#: "NO" are not, so the ordinary word "no" is never a label.
_LABEL_HEAD = re.compile(
    r"(?<![A-Za-z\uac00-\ud7a3])(?:(?i:" + _REFERENCE_WORD + r")|" + _REF_NO + r")"
    r"\s*\.?\s*(\d+)" + _NOT_A_QUANTITY)
#: A labelled list continues only through explicit list syntax: "and", "&",
#: "or", "to", "–", "-", or a comma list that ends in "and N". Every member must
#: be a bare integer. A label followed by a plain comma and another number does
#: not continue, so "In Table 2, 1,204 firms were surveyed" keeps its 1,204.
_LABEL_CONNECT = re.compile(
    r"\s*(?:&|\band\b|\bor\b|\bto\b|\bthrough\b|\u2013|\u2014|-)\s*(\d+)" + _NOT_A_QUANTITY, re.I)
_LABEL_COMMA_RUN = re.compile(
    r"\s*,\s*\d+(?:\s*,\s*\d+)*\s*,?\s*(?:&|\band\b)\s*(\d+)" + _NOT_A_QUANTITY, re.I)
#: One continuation of a label list: either explicit list syntax, or a comma
#: run that ends in "and N". A plain comma never continues the list.
_LABEL_MORE = re.compile(
    r"(?:" + _LABEL_CONNECT.pattern + r")|(?:" + _LABEL_COMMA_RUN.pattern + r")", re.I)
#: "p. 12", "pp. 12-14": a page reference. "pp" directly after a number is
#: percentage points, not a page, so the dot and the following number are what
#: make this a page reference.
_PAGE_REF = re.compile(r"(?<![A-Za-z])(?:pp?|pgs?)\.\s*\d+(?:\s*(?:\u2013|\u2014|-)\s*\d+)?", re.I)
#: "제3장", "제2절": a Korean chapter/section reference that reads as one token.
_KOREAN_REF = re.compile(r"\uc81c\s*\d+\s*(?:\uc7a5|\uc808|\ud56d|\ubaa9|\ubd80|\ud3b8|\uad8c|\ucc28)")
#: A parenthesised integer, masked only where it is citation-like: it directly
#: follows a word ("firms (12)", "see (12)") or sits at a clause end ("(12)",
#: "firms (12)."). Followed by a lowercase word it is a count instead, as in
#: "(12) participants" and "enrolled (12) firms".
_PAREN_INT = re.compile(r"\(\s*\d{1,3}\s*\)")
_LABEL_PREFIX = re.compile(
    r"(?<![A-Za-z\uac00-\ud7a3])(?:(?i:" + _REFERENCE_WORD + r")|" + _REF_NO + r")"
    r"\s*\.?\s*[A-Za-z]?\s*$")
_MARK_PREFIX = re.compile(r"[\u00a7#]\s*$")
_BRACKET = re.compile(r"\[[^\[\]]*\]")
_DATE = re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
_DOTTED = re.compile(r"\d+(?:\.\d+){2,}")
_RANGE_GAP = re.compile(
    r"\s*(?:[-\u2212\u2013\u2014~]|\bto\b|\bthrough\b)\s*[$\u20ac\u00a3\u00a5\u20a9]?\s*", re.I)
_WORD = re.compile(r"[A-Za-z\uac00-\ud7a3]{2,}")

_COEFFICIENT_LIKE = re.compile(r"coef|coefficient|beta|\u03b2|elasticity")
_MONEY_LIKE = re.compile(r"\b(?:usd|eur|krw|gbp|jpy|dollars?|cost|revenue|spend|price)\b", re.I)

#: A claim kind and a cell kind belong to the same family when a near miss may
#: be reported between them. A count, a decimal, a coefficient and a money
#: amount are all plain numbers; a percent and a percentage point are both
#: ratio-shaped units; a p-value only ever compares with a p-value.
_FAMILY = {"percent": "ratio", "other": "ratio", "p_value": "p_value",
           "count": "number", "decimal": "number", "coefficient": "number", "money": "number"}


# ---------------------------------------------------------------- public API
def reconcile(claim_text: str, table: dict) -> dict:
    """Compare the numbers of `claim_text` with the cells of a results table.

    `table` is `{"item_id", "title", "columns": [str], "rows": [[str]]}`. The
    answer holds one entry per number of the claim, in reading order, and a
    count per status. Matching cells are ordered by header similarity to the
    claim first, then by row, then by column, so two calls never disagree.
    """
    if not isinstance(claim_text, str):
        raise ValueError("claim_text must be text")
    columns, rows = _table_parts(table)
    cells = _cells(columns, rows)
    words = {word for word in _WORD.findall(claim_text.lower()) if len(word) > 1}
    numbers = []
    summary = {"total": 0, "match": 0, "near_mismatch": 0, "not_found": 0}
    for token in _tokens(claim_text, years=True):
        entry = _reconcile_token(token, cells, words)
        numbers.append(entry)
        summary["total"] += 1
        summary[entry["status"]] += 1
    return {"numbers": numbers, "summary": summary}


def looks_like_formula(value) -> bool:
    """True for a cell or header that a spreadsheet would evaluate.

    A leading "=" or "@" always starts a formula. A leading "+" or "-" does so
    only when the whole cell is not a single number the reader itself accepts,
    so "-.5", "-.03", "-.5e-3", "-$1.2M", "-0.3 %" and "+5.2 %" stay data while
    "-A1", "-A1+B2", "-SUM(B:B)", "+A1" and "=1+2" are refused. A lone dash is a
    "no data" marker, never a formula. The reader is the only authority here, so
    a cell can never be accepted as a number and rejected as a formula.

    A leading tab or CR followed by "=" or "@" is refused too: a spreadsheet
    strips that whitespace and evaluates what follows.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if value[:1] in ("\t", "\r", "\n") and text[0] in "=@":
        return True
    if text[0] in "=@":
        return True
    if text[0] in "+-":
        if text in ("-", "+") or text.strip("-+\u2212\u2013\u2014 ") == "":
            return False  # a lone dash is a "no data" marker, never a formula
        if _single_number(text):
            return False
        return True
    return False


def _single_number(text: str) -> bool:
    """True when `text` is exactly one number, read by the claim parser.

    One token that spans the whole text: "-$1.2M" and "+5.2 %" qualify, while
    "-A1+B2" does not. A leading sign is read as a sign here (not as a range
    dash), because a cell holds one value, not a range.
    """
    tokens = _tokens(text)
    return len(tokens) == 1 and tokens[0]["start"] <= 1 and tokens[0]["end"] >= len(text.rstrip())


# ---------------------------------------------------------------- numerals
def _numeral(raw: str) -> tuple[Decimal | None, int]:
    """Value and written decimal places of a numeral, separators removed.

    `.03` keeps its two places, so a p-value keeps the precision it was
    written with.
    """
    cleaned = raw
    for separator in _SPACES:
        cleaned = cleaned.replace(separator, "")
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None, 0
    fraction = cleaned.split(".", 1)[1] if "." in cleaned else ""
    return value, len(fraction)


def _half_step(decimals: int) -> Decimal:
    """Half of one unit in the last written place (0.05 for two digits)."""
    return Decimal(5) * Decimal(1).scaleb(-decimals - 1)


def _cut_by_separator(text: str, start: int, end: int) -> bool:
    """True when the match sits inside a malformed thousand-separator group.

    "12,04" is not a number a table would carry; reading it as 12 and 04 would
    invent two claims, so both halves are left alone.
    """
    if text[end:end + 1] == "," and text[end + 1:end + 2].isdigit():
        return True
    return start >= 2 and text[start - 1] == "," and text[start - 2].isdigit()


def _token(text: str, start: int, end: int, value: Decimal | None, decimals: int, kind: str,
           op: str | None = None, scale: int = 0, reason: str | None = None) -> dict:
    """One parsed number. `value` is None for a number too large to represent,
    which keeps the token in reading order while making it match nothing.
    `reason` says why a number that was read is nonetheless never matched."""
    return {"text": text[start:end], "start": start, "end": end, "dec": value,
            "decimals": decimals, "half": _half_step(decimals), "kind": kind,
            "op": op, "scale": scale, "reason": reason}


def _ends_a_number(text: str, index: int) -> bool:
    """True when the text just before `index` ends a number, unit and all.

    The look-back skips what may trail a number — a percent or percentage-point
    word, a multiplier letter or word, a currency symbol, or exactly one
    alphabetic unit word as in "5 km" and "5 months" — and then requires a
    digit. That is what makes the dash in "3%–5%", "3%-5%", "$1.2M–$1.5M",
    "5%-6", "5 pp–6 pp", "5 km–6 km" and "5 months–6 months" a range separator,
    while the dash after any other word ("fell to -5%") stays a sign. At most
    one unit word is skipped, so "the falls were -5%" keeps its minus.
    """
    cursor = index
    while cursor > 0 and text[cursor - 1].isspace():
        cursor -= 1
    for suffix in ("percentage points", "percent points", "percentage point", "percent point",
                   "percent", "pct", "\ud37c\uc13c\ud2b8", "%p", "%", "\uff05",
                   "billion", "million", "thousand", "trillion", "p.p.", "pp"):
        if text[:cursor].lower().endswith(suffix):
            cursor -= len(suffix)
            break
    else:
        if cursor > 0 and text[cursor - 1] in "KMBkmb":
            cursor -= 1
    while cursor > 0 and text[cursor - 1].isspace():
        cursor -= 1
    if not (cursor > 0 and text[cursor - 1].isdigit()):
        # Skip exactly one alphabetic unit word: "5 km", "5 months", "5 pounds".
        end = cursor
        while cursor > 0 and text[cursor - 1].isalpha():
            cursor -= 1
        if cursor == end:
            return False
        while cursor > 0 and text[cursor - 1].isspace():
            cursor -= 1
    return cursor > 0 and text[cursor - 1].isdigit()


def _is_bare_year(core: str, text: str, start: int, end: int, finish: int, scale: int) -> bool:
    """True when the number is written as a bare year.

    The test is on the *written form*, never on the computed value: a bare
    four-digit literal 1900 to 2100 with no sign, thousands separator, decimal
    point, multiplier, unit or percent sign. Testing the value instead made
    "2 thousand firms" vanish, because 2 scaled by a thousand lands inside the
    year range while nothing about "2 thousand" is a year.
    """
    if len(core) != 4 or not core.isdigit():
        return False
    if text[start - 1:start] in ("+",) + tuple(_SIGN_CHARS):
        return False
    if scale:
        return False  # a multiplier or exponent was written
    if text[finish:finish + 1] in ("%", "\uff05"):
        return False
    return Decimal(1900) <= Decimal(core) <= Decimal(2100)


def _classify(text: str, start: int, end: int, years: bool) -> dict | None:
    """One number token from the numeric core at `start`..`end`, or None when
    the text around it says it is not a claim about a quantity.

    `years` is set for claim text, where a standalone 1900..2100 number is a
    date the claim is not asserting. A table cell is a value in a column, so
    a bare 2000 there is data and is kept.
    """
    if _cut_by_separator(text, start, end):
        return None  # half of a malformed thousand-separator group
    if start > 0 and (text[start - 1].isalpha() or text[start - 1] == "_"):
        return None  # a footnote marker or a label glued to a word
    if text[end:end + 2].lower() in ("st", "nd", "rd", "th"):
        return None  # an ordinal
    window = text[max(0, start - 24):start]
    if _LABEL_PREFIX.search(window) or _MARK_PREFIX.search(window):
        return None  # "Table 2", "Figure 3", "No. 4"
    cursor = start
    money = False
    if cursor > 0 and text[cursor - 1] in _CURRENCY:
        money = True
        cursor -= 1
    negative = False
    if cursor > 0 and text[cursor - 1] in _SIGN_CHARS:
        char = text[cursor - 1]
        before = text[cursor - 2] if cursor >= 2 else ""
        if _ends_a_number(text, cursor - 1):
            pass  # the dash of a range, not a sign: the number stays unsigned
        elif before.isalpha() or before == "_":
            return None  # a hyphen inside a word or an identifier
        else:
            negative = char != "+"
            cursor -= 1
            if not money and cursor > 0 and text[cursor - 1] in _CURRENCY:
                money = True
                cursor -= 1
    core = text[start:end]
    value, mantissa_decimals = _numeral(core)
    if value is None:
        return None
    finish = end
    exponent = 0
    match = _EXPONENT.match(text, finish)
    if match:
        exponent = int(match.group(1))
        finish = match.end()
    kind = None
    multiplier = 0
    match = _POINTS.match(text, finish)
    if match:
        kind = "other"
        finish = match.end()
    if kind is None:
        match = _PERCENT.match(text, finish)
        if match:
            kind = "percent"
            finish = match.end()
    if kind is None:
        match = _MULTIPLIER_WORD.match(text, finish)
        if match:
            multiplier = _MULTIPLIER_SCALE[match.group(1).lower()]
            finish = match.end()
    if not money and (_CURRENCY_CODE.search(text[max(0, cursor - 6):cursor])
                      or _CURRENCY_WORD.match(text, finish)
                      or _CURRENCY_WORD_BEFORE.search(text[max(0, cursor - 12):cursor])):
        money = True
    if kind is None and multiplier == 0:
        # A letter directly after the digits is a multiplier: "5K respondents"
        # is 5,000 and "5M people" is 5,000,000. A lowercase "m" needs its
        # meaning settled, because it may be metres or millions: a currency
        # before or after it makes it money in millions ("$5m", "5m USD"), a
        # following count noun makes it a count in millions ("20m people",
        # "1.5m cases"), and anything else — "5m", "a span of 5m", "5m long",
        # "a 5m gap" — is ambiguous and is reported as such instead of being
        # matched at a value a million times off.
        letter = _MULTIPLIER_LETTER.match(text, finish)
        if letter:
            unit = letter.group(1)
            if unit != "m":
                multiplier = _MULTIPLIER_SCALE[unit.lower()]
                finish = letter.end()
                if _CURRENCY_AFTER.match(text, finish):
                    money = True
            else:
                finish = letter.end()
                if (money or _CURRENCY_AFTER.match(text, finish)
                        or _CURRENCY_BEFORE.search(text[max(0, cursor - 12):cursor])):
                    money = True
                    multiplier = _MULTIPLIER_SCALE["m"]
                elif _COUNT_NOUN.match(text, finish):
                    multiplier = _MULTIPLIER_SCALE["m"]
                else:
                    return _token(text, cursor, finish, None, 0, "other",
                                  reason=AMBIGUOUS_METRE)
    scale = exponent + multiplier
    if scale and not MIN_EXPONENT <= scale <= MAX_EXPONENT:
        # A written scale this large is not a quantity a results table carries,
        # and raising ten to it would itself overflow: refuse it before the
        # multiplication, so no input can raise.
        return _token(text, cursor, finish, None, 0, "other", scale=0)
    if scale:
        value = value * Decimal(10) ** scale
    decimals = mantissa_decimals - scale
    if negative:
        value = -value
    if kind is None:
        if money:
            kind = "money"
        elif "." in core:
            kind = "coefficient" if negative else "decimal"
        else:
            kind = "count"
    if years and _is_bare_year(core, text, start, end, finish, scale):
        return None  # a standalone year, not a quantity
    if abs(value) > MAX_MAGNITUDE:
        # Not a measurement any table carries, and not representable as a JSON
        # float: reported as an unusable number rather than as infinity.
        return _token(text, cursor, finish, None, 0, "other", scale=scale)
    return _token(text, cursor, finish, value, decimals, kind, scale=scale)


def _is_citation_paren(text: str, start: int, end: int) -> bool:
    """Is this parenthesised integer a citation rather than a count?

    A parenthesised integer is citation-like when it directly follows a word
    ("firms (12)", "see (12)") or sits at a clause end ("(12)", "firms (12).").
    Followed by a lowercase word it is a count instead, whatever precedes it, so
    "(12) participants" and "enrolled (12) firms" both keep their 12.

    The residual ambiguity is a capitalised noun after the parenthesis:
    "(12) Participants" reads as a count here, because the sentence can always
    be written the other way ("Participants (12)") when it is a citation, while
    a count written that way has no spacing that would tell the two apart.
    """
    if re.match(r"\s*[a-z\uac00-\ud7a3]", text[end:]):
        return False  # a lowercase noun follows: a count
    if re.search(r"[A-Za-z\uac00-\ud7a3]\s*$", text[:start]):
        return True  # glued to a word: a footnote mark or a citation
    return re.match(r"\s*(?:$|[.,;:!?)\]])", text[end:]) is not None


def _masked_spans(text: str) -> list[tuple[int, int]]:
    """Spans of `text` a claim number is never read from.

    A citation bracket, a citation-like parenthesised integer, a page reference,
    a date, a dotted section or version number, a Korean chapter reference, and a
    label naming one or more numbers ("Table 2", "Tables 2 and 3", "Figures 4-5",
    "Appendix 2 and 3", "No. 4") are masked whole. A label list is masked from
    its word through its last member, so no member leaks as a quantity.
    """
    spans = [match.span() for match in _BRACKET.finditer(text)]
    spans += [match.span() for match in _PAREN_INT.finditer(text)
              if _is_citation_paren(text, *match.span())]
    spans += [match.span() for match in _PAGE_REF.finditer(text)]
    spans += [match.span() for match in _DATE.finditer(text)]
    spans += [match.span() for match in _DOTTED.finditer(text)]
    spans += [match.span() for match in _KOREAN_REF.finditer(text)]
    for head in _LABEL_HEAD.finditer(text):
        start, end = head.span()
        while True:
            more = _LABEL_MORE.match(text, end)
            if more is None:
                break
            end = more.end()
        spans.append((start, end))
    return spans


def _tokens(text: str, years: bool = False) -> list[dict]:
    """Every claim number of `text`, in reading order, with ranges applied."""
    tokens: list[dict] = []
    taken = _masked_spans(text)
    for match in _P_VALUE.finditer(text):
        raw = match.group("num")
        value, decimals = _numeral(raw)
        if value is None:
            continue
        op = {"\u2264": "<=", "\u2265": ">=", "\u2260": "!="}.get(match.group("op"), match.group("op"))
        span = match.span()
        tokens.append(_token(text, span[0], span[1], value, decimals, "p_value", op=op))
        taken.append(span)
    for match in _NUMBER.finditer(text):
        start = match.start()
        if any(begin <= start < done for begin, done in taken):
            continue
        built = _classify(text, match.start(), match.end(), years)
        if built is None:
            continue
        tokens.append(built)
        taken.append((built["start"], built["end"]))
    tokens.sort(key=lambda token: token["start"])
    _apply_ranges(text, tokens)
    return tokens


def _apply_ranges(text: str, tokens: list[dict]) -> None:
    """Let a range's written unit govern both of its bounds.

    "3-5%" and "5%-6" are two percent claims, "$3-5M" and "$1.2M-$1.5M" are two
    money claims, and "3%–5%" is two percent claims; whichever bound carries
    the unit, the other takes it, and the numbers keep the magnitude they were
    written with. Both bounds stay positive: the separating dash was already
    read as a separator by `_classify`, never as a sign.
    """
    units = ("percent", "other", "money")
    for head, tail in zip(tokens, tokens[1:]):
        if head["op"] or tail["op"]:
            continue
        if head["dec"] is None or tail["dec"] is None:
            continue
        if not _RANGE_GAP.fullmatch(text[head["end"]:tail["start"]]):
            continue
        if tail["kind"] in units:
            source, target = tail, head
        elif head["kind"] in units and tail["kind"] in ("count", "decimal", "coefficient"):
            source, target = head, tail
        else:
            continue
        target["kind"] = source["kind"]
        scale = source["scale"] - target["scale"]
        if scale:
            target["dec"] = target["dec"] * Decimal(10) ** scale
            target["decimals"] -= scale
            target["half"] = _half_step(target["decimals"])
            target["scale"] = source["scale"]


# ---------------------------------------------------------------- table cells
def _table_parts(table) -> tuple[list[str], list[list[str]]]:
    if not isinstance(table, dict):
        raise ValueError("table must be an object")
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    if not isinstance(columns, (list, tuple)) or not isinstance(rows, (list, tuple)):
        raise ValueError("table columns and rows must be lists")
    clean_rows = [list(row) if isinstance(row, (list, tuple)) else [] for row in rows]
    return [str(column) for column in columns], clean_rows


def _header_tokens(column: str) -> list[str]:
    """The words of a column header, lowercased and split on punctuation."""
    return [word for word in re.split(r"[^a-z0-9\uac00-\ud7a3%\uff05]+", column.lower()) if word]


def _p_like(column: str) -> bool:
    """A p-value column: p, p-value, p_value, p.level, sig, signif, significance,
    and the Korean p값 / 유의확률."""
    if "\uc720\uc758" in column:  # 유의확률, 유의수준
        return True
    for word in _header_tokens(column):
        if word in ("p", "sig", "signif", "significance", "pvalue", "pval"):
            return True
        if word.startswith(("pvalue", "p\uac12")) or word.startswith("p-"):
            return True
    return False


def _percent_like(column: str) -> bool:
    for word in _header_tokens(column):
        if word in ("%", "\uff05") or word.startswith(("%", "\uff05")) or word.endswith(("%", "\uff05")):
            return True
        if word in ("pct", "percent", "percentage", "\ud37c\uc13c\ud2b8") or word.startswith("percent"):
            return True
    return False


def _money_like(column: str) -> bool:
    return any(symbol in column for symbol in _CURRENCY) or bool(_MONEY_LIKE.search(column))


def _cell_kind(column: str, token: dict) -> str:
    """A cell's kind: what it was written with, and what its column says."""
    if token["kind"] in ("percent", "other"):
        return token["kind"]
    if _p_like(column):
        return "p_value"
    if _percent_like(column):
        return "percent"
    if token["kind"] == "money" or _money_like(column):
        return "money"
    if _COEFFICIENT_LIKE.search(column.lower()):
        return "coefficient"
    return token["kind"]


#: The scale of a column's numbers, and the way it was decided. A results table
#: writes "4.2" for 4.2 percent about as often as it writes "5.2" under a "%"
#: header, so the scale is a property of the column, not of the digits.
SCALE_PERCENT, SCALE_FRACTION = "percent", "fraction"
FROM_HEADER, FROM_VALUES = "header", "inferred from values"
#: A header that names the fraction scale. "(0-1)" is spelled with punctuation
#: a word split loses, so the raw header is searched as well.
_FRACTION_SCALE_HEADER = re.compile(
    r"\bfractions?\b|\bproportions?\b|\bratios?\b|\bshare\s+of\s+1\b"
    r"|\(\s*0\s*[-\u2013\u2014~]\s*1\s*\)|\ube44\uc728|\ube44\uc911", re.I)
#: The cell kinds whose written number carries no unit of its own, so the
#: column's own numbers are what its scale is inferred from.
_PLAIN_WRITTEN = ("count", "decimal", "coefficient", "money")
#: The cell kinds whose value a percent claim may read through its column's
#: scale. A cell that states its own unit, or holds a p-value, is not one.
_PERCENT_SCALABLE = ("count", "decimal", "coefficient")


def _percent_scale_header(column: str) -> bool:
    """A header that names the percent scale: %, percent, pct, 퍼센트, (%) or pp."""
    if _percent_like(column):
        return True
    return any(word == "pp" for word in _header_tokens(column))


def _column_scale(column: str, values: list[Decimal]) -> tuple[str, str]:
    """The scale of one column's numbers, and how it was decided.

    The header wins when it names a scale. Otherwise the column's own values
    decide: a column whose every value lies within [-1, 1] with at least one
    non-integer holds fractions, and every other column holds percents — which
    is what a results table actually writes, so "The rate was 4.2%" matches a
    plain "Unemployment" column holding 4.2 instead of being read as a
    hundredfold confusion.
    """
    if _percent_scale_header(column):
        return SCALE_PERCENT, FROM_HEADER
    if _FRACTION_SCALE_HEADER.search(column):
        return SCALE_FRACTION, FROM_HEADER
    if values and all(abs(value) <= 1 for value in values) and any(
            value != value.to_integral_value() for value in values):
        return SCALE_FRACTION, FROM_VALUES
    return SCALE_PERCENT, FROM_VALUES


def _cells(columns: list[str], rows: list[list[str]]) -> list[dict]:
    """Every numeric cell, in row-major order. Non-numeric cells are skipped.

    A cell with several numbers contributes its first one, so "12.3 (2.1)"
    reads as 12.3 with a standard error beside it. A cell keeps the number of
    digits it was written with, so a claim can tell a rounded figure from one
    carried at more precision. Each cell also carries the scale of its column
    and how that scale was decided; a cell written with its own percent sign or
    percentage point does not vote on the column's scale, because it states its
    own.
    """
    cells = []
    for row_index, row in enumerate(rows):
        for column_index, column in enumerate(columns):
            text = str(row[column_index]) if column_index < len(row) else ""
            for token in _tokens(text):
                cells.append({"row": row_index, "col": column_index, "column": column,
                              "cell": text, "dec": token["dec"],
                              "decimals": token["decimals"],
                              "written": token["kind"],
                              "kind": _cell_kind(column, token)})
                break
    by_column: dict[int, list[dict]] = {}
    for cell in cells:
        by_column.setdefault(cell["col"], []).append(cell)
    for column_index, column in enumerate(columns):
        group = by_column.get(column_index, [])
        values = [cell["dec"] for cell in group
                  if cell["dec"] is not None and cell["written"] in _PLAIN_WRITTEN]
        scale, source = _column_scale(column, values)
        for cell in group:
            cell["scale"], cell["scale_source"] = scale, source
    return cells


# ---------------------------------------------------------------- comparison
#: How a value is being read: as written ("plain"), as a percent or percentage
#: point ("percent") or as the fraction a percent equals ("fraction"). A percent
#: claim is compared in both of its own readings, and a percent cell is read
#: both ways, but the two written percent forms never compare with the other
#: side's fraction form: that pair mixes units and would invent a match between
#: "0.3%" and a bare stored 0.3.
FORM_PLAIN, FORM_PERCENT, FORM_FRACTION = "plain", "percent", "fraction"


def _claim_reps(token: dict) -> list[tuple[Decimal, Decimal, Decimal, str]]:
    """The claim's value in each reading it has, with its half step and the
    factor that brings a difference back into the claim's own written units."""
    if token["kind"] != "percent":
        return [(token["dec"], token["half"], Decimal(1), FORM_PLAIN)]
    return [(token["dec"], token["half"], Decimal(1), FORM_PERCENT),
            (token["dec"] / 100, token["half"] / 100, Decimal(100), FORM_FRACTION)]


def _cell_values(cell: dict, token: dict) -> list[tuple[Decimal, str]]:
    """The readings of one cell, given the scale of its column.

    A cell written with its own percent sign states its own unit: 5.2 percent,
    which is also the fraction 0.052. A bare cell takes the scale its column
    decided: in a percent-scale column the written 5.2 means 5.2 percent and
    therefore the fraction 0.052, and in a fraction-scale column the written
    0.052 is the fraction itself. That is what lets a percent claim reach a
    percent column at the same digits and a fraction column at value/100, and
    what keeps "0.3%" away from 0.3 in a fraction column, where 0.3 means thirty
    percent.

    The reading a percent-scale column adds is that its digits are also a
    proportion, and it is offered to a percent claim and to a decimal or
    coefficient claim, but not to a count: a count is counted in the table's own
    units, so 100 permits in a column headed "Permits (%)" is not one permit.
    A column whose scale was merely inferred adds that reading only for a
    percent claim.
    """
    if cell["written"] == "other" or cell["kind"] in ("money", "p_value"):
        return [(cell["dec"], FORM_PLAIN)]  # percentage points, metres, money
    if cell["written"] == "percent":
        return [(cell["dec"], FORM_PERCENT), (cell["dec"] / 100, FORM_FRACTION)]
    if cell["scale"] == SCALE_FRACTION:
        return [(cell["dec"], FORM_FRACTION)]
    if token["kind"] == "percent" or (
            cell["scale_source"] == FROM_HEADER and token["kind"] in ("decimal", "coefficient")):
        return [(cell["dec"], FORM_PERCENT), (cell["dec"] / 100, FORM_FRACTION)]
    return [(cell["dec"], FORM_PERCENT)]


def _p_value_mismatch(token: dict, cell: dict) -> bool:
    """One side is a p-value and the other is not.

    A p-value is a probability in its own right and a p-like column holds only
    p-values, so a percentage or a count must never be compared with one in
    either direction — not to match, and not as a near miss either. Without
    this, a "5.2%" claim would be nudged towards a p column's 0.05 and reported
    as a 5.15-point gap that means nothing.
    """
    return (token["kind"] == "p_value" or cell["kind"] == "p_value") and not (
        token["kind"] == "p_value" and cell["kind"] == "p_value")


def _comparable(token: dict, cell: dict) -> bool:
    """A p-value claim is only ever compared with a p-like column, and the
    reverse holds too. A percent claim and a money amount are different units,
    and a percentage of a cost is not that cost.
    """
    if _p_value_mismatch(token, cell):
        return False
    if token["kind"] == "percent" and cell["kind"] == "money":
        return False
    return True


def _scale_comparable(token: dict, cell: dict) -> bool:
    """May a near miss be measured between this claim and this cell?

    The kinds decide, with one addition the column scale makes possible: a
    percent claim and a bare cell are read on the same axis, because the cell's
    reading is what its column's scale makes it. Without that, "The rate was
    4.6%" against an inferred percent column holding 4.2 would report nothing
    while the same column headed "%" would report the 0.4 point gap — a
    difference of header spelling alone — and "5.2%" against a fraction column
    holding 0.051 could not report its 0.1 point gap either.

    The p-value rule is symmetric here too: a bare digit in a column headed "p"
    is a p-value, and no percent or count claim is near it.
    """
    if _p_value_mismatch(token, cell):
        return False
    if _FAMILY[token["kind"]] == _FAMILY[cell["kind"]]:
        return True
    return token["kind"] == "percent" and cell["written"] in _PERCENT_SCALABLE


def _pairs(token: dict, cell: dict):
    """The readings of claim and cell that may be compared with each other.

    A claim without a unit is compared with every reading of the cell. A percent
    claim's written percent form only ever meets a percent reading, and its
    fraction form only a fraction or plain reading: the digits of "0.3%" must
    never meet 0.3 in a fraction column, where 0.3 is thirty percent. The
    genuine equivalences hold because each side carries the form the other
    needs, and the cell's forms follow its column's scale.
    """
    for claim_value, half, factor, claim_form in _claim_reps(token):
        for value, cell_form in _cell_values(cell, token):
            if claim_form == FORM_PERCENT and cell_form != FORM_PERCENT:
                continue
            if claim_form == FORM_FRACTION and cell_form == FORM_PERCENT:
                continue
            yield claim_value, half, factor, value


def _within(token: dict, cell: dict) -> bool:
    """Does the cell fall in the claim's window at the claim's precision?

    A cell whose value could not be represented never matches, so a cell of
    "1e999" is skipped rather than compared with anything.
    """
    if token["dec"] is None or cell["dec"] is None:
        return False
    for claim_value, half, _, value in _pairs(token, cell):
        if token["kind"] == "count":
            if claim_value == value:
                return True
        elif claim_value - half <= value < claim_value + half:
            return True
    return False


def _matches(token: dict, cell: dict) -> bool:
    if token["kind"] != "p_value":
        return _within(token, cell)
    if token["dec"] is None or cell["dec"] is None:
        return False
    threshold, value, op = token["dec"], cell["dec"], token["op"]
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    return _within(token, cell)


def _scale_mismatch(token: dict, cell: dict) -> tuple[Decimal, str] | None:
    """The 100x unit confusion between a percent claim and a fraction cell.

    A claim of "0.3%" against a cell of 0.3 in a fraction-scale column is the
    same digits in the wrong unit: the cell means thirty percent, a hundred
    times the claim. That is never a match, and it is worth saying so, so it is
    reported as a near miss whose reason names the scale. The difference is
    expressed in the claim's own units.

    The confusion only exists when the column's scale is the *other* one. In a
    percent-scale column the same digits are the same quantity, so there is
    nothing to report here and the ordinary comparison handles it.
    """
    if token["kind"] != "percent" or token["dec"] is None or cell["dec"] is None:
        return None
    if cell["written"] not in _PERCENT_SCALABLE or cell["scale"] != SCALE_FRACTION:
        return None
    if abs(cell["dec"] - token["dec"]) > token["half"]:
        return None
    if token["dec"] == 0:
        return None
    as_percent = cell["dec"] * 100
    return abs(as_percent - token["dec"]), "scale differs (percent vs fraction)"


def _nearness(token: dict, cell: dict) -> tuple[Decimal, str] | None:
    """The difference and the reason when the claim nearly, but not quite,
    meets this cell.

    Three ways to nearly meet: same kind and within 25 percent relative
    difference, the same magnitude under the other sign, or a figure that
    rounds to one written step away from the claim. The reported difference is
    always expressed in the claim's own units, so a 5.2 percent claim next to a
    stored 0.0592 reads as a 0.72 point gap, not as 0.0072. A percent claim and
    a plain cell holding the same digits nearly meet in one further way: their
    units are a hundred apart.
    """
    best = _scale_mismatch(token, cell)
    if token["dec"] is None or cell["dec"] is None:
        return best
    for claim_value, half, factor, value in _pairs(token, cell):
        difference = abs(value - claim_value)
        base = abs(claim_value) if claim_value != 0 else abs(value)
        relative = Decimal(0) if base == 0 else difference / base
        sign_differs = claim_value != 0 and value != 0 and (
            (claim_value < 0) != (value < 0))
        matched_magnitude = abs(abs(value) - abs(claim_value)) <= NEAR_RELATIVE * max(
            abs(claim_value), abs(value))
        if sign_differs and matched_magnitude:
            reason = ("sign differs: the claim is %s but the cell is %s"
                      % ("negative" if claim_value < 0 else "positive",
                         "negative" if value < 0 else "positive"))
        elif relative <= NEAR_RELATIVE:
            reason = "within 25% relative difference"
        elif _rounds_onto(value, claim_value, half):
            reason = "differs only by rounding direction"
        else:
            continue
        scaled = difference * factor
        if best is None or scaled < best[0]:
            best = (scaled, reason)
    if best is None:
        return None
    return best


def _rounds_onto(value: Decimal, claim_value: Decimal, half: Decimal) -> bool:
    """True when the cell, rounded to the claim's precision, lands on the claim
    or one written step away from it.

    "5.2%" against a cell of 5.249: it does not fall inside the claim's window
    ([5.15, 5.25) does contain it, but a cell of 5.349 does not), yet it
    rounds to 5.2, so the two differ only in how the figure was rounded rather
    than in what it says. A cell one whole written step away (0.049 against a
    claim of 0.05) reads the same way.
    """
    places = -half.as_tuple().exponent - 1
    if places < 0:
        return False
    unit = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(unit, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return False
    return rounded in (claim_value, claim_value + unit, claim_value - unit)


def _similarity(column: str, words: set[str]) -> int:
    """How many of the claim's own words the column header repeats."""
    return sum(1 for word in _WORD.findall(column.lower()) if word in words)


def _json_float(value: Decimal | None) -> float | None:
    """A JSON-safe float, or None when the number is not representable.

    Nothing here can raise or produce an infinity: the parser bounds exponents
    and magnitudes before a token is built, and this is the last guard so an
    unusable number is reported as null instead of breaking a JSON response.
    """
    if value is None:
        return None
    try:
        as_float = float(value)
    except (OverflowError, ValueError):
        return None
    if as_float in (float("inf"), float("-inf")) or as_float != as_float:
        return None
    return as_float


def _scale_shown(token: dict, cell: dict) -> bool:
    """Does this comparison have a scale worth reporting?

    A scale was used only when a percent claim was read against the cell, or
    when the column's own header declares a percent or a fraction scale. Every
    other column's "percent" would be nothing but "not fractions" — a count of
    permits and a cost in dollars are neither — so the two fields are null
    there rather than inviting a reader to think a unit was applied.
    """
    return token["kind"] == "percent" or cell["scale_source"] == FROM_HEADER


def _public_match(cell: dict, scale_shown: bool) -> dict:
    """One matching cell, with the scale it was read at when that matters."""
    return {"row": cell["row"], "col": cell["col"], "column": cell["column"],
            "cell": cell["cell"], "value": _json_float(cell["dec"]),
            "column_scale": cell["scale"] if scale_shown else None,
            "scale_source": cell["scale_source"] if scale_shown else None}


def _reconcile_token(token: dict, cells: list[dict], words: set[str]) -> dict:
    hits = [cell for cell in cells if _comparable(token, cell) and _matches(token, cell)]
    hits.sort(key=lambda cell: (-_similarity(cell["column"], words), cell["row"], cell["col"]))
    status = "match"
    nearest = None
    if token["reason"] is not None:
        # A number whose unit could not be settled is reported, never matched,
        # and never nudged towards a cell: the digits alone would be a guess.
        matches = []
        status = "not_found"
    elif hits:
        matches = [_public_match(cell, _scale_shown(token, cell)) for cell in hits[:MAX_MATCHES]]
    else:
        matches = []
        if token["kind"] == "p_value" and token["op"] != "=":
            # A one-sided p-value claim either holds below its threshold in a
            # p-like column or it holds nowhere; there is no half-way reading.
            status = "not_found"
        elif token["dec"] is None:
            status = "not_found"  # a number too large to be a measurement
        else:
            status, nearest = _nearest(token, cells, words)
    return {"text": token["text"], "start": token["start"], "end": token["end"],
            "value": _json_float(token["dec"]), "kind": token["kind"],
            "matches": matches, "status": status, "nearest": nearest,
            "reason": token["reason"]}


def _nearest(token: dict, cells: list[dict], words: set[str]) -> tuple[str, dict | None]:
    """The cell the claim nearly meets, and how far off it is.

    The smallest difference wins, so the answer really is the nearest cell; an
    identical difference is broken by the claim's own words in the header, then
    by row, then by column, so the same table always names the same cell.
    """
    family = _FAMILY[token["kind"]]
    best = None
    for cell in cells:
        if _scale_comparable(token, cell):
            near = _nearness(token, cell)
        else:
            # A percent claim and a fraction cell belong to different families,
            # yet the same digits there are the 100x unit confusion, which is
            # worth reporting rather than passing over in silence.
            near = _scale_mismatch(token, cell)
        if near is None:
            continue
        difference, reason = near
        key = (difference, -_similarity(cell["column"], words), cell["row"], cell["col"])
        if best is None or key < best[0]:
            best = (key, cell, difference, reason)
    if best is None:
        return "not_found", None
    _, cell, difference, reason = best
    return "near_mismatch", {"row": cell["row"], "col": cell["col"], "column": cell["column"],
                             "cell": cell["cell"], "value": _json_float(cell["dec"]),
                             "difference": _json_float(difference), "reason": reason,
                             "column_scale": cell["scale"] if _scale_shown(token, cell) else None,
                             "scale_source": (cell["scale_source"]
                                              if _scale_shown(token, cell) else None)}
