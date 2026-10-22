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

Every state but verified carries at least one reason (status_reasons_es/_en/_pt, each a
haggler.i18n.Text; verified only the edition reason of a medium confidence), naming components
by their service names. The card's badge already shows the dates it rests on (manual age, end
date, last check), so the reasons do not repeat them; a date only a reason gives is written out
in each language. Confidence is low unless
verified; manual is medium at most.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from haggler.i18n import Text, concat, fill, join_list, long_date, money, tr
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
_SOURCE_REASONS = {
    "search_summary": tr(
        "{what} tomados de un resumen de búsqueda; sin verificar en la página oficial.",
        "{what} taken from a search summary; not checked on the official page.",
        "{what} tirados de um resumo de busca; sem verificar na página oficial.",
    ),
    "agent_research": tr(
        "{what} propuestos por la investigación automática; sin verificar en la página oficial.",
        "{what} proposed by automated research; not checked on the official page.",
        "{what} propostos pela pesquisa automática; sem verificar na página oficial.",
    ),
    "official_fetch": tr(
        "{what} copiados de la página oficial, pero el vigilante aún no los ha confirmado.",
        "{what} copied from the official page, but the watcher has not confirmed them yet.",
        "{what} copiados da página oficial, mas o vigilante ainda não os confirmou.",
    ),
}
# Once the watcher has read the pages, its own reasons say what it confirmed and what not.
_WATCHED_SOURCE_REASONS = {
    "official_fetch": tr(
        "{what} copiados de la página oficial.",
        "{what} copied from the official page.",
        "{what} copiados da página oficial.",
    )
}
# The {what} of a source reason.
_PRICE_AND_COMPONENTS = tr("Precio y componentes", "Price and components", "Preço e componentes")
_COMPONENTS = tr("Componentes", "Components", "Componentes")
_WATCH_REASONS = {
    "manual": tr(
        "Se revisa a mano: sus páginas no están en la vigilancia automática.",
        "Checked by hand: its pages are not in the automatic watch.",
        "Revisado à mão: suas páginas não estão na vigilância automática.",
    ),
    "off": tr(
        "No se vigila: sus valores no se comprueban de forma automática.",
        "Not watched: its values are not checked automatically.",
        "Não vigiado: seus valores não são verificados automaticamente.",
    ),
    "auto": tr(
        "El vigilante aún no ha leído sus páginas oficiales.",
        "The watcher has not read its official pages yet.",
        "O vigilante ainda não leu suas páginas oficiais.",
    ),
}
_FACTS = {
    "price": tr("El precio", "The price", "O preço"),
    "base_price": tr("El precio del plan base", "The base plan price", "O preço do plano base"),
    "intro": tr("El precio de introducción", "The introductory price", "O preço promocional"),
    "validity": tr("La vigencia", "The validity", "A vigência"),
}
_PAGE_REASONS = {
    "blocked": tr(
        "{host} bloquea la lectura automática; no se confirma desde ahí.",
        "{host} blocks automated reading; nothing is confirmed from there.",
        "{host} bloqueia a leitura automática; nada se confirma por lá.",
    ),
    "robots_disallowed": tr(
        "robots.txt no permite leer {host}.",
        "robots.txt does not allow reading {host}.",
        "O robots.txt não permite ler {host}.",
    ),
    "not_found": tr(
        "Una página vigilada de {host} ya no existe.",
        "A watched page on {host} no longer exists.",
        "Uma página vigiada de {host} não existe mais.",
    ),
    "failed": tr(
        "No se pudo leer {host} en la última revisión.",
        "{host} could not be read in the last check.",
        "Não foi possível ler {host} na última revisão.",
    ),
    "unsupported_content": tr(
        "{host} no sirve una página de texto legible.",
        "{host} does not serve a readable text page.",
        "{host} não serve uma página de texto legível.",
    ),
    "pdf_error": tr(
        "El PDF de {host} no tiene texto legible (cifrado, escaneado o demasiado grande).",
        "The PDF on {host} has no readable text (encrypted, scanned or too large).",
        "O PDF de {host} não tem texto legível (criptografado, escaneado ou grande demais).",
    ),
    "wrong_country": tr(
        "{host} mostró la página de otro país (dominio o moneda); no confirma esta oferta.",
        "{host} showed another country's page (domain or currency); it does not confirm this "
        "offer.",
        "{host} mostrou a página de outro país (domínio ou moeda); não confirma esta oferta.",
    ),
    "over_budget": tr(
        "{host} no se leyó en la última revisión (límite de páginas por corrida).",
        "{host} was not read in the last check (page limit per run).",
        "{host} não foi lido na última revisão (limite de páginas por execução).",
    ),
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


def _what(anchor: Anchor, names: Mapping[str, str]) -> Text:
    if anchor.fact == "component":
        sid = anchor.service_id or ""
        name = names.get(sid, sid)
        return tr(f"El componente {name}", f"The component {name}", f"O componente {name}")
    return _FACTS[anchor.fact]


def _edition_reasons(bundle: Bundle, pages: Iterable[PageCheck]) -> list[str]:
    """A versioned PDF that this run's index did not establish as the current edition."""
    out: list[str] = []
    for p in pages:
        if p.edition is None or p.status == "superseded":
            continue
        e = p.edition
        if p.edition_check == "unchecked":
            out.append(
                tr(
                    f"Según la lista {e}; no se pudo comprobar si hay otra más nueva.",
                    f"According to price list {e}; it could not be checked whether a newer one "
                    "exists.",
                    f"Segundo a tabela {e}; não foi possível verificar se há outra mais nova.",
                )
            )
        elif p.edition_check == "validity":
            since = (
                fill(tr(" (desde el {d})", " (since {d})", " (desde {d})"), d=long_date(start))
                if (start := bundle.valid_from)
                else ""
            )
            out.append(
                concat(
                    tr(
                        f"Vigente según la propia lista {e}",
                        f"Valid according to price list {e} itself",
                        f"Vigente segundo a própria tabela {e}",
                    ),
                    since,
                    tr(
                        "; no se pudo comprobar si hay otra más nueva.",
                        "; it could not be checked whether a newer one exists.",
                        "; não foi possível verificar se há outra mais nova.",
                    ),
                )
            )
    return list(dict.fromkeys(out))


def _page_reasons(bundle: Bundle, pages: Iterable[PageCheck], today: date) -> list[str]:
    pages = list(pages)
    out: list[str] = []
    for p in pages:
        host = _host(p.final_url or p.url)
        if p.paused(today) and p.paused_until is not None:
            out.append(
                fill(
                    tr(
                        "La lectura de {host} está en pausa hasta el {until} tras varias "
                        "revisiones fallidas.",
                        "Reading {host} is paused until {until} after several failed checks.",
                        "A leitura de {host} está em pausa até {until} após várias revisões "
                        "falhas.",
                    ),
                    host=host,
                    until=long_date(p.paused_until),
                )
            )
        elif p.status in _PAGE_REASONS:
            out.append(fill(_PAGE_REASONS[p.status], host=host))
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
            fill(
                tr(
                    "Último valor confirmado {value} el {day}; ya no se confirma.",
                    "Last confirmed value {value} on {day}; no longer confirmed.",
                    "Último valor confirmado {value} em {day}; não se confirma mais.",
                ),
                value=money(price.amount, price.currency),
                day=long_date(check.last_verified_at),
            )
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
        what = _what(a, names)
        if c.status == "missing":
            if c.via is None:
                review.append(
                    fill(
                        tr(
                            "{what} ya no aparece en {host}.",
                            "{what} no longer appears on {host}.",
                            "{what} não aparece mais em {host}.",
                        ),
                        what=what,
                        host=host,
                    )
                )
            else:
                review.append(
                    fill(
                        tr(
                            "{what} sigue en {host}, pero ya no indica el valor registrado.",
                            "{what} is still on {host}, but no longer states the recorded value.",
                            "{what} continua em {host}, mas não indica mais o valor registrado.",
                        ),
                        what=what,
                        host=host,
                    )
                )
        elif c.unconfirmed == "missing" and c.unconfirmed_on is not None:
            # Missing when the page last loaded; the runs since could not read it.
            review.append(
                fill(
                    tr(
                        "{what} no aparecía en {host} la última vez que se pudo leer ({day}).",
                        "{what} did not appear on {host} the last time it could be read ({day}).",
                        "{what} não aparecia em {host} da última vez que se pôde ler ({day}).",
                    ),
                    what=what,
                    host=host,
                    day=long_date(c.unconfirmed_on),
                )
            )
    for p in pages.values():
        if p.status == "superseded":
            new, old = p.newest_edition, p.edition
            review.append(
                tr(
                    f"Hay una edición más nueva de la lista de precios ({new}); los valores son "
                    f"de la edición {old} hasta revisarlos.",
                    f"There is a newer edition of the price list ({new}); the values are from "
                    f"edition {old} until reviewed.",
                    f"Há uma edição mais nova da tabela de preços ({new}); os valores são da "
                    f"edição {old} até serem revisados.",
                )
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
        why.append(
            tr(
                "El precio no tiene un ancla que el vigilante pueda buscar.",
                "The price has no anchor the watcher can look for.",
                "O preço não tem uma âncora que o vigilante possa buscar.",
            )
        )
    unanchored = included - {a.service_id for a, _c in pairs if a.fact == "component"}
    if unanchored:
        n = len(unanchored)
        why.append(
            tr(
                "Falta el ancla de 1 componente incluido.",
                "1 included component has no anchor.",
                "Falta a âncora de 1 componente incluído.",
            )
            if n == 1
            else tr(
                f"Faltan las anclas de {n} componentes incluidos.",
                f"{n} included components have no anchor.",
                f"Faltam as âncoras de {n} componentes incluídos.",
            )
        )
    for a, c in decisive:
        if c is None:
            continue
        if c.status == "embedded_only":
            why.append(
                fill(
                    tr(
                        "{what} solo aparece en datos embebidos de la página, no en su texto "
                        "visible.",
                        "{what} only appears in the page's embedded data, not in its visible text.",
                        "{what} só aparece em dados incorporados da página, não no texto visível.",
                    ),
                    what=_what(a, names),
                )
            )
        elif c.unconfirmed == "embedded_only":
            why.append(
                fill(
                    tr(
                        "{what} solo aparecía en datos embebidos de la página la última vez que "
                        "se pudo leer.",
                        "{what} only appeared in the page's embedded data the last time it could "
                        "be read.",
                        "{what} só aparecia em dados incorporados da página da última vez que se "
                        "pôde ler.",
                    ),
                    what=_what(a, names),
                )
            )
    observed_ok = not price_missing and (
        price_observed_on is None or (today - price_observed_on).days <= VERIFY_MAX_AGE_DAYS
    )
    if bundle.price.source == "observed" and not price_missing and not observed_ok:
        why.append(
            tr(
                "El precio observado no es de los datos diarios recientes.",
                "The observed price is not from recent daily data.",
                "O preço observado não é dos dados diários recentes.",
            )
        )
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
        reasons.append(
            tr(
                "El vigilante aún no ha confirmado todas sus anclas.",
                "The watcher has not confirmed all its anchors yet.",
                "O vigilante ainda não confirmou todas as suas âncoras.",
            )
        )
    return _WatchView(
        reasons=tuple(dict.fromkeys(reasons)),
        checked_at=w.checked_at,
        intro_confirmed=intro,
        confirmed_components=components,
    )


# ------------------------------------------------------------------ pending changes

_PENDING_SOURCES = {
    "watcher": tr("el vigilante", "the watcher", "o vigilante"),
    "agent": tr("la investigación automática", "automated research", "a pesquisa automática"),
}
# English and Portuguese names of the fields a pending change can touch (the card's
# "pendingFields"); Spanish comes from the field spec.
_PENDING_FIELDS_EN_PT = {
    "bundle_price": ("price", "preço"),
    "bundle_intro_price": ("introductory price", "preço promocional"),
    "bundle_base_price": ("base plan price", "preço do plano base"),
    "bundle_intro_months": ("months at the introductory price", "meses do preço promocional"),
    "bundle_contract_months": ("minimum term", "fidelidade"),
    "bundle_intro_until": ("end of the introductory price", "fim do preço promocional"),
    "bundle_offer_until": ("end of the offer", "fim da oferta"),
    "bundle_includes": ("included service", "serviço incluído"),
    "bundle_discount": ("discount", "desconto"),
    "bundle_available": ("still on sale", "continua à venda"),
    "bundle_price_list_url": ("price list", "tabela de preços"),
}


def _pending_label(change: Proposal) -> Text:
    es = change.label_es[:1].lower() + change.label_es[1:]
    en, pt = _PENDING_FIELDS_EN_PT.get(change.field, (change.field, change.field))
    return tr(es, en, pt)


def _pending_reasons(changes: Sequence[Proposal]) -> list[str]:
    """One reason naming the fields with a pending change and who proposed them (the card
    lists the values under "Cambios pendientes de revisión")."""
    if not changes:
        return []
    labels = list(dict.fromkeys(_pending_label(c) for c in changes))
    sources = list(dict.fromkeys(_PENDING_SOURCES[c.source] for c in changes))
    return [
        fill(
            tr(
                "Cambios pendientes de revisión: {fields} (según {sources}, en la página oficial).",
                "Changes pending review: {fields} (according to {sources}, on the official page).",
                "Mudanças pendentes de revisão: {fields} (segundo {sources}, na página oficial).",
            ),
            fields=join_list(labels),
            sources=join_list(sources),
        )
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
            reasons.append(
                tr(
                    "La oferta pasó su fecha de fin.",
                    "The offer is past its end date.",
                    "A oferta passou da data de término.",
                )
            )
        if bundle.status == "expired":
            reasons.append(
                tr(
                    "Marcada como vencida en los datos curados.",
                    "Marked as expired in the curated data.",
                    "Marcada como vencida nos dados curados.",
                )
            )
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
            tr(
                "Aún no hay un precio publicado de este plan en los datos diarios.",
                "There is no published price for this plan in the daily data yet.",
                "Ainda não há um preço publicado deste plano nos dados diários.",
            )
            if bundle.price.source == "observed"
            else tr(
                "El precio no está registrado.",
                "The price is not recorded.",
                "O preço não está registrado.",
            )
        )
    if bundle.verified_by == "user_report" and report is not None:
        age = (today - report.date).days
        if age <= MANUAL_MAX_AGE_DAYS:
            return BundleState(
                display_status="manual",
                # The badge shows its age, counted to the same day (meta.generated_at).
                reasons_es=(
                    tr(
                        "Verificación manual: no cuenta como fuente oficial.",
                        "Manual check: it does not count as an official source.",
                        "Verificação manual: não conta como fonte oficial.",
                    ),
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
            reasons.append(
                tr(
                    "Verificación manual sin fecha; no cuenta.",
                    "Manual check without a date; it does not count.",
                    "Verificação manual sem data; não conta.",
                )
            )
        else:
            reasons.append(
                fill(
                    tr(
                        "La verificación manual del {day} tiene más de {n} días.",
                        "The manual check of {day} is more than {n} days old.",
                        "A verificação manual de {day} tem mais de {n} dias.",
                    ),
                    day=long_date(report.date),
                    n=str(MANUAL_MAX_AGE_DAYS),
                )
            )
    else:
        what = _COMPONENTS if bundle.price.source == "observed" else _PRICE_AND_COMPONENTS
        texts = (
            _SOURCE_REASONS
            if view.checked_at is None
            else _SOURCE_REASONS | _WATCHED_SOURCE_REASONS
        )
        reasons.append(fill(texts[bundle.verified_by], what=what))
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
