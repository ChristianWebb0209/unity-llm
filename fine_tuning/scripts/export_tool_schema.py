#!/usr/bin/env python3
"""
Export tool definitions from rag_service to fine_tuning/schemas/tools.json.
Run from repo root (with rag_service deps installed, e.g. pip install -r rag_service/requirements.txt):
  python fine_tuning/scripts/export_tool_schema.py
"""
from pathlib import Path
import json
import sys

def find_repo_root() -> Path:
    """Find repo root by locating the `rag_service/` folder."""
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "rag_service").exists():
            return p
    raise RuntimeError("Could not locate repo root (rag_service not found).")


REPO_ROOT = find_repo_root()

# Some imports under `rag_service` instantiate an OpenAI-backed agent singleton at
# import time. Ensure OPENAI_API_KEY is present for those imports.
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(REPO_ROOT / "fine_tuning" / ".env", override=True)
except Exception:
    # If python-dotenv isn't installed, we still attempt import; it may work if
    # OPENAI_API_KEY is already set in the environment.
    pass

SCHEMAS_DIR = REPO_ROOT / "fine_tuning" / "schemas"
OUTPUT_FILE = SCHEMAS_DIR / "tools.json"


def main() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    # Allow importing from rag_service
    rag = REPO_ROOT / "rag_service"
    if not rag.exists():
        sys.stderr.write("rag_service not found at repo root\n")
        sys.exit(1)
    sys.path.insert(0, str(rag))

    try:
        # Import directly from definitions to avoid importing the tools package
        # singleton, which instantiates an OpenAI-backed agent.
        from app.tools.definitions import get_registered_tools
    except ImportError as e:
        sys.stderr.write(
            f"Could not import rag_service app ({e}). "
            "Install deps from repo root: pip install -r rag_service/requirements.txt\n"
        )
        sys.exit(1)

    tools = get_registered_tools()
    payload = []
    for t in tools:
        payload.append({
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        })

    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Exported {len(payload)} tools to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
