#!/usr/bin/env python3
"""Build the macOS arm64 review DMG for Research Workbench.

Assembles a self-contained .app from the existing sources in this folder:

  <app>/Contents/Resources/app   Electron shell: package.json, main.js, menu.js,
                                 preload.js (the shipped menu/preload are reused
                                 byte for byte; main.js is packaging/electron_main.js)
  <app>/Contents/Resources/wb    runtime/python (CPython, statically usable
                                 without the build machine), runtime/site-packages
                                 (the app's own dependencies), app/, frontend/,
                                 data/ (sample fixtures), chips/builtin/, packaging/,
                                 tex/TinyTeX (portable pdfLaTeX)

Why a CPython runtime and not PyInstaller: app/latex_compile.py runs the bounded
wrapper as ``[sys.executable, "-c", LIMIT_EXEC, "sandbox-exec", ...]``. A frozen
PyInstaller binary has no ``-c`` mode, so the resource-limited sandbox launcher
would fail and PDF compilation would silently degrade to "toolchain failed". A
real interpreter keeps the shipped sandbox policy intact, so PDF is bundled for
real instead of being faked or declared unavailable.

Nothing is signed with a developer identity: the bundle is ad-hoc signed only, so
macOS shows an unidentified-developer warning on another machine.

Usage: python3 tools/build_review_dmg.py [--skip-tex] [--keep-staging]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
PROJECT = SOURCE.parent.parent
DELIVERABLES = PROJECT / "deliverables"

CACHE = Path.home() / "Library" / "Caches" / "ResearchWorkbenchBuild"
PY_TAG = "20261026"
PY_VERSION = "3.14.10"
PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
          f"{PY_TAG}/cpython-{PY_VERSION}+{PY_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz")

APP_NAME = "Research Workbench Review"
BUNDLE_ID = "local.review.research-workbench"
TEX_ROOT = Path("/opt/homebrew/opt/texlive-tinytex/TinyTeX")

# Site-packages kept out of the bundle: build and test tooling only. Every
# runtime dependency stays, because the app's import graph is wide (FastAPI,
# pydantic-ai and their transitive packages) and a review build must not trade
# correctness for a few megabytes.
EXCLUDED_PACKAGES = {
    "pip", "pip-*", "pytest", "_pytest", "playwright", "pyinstaller",
    "iniconfig*", "pluggy*", "greenlet*",
}


def run(argv, cwd=None, env=None, check=True):
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(argv)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def cached_download(url, target):
    CACHE.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 1_000_000:
        return target
    print(f"downloading {url}")
    partial = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    partial.replace(target)
    return target


def stage_python(staging):
    runtime = staging / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    archive = cached_download(PY_URL, CACHE / Path(PY_URL).name)
    with tarfile.open(archive) as tar:
        tar.extractall(runtime)  # trusted: the pinned python-build-standalone archive
    interpreter = runtime / "python" / "bin" / "python3.14"
    if not interpreter.is_file():
        raise RuntimeError("staged CPython interpreter is missing")
    return interpreter


def stage_site_packages(interpreter, staging):
    """Copy the app's dependencies, not the whole dev environment."""
    source = SOURCE / ".venv" / "lib" / "python3.14" / "site-packages"
    if not source.is_dir():
        raise RuntimeError(f"missing dependency tree: {source}")
    target = staging / "runtime" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    skipped = []
    for entry in sorted(source.iterdir()):
        name = entry.name
        if name == "__pycache__" or name.endswith(".pyc"):
            continue
        if any(_matches(name, pattern) for pattern in EXCLUDED_PACKAGES):
            skipped.append(name)
            continue
        if entry.is_dir():
            shutil.copytree(entry, target / name, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
        else:
            shutil.copy2(entry, target / name)
    return target, skipped


def _matches(name, pattern):
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def stage_app_sources(staging):
    for name in ("app", "frontend", "data", "chips"):
        shutil.copytree(SOURCE / name, staging / name, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "installed"))
    shutil.copytree(SOURCE / "packaging", staging / "packaging", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # A review build carries no user material: sample fixtures only.
    (staging / "data" / "library").mkdir(parents=True, exist_ok=True)


def stage_tex(staging):
    if not TEX_ROOT.is_dir():
        return None
    target = staging / "tex" / "TinyTeX"
    shutil.copytree(TEX_ROOT, target, symlinks=True,
                    ignore=shutil.ignore_patterns("*.x86_64-darwin", "man"))
    return target


def _rename_helpers(contents):
    """Give the Electron helper apps this bundle's own identity.

    Electron locates them by the main bundle name, so each helper app, its
    directory, its executable and its Info.plist must all agree on
    "<App Name> Helper[ (GPU|Plugin|Renderer)]".
    """
    frameworks = contents / "Frameworks"
    for helper in sorted(frameworks.iterdir()):
        if not helper.name.startswith("Electron Helper"):
            continue
        suffix = helper.name[len("Electron Helper"):]
        if suffix.endswith(".app"):
            suffix = suffix[:-len(".app")]
        bundle_name = f"{APP_NAME} Helper{suffix}"
        target = frameworks / f"{bundle_name}.app"
        helper.rename(target)
        macos = target / "Contents" / "MacOS"
        for candidate in list(macos.iterdir()):
            if candidate.name.startswith("Electron Helper"):
                candidate.rename(macos / bundle_name)
        plist = target / "Contents" / "Info.plist"
        with plist.open("rb") as handle:
            data = plistlib.load(handle)
        part = suffix.strip(" ()").lower().replace(" ", "-") or "core"
        data.update({
            "CFBundleExecutable": bundle_name,
            "CFBundleIdentifier": f"{BUNDLE_ID}.helper.{part}",
            "CFBundleDisplayName": bundle_name,
            "CFBundleName": bundle_name,
        })
        with plist.open("wb") as handle:
            plistlib.dump(data, handle)


def assemble_app(staging, interpreter, tex_included, version):
    build_dir = staging / "build"
    destination_app = build_dir / f"{APP_NAME}.app"
    electron_src = SOURCE / "node_modules" / "electron" / "dist" / "Electron.app"
    if not electron_src.is_dir():
        raise RuntimeError("node_modules/electron/dist/Electron.app is missing")
    shutil.copytree(electron_src, destination_app, symlinks=True)
    contents = destination_app / "Contents"
    resources = contents / "Resources"

    (contents / "MacOS" / "Electron").rename(contents / "MacOS" / APP_NAME)
    # The stock default app would let Electron start a second, unrelated window
    # if anything opened the frame without our package.json.
    default_asar = resources / "default_app.asar"
    if default_asar.exists():
        default_asar.unlink()
    code_signature = contents / "_CodeSignature"
    if code_signature.exists():
        shutil.rmtree(code_signature)
    _rename_helpers(contents)

    # App package: the packaged entry starts the bundled backend; menu.js and
    # preload.js are the shipped files, unchanged.
    app_resources = resources / "app"
    app_resources.mkdir(parents=True)
    shutil.copy2(SOURCE / "electron" / "menu.js", app_resources / "menu.js")
    shutil.copy2(SOURCE / "electron" / "preload.js", app_resources / "preload.js")
    shutil.copy2(SOURCE / "packaging" / "electron_main.js", app_resources / "main.js")
    (app_resources / "package.json").write_text(json.dumps({
        "name": "research-workbench-review",
        "version": version,
        "main": "main.js",
        "private": True,
    }, indent=2) + "\n")

    # Backend payload.
    wb_resources = resources / "wb"
    wb_resources.mkdir(parents=True)
    for name in ("app", "frontend", "data", "chips", "packaging"):
        shutil.copytree(staging / name, wb_resources / name, symlinks=True)
    (wb_resources / "runtime").mkdir()
    (staging / "runtime" / "python").rename(wb_resources / "runtime" / "python")
    (staging / "runtime" / "site-packages").rename(wb_resources / "runtime" / "site-packages")
    if tex_included:
        (staging / "tex").rename(wb_resources / "tex")

    plist = contents / "Info.plist"
    with plist.open("rb") as handle:
        data = plistlib.load(handle)
    data.update({
        "CFBundleDisplayName": APP_NAME,
        "CFBundleName": APP_NAME,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "NSHumanReadableCopyright": "Research Workbench. Local-first; cloud models run only on your own key.",
    })
    # The integrity record references default_app.asar, which this bundle omits.
    data.pop("ElectronAsarIntegrity", None)
    with plist.open("wb") as handle:
        plistlib.dump(data, handle)
    return destination_app


def adhoc_sign(app_bundle):
    """Ad-hoc signing only: this is an inspection build, not a distribution one."""
    for path in sorted((app_bundle / "Contents" / "Resources" / "wb").rglob("*"), reverse=True):
        if path.is_file() and os.access(path, os.X_OK) and _is_macho(path):
            run(["codesign", "--force", "--sign", "-", str(path)], check=False)
    run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=False)
    verify = run(["codesign", "--verify", "--deep", "--strict", str(app_bundle)], check=False)
    return verify.returncode == 0


