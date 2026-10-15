#!/usr/bin/env bash
# One-time (and re-runnable) GCP setup for haggler-agent. See docs/SETUP.md.
#
#   PROJECT_ID=my-project ./infra/bootstrap.sh
#
# Idempotent: every resource is created only if a describe call says it is missing; IAM bindings
# and policies are re-applied (add-iam-policy-binding is a no-op when the binding exists). The
# script never changes your gcloud configuration: the project is passed through the environment.
#
# Creates: APIs, Artifact Registry repo (cleanup: keep 5 versions), GCS bucket (raw/ deleted after
# 90 days, raw/research/ after 30), service accounts haggler-job / haggler-scheduler /
# haggler-github, Workload Identity Federation for GitHub Actions, two placeholder Cloud Run Jobs
# (deploy-job.yml deploys the real image) and their Cloud Scheduler triggers: haggler-daily
# (prices, every day) and haggler-research (feasibility research, weekly). Then a monthly budget
# with email alerts and, unless BILLING_CAP=0, a kill switch that unlinks the project from its
# billing account once actual spend reaches the budget (docs/SETUP.md §9). Finally prints the
# GitHub repository variables.

set -euo pipefail

# --- Configuration (override via environment) ------------------------------------------------
: "${PROJECT_ID:?Set PROJECT_ID, e.g. PROJECT_ID=my-project ./infra/bootstrap.sh}"
REGION="${REGION:-europe-north2}"
# Cloud Scheduler is not offered in every region (not in europe-north1 or europe-north2); it only
# needs to call the Run API, so it can live elsewhere while the jobs stay in REGION.
SCHEDULER_REGION="${SCHEDULER_REGION:-europe-west1}"
GITHUB_REPO="${GITHUB_REPO:-glrodasz/haggler-agent}"
# Only workflows running on this branch (deploy-job, publish) may impersonate haggler-github.
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
BUCKET="${BUCKET:-${PROJECT_ID}-haggler-data}"
BUCKET_LOCATION="${BUCKET_LOCATION:-${REGION}}"
AR_REPO="${AR_REPO:-haggler}"
JOB="${JOB:-haggler-daily}"
SCHEDULER_JOB="${SCHEDULER_JOB:-${JOB}-trigger}"
SCHEDULE="${SCHEDULE:-17 4 * * *}"
# Not read from $TZ on purpose: shells often export TZ=UTC or TZ=:/etc/localtime, which would
# silently move the schedule. Override with SCHEDULE_TZ.
SCHEDULE_TZ="${SCHEDULE_TZ:-Europe/Stockholm}"
# Weekly feasibility research, on Mondays after the daily run (same time zone).
RESEARCH_JOB="${RESEARCH_JOB:-haggler-research}"
RESEARCH_SCHEDULER_JOB="${RESEARCH_SCHEDULER_JOB:-${RESEARCH_JOB}-trigger}"
RESEARCH_SCHEDULE="${RESEARCH_SCHEDULE:-23 5 * * 1}"
WIF_POOL="${WIF_POOL:-github}"
WIF_PROVIDER="${WIF_PROVIDER:-github}"
PLACEHOLDER_IMAGE="${PLACEHOLDER_IMAGE:-us-docker.pkg.dev/cloudrun/container/job:latest}"
# Monthly budget, in the billing account's currency. Empty = 5 when that currency is USD, EUR,
# GBP or CHF; any other currency needs an explicit amount (5 SEK would cut the project off).
BUDGET_AMOUNT="${BUDGET_AMOUNT:-}"
# 1 = unlink billing once actual spend reaches BUDGET_AMOUNT; 0 = email alerts only.
BILLING_CAP="${BILLING_CAP:-1}"
# Workflows and Eventarc are not offered in every region either.
BILLING_CAP_REGION="${BILLING_CAP_REGION:-${SCHEDULER_REGION}}"
BUDGET_NAME="haggler ${PROJECT_ID}"
BUDGET_TOPIC="haggler-budget"
BILLING_CAP_NAME="haggler-billing-cap"

