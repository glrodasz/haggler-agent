# Puesta en marcha: GCP + GitHub Actions + GitHub Pages

Guía paso a paso para dejar corriendo el agente diario en Google Cloud y publicar el sitio en
GitHub Pages. Todo lo que crea es reproducible: `infra/bootstrap.sh` es idempotente y los
workflows de `.github/workflows/` hacen el resto.

```
Cloud Scheduler (europe-north1, 04:17 Europe/Stockholm)
   │  POST run.googleapis.com/v2/…/jobs/haggler-daily:run   (cuenta haggler-scheduler)
   ▼
Cloud Run Job haggler-daily (europe-north2)  ──►  gs://PROYECTO-haggler-data/
   imagen de Artifact Registry                      raw/  runs/  latest/
   cuenta haggler-job + Gemini en Vertex AI               │
                                                          ▼
GitHub Actions publish.yml (05:15 UTC, WIF, cuenta haggler-github)
   haggler sync ─► commit de data/ ─► haggler site-data ─► build Astro ─► GitHub Pages
```

| Workflow | Cuándo corre | Qué hace |
|---|---|---|
| `ci.yml` | cada push y pull request | ruff, pytest, `validate-catalog`; sitio: `astro check`, vitest, build con datos de muestra; contrato: `haggler daily` sobre los fixtures de replay → `sync` → `site-data` → build del sitio, y otro build con un `data/` vacío (primer despliegue) |
| `probe.yml` | push a `claude/**` que toque fuentes/fetch/fx/catálogo, o manual | descarga las páginas reales y reporta qué extrae cada adapter (no usa GCP) |
| `deploy-job.yml` | push a `main` que toque código/catálogo/Dockerfile, o manual desde `main` | build + push de la imagen y `gcloud run jobs deploy haggler-daily` |
| `publish.yml` | todos los días 05:15 UTC, o manual | `haggler sync` desde GCS, commit de `data/`, build y despliegue en Pages |

## 1. Requisitos

- Un proyecto de Google Cloud **con facturación activa** (Cloud Run, Scheduler y Vertex AI la
  exigen aunque el uso quede dentro de la capa gratuita).
- Permisos de *Owner* sobre el proyecto (o equivalentes: crear cuentas de servicio, asignar IAM,
  habilitar APIs).
- `gcloud` autenticado (`gcloud auth login`). Lo más simple es **Cloud Shell**, que ya trae
  `gcloud`, `bash` y `gh`. El script evita sintaxis de bash 4, así que también debería correr con
  el `bash` 3.2 de macOS.
