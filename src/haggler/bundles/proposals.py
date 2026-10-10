"""Proposed changes to a bundle's values: what the watcher and the agent found on official pages
that bundles.yaml does not say yet. CONTRACT.

Pure (no I/O). bundles.yaml is the only source of a bundle's values and no job writes it: a
proposal is accepted by EDITING the YAML (snippet(): the value at its YAML path plus an anchor
with the literal quote, ready to paste), and it then resolves itself: a proposal whose value the
YAML already holds is never listed. A rejection in data/curated/research_review.yaml (by id AND
hash, `haggler research reject`) hides it, and any other proposal of the same value with the same
hash (the watcher and the agent on the same line); new evidence (another quote, line or page) is
a new hash, so it comes back.

Sources, listed in this order:
* watcher: the PriceHints of the bundle's watch state (bundle_watch.json), only when that state
  counts (haggler.bundles.status.watched: watch.mode auto, state not manual/off). Role regular ->
  bundle_price, intro -> bundle_intro_price. id "watch:<bundle_id>:<field>:<value>", hash
  research.models claim_hash(field, value, None, line, url). A hint is only made when the value
  check passed on a page read on the provider's domains for the bundle's country: official, "ok".
* agent: the claims of the bundle subject (bundle:<bundle_id>, data/research/bundles.json) of a
  VALUE field (research.fields yaml_value), pending or accepted (a value is never accepted by a
  decision, but a hand-written one does not hide it). id and hash are the claim's. Only verified
  claims are persisted, so a field with a value check passed it ("ok"); bundle_available has no
  value check ("unchecked"). big_change is measured again against today's YAML for the money
  fields (research.values.big_change).

Resolution (resolved(): the YAML already holds the value):
* money: the same amount, currency and period (the YAML's own period when the value has none);
  a bundle_price equal to the YAML's base plan or marginal amount is not a new price either (the
  watcher skips those amounts too). An observed price (price.source observed) comes from the
  daily data, so it gets no bundle_price proposal;
* bundle_includes: an included component of that service (and plan, when the value names one);
* bundle_discount: a discount_pct component of that service with that percentage;
* bundle_price_list_url: one of the watch pages;
* bundle_available: "false" when the bundle is expired (status expired or past valid_until),
  else "true";
* every other field: the YAML value (haggler.bundles.research.yaml_values) is the proposed one.

Snippets (snippet()): the value at its YAML path (price.amount, price.intro_amount, valid_until,
components[], watch.pages[]...), with the marginal amount recomputed when the YAML keeps one, and
the literal quote as an anchor of the field's fact (a field no anchor fact confirms, such as the
contract months, gets an official evidence entry with the quote instead). When the quote alone
names no tier (the tier is a heading above it, as on a plans page that shows several tiers),
the anchor is the excerpt from that heading to the quote kept with the hint or the claim
(anchor_text, research.evidence.anchor_span), one line each: validate-catalog checks an
anchor's text alone and the watcher finds it on the page as one piece. A page that is another
edition of a versioned price list (a watch page with index_url and link_pattern whose pattern
matches it) replaces that page's entry, which keeps its index_url and link_pattern, instead of
being added as a new, unversioned page. edition_snippets(): for a versioned PDF the watcher found
superseded, the entry with the newest edition's url and the anchors that edition still contains
(PageCheck.newest_anchors) moved to it, with notes on the ones it does not contain. Notes say
what to check by hand: the page missing from watch.pages, an anchor validate-catalog would
refuse (its length, or the value check on its text alone, offline_check), an existing anchor of
the same fact that states the old value and must go (replaced_anchors: validate-catalog would
refuse it once the value changes), a big change, a manual watch.

`current` is the YAML value the proposal would replace, in the same vocabulary form (for a
multi-valued field, the entry of the same service), None when the YAML has none.

Published (bundles.json pending_changes; haggler.bundles.status needs_review): the PUBLISHABLE
proposals only (official, value check "ok"), one per (field, proposed value), the first source in
the order above. The others (no value check) are listed by the review CLI only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from haggler.bundles.research import yaml_values
from haggler.bundles.status import is_expired, watched
from haggler.dates import find_dates
from haggler.models.bundles import BundleWatchState, PriceHint, anchor_hash
from haggler.models.catalog import Catalog
from haggler.models.curated import Anchor, Bundle, BundlesFile, WatchPage
from haggler.models.research import Claim, SubjectFacts, claim_hash, claim_id
from haggler.money import format_money_code, parse_money_code, plain_amount
from haggler.research.evidence import ValueContext, canonical_url
from haggler.research.fields import FIELD_SPECS, FIELDS
from haggler.research.review import ClaimReview, ReviewIndex
from haggler.research.values import ValueCheck, big_change

ProposalSource = Literal["watcher", "agent"]
SOURCE_ES = {"watcher": "vigilante", "agent": "investigación automática"}
YAML_FILE = "data/curated/bundles.yaml"
# The anchor fact (models.curated AnchorFact) that confirms each value field, when one does; a
# field without one gets an `evidence` entry with the quote instead.
ANCHOR_FACT: dict[str, str] = {
    "bundle_price": "price",
    "bundle_intro_price": "intro",
    "bundle_base_price": "base_price",
    "bundle_intro_until": "validity",
    "bundle_offer_until": "validity",
    "bundle_includes": "component",
    "bundle_discount": "component",
}
_HINT_FIELDS = {"regular": "bundle_price", "intro": "bundle_intro_price"}
_MONEY_ATTR = {
    "bundle_price": "amount",
    "bundle_intro_price": "intro_amount",
    "bundle_base_price": "base_plan_amount",
}
_YAML_PERIODS = ("month", "year")
# models.curated / catalog anchor rules, repeated in the snippet's notes.
_ANCHOR_MIN, _ANCHOR_MAX = 20, 400
_FIELD_ORDER = {f.name: i for i, f in enumerate(FIELDS)}


@dataclass(frozen=True)
class Proposal:
    id: str
    hash: str
    bundle_id: str
    source: ProposalSource
    field: str
    # The YAML value it would replace (vocabulary form), None when the YAML has none.
    current: str | None
    proposed: str
    url: str
    # The literal excerpt: the claim's quote, or the watcher's line.
    quote: str
    # "ok" (the value check passed), "unchecked" (the field has none) or a check status.
    value_check: str
    official: bool
    big_change: bool = False
    first_seen: date | None = None
    retrieved_at: date | None = None
    # Agent claims: their review state ("pending" or "accepted"; "rejected" only when asked
    # for, see bundle_proposals).
    review: ClaimReview = "pending"
    reason: str = ""
    # The anchor text validate-catalog accepts when `quote` alone names no tier (the excerpt
    # from the heading above it, research.evidence.anchor_span); the snippet offers it.
    anchor_text: str | None = None

    @property
    def publishable(self) -> bool:
        """Official and value-checked: shown on the site and makes the bundle needs_review."""
        return self.official and self.value_check == "ok"

    @property
    def label_es(self) -> str:
        spec = FIELD_SPECS.get(self.field)
        return spec.label_es if spec else self.field


# ------------------------------------------------------------------ YAML values


def _service_entry(bundle: Bundle, field_name: str, service_id: str) -> str | None:
    """A multi-valued field's YAML entry for `service_id` (bundle_includes / bundle_discount)."""
    for c in bundle.components:
        if c.service_id != service_id:
            continue
        if field_name == "bundle_includes" and c.inclusion == "included":
            return c.service_id + (f".{c.plan_id}" if c.plan_id else "")
        if field_name == "bundle_discount" and c.inclusion == "discount_pct" and c.value:
            return f"{c.service_id}:{format(c.value.normalize(), 'f')}"
    return None


