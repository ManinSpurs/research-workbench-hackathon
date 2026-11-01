from app.release_scope import NOT_CHECKED, declared_release, question_release, year_applicability


def test_release_year_requires_ascii_digits():
    for release in ("２０２３", "٢٠٢٣"):
        assert question_release(release) is None
        assert year_applicability("applies to release 2023", release)["status"] == NOT_CHECKED
    for note in ("applies to release ２０２５", "applies to release ٢٠٢٥", "applies to release 2025６"):
        assert declared_release(note) is None


def test_receipt_year_uses_the_same_ascii_format():
    from app.ai.receipt import _safe_release_scope

    for year in ("２０２３", "٢٠٢٣"):
        scope = _safe_release_scope({"status": "MATCH", "question_release": year})
        assert scope["question_release"] is None
        assert scope["status"] == NOT_CHECKED
        assert "not passed" in scope["note"]
    assert _safe_release_scope({"status": "MATCH", "question_release": "2023"})["question_release"] == "2023"
