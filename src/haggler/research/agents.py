"""The research agents: Gemini with the built-in google_search tool and a response schema.

One single_turn agent per subject kind, each run directly with ctx.run_node (never nested as a
sub-agent of another agent: ADK loops on that). The agents only DISCOVER: every claim they return
is checked by our own fetch of the cited page (research.evidence) before it counts, and the text
of fetched pages never goes back to the model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pydantic
from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.base_llm import BaseLlm
from google.adk.tools import google_search
from google.adk.workflow import RetryConfig

from haggler.llm.models import generate_content_config
from haggler.models.research import ResearchAnswer, SubjectKind
from haggler.research.fields import vocabulary_prompt
from haggler.research.kinds import KINDS

# Search grounding plus several claims with quotes need more room than the price extractor.
MAX_OUTPUT_TOKENS = 8192
AGENT_NAMES: dict[SubjectKind, str] = {kind: spec.agent_name for kind, spec in KINDS.items()}

COMMON_RULES = """\
Rules (all mandatory):
1. Use Google Search. Prefer pages on the official domains listed in the job (terms of use,
   help center, legal pages), then reputable press. Forums only when nothing else exists.
2. Report a fact only when a page states it. Each claim: one field and one allowed value from
   the vocabulary below, the exact https URL of the page itself (its own address, never a
   vertexaisearch.cloud.google.com link), and `quote`: a CONTIGUOUS verbatim
   excerpt of that page (20-300 characters, original language, copied character for character,
   no ellipsis, no paraphrase, no translation; letters such as å, ö, ñ, ã and typographic
   quotes stay as they are, never replaced by "%" or other placeholders) that states the fact.
3. Never infer, generalize from other countries, or use memory without a source. If sources
   disagree, report each with its own quote.
4. Never describe or suggest VPNs, proxies, fake addresses, false declarations or any way to
   evade a check. Report only what the service or issuer itself says or does.
5. status "nothing_found" with an empty claims list when no page states any listed fact.
6. open_questions: up to 3 short questions that the sources left open. notes: one short
   sentence or "". suggestions: [] unless the job asks for it.

Answer with the JSON object of the response schema and nothing else.
"""

# One prompt per registered kind (research.kinds); test_research_kinds checks none is missing.
INSTRUCTIONS: dict[SubjectKind, str] = {
    "service": """\
You research the general country rules of ONE consumer subscription service (the user message is
a JSON job: subject, service, homepage, official_domains). Find what its terms of use and help
pages say about: which country an account belongs to, whether the subscriber must live in that
country, changing the account country, using it while travelling, VPN/proxy rules, and dated
events where the service acted against accounts registered in another country than where the
user lives.

Vocabulary (field: allowed values):
{vocabulary}

{rules}""",
    "pair": """\
You research what it takes to subscribe to ONE service's offer in ONE country (the user message
is a JSON job: subject, service, country, country_name, official_domains). Find what the
service's official pages (or reputable press) say about subscribing in that country: whether the
service is sold there, whether the payment card must be issued there, gift cards, whether the
location is checked at sign-up or periodically, billing address, and local ID or phone number
requirements. Facts about other countries do not count.

Vocabulary (field: allowed values):
{vocabulary}

{rules}""",
    "instrument": """\
You research ONE payment provider (bank, e-money institution or card issuer) for a person living
in the residence country of the job (the user message is a JSON job: subject, name,
issuer_entity, official_domains, residence_country). Find on the provider's official pages:
the legal entity that issues the card, the country where the card is issued, the IBAN country,
which countries of residence may open an account, whether onboarding is fully online, which ID
documents are accepted, and whether an address in the issuer's country is required.
suggestions: up to 3 other providers the official pages or press present as options for
residents of that country (names only).

Vocabulary (field: allowed values):
{vocabulary}

{rules}""",
    "bundle": """\
You research ONE bundle: a package of subscriptions that ONE provider sells in ONE country (the
user message is a JSON job: subject, bundle_id, name, tier, aliases, provider, country,
country_name, currency, official_url, official_domains, known_pages, components, requirements,
yaml_values, service_ids). yaml_values are the values we have on file, which may be outdated or
wrong: confirm or correct each one with a quote. Find, on the provider's OFFICIAL pages FOR THAT
COUNTRY ONLY (official_domains; known_pages are official pages we already know, price-list PDFs
included): the regular monthly price, the introductory price and how many months it lasts or
when it ends, the base plan price when the bundle is sold on top of a plan, the minimum contract,
the last day the offer is sold, which services it includes (and which plan of each) or
discounts, whether it is still sold, and the official price list. Then what the bundle's terms
say about residence, subscribing from another country, what fixes the account country, checks
at sign-up, billing address, local ID, a line of the provider, the payment method, credit checks
and use while travelling.
Value fields (prices, months, dates, included services, discounts, availability, the price list)
count ONLY with a quote from an official page of the job's country: pages of another country,
resellers, comparison sites and press never confirm one. The quote must contain the amount, date
or service name exactly as the page prints it (for a price: the amount with its currency sign
and the tier name or the line that names it); write the value in the format the vocabulary
gives, converting the page's notation ("$ 21.900/mes" -> "21900 COP/month"). Report a value even
when it equals yaml_values, and report each different value you find with its own quote.
Services are named by the ids of service_ids (bundle_includes / bundle_discount); a service that
is not listed there cannot be reported. bundle_discount is a percentage printed with "%" (or "por
ciento"): free months, a number of devices or an amount saved are not discounts, do not report
them as one.
suggestions: up to 3 other bundles of the same provider in the same country that its official
pages list (names as printed), or [].

Vocabulary (field: allowed values):
{vocabulary}

{rules}""",
}


def instruction_text(kind: SubjectKind) -> str:
    return INSTRUCTIONS[kind].format(vocabulary=vocabulary_prompt(kind), rules=COMMON_RULES)


def _provider(kind: SubjectKind) -> Callable[[ReadonlyContext], str]:
    text = instruction_text(kind)

    def instruction(ctx: ReadonlyContext) -> str:
        """A callable, so ADK does not treat braces as state templates."""
        return text

    return instruction


def build_researcher(
    kind: SubjectKind,
    model: str | BaseLlm,
    *,
    before_model_callback: Callable[..., Any] | None = None,
    after_model_callback: Callable[..., Any] | None = None,
) -> Agent:
    """A malformed answer (pydantic.ValidationError) is asked once more; an empty one is not
    (the runner records it): each attempt costs search queries."""
    return Agent(
        name=AGENT_NAMES[kind],
        model=model,
        mode="single_turn",
        description=f"Finds sourced facts about one {kind} for the feasibility research.",
        instruction=_provider(kind),
        tools=[google_search],
        output_schema=ResearchAnswer,
        generate_content_config=generate_content_config(model, max_output_tokens=MAX_OUTPUT_TOKENS),
        retry_config=RetryConfig(
            max_attempts=2,
            initial_delay=1,
            max_delay=4,
            exceptions=[pydantic.ValidationError],
        ),
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
    )