JOB_SA_NAME="haggler-job"
SCHEDULER_SA_NAME="haggler-scheduler"
GITHUB_SA_NAME="haggler-github"
JOB_SA="${JOB_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GITHUB_SA="${GITHUB_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BILLING_SA="${BILLING_CAP_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

APIS=(
  run.googleapis.com
  artifactregistry.googleapis.com
  cloudscheduler.googleapis.com
  aiplatform.googleapis.com
  storage.googleapis.com
  iam.googleapis.com
  iamcredentials.googleapis.com
  sts.googleapis.com
  logging.googleapis.com
  cloudbuild.googleapis.com
  cloudbilling.googleapis.com
  billingbudgets.googleapis.com
  pubsub.googleapis.com
)
if [[ "${BILLING_CAP}" == 1 ]]; then
  APIS+=(workflows.googleapis.com eventarc.googleapis.com)
fi

# Scope the project to this process and never prompt (a prompt would hang a re-run).
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# --- Helpers -----------------------------------------------------------------------------------
step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}
# Problems that leave the rest of the setup usable; repeated in the final summary.
WARNINGS=()
warn() {
  printf '    WARNING: %s\n' "$*" >&2
  WARNINGS+=("$*")
}

# Quiet existence check: "exists gcloud ... describe ...".
exists() { "$@" >/dev/null 2>&1; }

# IAM is eventually consistent: a service account created seconds ago can be "not found" by the
# next binding call. Retry a few times before giving up, except on errors waiting won't fix.
retry() {
  local attempt=1 max=6 delay=10 err rc
  err="$(mktemp)"
  while :; do
    # Stream stderr (long operations print progress there) while keeping a copy to inspect.
    rc=0
    { "$@" 2>&1 1>&3 3>&- | tee "${err}" >&2; } 3>&1 || rc=$?
    ((rc == 0)) && break
    if ((attempt >= max)) || grep -q 'is not a valid location' "${err}"; then
      rm -f "${err}"
      return 1
    fi
    info "retrying in ${delay}s (${attempt}/${max})..."
    sleep "${delay}"
    attempt=$((attempt + 1))
  done
  rm -f "${err}"
}

bind_project() { # member role
  retry gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="$1" --role="$2" --condition=None --format=none
  info "project: $2 -> $1"
}

bind_bucket() { # member role
  retry gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="$1" --role="$2" --format=none
  info "gs://${BUCKET}: $2 -> $1"
}

bind_repo() { # member role
  retry gcloud artifacts repositories add-iam-policy-binding "${AR_REPO}" \
    --location="${REGION}" --member="$1" --role="$2" --format=none
  info "Artifact Registry ${AR_REPO}: $2 -> $1"
}

bind_sa() { # target_sa member role
  retry gcloud iam service-accounts add-iam-policy-binding "$1" \
    --member="$2" --role="$3" --format=none
  info "${1}: $3 -> $2"
}

ensure_sa() { # name display_name
  local email="$1@${PROJECT_ID}.iam.gserviceaccount.com"
  if exists gcloud iam service-accounts describe "${email}"; then
    info "service account ${email} already exists"
  else
    gcloud iam service-accounts create "$1" --display-name="$2" --format=none
    info "created service account ${email}"
  fi
}

# --- Preflight ---------------------------------------------------------------------------------
step "Preflight"
command -v gcloud >/dev/null 2>&1 || die "gcloud CLI not found (https://cloud.google.com/sdk)"
ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null |
  head -n1 || true)"
[[ -n "${ACCOUNT}" ]] || die "no active gcloud account; run: gcloud auth login"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')" ||
  die "cannot read project ${PROJECT_ID} (does it exist, and can ${ACCOUNT} see it?)"
[[ -n "${PROJECT_NUMBER}" ]] || die "could not resolve the project number of ${PROJECT_ID}"
info "account=${ACCOUNT} project=${PROJECT_ID} (#${PROJECT_NUMBER})"
info "region=${REGION} scheduler_region=${SCHEDULER_REGION} bucket=gs://${BUCKET}"
info "jobs=${JOB},${RESEARCH_JOB} github=${GITHUB_REPO}@${GITHUB_BRANCH}"
case "${BILLING_CAP}" in
  0 | 1) ;;
  *) die "BILLING_CAP must be 0 (alerts only) or 1 (kill switch), got '${BILLING_CAP}'" ;;
