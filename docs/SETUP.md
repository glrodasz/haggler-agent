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
   cuenta haggler-job + Gemini en Vertex AI         research/  (semanal, §13 y §14)
                                                          │
Cloud Run Job haggler-research (lunes 05:23) ─────────────┤
   misma imagen: haggler research run                     ▼
GitHub Actions publish.yml (05:15 UTC, WIF, cuenta haggler-github)
   haggler sync ─► commit de data/ ─► haggler site-data ─► build Astro ─► GitHub Pages
```

| Workflow | Cuándo corre | Qué hace |
|---|---|---|
| `ci.yml` | cada push y pull request | ruff, pytest, `validate-catalog`; sitio: `astro check`, vitest, build con datos de muestra; contrato: `haggler daily` sobre los fixtures de replay → `sync` → `site-data` → build del sitio, y otro build con un `data/` vacío (primer despliegue) |
| `probe.yml` | push a `claude/**` que toque fuentes/fetch/fx/catálogo, o manual | descarga las páginas reales y reporta qué extrae cada adapter; `mode=research` hace una corrida real de investigación y `mode=bundles` mide la legibilidad de las páginas de los paquetes (no usa GCP) |
| `deploy-job.yml` | push a `main` que toque código/catálogo/Dockerfile, o manual desde `main` | build + push de la imagen y `gcloud run jobs deploy` de `haggler-daily` y `haggler-research` |
| `publish.yml` | todos los días 05:15 UTC, o manual | `haggler sync` desde GCS, commit de `data/`, build y despliegue en Pages; el resumen del run cuenta lo que hay por revisar en los paquetes (§14) |

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
| `GCP_RESEARCH_JOB_NAME` | variable | nombre del job de investigación si el bootstrap se corrió con otro `RESEARCH_JOB` (por defecto `haggler-research`) |
| `HAGGLER_EXTRACT_MODEL` | variable | modelo Gemini del fallback LLM si el de por defecto (`gemini-3.1-flash-lite`) no está disponible en Vertex; `deploy-job.yml` lo pasa al job |
| `HAGGLER_RESEARCH_MODEL` | variable | modelo Gemini con Google Search del job de investigación (por defecto `gemini-3-flash-preview`; el probe `mode=research` lista los disponibles) |
| `HAGGLER_RESEARCH_MAX_BUNDLES` | variable | paquetes que el agente investiga por corrida (por defecto 2; §14); `deploy-job.yml` lo pasa al job de investigación si es un número entero |
| `HAGGLER_BUNDLE_WATCH_MAX_PAGES` | variable | URL distintas que el vigilante de paquetes lee por corrida (por defecto 40; §14); igual que la anterior |
| `GEMINI_API_KEY` | **secreto** | clave de Google AI Studio, solo para `probe.yml` en `mode=full` y `mode=research` (sin ella el probe corre sin LLM) |

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
de `data/` (y un *warning* si el sync falló) y la línea "Paquetes: …" con lo que hay por revisar
(§14); `probe.yml` deja el `summary.md` del probe en el resumen y `summary.md` + `summary.json`
como artefacto `probe-summary-<modo>` (3 días).

## 8. Probe (afinar parsers con las páginas reales)

`probe.yml` corre en los runners de GitHub, que sí llegan a los sitios de los proveedores. Se
dispara solo con push a `claude/**` que toque `src/haggler/sources/`, `fetch/`, `fx/`,
`catalog/` o `probe/targets.txt`, o a mano:

- **Actions → probe → Run workflow**: `mode` = `adapters` (solo parsers), `fx` (tipos de cambio),
  `full` (workflow completo con store local; LLM solo si existe el secreto `GEMINI_API_KEY`),
  `research` (ver §13; targets en `probe/research_targets.txt`) o `bundles` (legibilidad de las
  páginas oficiales de los paquetes promocionales, sin LLM; targets `<etiqueta> <CC> <url>` en
  `probe/bundle_targets.txt`: solo una página `readable` puede vigilarse en modo `auto`).
- `targets` reemplaza `probe/targets.txt` para ese run (`probe/research_targets.txt` en
  `mode=research`, `probe/bundle_targets.txt` en `mode=bundles`), separado por `;`:
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
| Vertex AI (Gemini flash + Google Search) | investigación semanal: ≤ 40 llamadas y ≤ 200 consultas de búsqueda por corrida, tope de 1 500 consultas/mes | tokens < 1 USD; las consultas de *grounding* tienen una cuota gratuita mensual (5 000 en Gemini 3) y luego se facturan (~14 USD por 1 000): con los topes por defecto, ~0 |
| Cloud Logging | pocos MB | 0 (50 GiB gratis) |
| GitHub Actions / Pages | repo público | 0 |

Total típico: **< 1 USD/mes**; peor caso realista: ~5 USD/mes. Recomendado: crear un presupuesto
con alertas en **Billing → Budgets & alerts** (por ejemplo 5 USD).

## 10. Cómo desactivar

**Pausar el run diario** (reversible, sin borrar nada):

```bash
gcloud scheduler jobs pause haggler-daily-trigger --location europe-north1
gcloud scheduler jobs resume haggler-daily-trigger --location europe-north1   # reanudar
gcloud scheduler jobs pause haggler-research-trigger --location europe-north1  # solo la investigación
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
gcloud scheduler jobs delete haggler-research-trigger --location europe-north1 --project $P
gcloud run jobs delete haggler-daily --region europe-north2 --project $P
gcloud run jobs delete haggler-research --region europe-north2 --project $P
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
| `publish`: *warning* "push de data/ falló: No se pudo hacer push de data/" | `main` está protegida (*pull request* obligatorio, *status checks* requeridos…) y la identidad que hace el push no tiene *bypass* (ver §12). El sitio se publica igual con los datos sincronizados (artefacto del job de sync); solo `data/` del repositorio, y con él el historial de `data/changes/`, queda desactualizado hasta que el push vuelva a funcionar |
| `publish`: *warning* "actions/configure-pages falló" | Pages no está habilitado (paso 4); el sitio se construye con la ruta base `/haggler-agent` y no detecta un dominio propio |
| `publish`: el deploy a Pages falla | Pages no está habilitado con *Source: GitHub Actions* (paso 4), o se lanzó desde una rama que no es `main` |
| Los workflows programados dejaron de correr | GitHub desactiva los `schedule` de repos públicos tras 60 días sin actividad; reactivarlos en *Actions* |
| `publish`: *warning* "resumen de paquetes" | `haggler research review --bundles --summary` falló (el log del paso muestra el error); solo falta la línea del resumen, el sitio se publica igual. Reproducirlo en local con `data/` al día (§14) |
| `deploy-job`: *warning* "… no es un número entero" | la variable de repositorio `HAGGLER_RESEARCH_MAX_BUNDLES` o `HAGGLER_BUNDLE_WATCH_MAX_PAGES` no es un entero; el job queda con el valor por defecto. Corregirla y volver a correr `deploy-job` |
| Un paquete sigue en *Requiere revisión* | `haggler research review --subject <id>` da la causa. Una propuesta oficial que difiere del YAML: pegar su snippet o rechazarla. Sin propuesta ("Propuestas de valor: ninguna") no hay nada que pegar ni rechazar, y `reject` no cambia el estado: si falta un ancla de precio, plan base o componente en una página que cargó, volver a copiar su texto de la salida del probe `mode=bundles` (§8), o quitar el ancla vieja, en un PR a `bundles.yaml`; si su lista PDF tiene una edición nueva, cambiar `watch.pages[].url` a esa edición y volver a copiar sus anclas de ella (§14, "Sin propuesta") |

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
  tiene `contents: write` en el job de sync. Si la protección de `main` exige *pull requests* o
  *status checks*, ese push se rechaza: el `GITHUB_TOKEN` no puede añadirse al *bypass* de la
  protección de rama. Para seguir versionando `data/`, usa un *ruleset* cuyo *bypass* incluya la
  identidad que hace el push (una GitHub App o una *deploy key* con permiso de escritura, cambiando
  el `actions/checkout` del job de sync para usarla). Sin eso el workflow avisa con un *warning* y
  publica el sitio con los datos sincronizados igualmente (§11).

## 13. Investigación de factibilidad (Fase 2)

Un segundo Cloud Run Job, `haggler-research` (misma imagen, `haggler research run --store gcs`),
corre los lunes a las 05:23 Europe/Stockholm, después del run diario. Investiga con Gemini +
Google Search las reglas de país de los servicios, lo que exige contratar la oferta de un país
(solo para los pares servicio × país que ahorran al menos 1 €/mes y 15 %) y los medios de pago, y
**solo guarda lo que nuestro propio fetch verifica**: la cita literal tiene que aparecer en la URL
citada (o en las páginas oficiales conocidas de `research_urls` en `catalog/services.yaml` y
`data/curated/payment_instruments.yaml`), respetando `robots.txt` y sin evadir bloqueos.

```
research/runs/AAAA-MM-DD/RUN/report.json    presupuesto usado, verificación por clase de fuente
research/latest/{services,pairs,instruments}.json   hechos verificados (citas + URL + fecha)
research/latest/state.json                  vigencia y reintentos por sujeto
research/latest/run.json                    se escribe al final, con los SHA-256
research/latest/{bundles,bundle_watch}.json paquetes (§14)
research/usage/AAAA-MM.json                 llamadas y consultas del mes (también las de --dry-run)
raw/research/AAAA-MM-DD/…                   páginas leídas (se borran a los 30 días)
```

`haggler sync` (en `publish.yml`) también copia la investigación a `data/research/`, y
`haggler site-data` calcula los veredictos por perfil (`data/curated/profiles.yaml`). Los
hallazgos nuevos aparecen en el sitio como **Pendiente de revisión** y el comparador los ignora
salvo que se marque la casilla correspondiente.

**Primera vez** (después del bootstrap y del primer `deploy-job`):

```bash
gcloud run jobs execute haggler-research --region europe-north2 --wait
gcloud storage cat gs://mi-proyecto-haggler-data/research/latest/run.json
```

Para una prueba acotada: `--args='^;^research;run;--store;gcs;--services;netflix;--max-jobs;1'`.

**Revisar y aceptar** (en tu copia del repo, con `data/` al día):

```bash
uv run haggler research review                     # hallazgos pendientes e impacto por perfil
uv run haggler research accept netflix --by tu-usuario --note "revisado"
uv run haggler research reject pair:netflix:CO --note "la cita no dice eso"
git add data/curated/research_review.yaml && git commit -m "Revisión de hallazgos" && git push
```

Una decisión vale para el id del hallazgo **y** su hash de evidencia: si el agente encuentra otra
cita u otra URL para el mismo hecho, vuelve a quedar pendiente. Lo curado a mano en
`data/curated/feasibility.yaml` siempre gana.

**Probar antes de desplegar**: crea el secreto `GEMINI_API_KEY` (clave de Google AI Studio) y
lanza **Actions → probe → Run workflow → mode = research**. El resumen lista los modelos flash
disponibles, si `robots.txt` deja seguir las redirecciones de *grounding* (hoy no:
`vertexaisearch.cloud.google.com` lo prohíbe, por eso existen las `research_urls`), cuántas citas
se verifican por clase de fuente y lo que costó en consultas y tokens. Si menos del 60 % de las
citas oficiales se verifican, revisa las `research_urls` antes de activar el job.

**Ajustes** (variables de entorno, ver `.env.example`): `HAGGLER_RESEARCH_MODEL`,
`HAGGLER_RESEARCH_MAX_CALLS` (40), `HAGGLER_RESEARCH_MAX_QUERIES` (200),
`HAGGLER_RESEARCH_MAX_MONTH_QUERIES` (1500), `HAGGLER_RESEARCH_MAX_PAIRS` (6),
`HAGGLER_RESEARCH_MAX_SERVICES` (3), `HAGGLER_RESEARCH_MAX_INSTRUMENTS` (2). El job se despliega
con `--max-retries 0`: un reintento gastaría otra vez las consultas de búsqueda.

Nada de esto es asesoría legal: el sitio informa riesgos con su fuente y nunca sugiere VPN ni
declarar datos falsos (una ruta que lo exigiría se muestra como *No factible*).

## 14. Paquetes (Fase 3)

Los paquetes promocionales (Meli+, Telia, Apple One…) están en `data/curated/bundles.yaml`, **la
única fuente de sus valores**: precio, precio de introducción, plan base, permanencia,
componentes, fechas y requisitos. Ningún job escribe ese archivo. Lo automático es evidencia (el
vigilante) o propuestas (las pistas del vigilante y los hallazgos del agente); un valor solo
cambia cuando una persona edita el YAML en un pull request.

No hay job, Scheduler ni cambios en `bootstrap.sh` nuevos: todo corre dentro de
`haggler-research` (§13), y `publish.yml` lo sincroniza y lo publica como el resto.

```
plan_research → watch_bundles (vigilante, sin LLM) → research → verify_evidence → persist
```

### El vigilante: qué hace y cuándo

- **Cuándo**: en cada corrida de `haggler-research` (los lunes a las 05:23 Europe/Stockholm, o a
  mano), siempre: también con el guardia mensual de consultas agotado, con `--no-llm`, con
  `--max-jobs 0` y con `--watch-only`. No usa el modelo ni Google Search.
- **Qué lee**: las páginas (`watch.pages`) de cada paquete con `watch.mode: auto`, de un
  proveedor con `automated_access: allowed` y no vencido. Cada URL una vez por corrida, como
  máximo `HAGGLER_BUNDLE_WATCH_MAX_PAGES` (40) URL distintas, con las reglas de acceso de todo el
  agente: `robots.txt` en cada salto (también la página índice y el PDF), user agent
  identificable, al menos 3 s entre peticiones a un mismo host, sin proxies y sin evadir muros.
  Una página que no cabe en el tope queda `over_budget`, nunca se omite en silencio.
- **Qué registra**, por paquete, en `bundle_watch.json`:
  - cada página: su estado (`ok`, `blocked`, `robots_disallowed`, `not_found`, `wrong_country`,
    `pdf_error`…), la URL final y la edición, si es un PDF versionado;
  - cada ancla de `watch.anchors`: `verified` (la cita está en el texto visible y dice el valor
    del YAML), `missing` (la página cargó y la cita no está, o ya no dice ese valor),
    `embedded_only` (solo en datos embebidos: nunca confirma), `outdated_document` (cita una
    edición del PDF que ya tiene otra más nueva) o `unreadable` (no se pudo leer la página:
    **nunca** cuenta como "cambió el valor");
  - las **pistas de precio**: importes en la moneda del país que el chequeo valor‑cita lee como
    precio del paquete y que difieren del YAML. Son propuestas, nunca valores;
  - el resultado: `changed` (falta un ancla de precio, plan base o componente en una página que
    cargó), `new_document` (la lista PDF tiene una edición más nueva), `unreadable` (no se pudo
    leer ninguna página) u `ok`.
- **PDF versionados** (las listas de precios de Telia): con `index_url` + `link_pattern` se lee
  primero el índice y se toma la edición más nueva. El texto se extrae en un proceso aparte, con
  límites, y **nunca** va al modelo.
- **Pausa**: una página ilegible 3 corridas seguidas no se vuelve a pedir durante 28 días
  (conserva su último estado).

### El agente de paquetes

- Investiga paquetes **después** de todos los sujetos de la Fase 2, con los mismos topes de
  llamadas y consultas y el mismo guardia mensual: si el presupuesto se acaba, los paquetes son lo
  primero que se salta. Como máximo `HAGGLER_RESEARCH_MAX_BUNDLES` (2) por corrida.
- Solo elige paquetes `auto` (un `manual` solo si se pide con `--bundles`; nunca uno `off`,
  vencido o de un proveedor que prohíbe el acceso automatizado), a los 28 días de su última
  investigación, o a los 14 si la oferta o su precio de introducción terminan en los próximos 14
  días. Entre los que tocan van primero los que el vigilante vio cambiar en esa corrida, luego
  los nunca investigados, los que tienen valores sin ancla, los que vencen pronto y los más
  antiguos.
- Hay dos clases de hallazgos:
  - **valores** (precio, intro, plan base, permanencia, fechas, componentes, descuentos, URL de
    la lista de precios): solo cuentan con una cita literal en una página **oficial del país del
    paquete** que además pase el chequeo valor‑cita (si no, `value_not_in_quote` o
    `implausible`). Se aceptan editando el YAML (ver el ciclo de revisión);
  - **factibilidad** (cláusula de residencia, controles, línea local, medio de pago, consulta de
    crédito…): como en la Fase 2, se aceptan por id y hash.
- Los hallazgos pendientes de los paquetes `auto` que no se investigan esa semana se vuelven a
  comprobar en su página sin el modelo (`rechecked_on`): una cita que desapareció deja de contar.
- Nunca sugiere VPN, otra dirección ni datos falsos.

**Coste**: el vigilante hace unas 15–25 peticiones GET por semana y 0 consultas; el agente, como
máximo 2 paquetes por semana (≈ 100 consultas al mes), dentro del guardia de 1 500 que comparte
con la Fase 2 (§9).

### Archivos

```
research/latest/bundle_watch.json   estado del vigilante: páginas, anclas, pistas y resultado
research/latest/bundles.json        hechos del agente de paquetes (cuando hay alguno)
research/latest/run.json            los lista con su SHA-256 cuando existen
raw/research/AAAA-MM-DD/…           páginas y PDF leídos (se borran a los 30 días)

data/research/bundle_watch.json     copias de haggler sync (publish.yml), versionadas
data/research/bundles.json
data/curated/bundles.yaml           los valores y las anclas (a mano, en un PR)
data/curated/research_review.yaml   decisiones de factibilidad y propuestas rechazadas
```

Los dos archivos de paquetes son opcionales para `haggler sync`: un `run.json` que no los lista
(de antes de la Fase 3) se sincroniza igual. `haggler site-data` combina el YAML, `data/research/`
y los precios del día, y publica por paquete su estado, la evidencia, el veredicto de cada perfil
y los cambios pendientes (la sección "Cambios pendientes de revisión" de la tarjeta).

### Cuándo un valor está confirmado

El estado de un paquete es derivado: en el YAML solo cabe `status: unverified|expired`, y un
`verified`, `verified: true` o `retrieved_at` escrito a mano se rechaza. Un valor está
**confirmado** solo si se cumple todo esto:

1. está en el YAML con un **ancla** (`watch.anchors`): una cita literal copiada de *nuestra*
   extracción de la página (la salida del probe `mode=bundles`, §8, o un snippet de la revisión);
2. el ancla pasa `validate-catalog` offline;
3. el vigilante la encuentra en el texto visible (no solo en datos embebidos);
4. la página está en un dominio oficial del **país del paquete** (`providers[].domains`);
5. la verificación tiene 15 días o menos;
6. si es un PDF versionado, es la edición vigente.

Si falta algo, el sitio lo muestra "sin confirmar", con la razón; si deja de confirmarse, dice
"último valor confirmado X el <fecha>". Los estados, por precedencia: **Vencida**, **Requiere
revisión** (falta un ancla decisiva en una página que cargó, hay una edición nueva del PDF o una
propuesta oficial que difiere del YAML), **Verificado**, **Verificación manual** y **Sin
verificar**.

### Ciclo de revisión

1. **Ver qué hay.** El resumen del run de `publish.yml` trae la línea "Paquetes: …" con las
   propuestas de valor y los hallazgos de factibilidad pendientes y los paquetes por revisar. En
   tu copia del repo, con `data/` al día (`git pull`):

   ```bash
   uv run haggler research review --bundles                      # cada paquete con algo pendiente
   uv run haggler research review --subject meli_plus_co_total   # uno solo
   uv run haggler research review --bundles --summary            # la línea del resumen
   ```

   Para cada paquete: su estado y las razones, las páginas y anclas según el vigilante, las
   propuestas (valor antes → después, fuente, cita y URL), los hallazgos de factibilidad, el
   impacto en el veredicto de cada perfil y un **snippet YAML** por propuesta. La revisión
   general (`haggler research review`) termina con "Paquetes por revisar: …" cuando hay alguno.

2. **Copiar el snippet** en la fila del paquete de `data/curated/bundles.yaml`, en un pull
   request. Trae el valor en su ruta (`price.amount`, `price.intro_amount`, `valid_until`,
   `components`…, con `marginal_amount` recalculado si el YAML lo tiene) y la cita literal como
   ancla, o como evidencia oficial cuando ningún ancla confirma ese campo (la permanencia, por
   ejemplo). Lee sus notas: la página que falta en `watch.pages`, el ancla vieja que cita el
   valor anterior y hay que quitar, un ancla que `validate-catalog` rechazaría, un cambio de más
   del 50 %, una vigilancia `manual`. Con los precios pasa a menudo que la cita se verificó en
   línea porque el nombre del nivel estaba en un título más arriba, pero un ancla tiene que
   nombrarlo en su propio texto: copia entonces un fragmento más largo de la salida del probe
   `mode=bundles` (§8).

   `haggler research accept` no acepta valores de paquetes: sale con código 2 e imprime el
   snippet. Tampoco acepta un paquete entero (su id o `bundle:<id>`): sus hallazgos de
   factibilidad se aceptan uno a uno, por su id.

3. **`validate-catalog` comprueba el ancla offline**, en local (`uv run haggler validate-catalog`)
   y en el CI del pull request: entre 20 y 400 caracteres, sin elipsis, en una de las
   `watch.pages` (o en el índice de un PDF versionado), y su texto dice el valor del YAML
   (importe, moneda, periodo, rol y nombre del nivel; el servicio, el plan y el porcentaje de un
   componente; una fecha con año).

4. **Merge a `main`**: `deploy-job.yml` se dispara con los cambios en `data/curated/**` y
   redespliega la imagen con el YAML nuevo.

5. **La siguiente corrida semanal verifica el ancla en línea**, y el `publish.yml` siguiente
   publica el estado. La propuesta se resuelve sola en cuanto el YAML tiene el valor: no hay nada
   que aceptar. Para no esperar al lunes: una corrida `--watch-only` (abajo) y después
   `gh workflow run publish.yml --ref main`.

**Rechazar** una propuesta equivocada (una pista del vigilante o un hallazgo del agente), con el
id que muestra `review` (entre comillas: los valores llevan espacios):

```bash
uv run haggler research reject 'watch:meli_plus_co_total:bundle_price:9900 COP/month' \
  --by tu-usuario --note "es el precio del plan Esencial"
uv run haggler research reject meli_plus_co_total --note "revisado"   # todo lo del paquete
uv run haggler research accept 'ID-DEL-HALLAZGO' --by tu-usuario     # factibilidad, uno a uno
git add data/curated/research_review.yaml && git commit -m "Revisión de paquetes" && git push
```

Como en la Fase 2, una decisión vale para el id **y** el hash de su evidencia: si aparece otra
cita, otra línea u otra página, la propuesta vuelve.

**Sin propuesta.** Dos causas de *Requiere revisión* no traen ninguna propuesta: `review` dice
"Propuestas de valor: ninguna", no hay snippet que pegar y ni `reject` ni `accept` cambian el
estado. Solo sale de *Requiere revisión* cuando el vigilante vuelve a verificar el ancla o cuando
cambia el YAML (el ancla, o la URL de la edición). Se arregla a mano, en un PR a
`data/curated/bundles.yaml`:

- **Falta un ancla** ("… ya no aparece en …" o "… sigue en …, pero ya no indica el valor
  registrado"): la página cambió de redacción, o cambió un valor que el vigilante no propone (el
  plan base o un componente; solo propone precios). Corre el probe `mode=bundles` (§8) sobre esa
  página y vuelve a copiar el texto del ancla de su salida (con el valor nuevo en el YAML, si
  cambió), o quita el ancla vieja si ya no corresponde (un componente que el paquete dejó de
  incluir sale también de `components`).
- **Hay una edición nueva de la lista PDF** ("Hay una edición más nueva de la lista de
  precios"): cambia `watch.pages[].url` a la edición nueva (su URL es `newest_url` de esa página
  en `data/research/bundle_watch.json`). `index_url` y `link_pattern` siguen igual, y
  `link_pattern` tiene que seguir casando con la URL nueva (lo comprueba `validate-catalog`).
  Después vuelve a copiar las anclas de esa edición, con la URL nueva como su `url`. Si el
  vigilante leyó la edición nueva y sus precios difieren del YAML, `review` ya los muestra como
  propuestas: pega sus snippets en el mismo PR.

Un ancla editada empieza de cero: no cuenta hasta que la siguiente corrida la verifica. Los pasos
3–5 de arriba valen igual.

### Verificación manual

Si la página no se puede leer pero comprobaste la oferta tú mismo (tu factura, la app), en la fila
del paquete:

```yaml
    verified_by: user_report
    user_report:
      date: 2026-10-05
      note: "Factura propia de octubre: 21.900 COP."
```

Se muestra como "Verificación manual (hace N días)" y vence a los **45 días** (vuelve a *Sin
verificar*, con la razón). **Nunca** cuenta como fuente oficial: no confirma ningún valor ni
ancla, y un estado *Verificado* o *Requiere revisión* del vigilante tiene prioridad.

### Modos de vigilancia y lo que no se puede leer

| `watch.mode` | Qué pasa |
|---|---|
| `auto` | el vigilante lee las páginas cada semana y el agente puede investigar el paquete. Solo con páginas que el probe `mode=bundles` (§8) marca `readable` (su `suggested_mode`): ninguna URL ilegible queda en `auto` |
| `manual` | nadie lo lee solo; se comprueba a mano (verificación manual). El agente solo lo investiga si se pide con `--bundles` |
| `"off"` | ni vigilante ni agente; es obligatorio si el proveedor tiene `automated_access: forbidden`. Va entre comillas: sin ellas, YAML lee `off` como `false` |

Lo que nuestro fetch no puede leer (y nunca se intenta evadir) queda en `manual` o *Sin
verificar*, con la razón visible en el sitio:

- precios pintados con JavaScript: no hay navegador headless, y un dato que solo está en el JSON
  embebido (`__NEXT_DATA__`, ld+json) queda `embedded_only` y no confirma nada por sí solo;
- muros de login, de verificación de cuenta o anti-bot (`blocked`);
- páginas que `robots.txt` no permite (`robots_disallowed`);
- PDF escaneados (sin texto: no hay OCR), cifrados o de más de 60 páginas (`pdf_error`); uno
  de más de 10 MiB se rechaza al descargarlo (`failed`);
- precios que dependen de la IP (one.google.com le muestra USD al runner): `wrong_country`. Nunca
  se usan proxies ni VPN.

Una página que no se puede leer (`blocked`, `robots_disallowed`, `wrong_country`, `pdf_error`,
`failed`…) nunca cuenta como "cambió el valor": sus anclas quedan `unreadable`. Un precio pintado
con JavaScript es distinto, porque la página sí carga: su ancla queda `embedded_only` (si la cita
solo está en el JSON embebido) o `missing` (si no está en ninguna parte), y `missing` sí marca el
paquete como *Requiere revisión*. Por eso esas páginas van en `manual`. Para dejar de vigilar un
paquete basta con cambiar su `watch.mode` en un PR.

### Corridas a mano

Solo el vigilante, sin agente (0 llamadas al modelo y 0 consultas):

```bash
gcloud run jobs execute haggler-research --region europe-north2 \
  --args=research,run,--store,gcs,--watch-only --wait
```

Investigar paquetes concretos (es explícito: ignora la frescura y el tope, y vale también para un
paquete `manual`; el `^;^` es porque la lista lleva coma, §6):

```bash
gcloud run jobs execute haggler-research --region europe-north2 --wait \
  --args='^;^research;run;--store;gcs;--bundles;meli_plus_co_total,telia_se_obegransad_streaming;--max-jobs;2'
```

Después, `gh workflow run publish.yml --ref main` sincroniza y publica; el resultado queda en
`data/research/bundle_watch.json`. Cambiar los argumentos requiere el permiso
`run.jobs.runWithOverrides` (§6). Si dos corridas coinciden, la que termina segunda solo escribe
su reporte y no pisa `research/latest/`. En local (con red):
`uv run haggler research run --store local --watch-only` (con `--dry-run` no escribe nada).

### Ajustes

| Variable | Por defecto | Para qué |
|---|---|---|
| `HAGGLER_RESEARCH_MAX_BUNDLES` | `2` | paquetes que el agente investiga por corrida, después de todo lo demás |
| `HAGGLER_BUNDLE_WATCH_MAX_PAGES` | `40` | URL distintas que el vigilante lee por corrida (índices y PDF incluidos) |

En el job se fijan como variables de repositorio (§3): `deploy-job.yml` las pasa al job de
investigación solo si existen y son números enteros. Un cambio hecho a mano con
`gcloud run jobs update` se pierde en el siguiente despliegue, porque `--set-env-vars` reemplaza
todas las variables. En local van en `.env` (ver `.env.example`).
