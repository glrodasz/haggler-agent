/**
 * /paquetes: the active bundles grouped by verdict. As built, the default profile's verdict
 * (with the other public profiles' next to it); once the visitor's profile is known, each
 * bundle's rule (bundles.json `rule`, findings pending review counted like the comparator) is
 * walked for it and the fee follows its residence currency.
 */

import { es, lookup } from "../i18n/es";
import { flagEmoji, formatMoney, formatPercent } from "../logic/format";
import { groupBundles, savingClass } from "../logic/site";
import type { Bundle } from "../logic/types";
import { useProfile } from "./useProfile";

interface Mark {
  label: string;
  rank: number;
}

export interface BundleRow {
  bundle_id: string;
  name: string;
  href: string;
  linkLabel: string;
  country: string;
  countryName: string;
  countryHref: string;
  display_status: Bundle["display_status"];
  currency: string;
  monthly_eur: number | null;
  full_monthly_eur: number | null;
  monthly_eur_with_fee: number | null;
  saving_basis: string;
  saving_pct: number | null;
  /** bundleSavingNotes, already worded. */
  notes: string[];
  /** The default profile's reviewed verdict, its pending variant, the other profiles'. */
  main: Mark | null;
  pending: Mark | null;
  others: (Mark & { profileName: string })[];
}

export interface BundleGroupsProps {
  bundles: BundleRow[];
  labels: Record<string, { name: string; description: string; rank: number }>;
  /** e.g. "2 %". */
  fee: string;
}

const t = es.bundlesIndex;
const b = es.bundle;

function riskClass(rank: number): string {
  return rank >= 0 && rank <= 4 ? `risk-${rank}` : "risk-unknown";
}

const groupId = (label: string) => `grupo-${label.replace(/[^a-z0-9_-]/gi, "-")}`;

export default function BundleGroups(props: BundleGroupsProps) {
  const state = useProfile();
  const view = state?.view ?? null;
  const verdictOf = (x: BundleRow): (Mark & { pendingReview?: boolean }) | undefined => {
    if (!view) return x.main ?? undefined;
    const v = view.bundleVerdict(x.bundle_id);
    return v && { label: v.label, rank: v.rank, pendingReview: v.provenance === "agent_pending" };
  };
  const groups = groupBundles<BundleRow>(props.bundles, verdictOf);
  const badge = ({ label, rank }: Mark) => {
    const info = props.labels[label];
    return (
      <span class={`badge ${riskClass(info?.rank ?? rank)}`} title={info?.description}>
        {info?.name ?? label}
      </span>
    );
  };
  if (groups.length === 0) return <p>{t.empty}</p>;
  return (
    <>
      {groups.map((g) => {
        const info = props.labels[g.label];
        const id = groupId(g.label);
        return (
          <section aria-labelledby={id} key={g.label}>
            <h2 id={id}>
              {badge(g)} <span class="muted small">{t.groupCount(g.bundles.length)}</span>
            </h2>
            {info?.description && <p class="small muted">{info.description}</p>}
            <div class="table-wrap" role="region" aria-labelledby={`${id}-caption`} tabindex={0}>
              <table>
                <caption id={`${id}-caption`} class="visually-hidden">
                  {view
                    ? es.visitorProfile.bundleCaption(info?.name ?? g.label)
                    : t.caption(info?.name ?? g.label)}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t.colBundle}</th>
                    <th scope="col">{t.colCountry}</th>
                    <th scope="col">{t.colStatus}</th>
                    <th scope="col" class="num">
                      {t.colMonthly}
                    </th>
                    <th scope="col" class="num">
                      {t.colSaving}
                    </th>
                    {!view && <th scope="col">{t.colOthers}</th>}
                  </tr>
                </thead>
                <tbody>
                  {g.bundles.map((x) => {
                    const withFee = view
                      ? view.withFee(x.monthly_eur, x.currency)
                      : x.monthly_eur_with_fee;
                    const pendingReview = view ? verdictOf(x)?.pendingReview : false;
                    return (
                      <tr key={x.bundle_id}>
                        <th scope="row">
                          <a href={x.href} aria-label={x.linkLabel}>
                            {x.name}
                          </a>
                          {!view && x.pending && (
                            <div class="small muted">
                              {t.pendingVerdict} {badge(x.pending)}
                            </div>
                          )}
                          {pendingReview && (
                            <div class="small">
                              <span
                                class="badge provenance-agent_pending"
                                title={es.provenanceTitles.agent_pending}
                              >
                                {es.provenance.agent_pending}
                              </span>
                            </div>
                          )}
                        </th>
                        <td>
                          <span class="nowrap">
                            <span class="flag" aria-hidden="true">
                              {flagEmoji(x.country)}
                            </span>{" "}
                            <a href={x.countryHref}>{x.countryName}</a>
                          </span>
                        </td>
                        <td>
                          <span
                            class={`badge status-${x.display_status}`}
                            title={b.displayStatusTitles[x.display_status]}
                          >
                            {lookup(b.displayStatus, x.display_status)}
                          </span>
                        </td>
                        <td class="num">
                          {formatMoney(x.monthly_eur, "EUR")}
                          {x.saving_basis === "marginal" && x.monthly_eur !== null && (
                            <div class="small muted">
                              {t.marginal(formatMoney(x.full_monthly_eur, "EUR"))}
                            </div>
                          )}
                          {withFee !== null && withFee !== x.monthly_eur && (
                            <div class="small muted">
                              {b.withFee(formatMoney(withFee, "EUR"), props.fee)}
                            </div>
                          )}
                        </td>
                        <td class="num">
                          <span class={savingClass(x.saving_pct)}>
                            {formatPercent(x.saving_pct, { signed: true })}
                          </span>
                          {x.notes.length > 0 && (
                            <div class="small muted">{x.notes.join(", ")}</div>
                          )}
                        </td>
                        {!view && (
                          <td>
                            {x.others.length === 0 ? (
                              <span class="muted">{t.noOthers}</span>
                            ) : (
                              <ul class="profile-verdicts small">
                                {x.others.map((v) => (
                                  <li key={v.profileName}>
                                    <span class="muted">{v.profileName}:</span> {badge(v)}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </>
  );
}
