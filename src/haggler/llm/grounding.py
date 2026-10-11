"""Grounding metadata of model answers, as plain pydantic models (no ADK import)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
