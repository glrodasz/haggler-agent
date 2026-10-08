"""The one LLM definition of v1: a tool-less price extractor with structured output.

The model only quotes: it never computes or converts. Every item it returns is re-checked in code
(haggler.pipeline.nodes.post_check) against the exact page text it was given, so a hallucinated
price cannot reach the published data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pydantic
from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import RetryConfig
from google.genai import types

from haggler.models.run import LlmExtraction

AGENT_NAME = "price_extractor"

INSTRUCTION = """\
You read the text of ONE official web page (or an App Store in-app purchase list) for one
subscription service in one country, and report the subscription prices printed on it.

The user message is a JSON job with these fields: service_id, country (ISO 3166-1 alpha-2),
channel, source_url, expected_currencies, plans (the allowed plan ids, each with a label and
hints), reason and page_text.

Rules:
1. Report ONLY prices that literally appear in page_text. Never guess, compute, convert, round,
   add or remove taxes, or use prices you remember from elsewhere. Nothing printed, nothing
   reported.
2. price_text: copy the price exactly as printed, character for character, with its currency
   symbol or code (for example "129 kr", "COP 18.900", "US$4.99", "R$ 34,90").
3. evidence_snippet: a verbatim excerpt of page_text, at most 200 characters, that contains the
   plan name (when printed) and the price. Copy it exactly; never paraphrase, never join distant
   fragments, never add words.
4. plan_id: map each price to exactly one id from plans, using its label and hints. Use "other"
   for anything no allowed plan fits: bundles, add-ons, extra members, gift cards, one-off
   purchases, other services.
5. currency: the ISO-4217 code of the printed price. billing_period: "month", "quarter" or
   "year", as printed. Prefer the regular recurring price over introductory, trial or discounted
   prices; skip a price that is only valid for a limited introductory period.
6. page_status: "wrong_country" if the page is for another country or shows prices in a currency
   that is not in expected_currencies; "blocked" for a bot check, captcha or access-denied page;
   "no_prices" if no subscription price is printed; otherwise "ok".
7. reason "unmapped_iap": page_text is a list of in-app purchase lines ("name - price"). Map each
   line to a plan_id (or "other"); price_text is the price part of the line and
   evidence_snippet the whole line.
8. reason "parser_failed" or "second_opinion": extract every subscription price as described.
9. notes: one short sentence, or an empty string.

Answer with the JSON object of the response schema and nothing else.
"""


def instruction(ctx: ReadonlyContext) -> str:
    """Instruction provider: a callable, so ADK does not treat any brace as a state template."""
    return INSTRUCTION


def build_price_extractor(
    model: str | BaseLlm,
    *,
    after_model_callback: Callable[..., Any] | None = None,
    validation_attempts: int = 2,
) -> Agent:
    """`after_model_callback` lets the pipeline count real model calls (retries included)."""
    return Agent(
        name=AGENT_NAME,
        model=model,
        description="Quotes subscription prices printed on one official page.",
        instruction=instruction,
        output_schema=LlmExtraction,
        generate_content_config=types.GenerateContentConfig(temperature=0, max_output_tokens=2048),
        # A malformed JSON answer raises pydantic.ValidationError: ask once more, then give up
        # (the caller records the job as an error).
        retry_config=RetryConfig(
            max_attempts=validation_attempts,
            initial_delay=0.5,
            max_delay=2,
            exceptions=[pydantic.ValidationError],
        ),
        after_model_callback=after_model_callback,
    )
