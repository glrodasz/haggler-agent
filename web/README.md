# Sitio web de haggler

Sitio estático (Astro 7 + Preact, TypeScript) que publica los precios recogidos por el agente en
GitHub Pages: <https://glrodasz.github.io/haggler-agent/>. Interfaz en español; todos los textos
están en `src/i18n/es.ts`.

## Páginas

| Ruta | Contenido |
|---|---|
| `/` | Comparador (única isla interactiva): servicios, plan, riesgo máximo aceptado, moneda, ahorro neto de comisión de cambio |
| `/servicios/[id]/` | Precios por país y plan, factibilidad desde Suecia y paquetes que incluyen el servicio |
| `/paises/[cc]/` | Todas las ofertas de un país |
| `/cambios/` | Cambios de precio de los últimos 90 días |
| `/metodologia/` | Fuentes, ejes de riesgo, supuestos arriesgados, cobertura, atribuciones y aviso legal |

## Datos

Las páginas leen los JSON del contrato de datos desde `src/data/` (generado, ignorado por git).
Los tipos del contrato están en `src/logic/types.ts`.

```bash
# Datos reales (desde la raíz del repositorio)
uv run haggler site-data --data-dir data --out web/src/data

# o datos de ejemplo (src/data-sample -> src/data)
npm run sample-data            # no pisa datos reales; usa "-- --force" para reemplazarlos
```

`npm run build` falla con un mensaje claro si falta `src/data/`.

## Comandos

```bash
npm ci
npm run sample-data
npm run dev       # http://localhost:4321/haggler-agent/
npm run check     # astro check (tipos)
npm test          # vitest: lógica del comparador, formato, datos de ejemplo
npm run build     # genera dist/
npm run preview
```

Variables de entorno para el build: `SITE_URL` (por defecto `https://glrodasz.github.io`) y
`BASE_PATH` (por defecto `/haggler-agent`; usa `/` para un dominio propio). En CI conviene
`ASTRO_TELEMETRY_DISABLED=1`.

## Lógica

- `src/logic/compare.ts` replica `src/haggler/compare.py`: coste mensual en EUR + comisión de
  cambio si la moneda no es la de residencia, la más barata con nivel de riesgo ≤ k y las
  ofertas «sin evaluar» nunca se eligen. Un test compara el resultado con `cheapest.json`.
- `src/logic/format.ts`: formato es-ES con `Intl` (moneda, porcentajes, fechas en UTC).
- Sin CDNs, fuentes externas, analítica ni cookies.
