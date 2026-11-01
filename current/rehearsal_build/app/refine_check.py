"""Independent meaning check for refined questions (lite §5.4).

The refiner is the model; this checker uses its own clause-level rules and never sees the refiner's logic.
It normalises each text to a kept year interval and an excluded-status set, then compares them.
If it cannot parse a bound in the original, it says so (status "unparsed") instead of guessing.
"""
from __future__ import annotations

import re

NEG = r"(?:don't|do not|never|not|exclude|excluding|leave out|leaving out|without|drop|omit|ignore|remove)"
STATUS_WORDS = {"withdrawn": "W", "expired": "X", "approved": "A", "issued": "I"}


def _clauses(text: str) -> list[str]:
    low = re.sub(r"\b(and|or) (later|earlier)\b", r"\1_\2", text.lower())  # keep "and later" with its year
    return [c.strip() for c in re.split(r"[.;?!,]| and | but ", low) if c.strip()]


def kept_set(text: str) -> dict:
    start = end = None
    excluded = set()
    for c in _clauses(text):
        negative = re.search(rf"\b{NEG}\b", c) is not None
        for word, code in STATUS_WORDS.items():
            if word in c and negative:
                excluded.add(code)
        m = re.search(r"\b(19|20)\d{2}\b", c)
        if not m:
            continue
        y = int(m.group(0))
        if re.search(r"\bbefore\b|\bprior to\b|\bearlier than\b", c):
            start, end = (y, end) if negative else (start, y - 1)
        elif re.search(r"\bafter\b|\blater than\b", c):
            start, end = (start, y) if negative else (y + 1, end)
        elif re.search(r"\bonward|\bonwards|\bsince\b|\band_later\b|\bor_later\b|\bfrom\b", c):
            start, end = (start, y - 1) if negative else (y, end)
        elif re.search(r"\bthrough\b|\buntil\b|\bup to\b|\band_earlier\b|\bor_earlier\b", c):
            start, end = (y + 1, end) if negative else (start, y)
    return {"start": start, "end": end, "excluded": excluded}


def check(original: str, refined: str, variable_names: list[str] | None = None) -> tuple[str, list[str]]:
    """Return ("ok" | "mismatch" | "unparsed", reasons)."""
    o, r = kept_set(original), kept_set(refined)
    reasons = []
    # Exact non-year numeric constraints cannot disappear or change. Years are
    # compared by kept intervals below, so equivalent exclusive forms remain valid.
    def numbers(t):
        return sorted(n for n in re.findall(r"(?<![\w])[-+]?\d+(?:\.\d+)?%?", t)
                      if not re.fullmatch(r"(?:19|20)\d{2}", n))
    if numbers(original) != numbers(refined):
        reasons.append("non-year numeric constraints changed")
    def named(t, name):
        pattern = r"\b" + r"[ _-]+".join(re.escape(w) for w in re.split(r"[ _-]+", name)) + r"\b"
        return bool(re.search(pattern, t, re.I))
    for name in variable_names or []:
        if named(original, name) != named(refined, name):
            reasons.append(f"registered variable reference changed: {name}")
    if not refined.strip().endswith("?"):
        reasons.append("refined text is not a question")
    has_year = re.search(r"\b(19|20)\d{2}\b", original) is not None
    if has_year and o["start"] is None and o["end"] is None:
        return "unparsed", reasons + ["year bound in the original could not be parsed"]
    if (o["start"], o["end"]) != (r["start"], r["end"]):
        reasons.append(f"kept years changed: original {o['start']}..{o['end']} vs refined {r['start']}..{r['end']}")
    if o["excluded"] != r["excluded"]:
        reasons.append(f"exclusions changed: original {sorted(o['excluded'])} vs refined {sorted(r['excluded'])}")
    return ("ok" if not reasons else "mismatch"), reasons
