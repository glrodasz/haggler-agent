"""ADK CLI entry point for the feasibility research: `adk web agents` (pick haggler_research).

Re-exports the app of the installed `haggler` package (see agents/haggler_agent).
"""

from haggler.research.app import app, root_agent

__all__ = ["app", "root_agent"]
