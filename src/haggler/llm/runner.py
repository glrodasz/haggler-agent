"""Fan agent jobs out under ctx.run_node, inside one `@node(rerun_on_resume=True)` node.

Every failure (malformed output after the agent's own retry, model errors, timeouts, empty
answers, a budget) stays inside its AgentCall, so one bad job never fails the node.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from google.adk import Agent, Context
from pydantic import BaseModel

from haggler.llm.budget import SKIP_MAX_CALLS, LlmBudget, LlmBudgetExceededError
from haggler.pipeline._adk_compat import (
    DynamicNodeFailError,
    LlmCallsLimitExceededError,
    root_cause,
)

# Finish reasons of an empty answer worth asking again: the model just stopped. SAFETY,
# MAX_TOKENS and friends would fail the same way twice.
RETRY_EMPTY_REASONS = frozenset({None, "STOP", "FINISH_REASON_UNSPECIFIED"})


@dataclass
class AgentJob:
    # Base of the ADK run id (unique_run_ids makes it unique within the node).
    key: str
    # The user message the agent receives.
    payload: str


@dataclass
class AgentCall:
    job: AgentJob
    # Run id of the last attempt ("<key>_retry" after an empty answer was asked again).
    run_id: str
    # The agent's answer as a JSON object (pydantic outputs are dumped).
    output: dict[str, Any] | None = None
    error: str | None = None
    # Set when a budget refused the call (SKIP_MAX_CALLS, SKIP_MAX_QUERIES).
    skipped: str | None = None


def unique_run_ids(keys: Sequence[str]) -> list[str]:
    """One ADK run id per key, unique within the node: ADK answers a repeated run id with the
    first run's cached output."""
    seen: set[str] = set()
    out: list[str] = []
    for base in keys:
        run_id = base
        n = 1
        while run_id in seen:
            n += 1
            run_id = f"{base}_{n}"
        seen.add(run_id)
        out.append(run_id)
    return out


async def run_agent_jobs(
    ctx: Context,
    agent: Agent,
    jobs: Sequence[AgentJob],
    *,
    budget: LlmBudget,
    concurrency: int,
    timeout_s: float,
    retry_empty: bool = True,
) -> list[AgentCall]:
    """One agent run per job (in job order), at most `concurrency` at a time, each capped at
    `timeout_s` (agent retries and HTTP retries included). With `retry_empty`, an empty answer
    whose finish reason is in RETRY_EMPTY_REASONS is asked once more under a new run id (an empty
    answer skips ADK's schema validation, so the agent's own retry never fires)."""
    sem = asyncio.Semaphore(max(1, concurrency))
    reasons = budget.finish_reasons

    async def call(job: AgentJob, run_id: str) -> Any:
        return await asyncio.wait_for(
            ctx.run_node(agent, node_input=job.payload, run_id=run_id), timeout=timeout_s
        )

    async def one(job: AgentJob, run_id: str) -> AgentCall:
        result = AgentCall(job=job, run_id=run_id)
        async with sem:
            try:
                out = await call(job, run_id)
                if out is None and retry_empty and reasons.get(run_id) in RETRY_EMPTY_REASONS:
                    run_id = result.run_id = f"{run_id}_retry"
                    out = await call(job, run_id)
            except TimeoutError:
                result.error = f"timeout after {timeout_s:g}s"
                return result
            except Exception as exc:
                cause = root_cause(exc) if isinstance(exc, DynamicNodeFailError) else exc
                if isinstance(cause, LlmBudgetExceededError):
                    result.skipped = cause.reason
                elif isinstance(cause, LlmCallsLimitExceededError):
                    # ADK's own RunConfig limit refused a call before_model already counted.
                    budget.refund()
                    result.skipped = SKIP_MAX_CALLS
                else:
                    result.error = f"{type(cause).__name__}: {str(cause)[:300]}"
                return result
        if out is None:
            result.error = f"empty answer (finish_reason={reasons.get(run_id) or 'none'})"
        elif isinstance(out, BaseModel):
            result.output = out.model_dump(mode="json")
        elif isinstance(out, dict):
            result.output = out
        else:
            result.error = f"unexpected agent output type {type(out).__name__}"
        return result

    run_ids = unique_run_ids([j.key for j in jobs])
    return list(await asyncio.gather(*(one(j, r) for j, r in zip(jobs, run_ids, strict=True))))
