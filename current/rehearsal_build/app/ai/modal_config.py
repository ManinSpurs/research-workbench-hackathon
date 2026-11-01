"""Code-only Modal endpoint configuration. Never reads SDK credentials."""
import os
import re
from urllib.parse import urlsplit

PROVIDER_ID = "modal"
MODEL_ID = "meta-models/Muse-Glimmer-30B"
CHAT_PATH = "/v1/chat/completions"


def endpoint_origin(raw):
    if (isinstance(raw, str) and raw and raw == raw.strip() and
            all(32 <= ord(c) <= 126 for c in raw)):
        try:
            parsed = urlsplit(raw)
            if (parsed.scheme == "https" and len(parsed.netloc) <= 253 and
                    re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:modal\.run|modal\.direct)",
                                 parsed.netloc) and
                    parsed.path in ("", "/") and not parsed.query and
                    not parsed.fragment and "?" not in raw and "#" not in raw):
                return "https://" + parsed.netloc
        except ValueError:
            pass
    return None


def read_config():
    """Private transport configuration; do not serialize this mapping."""
    raw = os.environ.get("WB_MODAL_BASE_URL", "")
    base = endpoint_origin(raw)
    token_id = os.environ.get("WB_MODAL_PROXY_TOKEN_ID", "")
    token_secret = os.environ.get("WB_MODAL_PROXY_TOKEN_SECRET", "")
    return {"base_url": base, "endpoint_invalid": bool(raw) and base is None,
            "proxy_token_id": token_id, "proxy_token_secret": token_secret}


def valid_proxy_pair(config):
    return all(isinstance(config.get(k), str) and config[k] and
               all(33 <= ord(c) <= 126 for c in config[k])
               for k in ("proxy_token_id", "proxy_token_secret"))


def connection_status():
    """Configuration readiness only, never account or runtime verification."""
    config = read_config()
    reasons = []
    if not config["base_url"]:
        reasons.append("endpoint_invalid" if config["endpoint_invalid"] else
                       "endpoint_undeployed_or_unconfigured")
    if not valid_proxy_pair(config):
        reasons.append("proxy_auth_unconfigured")
    reasons.extend(["deployment_runtime_not_verified", "gpu_accounting_missing",
                    "live_execution_approval_missing"])
    return {"model_id": MODEL_ID, "provider": PROVIDER_ID,
            "base_url": config["base_url"],
            "endpoint_configured": bool(config["base_url"]),
            "proxy_auth_configured": valid_proxy_pair(config),
            "transport": "openai_chat_completions", "billing": "gpu_time",
            "execution_enabled": False, "status": "blocked",
            "blocking_reasons": reasons}
