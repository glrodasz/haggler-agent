# Sitio web de haggler

Sitio estático (Astro 7 + Preact, TypeScript) que publica los precios recogidos por el agente en
GitHub Pages: <https://haggler.guillermorodas.com/>. Interfaz en inglés (idioma principal,
sin prefijo), español (`/es/`) y portugués (`/pt/`); ver [Idiomas](#idiomas).

## Páginas

| Ruta | Contenido |
|---|---|
| `/` | Comparador (única isla interactiva): servicios, plan, perfil de factibilidad, hallazgos pendientes de revisión (opcional), riesgo máximo aceptado, moneda, ahorro neto de comisión de cambio |
| `/services/[id]/` | Precios por país y plan, factibilidad del perfil por defecto (fuente, evidencia, supuestos y motivos) y paquetes vigentes que incluyen el servicio (también por su precio observado o `covers`), más los que solo dan un descuento o meses gratis en él |
| `/countries/[cc]/` | Todas las ofertas de un país, sus paquetes vigentes (estado, precio, ahorro y veredicto por perfil) y, plegadas, las ofertas vencidas |
| `/changes/` | Cambios de precio de los últimos 90 días |
| `/methodology/` | Fuentes, ejes de riesgo, supuestos arriesgados, cobertura, atribuciones y aviso legal |

Cada ruta existe también bajo `/es/` y `/pt/` (`/es/services/netflix/`). Las antiguas rutas en
español sin prefijo (`/servicios/…`, `/paises/…`, `/paquetes/`, `/cambios/`, `/metodologia/`)
redirigen a su página en `/es/`, conservando la consulta y el hash.

## Idiomas

- `src/i18n/locale.ts`: configuración (`LOCALES`, `DEFAULT_LOCALE = "en"`) y las funciones puras
  (`localizePath`, `resolveLocaleRedirect`, `alternateLinks`…). Nada más reimplementa esa lógica.
- La URL decide el idioma de la página. Solo una primera visita sin prefijo se redirige: a la
  elección guardada, si no al primer idioma soportado de `navigator.languages`, si no inglés.
  Lo hace `src/i18n/boot.ts`, empaquetado con rolldown e incrustado al inicio del `<head>`
  (`location.replace`, antes del primer pintado).
- La elección del selector de idioma (pie de página) se guarda en `localStorage` (`locale`),
  no en una cookie: el sitio promete «sin cookies» y en GitHub Pages nadie la leería.
- Textos: `src/i18n/en.ts` es la fuente del tipo; `es.ts` y `pt.ts` deben tener la misma forma
  (`satisfies Messages`). Componentes y páginas leen el idioma con `localeOfUrl(Astro.url)` o
  la prop `locale`; las islas lo reciben como prop y lo pasan por contexto.
- Nombres: países con `Intl.DisplayNames` (en español, `name_es` del catálogo); etiquetas,
  planes y perfiles desde el diccionario. Las notas, motivos y supuestos de la investigación y de
  los datos curados solo existen en español y se marcan con `lang="es"`.
- Números y fechas: `formatFor(locale)` (`src/logic/format.ts`).

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
npm run dev       # http://localhost:4321/
npm run check     # astro check (tipos)
npm test          # vitest: lógica del comparador, formato, datos de ejemplo
npm run build     # genera dist/
npm run preview
```

Variables de entorno para el build: `SITE_URL` (por defecto `https://haggler.guillermorodas.com`) y
`BASE_PATH` (por defecto `/`; usa `/haggler-agent` para servirlo sin dominio propio en
`glrodasz.github.io`). `publish.yml` las toma de `actions/configure-pages`. En CI conviene
`ASTRO_TELEMETRY_DISABLED=1`.

## Lógica

- `src/logic/compare.ts` replica `src/haggler/compare.py`: coste mensual en EUR + comisión de
  cambio si la moneda no es la de residencia, redondeado a céntimos (diferencias de menos de un
  céntimo son empates), la más barata con nivel de riesgo ≤ k y las ofertas «sin evaluar» nunca
  se eligen. `src/logic/contract.test.ts` comprueba que reproduce `cheapest.json` (filas, orden,
  elecciones, costes y ahorros) sobre `src/data-sample/` y sobre `src/data/` si existe; en CI, el
  job `contract` lo corre con la salida de `haggler site-data` generada desde los fixtures de
  replay. El `cheapest.json` de ejemplo está calculado con `compare.py`, y el `bundles.json` de
  ejemplo con el `publish.py` real sobre las ofertas y los tipos de cambio de ejemplo (más una fila
  vencida ilustrativa, `meli_plus_mx_mega_lanzamiento`, que solo existe en el ejemplo). Las mismas
  pruebas comprueban las reglas de `bundles.json` v2 (costes, estado y veredictos) en ambos.
- `src/logic/verdicts.ts` reetiqueta las ofertas para otro perfil de `feasibility.json` (o
  incluyendo los hallazgos pendientes de revisión): país de residencia → «Oficial», si no la ficha
  del perfil para (servicio, país), y sin ficha «Sin evaluar». Para el perfil por defecto sin
  pendientes reproduce las etiquetas de `offers.json` (`verdicts.test.ts`, también sobre
  `src/data/` si existe). El selector de perfil y la casilla se guardan en el hash (`p=`, `u=1`).
- `src/logic/format.ts`: formato por idioma con `Intl` (moneda, porcentajes, fechas en UTC);
  en español y portugués, miles con espacio duro y coma decimal.
- Sin CDNs, fuentes externas, analítica ni cookies.