def current_value(bundle: Bundle, field_name: str, proposed: str, today: date) -> str | None:
    """The YAML value a proposal of `field_name` would replace (module docstring)."""
    if field_name in ("bundle_includes", "bundle_discount"):
        sid = proposed.replace(":", ".").split(".", 1)[0]
        return _service_entry(bundle, field_name, sid)
    if field_name == "bundle_available":
        return "false" if is_expired(bundle, today) else "true"
    if field_name == "bundle_price_list_url":
        urls = [str(p.url) for p in bundle.watch.pages]
        target = canonical_url(proposed)
        same = next((u for u in urls if canonical_url(u) == target), None)
        return same or yaml_values(bundle).get(field_name)
    value = yaml_values(bundle).get(field_name)
    return value if isinstance(value, str) else None


def _same_money(proposed: str, amount: Decimal | None, bundle: Bundle) -> bool:
    parsed = parse_money_code(proposed)
    if parsed is None or amount is None:
        return False
    value, currency, period = parsed
    price = bundle.price
    return (
        value == amount and currency == price.currency and (period or price.period) == price.period
    )


def resolved(bundle: Bundle, field_name: str, proposed: str, today: date) -> bool:
    """The YAML already holds `proposed` (module docstring, "Resolution")."""
    price = bundle.price
    if field_name in _MONEY_ATTR:
        if _same_money(proposed, getattr(price, _MONEY_ATTR[field_name]), bundle):
            return True
        if field_name == "bundle_price":
            others = (price.base_plan_amount, price.marginal_amount)
            return any(_same_money(proposed, x, bundle) for x in others)
        return False
    if field_name == "bundle_includes":
        sid, _dot, plan = proposed.partition(".")
        return any(
            c.inclusion == "included" and c.service_id == sid and (not plan or c.plan_id == plan)
            for c in bundle.components
        )
    if field_name == "bundle_discount":
        sid, _colon, pct = proposed.partition(":")
        try:
            value = Decimal(pct)
        except InvalidOperation:
            return False  # not a stored form (research.fields normalizes "hbo_max:30")
        return any(
            c.inclusion == "discount_pct" and c.service_id == sid and c.value == value
            for c in bundle.components
        )
    if field_name == "bundle_price_list_url":
        return canonical_url(proposed) in {canonical_url(str(p.url)) for p in bundle.watch.pages}
    return current_value(bundle, field_name, proposed, today) == proposed


