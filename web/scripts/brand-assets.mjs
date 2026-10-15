// Renders the PNG icons and the social preview card from the bowl isotype. The outputs are
// committed under public/, so this only runs when the logo or the tagline changes:
//   node scripts/brand-assets.mjs
// Uses sharp (installed with Astro) and the system Inter font for the card's text.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import { es } from "../src/i18n/es.ts";

const publicDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public");
const INK = "#0a0a0a";
const FG = "#ffffff";
const MUTED = "#a3a3a3";
const DOT = "#9a9a9a";

/** The isotype (LogoMark.astro) in a 56×44 box, filled with `color`. */
const bowl = (color) => `
  <mask id="cut"><rect width="56" height="44" fill="#fff"/><path d="M47.4 14.4 37.9 20.6M48.6 16.4 38.9 25.1M46.7 21.1 31 38" stroke="#000" stroke-width="1.9" stroke-linecap="round"/></mask>
  <path fill="${color}" mask="url(#cut)" d="M3 9C4 28 14 41.5 28 41.5S52 28 53 9A25 5.5 0 0 1 3 9Z"/>
  <ellipse cx="28" cy="9" rx="25" ry="5.5" fill="none" stroke="${color}" stroke-width="3"/>`;

/** White bowl centred on the black brand square; `inset` is the share of the side it spans. */
function iconSvg(size, inset, radius = 0) {
  const w = size * inset;
  const scale = w / 56;
  const x = (size - w) / 2;
  const y = (size - 44 * scale) / 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">
    <rect width="${size}" height="${size}" rx="${radius}" fill="${INK}"/>
    <g transform="translate(${x} ${y}) scale(${scale})">${bowl(FG)}</g></svg>`;
}

const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

function ogSvg() {
  // Tagline on two lines, ending in the grey dot the site's headings use.
  const words = es.site.tagline.split(" ");
  const cut = words.indexOf("por"); // "Precios de suscripciones" / "por país, con su riesgo"
  const lines = [words.slice(0, cut).join(" "), words.slice(cut).join(" ")];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630">
    <rect width="1200" height="630" fill="${INK}"/>
    <g transform="translate(80 80) scale(1.6)">${bowl(FG)}</g>
    <text x="186" y="133" font-family="Inter" font-weight="800" font-size="52" fill="${FG}" letter-spacing="-1">${es.site.name}</text>
    <text font-family="Inter" font-weight="800" font-size="76" fill="${FG}" letter-spacing="-2.5">
      <tspan x="80" y="330">${escape(lines[0])}</tspan>
      <tspan x="80" y="420">${escape(lines[1])}<tspan fill="${DOT}">.</tspan></tspan>
    </text>
    <text x="80" y="550" font-family="Inter" font-weight="500" font-size="30" fill="${MUTED}">glrodasz.github.io/haggler-agent</text>
    <text x="1120" y="550" text-anchor="end" font-family="Inter" font-weight="500" font-size="30" fill="${MUTED}">Sin analítica ni cookies</text>
  </svg>`;
}

async function png(svg, file) {
  const out = join(publicDir, file);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, await sharp(Buffer.from(svg)).png({ compressionLevel: 9 }).toBuffer());
  console.log(file);
}

await png(iconSvg(32, 0.86, 6), "favicon-32.png");
await png(iconSvg(180, 0.66), "apple-touch-icon.png");
// The PWA icons keep the bowl inside the maskable safe zone (the central 80%).
await png(iconSvg(192, 0.6), "icon-192.png");
await png(iconSvg(512, 0.6), "icon-512.png");
await png(ogSvg(), "og/haggler.png");
