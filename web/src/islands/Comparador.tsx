/**
 * Comparador: the only interactive island. Controls render on the server; the offers list is
 * read from an inline <script type="application/json"> after hydration because passing ~1-2k
 * offers as island props would bloat the HTML with Astro's prop encoding. The feasibility
 * rules travel the same way: the offers carry the default profile's labels and are relabelled
 * (logic/verdicts.ts) when the visitor picks or describes another profile (logic/profile.ts) or
 * includes pending findings.
 */

import { useEffect, useMemo, useState } from "preact/hooks";
import { ServiceMark } from "../components/ServiceMark";
import { es } from "../i18n/es";
import { instrumentAdvice } from "../logic/advice";
import {
  cheapestFor,
  type CompareContext,
  type CompareOfferFields,
  type PricedOffer,
  type SummaryRow,
  savingsByRisk,
  savingsPctByRisk,
  summarize,
} from "../logic/compare";
import {
  currencyOption,
  flagEmoji,
  formatDate,
  formatMoney,
  formatNumber,
  formatPercent,
  formatPrice,
  formatSignedMoney,
  periodSuffix,
} from "../logic/format";
import { GEO_IP_HOST, guessFromBrowser, type Located, lookupIp } from "../logic/locate";
import {
  changeResidence,
  emptyTie,
  fromCapabilities,
  type Presence,
  type Tie,
  toCapabilities,
  type VisitorProfile,
} from "../logic/profile";
import { type CompareState, decodeState, defaultState, encodeState } from "../logic/selection";
import { unpackOffers } from "../logic/transport";
import {
  type FeasibilityRule,
  type PaymentInstrumentView,
  type ProfileCapabilities,
  RISK_LEVELS,
  type RiskLevel,
} from "../logic/types";
import {
  capabilitiesOf,
  matchingPreset,
  relabelOffers,
  type VerdictSource,
  verdictLookup,
} from "../logic/verdicts";
import { CountryCombobox } from "./CountryCombobox";
import { RiskSlider } from "./RiskSlider";

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

export interface ComparadorProfile {
  id: string;
  name: string;
  description: string;
  capabilities: ProfileCapabilities;
}

export interface ComparadorCountry {
  cc: string;
  name: string;
}

export interface ComparadorResidence extends ComparadorCountry {
  currency: string;
}

export interface ComparadorProps {
  services: ComparadorService[];
  /** Every label by id, for badges. */
  labels: Record<string, ComparadorLabel>;
  /** Thresholds the visitor can accept, rank 0..3. */
  riskOptions: ComparadorLabel[];
  countryNames: Record<string, string>;
  rates: Record<string, number>;
  /** Null before the first FX table is published. */
  fxAsOf: string | null;
  residence: string;
  residenceName: string;
  residenceCurrency: string;
  feePct: number;
  currencies: string[];
  defaultCurrency: string;
  /** id of the inline JSON element holding the packed offers (see logic/transport.ts). */
  dataId: string;
  /** Public profiles, default first. */
  profiles: ComparadorProfile[];
  defaultProfile: string;
  /** Countries a visitor can live in: the ones with prices, sorted by name. */
  residences: ComparadorResidence[];
  /** Countries a visitor can have ties with: the ones with prices, sorted by name. */
  tieCountries: ComparadorCountry[];
  eeaCountries: string[];
  /** Whether any verdict depends on findings pending review (the checkbox only renders then). */
  hasPending: boolean;
  /** id of the inline JSON element holding the feasibility rules. */
  verdictsId: string;
  /** Payment instruments that can be opened online, for the residence advice. */
  instruments: PaymentInstrumentView[];
}

const PRESENCES: Presence[] = ["none", "once", "periodic"];

/** A profile that only lives in `residence`, with its ID, phone and address. */
function emptyCaps(residence: string): ProfileCapabilities {
  return {
    residence_country: residence,
    id_document_countries: [residence],
    phone_countries: [residence],
    address_countries: [residence],
    card_countries: [],
    presence: {},
  };
}

/** Someone who only lives in `cc`: its ID document and phone, no ties (the starting point). */
function neutralProfile(cc: string): VisitorProfile {
  return {
    residence: cc,
    residenceDoc: true,
    residencePhone: true,
    residenceCard: false,
    ties: [],
  };
}

