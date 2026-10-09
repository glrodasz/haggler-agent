"""Model construction and Gemini request settings shared by every agent."""

from __future__ import annotations

from google.adk.models import Gemini, LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.genai import types

from haggler.config import Settings, llm_credentials_available

# Per HTTP attempt to the model (genai's default is no timeout at all); the workflows also cap
# each whole job (Settings.llm_job_timeout_s and friends).
LLM_HTTP_TIMEOUT_MS = 60_000
# genai's own default set: replacing it with a shorter list would stop retrying 408/502/504.
LLM_RETRY_STATUS_CODES = [408, 429, 500, 502, 503, 504]


class HagglerGemini(Gemini):
    """Gemini that pairs an output schema with tools on every backend.

    ADK only grants that capability on Vertex AI; Gemini 3 accepts a response schema next to the
    built-in google_search tool on AI Studio too, and without the capability ADK moves the schema
    into a prompt workaround whose answer it never validates."""

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(
            **super().capabilities.model_dump() | {"output_schema_and_tools": True}
        )


def model_name(model: str | BaseLlm) -> str:
    """Bare model id ("gemini-3.1-flash-lite") of a model string, resource path or BaseLlm."""
    name = model if isinstance(model, str) else str(getattr(model, "model", "") or "")
    return name.rsplit("/", 1)[-1]


def generate_content_config(
    model: str | BaseLlm, *, max_output_tokens: int
) -> types.GenerateContentConfig:
    """Gemini 3 settings: the model's default temperature (Google advises against lowering it for
    Gemini 3), low thinking, and room for thoughts plus the JSON answer (a truncated answer fails
    schema validation and costs a retry)."""
    thinking = None
    if model_name(model).startswith("gemini-3"):
        thinking = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
    return types.GenerateContentConfig(
        max_output_tokens=max_output_tokens, thinking_config=thinking
    )


def build_model(
    settings: Settings, model: str | None = None, *, max_calls: int | None = None
) -> BaseLlm | None:
    """Gemini with HTTP-level retries and a per-request timeout, or None when the LLM is off, has
    no call budget (max_calls, default HAGGLER_MAX_LLM_CALLS, <= 0) or has no credentials.
    `model` defaults to HAGGLER_EXTRACT_MODEL."""
    budget = settings.max_llm_calls if max_calls is None else max_calls
    if not settings.llm_enabled or budget <= 0:
        return None
    if not llm_credentials_available():
        return None
    retry = types.HttpRetryOptions(
        attempts=4, initial_delay=2, http_status_codes=list(LLM_RETRY_STATUS_CODES)
    )
    return HagglerGemini(
        model=model or settings.extract_model,
        retry_options=retry,
        # ADK builds the client's HttpOptions from retry_options only; client_kwargs replaces
        # them, so the retries are repeated here next to the timeout.
        client_kwargs={
            "http_options": types.HttpOptions(timeout=LLM_HTTP_TIMEOUT_MS, retry_options=retry)
        },
    )