def _big_change(bundle: Bundle, field_name: str, proposed: str, stored: bool) -> bool:
    """big_change against today's YAML for a money field; the stored flag otherwise."""
    attr = _MONEY_ATTR.get(field_name)
    parsed = parse_money_code(proposed)
    if attr is None or parsed is None:
        return stored
    current = getattr(bundle.price, attr)
    if current is None:
        return False
    amount, currency, period = parsed
    return big_change(amount, period, current, currency, bundle.price.period)


# ------------------------------------------------------------------ proposals


def _hint_proposal(
    bundle: Bundle, hint: PriceHint, watch: BundleWatchState, today: date
) -> Proposal:
    field_name = _HINT_FIELDS[hint.role]
    value = format_money_code(hint.amount, hint.currency, hint.period or bundle.price.period)
    return Proposal(
        id=claim_id(f"watch:{bundle.bundle_id}", field_name, value),
        hash=claim_hash(field_name, value, None, hint.line, hint.url),
        bundle_id=bundle.bundle_id,
        source="watcher",
        field=field_name,
        current=current_value(bundle, field_name, value, today),
        proposed=value,
        url=hint.url,
        quote=hint.line,
        value_check="ok",
        official=True,
        big_change=_big_change(bundle, field_name, value, hint.big_change),
        first_seen=hint.first_seen or watch.checked_at,
        retrieved_at=watch.checked_at,
        reason=hint.reason,
        anchor_text=hint.anchor_text,
    )


def _claim_proposal(bundle: Bundle, claim: Claim, review: ClaimReview, today: date) -> Proposal:
    spec = FIELD_SPECS[claim.field]
    if spec.value_check is None:
        check = "unchecked"
    else:
        check = "ok" if claim.check.status == "verified" else claim.check.status
    retrieved = claim.check.retrieved_at
    return Proposal(
        id=claim.id,
        hash=claim.hash,
        bundle_id=bundle.bundle_id,
        source="agent",
        field=claim.field,
        current=current_value(bundle, claim.field, claim.value, today),
        proposed=claim.value,
        url=claim.url,
        quote=claim.quote,
        value_check=check,
        official=claim.official,
        big_change=_big_change(bundle, claim.field, claim.value, claim.big_change),
        first_seen=claim.first_seen,
        retrieved_at=retrieved.date() if retrieved else None,
        review=review,
        reason=claim.check.detail,
        anchor_text=claim.anchor_text,
    )