def _is_macho(path):
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def _post_json(opener, url, payload, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with opener.open(request, timeout=120) as response:
        return json.loads(response.read().decode())


def _verify_local_modules(interpreter, wb_root, environment):
    """Prove the staged bundle ships the optional local runtime, without a model.

    Two checks through the staged interpreter, exactly as the app would import
    them: the modules import at all, and the watchdog's own entry point answers
    its documented usage exit code (2) for bad arguments — reached through
    ``reaper_command``, the argv the launcher really spawns, so the bootstrap
    string is proven to load the module from this bundle's own app root. Only the
    argv-parsing path runs: no process is armed, no pid is signalled, no weight
    file is opened and the configuration is the explicitly absent one, so this
    cannot need a model or leave anything running.
    """
    staged = os.environ.get("PYTHONNOUSERSITE")
    child_env = dict(environment, PYTHONNOUSERSITE=staged or "1")
    child_env["PYTHONPATH"] = str(wb_root)
    probe = (
        "import subprocess, sys\n"
        "from app.ai import local_launcher, local_reaper\n"
        "# The argv the launcher spawns: interpreter, -c, bootstrap, pids, period.\n"
        "argv = local_reaper.reaper_command(1, 1)\n"
        "if argv[0] != sys.executable or argv[1] != '-c':\n"
        "    print('reaper_command does not start with this interpreter and -c')\n"
        "    sys.exit(71)\n"
        "if str(%r) not in argv[2]:\n"
        "    print('reaper_command does not load the watchdog from this bundle')\n"
        "    sys.exit(72)\n"
        "# Run it once with a usage error: no pids, so nothing can be watched.\n"
        "usage = subprocess.run(argv[:3] + ['only-one-argument'],\n"
        "                       capture_output=True, text=True, timeout=60)\n"
        "if usage.returncode != 2:\n"
        "    print('watchdog usage exit was %%s, expected 2: %%s'\n"
        "          %% (usage.returncode, (usage.stderr or usage.stdout).strip()[:500]))\n"
        "    sys.exit(73)\n"
        "sys.exit(0)\n") % (str(wb_root),)
    result = subprocess.run([str(interpreter), "-c", probe], cwd=str(wb_root),
                            env=child_env, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            "the staged bundle is missing a working optional local runtime "
            f"(import app.ai.local_launcher, app.ai.local_reaper and the watchdog's "
            f"usage exit code): exit {result.returncode}; "
            f"{(result.stderr or result.stdout or '').strip()[:2000]}")
    return {"import_ok": True, "reaper_usage_exit": 2}


def verify_staged_runtime(wb_root, skip_tex):
    """Start the backend from the staged app and query it, before the DMG exists."""
    interpreter = wb_root / "runtime" / "python" / "bin" / "python3.14"
    workspace = Path(tempfile.mkdtemp(prefix="wb-review-verify-"))
    ready = workspace / "ready.json"
    environment = {k: v for k, v in os.environ.items()
                   if k not in {"FAKE_BASE_URL", "WB_ALLOW_REAL_MUSE", "WB_DB"}}
    environment["PYTHONNOUSERSITE"] = "1"
    # These checks import the staged app in place; bytecode written there would
    # ship inside the DMG carrying the builder's absolute paths.
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # The local model is OPTIONAL and is never bundled. This check therefore runs
    # the staged backend against an explicitly absent configuration, so it proves
    # the "no model configured" path works here regardless of whether this build
    # machine happens to have a model installed, and never loads one: the check
    # must not require a model, must not start one and must not leave one running.
    # The app's own use of an installed model is verified by launching the app.
    environment["WB_LOCAL_CONFIG"] = str(workspace / "no-local-runtime-configured.json")
    # Prove the credential isolation rather than assume it: hand the staged
    # backend a set of obviously fake credential variables, exactly as an owner's
    # shell might export them. They are never used for a call (this is a review
    # build with no cloud route reachable), and the checks below require the app
    # to report every one of them as absent.
    for name in ("META_API_KEY", "MODEL_API_KEY", "BASETEN_API_KEY", "OPENAI_API_KEY",
                 "WB_MODAL_SHARED_API_KEY"):
        environment[name] = "build-verification-fake-value-not-a-credential"
    # The optional local runtime must be *shipped*, not merely tolerated: a stage
    # that dropped app/ai/local_launcher.py or local_reaper.py would still start
    # with local mode unavailable, so the absence would look like an honest
    # "not configured" and the local feature would be missing from the DMG. Both
    # modules are imported through the staged interpreter, and the watchdog is
    # run once with bad arguments to prove its documented usage exit code. Neither
    # touches a model, a weight file, the network or the configuration above.
    checks = {"local_modules": _verify_local_modules(interpreter, wb_root, environment)}
    child = subprocess.Popen([str(interpreter), str(wb_root / "packaging" / "backend_server.py"),
                              "--state-dir", str(workspace / "state"),
                              "--ready-file", str(ready),
                              "--parent-pid", str(os.getpid())],
                             cwd=str(wb_root / "packaging"), env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 120
        info = None
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("staged backend exited: " + (child.stdout.read() if child.stdout else ""))
            if ready.is_file():
                info = json.loads(ready.read_text())
                break
            time.sleep(0.2)
        if not info:
            raise RuntimeError("staged backend did not become ready")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(info["api"] + "/projects", timeout=30) as response:
            checks["projects"] = json.loads(response.read().decode())
        with opener.open(info["api"] + "/ai/registry", timeout=30) as response:
            registry = json.loads(response.read().decode())
            settings = registry.get("settings") or {}
            checks["providers"] = sorted(str(row.get("id")) for row in registry.get("providers", []) if isinstance(row, dict))
            local_row = next((row for row in registry.get("models", [])
                              if isinstance(row, dict) and str(row.get("id", "")).startswith("local:")), None)
            checks["local_model_row"] = {
                "endpoint": (local_row or {}).get("endpoint"),
                "status": (local_row or {}).get("status"),
            }
            # A review session must inherit no credential from the build machine.
            # Each flag reads one provider's environment override first, so a
            # True here means an exported build-machine key was inherited.
            checks["inherited_credentials"] = {
                "meta_key_configured": settings.get("key_configured"),
                "baseten_key_configured": settings.get("baseten_key_configured"),
                "openai_key_configured": settings.get("openai_key_configured"),
                "modal_shared_key_present": (settings.get("modal_shared_connection") or {}).get("key_present"),
            }
            checks["cloud_enabled"] = settings.get("cloud_enabled")
        # Fake credential variables were exported into the staged backend's
        # environment above; none of them may have reached a credential reader.
        leaked = {name: value for name, value in checks["inherited_credentials"].items()
                  if value is True}
        if leaked:
            raise RuntimeError(f"staged backend inherited a machine credential: {leaked}")
        identity = info["identity"]
        local = identity.get("local_runtime") or {}
        checks["local_runtime"] = {
            "available": local.get("available"),
            "status": local.get("status"),
            "model_bundled": identity.get("model_bundled"),
            "cloud_ai": identity.get("cloud_ai"),
            "live_ai": identity.get("live_ai"),
        }
        # The unconfigured path must be honest and must not advertise a model.
        if local.get("available") is not False or checks["local_model_row"]["endpoint"] is not None:
            raise RuntimeError(f"staged backend advertised a local model with none configured: "
                               f"{checks['local_runtime']} {checks['local_model_row']}")
        if identity.get("live_ai") is not False or identity.get("cloud_ai") is not False:
            raise RuntimeError(f"review identity claims live or cloud AI: {identity}")
        # Nothing in this bundle may carry the model or its credential.
        bundled = [str(path.relative_to(wb_root)) for path in wb_root.rglob("*.gguf")]
        if bundled:
            raise RuntimeError(f"the review bundle contains model weights: {bundled}")
        with opener.open(info["frontend"] + "/index.html", timeout=30) as response:
            checks["frontend_status"] = response.status
            checks["frontend_bytes"] = len(response.read())
        # Real PDF through the bundled TeX under the shipped sandbox policy.
        project = checks["projects"]["projects"][0]["id"]
        if not skip_tex:
            draft = _post_json(opener, info["api"] + f"/projects/{project}/drafts", {
                "title": "Build verification",
                "import": {"name": "main.tex",
                           "text": "\\documentclass{article}\\begin{document}"
                                   "Review build PDF check.\\end{document}"}})
            compiled = _post_json(opener, info["api"] + f"/drafts/{draft['draft_id']}/compile",
                                  {"source": "manual"}, origin=info["frontend"])
            checks["pdf_compile_status"] = compiled.get("status")
            with opener.open(info["api"] + f"/drafts/{draft['draft_id']}/pdf?revision=0", timeout=30) as response:
                pdf = response.read()
            checks["pdf_bytes"] = len(pdf)
            checks["pdf_magic"] = pdf[:5].decode("latin-1") if pdf else None
            if compiled.get("status") != "ok" or not pdf.startswith(b"%PDF-"):
                raise RuntimeError(f"bundled PDF compilation failed: {compiled.get('status')} "
                                   f"{compiled.get('diagnostics')}")
        checks["identity"] = info["identity"]
        checks["api"] = info["api"]
        return checks
    finally:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
        shutil.rmtree(workspace, ignore_errors=True)


def build_dmg(app_bundle, output, staging):
    readme = staging / "build" / "READ ME - REVIEW BUILD.txt"
    readme.write_text(READ_ME, encoding="utf-8")
    run(["hdiutil", "create", "-volname", f"{APP_NAME} (review)",
         "-srcfolder", str(staging / "build"), "-ov", "-format", "UDZO",
         "-fs", "HFS+", str(output)], check=True)
    return output


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


READ_ME = f"""{APP_NAME} - TEST/REVIEW BUILD (macOS arm64)

What this is
  A self-contained copy of Research Workbench for macOS. Drag the app into
  Applications and open it. It runs its own backend and frontend on free
  loopback ports; no project folder, Python installation or Homebrew package
  is needed.

Your workspace is kept
  Projects, conversations, settings and any key you save in Settings are kept
  in one workspace and are still there the next time you open the app:
    ~/Library/Application Support/research-workbench-review/workspace/
  Two sample projects are added on the first launch only; delete them if you
  do not need them. To start over, quit the app and move that folder to the
  Trash. Only one copy of the app runs at a time.

What it is not
  - Not signed with a developer identity and not notarized: macOS blocks the
    first launch. Try to open it once, then go to System Settings > Privacy &
    Security and click "Open Anyway" for Research Workbench Review.
  - Not evidence of model quality.

MODELS AND COST
  The Secretary uses the local model by default (see below); local compute is
  free and is not metered as spend. Cloud routes are off until you turn them
  on. Baseten (Muse Spark Open), OpenAI and Modal Shared are real paid
  endpoints that run on YOUR OWN key: paste it in Settings, where it is saved
  only in the workspace above as an owner-only file, never shown again and
  never sent anywhere except that provider. No credential ships with this build
  and none is read from the Mac it is opened on, including keys another copy of
  Research Workbench saved or shell environment variables. Before the
  Secretary sends anything to a paid model, Settings asks once for consent: it
  names the provider and model, says that recent conversation turns and the
  project briefing leave the device, that each reply has a calculated cost,
  and shows the shared spend limit. Each reply then shows its model and
  calculated cost and keeps a receipt, and nothing is sent once the spend
  limit would be exceeded. Costs are calculated from published prices, not
  invoice-verified. The
  legacy Meta route in this build answers from a bundled scripted fake
  provider (MOCK), which the app labels on screen.

LOCAL MODEL (OPTIONAL, NOT BUNDLED)
  No model is inside this build and nothing is downloaded. If this Mac already
  has a local model configured for Research Workbench (the owner-only file
  ~/Library/Application Support/ResearchWorkbench/local_runtime.json naming an
  installed llama-server and its weights), the app starts that model as its own
  child on the loopback port that file configures. If another program already
  uses that port, the app chooses a free port for its own model instead. It
  never takes over or stops the other program and keeps your saved port unchanged.
  It verifies the weights against the pinned size and hash, and serves local chat
  from it until you quit. The app stops that model when it quits, and a small
  watchdog stops it if the app is force-killed.
  Local mode is unavailable when no model is configured, when the configuration
  is invalid, when the model file does not match, or when startup fails.
  In every one of those cases the app still opens, says why local
  mode is unavailable, and never falls back to a cloud call.

PDF
  A portable TeX Live (TinyTeX) tree is bundled so the PDF tool works offline.
  Compilation still runs inside the shipped macOS sandbox policy with the
  bundled TeX root as the only trusted one; no arbitrary root or environment
  override is enabled.

Known limitations carried over from the reviewed source (not fixed here)
  1 Python test is marked xfail; 1 of 45 UI checks (Esc drag cancellation) is
  flaky; native save/PDF/close behaviour was verified separately on the source
  build, not re-verified inside this bundle beyond the checks listed in the
  build manifest. The optional local model is verified in the source build; a
  real launch of this bundle on a Mac with a configured model is a separate
  check.
"""


def _sw_vers():
    result = run(["/usr/bin/sw_vers", "-productVersion"], check=False)
    return result.stdout.strip() or platform.platform()


def _electron_version():
    path = SOURCE / "node_modules" / "electron" / "dist" / "version"
    return path.read_text().strip() if path.is_file() else "unknown"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tex", action="store_true")
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument("--version", default="1.1.2")
    args = parser.parse_args(argv)
    if platform.machine() != "arm64" or platform.system() != "Darwin":
        raise SystemExit("This builder targets macOS arm64.")

    version = args.version
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise SystemExit("--version must have the form major.minor.patch")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    DELIVERABLES.mkdir(parents=True, exist_ok=True)
    output = DELIVERABLES / f"RESEARCH_WORKBENCH_review_{stamp}_arm64.dmg"
    if output.exists():
        raise SystemExit(f"refusing to overwrite {output}")

    staging = Path(tempfile.mkdtemp(prefix="wb-review-build-"))
    print(f"staging: {staging}")
    try:
        interpreter = stage_python(staging)
        _, skipped = stage_site_packages(interpreter, staging)
        stage_app_sources(staging)
        tex_included = False if args.skip_tex else bool(stage_tex(staging))
        app_bundle = assemble_app(staging, interpreter, tex_included, version)

        checks = verify_staged_runtime(app_bundle / "Contents" / "Resources" / "wb",
                                        skip_tex=not tex_included)
        inherited = checks["inherited_credentials"]
        if any(inherited.values()) or checks["cloud_enabled"]:
            raise RuntimeError(f"review session inherited owner credentials: {inherited}, cloud_enabled={checks['cloud_enabled']}")
        signed = adhoc_sign(app_bundle)
        build_dmg(app_bundle, output, staging)

        manifest = {
            # File names only: absolute paths would publish the builder's home directory.
            "artifact": output.name,
            "bytes": output.stat().st_size,
            "sha256": sha256(output),
            "version": version,
            "built_at": stamp,
            "host": {"machine": platform.machine(), "macos": _sw_vers()},
            "python_runtime": f"CPython {PY_VERSION} (python-build-standalone {PY_TAG}, arm64)",
            "electron": _electron_version(),
            "bundled_tex": tex_included,
            "tex_source": TEX_ROOT.name if tex_included else None,
            "excluded_site_packages": skipped,
            "adhoc_signed": signed,
            "backend_checks": checks,
            "not_done": ["developer-id signing", "notarization", "staple",
                          "bundled Playwright UI run (the delivered UI suite covers the source build)"],
        }
        manifest_path = output.with_suffix(".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"dmg": str(output), "bytes": manifest["bytes"],
                          "manifest": str(manifest_path), "checks": checks}, indent=2))
        return 0
    finally:
        if args.keep_staging:
            print(f"staging kept: {staging}")
        else:
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
