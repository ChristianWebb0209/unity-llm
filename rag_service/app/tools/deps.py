"""
Request-scoped dependencies for the Pydantic AI Godot agent.
Populated in main from QueryContext before calling the agent; tools and execute_tool use these.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GodotQueryDeps:
    """
    Dependencies passed to the agent and tools for each request.
    Mirrors the context extracted from QueryContext in main.py for the tool loop.
    """
    project_root_abs: Optional[str] = None
    active_scene_path: Optional[str] = None
    active_file_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    # Request-scoped read_file cache: path -> content. Mutated by execute_tool.
    read_file_cache: Dict[str, str] = field(default_factory=dict)
