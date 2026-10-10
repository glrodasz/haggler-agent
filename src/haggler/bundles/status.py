"""A bundle's display state (bundles.json v2 `display_status`). CONTRACT.

Derived, never typed: bundles.yaml only says `status: unverified|expired`. Precedence:

1. expired      `status: expired`, or valid_until before today. An expired bundle is still
                published for EXPIRED_KEEP_DAYS after it ended (expired_on) for the "Ofertas
                vencidas" section, then omitted.
2. needs_review (watcher data: bundle_watch.json, haggler.bundles.watch) a price, base_price or
                component anchor missing on a page that loaded (from the first miss: the excerpt
                is gone, or no longer states the YAML value; it stays so through the runs that
                cannot read the page, until a read verifies the anchor again or the YAML anchor
                is edited), or a newer edition of a versioned PDF, or a pending change: an
                official proposal whose value check passed and that differs from the YAML
                (haggler.bundles.proposals.pending_changes: a watcher hint or an agent value
                finding; it resolves itself once the YAML holds the value, and a rejection
                hides it). It outranks verified and manual: the YAML no longer says what the
                official page says.
3. verified     (watcher data) every decisive anchor (price, base_price, component) confirmed:
                verified by the watcher within VERIFY_MAX_AGE_DAYS (a page unreadable since then
                does not undo it; a missing, embedded-only or outdated anchor does, until a read
                verifies it again), plus at least one price anchor for a curated price (an
                observed price instead needs the daily data's row of its plan, within the same
                window) and one component anchor per included component. Confidence high;
                medium, with the reason, when this run's index did not establish a versioned PDF
                as the current edition (edition_check validity or unchecked: "Vigente según la
                propia lista 18 ...; no se pudo comprobar si hay otra más nueva.").
4. manual       verified_by user_report with a user_report dated within MANUAL_MAX_AGE_DAYS: shown
                as a manual verification, never as an official source.
5. unverified   everything else.

Watcher data counts only for a bundle whose watch.mode is auto and whose watch state is not
manual/off. Without it (no bundle_watch.json yet) states 2 and 3 never happen. With it, the
manual and unverified reasons also say what the watcher found ("robots.txt no permite leer
...", "El precio solo aparece en datos embebidos ...", "Según la lista 18; no se pudo comprobar
si hay otra más nueva.", "Último valor confirmado 21.900 COP el 3 de octubre de 2026; ya no se
confirma.").

Every state but verified carries at least one Spanish reason (status_reasons_es; verified only
the edition reason of a medium confidence), naming components by their service names. The card's
badge already shows the dates it rests on (manual age, end date, last check), so the reasons do
not repeat them; a date only a reason gives is written out in Spanish. Confidence is low unless
verified; manual is medium at most.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from haggler.models.bundles import AnchorCheck, BundleWatchState, PageCheck, anchor_hash
from haggler.models.curated import Anchor, Bundle

if TYPE_CHECKING:
    from haggler.bundles.proposals import Proposal

EXPIRED_KEEP_DAYS = 90
MANUAL_MAX_AGE_DAYS = 45
VERIFY_MAX_AGE_DAYS = 15
DISPLAY_STATUSES = ("verified", "needs_review", "manual", "unverified", "expired")
DECISIVE_FACTS = ("price", "base_price", "component")
# Anchor states that undo an earlier verification (unreadable / not_checked do not).
_UNCONFIRMED = ("missing", "embedded_only", "outdated_document")
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
# Once the watcher has read the pages, its own reasons say what it confirmed and what not.
_WATCHED_SOURCE_REASONS = {"official_fetch": "{what} copiados de la página oficial."}
_WATCH_REASONS = {
    "manual": "Se revisa a mano: sus páginas no están en la vigilancia automática.",
    "off": "No se vigila: sus valores no se comprueban de forma automática.",
    "auto": "El vigilante aún no ha leído sus páginas oficiales.",
}
_FACT_ES = {
    "price": "El precio",
    "base_price": "El precio del plan base",
    "intro": "El precio de introducción",
    "validity": "La vigencia",
}
_PAGE_REASONS = {
    "blocked": "{host} bloquea la lectura automática; no se confirma desde ahí.",
    "robots_disallowed": "robots.txt no permite leer {host}.",
    "not_found": "Una página vigilada de {host} ya no existe.",
    "failed": "No se pudo leer {host} en la última revisión.",
    "unsupported_content": "{host} no sirve una página de texto legible.",
    "pdf_error": "El PDF de {host} no tiene texto legible (cifrado, escaneado o demasiado grande).",
    "wrong_country": (
        "{host} mostró la página de otro país (dominio o moneda); no confirma esta oferta."
    ),
    "over_budget": "{host} no se leyó en la última revisión (límite de páginas por corrida).",
}


@dataclass(frozen=True)
class BundleState:
    display_status: str
    reasons_es: tuple[str, ...]
    confidence: str
    # The last watcher (or manual) verification and the last watcher check.
    verified_at: date | None = None
    checked_at: date | None = None
    # False once an expired bundle is past the EXPIRED_KEEP_DAYS window: left out of the site.
    published: bool = True
    # An intro anchor is confirmed (the only case the first-year cost uses the intro price).
    intro_confirmed: bool = False
    # Services whose component anchor is confirmed (bundles.json components[].confirmed).
    confirmed_components: frozenset[str] = field(default_factory=frozenset)


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


def _money_es(amount: Decimal, currency: str) -> str:
    """'21.900 COP', '13,99 EUR'."""
    if amount == amount.to_integral_value():
        text = f"{int(amount):,}".replace(",", ".")
    else:
        text = f"{amount:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} {currency}"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url).removeprefix("www.")


def _cap(confidence: str, ceiling: str) -> str:
    return min(confidence, ceiling, key=_CONFIDENCE_ORDER.index)


# ------------------------------------------------------------------ watcher evidence


def watched(bundle: Bundle, watch: BundleWatchState | None) -> BundleWatchState | None:
    """The watch state that counts for the bundle: auto mode, state not manual/off."""
    if watch is None or bundle.watch.mode != "auto" or watch.outcome in ("manual", "off"):
        return None
    return watch


def anchor_checks(
    bundle: Bundle, watch: BundleWatchState | None
) -> list[tuple[Anchor, AnchorCheck | None]]:
    """Every anchor of the YAML with the watcher's check of it (matched by anchor_hash)."""
    return [(a, watch.anchor(anchor_hash(a)) if watch else None) for a in bundle.watch.anchors]