esac
if [[ -n "${BUDGET_AMOUNT}" ]]; then
  [[ "${BUDGET_AMOUNT}" =~ ^[0-9]+([.][0-9]{1,2})?$ ]] ||
    die "BUDGET_AMOUNT must be a plain amount such as 5 or 7.50 (the currency is the billing account's)"
  [[ ! "${BUDGET_AMOUNT}" =~ ^0+([.]0+)?$ ]] || die "BUDGET_AMOUNT must be greater than 0"
fi
if [[ "${BILLING_CAP}" == 1 ]]; then
  [[ -f "${SCRIPT_DIR}/billing_cap.yaml" ]] || die "missing ${SCRIPT_DIR}/billing_cap.yaml"
fi
info "budget=${BUDGET_AMOUNT:-default} billing_cap=${BILLING_CAP} (${BILLING_CAP_REGION})"
# Best effort: the call itself needs the Cloud Billing API, so only a definite "False" stops us.
BILLING="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' \
  2>/dev/null || true)"
case "${BILLING}" in
  False | false) die "billing is not enabled on ${PROJECT_ID}; Cloud Run/Scheduler/Vertex need it" ;;
esac

# --- APIs --------------------------------------------------------------------------------------
step "APIs"
ENABLED="$(gcloud services list --enabled --format='value(config.name)')"
MISSING=()
for api in "${APIS[@]}"; do
  grep -qxF "${api}" <<<"${ENABLED}" || MISSING+=("${api}")
done
if ((${#MISSING[@]})); then
  info "enabling: ${MISSING[*]}"
  gcloud services enable "${MISSING[@]}"
else
  info "all ${#APIS[@]} APIs already enabled"
fi

# --- Artifact Registry ---------------------------------------------------------------------------
step "Artifact Registry repository ${AR_REPO} (${REGION})"
if exists gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}"; then
  info "already exists"
else
  gcloud artifacts repositories create "${AR_REPO}" --location="${REGION}" \
    --repository-format=docker --description="haggler-agent images" --format=none
  info "created"
fi
# Keep the 5 most recent versions; delete the rest once they are a day old (the age guard
# avoids racing a push that is still uploading). Keep rules win over delete rules.
cat >"${TMP_DIR}/cleanup.json" <<'JSON'
[
  {
    "name": "keep-5-most-recent",
    "action": {"type": "Keep"},
    "mostRecentVersions": {"keepCount": 5}
  },
  {
    "name": "delete-older",
    "action": {"type": "Delete"},
    "condition": {"tagState": "any", "olderThan": "1d"}
  }
]
JSON
gcloud artifacts repositories set-cleanup-policies "${AR_REPO}" --location="${REGION}" \
  --policy="${TMP_DIR}/cleanup.json" --no-dry-run --format=none
info "cleanup policy: keep 5 most recent versions"

# --- GCS bucket ------------------------------------------------------------------------------------
step "Bucket gs://${BUCKET} (${BUCKET_LOCATION})"
if exists gcloud storage buckets describe "gs://${BUCKET}"; then
  info "already exists"
else
  gcloud storage buckets create "gs://${BUCKET}" --location="${BUCKET_LOCATION}" \
    --default-storage-class=STANDARD --uniform-bucket-level-access \
    --public-access-prevention
  info "created"
fi
# Raw pages are only kept for debugging/replays; snapshots and latest/ are kept forever. Pages
# fetched by the research job (third-party terms and press) are kept for 30 days only.
cat >"${TMP_DIR}/lifecycle.json" <<'JSON'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 90, "matchesPrefix": ["raw/"]}
    },
    {
      "action": {"type": "Delete"},
      "condition": {"age": 30, "matchesPrefix": ["raw/research/"]}
    }
  ]
}
JSON
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file="${TMP_DIR}/lifecycle.json" \
  --uniform-bucket-level-access --public-access-prevention
info "lifecycle: delete raw/ after 90 days, raw/research/ after 30"
info "uniform bucket-level access + public access prevention"

# --- Service accounts --------------------------------------------------------------------------
step "Service accounts"
ensure_sa "${JOB_SA_NAME}" "haggler daily job (Cloud Run)"
ensure_sa "${SCHEDULER_SA_NAME}" "haggler Cloud Scheduler trigger"
ensure_sa "${GITHUB_SA_NAME}" "haggler GitHub Actions (deploy + publish)"

