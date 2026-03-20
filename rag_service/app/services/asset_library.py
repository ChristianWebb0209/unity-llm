"""
Local-only asset catalog service stubs for Unity mode.
"""

from typing import Any, Dict


def search_asset_library(
    filter_text: str,
    unity_version: str = "2022.3",
    asset_type: str = "package",
    max_results: int = 20,
) -> Dict[str, Any]:
    return {
        "success": False,
        "message": "disabled_in_local_mode: remote asset catalog lookups are disabled; use local project search tools instead.",
        "assets": [],
        "total_items": 0,
    }