const t = es.compare;

function riskClass(rank: number): string {
  return rank >= 0 && rank <= 4 ? `risk-${rank}` : "risk-unknown";
}

function unpackRules(value: unknown): FeasibilityRule[] {
  const rules = (value as { rules?: unknown } | null)?.rules;
  return Array.isArray(rules) ? (rules as FeasibilityRule[]) : [];
}

export default function Comparador(props: ComparadorProps) {
  const { services, rates, feePct } = props;
  const [state, setState] = useState<CompareState>(() =>
    defaultState(services, props.defaultCurrency),
  );
  const [offers, setOffers] = useState<CompareOfferFields[] | null>(null);
  const [rules, setRules] = useState<FeasibilityRule[]>([]);
  const [ready, setReady] = useState(false);
  const [shareState, setShareState] = useState<"idle" | "copied" | "failed">("idle");
  /** Where the visitor seems to be (browser hints, or their IP when they ask for it). */
  const [located, setLocated] = useState<Located | null>(null);
  const [ipState, setIpState] = useState<"idle" | "loading" | "failed">("idle");

  const source: VerdictSource = useMemo(
    () => ({
      default_profile: props.defaultProfile,
      profiles: props.profiles,
      rules,
      eea_countries: props.eeaCountries,
    }),
    [props.defaultProfile, props.profiles, rules, props.eeaCountries],
  );
  const choice = state.custom ? toCapabilities(state.custom) : state.profile;
  const caps = capabilitiesOf(source, choice);
  const preset = props.profiles.find((p) => p.id === (state.profile ?? props.defaultProfile));
  const visitor: VisitorProfile | null = state.custom ?? (caps ? fromCapabilities(caps) : null);

  const residenceById = useMemo(
    () => new Map(props.residences.map((r) => [r.cc, r])),
    [props.residences],
  );
  const countryName = useMemo(() => {
    const names = new Map(props.tieCountries.map((c) => [c.cc, c.name]));
    return (cc: string) => names.get(cc) ?? props.countryNames[cc] ?? cc;
  }, [props.tieCountries, props.countryNames]);
  const residence = caps?.residence_country ?? props.residence;
  const home = residenceById.get(residence);
  const residenceName = home?.name ?? props.residenceName;
  const residenceCurrency = home?.currency ?? props.residenceCurrency;

  const currencies = useMemo(
    () =>
      [...new Set([residenceCurrency, ...props.currencies])].filter(
        (c) => c === "EUR" || (rates[c] ?? 0) > 0,
      ),
    [residenceCurrency, props.currencies, rates],
  );
  // Residence currency, EUR and USD on top; every other one alphabetically below.
  const [commonCurrencies, otherCurrencies] = useMemo(() => {
    const common = new Set([residenceCurrency, "EUR", "USD"]);
    return [
      currencies.filter((c) => common.has(c)),
      currencies.filter((c) => !common.has(c)).sort(),
    ];
  }, [currencies, residenceCurrency]);

  useEffect(() => {
    const read = <T,>(id: string, unpack: (value: unknown) => T): T => {
      let value: unknown = null;
      try {
        value = JSON.parse(document.getElementById(id)?.textContent ?? "{}");
      } catch {
        value = null;
      }
      return unpack(value);
    };
    setOffers(read(props.dataId, unpackOffers));
    setRules(read(props.verdictsId, unpackRules));
    const decoded = decodeState(
      window.location.hash,
      services,
      [...new Set([...props.residences.map((r) => r.currency), ...props.currencies])].filter(
        (c) => c === "EUR" || (rates[c] ?? 0) > 0,
      ),
      state,
      {
        profiles: props.profiles.map((p) => p.id).filter((id) => id !== props.defaultProfile),
        pending: props.hasPending,
        residences: props.residences.map((r) => r.cc),
        countries: new Set(props.tieCountries.map((c) => c.cc)),
      },
    );
    // A fresh visit (no comparison in the link) starts from the visitor's country (browser
    // hints only; nothing leaves the browser) with no ties. A shared link keeps its profile,
    // including the default one, which it encodes by leaving p and pf out.
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    if (["s", "p", "pf"].some((k) => params.has(k))) {
      setState(normalizeProfile(decoded));
    } else {
      const guess = guessFromBrowser();
      setLocated(guess);
      const cc = guess && residenceById.has(guess.cc) ? guess.cc : props.residence;
      setState(
        followCurrency(
          decoded,
          normalizeProfile({ ...decoded, profile: null, custom: neutralProfile(cc) }),
        ),
      );
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const hash = `#${encodeState(state)}`;
    if (window.location.hash !== hash) {
      history.replaceState(history.state, "", hash);
    }
  }, [state, ready]);

  const verdict = useMemo(
    () => verdictLookup(source, choice, state.pending),
    [source, state.custom, state.profile, state.pending],
  );
  // offers.json already carries the default profile's verdicts without pending findings.
  const relabelled = useMemo(
    () =>
      offers && (state.custom || state.profile !== null || state.pending)
        ? relabelOffers(offers, source, choice, state.pending)
        : offers,
    [offers, source, state.custom, state.profile, state.pending],
  );

  const ctx: CompareContext = useMemo(
    () => ({ rates, residence, residenceCurrency, feePct }),
    [rates, residence, residenceCurrency, feePct],
  );

  const rows = useMemo(
    () =>
      relabelled
        ? state.services.map((id) => cheapestFor(id, state.plans[id] ?? "", relabelled, ctx))
        : [],
    [relabelled, state.services, state.plans, ctx],
  );
  const display = useMemo(
    () => ({ currency: state.currency, rates, feePct }),
    [state.currency, rates, feePct],
  );
  const summary = useMemo(() => summarize(rows, state.risk, display), [rows, state.risk, display]);
  const byRisk = useMemo(() => savingsByRisk(rows, display), [rows, display]);
  const pctByRisk = useMemo(() => savingsPctByRisk(rows, display), [rows, display]);
  const riskNames = useMemo(
    () => Object.fromEntries(props.riskOptions.map((o) => [o.rank, o.name])),
    [props.riskOptions],
  );
  const maxSaving = Math.max(0, ...RISK_LEVELS.map((k) => byRisk[k]));

  const eea = useMemo(() => new Set(props.eeaCountries), [props.eeaCountries]);
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
  const setProfile = (id: string) =>
    setState((s) =>
      followCurrency(s, { ...s, custom: null, profile: id === props.defaultProfile ? null : id }),
    );
  /** A described profile with a curated profile's capabilities is that profile. */
  function normalizeProfile(s: CompareState): CompareState {
    if (!s.custom) return s;
    const id = matchingPreset(source, toCapabilities(s.custom));
    if (id === null) return s;
    return { ...s, custom: null, profile: id === props.defaultProfile ? null : id };
  }
  const usable = (c: string) => c === "EUR" || (rates[c] ?? 0) > 0;
  const homeOf = (s: CompareState) =>
    residenceById.get(
      capabilitiesOf(source, s.custom ? toCapabilities(s.custom) : s.profile)?.residence_country ??
        "",
    );
  /** A residence change takes the display currency along when it was the old home one. */
  const followCurrency = (prev: CompareState, next: CompareState): CompareState => {
    const before = homeOf(prev)?.currency;
    const after = homeOf(next)?.currency;
    return after && before !== after && prev.currency === before && usable(after)
      ? { ...next, currency: after }
      : next;
  };
  const editProfile = (edit: (v: VisitorProfile) => VisitorProfile) =>
    setState((s) => {
      const current =
        s.custom ??
        fromCapabilities(capabilitiesOf(source, s.profile) ?? emptyCaps(props.residence));
      return followCurrency(s, normalizeProfile({ ...s, custom: edit(current) }));
    });
  const setTie = (i: number, patch: Partial<Tie>) =>
    editProfile((v) => ({
      ...v,
      ties: v.ties.map((t, j) => (j === i ? { ...t, ...patch } : t)),
    }));
  const addTie = (cc: string) =>
    cc && editProfile((v) => ({ ...v, ties: [...v.ties, emptyTie(cc)] }));
  const removeTie = (i: number) =>
    editProfile((v) => ({ ...v, ties: v.ties.filter((_, j) => j !== i) }));
  const setPending = (pending: boolean) => setState((s) => ({ ...s, pending }));
  const detectByIp = async () => {
    setIpState("loading");
    const found = await lookupIp();
    if (!found) {
      setIpState("failed");
      return;
    }
    setIpState("idle");
    setLocated(found);
    if (residenceById.has(found.cc)) editProfile((v) => changeResidence(v, found.cc));
  };

  const planLabel = (serviceId: string, planId: string) =>
    serviceById.get(serviceId)?.plans.find((p) => p.id === planId)?.label_es ?? planId;
  const names = (ids: string[]) => ids.map((id) => serviceById.get(id)?.name ?? id).join(", ");
  const unevaluatedNote = (r: SummaryRow): UnevaluatedInfo | null => {
    if (!r.unevaluated) return null;
    const reference = r.bestCost ?? r.homeCost;
    return {
      country: r.unevaluated.country,
      countryName: props.countryNames[r.unevaluated.country] ?? r.unevaluated.country,
      cost: formatMoney(r.unevaluatedCost, cur),
      diff:
        reference !== null && r.unevaluatedCost !== null
          ? formatSignedMoney(r.unevaluatedCost - reference, cur)
          : null,
    };
  };

  const { totals } = summary;
  const hasSaving = totals.saving > 0.005;
  const share = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      setShareState("copied");
    } catch {
      setShareState("failed");
    }
    window.setTimeout(() => setShareState("idle"), 2500);
  };
  const liveText =
    offers && state.services.length > 0
      ? t.summaryLive(
          formatSignedMoney(totals.saving, cur),
          formatSignedMoney(totals.saving * 12, cur),
        )
      : "";

  return (
    <div class="comparador">
      <div class="comparador-main">
        <form class="controls" onSubmit={(e) => e.preventDefault()}>
          <fieldset>
            <legend>{t.servicesLegend}</legend>
            <p class="fieldset-help">{t.servicesHelp}</p>
            <ul class="service-list">
              {services.map((s) => {
                const checked = state.services.includes(s.id);
                return (
                  <li class={`service-row ${checked ? "is-on" : ""}`} key={s.id}>
                    <label class="check" for={`svc-${s.id}`}>
                      <input
                        type="checkbox"
                        id={`svc-${s.id}`}
                        checked={checked}
                        onChange={(e) => toggleService(s.id, e.currentTarget.checked)}
                      />
                      <ServiceMark id={s.id} name={s.name} />
                      <span class="service-name">{s.name}</span>
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
              <legend>{t.profileLegend}</legend>
              <p class="fieldset-help">{t.profileHelp}</p>
              {visitor && (
                <>
                  <div class="field">
                    <label for="residencia">{t.residenceLabel}</label>
                    <CountryCombobox
                      id="residencia"
                      countries={props.residences}
                      value={visitor.residence}
                      describedBy="residencia-origen residencia-ayuda"
                      onSelect={(cc) => editProfile((v) => changeResidence(v, cc))}
                    />
                    <p id="residencia-origen" class="fieldset-help locate-note" aria-live="polite">
                      {ipState === "loading"
                        ? t.locateLoading
                        : ipState === "failed"
                          ? t.locateFailed
                          : located && !residenceById.has(located.cc)
                            ? t.locateUncovered(countryName(located.cc))
                            : located && located.cc === visitor.residence
                              ? t.locateFound[located.source]
                              : null}{" "}
                      {located?.source !== "ip" && ipState !== "loading" && (
                        <button type="button" class="link-button" onClick={detectByIp}>
                          {t.locateIp}
                        </button>
                      )}
                    </p>
                    <p id="residencia-ayuda" class="fieldset-help">
                      {t.residenceHelp(GEO_IP_HOST)}
                    </p>
                  </div>
                  <fieldset class="own">
                    <legend>{t.ownLabel(residenceName)}</legend>
                    <ul class="tie-flags">
                      {(
                        [
                          ["residenceDoc", t.ownDoc],
                          ["residencePhone", t.tiePhone],
                          ["residenceCard", t.tieCard],
                        ] as const
                      ).map(([key, label]) => (
                        <li key={key}>
                          <label class="check" for={`propio-${key}`}>
                            <input
                              type="checkbox"
                              id={`propio-${key}`}
                              checked={visitor[key]}
                              onChange={(e) => {
                                const on = e.currentTarget.checked;
                                editProfile((v) => ({ ...v, [key]: on }));
                              }}
                            />
                            {label}
                          </label>
                        </li>
                      ))}
                    </ul>
                  </fieldset>
                  <fieldset class="ties">
                    <legend>{t.tiesLabel}</legend>
                    <p class="fieldset-help">{t.tiesHelp}</p>
                    {visitor.ties.length === 0 && <p class="small muted">{t.tiesNone}</p>}
                    <ul class="tie-list">
                      {visitor.ties.map((tie, i) => (
                        <TieRow
                          key={tie.cc}
                          index={i}
                          tie={tie}
                          name={countryName(tie.cc)}
                          options={props.tieCountries.filter(
                            (c) =>
                              c.cc === tie.cc ||
                              (c.cc !== visitor.residence &&
                                !visitor.ties.some((other) => other.cc === c.cc)),
                          )}
                          onChange={(patch) => setTie(i, patch)}
                          onRemove={() => removeTie(i)}
                        />
                      ))}
                    </ul>
                    <label class="visually-hidden" for="vinculo-nuevo">
                      {t.tieAdd}
                    </label>
                    <CountryCombobox
                      id="vinculo-nuevo"
                      countries={props.tieCountries.filter(
                        (c) =>
                          c.cc !== visitor.residence &&
                          !visitor.ties.some((other) => other.cc === c.cc),
                      )}
                      value={null}
                      placeholder={t.tieAdd}
                      onSelect={addTie}
                    />
                  </fieldset>
                </>
              )}
              <p id="perfil-descripcion" class="profile-desc">
                {state.custom ? t.profileCustomDesc : preset?.description}
              </p>
              <p class="profile-examples">
                <span class="small muted">{t.profileExamples}</span>
                {props.profiles.map((p) => (
                  <button
                    type="button"
                    class="chip"
                    key={p.id}
                    aria-pressed={!state.custom && preset?.id === p.id}
                    onClick={() => setProfile(p.id)}
                  >
                    {p.name}
                  </button>
                ))}
              </p>
              {props.hasPending && (
                <>
                  <label class="check" for="pendientes">
                    <input
                      type="checkbox"
                      id="pendientes"
                      checked={state.pending}
                      aria-describedby="pendientes-ayuda"
                      onChange={(e) => setPending(e.currentTarget.checked)}
                    />
                    {t.pendingLabel}
                  </label>
                  <p id="pendientes-ayuda" class="fieldset-help">
                    {t.pendingHelp}
                  </p>
                </>
              )}
            </fieldset>

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
                      <span class={`risk-dot-label ${riskClass(o.rank)}`}>
                        {es.riskLevel(o.rank)}
                      </span>
                      <span class="radio-name">{o.name}</span>
                      <span class="radio-desc">{o.description}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>

            <fieldset>
              <legend>{t.currencyLegend}</legend>
              <label class="visually-hidden" for="moneda">
                {t.currencyLabel}
              </label>
              <select id="moneda" value={cur} onChange={(e) => setCurrency(e.currentTarget.value)}>
                <optgroup label={t.currencyCommon}>
                  {commonCurrencies.map((c) => (
                    <option value={c} key={c}>
                      {currencyOption(c)}
                    </option>
                  ))}
                </optgroup>
                {otherCurrencies.length > 0 && (
                  <optgroup label={t.currencyAll}>
                    {otherCurrencies.map((c) => (
                      <option value={c} key={c}>
                        {currencyOption(c)}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            </fieldset>
          </div>
        </form>

        <section aria-labelledby="resultado-titulo">
          <h2 id="resultado-titulo">{t.resultsTitle}</h2>
          {relabelled === null ? (
            <p class="loading">{t.loading}</p>
          ) : state.services.length === 0 ? (
            <p class="callout">{t.noneSelected}</p>
          ) : (
            <>
              <div class="result-card">
                <div class={`result-top ${hasSaving ? "is-positive" : ""}`}>
                  {hasSaving ? (
                    <>
                      <span class="result-label">{t.savingYearLabel}</span>
                      <span class="result-amount">
                        {formatNumber(Math.round(totals.saving * 12), 0)}{" "}
                        <span class="result-currency">{cur}</span>
                      </span>
                      <span class="result-sub">
                        {t.savingMonthLabel(formatMoney(totals.saving, cur))} · {riskName}
                      </span>
                    </>
                  ) : (
                    <>
                      <span class="result-sub result-none">
                        {maxSaving <= 0.005 && totals.unevaluatedSaving > 0.005
                          ? t.savingNoneEvaluated
                          : t.savingNone}
                      </span>
                      {maxSaving > 0.005 && (
                        <span class="result-sub">{t.savingMore(formatMoney(maxSaving, cur))}</span>
                      )}
                      {totals.unevaluatedSaving > 0.005 && (
                        <span class="result-sub">
                          {t.savingUnevaluated(formatMoney(totals.unevaluatedSaving, cur))}
                        </span>
                      )}
                    </>
                  )}
                  {feePct > 0 && (
                    <span class="result-chip">{t.feeChip(formatPercent(feePct))}</span>
                  )}
                </div>

                <div class="result-body">
                  <h3 id="ahorro-por-riesgo">{t.savingsByRiskTitle}</h3>
                  <p class="visually-hidden">{t.savingsByRiskHelp}</p>
                  <RiskSlider
                    levels={RISK_LEVELS}
                    value={state.risk}
                    onChange={setRisk}
                    pct={pctByRisk}
                    names={riskNames}
                    labelledBy="ahorro-por-riesgo"
                  />

                  <ul class="result-rows">
                    {summary.rows.map((r) => (
                      <li key={r.service_id}>
                        <span class="result-row-name">
                          <strong>{serviceById.get(r.service_id)?.name ?? r.service_id}</strong>
                          <span class="small muted">
                            {r.best && (
                              <>
                                <span aria-hidden="true">{flagEmoji(r.best.country)}</span>{" "}
                                {props.countryNames[r.best.country] ?? r.best.country} ·{" "}
                              </>
                            )}
                            {planLabel(r.service_id, r.plan_id)}
                          </span>
                        </span>
                        <span class="result-row-num">
                          <strong class={r.saving !== null && r.saving > 0.005 ? "positive" : ""}>
                            {formatSignedMoney(r.saving, cur)}
                          </strong>
                          <span class="small muted">
                            {t.serviceSaving(formatMoney(r.bestCost, cur))}
                          </span>
                        </span>
                      </li>
                    ))}
                    <li class="result-total">
                      <span>{t.totalOptimized}</span>
                      <strong>{formatMoney(totals.optimized, cur)}</strong>
                    </li>
                  </ul>

                  <button type="button" class="button button-dark share-button" onClick={share}>
                    {shareState === "copied"
                      ? t.shareCopied
                      : shareState === "failed"
                        ? t.shareFailed
                        : t.shareButton}
                  </button>
                  <p class="small muted result-fx">
                    {t.fxNote(formatDate(props.fxAsOf))} {t.shareNote}
                  </p>
                </div>
              </div>
            </>
          )}
          <p class="visually-hidden" aria-live="polite">
            {liveText}
          </p>
        </section>
      </div>
      {relabelled !== null && state.services.length > 0 && (
        <div class="result-detail">
          <h3 class="detail-title">{t.detailTitle}</h3>
          <div class="table-wrap is-plain" role="region" aria-labelledby="cmp-caption" tabIndex={0}>
            <table>
              <caption id="cmp-caption" class="visually-hidden">
                {t.tableCaption(residenceName, riskName)}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t.colService}</th>
                  <th scope="col" class="num">
                    {t.colHome(residenceName)}
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
                            residenceName={residenceName}
                            runnerUp={
                              r.runnerUp && {
                                countryName:
                                  props.countryNames[r.runnerUp.country] ?? r.runnerUp.country,
                                country: r.runnerUp.country,
                                aboveRisk: r.runnerUpAboveRisk,
                                cost: formatMoney(r.runnerUpCost, cur),
                                diff: formatSignedMoney(
                                  r.runnerUpCost !== null && r.bestCost !== null
                                    ? r.runnerUpCost - r.bestCost
                                    : null,
                                  cur,
                                ),
                              }
                            }
                            unevaluated={unevaluatedNote(r)}
                            pending={
                              state.pending &&
                              verdict(r.best.service_id, r.best.country).provenance ===
                                "agent_pending"
                            }
                            template={
                              verdict(r.best.service_id, r.best.country).provenance === "template"
                            }
                          />
                        ) : (
                          <div class="offer-cell">
                            <span class="muted">{t.noOffer}</span>
                            <UnevaluatedNote note={unevaluatedNote(r)} />
                          </div>
                        )}
                      </td>
                      <td class="num">{formatMoney(r.bestCost, cur)}</td>
                      <td class={`num ${r.saving !== null && r.saving > 0.005 ? "positive" : ""}`}>
                        {formatSignedMoney(r.saving, cur)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">{t.rowTotals}</th>
                  <td class="num">
                    {formatMoney(totals.home, cur)}
                    <div class="small muted">{t.perYear(formatMoney(totals.home * 12, cur))}</div>
                  </td>
                  <td class="small muted">{t.feeChip(formatPercent(feePct))}</td>
                  <td class="num">
                    {formatMoney(totals.optimized, cur)}
                    <div class="small muted">
                      {t.perYear(formatMoney(totals.optimized * 12, cur))}
                    </div>
                  </td>
                  <td class={`num ${hasSaving ? "positive" : ""}`}>
                    {formatSignedMoney(totals.saving, cur)}
                    <div class="small muted">
                      {t.perYear(formatSignedMoney(totals.saving * 12, cur))}
                    </div>
                  </td>
                </tr>
              </tfoot>
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
          {state.pending && <p class="note">{t.pendingNote}</p>}
        </div>
      )}
      {caps && (
        <ResidenceAdvice
          caps={caps}
          residenceName={residenceName}
          instruments={props.instruments}
          eea={eea}
          countryName={countryName}
        />
      )}
    </div>
  );
}

/**
 * What the residence (and the documents the visitor marked) opens up for paying: accounts that
 * can be opened online, and the EU rules an EU/EEA resident can rely on. Never a way around a
 * service's terms: the labels above already count these accounts.
 */
function ResidenceAdvice(props: {
  caps: ProfileCapabilities;
  residenceName: string;
  instruments: PaymentInstrumentView[];
  eea: ReadonlySet<string>;
  countryName: (cc: string) => string;
}) {
  const { caps, countryName } = props;
  const advice = instrumentAdvice(props.instruments, caps, props.eea);
  const inEea = props.eea.has(caps.residence_country);
  const facts = (i: PaymentInstrumentView) =>
    [
      t.adviceIssuer(countryName(i.issuer_country)),
      i.card_country ? t.adviceCard(countryName(i.card_country)) : t.adviceCardUnknown,
      i.currencies.join(", "),
    ].join(" · ");
  const name = (i: PaymentInstrumentView) =>
    i.url ? (
      <a href={i.url} rel="noopener noreferrer">
        {i.name}
      </a>
    ) : (
      i.name
    );
  return (
    <div class="advice" role="region" aria-labelledby="ventajas-titulo">
      <h3 id="ventajas-titulo" class="detail-title">
        {t.adviceTitle(props.residenceName)}
      </h3>
      <p class="note">{t.adviceLead}</p>
      {advice.open.length > 0 ? (
        <ul class="advice-list">
          {advice.open.map((i) => (
            <li key={i.id}>
              <strong>{name(i)}</strong>
              <span class="small muted">{facts(i)}</span>
              {i.notes_es && <span class="small">{i.notes_es}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p class="note">{t.adviceNone}</p>
      )}
      {inEea && <p class="note">{t.adviceEea}</p>}
      <p class="note">{t.adviceHonest}</p>
    </div>
  );
}

function OfferCell(props: {
  offer: PricedOffer;
  isHome: boolean;
  countryName: string;
  label: ComparadorLabel | undefined;
  residenceName: string;
  /** Cheapest offer from another country, shown when the home offer wins. */
  runnerUp: {
    country: string;
    countryName: string;
    cost: string;
    diff: string;
    aboveRisk: boolean;
  } | null;
  /** Cheaper offer from another country with no verdict yet for this profile. */
  unevaluated: UnevaluatedInfo | null;
  /** The verdict comes from findings still pending review. */
  pending: boolean;
  /** The verdict reapplies the service's curated record to this country. */
  template: boolean;
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
          <span class="badge badge-eu" title={es.euBadgeTitle(props.residenceName)}>
            {es.euBadge}
          </span>
        )}
        {props.pending && (
          <span class="badge provenance-agent_pending" title={es.provenanceTitles.agent_pending}>
            {es.provenance.agent_pending}
          </span>
        )}
        {props.template && (
          <span class="badge provenance-template" title={es.provenanceTitles.template}>
            {es.provenance.template}
          </span>
        )}
      </span>
      <span class="small muted">
        {t.localPrice(
          `${formatPrice(offer.amount, offer.currency)}${periodSuffix(offer.billing_period)}`,
        )}
      </span>
      {props.isHome && (
        <span class="small">{props.unevaluated ? t.homeIsBestEvaluated : t.homeIsBest}</span>
      )}
      {props.isHome && (
        <span class="small muted">
          {props.runnerUp ? (
            <>
              <span aria-hidden="true">{flagEmoji(props.runnerUp.country)}</span>{" "}
              {(props.runnerUp.aboveRisk ? t.runnerUpAboveRisk : t.runnerUp)(
                props.runnerUp.countryName,
                props.runnerUp.cost,
                props.runnerUp.diff,
              )}
            </>
          ) : props.unevaluated ? null : (
            t.noRunnerUp
          )}
        </span>
      )}
      <UnevaluatedNote note={props.unevaluated} />
    </div>
  );
}

interface UnevaluatedInfo {
  country: string;
  countryName: string;
  cost: string;
  diff: string | null;
}

function UnevaluatedNote(props: { note: UnevaluatedInfo | null }) {
  const { note } = props;
  if (!note) return null;
  return (
    <span class="small muted">
      <span aria-hidden="true">{flagEmoji(note.country)}</span>{" "}
      {t.unevaluatedOffer(note.countryName, note.cost, note.diff)}
    </span>
  );
}

function TieRow(props: {
  index: number;
  tie: Tie;
  name: string;
  options: ComparadorCountry[];
  onChange: (patch: Partial<Tie>) => void;
  onRemove: () => void;
}) {
  const { tie, index } = props;
  const id = `vinculo-${index}`;
  const flags = [
    ["doc", t.tieDoc],
    ["card", t.tieCard],
    ["phone", t.tiePhone],
    ["address", t.tieAddress],
  ] as const;
  return (
    <li class="tie">
      <div class="tie-head">
        <label class="visually-hidden" for={`${id}-pais`}>
          {t.tieCountry(index + 1)}
        </label>
        <select
          id={`${id}-pais`}
          value={tie.cc}
          onChange={(e) => props.onChange({ cc: e.currentTarget.value })}
        >
          {props.options.map((c) => (
            <option value={c.cc} key={c.cc}>
              {c.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          class="link-button"
          aria-label={t.tieRemove(props.name)}
          onClick={props.onRemove}
        >
          {t.tieRemoveText}
        </button>
      </div>
      <ul class="tie-flags">
        {flags.map(([key, label]) => (
          <li key={key}>
            <label class="check" for={`${id}-${key}`}>
              <input
                type="checkbox"
                id={`${id}-${key}`}
                checked={tie[key]}
                onChange={(e) => props.onChange({ [key]: e.currentTarget.checked })}
              />
              {label}
            </label>
          </li>
        ))}
      </ul>
      {!tie.card && <p class="tie-note small muted">{t.tieNoCard(props.name)}</p>}
      <div class="tie-presence">
        <label for={`${id}-viajes`}>{t.tiePresence}</label>
        <select
          id={`${id}-viajes`}
          value={tie.presence}
          onChange={(e) => props.onChange({ presence: e.currentTarget.value as Presence })}
        >
          {PRESENCES.map((p) => (
            <option value={p} key={p}>
              {t.presence[p]}
            </option>
          ))}
        </select>
      </div>
    </li>
  );
}