step "IAM: ${JOB_SA_NAME}"
bind_project "serviceAccount:${JOB_SA}" roles/aiplatform.user
bind_project "serviceAccount:${JOB_SA}" roles/logging.logWriter
bind_bucket "serviceAccount:${JOB_SA}" roles/storage.objectAdmin

step "IAM: ${GITHUB_SA_NAME}"
bind_repo "serviceAccount:${GITHUB_SA}" roles/artifactregistry.writer
bind_project "serviceAccount:${GITHUB_SA}" roles/run.developer
# Needed to deploy a job that runs as haggler-job.
bind_sa "${JOB_SA}" "serviceAccount:${GITHUB_SA}" roles/iam.serviceAccountUser
bind_bucket "serviceAccount:${GITHUB_SA}" roles/storage.objectViewer

# --- Workload Identity Federation (GitHub OIDC) ------------------------------------------------
step "Workload Identity Federation: pool ${WIF_POOL}, provider ${WIF_PROVIDER}"
POOL_STATE="$(gcloud iam workload-identity-pools describe "${WIF_POOL}" --location=global \
  --format='value(state)' 2>/dev/null || true)"
if [[ -z "${POOL_STATE}" ]]; then
  gcloud iam workload-identity-pools create "${WIF_POOL}" --location=global \
    --display-name="GitHub Actions" --format=none
  info "created pool"
elif [[ "${POOL_STATE}" == "DELETED" ]]; then
  # Deleted pools linger for 30 days and block re-creation under the same id.
  gcloud iam workload-identity-pools undelete "${WIF_POOL}" --location=global --format=none
  info "undeleted pool"
else
  info "pool already exists (${POOL_STATE})"
fi

WIF_MAPPING="google.subject=assertion.sub,attribute.repository=assertion.repository"
WIF_MAPPING+=",attribute.ref=assertion.ref"
# Repository AND branch: a workflow pushed to any other branch (claude/**, a same-repo PR) gets no
# token, so it cannot deploy an image that runs as haggler-job. The probe never uses GCP.
WIF_CONDITION="assertion.repository=='${GITHUB_REPO}' && assertion.ref=='refs/heads/${GITHUB_BRANCH}'"
PROVIDER_STATE="$(gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER}" \
  --workload-identity-pool="${WIF_POOL}" --location=global --format='value(state)' \
  2>/dev/null || true)"
if [[ -z "${PROVIDER_STATE}" ]]; then
  gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER}" \
    --workload-identity-pool="${WIF_POOL}" --location=global \
    --display-name="GitHub OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="${WIF_MAPPING}" \
    --attribute-condition="${WIF_CONDITION}" --format=none
  info "created OIDC provider"
else
  if [[ "${PROVIDER_STATE}" == "DELETED" ]]; then
    gcloud iam workload-identity-pools providers undelete "${WIF_PROVIDER}" \
      --workload-identity-pool="${WIF_POOL}" --location=global --format=none
    info "undeleted OIDC provider"
  fi
  # Re-apply mapping and condition so the trust stays pinned to ${GITHUB_REPO}@${GITHUB_BRANCH}.
  gcloud iam workload-identity-pools providers update-oidc "${WIF_PROVIDER}" \
    --workload-identity-pool="${WIF_POOL}" --location=global \
    --attribute-mapping="${WIF_MAPPING}" \
    --attribute-condition="${WIF_CONDITION}" --format=none
  info "OIDC provider already exists; mapping/condition re-applied"
fi

WIF_PROVIDER_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"
WIF_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPO}"
bind_sa "${GITHUB_SA}" "${WIF_PRINCIPAL}" roles/iam.workloadIdentityUser

