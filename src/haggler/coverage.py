"""Which profiles get savings: `haggler coverage` (Markdown report).

For every residence with prices, the comparator is run (compare.cheapest + the same evaluator
the site uses) for a visitor with no ties, and for one tie with each other country with prices:
ID, card, phone and real address there, without trips and with occasional trips. Each
service counts with its default plan. Savings are the share of the home cost saved at risk
levels 2 and 3 (0 and 1 need EU rights or official offers, which ties do not change).

A tie with a country whose prices we do not track (Bolivia, say) never unlocks an offer: the
report lists the countries that can.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from haggler.catalog import (
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_review,
)
from haggler.compare import cheapest
from haggler.models.profiles import Profile
from haggler.research.evaluate import EvalContext, verdict_fn

LEVELS = (2, 3)


@dataclass(frozen=True)
class Result:
    tie: str | None
    trips: bool
    pct: dict[int, float]
    services: tuple[str, ...]


def _profile(residence: str, tie: str | None, trips: bool) -> Profile:
    own = [residence]
    if tie is None:
        return Profile(
            id="coverage",
            name_es="coverage",
            residence_country=residence,
            id_document_countries=own,
            phone_countries=own,
        )
    return Profile(
        id="coverage",
        name_es="coverage",
        residence_country=residence,
        id_document_countries=[residence, tie],
        phone_countries=[residence, tie],
        address_countries=[tie],
        card_countries=[tie],
        presence={tie: "once"} if trips else {},
    )


def coverage(data_dir: Path) -> tuple[list[str], dict[str, list[Result]]]:
    from haggler.publish import _load_latest
    from haggler.research.io import load_data_dir

    catalog = load_catalog()
    feasibility = load_feasibility()
    subjects, _, _ = load_data_dir(data_dir)
    ctx = EvalContext.build(
        catalog=catalog,
        feasibility=feasibility,
        instruments=load_payment_instruments(missing_ok=True),
        subjects=subjects,
        review=load_review(),
        today=datetime.now(UTC).date(),
    )
    latest = _load_latest(data_dir)
    if latest.fx is None:
        raise ValueError(f"{data_dir}/latest/fx.json no existe: ejecuta `haggler sync` antes")
    defaults = {(s.id, s.default_plan) for s in catalog.services}
    countries = sorted(catalog.country_codes())
    offers = [
        p
        for p in latest.prices
        if (p.service_id, p.plan_id) in defaults
        and p.country in countries
        and p.status != "rejected"
    ]

    def run(residence: str, verdicts: Callable) -> tuple[dict[int, float], tuple[str, ...]]:
        rows = cheapest(
            offers, fx=latest.fx, catalog=catalog, feasibility=feasibility,
            residence=residence, verdicts=verdicts,
        )  # fmt: skip
        home = sum((r.home.cost_eur_with_fee for r in rows if r.home), Decimal(0))
        pct = {}
        for level in LEVELS:
            saved = sum((r.savings_month_eur.get(level) or Decimal(0) for r in rows), Decimal(0))
            pct[level] = float(saved / home * 100) if home else 0.0
        abroad = tuple(
            sorted(
                r.service_id
                for r in rows
                if (best := r.best_by_risk.get(3)) is not None and best.country != residence
            )
        )
        return pct, abroad

    report: dict[str, list[Result]] = {}
    for residence in countries:
        results = []
        for tie in (None, *(cc for cc in countries if cc != residence)):
            for trips in (False, True) if tie else (False,):
                profile = _profile(residence, tie, trips)
                pct, services = run(residence, verdict_fn(profile, ctx))
                results.append(Result(tie, trips, pct, services))
        report[residence] = results
    return countries, report


def render(countries: list[str], report: dict[str, list[Result]], names: dict[str, str]) -> str:
    def pct(value: float) -> str:
        return f"{value:.0f} %"

    lines = [
        "# Cobertura del comparador",
        "",
        "Ahorro sobre lo que se paga en casa con el plan por defecto de cada servicio, para un",
        "vínculo con documento, tarjeta, teléfono y dirección en el país (sin viajes / con viajes",
        "de vez en cuando). Niveles 2 y 3; los niveles 0 y 1 no dependen de los vínculos.",
        "",
        "Países con precios (solo un vínculo con uno de ellos abre ofertas): "
        + ", ".join(names.get(cc, cc) for cc in countries)
        + ".",
        "",
        "| Vives en | Sin vínculos (2 / 3) | Vínculos que ahorran "
        "| Mejores vínculos (nivel 3, con viajes) |",
        "| --- | --- | --- | --- |",
    ]
    for residence, results in report.items():
        alone = results[0]
        ties = [r for r in results[1:] if r.trips]
        useful = [r for r in ties if r.pct[3] > 0]
        best = sorted(useful, key=lambda r: -r.pct[3])[:3]
        top = ", ".join(f"{names.get(r.tie or '', r.tie)} {pct(r.pct[3])}" for r in best)
        lines.append(
            f"| {names.get(residence, residence)} "
            f"| {pct(alone.pct[2])} / {pct(alone.pct[3])} "
            f"| {len(useful)} de {len(ties)} "
            f"| {top or '—'} |"
        )
    return "\n".join(lines) + "\n"


def write_report(data_dir: Path, out: Path) -> Path:
    countries, report = coverage(data_dir)
    names = {c.cc: c.name_es for c in load_catalog().countries}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(countries, report, names), encoding="utf-8")
    return out
