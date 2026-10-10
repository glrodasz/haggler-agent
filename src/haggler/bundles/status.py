"""A bundle's display state (bundles.json v2 `display_status`). CONTRACT.

Derived, never typed: bundles.yaml only says `status: unverified|expired`. Precedence:

1. expired      `status: expired`, or valid_until before today. An expired bundle is still
                published for EXPIRED_KEEP_DAYS after it ended (expired_on) for the "Ofertas
                vencidas" section, then omitted.
2. needs_review (watcher data, milestone H7): an anchor missing on a page that loaded, a new
                document, or an official proposal that differs from the YAML.
3. verified     (watcher data, milestone H7): every decisive anchor verified within 15 days.
4. manual       verified_by user_report with a user_report dated within MANUAL_MAX_AGE_DAYS: shown
                as a manual verification, never as an official source.
5. unverified   everything else.

Every state but verified carries at least one Spanish reason (status_reasons_es). The card's
badge already shows the dates it rests on (manual age, end date), so the reasons do not repeat
them; a date only a reason gives is written out in Spanish. Confidence is low unless verified;
manual is medium at most.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from haggler.models.curated import Bundle

EXPIRED_KEEP_DAYS = 90
MANUAL_MAX_AGE_DAYS = 45
DISPLAY_STATUSES = ("verified", "needs_review", "manual", "unverified", "expired")
_CONFIDENCE_ORDER = ("low", "medium", "high")
_MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

_SOURCE_REASONS = {
    "search_summary": (
        "{what} tomados de un resumen de búsqueda; sin verificar en la página oficial."
    ),
    "agent_research": (
        "{what} propuestos por la investigación automática; sin verificar en la página oficial."
    ),
    "official_fetch": (
        "{what} copiados de la página oficial, pero el vigilante aún no los ha confirmado."
    ),
}
_WATCH_REASONS = {
    "manual": "Se revisa a mano: sus páginas no están en la vigilancia automática.",
    "off": "No se vigila: sus valores no se comprueban de forma automática.",
    "auto": "El vigilante aún no ha leído sus páginas oficiales.",
}


@dataclass(frozen=True)
class BundleState:
    display_status: str
    reasons_es: tuple[str, ...]
    confidence: str
    # The last watcher (or manual) verification and the last watcher check (H7).
    verified_at: date | None = None
    checked_at: date | None = None
    # False once an expired bundle is past the EXPIRED_KEEP_DAYS window: left out of the site.
    published: bool = True


def is_expired(bundle: Bundle, today: date) -> bool:
    return bundle.status == "expired" or (
        bundle.valid_until is not None and bundle.valid_until < today
    )


def expired_on(bundle: Bundle) -> date:
    """The day an expired bundle ended: its valid_until, or the day it was last checked when
    that comes first (a bundle marked expired before its announced end)."""
    if bundle.valid_until is None:
        return bundle.last_verified
    if bundle.status == "expired":
        return min(bundle.valid_until, bundle.last_verified)
    return bundle.valid_until


def _date_es(d: date) -> str:
    """'31 de agosto de 2026', like the site's long dates."""
    return f"{d.day} de {_MONTHS_ES[d.month - 1]} de {d.year}"


def _cap(confidence: str, ceiling: str) -> str:
    return min(confidence, ceiling, key=_CONFIDENCE_ORDER.index)


def _watcher_state(bundle: Bundle, today: date) -> BundleState | None:
    """needs_review / verified from the watcher's evidence (bundle_watch.json, milestone H7).
    There is no watcher data yet, so it never decides."""
    return None


def bundle_state(bundle: Bundle, *, today: date, price_missing: bool = False) -> BundleState:
    """`price_missing`: the bundle has no regular price today (an observed plan without a
    published row, or no amount in the YAML)."""
    if is_expired(bundle, today):
        reasons: list[str] = []
        if bundle.valid_until is not None and bundle.valid_until < today:
            reasons.append("La oferta pasó su fecha de fin.")
        if bundle.status == "expired":
            reasons.append("Marcada como vencida en los datos curados.")
        end = expired_on(bundle)
        return BundleState(
            display_status="expired",
            reasons_es=tuple(reasons),
            confidence="low",
            published=today <= end + timedelta(days=EXPIRED_KEEP_DAYS),
        )

    watched = _watcher_state(bundle, today)
    if watched is not None:
        return watched

    report = bundle.user_report
    price_reason = []
    if price_missing:
        price_reason.append(
            "Aún no hay un precio publicado de este plan en los datos diarios."
            if bundle.price.source == "observed"
            else "El precio no está registrado."
        )
    if bundle.verified_by == "user_report" and report is not None:
        age = (today - report.date).days
        if age <= MANUAL_MAX_AGE_DAYS:
            return BundleState(
                display_status="manual",
                # The badge shows its age, counted to the same day (meta.generated_at).
                reasons_es=("Verificación manual: no cuenta como fuente oficial.", *price_reason),
                confidence=_cap(bundle.confidence, "medium"),
                verified_at=report.date,
            )

    reasons = []
    if bundle.verified_by == "user_report":
        if report is None:
            reasons.append("Verificación manual sin fecha; no cuenta.")
        else:
            reasons.append(
                f"La verificación manual del {_date_es(report.date)} tiene más de "
                f"{MANUAL_MAX_AGE_DAYS} días."
            )
    else:
        what = "Componentes" if bundle.price.source == "observed" else "Precio y componentes"
        reasons.append(_SOURCE_REASONS[bundle.verified_by].format(what=what))
    reasons.extend(price_reason)
    reasons.append(_WATCH_REASONS[bundle.watch.mode])
    return BundleState(display_status="unverified", reasons_es=tuple(reasons), confidence="low")
