"""Per-run model budget, enforced by the agents' own callbacks.

The workflows enforce their budgets in before_model_callback rather than through a RunConfig, so
the caps hold under `adk web`/`adk run` too (they build their own RunConfig). The same object
collects what each answer reports: its finish reason (it explains empty answers) and, for agents
with google_search, the grounding metadata (queries, sources) plus token usage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from haggler.pipeline._adk_compat import LlmCallsLimitExceededError

SKIP_MAX_CALLS = "max_llm_calls"
SKIP_MAX_QUERIES = "max_search_queries"


class LlmBudgetExceededError(LlmCallsLimitExceededError):
    """A budget of LlmBudget was reached: raised by its before_model callback. `reason` is the
    skip reason the job runner records (SKIP_MAX_CALLS or SKIP_MAX_QUERIES)."""

    def __init__(self, message: str, *, reason: str = SKIP_MAX_CALLS) -> None:
        super().__init__(message)
        self.reason = reason


def agent_run_id(callback_context: Any) -> str:
    """The run id ctx.run_node gave the agent (its node path ends in "<agent>@<run id>")."""
    path = str(getattr(callback_context, "node_path", "") or "")
    return path.rsplit("/", 1)[-1].partition("@")[2]


class GroundingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Usually a vertexaisearch.cloud.google.com redirect, not the page itself.
    uri: str
    title: str = ""
    domain: str = ""


class GroundingRecord(BaseModel):
    """What one model answer reported about its search grounding and token usage."""

    model_config = ConfigDict(extra="forbid")

    finish_reason: str | None = None
    queries: list[str] = Field(default_factory=list)
    sources: list[GroundingSource] = Field(default_factory=list)
    supports: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    tool_prompt_tokens: int = 0


def _reason_name(reason: Any) -> str | None:
    if reason is None:
        return None
    return getattr(reason, "name", str(reason))


def grounding_record(llm_response: Any) -> GroundingRecord:
    """GroundingRecord of an LlmResponse (every field is optional on the wire)."""
    meta = getattr(llm_response, "grounding_metadata", None)
    usage = getattr(llm_response, "usage_metadata", None)
    sources: list[GroundingSource] = []
    for chunk in (getattr(meta, "grounding_chunks", None) or []) if meta else []:
        web = getattr(chunk, "web", None)
        uri = str(getattr(web, "uri", "") or "") if web is not None else ""
        if uri:
            sources.append(
                GroundingSource(
                    uri=uri,
                    title=str(getattr(web, "title", "") or ""),
                    domain=str(getattr(web, "domain", "") or ""),
                )
            )
    return GroundingRecord(
        finish_reason=_reason_name(getattr(llm_response, "finish_reason", None)),
        queries=[str(q) for q in (getattr(meta, "web_search_queries", None) or [])] if meta else [],
        sources=sources,
        supports=len(getattr(meta, "grounding_supports", None) or []) if meta else 0,
        prompt_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
        output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        thought_tokens=int(getattr(usage, "thoughts_token_count", 0) or 0),
        tool_prompt_tokens=int(getattr(usage, "tool_use_prompt_token_count", 0) or 0),
    )


@dataclass
class LlmBudget:
    """Model requests sent (one per attempt, failed ones included) against max_calls, and search
    queries reported against max_queries (None = no query cap).

    Queries are only known once an answer arrives, so the query cap refuses the next request
    after it is reached rather than the one that crosses it."""

    max_calls: int
    max_queries: int | None = None
    calls_setting: str = "HAGGLER_MAX_LLM_CALLS"
    queries_setting: str = "max search queries"
    calls: int = 0
    queries: int = 0
    # Agent run id -> finish reason of its last model answer.
    finish_reasons: dict[str, str | None] = field(default_factory=dict)
    # Agent run id -> one record per model answer (retries under the same run id append).
    grounding: dict[str, list[GroundingRecord]] = field(default_factory=dict)

    def before_model(self, callback_context: Any, llm_request: Any) -> None:
        """before_model_callback: runs once per model attempt, before ADK sends it."""
        if self.calls >= self.max_calls:
            raise LlmBudgetExceededError(
                f"{self.calls_setting}={self.max_calls} model calls reached",
                reason=SKIP_MAX_CALLS,
            )
        if self.max_queries is not None and self.queries >= self.max_queries:
            raise LlmBudgetExceededError(
                f"{self.queries_setting}={self.max_queries} search queries reached",
                reason=SKIP_MAX_QUERIES,
            )
        self.calls += 1
        return None

    def after_model(self, callback_context: Any, llm_response: Any) -> None:
        """after_model_callback: finish reason, grounding and usage of each answer."""
        run_id = agent_run_id(callback_context)
        record = grounding_record(llm_response)
        self.finish_reasons[run_id] = record.finish_reason
        self.queries += len(record.queries)
        self.grounding.setdefault(run_id, []).append(record)
        return None

    def refund(self) -> None:
        """ADK's own RunConfig limit refused a request before_model already counted."""
        self.calls = max(0, self.calls - 1)
