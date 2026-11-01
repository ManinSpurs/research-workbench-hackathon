"""Lead-only deployment artifact. Import declares resources; never builds or runs.

Sources:
https://huggingface.co/blog/muse-glimmer
https://github.com/vllm-project/vllm/releases/tag/v0.28.0
https://github.com/huggingface/transformers/releases/tag/v5.15.0
https://modal.com/docs/guide/webhooks

Lead runs tools/modal_glimmer_smoke.py, which owns an ephemeral App.run context
and stops only its newly allocated app ID in finally. No persistent deployment.
Build/download CPU time, image storage, GPU startup and idle time are billable.
These settings are NOT a $10 provider billing cap. Never leave deployed.
"""
import re
import shlex
from pathlib import Path

import modal

MODEL_ID = "meta-models/Muse-Glimmer-30B"
REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"
VLLM_VERSION = "0.28.0"
TRANSFORMERS_VERSION = "5.15.0"


def identity_code(allow_upgrade=False):
    """Official v0.28.0 setup.py uses a CUDA local version; cu129 is required.

    Sources: https://github.com/vllm-project/vllm/blob/v0.28.0/setup.py
    https://huggingface.co/docs/transformers/v5.15.0/en/model_doc/muse_glimmer
    Inspect installed metadata, not a guessed bare-version equality. No weights.
    """
    return f"""
import importlib.metadata as m
import json, subprocess, sys
from packaging.version import Version
def observed():
    return {{"EXEC": sys.executable, "PREFIX": sys.prefix,
             "VLLM": m.version("vllm"), "TRANSFORMERS": m.version("transformers"),
             "TOKENIZERS": m.version("tokenizers")}}
print(json.dumps(observed()), flush=True)
v = Version(m.version("vllm"))
assert v.base_version == "0.28.0" and v.local == "cu129", "vllm_cuda_variant_mismatch"
if Version(m.version("transformers")) < Version("5.15.0"):
    assert {allow_upgrade!r}, "transformers_upgrade_required"
    subprocess.run(["/usr/bin/python3", "-m", "pip", "install",
                    "transformers==5.15.0"], check=True, timeout=120)
assert Version(m.version("transformers")) >= Version("5.15.0")
from transformers import MuseGlimmerForConditionalGeneration
assert MuseGlimmerForConditionalGeneration is not None
print(json.dumps(observed()), flush=True)
"""


def runtime_image():
    return (
        # Modal requires bare python/pip on PATH and an entrypoint that executes
        # its runtime command. The upstream image has only python3 and starts
        # `vllm serve`; retain its interpreter/packages, not a second Python.
        modal.Image.from_registry(
            "vllm/vllm-openai:v0.28.0-cu129",
            setup_dockerfile_commands=[
                "RUN ln -s /usr/bin/python3 /usr/local/bin/python"
                " && python -m pip --version && pip --version",
                "ENTRYPOINT []",
            ],
        )
        .run_commands("/usr/bin/python3 -c " + shlex.quote("exec(" + repr(identity_code(True)) + ")"))
        .add_local_file(Path(__file__).resolve(), "/root/modal_glimmer_server.py", copy=True)
    )


def cpu_preflight():
    """CPU-only identity/import check: no HF download, endpoint or GPU."""
    import json
    import subprocess
    result = subprocess.run(
        ["/usr/bin/python3", "-c", identity_code()], capture_output=True,
        text=True, check=True, timeout=120)
    return json.loads(result.stdout.splitlines()[-1])


def create_preflight_app(name):
    if not re.fullmatch(r"wb-glimmer-trial-[a-z0-9]{12,32}", name):
        raise ValueError("Invalid trial name")
    application = modal.App(name, include_source=False)
    function = application.function(
        image=runtime_image(), cpu=(1, 2), memory=(2048, 4096),
        max_containers=1, min_containers=0, buffer_containers=0,
        scaledown_window=2, timeout=150, startup_timeout=180, retries=0,
    )(cpu_preflight)
    return application, function


def download_weights():
    """CPU-only image build step, bounded by Modal's build-function timeout."""
    from huggingface_hub import snapshot_download
    snapshot_download(
        MODEL_ID, revision=REVISION, cache_dir="/model-cache",
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.jinja",
                        "*.tiktoken", "merges.txt", "vocab.txt"],
        max_workers=4,
    )


def server_command():
    return [
        "vllm", "serve", MODEL_ID, "--revision", REVISION,
        "--tokenizer-revision", REVISION, "--served-model-name", MODEL_ID,
        "--model-impl", "transformers", "--tensor-parallel-size", "1",
        "--dtype", "bfloat16", "--max-model-len", "4096",
        "--max-num-seqs", "1", "--max-num-batched-tokens", "4096",
        "--gpu-memory-utilization", "0.90", "--enforce-eager",
        "--language-model-only", "--generation-config", "vllm",
        "--override-generation-config", '{"max_new_tokens":512}',
        "--host", "0.0.0.0", "--port", "8000", "--disable-log-requests",
    ]


def serve():
    """Top-level import-by-reference avoids cross-Python cloudpickle code."""
    import subprocess
    subprocess.Popen(server_command())


def create_app(name):
    if not re.fullmatch(r"wb-glimmer-trial-[a-z0-9]{12,32}", name):
        raise ValueError("A fresh wb-glimmer-trial-<12-32 lowercase alphanumerics> name is required")
    image = (
        runtime_image()
        .env({"HF_HUB_CACHE": "/model-cache", "HF_HUB_OFFLINE": "0",
              "HF_HUB_DISABLE_TELEMETRY": "1"})
        .run_function(download_weights, cpu=2, memory=8192, timeout=600,
                      include_source=False)
        .env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    )
    application = modal.App(name, include_source=False)
    protected = modal.web_server(
        8000, startup_timeout=900, requires_proxy_auth=True)(serve)
    endpoint = application.function(
        image=image, gpu="H100", cpu=(2, 8), memory=(65536, 131072),
        serialized=False,
        max_containers=1, min_containers=0, buffer_containers=0,
        # SDK 1.6.3 rejects even retries=0 on Web Functions. None means no
        # configured Modal function retry policy; the HTTP client never retries.
        scaledown_window=2, timeout=180, startup_timeout=900, retries=None,
    )(protected)
    return application, endpoint
