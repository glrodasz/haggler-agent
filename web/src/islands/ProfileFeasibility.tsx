/**
 * The "Factibilidad desde …" section of a service or country page. As built it shows the
 * default profile's curated and researched records (the children); for a visitor whose profile
 * is another one it lists the verdict of each evaluated pair for that profile instead, from the
 * same rules as the comparator (records are written for one profile and do not apply).
 */

import type { ComponentChildren } from "preact";
import { getMessages } from "../i18n";
import type { Locale } from "../i18n/locale";
import { flagEmoji, formatFor } from "../logic/format";
import { routesFor } from "../logic/paths";
import { isEvaluated } from "../logic/profileKit";
import { useProfile } from "./useProfile";

export interface ProfileFeasibilityProps {
  locale: Locale;
  /** "service": one row per target country; "country": one row per service in that country. */
  mode: "service" | "country";
  /** The service id or the country code of the page. */
  subject: string;
  defaultProfile: string;
  defaultResidenceName: string;
  /** Service names by id. */
  names: Record<string, string>;
  children?: ComponentChildren;
}

function riskClass(rank: number): string {
  return rank >= 0 && rank <= 4 ? `risk-${rank}` : "risk-unknown";
}

export default function ProfileFeasibility(props: ProfileFeasibilityProps) {
  const state = useProfile();
  if (!state || state.view.preset === props.defaultProfile) return <>{props.children}</>;
  const { view, kit } = state;
  const es = getMessages(props.locale);
  const t = es.visitorProfile;
  const routes = routesFor(props.locale);
  const { compareNames } = formatFor(props.locale);
  const title = (props.mode === "service" ? es.service : es.country).feasibilityTitle(
    view.residenceName,
  );
  const heading = <h2 id="factibilidad">{title}</h2>;
  if (props.mode === "country" && props.subject === view.residence) {
    return (
      <>
        {heading}
        <p>{t.ownCountry}</p>
      </>
    );
  }
  const pairs =
    props.mode === "service"
      ? [
          ...new Set(
            kit.rules.filter((r) => r.service_id === props.subject).map((r) => r.target_country),
          ),
        ]
          .filter((cc) => cc !== view.residence)
          .map((cc) => ({ service: props.subject, country: cc }))
      : [
          ...new Set(
            kit.rules.filter((r) => r.target_country === props.subject).map((r) => r.service_id),
          ),
        ].map((id) => ({ service: id, country: props.subject }));
  const rows = pairs
    .map((p) => ({ ...p, verdict: view.verdict(p.service, p.country) }))
    .filter((r) => isEvaluated(r.verdict));
  const nameOf = (r: { service: string; country: string }) =>
    props.mode === "service"
      ? (kit.country_names[r.country] ?? r.country)
      : (props.names[r.service] ?? r.service);
  rows.sort(
    (a, b) => a.verdict.rank - b.verdict.rank || compareNames(nameOf(a), nameOf(b)),
  );
  const missing = pairs.length - rows.length;
  return (
    <>
      {heading}
      {rows.length === 0 ? (
        <p>{t.noneEvaluated}</p>
      ) : (
        <>
          <p class="note">{props.mode === "service" ? t.feasibilityLead : t.countryFeasibilityLead}</p>
          <div
            class="table-wrap"
            role="region"
            aria-labelledby="factibilidad-perfil-caption"
            tabindex={0}
          >
            <table>
              <caption id="factibilidad-perfil-caption">{title}</caption>
              <thead>
                <tr>
                  <th scope="col">
                    {props.mode === "service" ? es.service.colTarget : es.country.colService}
                  </th>
                  <th scope="col">{es.service.colLabel}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const info = kit.labels[r.verdict.label];
                  const via = r.verdict.card_via ? kit.instruments[r.verdict.card_via] : undefined;
                  return (
                    <tr key={`${r.service}:${r.country}`}>
                      <th scope="row">
                        {props.mode === "service" ? (
                          <span class="nowrap">
                            <span class="flag" aria-hidden="true">
                              {flagEmoji(r.country)}
                            </span>{" "}
                            <a href={routes.country(r.country)}>{nameOf(r)}</a>
                          </span>
                        ) : (
                          <a href={routes.service(r.service)}>{nameOf(r)}</a>
                        )}
                      </th>
                      <td>
                        <span class="badges">
                          <span
                            class={`badge ${riskClass(info?.rank ?? r.verdict.rank)}`}
                            title={info?.description}
                          >
                            {info?.name ?? r.verdict.label}
                          </span>
                          {r.verdict.eu_badge && (
                            <span
                              class="badge badge-eu"
                              title={es.euBadgeTitle(view.residenceName)}
                            >
                              {es.euBadge}
                            </span>
                          )}
                          {r.verdict.provenance === "agent_pending" && (
                            <span
                              class="badge provenance-agent_pending"
                              title={es.provenanceTitles.agent_pending}
                            >
                              {es.provenance.agent_pending}
                            </span>
                          )}
                        </span>
                        {via && <div class="small muted">{es.compare.cardViaShort(via)}</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {missing > 0 && rows.length > 0 && (
        <p class="small muted">
          {props.mode === "service" ? t.unevaluated(missing) : t.unevaluatedServices(missing)}
        </p>
      )}
    </>
  );
}