def bundle_proposals(
    bundle: Bundle,
    *,
    watch: BundleWatchState | None,
    facts: SubjectFacts | None,
    review: ReviewIndex | None,
    today: date,
    include_rejected: bool = False,
) -> list[Proposal]:
    """Every open proposal for `bundle` (module docstring): watcher hints, then the bundle
    subject's value claims in vocabulary order; resolved ones left out, and rejected ones
    unless `include_rejected` (the review CLI, to say a rejection is already there)."""
    index = review or ReviewIndex()
    out: list[Proposal] = []
    w = watched(bundle, watch)
    observed = bundle.price.source == "observed"
    for hint in w.hints if w is not None else []:
        if observed and hint.role == "regular":
            continue
        out.append(_hint_proposal(bundle, hint, w, today))  # type: ignore[arg-type]
    claims = [c for c in (facts.claims if facts else []) if _is_value(c.field)]
    claims.sort(key=lambda c: (_FIELD_ORDER.get(c.field, len(_FIELD_ORDER)), c.value))
    for claim in claims:
        if observed and claim.field == "bundle_price":
            continue
        status = index.status(claim)
        out.append(_claim_proposal(bundle, claim, status, today))
    # A rejection hides the proposal, and every other one of the same value on the same
    # evidence (the same hash: the watcher and the agent read the same line).
    rejected = {
        (p.field, p.proposed, p.hash)
        for p in out
        if (d := index.lookup(p.id, p.hash)) is not None and d.decision == "reject"
    }
    kept: list[Proposal] = []
    seen: set[tuple[str, str]] = set()
    for p in out:
        if (p.id, p.hash) in seen or resolved(bundle, p.field, p.proposed, today):
            continue
        if not include_rejected and (p.field, p.proposed, p.hash) in rejected:
            continue
        seen.add((p.id, p.hash))
        kept.append(p)
    return kept


def _is_value(field_name: str) -> bool:
    spec = FIELD_SPECS.get(field_name)
    return spec is not None and spec.yaml_value


def pending_changes(proposals: Iterable[Proposal]) -> list[Proposal]:
    """The publishable proposals, one per (field, proposed value), first source first."""
    out: list[Proposal] = []
    seen: set[tuple[str, str]] = set()
    for p in proposals:
        if not p.publishable or (p.field, p.proposed) in seen:
            continue
        seen.add((p.field, p.proposed))
        out.append(p)
    return out


# ------------------------------------------------------------------ snippets


class _SnippetDumper(yaml.SafeDumper):
    """safe_dump, with a multi-line anchor text as a literal block (its line breaks visible)."""


def _str_block(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None
    )


_SnippetDumper.add_representer(str, _str_block)


@dataclass
class Snippet:
    """What to paste into bundles.yaml to accept a proposal: `path` names the value's place,
    `fragment` is the YAML to merge into the bundle's row, `notes` what to check by hand."""

    bundle_id: str
    path: str
    fragment: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        dumped = yaml.dump(
            self.fragment,
            Dumper=_SnippetDumper,
            sort_keys=False,
            allow_unicode=True,
            width=10_000,
            indent=2,
        )
        head = [
            f"# {YAML_FILE}, paquete {self.bundle_id}: {self.path}",
            *(f"# nota: {n}" for n in self.notes),
        ]
        return "\n".join([*head, dumped.rstrip("\n")])


def _page_kind(url: str) -> str:
    return "pdf" if urlsplit(url).path.lower().endswith(".pdf") else "html"


