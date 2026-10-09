"""Shared plumbing of the batch jobs: run an ADK App once and keep ADK's logs readable."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.sessions import InMemorySessionService
from google.genai import types

log = logging.getLogger(__name__)

ADK_NODE_RUNNER_LOGGER = "google_adk.google.adk.workflow._node_runner"
ADK_NODE_FAILURE_MESSAGE = "Node execution failed with exception"


class HandledNodeErrors(logging.Filter):
    """ADK logs every exception raised inside a node at ERROR with a traceback, before deciding
    to retry it, even the ones the pipeline handles (a TransientFetchError retry, a failed LLM
    job). Those are recorded in the run report, so they are logged as WARNING with the exception
    in one line; a real crash still reaches main(), which logs it with its traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR and record.getMessage() == ADK_NODE_FAILURE_MESSAGE:
            exc = record.exc_info[1] if record.exc_info else None
            if exc is not None:
                record.msg = f"{ADK_NODE_FAILURE_MESSAGE}: {type(exc).__name__}: {exc}"[:500]
                record.args = None
            record.levelno, record.levelname = logging.WARNING, "WARNING"
            record.exc_info, record.exc_text = None, None
        return True


def quiet_handled_node_errors() -> None:
    """Install HandledNodeErrors on ADK's node runner logger (idempotent)."""
    logger = logging.getLogger(ADK_NODE_RUNNER_LOGGER)
    if not any(isinstance(f, HandledNodeErrors) for f in logger.filters):
        logger.addFilter(HandledNodeErrors())


async def run_workflow_once(
    app: App,
    request: dict[str, Any],
    *,
    user_id: str,
    session_id: str,
    max_llm_calls: int,
) -> Any:
    """Send `request` (as JSON) to the app's root workflow with an in-memory session (the job's
    disk is ephemeral; everything durable goes to the Store) and return the workflow's final
    output, or None when it produced none.

    The workflows enforce their own call budgets (haggler.llm.budget), so adk web/run honour them
    too; max_llm_calls is the same cap for ADK's own guard. A value <= 0 turns the LLM off, which
    ADK would read as "unlimited": ADK's default is kept then."""
    message = types.UserContent(parts=[types.Part(text=json.dumps(request))])
    target = f"{app.root_agent.name}@1"
    final: Any = None
    runner = Runner(app=app, session_service=InMemorySessionService(), auto_create_session=True)
    async with runner:
        async for ev in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
            run_config=RunConfig(max_llm_calls=max_llm_calls) if max_llm_calls > 0 else RunConfig(),
        ):
            if ev.error_code:
                path = ev.node_info.path if ev.node_info else "?"
                log.debug("node event error %s at %s: %s", ev.error_code, path, ev.error_message)
            node_info = ev.node_info
            if ev.output is not None and node_info and target in (node_info.output_for or []):
                final = ev.output
    return final
