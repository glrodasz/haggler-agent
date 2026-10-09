# haggler-agent

Agente construido con **Google ADK** (`google-adk` 2.x) que recolecta cada día los precios de
suscripciones de consumo **por país** desde fuentes oficiales, los valida y alimenta un sitio
estático para encontrar la combinación más barata *y factible* para quien vive en Suecia.

> Estado: **Fase 2** — precios diarios, sitio y un agente semanal de investigación de
> factibilidad con perfiles configurables. Bundles e instrumentos de pago siguen curados a mano
> (`data/curated/`). Ver [el roadmap](#roadmap).

## Qué hace

| Paso | Cómo |
|---|---|
| Fuentes | Páginas oficiales direccionables por país (soporte de iCloud+, apple.com/{país}, spotify.com/{país}, ayuda de Netflix, catálogo de Microsoft Store) y, para servicios que solo muestran el precio según la IP (YouTube Premium, Google One, HBO Max) o cuyo robots.txt no permite el acceso al bot (Disney+), el listado de compras in-app del **App Store** de cada país. Dataset secundario de Netflix (tompec, CC BY 4.0) para contrastar. |
| Extracción | Parsers determinísticos primero. Un agente LLM (Gemini en Vertex AI) solo actúa como respaldo cuando un parser falla, y únicamente puede citar texto literal de la página; el código verifica que el precio y la evidencia existan. |
| Normalización | Tipos de cambio BCE + bancos centrales vía Frankfurter (COP=BanRep, ARS=BCRA…), equivalente mensual, IVA incluido sí/no. |
| Validación | Reglas determinísticas: moneda vs país, rangos plausibles, cambios >30 % en cuarentena hasta confirmarse, orden de planes, arrastre de valores cuando una fuente falla. |
| Publicación | Snapshots completos en GCS; `data/latest/` en el repo (cada cambio de precio = una línea en el diff de git) y `data/changes/*.jsonl`; sitio en GitHub Pages. |

**Factibilidad** = tres ejes separados (legalidad / términos de servicio / practicidad); la etiqueta
es el peor de los tres. El sitio informa riesgos y supuestos; **no es asesoría legal** ni
recomienda declarar datos falsos.

### Investigación de factibilidad (Fase 2)

| Paso | Cómo |
|---|---|
| Qué investiga | Reglas de país de cada servicio, lo que exige contratar la oferta de un país (solo los pares con ahorro ≥ 1 €/mes y ≥ 15 %) y los medios de pago, con un vocabulario cerrado de hechos (`src/haggler/research/fields.py`). |
| Descubrir | Tres agentes ADK `single_turn` con Gemini + Google Search y salida estructurada; topes de llamadas, de consultas por corrida y por mes. |
| Verificar | Un hecho cuenta solo si nuestro fetch (robots.txt, sin evadir bloqueos) encuentra la **cita literal** en la página citada o en las páginas oficiales conocidas del sujeto. Clase de fuente: oficial / prensa / foro. |
| Evaluar | Función pura (`research/evaluate.py`) que deriva los tres ejes **por perfil** (`data/curated/profiles.yaml`): país de la tarjeta, presencia, documentos, dirección. Lo curado gana; las mejoras exigen fuente oficial; una ruta que requiere declarar datos falsos es *No factible*. |
| Revisar | Los hallazgos quedan *Pendiente de revisión* hasta `haggler research accept`; el comparador los excluye salvo que se active la casilla. |

## Arquitectura

```
Cloud Scheduler ──► Cloud Run Job (europe-north2) ──► GCS (snapshots + latest/)
                     haggler daily (ADK Workflow)            │
                                                             ▼
                    GitHub Actions publish.yml ──► commit data/ ──► GitHub Pages (web/)

Cloud Scheduler (lunes) ──► Cloud Run Job haggler-research ──► GCS research/latest/
                             haggler research run (ADK Workflow)   (sync → data/research/)
```

Workflow ADK (cadena lineal; cada nodo es un no-op si no recibe trabajo):

```
plan_tasks → fetch_and_parse (paralelo, reintentos por ítem) → llm_extract → fx_normalize
          → validate → adjudicate (segunda opinión LLM) → persist
```

Workflow de investigación:

```
plan_research → research (agentes con Google Search) → verify_evidence (nuestro fetch) → persist
```

## Estructura

```
src/haggler/        paquete Python (modelos, fuentes, fetch, fx, validación, pipeline ADK, CLI)
agents/haggler_agent/  shim para `adk web agents` / `adk run agents/haggler_agent`
agents/haggler_research/  shim del workflow de investigación para `adk web agents`
catalog/            servicios, planes, fuentes y países (YAML editable)
data/latest/        últimos precios publicados (gestionado por el bot)
data/changes/       historial de cambios de precio (append-only)
data/curated/       factibilidad, perfiles, revisiones, bundles, instrumentos de pago
data/research/      hechos verificados por el agente de investigación (gestionado por el bot)
web/                sitio estático (Astro + Preact)
infra/              bootstrap de GCP
.github/workflows/  CI, probe, despliegue del job, publicación
```

## Desarrollo local

```bash
uv sync
uv run pytest
uv run haggler validate-catalog

# Corrida offline con páginas grabadas (sin red ni LLM)
uv run haggler daily --replay tests/workflow/fixtures/replay --store local --no-llm

# Corrida real (necesita acceso a internet; LLM opcional)
cp .env.example .env   # descomenta UNA opción: A) GOOGLE_API_KEY (AI Studio) o B) Vertex vía ADC
uv run haggler daily --store local --services netflix,spotify --countries SE,CO

# Datos del sitio y build
uv run haggler sync --from-dir .haggler --data-dir data
uv run haggler site-data --data-dir data --out web/src/data
npm --prefix web ci && npm --prefix web run build

# Investigación de factibilidad (necesita LLM con Google Search para investigar)
uv run haggler research run --store local --dry-run --no-llm   # solo planifica
uv run haggler research run --store local --services netflix --pairs spotify:CO
uv run haggler research review                                   # pendientes e impacto
uv run haggler research accept netflix --by tu-usuario

# UI de desarrollo de ADK
PYTHONPATH=agents uv run adk web agents
```

`haggler` carga el `.env` del directorio de trabajo al arrancar (`uv run` no lo lee por sí solo)
sin pisar las variables que ya están en el entorno; `adk web agents` carga el `.env` de la raíz
del repo. En `.env.example` las dos opciones de LLM vienen comentadas: sin ninguna, el respaldo
LLM queda apagado; con las dos, google-genai ignora `GOOGLE_API_KEY` y llama a Vertex AI.

Despliegue en GCP y GitHub Pages: ver [docs/SETUP.md](docs/SETUP.md).

## Roadmap

1. **Fase 1**: precios diarios por país + sitio mínimo.
2. **Fase 2 (esta)**: agente semanal de investigación de factibilidad (hechos verificados con cita
   literal, evaluador determinístico por perfil, revisión humana por hash de evidencia).
3. **Fase 3**: agente de bundles (Meli+, telcos, Apple One…) que monitorea páginas/PDF oficiales.
4. **Fase 4**: optimizador de combinaciones en el navegador (una región por cuenta Apple/Google,
   máximo de instrumentos de pago extranjeros, bundles como SKU virtuales).

## Licencia

Apache-2.0. Los precios pertenecen a sus respectivos proveedores; el dataset de Netflix de
[tompec/netflix-prices](https://github.com/tompec/netflix-prices) se usa bajo CC BY 4.0.