- Ser administrador del repositorio `glrodasz/haggler-agent` en GitHub (variables y Pages).
- Opcional: [GitHub CLI](https://cli.github.com/) (`gh`) para fijar variables desde la terminal.

## 2. Bootstrap de GCP

```bash
git clone https://github.com/glrodasz/haggler-agent.git && cd haggler-agent
PROJECT_ID=mi-proyecto ./infra/bootstrap.sh
```

Se puede volver a correr cuantas veces haga falta: cada recurso se crea solo si `describe` dice
que no existe, y los permisos/políticas se vuelven a aplicar sin duplicarse. El script no cambia
tu configuración de `gcloud` (el proyecto se pasa por variable de entorno).

Variables configurables (todas opcionales salvo `PROJECT_ID`):

| Variable | Valor por defecto | Para qué |
|---|---|---|
| `PROJECT_ID` | — (obligatoria) | proyecto de GCP |
| `REGION` | `europe-north2` (Estocolmo) | Cloud Run Job, Artifact Registry |
| `SCHEDULER_REGION` | `europe-north1` (Finlandia) | Cloud Scheduler no está disponible en todas las regiones; solo llama a la API de Run |
| `BUCKET` | `$PROJECT_ID-haggler-data` | bucket de datos |
| `BUCKET_LOCATION` | `$REGION` | ubicación del bucket |
| `AR_REPO` | `haggler` | repositorio Docker de Artifact Registry |
| `JOB` | `haggler-daily` | nombre del Cloud Run Job (si lo cambias, crea también la variable `GCP_JOB_NAME`, paso 3) |
| `SCHEDULER_JOB` | `$JOB-trigger` | nombre del job de Cloud Scheduler |
| `SCHEDULE` | `17 4 * * *` | cron del run diario |
| `SCHEDULE_TZ` | `Europe/Stockholm` | zona horaria del cron (no se lee `$TZ` a propósito: muchas shells exportan `TZ=UTC`) |
| `GITHUB_REPO` | `glrodasz/haggler-agent` | único repositorio autorizado por Workload Identity Federation |
| `GITHUB_BRANCH` | `main` | única rama autorizada por Workload Identity Federation (la rama por defecto) |

Por ejemplo, si Cloud Scheduler sí está disponible en Estocolmo:
`PROJECT_ID=mi-proyecto SCHEDULER_REGION=europe-north2 ./infra/bootstrap.sh`.

Qué crea:

| Recurso | Detalle |
|---|---|
| APIs | run, artifactregistry, cloudscheduler, aiplatform, storage, iam, iamcredentials, sts, logging, cloudbuild |
| Artifact Registry `haggler` | Docker, política de limpieza: conserva las 5 versiones más recientes |
| Bucket `gs://$PROJECT_ID-haggler-data` | acceso uniforme, *public access prevention*, borra `raw/` a los 90 días |
| `haggler-job@` | cuenta del job: `roles/aiplatform.user` y `roles/logging.logWriter` (proyecto), `roles/storage.objectAdmin` (bucket) |
| `haggler-scheduler@` | `roles/run.invoker` sobre el job `haggler-daily` |
| `haggler-github@` | `roles/artifactregistry.writer` (repo), `roles/run.developer` (proyecto), `roles/iam.serviceAccountUser` sobre `haggler-job`, `roles/storage.objectViewer` (bucket) |
| Workload Identity Federation | pool `github` + proveedor OIDC `github` (`token.actions.githubusercontent.com`), condición `assertion.repository=='glrodasz/haggler-agent' && assertion.ref=='refs/heads/main'`; `roles/iam.workloadIdentityUser` de `haggler-github` para ese repositorio |
| Cloud Run Job `haggler-daily` | **marcador de posición** con una imagen pública de ejemplo, solo si no existe (para que el Scheduler tenga destino); `deploy-job.yml` despliega la imagen real |
| Cloud Scheduler `haggler-daily-trigger` | `POST https://run.googleapis.com/v2/projects/…/locations/europe-north2/jobs/haggler-daily:run` con token OAuth de `haggler-scheduler`, 04:17 hora de Estocolmo |

No se crean claves de cuentas de servicio: GitHub Actions se autentica con tokens OIDC de corta
duración (WIF), y el job usa la identidad de su cuenta de servicio.

Al final el script imprime las variables para GitHub (siguiente paso).

## 3. Variables del repositorio en GitHub

En **Settings → Secrets and variables → Actions → pestaña Variables → New repository variable**
crea estas siete (son *variables*, no secretos: ninguna es una credencial):

| Variable | Ejemplo |
|---|---|
| `GCP_PROJECT_ID` | `mi-proyecto` |
| `GCP_REGION` | `europe-north2` |
| `GCP_WIF_PROVIDER` | `projects/123456789012/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_GITHUB_SA` | `haggler-github@mi-proyecto.iam.gserviceaccount.com` |
| `GCP_JOB_SA` | `haggler-job@mi-proyecto.iam.gserviceaccount.com` |
| `GCS_BUCKET` | `mi-proyecto-haggler-data` (sin `gs://`) |
| `AR_REPOSITORY` | `haggler` |

O con `gh` (el bootstrap imprime los comandos ya rellenados):

```bash
gh variable set GCP_PROJECT_ID --repo glrodasz/haggler-agent --body 'mi-proyecto'
gh variable set GCP_REGION --repo glrodasz/haggler-agent --body 'europe-north2'
# … y así con las demás
gh variable list --repo glrodasz/haggler-agent
```

Opcionales:

| Nombre | Tipo | Para qué |
|---|---|---|
| `GCP_JOB_NAME` | variable | nombre del Cloud Run Job si el bootstrap se corrió con otro `JOB` (por defecto `haggler-daily`); `deploy-job.yml` despliega ese job, el mismo al que apunta el Scheduler |
| `HAGGLER_EXTRACT_MODEL` | variable | modelo Gemini del fallback LLM si el de por defecto (`gemini-3.1-flash-lite`) no está disponible en Vertex; `deploy-job.yml` lo pasa al job |
| `GEMINI_API_KEY` | **secreto** | clave de Google AI Studio, solo para `probe.yml` en `mode=full` (sin ella el probe corre sin LLM) |

Mientras falten variables, `deploy-job.yml` y el paso de sync de `publish.yml` no fallan: dejan un
aviso (*notice*) en el run y se saltan los pasos de GCP. Lo mismo ocurre si se lanzan desde una
rama que no es `main`: Workload Identity Federation solo emite credenciales para la rama por
defecto.

## 4. Habilitar GitHub Pages

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. Eso crea el entorno `github-pages`, que por defecto solo acepta despliegues desde la rama por
   defecto. No hace falta tocarlo.
3. El sitio queda en `https://glrodasz.github.io/haggler-agent/`. Con dominio propio
   (Settings → Pages → Custom domain) el workflow detecta solo la nueva URL base
   (`actions/configure-pages`), no hay que cambiar nada en `web/astro.config.mjs`.

## 5. Mergear a `main`

Los workflows programados (`schedule`) **solo corren desde la rama por defecto**, y
`deploy-job.yml` se dispara con push a `main`. Mientras el trabajo esté en una rama `claude/…`
solo corren `ci.yml` y `probe.yml`.

1. Abrir el pull request de la rama de trabajo hacia `main` y esperar el CI en verde.
2. Mergear. El push a `main` dispara `deploy-job.yml` (si cambió código, catálogo o Dockerfile).
3. Si el deploy no se disparó (por ejemplo porque las variables se crearon después del merge),
   correrlo a mano: **Actions → deploy-job → Run workflow** (rama `main`), o
   `gh workflow run deploy-job.yml --ref main`.

Comprobar que el job ya usa la imagen real:

```bash
gcloud run jobs describe haggler-daily --region europe-north2 \
  --format='value(spec.template.spec.template.spec.containers[0].image)'
```

> `publish.yml` y `deploy-job.yml` también se pueden lanzar a mano desde otra rama, pero
> Workload Identity Federation solo confía en `main`: `deploy-job.yml` deja un aviso y no
> despliega, y `publish.yml` construye el sitio con el `data/` ya versionado en esa rama, sin
> sync, commit ni despliegue.

## 6. Primera ejecución manual

```bash
gcloud run jobs execute haggler-daily --region europe-north2 --wait
```

Tarda unos minutos (la etiqueta es 1 petición cada 3 s por host). Para una prueba más corta se
pueden reemplazar los argumentos del contenedor (el *entrypoint* es `haggler`; el `CMD` por
defecto es `daily --store gcs`):

```bash
gcloud run jobs execute haggler-daily --region europe-north2 --wait \
  --args='^;^daily;--store;gcs;--services;netflix,spotify;--countries;SE,CO'
```

El prefijo `^;^` cambia el separador de `--args` a `;` (por defecto es la coma, que partiría
`netflix,spotify` en dos argumentos; ver `gcloud topic escaping`). Ejecutar con argumentos
distintos requiere el permiso `run.jobs.runWithOverrides` (lo tiene *Owner* o
`roles/run.developer`).

Verificar lo que escribió:

```bash
gcloud storage ls gs://mi-proyecto-haggler-data/latest/
gcloud storage cat gs://mi-proyecto-haggler-data/latest/run.json
```

`latest/run.json` se escribe **al final** y lista el SHA-256 de cada archivo de `latest/`; si no
está, el run no terminó.

**Código de salida y reintentos.** `haggler daily` sale con 0 en cuanto guardó `latest/`, aunque
haya sido un mal día (pocas tareas OK: se ve en `run.json` y `coverage.json`, y en el sitio como
cobertura); solo sale con error si no llegó a guardar nada. El job se despliega con
`--max-retries 1`, así que Cloud Run solo reintenta una tarea que se cayó antes de guardar, lo que
es seguro. Un reintento después de guardar validaría el mismo día contra su propia salida: daría
por confirmados en la misma noche los cambios grandes que estaban en revisión y contaría dos veces
los runs de *carry-forward*. Por la misma razón, evita relanzar a mano un run completo el mismo
día salvo que el anterior haya fallado.

Probar también el camino del Scheduler (dispara el job como lo hará cada noche):

```bash
gcloud scheduler jobs run haggler-daily-trigger --location europe-north1
gcloud run jobs executions list --job haggler-daily --region europe-north2 --limit 3
```

Y publicar sin esperar al cron: **Actions → publish → Run workflow** (rama `main`) o
`gh workflow run publish.yml --ref main`. El primer run hace el commit
`data: precios AAAA-MM-DD` y despliega el sitio.

## 7. Cómo leer los logs

**Ejecuciones del job**

```bash
gcloud run jobs executions list --job haggler-daily --region europe-north2 --limit 10
gcloud run jobs executions describe EJECUCION --region europe-north2
```

**Logs del job** (los del último día; `jsonPayload` si el log es estructurado):

```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="haggler-daily"' \
  --freshness=1d --order=asc --limit=500 \
  --format='table(timestamp, severity, textPayload, jsonPayload.message)'
```

Solo errores: añadir `AND severity>=ERROR` al filtro. En la consola: **Cloud Run → Jobs →
haggler-daily → Logs** (o el enlace "View logs" de cada ejecución).

**Reporte del run** (qué tareas fallaron, cuántas llamadas LLM, cuarentenas):

```bash
gcloud storage cat gs://mi-proyecto-haggler-data/latest/run.json
gcloud storage ls gs://mi-proyecto-haggler-data/runs/$(date -u +%F)/
gcloud storage cat gs://mi-proyecto-haggler-data/latest/coverage.json | head -50
gcloud storage cat gs://mi-proyecto-haggler-data/latest/review_queue.json
```

**Scheduler**

```bash
gcloud scheduler jobs describe haggler-daily-trigger --location europe-north1 \
  --format='yaml(state, schedule, timeZone, lastAttemptTime, status)'
gcloud logging read 'resource.type="cloud_scheduler_job"' --freshness=2d --limit=20
```

**GitHub Actions**: pestaña *Actions*. `publish.yml` deja en el resumen del run el `diff --stat`
de `data/` (y un *warning* si el sync falló); `probe.yml` deja el `summary.md` del probe en el
resumen y `summary.md` + `summary.json` como artefacto `probe-summary-<modo>` (3 días).

## 8. Probe (afinar parsers con las páginas reales)

`probe.yml` corre en los runners de GitHub, que sí llegan a los sitios de los proveedores. Se
dispara solo con push a `claude/**` que toque `src/haggler/sources/`, `fetch/`, `fx/`,
`catalog/` o `probe/targets.txt`, o a mano:

- **Actions → probe → Run workflow**: `mode` = `adapters` (solo parsers), `fx` (tipos de cambio)
  o `full` (workflow completo con store local; LLM solo si existe el secreto `GEMINI_API_KEY`).
- `targets` reemplaza `probe/targets.txt` para ese run, separado por `;`:
  `netflix SE CO; spotify *`.

Los targets que fallan no hacen fallar el job; solo un crash del CLI lo hace.

Las páginas descargadas (`probe-out/raw/`, y el store completo en `mode=full`) se quedan en el
runner: el artefacto solo lleva el resumen, que cita como mucho fragmentos cortos, porque el HTML
crudo de los proveedores nunca se republica. El probe no usa GCP ni credenciales.

## 9. Costos estimados

Uso esperado: un run diario de ~5–20 min con 1 vCPU / 1 GiB, unos MB por día en GCS y casi
ninguna llamada LLM (solo cuando un parser falla). Precios de lista aproximados (USD, 2026),
verificar en la calculadora de GCP:

| Servicio | Uso | Costo mensual aprox. |
|---|---|---|
| Cloud Run Jobs | ≤ 20 min/día ≈ 36 000 vCPU-s + 36 000 GiB-s | ~0 dentro de la capa gratuita de Cloud Run; < 1 USD sin ella |
| Cloud Scheduler | 1 job | 0 (3 jobs gratis por cuenta de facturación) |
| Cloud Storage | `raw/` 90 días + snapshots, ~1–3 GB | < 0,10 USD |
| Artifact Registry | ≤ 5 imágenes (~1 GB) | ~0,05–0,10 USD (0,5 GB gratis) |
| Vertex AI (Gemini flash-lite) | fallback ≤ 150 llamadas/día, típico ~0 | 0–3 USD (tope de diseño ≈ 0,10 USD/día) |
| Cloud Logging | pocos MB | 0 (50 GiB gratis) |
| GitHub Actions / Pages | repo público | 0 |

Total típico: **< 1 USD/mes**; peor caso realista: ~5 USD/mes. Recomendado: crear un presupuesto
con alertas en **Billing → Budgets & alerts** (por ejemplo 5 USD).

## 10. Cómo desactivar

**Pausar el run diario** (reversible, sin borrar nada):

```bash
gcloud scheduler jobs pause haggler-daily-trigger --location europe-north1
gcloud scheduler jobs resume haggler-daily-trigger --location europe-north1   # reanudar
```

**Pausar la publicación / despliegues**:

```bash
gh workflow disable publish.yml --repo glrodasz/haggler-agent
gh workflow disable deploy-job.yml --repo glrodasz/haggler-agent
gh workflow enable publish.yml --repo glrodasz/haggler-agent                 # reactivar
```

(o **Actions → workflow → ··· → Disable workflow**). Quitar las variables `GCP_*` también los
deja sin efecto: los workflows solo dejarán un aviso.

**Quitar el sitio**: **Settings → Pages → Unpublish site** (o cambiar *Source*).

**Borrar todo en GCP** (irreversible; el bucket contiene el historial de snapshots):

```bash
P=mi-proyecto
gcloud scheduler jobs delete haggler-daily-trigger --location europe-north1 --project $P
gcloud run jobs delete haggler-daily --region europe-north2 --project $P
gcloud artifacts repositories delete haggler --location europe-north2 --project $P
gcloud storage rm --recursive gs://$P-haggler-data          # borra también el bucket
gcloud iam workload-identity-pools delete github --location global --project $P
for sa in haggler-job haggler-scheduler haggler-github; do
  gcloud iam service-accounts delete $sa@$P.iam.gserviceaccount.com --project $P
done
```

Los pools de WIF borrados quedan 30 días en estado `DELETED`; si se vuelve a correr el bootstrap
en ese periodo, el script los restaura (`undelete`). Las bindings de IAM a nivel de proyecto de
las cuentas borradas desaparecen solas (quedan como `deleted:serviceAccount:…` hasta limpiarse).

## 11. Solución de problemas

| Síntoma | Causa probable / solución |
|---|---|
| `bootstrap.sh`: Cloud Scheduler no disponible en la región | usar otra `SCHEDULER_REGION` (por defecto `europe-north1`); el job sigue en `REGION` |
| `bootstrap.sh`: "service account does not exist" justo después de crearla | propagación de IAM; el script reintenta 6 veces cada 10 s. Si aun así falla, volver a correrlo |
| `google-github-actions/auth`: `Permission 'iam.serviceAccounts.getAccessToken' denied` o `unauthorized_client` / "rejected by the attribute condition" | `GCP_WIF_PROVIDER` o `GCP_GITHUB_SA` mal copiados, o el run no coincide con la condición: otro repositorio (`GITHUB_REPO`, distingue mayúsculas) u otra rama (`GITHUB_BRANCH`, solo `main`). Tras crear WIF puede tardar ~5 min en propagarse |
| `deploy-job`: `denied: Permission "artifactregistry.repositories.uploadArtifacts"` | `GCP_REGION`/`AR_REPOSITORY` no coinciden con el repo creado; volver a correr el bootstrap |
| `deploy-job`: `iam.serviceaccounts.actAs` denegado | falta `roles/iam.serviceAccountUser` de `haggler-github` sobre `haggler-job` (bootstrap) |
| Scheduler en estado de error / `PERMISSION_DENIED` | falta `roles/run.invoker` de `haggler-scheduler` sobre el job; volver a correr el bootstrap. Ver `status` con `gcloud scheduler jobs describe` |
| El job corre la imagen de ejemplo (`us-docker.pkg.dev/cloudrun/container/job`) y no escribe nada en el bucket | todavía no se corrió `deploy-job.yml` (paso 5), o el bootstrap se corrió con otro `JOB` y falta la variable `GCP_JOB_NAME`: `deploy-job` desplegó `haggler-daily`, que no tiene Scheduler (`gcloud run jobs list --region europe-north2`) |
| Errores 404 / modelo no encontrado en Vertex AI | el modelo no está disponible en `global` para el proyecto: fijar la variable `HAGGLER_EXTRACT_MODEL` y volver a correr `deploy-job` |
| `publish`: *warning* "sync falló: … latest/run.json no existe" | el job aún no terminó ninguna corrida en GCS (pasos 5–6). El sitio se publica igual con el `data/` ya versionado |
| `publish`: *warning* "sync falló" por `run_id` o SHA-256 distintos entre archivos | el job estaba escribiendo `latest/` en ese momento; el sitio se publica con el `data/` anterior; relanzar `publish` más tarde |
| `publish`: "Aún no hay data/latest/prices.json" | no hay datos versionados en `data/` y no hubo sync: faltan variables (paso 3), se lanzó desde otra rama, o el sync falló (ver el *warning* del job de sync) |
| `publish`: *warning* "actions/configure-pages falló" | Pages no está habilitado (paso 4); el sitio se construye con la ruta base `/haggler-agent` y no detecta un dominio propio |
| `publish`: el deploy a Pages falla | Pages no está habilitado con *Source: GitHub Actions* (paso 4), o se lanzó desde una rama que no es `main` |
| Los workflows programados dejaron de correr | GitHub desactiva los `schedule` de repos públicos tras 60 días sin actividad; reactivarlos en *Actions* |

## 12. Notas de seguridad

- Sin claves de larga duración: GitHub usa WIF y solo los workflows del repositorio
  `glrodasz/haggler-agent` que corren en la rama `main` pueden asumir `haggler-github`. Un
  workflow nuevo empujado a otra rama (`claude/**`, un pull request del mismo repo) no recibe
  credenciales de GCP. Para que esto valga, protege `main` (*Settings → Branches*): quien puede
  hacer push a `main` puede desplegar el job. Si corriste el bootstrap con una versión anterior
  (condición solo por repositorio), vuelve a correrlo: reaplica la condición del proveedor.
- `haggler-github` puede publicar imágenes y desplegar el job, y solo **leer** el bucket.
  `haggler-job` escribe en el bucket y llama a Vertex AI. `haggler-scheduler` solo puede ejecutar
  el job.
- El bucket es privado (acceso uniforme + *public access prevention*). El HTML crudo de las
  páginas (`raw/`) nunca se publica y se borra a los 90 días. El probe tampoco lo sube como
  artefacto: solo su resumen.
- Los commits de datos los hace `github-actions[bot]` con el `GITHUB_TOKEN` del run, que solo
  tiene `contents: write` en el job de sync.