# --- Cloud Run Jobs (placeholders) and their Cloud Scheduler triggers ---------------------------
ensure_job_and_trigger() { # job scheduler_job schedule description
  local job="$1" scheduler_job="$2" schedule="$3" description="$4"

  step "Cloud Run Job ${job} (${REGION})"
  if exists gcloud run jobs describe "${job}" --region="${REGION}"; then
    info "already exists (not touched; deploy-job.yml manages its image and settings)"
  else
    # A public sample image, only so the scheduler has a target before the first real deploy.
    retry gcloud run jobs create "${job}" --region="${REGION}" --image="${PLACEHOLDER_IMAGE}" \
      --service-account="${JOB_SA}" --tasks=1 --max-retries=0 --task-timeout=10m \
      --labels=managed-by=haggler-bootstrap --format=none
    info "created placeholder job (image ${PLACEHOLDER_IMAGE})"
  fi

  step "IAM: ${SCHEDULER_SA_NAME} on ${job}"
  retry gcloud run jobs add-iam-policy-binding "${job}" --region="${REGION}" \
    --member="serviceAccount:${SCHEDULER_SA}" --role=roles/run.invoker --format=none
  info "job ${job}: roles/run.invoker -> serviceAccount:${SCHEDULER_SA}"

  step "Cloud Scheduler ${scheduler_job} (${SCHEDULER_REGION}): '${schedule}' ${SCHEDULE_TZ}"
  local run_uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}"
  run_uri+="/jobs/${job}:run"
  local scheduler_args=(
    --location="${SCHEDULER_REGION}"
    --schedule="${schedule}"
    --time-zone="${SCHEDULE_TZ}"
    --uri="${run_uri}"
    --http-method=POST
    --oauth-service-account-email="${SCHEDULER_SA}"
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
    --attempt-deadline=180s
    --max-retry-attempts=2
    --min-backoff=120s
    --description="${description}"
    --format=none
  )
  if exists gcloud scheduler jobs describe "${scheduler_job}" --location="${SCHEDULER_REGION}"; then
    gcloud scheduler jobs update http "${scheduler_job}" "${scheduler_args[@]}"
    info "already exists; schedule/target re-applied"
  else
    retry gcloud scheduler jobs create http "${scheduler_job}" "${scheduler_args[@]}" \
      || die "Cloud Scheduler failed in ${SCHEDULER_REGION}; re-run with another SCHEDULER_REGION"
    info "created"
  fi
}

ensure_job_and_trigger "${JOB}" "${SCHEDULER_JOB}" "${SCHEDULE}" \
  "Runs Cloud Run Job ${JOB} (haggler daily prices)"
ensure_job_and_trigger "${RESEARCH_JOB}" "${RESEARCH_SCHEDULER_JOB}" "${RESEARCH_SCHEDULE}" \
  "Runs Cloud Run Job ${RESEARCH_JOB} (haggler weekly feasibility research)"

# --- Budget and billing kill switch (docs/SETUP.md §9) ------------------------------------------
# A budget alone only sends emails. With BILLING_CAP=1 its Pub/Sub notifications also run the
# workflow infra/billing_cap.yaml, which unlinks the project from its billing account once actual
# spend reaches the budget. Billing data lags by hours, so spend can overshoot a little first.
BUDGET_TOPIC_PATH="projects/${PROJECT_ID}/topics/${BUDGET_TOPIC}"
BUDGET_SUMMARY="none (see the warnings below)"
BUDGET_OK=0

