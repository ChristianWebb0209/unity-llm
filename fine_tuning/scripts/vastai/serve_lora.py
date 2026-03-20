#!/usr/bin/env python3
"""
OpenAI-compatible LoRA inference server — run **on the GPU machine** after you manually
upload this file and your adapter (see fine_tuning/scripts/vastai/README.md).

Does:
- pip-installs deps (first run only)
- verifies adapter dir + weights
- downloads the base model from Hugging Face if not present under BASE_MODEL_LOCAL_DIR
- serves POST /v1/chat/completions and GET /docs

Example:
  export ADAPTER_DIR=/workspace/adapter
  python serve_lora.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List, Optional

BASE_MODEL_ID = os.getenv("BASE_MODEL_ID", "Qwen/Qwen2.5-Coder-7B-Instruct")
ADAPTER_DIR = os.getenv("ADAPTER_DIR", "/workspace/adapter")
CACHE_DIR = os.getenv("HF_HOME", "/workspace/.cache/huggingface")
# Default cache dir per model id so you rarely need to set BASE_MODEL_LOCAL_DIR by hand.
_BASE_SLUG = BASE_MODEL_ID.replace("/", "--")
BASE_MODEL_LOCAL_DIR = os.getenv("BASE_MODEL_LOCAL_DIR", f"/workspace/models/{_BASE_SLUG}")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

_REEXEC_FLAG = "SERVE_LORA_DEPS_READY"


def _run(cmd: list[str]) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _ensure_dependencies() -> None:
    if os.getenv(_REEXEC_FLAG) == "1":
        return

    # Install everything needed by this script. Safe to run repeatedly.
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
            "transformers>=4.52.0",
            "accelerate>=0.34.0",
            "peft>=0.17.0",
            "bitsandbytes>=0.46.1",
            "sentencepiece",
            "einops",
            "huggingface_hub>=0.23.0",
            "fastapi",
            "uvicorn[standard]",
            "pydantic",
            "torch",
        ]
    )

    # Re-exec so newly installed packages are guaranteed importable in this process.
    env = os.environ.copy()
    env[_REEXEC_FLAG] = "1"
    os.execve(sys.executable, [sys.executable, __file__], env)


_ensure_dependencies()

import torch
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from huggingface_hub import snapshot_download


def _ensure_paths_and_files() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(BASE_MODEL_LOCAL_DIR, exist_ok=True)

    if not os.path.isdir(ADAPTER_DIR):
        raise RuntimeError(f"Adapter dir not found: {ADAPTER_DIR}")

    required_adapter_files = ("adapter_config.json",)
    missing_required = [f for f in required_adapter_files if not os.path.isfile(os.path.join(ADAPTER_DIR, f))]
    has_weights = any(
        os.path.isfile(os.path.join(ADAPTER_DIR, fn))
        for fn in ("adapter_model.safetensors", "adapter_model.bin")
    )
    if missing_required or not has_weights:
        raise RuntimeError(
            f"Adapter incomplete at {ADAPTER_DIR}. "
            "Need adapter_config.json and adapter_model.safetensors (or adapter_model.bin)."
        )

    # If local model dir is empty/incomplete, materialize full snapshot there.
    if not os.path.isfile(os.path.join(BASE_MODEL_LOCAL_DIR, "config.json")):
        print(f"Base model not found locally at {BASE_MODEL_LOCAL_DIR}; downloading now...")
        snapshot_download(
            repo_id=BASE_MODEL_ID,
            cache_dir=CACHE_DIR,
            local_dir=BASE_MODEL_LOCAL_DIR,
            token=os.getenv("HF_TOKEN") or None,
        )


_ensure_paths_and_files()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionsRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 512
    top_p: Optional[float] = 0.95


model_source = BASE_MODEL_LOCAL_DIR if os.path.isdir(BASE_MODEL_LOCAL_DIR) else BASE_MODEL_ID

print(f"Model source: {model_source}")
print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(
    model_source,
    trust_remote_code=True,
    cache_dir=CACHE_DIR,
    local_files_only=os.path.isdir(BASE_MODEL_LOCAL_DIR),
)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

print("Loading base model...")
device_map = {"": 0} if torch.cuda.is_available() else None
model = AutoModelForCausalLM.from_pretrained(
    model_source,
    trust_remote_code=True,
    dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    device_map=device_map,
    cache_dir=CACHE_DIR,
    low_cpu_mem_usage=True,
    local_files_only=os.path.isdir(BASE_MODEL_LOCAL_DIR),
)

print("Loading LoRA adapter...")
model = PeftModel.from_pretrained(model, ADAPTER_DIR)
model.eval()

app = FastAPI(title="Unity Composer LoRA Server")


@app.get("/")
def root() -> RedirectResponse:
    """So a bare tunnel or browser open lands on the interactive API surface."""
    return RedirectResponse(url="/docs")


def _build_model_inputs(messages: List[ChatMessage]) -> dict:
    # Use the model-native chat template. Qwen instruct models expect this
    # formatting; ad-hoc role tags can lead to empty/low-quality generations.
    chat_messages = []
    for m in messages:
        role = (m.role or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        chat_messages.append({"role": role, "content": m.content})

    if hasattr(tokenizer, "apply_chat_template"):
        templated = tokenizer.apply_chat_template(
            chat_messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        )
        # Different transformers/tokenizer versions may return:
        # - torch.Tensor (input_ids)
        # - BatchEncoding / dict with input_ids(+attention_mask)
        if isinstance(templated, torch.Tensor):
            return {"input_ids": templated}
        if isinstance(templated, dict) or hasattr(templated, "keys"):
            out = {}
            if "input_ids" in templated:
                out["input_ids"] = templated["input_ids"]
            if "attention_mask" in templated:
                out["attention_mask"] = templated["attention_mask"]
            if "input_ids" in out:
                return out

    # Fallback for tokenizers without chat template support.
    parts: List[str] = []
    for m in chat_messages:
        parts.append(f"<{m['role']}>{m['content']}</{m['role']}>")
    enc = tokenizer("\n".join(parts), return_tensors="pt")
    return {
        "input_ids": enc["input_ids"],
        "attention_mask": enc.get("attention_mask"),
    }


def _generate(req: ChatCompletionsRequest) -> dict:
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages required")

    model_inputs = _build_model_inputs(req.messages)
    model_inputs = {
        k: v.to(model.device)
        for k, v in model_inputs.items()
        if v is not None
    }
    input_ids = model_inputs["input_ids"]

    max_new_tokens = int(req.max_tokens or 512)
    temperature = float(req.temperature if req.temperature is not None else 0.2)
    top_p = float(req.top_p if req.top_p is not None else 0.95)

    with torch.no_grad():
        out_ids = model.generate(
            **model_inputs,
            max_new_tokens=max_new_tokens,
            do_sample=temperature > 0,
            temperature=max(temperature, 1e-5),
            top_p=top_p,
            pad_token_id=tokenizer.eos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )

    gen_ids = out_ids[0][input_ids.shape[1] :]
    text = tokenizer.decode(gen_ids, skip_special_tokens=True).strip()
    prompt_tokens = int(input_ids.shape[1])
    completion_tokens = int(gen_ids.shape[0])

    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Some tunnel providers probe this path.
@app.get("/portal-resolver")
def portal_resolver() -> dict:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat_completions_v1(req: ChatCompletionsRequest = Body(...)) -> dict:
    return _generate(req)


# Compatibility alias for clients that already include /v1 in base URL handling.
@app.post("/chat/completions")
def chat_completions(req: ChatCompletionsRequest = Body(...)) -> dict:
    return _generate(req)


if __name__ == "__main__":
    import uvicorn

    print(f"Serving on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")