def anchor_confirmed(check: AnchorCheck | None, today: date) -> bool:
    """Verified within VERIFY_MAX_AGE_DAYS, and not missing / embedded only / outdated since
    (AnchorCheck.unconfirmed: also through the runs that could not read it)."""
    if (
        check is None
        or check.last_verified_at is None
        or check.status in _UNCONFIRMED
        or check.unconfirmed is not None
    ):
        return False
    return 0 <= (today - check.last_verified_at).days <= VERIFY_MAX_AGE_DAYS


@dataclass(frozen=True)
class _WatchView:
    decision: BundleState | None = None
    reasons: tuple[str, ...] = ()
    checked_at: date | None = None
    intro_confirmed: bool = False
    confirmed_components: frozenset[str] = frozenset()


def _what(anchor: Anchor, names: Mapping[str, str]) -> str:
    if anchor.fact == "component":
        sid = anchor.service_id or ""
        return f"El componente {names.get(sid, sid)}"
    return _FACT_ES[anchor.fact]


def _edition_reasons(bundle: Bundle, pages: Iterable[PageCheck]) -> list[str]:
    """A versioned PDF that this run's index did not establish as the current edition."""
    out: list[str] = []
    for p in pages:
        if p.edition is None or p.status == "superseded":
            continue
        if p.edition_check == "unchecked":
            out.append(f"Según la lista {p.edition}; no se pudo comprobar si hay otra más nueva.")
        elif p.edition_check == "validity":
            since = f" (desde el {_date_es(bundle.valid_from)})" if bundle.valid_from else ""
            out.append(
                f"Vigente según la propia lista {p.edition}{since}; no se pudo comprobar si hay "
                "otra más nueva."
            )
    return list(dict.fromkeys(out))


def _page_reasons(bundle: Bundle, pages: Iterable[PageCheck], today: date) -> list[str]:
    pages = list(pages)
    out: list[str] = []
    for p in pages:
        host = _host(p.final_url or p.url)
        if p.paused(today) and p.paused_until is not None:
            out.append(
                f"La lectura de {host} está en pausa hasta el {_date_es(p.paused_until)} tras "
                "varias revisiones fallidas."
            )
        elif p.status in _PAGE_REASONS:
            out.append(_PAGE_REASONS[p.status].format(host=host))
    out += _edition_reasons(bundle, pages)
    return list(dict.fromkeys(out))


