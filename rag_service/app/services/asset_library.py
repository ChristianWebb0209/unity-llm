"""
Godot Asset Library API: search assets (addons, projects) by filter.
API: GET /asset?filter=...&type=addon&godot_version=4.2&max_results=20
"""

import logging
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None

_BASE_URL = "https://godotengine.org/asset-library/api"
_TIMEOUT = 15

logger = logging.getLogger(__name__)


def search_asset_library(
    filter_text: str,
    godot_version: str = "4.2",
    asset_type: str = "addon",
    max_results: int = 20,
) -> Dict[str, Any]:
    """
    Search the Godot Asset Library. Returns {
        "success": bool,
        "message": str,
        "assets": [{"asset_id", "title", "author", "support_level", "godot_version", "category", "browse_url"}],
        "total_items": int
    }.
    """
    if not requests:
        return {
            "success": False,
            "message": "requests library is required for Asset Library search.",
            "assets": [],
            "total_items": 0,
        }
    filter_text = (filter_text or "").strip()
    params: Dict[str, Any] = {
        "filter": filter_text or "",
        "type": asset_type if asset_type in ("addon", "project", "any") else "addon",
        "godot_version": godot_version or "4.2",
        "max_results": min(100, max(1, int(max_results))),
    }
    try:
        r = requests.get(
            _BASE_URL + "/asset",
            params=params,
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        logger.warning("Asset Library request failed: %s", e)
        return {
            "success": False,
            "message": "Asset Library request failed: %s" % (e,),
            "assets": [],
            "total_items": 0,
        }
    except (ValueError, KeyError) as e:
        logger.warning("Asset Library response parse error: %s", e)
        return {
            "success": False,
            "message": "Invalid response from Asset Library.",
            "assets": [],
            "total_items": 0,
        }
    result_list = data.get("result") or []
    assets: List[Dict[str, Any]] = []
    for item in result_list:
        if isinstance(item, dict):
            assets.append({
                "asset_id": str(item.get("asset_id", "")),
                "title": str(item.get("title", "")),
                "author": str(item.get("author", "")),
                "support_level": str(item.get("support_level", "")),
                "godot_version": str(item.get("godot_version", "")),
                "category": str(item.get("category", "")),
                "browse_url": str(item.get("browse_url", "")),
                "version_string": str(item.get("version_string", "")),
            })
    return {
        "success": True,
        "message": "Found %d asset(s)." % len(assets),
        "assets": assets,
        "total_items": data.get("total_items", len(assets)),
    }