def _value_fragment(bundle: Bundle, p: Proposal, notes: list[str]) -> tuple[str, dict[str, Any]]:
    """The proposed value at its YAML path."""
    price = bundle.price
    value = p.proposed
    if p.field in _MONEY_ATTR:
        attr = _MONEY_ATTR[p.field]
        parsed = parse_money_code(value)
        if parsed is None:  # research.fields stores money in this form
            raise ValueError(f"{p.id}: {value!r} is not an amount like '21900 COP/month'")
        amount, _currency, period = parsed
        frag: dict[str, Any] = {attr: plain_amount(amount)}
        if period and period != price.period:
            if period in _YAML_PERIODS and p.field == "bundle_price":
                frag["period"] = period
            else:
                notes.append(
                    f"el valor es por {period} y el paquete por {price.period}: conviértelo o "
                    "cambia price.period"
                )
        regular = amount if p.field == "bundle_price" else price.amount
        base = amount if p.field == "bundle_base_price" else price.base_plan_amount
        if price.marginal_amount is not None and regular is not None and base is not None:
            frag["marginal_amount"] = plain_amount(regular - base)
        if p.field == "bundle_base_price" and not price.base_plan_name:
            notes.append("añade price.base_plan_name: el nombre del plan base tal como lo imprime")
        return f"price.{attr}", {"price": frag}
    if p.field in ("bundle_intro_months", "bundle_contract_months"):
        attr = "intro_months" if p.field == "bundle_intro_months" else "contract_months"
        return f"price.{attr}", {"price": {attr: int(value)}}
    if p.field == "bundle_intro_until":
        until = date.fromisoformat(value)
        return "price.intro_valid_until", {"price": {"intro_valid_until": until}}
    if p.field == "bundle_offer_until":
        return "valid_until", {"valid_until": date.fromisoformat(value)}
    if p.field in ("bundle_includes", "bundle_discount"):
        if p.field == "bundle_includes":
            sid, _dot, plan = value.partition(".")
            comp: dict[str, Any] = {"service_id": sid}
            if plan:
                comp["plan_id"] = plan
            comp["inclusion"] = "included"
        else:
            sid, _colon, pct = value.partition(":")
            comp = {"service_id": sid, "inclusion": "discount_pct", "value": pct}
        others = [c for c in bundle.components if c.service_id == sid]
        for c in others:
            what = c.inclusion + (f" {format(c.value.normalize(), 'f')}" if c.value else "")
            notes.append(f"reemplaza la entrada actual de {sid} en components ({what})")
        return "components[]", {"components": [comp]}
    if p.field == "bundle_available":
        if value == "false":
            return "status", {"status": "expired"}
        notes.append("la página la sigue vendiendo: revisa también valid_until")
        return "status", {"status": "unverified"}
    if p.field == "bundle_price_list_url":
        versioned = edition_of(bundle, value)
        if versioned is not None:
            notes.append(_replace_page_note(versioned))
            return "watch.pages[]", {"watch": {"pages": [_edition_entry(versioned, value)]}}
        notes.append(
            "si es una edición nueva de una lista versionada, cambia la url de esa página "
            "(index_url y link_pattern siguen igual) y vuelve a copiar sus anclas"
        )
        return "watch.pages[]", {"watch": {"pages": [{"url": value, "kind": "pdf"}]}}
    raise ValueError(f"{p.field} is not a bundle value field")


def offline_check(
    bundle: Bundle,
    p: Proposal,
    *,
    bundles: BundlesFile | None = None,
    catalog: Catalog | None = None,
) -> ValueCheck | None:
    """The proposed value checked on the quote ALONE, as validate-catalog checks an anchor's
    text (research.evidence.ValueContext.offline: no page context, its line breaks kept; a
    price anchor must name the bundle's tier itself, where the agent's quote may have taken it
    from a heading above). None for a field without an anchor fact."""
    ctx = ValueContext(
        bundle=bundle,
        provider=bundles.provider(bundle.provider_id) if bundles else None,
        bundles=tuple(bundles.bundles) if bundles else (),
        catalog=catalog,
    )
    return ctx.offline(p.field, p.proposed, p.quote)


