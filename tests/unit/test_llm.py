"""haggler.llm: the call/query budget, its grounding log and the agent job runner."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
from google.adk import Agent, Runner, Workflow
from google.adk.apps import App
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import START, node
from google.genai import types
from pydantic import BaseModel

from conftest import ScriptedLlm, _request_text
from haggler.llm.budget import (
    SKIP_MAX_CALLS,
    SKIP_MAX_QUERIES,
    LlmBudget,
    LlmBudgetExceededError,
    agent_run_id,
    grounding_record,
)
from haggler.llm.runner import AgentJob, run_agent_jobs


def _grounded_response(text: str, queries: list[str]) -> LlmResponse:
    return LlmResponse(
        content=types.ModelContent(parts=[types.Part(text=text)]),
        finish_reason=types.FinishReason.STOP,
        grounding_metadata=types.GroundingMetadata(
            web_search_queries=queries,
            grounding_chunks=[
                types.GroundingChunk(
                    web=types.GroundingChunkWeb(
                        uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                        title="help.netflix.com",
                        domain="help.netflix.com",
                    )
                ),
                types.GroundingChunk(web=types.GroundingChunkWeb(uri=None)),
            ],
            grounding_supports=[types.GroundingSupport(grounding_chunk_indices=[0])],
        ),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=120,
            candidates_token_count=40,
            thoughts_token_count=30,
            tool_use_prompt_token_count=900,
        ),
    )


def test_grounding_record_reads_queries_sources_and_usage() -> None:
    rec = grounding_record(_grounded_response("{}", ["netflix household rules", "q2"]))
    assert rec.finish_reason == "STOP"
    assert rec.queries == ["netflix household rules", "q2"]
    assert [s.domain for s in rec.sources] == ["help.netflix.com"]  # chunks without uri dropped
    assert rec.supports == 1
    assert (rec.prompt_tokens, rec.output_tokens, rec.thought_tokens, rec.tool_prompt_tokens) == (
        120,
        40,
        30,
        900,
    )


def test_grounding_record_of_a_bare_response() -> None:
    rec = grounding_record(LlmResponse())
    assert rec.finish_reason is None and rec.queries == [] and rec.sources == []
    assert rec.prompt_tokens == 0


def test_agent_run_id_is_the_suffix_of_the_node_path() -> None:
    ctx = SimpleNamespace(node_path="wf@1/research@1/pair_researcher@pair_netflix_CO")
    assert agent_run_id(ctx) == "pair_netflix_CO"
    assert agent_run_id(SimpleNamespace()) == ""


def test_budget_counts_calls_and_refuses_past_the_cap() -> None:
    budget = LlmBudget(max_calls=2, calls_setting="HAGGLER_RESEARCH_MAX_CALLS")
    budget.before_model(None, None)
    budget.before_model(None, None)
    with pytest.raises(LlmBudgetExceededError) as exc:
        budget.before_model(None, None)
    assert exc.value.reason == SKIP_MAX_CALLS
    assert "HAGGLER_RESEARCH_MAX_CALLS=2" in str(exc.value)
    budget.refund()
    assert budget.calls == 1


def test_budget_query_cap_refuses_the_next_request() -> None:
    budget = LlmBudget(max_calls=10, max_queries=2)
    ctx = SimpleNamespace(node_path="w@1/n@1/agent@job_a")
    budget.before_model(ctx, None)
    budget.after_model(ctx, _grounded_response("{}", ["a", "b", "c"]))
    assert budget.queries == 3  # the answer that crosses the cap is kept
    assert budget.finish_reasons == {"job_a": "STOP"}
    assert len(budget.grounding["job_a"]) == 1
    with pytest.raises(LlmBudgetExceededError) as exc:
        budget.before_model(ctx, None)
    assert exc.value.reason == SKIP_MAX_QUERIES
    assert budget.calls == 1


class Answer(BaseModel):
    echo: str


class GroundedLlm(ScriptedLlm):
    """Answers {"echo": <payload>} and reports two search queries per answer."""

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _request_text(llm_request)
        self.requests.append(text)
        yield _grounded_response(json.dumps({"echo": text}), ["q1", "q2"])


async def _run(budget: LlmBudget, jobs: list[AgentJob], llm: Any, **kw: Any) -> list[Any]:
    agent = Agent(
        name="echo",
        model=llm,
        instruction="Echo.",
        output_schema=Answer,
        before_model_callback=budget.before_model,
        after_model_callback=budget.after_model,
    )

    @node(rerun_on_resume=True)
    async def ask(node_input: Any, ctx: Any) -> list[dict[str, Any]]:
        calls = await run_agent_jobs(
            ctx, agent, jobs, budget=budget, concurrency=1, timeout_s=30, **kw
        )
        return [
            {"run_id": c.run_id, "output": c.output, "error": c.error, "skipped": c.skipped}
            for c in calls
        ]

    app = App(name="t", root_agent=Workflow(name="w", edges=[(START, ask)]))
    final = None
    msg = types.UserContent(parts=[types.Part(text="go")])
    async with Runner(
        app=app, session_service=InMemorySessionService(), auto_create_session=True
    ) as runner:
        async for ev in runner.run_async(user_id="u", session_id="s", new_message=msg):
            if ev.output is not None and "w@1" in (ev.node_info.output_for or []):
                final = ev.output
    assert final is not None
    return final


async def test_run_agent_jobs_skips_jobs_once_the_query_cap_is_reached() -> None:
    llm = GroundedLlm()
    budget = LlmBudget(max_calls=10, max_queries=2)
    calls = await _run(budget, [AgentJob("a", "first"), AgentJob("a", "second")], llm)
    assert calls[0]["output"] == {"echo": "first"} and calls[0]["run_id"] == "a"
    assert calls[1]["skipped"] == SKIP_MAX_QUERIES and calls[1]["run_id"] == "a_2"
    assert len(llm.requests) == 1
    assert budget.calls == 1 and budget.queries == 2
    assert [r.queries for r in budget.grounding["a"]] == [["q1", "q2"]]


async def test_run_agent_jobs_reports_empty_answers_without_retry_when_asked() -> None:
    llm = ScriptedLlm(responder=lambda text: "")
    budget = LlmBudget(max_calls=10)
    calls = await _run(budget, [AgentJob("x", "p")], llm, retry_empty=False)
    assert calls[0]["error"].startswith("empty answer")
    assert len(llm.requests) == 1
