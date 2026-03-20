"""
Test harness config: backend URLs, models, and optional credentials for
RAG vs Godot Composer.

The goal is to make it easy to run A/B tests between:
- RAG (typically GPT on OpenAI)
- Composer (e.g. a LoRA hosted on Vast.ai or another OpenAI-compatible host)

For Composer you can point at a Vast.ai (or other) endpoint just by setting
environment variables—no code changes required.
"""
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables for both:
# - shared repo defaults (./.env)
# - rag_service backend (rag_service/.env)
# - local fine-tuning defaults (fine_tuning/.env, optional)
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
load_dotenv(REPO_ROOT / "rag_service" / ".env", override=True)
load_dotenv(REPO_ROOT / "fine_tuning" / ".env", override=True)

# Base URL of the running RAG service (e.g. http://localhost:8000)
RAG_BASE_URL: str = os.getenv("RAG_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Model used by POST /query (RAG + agent). Server default is gpt-4.1-mini if unset.
RAG_MODEL: Optional[str] = os.getenv("RAG_MODEL") or "gpt-4.1-mini"

# Model used by POST /composer/query (fine-tuned Godot Composer).
# This can be an OpenAI model name OR a model string accepted by your inference host.
COMPOSER_MODEL: Optional[str] = (
    os.getenv("COMPOSER_MODEL")
    or os.getenv("OPENAI_MODEL")
    # Fallback example (model id, optionally with '#<deployment id>' routing)
    # Safe default so tests don't require editing this file when using
    # your own OpenAI-compatible host (Vast.ai / RunPod / etc).
    or "godot-composer"
)

# Optional credentials / endpoint for Composer when hosted outside OpenAI.
# Typical Vast.ai setup:
#   VASTAI_API_KEY   = your API key (can be anything if your server doesn't enforce keys)
#   VASTAI_BASE_URL  = e.g. https://<YOUR_VASTAI_OPENAI_ENDPOINT>/v1
COMPOSER_API_KEY: Optional[str] = (
    os.getenv("COMPOSER_API_KEY")
    or os.getenv("VASTAI_API_KEY")
    or os.getenv("RUNPOD_API_KEY")
    or os.getenv("OPENAI_API_KEY")
)
_composer_base_url_raw = (
    os.getenv("COMPOSER_BASE_URL")
    or os.getenv("VASTAI_BASE_URL")
    or os.getenv("RUNPOD_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or ""
)

def _normalize_openai_base_url(raw: str) -> Optional[str]:
    s = (raw or "").strip().rstrip("/")
    if not s:
        return None
    # OpenAI-compatible chat clients expect a /v1 base; normalize when omitted.
    if not s.endswith("/v1"):
        s = f"{s}/v1"
    return s


COMPOSER_BASE_URL: Optional[str] = _normalize_openai_base_url(_composer_base_url_raw)

# Endpoints (non-streaming for simpler test collection)
ENDPOINT_RAG: str = "/query"
ENDPOINT_COMPOSER: str = "/composer/query"

def get_rag_url() -> str:
    return f"{RAG_BASE_URL}{ENDPOINT_RAG}"

def get_composer_url() -> str:
    return f"{RAG_BASE_URL}{ENDPOINT_COMPOSER}"
