"""The only place that imports private ADK names (pinned google-adk==2.11.0).

`ctx.run_node()` wraps any failure of the dynamic child in DynamicNodeFailError, which ADK does not
export publicly yet. Keeping the private import here means an ADK upgrade breaks one module (and
tests/workflow/test_adk_compat.py) instead of the pipeline nodes.
"""

from __future__ import annotations

from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.workflow._errors import DynamicNodeFailError

__all__ = ["DynamicNodeFailError", "LlmCallsLimitExceededError", "root_cause"]


def root_cause(exc: BaseException) -> BaseException:
    """The original exception behind a DynamicNodeFailError (ADK keeps it in `.error`, not in
    `__cause__`); any other exception is returned unchanged."""
    seen = 0
    while isinstance(exc, DynamicNodeFailError) and isinstance(exc.error, BaseException):
        exc = exc.error
        seen += 1
        if seen > 10:  # defensive: never loop on a self-referencing chain
            break
    return exc
