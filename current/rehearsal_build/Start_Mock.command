#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then echo 'Python 3.11+ is required. No installation was attempted.'; read -r -p 'Press Return to close'; exit 1; fi
if [ ! -x .venv/bin/python ]; then
  echo 'This will create a local virtual environment and install the pinned Python dependencies from PyPI.'
  read -r -p 'Continue? [y/N] ' answer
  case "$answer" in y|Y) ;; *) exit 0 ;; esac
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python run_mock.py --desktop
