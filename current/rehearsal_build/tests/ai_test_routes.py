"""Real Baseten routing over the caller's synthetic HTTP responder."""
import asyncio

import httpx

from app.ai import service
from app.ai.provider import BasetenProvider


def ready_baseten_mock(svc, monkeypatch):
    monkeypatch.setattr(service.secrets, "baseten_key", lambda: "baseten-test-only")

    def provider():
        # Tests replace the responder between stages; retain that live binding.
        assert isinstance(svc.provider.transport, httpx.MockTransport)
        return BasetenProvider(svc.provider.transport,
                               key_reader=lambda: "baseten-test-only")

    svc._provider_factory["baseten"] = provider
    asyncio.run(svc.verify_account("baseten"))
