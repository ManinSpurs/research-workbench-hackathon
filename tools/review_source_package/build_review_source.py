#!/usr/bin/env python3
"""Assemble a path-safe source review ZIP of the current Research Workbench tree.

Reads the working tree (never git archive: current app sources are modified and
untracked), applies an allowlist of source/test/fixture areas plus an explicit
denylist, writes one versioned ZIP under deliverables/, then re-opens the ZIP and
verifies every member. Nothing outside the ZIP is written.

Usage:  python3 tools/review_source_package/build_review_source.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RB = ROOT / "current" / "rehearsal_build"
OUT_DIR = ROOT / "deliverables"
HERE = Path(__file__).resolve().parent

# (source path, archive path). Archive paths are relative and use "/" only.
SINGLE_FILES = [
    (HERE / "SOURCE_REVIEW_README.md", "SOURCE_REVIEW_README.md"),
    (ROOT / "AGENTS.md", "AGENTS.md"),
    (ROOT / "current" / "AGENTS.md", "docs/current/AGENTS.md"),
    (ROOT / "current" / "HOME_CONSOLE_UI_EN.md", "docs/current/HOME_CONSOLE_UI_EN.md"),
    (ROOT / "current" / "DESKTOP_CONSOLE_TRANSPARENCY_EN.md",
     "docs/current/DESKTOP_CONSOLE_TRANSPARENCY_EN.md"),
]

RB_TOP_FILES = [
    "README.md",
    "REPORT.md",
    "RUN_LOG.md",
    "DOT_VERIFICATION.md",
    "EVALUATION_PROTOCOL.md",
    "README_DOT.md",
    "ORIGINAL_README.md",
    "CHANGELOG_DOT.json",
    "compare_report.json",
    "sample_dictionary.json",
    "package.json",
    "package-lock.json",
    "requirements.txt",
    "requirements.review.lock",
    "run_mock.py",
    "run_click.sh",
    "Start_Mock.command",
    "Start_Modal_Shared.command",
]

# Trees shipped in full. data/ carries the synthetic FX fixture plus the four
# verified public NYC test fixtures; anything else under data/ is out of scope.
RB_TREES = ["app", "frontend", "electron", "tests", "tools", "data"]
# Shipped only when present: the DMG packaging sources are source-only (scripts,
# declarations, manifests); build outputs, binaries and vendored runtimes are not.
OPTIONAL_TREES = ["packaging"]
# Data allowlist: the synthetic fixture tree plus the four reviewed public
# agency test fixtures. Lead verification: both CSVs match SNAPSHOT.json SHA-256
# and row counts, columns limited to job/permit ids, dates, status and borough
# (no names, phones or addresses); sources.json holds dictionary metadata only.
# dev_project/ is the authored seed for the in-app development project: source
# documents in data/dev_project/README.md, verified public-safe by the exclusion
# greps in tests/test_seed_dev_project.py.
DATA_ALLOW_PREFIXES = ("fx/", "dev_project/")
DATA_ALLOW_EXACT = {
    "data/nyc/SNAPSHOT.json",
    "data/nyc/sources.json",
    "data/nyc/bis_2024_subset.csv",
    "data/nyc/dobnow_2024_subset.csv",
}

SKIP_DIR_NAMES = {
    "__pycache__", "node_modules", ".venv", ".git", ".pytest_cache", "tmp",
    "logs", ".mypy_cache", ".ruff_cache", "site-packages", ".idea", ".vscode",
}
SKIP_DIR_SUFFIX = (".egg-info",)
# Packaging sources only: never build outputs, bundles or vendored runtimes.
PACKAGING_SKIP_DIRS = {
    "dist", "build", "out", "output", "release", "releases", "stage", "staging",
    "vendor", "vendored", "resources", "cache", "caches", "frameworks", "Runtime",
    "electron_dist", "app_bundle", "outputs", "artifacts", "node_modules",
}
PACKAGING_SKIP_SUFFIXES = (
    ".app", ".dmg", ".pkg", ".exe", ".dll", ".dylib", ".so", ".a", ".o", ".woff2",
    ".asar", ".blockmap", ".icns", ".ico", ".bin", ".node", ".whl", ".tar", ".gz",
)
SKIP_FILE_NAMES = {".DS_Store", ".gitignore", ".env", ".env.local", "Thumbs.db"}
SKIP_FILE_SUFFIXES = (
    ".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite",
    ".sqlite3", ".key", ".pem", ".p12", ".pfx", ".gguf", ".safetensors", ".ckpt",
    ".onnx", ".dmg", ".pkg", ".zip", ".bak-before-cleanup", ".log", ".so", ".dylib",
)
# Compiled on demand from main.swift; never shipped as a binary.
SKIP_REL_PREFIXES = ("tools/apple_fm_bridge/bin/",)
# Generated run leftovers that a suffix rule does not catch.
SKIP_REL_EXACT = {
    "tests/phase1-project.json",
    "tests/probe_tmp_scratch.py.disabled",
    "FILES_SHA256.json",
}
# Credential-store filenames only. Ordinary source modules whose name merely
# contains "secret" or "token" are application code and stay in scope.
SKIP_NAME_RE = re.compile(r"^(id_rsa|id_dsa|id_ecdsa|id_ed25519|\.netrc|credentials|\.htpasswd)", re.I)

MAX_FILE_BYTES = 8 * 1024 * 1024
REQUIRED_MEMBERS = [
    "SOURCE_REVIEW_README.md",
    "AGENTS.md",
    "docs/current/AGENTS.md",
    "docs/current/HOME_CONSOLE_UI_EN.md",
    "rehearsal_build/app/api.py",
    "rehearsal_build/app/harness.py",
    "rehearsal_build/app/latex_compile.py",
    "rehearsal_build/app/list_order.py",
    "rehearsal_build/packaging/backend_server.py",
    "rehearsal_build/packaging/electron_main.js",
    "rehearsal_build/tools/build_review_dmg.py",
    "rehearsal_build/app/ai/provider.py",
    "rehearsal_build/app/ai/service.py",
    "rehearsal_build/app/ai/modal_shared_config.py",
    "rehearsal_build/app/ai/receipt.py",
    "rehearsal_build/frontend/index.html",
    "rehearsal_build/frontend/app.js",
    "rehearsal_build/frontend/declutter_shell.js",
    "rehearsal_build/frontend/interior_settings.js",
    "rehearsal_build/frontend/assets/Console_Concept_A.png",
    "rehearsal_build/electron/main.js",
    "rehearsal_build/electron/preload.js",
    "rehearsal_build/tests/test_modal_shared_route.py",
    "rehearsal_build/tests/test_modal_deployment.py",
    "rehearsal_build/tests/test_modal_shared_receipt_errors.py",
    "rehearsal_build/tests/review_modal_shared_route.mjs",
    "rehearsal_build/tests/review_desktop_unsaved_close.mjs",
    "rehearsal_build/tests/review_project_order.mjs",
    "rehearsal_build/tests/conftest.py",
    "rehearsal_build/tools/verify_all_local.py",
    "rehearsal_build/tools/preview_desktop_app.mjs",
    "rehearsal_build/tools/modal_glimmer_server.py",
    "rehearsal_build/data/fx/permits_2023.csv",
    "rehearsal_build/data/fx/sources.json",
    "rehearsal_build/data/nyc/SNAPSHOT.json",
    "rehearsal_build/data/nyc/sources.json",
    "rehearsal_build/data/nyc/bis_2024_subset.csv",
    "rehearsal_build/data/nyc/dobnow_2024_subset.csv",
    "rehearsal_build/package.json",
    "rehearsal_build/package-lock.json",
    "rehearsal_build/requirements.txt",
    "rehearsal_build/requirements.review.lock",
    "rehearsal_build/Start_Mock.command",
    "rehearsal_build/Start_Modal_Shared.command",
    "rehearsal_build/run_mock.py",
]
# Token shapes that must never appear in a shipped source tree. Matches are
# reported by path and pattern only; values are never printed.
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|wk-[A-Za-z0-9_-]{12,}\.ws-[A-Za-z0-9_-]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.)"
)
TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".json", ".md", ".txt", ".css", ".html", ".sh",
    ".command", ".csv", ".swift", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".lock",
}

FIXED_MTIME = (2026, 10, 29, 0, 0, 0)
FIXED_MODE = 0o644


def skip_file(name: str, rel: str) -> str | None:
    if name in SKIP_FILE_NAMES or name.startswith(".env"):
        return "env-or-os-file"
    if name.lower().endswith(SKIP_FILE_SUFFIXES):
        return "suffix"
    if SKIP_NAME_RE.search(name):
        return "secret-shaped-name"
    if any(rel.startswith(p) for p in SKIP_REL_PREFIXES):
        return "generated-binary"
    return None


def data_allowed(rel_under_rb: str) -> bool:
    """True for the synthetic FX tree, the reviewed fixtures and the dev project seed."""
    if rel_under_rb in DATA_ALLOW_EXACT:
        return True
    return any(rel_under_rb.startswith(f"data/{p}") for p in DATA_ALLOW_PREFIXES)


def collect() -> tuple[list[tuple[Path, str]], list[tuple[str, str]]]:
    members: list[tuple[Path, str]] = []
    skipped: list[tuple[str, str]] = []
    for src, arc in SINGLE_FILES:
        if not src.is_file():
            raise SystemExit(f"missing required file: {src}")
        members.append((src, arc))
    for name in RB_TOP_FILES:
        src = RB / name
        if not src.is_file():
            raise SystemExit(f"missing required file: {src}")
        members.append((src, f"rehearsal_build/{name}"))
    for tree in RB_TREES + [t for t in OPTIONAL_TREES if (RB / t).is_dir()]:
        base = RB / tree
        if not base.is_dir():
            if tree in OPTIONAL_TREES:
                continue
            raise SystemExit(f"missing required tree: {base}")
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            here = Path(dirpath)
            skip_dirs = SKIP_DIR_NAMES | (PACKAGING_SKIP_DIRS if tree == "packaging" else set())
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in skip_dirs and not d.endswith(SKIP_DIR_SUFFIX)
                and not (here / d).is_symlink()
            )
            for name in sorted(filenames):
                src = here / name
                rel_under_rb = src.relative_to(RB).as_posix()
                if tree == "data" and not data_allowed(rel_under_rb):
                    skipped.append((rel_under_rb, "non-fixture-data"))
                    continue
                if tree == "packaging" and name.lower().endswith(PACKAGING_SKIP_SUFFIXES):
                    skipped.append((rel_under_rb, "packaging-build-output"))
                    continue
                if rel_under_rb in SKIP_REL_EXACT:
                    skipped.append((rel_under_rb, "generated-leftover"))
                    continue
                if src.is_symlink():
                    skipped.append((rel_under_rb, "symlink"))
                    continue
                reason = skip_file(name, rel_under_rb)
                if reason:
                    skipped.append((rel_under_rb, reason))
                    continue
                members.append((src, f"rehearsal_build/{rel_under_rb}"))
    return members, skipped


def build(members: list[tuple[Path, str]], out: Path) -> dict:
    total = 0
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in sorted(members, key=lambda m: m[1]):
            size = src.stat().st_size
            if size > MAX_FILE_BYTES:
                raise SystemExit(f"unexpectedly large file, refusing: {arc} ({size} bytes)")
            info = zipfile.ZipInfo(arc, date_time=FIXED_MTIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | FIXED_MODE) << 16
            if os.access(src, os.X_OK) and src.suffix in {".sh", ".command", ".py"}:
                info.external_attr = (0o100000 | 0o755) << 16
            data = src.read_bytes()
            zf.writestr(info, data)
            total += len(data)
    return {"files": len(members), "uncompressed_bytes": total}


def verify(out: Path, members: list[tuple[Path, str]]) -> dict:
    problems: list[str] = []
    expected = {arc for _, arc in members}
    names: list[str] = []
    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
        if bad:
            problems.append(f"corrupt member: {bad}")
        for item in zf.infolist():
            name = item.filename
            names.append(name)
            if name.startswith("/") or name.startswith("\\"):
                problems.append(f"absolute member path: {name}")
            parts = name.split("/")
            if any(p in ("", ".", "..") for p in parts):
                problems.append(f"unsafe member path: {name}")
            if "\\" in name:
                problems.append(f"backslash in member path: {name}")
            if item.create_system == 3 and (item.external_attr >> 16) & 0o170000 == 0o120000:
                problems.append(f"symlink member: {name}")
            if item.file_size > MAX_FILE_BYTES:
                problems.append(f"oversized member: {name}")
        if len(names) != len(set(names)):
            problems.append("duplicate member names")
        missing = sorted(a for a in expected if a not in set(names))
        if missing:
            problems.append(f"missing expected members: {missing[:5]}")
        required = [n for n in REQUIRED_MEMBERS if n in expected]
        missing_required = [n for n in REQUIRED_MEMBERS if n not in set(names)]
        if missing_required:
            problems.append(f"required members absent from archive: {missing_required[:5]}")
        if len(required) != len(REQUIRED_MEMBERS):
            problems.append(f"required members not collected: {len(required)}/{len(REQUIRED_MEMBERS)}")
        forbidden = [n for n in names if not n.startswith(("rehearsal_build/", "docs/current/"))
                     and n not in {"SOURCE_REVIEW_README.md", "AGENTS.md"}]
        if forbidden:
            problems.append(f"member outside the allowlisted roots: {forbidden[:5]}")
        out_of_scope = []
        for name in names:
            if not name.startswith("rehearsal_build/"):
                continue
            rel = name[len("rehearsal_build/"):]
            if rel.startswith(("chips/", "docs/", "tmp/", "logs/")):
                out_of_scope.append(name)
            elif rel.startswith("data/") and not data_allowed(rel):
                out_of_scope.append(name)
            elif rel.startswith("packaging/") and (
                name.lower().endswith(PACKAGING_SKIP_SUFFIXES)
                or any(part in PACKAGING_SKIP_DIRS for part in rel.split("/")[:-1])
            ):
                out_of_scope.append(name)
        if out_of_scope:
            problems.append(f"out-of-scope members: {out_of_scope[:5]}")
        mutated = []
        for src, arc in members:
            if arc not in set(names):
                continue
            if hashlib.sha256(src.read_bytes()).hexdigest() != hashlib.sha256(zf.read(arc)).hexdigest():
                mutated.append(arc)
        if mutated:
            problems.append(f"members not byte-identical to source: {mutated[:5]}")
        hits = []
        scanned = 0
        for item in zf.infolist():
            if Path(item.filename).suffix.lower() not in TEXT_SUFFIXES:
                continue
            if item.file_size > MAX_FILE_BYTES:
                continue
            raw = zf.read(item.filename)
            scanned += 1
            if SECRET_RE.search(raw.decode("utf-8", "ignore")):
                hits.append(item.filename)
    if hits:
        problems.append(f"token-shaped content in: {hits[:5]}")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "members": len(names),
        "scanned_text_members": scanned,
        "sha256": digest,
        "problems": problems,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the source review ZIP.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    members, skipped = collect()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"RESEARCH_WORKBENCH_source_review_{stamp}.zip"
    out = OUT_DIR / name
    print(f"root: {ROOT}")
    print(f"members: {len(members)}  skipped: {len(skipped)}")
    for rel, reason in skipped[:25]:
        print(f"  skip {reason}: {rel}")
    if len(skipped) > 25:
        print(f"  ... {len(skipped) - 25} more skipped entries")
    if args.dry_run:
        for _, arc in sorted(members, key=lambda m: m[1])[:400]:
            print(f"  {arc}")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"refusing to overwrite an existing archive: {out}")
    stats = build(members, out)
    report = verify(out, members)
    size = out.stat().st_size
    print(f"archive: {out}")
    print(f"files: {stats['files']}  uncompressed: {stats['uncompressed_bytes']} bytes"
          f"  compressed: {size} bytes")
    print(f"sha256: {report['sha256']}")
    print(f"verified members: {report['members']}  text members scanned: {report['scanned_text_members']}")
    if report["problems"]:
        print("PROBLEMS:")
        for problem in report["problems"]:
            print(f"  {problem}")
        return 1
    print("VERIFY_OK: no unsafe paths, no symlinks, no missing required members, no secret-shaped content")
    return 0


if __name__ == "__main__":
    sys.exit(main())
