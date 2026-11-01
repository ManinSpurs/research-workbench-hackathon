"""Optional real local pdfLaTeX check, isolated from saved research and network."""
import hashlib
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from app import harness
from app import latex_compile
from app.api import create_app
from test_dot_safety import StubAdapter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = r"""\documentclass{article}
\usepackage{lmodern}
\usepackage{amsmath,booktabs,geometry}
\begin{document}
\section{Synthetic writing check}
This is a \textbf{synthetic fixture}, not a research finding.
\begin{itemize}
\item A registered definition is not an adoption decision.
\item The original files remain unchanged.
\end{itemize}
\[
y = \alpha + \beta x
\]
\begin{tabular}{ll}
\toprule
Field & Meaning \\
\midrule
approval\_date & Plan-review completion \\
\bottomrule
\end{tabular}
\end{document}
"""


def test_real_pdf_current_revision_and_stale_rejection(tmp_path, monkeypatch):
    if not latex_compile.sandbox_available() or not latex_compile.discover().get("pdflatex"):
        pytest.skip("A trusted local pdfLaTeX and macOS sandbox are required")
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    monkeypatch.setenv("WB_DB", str(tmp_path / "isolated.db"))
    monkeypatch.setattr(harness, "ModelAdapter", StubAdapter)
    with TestClient(create_app(data_dir=data)) as client:
        pid = client.post("/projects", json={"kind": "fx"}).json()["project_id"]
        draft = client.post(f"/projects/{pid}/drafts", json={"title": "Synthetic writing check"}).json()
        did = draft["draft_id"]
        starter = client.post(f"/drafts/{did}/compile", json={"source": "manual"})
        assert starter.status_code == 200, starter.text
        assert starter.json()["status"] == "ok", starter.json()
        saved = client.put(f"/drafts/{did}", json={
            "expected_revision": draft["revision"], "engine": "pdflatex",
            "files": [{"path": "main.tex", "text": SOURCE}], "source": "manual",
        })
        assert saved.status_code == 200, saved.text
        revision = saved.json()["revision"]
        compiled = client.post(f"/drafts/{did}/compile", json={"source": "manual"})
        assert compiled.status_code == 200, compiled.text
        result = compiled.json()
        assert result["status"] == "ok", result
        assert result["revision"] == revision
        response = client.get(result["pdf_url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["cache-control"] == "no-store"
        assert response.content.startswith(b"%PDF-")
        assert b"%%EOF" in response.content[-128:]
        assert 1000 < len(response.content) <= 8 * 1024 * 1024
        print(f"Native PDF: {len(response.content)} bytes, SHA-256 {hashlib.sha256(response.content).hexdigest()}")
        assert client.put(f"/drafts/{did}", json={
            "expected_revision": revision, "files": [{"path": "main.tex", "text": SOURCE + "\n% revised"}],
            "source": "manual",
        }).status_code == 200
        assert client.get(result["pdf_url"]).status_code == 404
