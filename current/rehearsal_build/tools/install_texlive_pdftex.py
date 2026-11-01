"""Bounded, checksum-pinned TinyTeX install for a real local pdfLaTeX. No sudo, no PATH edits.

What this does and does not touch
--------------------------------
Downloads ONE pinned GitHub release asset (TinyTeX ``scheme-infraonly`` + the ~100
packages that make a real pdfLaTeX work: pdftex, ``latex-bin``, ``cm``/``amsfonts``
fonts, the prebuilt ``pdflatex.fmt`` format, ``latexmk``, ``tlmgr``), verifies its
published SHA-256 and byte size, then extracts it under ``--target``.

It never runs an upstream install script, never pipes a download into a shell,
never writes outside ``--target``, never edits shell profiles or system PATH, and
refuses to run under ``sudo``. Uninstalling is ``rm -rf`` of that one folder.

Why the default target is /opt/homebrew/opt
-------------------------------------------
``app/latex_compile.py:_work`` builds its sandbox profile with an explicit read
allowlist -- ``/System``, ``/usr``, ``/Library/TeX``, ``/opt/homebrew`` -- plus the
per-job temp root. Two consequences were measured on this host (macOS 27.0.1,
arm64) before this script was written:

* A TeX tree at any *non-ASCII* path cannot be exec'd under ``sandbox-exec``
  (``execvp: Operation not permitted``), even though the same tree compiles fine
  without the sandbox. The Project root here contains Hangul (``연구``), so a
  project-local install is not reachable by the current sandbox at all.
* A TeX tree outside the allowlist is simply unreadable to the job.

``/opt/homebrew`` is user-owned (no sudo), ASCII, already allowlisted, and
deletable -- so it needs no extra grant. ``--target`` still exists for a tree you
want somewhere else, but then the backend's read allowlist must name that root.

This script installs nothing itself unless you run it; it also does not register
the engine with the app. Pass the printed bin directory as ``PATH`` (or export it
in the shell you launch the backend from) so ``shutil.which`` finds ``pdflatex``.

Usage (system python3 is fine; no third-party imports):

    python3 tools/install_texlive_pdftex.py --dry-run
    python3 tools/install_texlive_pdftex.py
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# Pinned release: rstudio/tinytex-releases v2026.10 (TeX Live 2026), asset digest as
# published in the GitHub release API. TinyTeX is a packaged subset of TeX Live,
# redistributed under GPL-2 / the TeX Live copying conditions (see LICENSE.TL and
# LICENSE.CTAN inside the archive).
VERSION = "2026.10"
ASSET = "TinyTeX-1-darwin-v2026.10.tar.xz"
URL = ("https://github.com/rstudio/tinytex-releases/releases/download/"
       "v2026.10/TinyTeX-1-darwin-v2026.10.tar.xz")
SHA256 = "e40c294efae81026d8cc8d12c09d2dd456d268efe7fe092410478bc1973fd40f"
SIZE = 67189208  # bytes, as published
DEFAULT_TARGET = Path("/opt/homebrew/opt/texlive-tinytex")
# Layout inside the archive is a single top-level "TinyTeX/" directory; the
# binaries live in bin/universal-darwin (a universal x86_64+arm64 Mach-O set).
BINDIR = Path("bin") / "universal-darwin"
ENGINES = ("pdflatex", "xelatex", "lualatex", "latexmk")
MAX_BYTES = SIZE + (1 << 20)  # refuse anything materially larger than the pin


def _fail(message):
    print("error: " + message, file=sys.stderr)
    raise SystemExit(1)


def _check_target(target):
    text = str(target)
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        # Verified on this host: sandbox-exec cannot exec a binary under a
        # non-ASCII path, so such an install would be silently unusable.
        _fail("target path must be ASCII-only (macOS sandbox-exec cannot exec "
              "binaries under non-ASCII paths): " + text)
    if target.exists() and any(target.iterdir()):
        _fail("target already exists and is not empty: " + text)
    if not target.parent.exists():
        _fail("target parent does not exist: " + str(target.parent))
    if not os.access(str(target.parent), os.W_OK):
        _fail("target parent is not writable by this user: " + str(target.parent))


def _download(destination):
    print("downloading %s (%d bytes)" % (ASSET, SIZE))
    digest = hashlib.sha256()
    read = 0
    with urllib.request.urlopen(URL, timeout=120) as response, destination.open("wb") as sink:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            read += len(chunk)
            if read > MAX_BYTES:
                _fail("download exceeded the pinned size; aborting")
            digest.update(chunk)
            sink.write(chunk)
    if read != SIZE:
        _fail("size mismatch: got %d bytes, expected %d" % (read, SIZE))
    if digest.hexdigest() != SHA256:
        _fail("sha256 mismatch: got %s, expected %s" % (digest.hexdigest(), SHA256))
    print("sha256 verified: " + SHA256)


def _extract(archive, target):
    # The archive is a single "TinyTeX/" tree. Refuse anything that would land
    # outside the target (absolute names or parent traversal).
    with tarfile.open(str(archive), "r:xz") as tar:
        for member in tar.getmembers():
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                _fail("refusing unsafe archive member: " + name)
        tar.extractall(str(target))
    root = target / "TinyTeX"
    if not (root / BINDIR / "pdflatex").exists():
        _fail("archive did not contain TinyTeX/%s/pdflatex" % BINDIR)
    return root


def _report(root):
    bindir = root / BINDIR
    version = subprocess.run([str(bindir / "pdflatex"), "--version"],
                             capture_output=True, text=True, timeout=60)
    first = (version.stdout or "").splitlines()
    print("\ninstalled at: " + str(root))
    print("engine: " + (first[0] if first else "(pdflatex --version printed nothing)"))
    print("pdflatex path that the app's shutil.which() must find:")
    print("  " + str(bindir / "pdflatex"))
    print("PATH argument for the backend/app process (prepend, do not replace):")
    print("  " + str(bindir))
    print("\nFor the sandbox to exec this engine, app/latex_compile.py's profile also "
          "needs (literal \"/\") added to its (allow file-read* ...) form; that is a "
          "backend change and is not made by this script.")
    print("Engines present in this bundle:")
    for name in ENGINES:
        print("  %-9s %s" % (name, "yes" if (bindir / name).exists() else "no"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=str(DEFAULT_TARGET),
                        help="ASCII-only install root (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the pinned artifact and target, change nothing")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().absolute()
    print("TinyTeX %s (%s)" % (VERSION, ASSET))
    print("url:    " + URL)
    print("size:   %d bytes" % SIZE)
    print("sha256: " + SHA256)
    print("target: " + str(target))
    print("bin:    " + str(target / "TinyTeX" / BINDIR))
    if args.dry_run:
        _check_target(target)
        print("dry run: nothing downloaded, nothing written")
        return 0

    if os.geteuid() == 0:
        _fail("do not run this with sudo/root; the install must stay user-owned")
    _check_target(target)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tinytex-dl-", dir=str(target.parent)) as work:
        archive = Path(work) / ASSET
        _download(archive)
        root = _extract(archive, target)
    _report(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
