"""ADK CLI entry point: `adk web agents` / `adk run agents/haggler_agent`.

The real app lives in the installed `haggler` package; this shim only re-exports it so the ADK
agent loader (which imports `<agents_dir>/<name>`) finds `app` and `root_agent`.
"""

from haggler.pipeline.app import app, root_agent

__all__ = ["app", "root_agent"]
