"""Keep actionable provider failures visible without exposing remote text."""
import pytest

from app.ai.receipt import _safe_error_code


@pytest.mark.parametrize("code", ["payment_required", "provider_overloaded"])
def test_shared_provider_failure_survives_receipt_sanitization(code):
    assert _safe_error_code(code) == code
