#!/usr/bin/env python3
"""
Deprecated placeholder: Vast deployment is **manual** only.

Read: fine_tuning/scripts/vastai/README.md
Run on the GPU instance: python serve_lora.py (after uploading adapter + this script).

This file only prints a reminder so old commands fail loudly with directions.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual Vast workflow — no CLI automation. See README in this folder."
    )
    parser.parse_args()

    here = Path(__file__).resolve().parent
    readme = here / "README.md"
    print(
        "Vast/LoRA launch is manual.\n\n"
        f"Steps: {readme}\n\n"
        "Summary:\n"
        "  1) Start a GPU container (UI).\n"
        "  2) mkdir adapter dir; upload LoRA weights there.\n"
        "  3) Upload serve_lora.py to the instance.\n"
        "  4) python serve_lora.py  (downloads base model from HF if needed, then serves HTTP).\n"
    )


if __name__ == "__main__":
    main()