def replaced_anchors(
    bundle: Bundle,
    p: Proposal,
    *,
    bundles: BundlesFile | None = None,
    catalog: Catalog | None = None,
) -> list[Anchor]:
    """The bundle's anchors of `p`'s fact (a component's: of the same service) that would not
    confirm the proposed value once it is in the YAML: their text checked against the new
    value as validate-catalog checks it (offline_check). A validity anchor: one that states
    the date being replaced and none of the bundle's other dates, nor the new one."""
    fact = ANCHOR_FACT.get(p.field)
    if fact is None:
        return []
    anchors = [a for a in bundle.watch.anchors if a.fact == fact]
    if fact == "validity":
        price = bundle.price
        if p.field == "bundle_intro_until":
            old, other = price.intro_valid_until, bundle.valid_until
        else:
            old, other = bundle.valid_until, price.intro_valid_until
        if old is None:
            return []
        still = {d for d in (bundle.valid_from, other) if d is not None}
        still.add(date.fromisoformat(p.proposed))
        out = []
        for a in anchors:
            stated = {d.value for d in find_dates(" ".join(a.text.split()))}
            if old in stated and not stated & still:
                out.append(a)
        return out
    if fact == "component":
        sid = p.proposed.replace(":", ".").split(".", 1)[0]
        anchors = [a for a in anchors if a.service_id == sid]
    out = []
    for a in anchors:
        check = offline_check(bundle, replace(p, quote=a.text), bundles=bundles, catalog=catalog)
        if check is not None and not check.ok:
            out.append(a)
    return out


def edition_of(bundle: Bundle, url: str) -> WatchPage | None:
    """The versioned watch page (index_url and link_pattern) `url` is another edition of: its
    link_pattern matches `url`, which is not the page's own url."""
    target = canonical_url(url)
    for wp in bundle.watch.pages:
        if wp.index_url is None or wp.link_pattern is None or canonical_url(str(wp.url)) == target:
            continue
        try:
            if re.search(wp.link_pattern, url):
                return wp
        except re.error:  # validate-catalog refuses it; nothing to match here
            continue
    return None


def _edition_entry(wp: WatchPage, url: str) -> dict[str, Any]:
    return {
        "url": url,
        "kind": wp.kind,
        "index_url": str(wp.index_url),
        "link_pattern": wp.link_pattern,
    }


def _replace_page_note(wp: WatchPage) -> str:
    return (
        f"es otra edición de la lista versionada {wp.url}: reemplaza esa entrada de "
        "watch.pages por esta (index_url y link_pattern siguen igual) y cambia la url de sus "
        "anclas que sigan en la edición nueva"
    )


def _anchor_entry(a: Anchor, url: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"fact": a.fact}
    if a.service_id:
        entry["service_id"] = a.service_id
    return entry | {"url": url, "text": a.text}


def edition_snippets(bundle: Bundle, watch: BundleWatchState | None) -> list[Snippet]:
    """For each versioned PDF the watcher found superseded (its PageCheck superseded, with the
    newest edition's URL; only while the watch state counts, status.watched): the watch.pages
    entry with the newest edition's url (index_url and link_pattern unchanged) that replaces the
    curated one, and the curated edition's anchors the newest one still contains
    (PageCheck.newest_anchors) at the new url, replacing theirs. Notes name the anchors it does
    not contain, or say that it could not be read."""
    w = watched(bundle, watch)
    if w is None:
        return []
    out: list[Snippet] = []
    for wp in bundle.watch.pages:
        check = w.page(str(wp.url))
        if (
            wp.index_url is None
            or wp.link_pattern is None
            or check is None
            or check.status != "superseded"
            or not check.newest_url
        ):
            continue
        new, edition = check.newest_url, check.newest_edition
        notes = [
            f"reemplaza la entrada de watch.pages con url {wp.url}: la edición {edition} "
            f"sustituye a la {check.edition} (index_url y link_pattern siguen igual)"
        ]
        moved: list[dict[str, Any]] = []
        missing: list[str] = []
        for a in bundle.watch.anchors:
            if canonical_url(str(a.url)) != canonical_url(str(wp.url)):
                continue
            if check.newest_status == "ok" and anchor_hash(a) in check.newest_anchors:
                moved.append(_anchor_entry(a, new))
            else:
                missing.append(a.fact + (f" ({a.service_id})" if a.service_id else ""))
        if moved:
            notes.append(
                "reemplaza estas anclas de la edición anterior por las de abajo (mismo texto, "
                f"url nueva): la edición {edition} las contiene"
            )
        if missing and check.newest_status == "ok":
            notes.append(
                f"la edición {edition} no contiene estas anclas de la anterior: "
                f"{', '.join(missing)}; quítalas o cópialas de nuevo de la salida de "
                "`haggler probe --mode bundles`"
            )
        elif missing:
            notes.append(
                f"la edición {edition} no se pudo leer ({check.newest_status or 'sin leer'}): "
                f"comprueba a mano si contiene estas anclas: {', '.join(missing)}"
            )
        frag: dict[str, Any] = {"watch": {"pages": [_edition_entry(wp, new)]}}
        if moved:
            frag["watch"]["anchors"] = moved
        out.append(Snippet(bundle.bundle_id, "watch.pages[]", frag, notes))
    return out


