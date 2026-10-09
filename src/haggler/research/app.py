"""ADK App for the weekly feasibility research (`adk web agents`, haggler.jobs.research).

Built at import time from the environment, so it must stay offline-safe (see research.deps).
"""

from __future__ import annotations

from google.adk.apps import App

from haggler.research.deps import ResearchDeps, build_default_research_deps
from haggler.research.nodes import build_research_workflow

APP_NAME = "haggler_research"


def build_research_app(deps: ResearchDeps | None = None) -> App:
    return App(
        name=APP_NAME, root_agent=build_research_workflow(deps or build_default_research_deps())
    )


app = build_research_app()
root_agent = app.root_agent

__all__ = ["APP_NAME", "app", "build_research_app", "root_agent"]