def _last_confirmed(
    bundle: Bundle, pairs: list[tuple[Anchor, AnchorCheck | None]], today: date
) -> list[str]:
    """'Último valor confirmado ...' for a price anchor that stopped confirming."""
    out: list[str] = []
    price = bundle.price
    for anchor, check in pairs:
        if anchor.fact != "price" or check is None or check.last_verified_at is None:
            continue
        if anchor_confirmed(check, today) or price.amount is None:
            continue
        out.append(
            f"Último valor confirmado {_money_es(price.amount, price.currency)} el "
            f"{_date_es(check.last_verified_at)}; ya no se confirma."
        )
    return list(dict.fromkeys(out))


def _watch_view(
    bundle: Bundle,
    watch: BundleWatchState | None,
    today: date,
    *,
    price_missing: bool,
    price_observed_on: date | None,
    names: Mapping[str, str],
) -> _WatchView:
    w = watched(bundle, watch)
    if w is None:
        return _WatchView()
    pairs = anchor_checks(bundle, w)
    # Only the pages the YAML still watches (a page replaced in the YAML keeps no say).
    current = {str(p.url) for p in bundle.watch.pages}
    current |= {str(p.index_url) for p in bundle.watch.pages if p.index_url is not None}
    pages = {p.url: p for p in w.pages if p.url in current}
    confirmed = {id(a): anchor_confirmed(c, today) for a, c in pairs}
    components = frozenset(
        a.service_id
        for a, _c in pairs
        if a.fact == "component" and a.service_id and confirmed[id(a)]
    )
    intro = bundle.price.intro_amount is not None and any(
        a.fact == "intro" and confirmed[id(a)] for a, _c in pairs
    )
    last = _last_confirmed(bundle, pairs, today)
    page_why = _page_reasons(bundle, pages.values(), today)

    review: list[str] = []
    for a, c in pairs:
        if a.fact not in DECISIVE_FACTS or c is None:
            continue
        host = _host(c.url)
        if c.status == "missing":
            if c.via is None:
                review.append(f"{_what(a, names)} ya no aparece en {host}.")
            else:
                review.append(
                    f"{_what(a, names)} sigue en {host}, pero ya no indica el valor registrado."
                )
        elif c.unconfirmed == "missing" and c.unconfirmed_on is not None:
            # Missing when the page last loaded; the runs since could not read it.
            review.append(
                f"{_what(a, names)} no aparecía en {host} la última vez que se pudo leer "
                f"({_date_es(c.unconfirmed_on)})."
            )
    for p in pages.values():
        if p.status == "superseded":
            review.append(
                f"Hay una edición más nueva de la lista de precios ({p.newest_edition}); los "
                f"valores son de la edición {p.edition} hasta revisarlos."
            )
    if review:
        decision = BundleState(
            display_status="needs_review",
            reasons_es=tuple(dict.fromkeys([*review, *page_why, *last])),
            confidence="low",
            checked_at=w.checked_at,
            confirmed_components=components,
        )
        return _WatchView(decision=decision, checked_at=w.checked_at)

    decisive = [(a, c) for a, c in pairs if a.fact in DECISIVE_FACTS]
    included = {c.service_id for c in bundle.components if c.inclusion == "included"}
    why: list[str] = []
    if bundle.price.source == "curated" and not any(a.fact == "price" for a, _c in pairs):
        why.append("El precio no tiene un ancla que el vigilante pueda buscar.")
    unanchored = included - {a.service_id for a, _c in pairs if a.fact == "component"}
    if unanchored:
        n = len(unanchored)
        why.append(
            "Falta el ancla de 1 componente incluido."
            if n == 1
            else f"Faltan las anclas de {n} componentes incluidos."
        )
    for a, c in decisive:
        if c is None:
            continue
        if c.status == "embedded_only":
            why.append(
                f"{_what(a, names)} solo aparece en datos embebidos de la página, no en su texto "
                "visible."
            )
        elif c.unconfirmed == "embedded_only":
            why.append(
                f"{_what(a, names)} solo aparecía en datos embebidos de la página la última vez "
                "que se pudo leer."
            )
    observed_ok = not price_missing and (
        price_observed_on is None or (today - price_observed_on).days <= VERIFY_MAX_AGE_DAYS
    )
    if bundle.price.source == "observed" and not price_missing and not observed_ok:
        why.append("El precio observado no es de los datos diarios recientes.")
    stale = [a for a, _c in decisive if not confirmed[id(a)]]
    if not why and not stale and (bundle.price.source == "curated" or observed_ok):
        dates = [c.last_verified_at for _a, c in decisive if c and c.last_verified_at]
        if bundle.price.source == "observed" and price_observed_on is not None:
            dates.append(price_observed_on)
        # A versioned PDF this run's index did not establish as current: at most medium.
        decisive_urls = {str(a.url) for a, _c in decisive}
        edition_why = _edition_reasons(bundle, (p for u, p in pages.items() if u in decisive_urls))
        decision = BundleState(
            display_status="verified",
            reasons_es=tuple(edition_why),
            confidence="medium" if edition_why else "high",
            verified_at=min(dates) if dates else w.checked_at,
            checked_at=w.checked_at,
            intro_confirmed=intro,
            confirmed_components=components,
        )
        return _WatchView(decision=decision, checked_at=w.checked_at)

    reasons = [*why, *page_why, *last]
    if stale and not reasons:
        reasons.append("El vigilante aún no ha confirmado todas sus anclas.")
    return _WatchView(
        reasons=tuple(dict.fromkeys(reasons)),
        checked_at=w.checked_at,
        intro_confirmed=intro,
        confirmed_components=components,
    )


