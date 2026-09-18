const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:8000";
const OUT = path.join(
  __dirname,
  "../docs/reports/evidence/arabic-homepage-restore/20260906T1517Z",
);

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const enLang = await page.locator("html").getAttribute("lang");
  const enDir = await page.locator("html").getAttribute("dir");
  if (enLang !== "en" || enDir !== "ltr") {
    throw new Error(`English homepage metadata failed: lang=${enLang} dir=${enDir}`);
  }
  await page.screenshot({ path: path.join(OUT, "desktop-en.png"), fullPage: true });

  await page.locator(".lang-switcher").getByRole("link", { name: "عربي" }).click();
  await page.waitForURL(/lang=ar/);
  const arLang = await page.locator("html").getAttribute("lang");
  const arDir = await page.locator("html").getAttribute("dir");
  const arTitle = await page.locator("h1.hero__title").innerText();
  if (arLang !== "ar" || arDir !== "rtl") {
    throw new Error(`Arabic after toggle failed: lang=${arLang} dir=${arDir}`);
  }
  if (!arTitle.includes("اختر مسار")) {
    throw new Error(`Arabic title missing: ${arTitle}`);
  }
  await page.screenshot({ path: path.join(OUT, "desktop-ar.png"), fullPage: true });

  await page.reload({ waitUntil: "networkidle" });
  const afterReload = await page.locator("html").evaluate((el) => ({
    lang: el.getAttribute("lang"),
    dir: el.getAttribute("dir"),
  }));
  if (afterReload.lang !== "ar" || afterReload.dir !== "rtl") {
    throw new Error(`Arabic lost after reload: ${JSON.stringify(afterReload)}`);
  }

  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const cookieOnly = await page.locator("html").evaluate((el) => ({
    lang: el.getAttribute("lang"),
    dir: el.getAttribute("dir"),
  }));
  if (cookieOnly.lang !== "ar" || cookieOnly.dir !== "rtl") {
    throw new Error(`Arabic lost without query: ${JSON.stringify(cookieOnly)}`);
  }

  await page.getByRole("navigation", { name: "القائمة الرئيسية" }).getByRole("link", { name: "الحلول الجاهزة" }).click();
  await page.waitForURL(/catalog/);
  const catalog = await page.locator("html").evaluate((el) => ({
    lang: el.getAttribute("lang"),
    dir: el.getAttribute("dir"),
    url: location.href,
  }));
  if (catalog.lang !== "ar" || catalog.dir !== "rtl" || !catalog.url.includes("lang=ar")) {
    throw new Error(`Arabic lost on catalog: ${JSON.stringify(catalog)}`);
  }

  await page.locator(".lang-switcher").getByRole("link", { name: "EN" }).click();
  await page.waitForURL(/lang=en/);
  const backEn = await page.locator("html").evaluate((el) => ({
    lang: el.getAttribute("lang"),
    dir: el.getAttribute("dir"),
  }));
  if (backEn.lang !== "en" || backEn.dir !== "ltr") {
    throw new Error(`English toggle failed: ${JSON.stringify(backEn)}`);
  }

  const mobile = await browser.newContext({ ...devices["Pixel 5"] });
  const mpage = await mobile.newPage();
  await mpage.goto(`${BASE}/?lang=ar`, { waitUntil: "networkidle" });
  const overflow = await mpage.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  if (overflow > 8) {
    throw new Error(`Horizontal scroll on mobile Arabic: ${overflow}`);
  }
  const switcherBox = await mpage.locator(".lang-switcher").boundingBox();
  if (!switcherBox || switcherBox.width < 8) {
    throw new Error("Language switcher not visible on mobile");
  }
  await mpage.screenshot({ path: path.join(OUT, "mobile-ar.png"), fullPage: true });
  await mobile.close();

  await browser.close();
  if (errors.length) {
    throw new Error(`Browser console errors:\n${errors.join("\n")}`);
  }
  console.log(JSON.stringify({ ok: true, out: OUT, screenshots: ["desktop-en.png", "desktop-ar.png", "mobile-ar.png"] }, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
