"""The private ADK imports the pipeline depends on (google-adk==2.11.0). If an ADK upgrade moves
them, this file fails first and haggler/pipeline/_adk_compat.py is the only module to fix."""

from __future__ import annotations

import importlib

from haggler.pipeline._adk_compat import (
    DynamicNodeFailError,
    LlmCallsLimitExceededError,
    root_cause,
)


def test_private_imports_resolve_to_adk_classes() -> None:
    errors = importlib.import_module("google.adk.workflow._errors")
    ctx = importlib.import_module("google.adk.agents.invocation_context")
    assert DynamicNodeFailError is errors.DynamicNodeFailError
    assert LlmCallsLimitExceededError is ctx.LlmCallsLimitExceededError
    assert issubclass(DynamicNodeFailError, Exception)


def test_root_cause_unwraps_dynamic_node_failures() -> None:
    original = ValueError("bad json")
    wrapped = DynamicNodeFailError(message="child failed", error=original, error_node_path="a/b")
    twice = DynamicNodeFailError(message="parent failed", error=wrapped, error_node_path="a")
    assert wrapped.error is original
    assert root_cause(twice) is original
    other = RuntimeError("x")
    assert root_cause(other) is other
