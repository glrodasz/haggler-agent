/**
 * Comparador: the only interactive island. Controls render on the server; the offers list is
 * read from an inline <script type="application/json"> after hydration because passing ~1-2k
 * offers as island props would bloat the HTML with Astro's prop encoding.
 */

import { useEffect, useMemo, useState } from "preact/hooks";
import { es } from "../i18n/es";
import {
  cheapestFor,
  type CompareContext,
  type CompareOfferFields,
  type PricedOffer,
  savingsByRisk,
  summarize,
} from "../logic/compare";
import {
  flagEmoji,
  formatDate,
  formatMoney,
  formatPercent,
  formatPrice,
  formatSignedMoney,
  periodSuffix,
} from "../logic/format";
import { type CompareState, decodeState, defaultState, encodeState } from "../logic/selection";
import { unpackOffers } from "../logic/transport";
import { RISK_LEVELS, type RiskLevel } from "../logic/types";

export interface ComparadorService {
  id: string;
  name: string;
  default_plan: string;
  plans: { id: string; label_es: string }[];
  href: string;
}

export interface ComparadorLabel {
  id: string;
  rank: number;
  name: string;
  description: string;
}

export interface ComparadorProps {
  services: ComparadorService[];
  /** Every label by id, for badges. */
  labels: Record<string, ComparadorLabel>;
  /** Thresholds the visitor can accept, rank 0..3. */
  riskOptions: ComparadorLabel[];
  countryNames: Record<string, string>;
  rates: Record<string, number>;
  fxAsOf: string;
  residence: string;
  residenceName: string;
  residenceCurrency: string;
  feePct: number;
  currencies: string[];
  defaultCurrency: string;
  /** id of the inline JSON element holding the packed offers (see logic/transport.ts). */
  dataId: string;
}

const t = es.compare;

function riskClass(rank: number): string {
  return rank >= 0 && rank <= 4 ? `risk-${rank}` : "risk-unknown";
}

