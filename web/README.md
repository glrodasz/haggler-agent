# Sitio web de haggler

Sitio estático (Astro 7 + Preact, TypeScript) que publica los precios recogidos por el agente en
GitHub Pages: <https://glrodasz.github.io/haggler-agent/>. Interfaz en español; todos los textos
están en `src/i18n/es.ts`.

## Páginas

| Ruta | Contenido |
|---|---|
| `/` | Comparador (única isla interactiva): servicios, plan, perfil de factibilidad, hallazgos pendientes de revisión (opcional), riesgo máximo aceptado, moneda, ahorro neto de comisión de cambio |
| `/servicios/[id]/` | Precios por país y plan, factibilidad del perfil por defecto (fuente, evidencia, supuestos y motivos) y paquetes que incluyen el servicio |
| `/paises/[cc]/` | Todas las ofertas de un país |
| `/cambios/` | Cambios de precio de los últimos 90 días |
| `/metodologia/` | Fuentes, ejes de riesgo, supuestos arriesgados, cobertura, atribuciones y aviso legal |

## Datos

Las páginas leen los JSON del contrato de datos desde `src/data/` (generado, ignorado por git).
Los tipos del contrato están en `src/logic/types.ts`. El país de residencia sale de los datos
(`meta.residence` + `countries.json`, opción `--residence` de `site-data`), nunca del texto.
Un `data/` sin ninguna corrida (clon nuevo) también genera un contrato válido (`run_id`, `as_of`
y `fx.as_of` en `null`, listas vacías) y el sitio se construye igual.

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
  cambio si la moneda no es la de residencia, redondeado a céntimos (diferencias de menos de un
  céntimo son empates), la más barata con nivel de riesgo ≤ k y las ofertas «sin evaluar» nunca
  se eligen. `src/logic/contract.test.ts` comprueba que reproduce `cheapest.json` (filas, orden,
  elecciones, costes y ahorros) sobre `src/data-sample/` y sobre `src/data/` si existe; en CI, el
  job `contract` lo corre con la salida de `haggler site-data` generada desde los fixtures de
  replay. El `cheapest.json` de ejemplo está calculado con `compare.py`.
- `src/logic/verdicts.ts` reetiqueta las ofertas para otro perfil de `feasibility.json` (o
  incluyendo los hallazgos pendientes de revisión): país de residencia → «Oficial», si no la ficha
  del perfil para (servicio, país), y sin ficha «Sin evaluar». Para el perfil por defecto sin
  pendientes reproduce las etiquetas de `offers.json` (`verdicts.test.ts`, también sobre
  `src/data/` si existe). El selector de perfil y la casilla se guardan en el hash (`p=`, `u=1`).
- `src/logic/format.ts`: formato es-ES con `Intl` (moneda, porcentajes, fechas en UTC).
- Sin CDNs, fuentes externas, analítica ni cookies.