# ------------------------------------------------------------------ pending changes

_PENDING_SOURCES_ES = {"watcher": "el vigilante", "agent": "la investigación automática"}


def _join_es(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} y {items[-1]}"


def _pending_reasons(changes: Sequence[Proposal]) -> list[str]:
    """One reason naming the fields with a pending change and who proposed them (the card
    lists the values under "Cambios pendientes de revisión")."""
    if not changes:
        return []
    labels = list(dict.fromkeys(c.label_es[:1].lower() + c.label_es[1:] for c in changes))
    sources = list(dict.fromkeys(_PENDING_SOURCES_ES[c.source] for c in changes))
    return [
        f"Cambios pendientes de revisión: {_join_es(labels)} (según {_join_es(sources)}, en la "
        "página oficial)."
    ]


# ------------------------------------------------------------------ state


def bundle_state(
    bundle: Bundle,
    *,
    today: date,
    price_missing: bool = False,
    watch: BundleWatchState | None = None,
    price_observed_on: date | None = None,
    service_names: Mapping[str, str] | None = None,
    pending_changes: Sequence[Proposal] = (),
) -> BundleState:
    """`price_missing`: the bundle has no regular price today (an observed plan without a
    published row, or no amount in the YAML). `watch`: the watcher's state of the bundle
    (bundle_watch.json); `price_observed_on`: the date of the daily row an observed price comes
    from; `service_names`: {service_id: name} for the reasons (the id when absent);
    `pending_changes`: haggler.bundles.proposals.pending_changes of the bundle."""
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

    view = _watch_view(
        bundle,
        watch,
        today,
        price_missing=price_missing,
        price_observed_on=price_observed_on,
        names=service_names or {},
    )
    pending_why = _pending_reasons(pending_changes)
    if pending_why:
        decided = view.decision
        if decided is not None and decided.display_status == "needs_review":
            reasons = (*decided.reasons_es, *pending_why)
            return replace(decided, reasons_es=tuple(dict.fromkeys(reasons)))
        # Outranks verified (its anchors still count as confirmed) and manual.
        return BundleState(
            display_status="needs_review",
            reasons_es=tuple(dict.fromkeys([*pending_why, *view.reasons])),
            confidence="low",
            checked_at=decided.checked_at if decided else view.checked_at,
            confirmed_components=(
                decided.confirmed_components if decided else view.confirmed_components
            ),
        )
    if view.decision is not None:
        return view.decision
    extra = {
        "checked_at": view.checked_at,
        "intro_confirmed": view.intro_confirmed,
        "confirmed_components": view.confirmed_components,
    }

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
                reasons_es=(
                    "Verificación manual: no cuenta como fuente oficial.",
                    *price_reason,
                    *view.reasons,
                ),
                confidence=_cap(bundle.confidence, "medium"),
                verified_at=report.date,
                **extra,  # type: ignore[arg-type]
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
        texts = (
            _SOURCE_REASONS
            if view.checked_at is None
            else _SOURCE_REASONS | _WATCHED_SOURCE_REASONS
        )
        reasons.append(texts[bundle.verified_by].format(what=what))
    reasons.extend(price_reason)
    if view.reasons:
        reasons.extend(view.reasons)
    elif view.checked_at is None:
        # Only before the watcher read the pages: once it did, its own reasons stand.
        reasons.append(_WATCH_REASONS[bundle.watch.mode])
    return BundleState(
        display_status="unverified",
        reasons_es=tuple(reasons),
        confidence="low",
        **extra,  # type: ignore[arg-type]
    )