export default function Comparador(props: ComparadorProps) {
  const { services, rates, feePct } = props;
  const currencies = useMemo(
    () => props.currencies.filter((c) => c === "EUR" || (rates[c] ?? 0) > 0),
    [props.currencies, rates],
  );
  const initial = useMemo(
    () =>
      defaultState(
        services,
        currencies.includes(props.defaultCurrency)
          ? props.defaultCurrency
          : (currencies[0] ?? "EUR"),
      ),
    [services, currencies, props.defaultCurrency],
  );
  const [state, setState] = useState<CompareState>(initial);
  const [offers, setOffers] = useState<CompareOfferFields[] | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let parsed: CompareOfferFields[] = [];
    try {
      const text = document.getElementById(props.dataId)?.textContent ?? "{}";
      parsed = unpackOffers(JSON.parse(text));
    } catch {
      parsed = [];
    }
    setOffers(parsed);
    setState(decodeState(window.location.hash, services, currencies, initial));
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const hash = `#${encodeState(state)}`;
    if (window.location.hash !== hash) {
      history.replaceState(history.state, "", hash);
    }
  }, [state, ready]);

  const ctx: CompareContext = useMemo(
    () => ({
      rates,
      residence: props.residence,
      residenceCurrency: props.residenceCurrency,
      feePct,
    }),
    [rates, props.residence, props.residenceCurrency, feePct],
  );

  const rows = useMemo(
    () =>
      offers ? state.services.map((id) => cheapestFor(id, state.plans[id] ?? "", offers, ctx)) : [],
    [offers, state.services, state.plans, ctx],
  );
  const display = useMemo(
    () => ({ currency: state.currency, rates, feePct }),
    [state.currency, rates, feePct],
  );
  const summary = useMemo(() => summarize(rows, state.risk, display), [rows, state.risk, display]);
  const byRisk = useMemo(() => savingsByRisk(rows, display), [rows, display]);

  const serviceById = useMemo(() => new Map(services.map((s) => [s.id, s])), [services]);
  const cur = state.currency;
  const riskName = props.riskOptions.find((o) => o.rank === state.risk)?.name ?? `${state.risk}`;

  const toggleService = (id: string, on: boolean) =>
    setState((s) => {
      const picked = new Set(s.services);
      if (on) picked.add(id);
      else picked.delete(id);
      return { ...s, services: services.map((x) => x.id).filter((x) => picked.has(x)) };
    });
  const setPlan = (id: string, plan: string) =>
    setState((s) => ({ ...s, plans: { ...s.plans, [id]: plan } }));
  const setRisk = (risk: RiskLevel) => setState((s) => ({ ...s, risk }));
  const setCurrency = (currency: string) => setState((s) => ({ ...s, currency }));

  const planLabel = (serviceId: string, planId: string) =>
    serviceById.get(serviceId)?.plans.find((p) => p.id === planId)?.label_es ?? planId;
  const names = (ids: string[]) => ids.map((id) => serviceById.get(id)?.name ?? id).join(", ");

  const { totals } = summary;
  const liveText =
    offers && state.services.length > 0
      ? t.summaryLive(
          formatSignedMoney(totals.saving, cur),
          formatSignedMoney(totals.saving * 12, cur),
        )
      : "";

  return (
    <div class="comparador">
      <form class="controls" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>{t.servicesLegend}</legend>
          <p class="fieldset-help">{t.servicesHelp}</p>
          <ul class="service-list">
            {services.map((s) => {
              const checked = state.services.includes(s.id);
              return (
                <li class="service-row" key={s.id}>
                  <label class="check" for={`svc-${s.id}`}>
                    <input
                      type="checkbox"
                      id={`svc-${s.id}`}
                      checked={checked}
                      onChange={(e) => toggleService(s.id, e.currentTarget.checked)}
                    />
                    {s.name}
                  </label>
                  <label class="visually-hidden" for={`plan-${s.id}`}>
                    {t.planLabel(s.name)}
                  </label>
                  <select
                    id={`plan-${s.id}`}
                    value={state.plans[s.id] ?? s.default_plan}
                    disabled={!checked}
                    onChange={(e) => setPlan(s.id, e.currentTarget.value)}
                  >
                    {s.plans.map((p) => (
                      <option value={p.id} key={p.id}>
                        {p.label_es}
                      </option>
                    ))}
                  </select>
                </li>
              );
            })}
          </ul>
        </fieldset>

        <div class="side">
          <fieldset>
            <legend>{t.riskLegend}</legend>
            <p class="fieldset-help">{t.riskHelp}</p>
            <ul class="radio-list">
              {props.riskOptions.map((o) => (
                <li key={o.id}>
                  <label class="radio">
                    <input
                      type="radio"
                      name="riesgo"
                      value={o.rank}
                      checked={state.risk === o.rank}
                      onChange={() => setRisk(o.rank as RiskLevel)}
                    />
                    <span>
                      <span class={`badge ${riskClass(o.rank)}`}>{es.riskLevel(o.rank)}</span>{" "}
                      {o.name}
                    </span>
                    <span class="radio-desc">{o.description}</span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>

          <fieldset>
            <legend>{t.currencyLegend}</legend>
            <div class="segmented">
              {currencies.map((c) => (
                <label class="radio" key={c}>
                  <input
                    type="radio"
                    name="moneda"
                    value={c}
                    checked={cur === c}
                    onChange={() => setCurrency(c)}
                  />
                  <span>{c}</span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      </form>

      <section aria-labelledby="resultado-titulo">
        <h2 id="resultado-titulo">{t.resultsTitle}</h2>
        {offers === null ? (
          <p class="loading">{t.loading}</p>
        ) : state.services.length === 0 ? (
          <p class="callout">{t.noneSelected}</p>
        ) : (
          <>
            <h3 id="ahorro-por-riesgo">{t.savingsByRiskTitle}</h3>
            <p class="small muted">{t.savingsByRiskHelp}</p>
            <ul class="risk-strip" aria-labelledby="ahorro-por-riesgo">
              {RISK_LEVELS.map((k) => (
                <li key={k}>
                  <button type="button" aria-pressed={state.risk === k} onClick={() => setRisk(k)}>
                    <span class={`badge ${riskClass(k)}`}>{es.riskLevel(k)}</span>{" "}
                    {props.riskOptions.find((o) => o.rank === k)?.name}
                    <span class="amount">{formatSignedMoney(byRisk[k], cur)}</span>
                  </button>
                </li>
              ))}
            </ul>

            <div class="table-wrap" role="region" aria-labelledby="cmp-caption" tabIndex={0}>
              <table>
                <caption id="cmp-caption">{t.tableCaption(props.residenceName, riskName)}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t.colService}</th>
                    <th scope="col" class="num">
                      {t.colHome(props.residenceName)}
                    </th>
                    <th scope="col">{t.colBest}</th>
                    <th scope="col" class="num">
                      {t.colMonthly}
                    </th>
                    <th scope="col" class="num">
                      {t.colSaving}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {summary.rows.map((r) => {
                    const svc = serviceById.get(r.service_id);
                    return (
                      <tr key={r.service_id}>
                        <th scope="row">
                          {svc ? <a href={svc.href}>{svc.name}</a> : r.service_id}
                          <div class="small muted">{planLabel(r.service_id, r.plan_id)}</div>
                        </th>
                        <td class="num">
                          {r.home ? (
                            <>
                              {formatMoney(r.homeCost, cur)}
                              {r.home.currency !== cur && (
                                <div class="small muted">
                                  {formatPrice(r.home.amount, r.home.currency)}
                                  {periodSuffix(r.home.billing_period)}
                                </div>
                              )}
                            </>
                          ) : (
                            <span class="muted">{t.noHome}</span>
                          )}
                        </td>
                        <td>
                          {r.best ? (
                            <OfferCell
                              offer={r.best}
                              isHome={r.home?.obs_key === r.best.obs_key}
                              countryName={props.countryNames[r.best.country] ?? r.best.country}
                              label={props.labels[r.best.label]}
                            />
                          ) : (
                            <span class="muted">{t.noOffer}</span>
                          )}
                        </td>
                        <td class="num">{formatMoney(r.bestCost, cur)}</td>
                        <td
                          class={`num ${r.saving !== null && r.saving > 0.005 ? "positive" : ""}`}
                        >
                          {formatSignedMoney(r.saving, cur)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <h3>{t.totalsTitle}</h3>
            <div class="table-wrap" role="region" aria-labelledby="tot-caption" tabIndex={0}>
              <table>
                <caption id="tot-caption">{t.totalsCaption}</caption>
                <thead>
                  <tr>
                    <td />
                    <th scope="col" class="num">
                      {t.colPayHome(props.residenceName)}
                    </th>
                    <th scope="col" class="num">
                      {t.colOptimized}
                    </th>
                    <th scope="col" class="num">
                      {t.colNetSaving}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      [t.rowMonthly, 1],
                      [t.rowYearly, 12],
                    ] as const
                  ).map(([label, months]) => (
                    <tr key={label}>
                      <th scope="row">{label}</th>
                      <td class="num">{formatMoney(totals.home * months, cur)}</td>
                      <td class="num">{formatMoney(totals.optimized * months, cur)}</td>
                      <td class={`num ${totals.saving > 0.005 ? "positive" : ""}`}>
                        {formatSignedMoney(totals.saving * months, cur)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {totals.fee > 0.005 && (
              <p class="note">{t.feeNote(formatPercent(feePct), formatMoney(totals.fee, cur))}</p>
            )}
            {totals.withoutHome.length > 0 && (
              <p class="note">{t.excludedNote(names(totals.withoutHome))}</p>
            )}
            {totals.withoutOffers.length > 0 && (
              <p class="note">{t.noOfferNote(names(totals.withoutOffers))}</p>
            )}
            <p class="note">
              {t.fxNote(formatDate(props.fxAsOf))} {t.shareNote}
            </p>
          </>
        )}
        <p class="visually-hidden" aria-live="polite">
          {liveText}
        </p>
      </section>
    </div>
  );
}

function OfferCell(props: {
  offer: PricedOffer;
  isHome: boolean;
  countryName: string;
  label: ComparadorLabel | undefined;
}) {
  const { offer, label } = props;
  return (
    <div class="offer-cell">
      <span>
        <span class="flag" aria-hidden="true">
          {flagEmoji(offer.country)}
        </span>{" "}
        {props.countryName}
      </span>
      <span class="badges">
        <span class={`badge channel-${offer.channel}`} title={es.channelTitles[offer.channel]}>
          {es.channels[offer.channel] ?? offer.channel}
        </span>
        <span class={`badge ${riskClass(offer.rank)}`} title={label?.description}>
          {label?.name ?? offer.label}
        </span>
        {offer.eu_badge && (
          <span class="badge badge-eu" title={es.euBadgeTitle}>
            {es.euBadge}
          </span>
        )}
      </span>
      <span class="small muted">
        {t.localPrice(
          `${formatPrice(offer.amount, offer.currency)}${periodSuffix(offer.billing_period)}`,
        )}
      </span>
      {props.isHome && <span class="small">{t.homeIsBest}</span>}
    </div>
  );
}
