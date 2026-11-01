"""Apple on-device AI (`FoundationModels.SystemLanguageModel`) wrapper.

The only model this module can reach is the one already installed on this Mac.
There is no HTTP client, no provider SDK and no API key anywhere in this file;
the sole external process is the compiled Swift bridge in `tools/`.

Grounding rule: the model is asked for quotes and those quotes are checked back
against the exact text that was supplied. A quote that does not appear verbatim
is dropped from the result and counted in `unverified_dropped` — it is never
"repaired", completed or paraphrased into something plausible.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWIFT_SOURCE = ROOT / "tools" / "apple_ai_bridge.swift"
BUILD_TIMEOUT = 180
CALL_TIMEOUT = 120
MAX_QUOTE_CHARS = 600
MAX_SCREEN_ITEMS = 25
MAX_CHARS_PER_ITEM = 12_000

SYSTEM_PROMPT = (
    "You screen research-library items for a researcher. Use ONLY the supplied text. "
    "Never add facts that are not in the text. Copy quotes character for character from the "
    "supplied text (they must be findable there exactly). If the text has nothing relevant, "
    "return an empty quote list and say so in the summary. Answer with one JSON object only, "
    "no prose before or after it, in this shape: "
    '{"summary": "<one sentence>", "items": [{"item_id": "<id>", "relevance": "high|medium|low", '
    '"quote": "<verbatim excerpt from that item\'s text>", "why": "<one short sentence>"}]}'
)

_build_lock = threading.Lock()


def _cache_dir() -> Path:
    base = os.environ.get("WB_APPLE_AI_CACHE")
    if base:
        return Path(base)
    return Path(tempfile.gettempdir()) / "wb_apple_ai_bridge"


def _binary() -> tuple[Path | None, str]:
    """Compile-on-demand; returns (binary, reason-it-is-missing)."""
    if shutil.which("swiftc") is None:
        return None, "swiftc not found; the on-device bridge cannot be built"
    cache = _cache_dir()
    target = cache / "apple_ai_bridge"
    source_mtime = SWIFT_SOURCE.stat().st_mtime if SWIFT_SOURCE.is_file() else 0
    if target.is_file() and target.stat().st_mtime >= source_mtime:
        return target, ""
    with _build_lock:
        if target.is_file() and target.stat().st_mtime >= source_mtime:
            return target, ""
        if not SWIFT_SOURCE.is_file():
            return None, f"bridge source missing: {SWIFT_SOURCE}"
        sdk = subprocess.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"],
                             capture_output=True, text=True, check=False)
        if sdk.returncode != 0:
            return None, "could not locate the macOS SDK"
        cache.mkdir(parents=True, exist_ok=True)
        try:
            done = subprocess.run(
                ["swiftc", "-sdk", sdk.stdout.strip(), "-O", str(SWIFT_SOURCE), "-o", str(target)],
                capture_output=True, text=True, timeout=BUILD_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"bridge compilation failed: {exc}"
        if done.returncode != 0 or not target.is_file():
            return None, "bridge compilation failed: " + (done.stderr or "").strip()[:400]
        return target, ""


def _run(prompt: str, system: str) -> dict:
    binary, why = _binary()
    if binary is None:
        return {"ok": False, "error": why}
    payload = json.dumps({"prompt": prompt, "system": system}, ensure_ascii=False)
    try:
        done = subprocess.run([str(binary)], input=payload.encode("utf-8"),
                              capture_output=True, timeout=CALL_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"bridge call failed: {exc}"}
    raw = done.stdout.decode("utf-8", "replace").strip()
    if not raw:
        return {"ok": False, "error": (done.stderr.decode("utf-8", "replace").strip() or "bridge returned nothing")[:400]}
    try:
        reply = json.loads(raw.splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "bridge output was not JSON"}
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        return {"ok": False, "error": "bridge output did not follow the contract"}
    if reply["ok"] and not isinstance(reply.get("text"), str):
        return {"ok": False, "error": "bridge reported success without text"}
    if not reply["ok"]:
        return {"ok": False, "error": str(reply.get("error") or "on-device model unavailable")}
    return reply


def available() -> bool:
    """True when the bridge compiles and the on-device model answered the probe.

    The probe is cached for the process: a missing SDK or an unavailable model
    is a property of this machine, not of the request.
    """
    global _available_cache
    if _available_cache is None:
        _available_cache = probe()["available"]
    return _available_cache


_available_cache: bool | None = None


def probe() -> dict:
    binary, why = _binary()
    if binary is None:
        return {"available": False, "reason": why}
    reply = _run("Reply with the single word: ready", "You answer with one word only.")
    if not reply.get("ok"):
        return {"available": False, "reason": reply.get("error", "on-device model unavailable")}
    return {"available": True, "reason": "", "bridge": str(binary)}


def _quote_pattern(quote: str) -> re.Pattern | None:
    """Regex for `quote` that tolerates any whitespace run, including a page break."""
    words = quote.split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(w) for w in words))


def _page_of(text: str, quote: str) -> int | None:
    """The page the quote really starts on, from the stored page-separated text.

    Page attribution comes from the match offset, so a quote that runs across a
    page break is reported on the page where it begins. No match, no page —
    never a guess.
    """
    pattern = _quote_pattern(quote)
    if pattern is None:
        return None
    found = pattern.search(text)
    if found is None:
        return None
    return text.count("\f", 0, found.start()) + 1


def _parse_reply(raw: str) -> dict | None:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate[candidate.find("{"):] if "{" in candidate else candidate
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def screen(items: list[dict]) -> dict:
    """First-pass screen of the supplied items, grounded in their stored text.

    `items` is a list of `{item_id, title, kind, text}`; `text` is the stored
    body (PDF pages separated by `\\f`). The reply is validated: every quote
    must appear in that item's own text, otherwise it is dropped and counted.
    """
    if not items:
        return {"available": False, "reason": "no items to screen", "results": [], "unverified_dropped": 0}
    unique = list(dict.fromkeys(i["item_id"] for i in items))
    by_id_all = {i["item_id"]: i for i in items}
    batch = [by_id_all[i] for i in unique[:MAX_SCREEN_ITEMS]]
    over_limit = unique[MAX_SCREEN_ITEMS:]
    readable = [i for i in batch if (i.get("text") or "").strip()]
    if not readable:
        return {"available": False, "reason": "NO_TEXT_LAYER: none of the selected items has extractable text",
                "results": [], "unverified_dropped": 0, "unread_items": [i["item_id"] for i in batch],
                "not_screened": over_limit}

    sections = []
    for item in readable:
        body = item["text"][:MAX_CHARS_PER_ITEM]
        sections.append(f"[ITEM {item['item_id']}] title: {item['title']}\n<<<\n{body}\n>>>")
    prompt = ("Screen these library items for a researcher.\n\n" + "\n\n".join(sections) +
              "\n\nReturn the JSON object now. Every quote must be copied verbatim from the text "
              "between <<< and >>> of the same item.")

    reply = _run(prompt, SYSTEM_PROMPT)
    if not reply.get("ok"):
        return {"available": False, "reason": reply.get("error", "on-device model unavailable"),
                "results": [], "unverified_dropped": 0, "not_screened": over_limit}

    parsed = _parse_reply(reply["text"])
    if parsed is None:
        return {"available": True, "reason": "", "results": [], "unverified_dropped": 0,
                "parse_failed": True, "raw_text": reply["text"][:2000], "not_screened": over_limit}

    by_id = {i["item_id"]: i for i in readable}
    results, dropped, rejected = [], 0, 0
    for entry in parsed.get("items") or []:
        if not isinstance(entry, dict):
            rejected += 1
            continue
        item_id = entry.get("item_id")
        source = by_id.get(item_id)
        if source is None:
            rejected += 1
            continue
        quote = entry.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            rejected += 1
            continue
        quote = quote.strip()[:MAX_QUOTE_CHARS]
        pattern = _quote_pattern(quote)
        if pattern is None or pattern.search(source["text"]) is None:
            dropped += 1
            continue
        relevance = entry.get("relevance")
        results.append({"item_id": item_id, "title": source["title"],
                        "relevance": relevance if relevance in ("high", "medium", "low") else "medium",
                        "quote": quote,
                        # A note has no pages, so it reports none rather than page 1.
                        "page": _page_of(source["text"], quote) if source.get("kind") == "pdf" else None,
                        "why": str(entry.get("why") or "").strip()[:400]})
    summary = parsed.get("summary")
    return {"available": True, "reason": "",
            "summary": summary.strip()[:600] if isinstance(summary, str) else "",
            "results": results, "unverified_dropped": dropped, "rejected_entries": rejected,
            "unread_items": [i["item_id"] for i in batch if i not in readable],
            "not_screened": over_limit,
            "model": "apple_on_device"}
