# haggler-agent

Agent built with **Google ADK** (`google-adk` 2.x) that collects consumer subscription prices
**per country** from official sources every day, validates them and feeds a static site that
finds the cheapest *and feasible* combination for someone living in Sweden.

> Status: **Phase 3** — daily prices, the site, a weekly feasibility research agent with
> configurable profiles, and bundle monitoring: bundle values are still hand-curated in
> `data/curated/bundles.yaml`, and an LLM-free watcher confirms them on the official pages and
> PDFs. The first-wave data is still missing (the anchors, after the first deployment). See
> [the roadmap](#roadmap).

## What it does

| Step | How |
|---|---|
| Sources | Official pages addressable by country (iCloud+ support, apple.com/{country}, spotify.com/{country}, Netflix help, Microsoft Store catalog) and, for services that only show the price based on IP (YouTube Premium, Google One, HBO Max) or whose robots.txt disallows bot access (Disney+), each country's **App Store** in-app purchase listing. Secondary Netflix dataset (tompec, CC BY 4.0) for cross-checking. |
| Extraction | Deterministic parsers first. An LLM agent (Gemini on Vertex AI) only acts as a fallback when a parser fails, and may only quote literal text from the page; the code verifies that the price and the evidence exist. |
| Normalization | ECB + central bank exchange rates via Frankfurter (COP=BanRep, ARS=BCRA…), monthly equivalent, VAT included yes/no. |
| Validation | Deterministic rules: currency vs. country, plausible ranges, changes >30 % quarantined until confirmed, plan ordering, carrying values forward when a source fails. |
| Publishing | Full snapshots in GCS; `data/latest/` in the repo (every price change = one line in the git diff) and `data/changes/*.jsonl`; site on GitHub Pages. |

**Feasibility** = three separate axes (legality / terms of service / practicality); the label
is the worst of the three. The site reports risks and assumptions; it is **not legal advice** and
does not recommend declaring false information.

### Feasibility research (Phase 2)

| Step | How |
|---|---|
| What it researches | Each service's country rules, what it takes to sign up for a country's offer (only pairs saving ≥ 1 €/month and ≥ 15 %) and payment methods, using a closed vocabulary of facts (`src/haggler/research/fields.py`). |
| Discover | Three ADK `single_turn` agents with Gemini + Google Search and structured output; caps on calls, on queries per run and per month. |
| Verify | A fact only counts if our fetch (robots.txt, no bypassing blocks) finds the **literal quote** on the cited page or on the subject's known official pages. Source class: official / press / forum. |
| Evaluate | Pure function (`research/evaluate.py`) that derives the three axes **per profile** (`data/curated/profiles.yaml`): card country, presence, documents, address. Curated data wins; upgrades require an official source; a route that requires declaring false information is *Not feasible*. |
| Review | Findings stay *Pending review* until `haggler research accept`; the comparator excludes them unless the checkbox is enabled. |

### Bundles (Phase 3)

| Step | How |
|---|---|
| Values | `data/curated/bundles.yaml` is the single source (price, intro, base plan, components, dates, requirements) and no job writes to it. Each value is confirmed by an **anchor**: a literal quote copied from our own extraction of the official page. |
| Watch | Inside the weekly job, with no LLM or queries: reads the bundles' pages and versioned PDF lists in `auto` mode (robots.txt, no bypassing walls, official domains of the bundle's country), checks the anchors, notes price hints and detects new editions. |
| Propose | An agent with Google Search looks up values and requirements for up to 2 bundles per week, after all the Phase 2 work. A value only counts if the quote is on an official page of the country and states that value (value‑quote check). |
| Review | `haggler research review --subject <id>` prints a YAML snippet per proposal; it is accepted by pasting it into a PR (`validate-catalog` checks the anchor offline) and the next run verifies it online. Feasibility is accepted by id, as in Phase 2. |
| Display | `/paquetes`: derived status (verified, needs review, manual verification, unverified, expired), monthly price with and without the 2 % currency exchange fee, savings excluding the intro price, and verdict per profile. |

## Architecture