if [[ "${BILLING_CAP}" == 1 ]]; then
  step "Pub/Sub topic ${BUDGET_TOPIC} (budget notifications)"
  if exists gcloud pubsub topics describe "${BUDGET_TOPIC}"; then
    info "already exists"
  else
    gcloud pubsub topics create "${BUDGET_TOPIC}" --format=none
    info "created"
  fi
  # Cloud Billing publishes budget notifications as this Google-managed account.
  retry gcloud pubsub topics add-iam-policy-binding "${BUDGET_TOPIC}" \
    --member=serviceAccount:billing-budget-alert@system.gserviceaccount.com \
    --role=roles/pubsub.publisher --format=none
  info "topic ${BUDGET_TOPIC}: roles/pubsub.publisher -> Cloud Billing budget alerts"

  step "Service account ${BILLING_CAP_NAME}"
  ensure_sa "${BILLING_CAP_NAME}" "haggler billing kill switch (Workflows)"
  # Runs the workflow (Eventarc) and unlinks the billing account; it reads no project data.
  bind_project "serviceAccount:${BILLING_SA}" roles/billing.projectManager
  bind_project "serviceAccount:${BILLING_SA}" roles/workflows.invoker
  bind_project "serviceAccount:${BILLING_SA}" roles/eventarc.eventReceiver
  bind_project "serviceAccount:${BILLING_SA}" roles/logging.logWriter
  # Eventarc pushes to the workflow with an OIDC token of BILLING_SA minted by the Pub/Sub agent.
  # Projects whose agent predates April 2021 need this grant; elsewhere it is redundant.
  PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
  if retry gcloud iam service-accounts add-iam-policy-binding "${BILLING_SA}" \
    --member="serviceAccount:${PUBSUB_AGENT}" --role=roles/iam.serviceAccountTokenCreator \
    --format=none; then
    info "${BILLING_SA}: roles/iam.serviceAccountTokenCreator -> ${PUBSUB_AGENT}"
  else
    warn "could not grant ${PUBSUB_AGENT} roles/iam.serviceAccountTokenCreator on ${BILLING_SA};" \
      "only projects older than April 2021 need it"
  fi

  step "Workflow ${BILLING_CAP_NAME} (${BILLING_CAP_REGION})"
  retry gcloud workflows deploy "${BILLING_CAP_NAME}" --location="${BILLING_CAP_REGION}" \
    --source="${SCRIPT_DIR}/billing_cap.yaml" --service-account="${BILLING_SA}" \
    --description="Unlinks billing once the haggler budget is spent (docs/SETUP.md §9)" \
    --labels=managed-by=haggler-bootstrap --format=none
  info "deployed from infra/billing_cap.yaml"

  step "Eventarc trigger ${BILLING_CAP_NAME}: topic ${BUDGET_TOPIC} -> workflow"
  if exists gcloud eventarc triggers describe "${BILLING_CAP_NAME}" \
    --location="${BILLING_CAP_REGION}"; then
    info "already exists"
  else
    retry gcloud eventarc triggers create "${BILLING_CAP_NAME}" \
      --location="${BILLING_CAP_REGION}" \
      --destination-workflow="${BILLING_CAP_NAME}" \
      --destination-workflow-location="${BILLING_CAP_REGION}" \
      --event-filters="type=google.cloud.pubsub.topic.v1.messagePublished" \
      --transport-topic="${BUDGET_TOPIC_PATH}" \
      --service-account="${BILLING_SA}" --format=none
    info "created"
  fi
fi

# Budgets live on the billing account, not the project: creating one needs Billing Account
# Administrator or Billing Account Costs Manager there, which a project Owner may lack. Without
# it the rest of the setup stands and the warning says how to finish by hand.
ensure_budget() {
  local account currency amount listing budget_id rule
  account="$(gcloud billing projects describe "${PROJECT_ID}" \
    --format='value(billingAccountName)' 2>/dev/null || true)"
  account="${account#billingAccounts/}"
  if [[ -z "${account}" ]]; then
    warn "could not read the billing account of ${PROJECT_ID}; no budget created (docs/SETUP.md §9)"
    return 0
  fi
  currency="$(gcloud billing accounts describe "${account}" --format='value(currencyCode)' \
    2>/dev/null || true)"
  amount="${BUDGET_AMOUNT}"
  if [[ -z "${amount}" ]]; then
    case "${currency}" in
      USD | EUR | GBP | CHF) amount=5 ;;
      *)
        warn "billing account ${account} bills in '${currency:-unknown currency}': re-run with" \
          "BUDGET_AMOUNT in that currency; no budget created"
        return 0
        ;;
    esac
  fi

  local thresholds=(percent=0.5 percent=0.9 percent=1.0 percent=1.0,basis=forecasted-spend)
  local args=(
    --billing-account="${account}"
    --billing-project="${PROJECT_ID}"
    --budget-amount="${amount}"
    --calendar-period=month
    --filter-projects="projects/${PROJECT_ID}"
    --format=none
  )
  if [[ "${BILLING_CAP}" == 1 ]]; then
    args+=(--notifications-rule-pubsub-topic="${BUDGET_TOPIC_PATH}")
  fi

  if ! listing="$(gcloud billing budgets list --billing-account="${account}" \
    --billing-project="${PROJECT_ID}" --format='value(name,displayName)')"; then
    warn "cannot list the budgets of billing account ${account} (needs Billing Account" \
      "Administrator or Costs Manager); create '${BUDGET_NAME}' by hand (docs/SETUP.md §9)"
    return 0
  fi
  budget_id="$(awk -F '\t' -v name="${BUDGET_NAME}" '$2 == name { print $1; exit }' \
    <<<"${listing}")"

  if [[ -n "${budget_id}" ]]; then
    args+=(--clear-threshold-rules)
    for rule in "${thresholds[@]}"; do args+=(--add-threshold-rule="${rule}"); done
    if ! gcloud billing budgets update "${budget_id##*/}" "${args[@]}"; then
      warn "could not update budget '${BUDGET_NAME}' (${budget_id})"
      return 0
    fi
    info "already exists; amount, scope, thresholds and topic re-applied"
  else
    for rule in "${thresholds[@]}"; do args+=(--threshold-rule="${rule}"); done
    if ! gcloud billing budgets create --display-name="${BUDGET_NAME}" "${args[@]}"; then
      warn "could not create budget '${BUDGET_NAME}' on billing account ${account} (needs" \
        "Billing Account Administrator or Costs Manager; docs/SETUP.md §9)"
      return 0
    fi
    info "created"
  fi
  BUDGET_SUMMARY="${amount} ${currency:-(billing account currency)}/month on billing account"
  BUDGET_SUMMARY+=" ${account}; emails at 50/90/100 % and at a 100 % forecast"
  BUDGET_OK=1
}

