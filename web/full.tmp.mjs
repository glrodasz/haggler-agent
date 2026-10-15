import { chromium } from "playwright";
const b = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium" });
const [out, path = "", tag = "home"] = process.argv.slice(2);
for (const [n, w] of [["m", 390], ["d", 1440]]) {
  const p = await b.newPage({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1 });
  await p.goto("http://localhost:4321/haggler-agent/" + path, { waitUntil: "networkidle" });
  await p.waitForTimeout(500);
  await p.screenshot({ path: `${out}/${tag}-${n}.png`, fullPage: true });
}
await b.close();