def _excerpt(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def snippet(
    bundle: Bundle,
    p: Proposal,
    *,
    bundles: BundlesFile | None = None,
    catalog: Catalog | None = None,
) -> Snippet:
    """The YAML fragment that accepts `p` (module docstring): the value at its path, and the
    quote as an anchor (watch.anchors, with the page in watch.pages when it is not there yet)
    or, for a field no anchor fact confirms, as an official evidence entry. A note says what
    validate-catalog would refuse in the anchor (offline_check, its length). `bundles` and
    `catalog` default to data/curated/bundles.yaml and the service catalog."""
    notes: list[str] = []
    path, frag = _value_fragment(bundle, p, notes)
    quote = " ".join(p.quote.split())
    fact = ANCHOR_FACT.get(p.field)
    if fact is not None:
        # The excerpt from the tier's heading on, when the quote alone names no tier.
        text = p.anchor_text or quote
        anchor: dict[str, Any] = {"fact": fact}
        if fact == "component":
            anchor["service_id"] = p.proposed.replace(":", ".").split(".", 1)[0]
        anchor |= {"url": p.url, "text": text}
        watch = frag.setdefault("watch", {})
        pages = {canonical_url(str(w.url)) for w in bundle.watch.pages}
        pages |= {canonical_url(str(w.index_url)) for w in bundle.watch.pages if w.index_url}
        if canonical_url(p.url) not in pages:
            versioned = edition_of(bundle, p.url)
            if versioned is not None:
                # Another edition of a versioned price list: its entry changes url, it stays
                # versioned (a new page would never be checked for a newer edition).
                watch.setdefault("pages", []).append(_edition_entry(versioned, p.url))
                notes.append(_replace_page_note(versioned))
            else:
                watch.setdefault("pages", []).append({"url": p.url, "kind": _page_kind(p.url)})
                notes.append("la página aún no está en watch.pages: añádela (un ancla cita una)")
        watch["anchors"] = [anchor]
        length = len(" ".join(text.split()))
        if not _ANCHOR_MIN <= length <= _ANCHOR_MAX:
            notes.append(
                f"el texto del ancla tiene {length} caracteres ({_ANCHOR_MIN}-{_ANCHOR_MAX}): "
                "cópialo más completo de la salida de `haggler probe --mode bundles`"
            )
        check = offline_check(bundle, replace(p, quote=text), bundles=bundles, catalog=catalog)
        if check is not None and not check.ok:
            notes.append(
                f"validate-catalog no aceptará este texto tal cual ({check.reason}): cópialo de "
                "la salida de `haggler probe --mode bundles` con lo que le falte (p. ej. el "
                "nombre del nivel)"
            )
        for old in replaced_anchors(bundle, p, bundles=bundles, catalog=catalog):
            label = f"{old.fact} ({old.service_id})" if old.service_id else old.fact
            notes.append(
                f"quita el ancla {label} actual de watch.anchors, que dice el valor de antes "
                f'(validate-catalog la rechazará): "{_excerpt(old.text)}"'
            )
        if bundle.watch.mode != "auto":
            notes.append(
                f"watch.mode es {bundle.watch.mode}: el vigilante solo comprueba anclas en auto"
            )
    else:
        frag["evidence"] = [{"url": p.url, "kind": "official" if p.official else "press"}]
        if quote:
            frag["evidence"][0]["quote"] = quote
    if p.big_change:
        notes.append("cambio grande (más del 50 % sobre el valor actual): compruébalo en la página")
    return Snippet(bundle.bundle_id, path, frag, notes)