step "Budget '${BUDGET_NAME}'"
ensure_budget

# --- Output --------------------------------------------------------------------------------------
step "Done. Set these GitHub repository VARIABLES (Settings > Secrets and variables > Actions >"
info "Variables), not secrets:"
cat <<EOF

  GCP_PROJECT_ID   = ${PROJECT_ID}
  GCP_REGION       = ${REGION}
  GCP_WIF_PROVIDER = ${WIF_PROVIDER_NAME}
  GCP_GITHUB_SA    = ${GITHUB_SA}
  GCP_JOB_SA       = ${JOB_SA}
  GCS_BUCKET       = ${BUCKET}
  AR_REPOSITORY    = ${AR_REPO}
  GCP_JOB_NAME     = ${JOB}    (optional while it is haggler-daily)
  GCP_RESEARCH_JOB_NAME = ${RESEARCH_JOB}    (optional while it is haggler-research)

Or with the GitHub CLI:

  gh variable set GCP_PROJECT_ID   --repo ${GITHUB_REPO} --body '${PROJECT_ID}'
  gh variable set GCP_REGION       --repo ${GITHUB_REPO} --body '${REGION}'
  gh variable set GCP_WIF_PROVIDER --repo ${GITHUB_REPO} --body '${WIF_PROVIDER_NAME}'
  gh variable set GCP_GITHUB_SA    --repo ${GITHUB_REPO} --body '${GITHUB_SA}'
  gh variable set GCP_JOB_SA       --repo ${GITHUB_REPO} --body '${JOB_SA}'
  gh variable set GCS_BUCKET       --repo ${GITHUB_REPO} --body '${BUCKET}'
  gh variable set AR_REPOSITORY    --repo ${GITHUB_REPO} --body '${AR_REPO}'
  gh variable set GCP_JOB_NAME     --repo ${GITHUB_REPO} --body '${JOB}'
  gh variable set GCP_RESEARCH_JOB_NAME --repo ${GITHUB_REPO} --body '${RESEARCH_JOB}'

Next: run the "deploy-job" workflow from ${GITHUB_BRANCH} (or merge to it), then
  gcloud run jobs execute ${JOB} --region ${REGION} --wait
  gcloud run jobs execute ${RESEARCH_JOB} --region ${REGION} --wait   # after the daily run

Budget: ${BUDGET_SUMMARY}
EOF
if [[ "${BILLING_CAP}" == 1 && "${BUDGET_OK}" == 1 ]]; then
  info "Kill switch: ON. Once actual spend reaches the budget, workflow ${BILLING_CAP_NAME}"
  info "(${BILLING_CAP_REGION}) unlinks billing and every paid service stops (docs/SETUP.md §9)."
elif [[ "${BILLING_CAP}" == 1 ]]; then
  info "Kill switch: deployed but IDLE until a budget notifies topic ${BUDGET_TOPIC_PATH}"
  info "(docs/SETUP.md §9)."
else
  info "Kill switch: OFF (BILLING_CAP=0): the budget only sends emails."
fi
if ((${#WARNINGS[@]})); then
  printf '\nWarnings:\n' >&2
  for warning in "${WARNINGS[@]}"; do
    printf '  - %s\n' "${warning}" >&2
  done
fi
