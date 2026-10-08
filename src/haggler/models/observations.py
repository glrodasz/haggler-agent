"""Price observations produced by adapters, the pipeline and the publish step."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from haggler.models.catalog import Channel, SourceKind

BillingPeriod = Literal["month", "quarter", "year"]
Extraction = Literal["parser", "llm"]
ObsStatus = Literal["accepted", "quarantined", "rejected", "carried_forward"]

PERIOD_MONTHS: dict[str, int] = {"month": 1, "quarter": 3, "year": 12}


def obs_key(service_id: str, plan_id: str, country: str, channel: str, period: str) -> str:
    return f"{service_id}:{plan_id}:{country}:{channel}:{period}"


class ObservationDraft(BaseModel):
    """What an adapter extracts from one page, before pipeline enrichment."""

    model_config = ConfigDict(extra="forbid")

    service_id: str
    plan_id: str
    plan_label_raw: str
    country: str
    currency: str
    amount: Decimal
    billing_period: BillingPeriod = "month"
    evidence_snippet: str = Field(default="", max_length=240)
    flags: list[str] = Field(default_factory=list)


class PriceObservation(BaseModel):
    """One price as seen by today's run (stored in GCS snapshots)."""

    model_config = ConfigDict(extra="forbid")

    obs_key: str
    service_id: str
    plan_id: str
    plan_label_raw: str
    country: str
    currency: str
    amount: Decimal
    billing_period: BillingPeriod
    monthly_equiv: Decimal
    monthly_eur: Decimal | None = None
    vat_included: bool | None = None
    channel: Channel
    source_url: str
    source_kind: SourceKind
    adapter_id: str
    observed_at: datetime
    content_hash: str
    extraction: Extraction
    reused: bool = False
    confidence: float = Field(ge=0, le=1)
    evidence_snippet: str = ""
    status: ObsStatus = "accepted"
    flags: list[str] = Field(default_factory=list)

    @classmethod
    def from_draft(
        cls,
        draft: ObservationDraft,
        *,
        channel: Channel,
        source_url: str,
        source_kind: SourceKind,
        adapter_id: str,
        observed_at: datetime,
        content_hash: str,
        extraction: Extraction,
        vat_included: bool | None,
        confidence: float,
        reused: bool = False,
    ) -> PriceObservation:
        months = PERIOD_MONTHS[draft.billing_period]
        return cls(
            obs_key=obs_key(
                draft.service_id, draft.plan_id, draft.country, channel, draft.billing_period
            ),
            service_id=draft.service_id,
            plan_id=draft.plan_id,
            plan_label_raw=draft.plan_label_raw,
            country=draft.country,
            currency=draft.currency,
            amount=draft.amount,
            billing_period=draft.billing_period,
            monthly_equiv=(draft.amount / months).quantize(Decimal("0.01")),
            vat_included=vat_included,
            channel=channel,
            source_url=source_url,
            source_kind=source_kind,
            adapter_id=adapter_id,
            observed_at=observed_at,
            content_hash=content_hash,
            extraction=extraction,
            reused=reused,
            confidence=confidence,
            evidence_snippet=draft.evidence_snippet,
            flags=list(draft.flags),
        )


class PublishedPrice(BaseModel):
    """Repo-facing price row. No per-run or FX-dependent fields so git diffs stay meaningful."""

    model_config = ConfigDict(extra="forbid")

    obs_key: str
    service_id: str
    plan_id: str
    plan_label_raw: str
    country: str
    currency: str
    amount: Decimal
    billing_period: BillingPeriod
    monthly_equiv: Decimal
    vat_included: bool | None
    channel: Channel
    source_url: str
    source_kind: SourceKind
    adapter_id: str
    extraction: Extraction
    confidence: float
    status: ObsStatus
    flags: list[str] = Field(default_factory=list)
    first_seen: date
    last_changed: date