```
Cloud Scheduler ──► Cloud Run Job (europe-north2) ──► GCS (snapshots + latest/)
                     haggler daily (ADK Workflow)            │
                                                             ▼
                    GitHub Actions publish.yml ──► commit data/ ──► GitHub Pages (web/)

Cloud Scheduler (Mondays) ──► Cloud Run Job haggler-research ──► GCS research/latest/
                               haggler research run (ADK Workflow)  (sync → data/research/)
```

ADK workflow (linear chain; each node is a no-op if it receives no work):

```
plan_tasks → fetch_and_parse (parallel, per-item retries) → llm_extract → fx_normalize
          → validate → adjudicate (LLM second opinion) → persist
```

Research workflow:

```
plan_research → watch_bundles (bundle watcher, no LLM) → research (agents with Google Search)
              → verify_evidence (our fetch) → persist
```

## Layout

```
src/haggler/        Python package (models, sources, fetch, fx, validation, ADK pipeline, CLI)
agents/haggler_agent/  shim for `adk web agents` / `adk run agents/haggler_agent`
agents/haggler_research/  research workflow shim for `adk web agents`
catalog/            services, plans, sources and countries (editable YAML)
data/latest/        latest published prices (managed by the bot)
data/changes/       price change history (append-only)
data/curated/       feasibility, profiles, reviews, bundles, payment instruments
data/research/      facts verified by the agent and bundle watcher state (bot)
web/                static site (Astro + Preact)
infra/              GCP bootstrap
.github/workflows/  CI, probe, job deployment, publishing
```

## Local development

```bash
uv sync
uv run pytest
uv run haggler validate-catalog

# Offline run with recorded pages (no network or LLM)
uv run haggler daily --replay tests/workflow/fixtures/replay --store local --no-llm

# Real run (needs internet access; LLM optional)
cp .env.example .env   # uncomment ONE option: A) GOOGLE_API_KEY (AI Studio) or B) Vertex via ADC
uv run haggler daily --store local --services netflix,spotify --countries SE,CO

# Site data and build
uv run haggler sync --from-dir .haggler --data-dir data
uv run haggler site-data --data-dir data --out web/src/data
npm --prefix web ci && npm --prefix web run build

# Feasibility research (needs an LLM with Google Search to research)
uv run haggler research run --store local --dry-run --no-llm   # plan only
uv run haggler research run --store local --services netflix --pairs spotify:CO
uv run haggler research review                                   # pending items and impact
uv run haggler research accept netflix --by your-username

# Bundles (docs/SETUP.md §14)
uv run haggler research run --store local --watch-only           # watcher only: 0 queries
uv run haggler research review --bundles                         # proposals and YAML snippets

# ADK development UI
PYTHONPATH=agents uv run adk web agents
```

`haggler` loads the `.env` from the working directory on startup (`uv run` does not read it on its
own) without overriding variables already set in the environment; `adk web agents` loads the `.env`
from the repo root. In `.env.example` both LLM options are commented out: with neither, the LLM
fallback is off; with both, google-genai ignores `GOOGLE_API_KEY` and calls Vertex AI.

Deployment to GCP and GitHub Pages: see [docs/SETUP.md](docs/SETUP.md).

## Roadmap

1. **Phase 1**: daily per-country prices + minimal site.
2. **Phase 2**: weekly feasibility research agent (facts verified with a literal quote,
   deterministic per-profile evaluator, human review by evidence hash).
3. **Phase 3 (current)**: bundle monitoring (Meli+, Telia, Apple One…). Done: canonical YAML with
   anchors, weekly LLM-free watcher of official pages and PDFs, a propose-only agent, derived
   status, honest savings and per-profile verdict on `/paquetes`. Pending: the first-wave data
   (anchors copied from the probe after deployment) and the second wave (Telia Play).
4. **Phase 4**: in-browser combination optimizer (one region per Apple/Google account,
   maximum number of foreign payment instruments, bundles as virtual SKUs).

## License

Apache-2.0. Prices belong to their respective providers; the Netflix dataset from
[tompec/netflix-prices](https://github.com/tompec/netflix-prices) is used under CC BY 4.0.
