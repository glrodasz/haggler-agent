"""ADK App for the daily price run (`adk web agents`, `adk run`, haggler.jobs.daily).

`app` and `root_agent` are built at import time from the environment, so this module must stay
offline-safe: build_default_deps creates no HTTP, GCS or model client until a run needs one.
"""

from __future__ import annotations

from google.adk.apps import App

from haggler.pipeline.deps import Deps, build_default_deps
from haggler.pipeline.nodes import build_workflow

APP_NAME = "haggler_agent"


def build_app(deps: Deps | None = None) -> App:
    return App(name=APP_NAME, root_agent=build_workflow(deps or build_default_deps()))


app = build_app()
root_agent = app.root_agent

__all__ = ["APP_NAME", "app", "build_app", "root_agent"]
