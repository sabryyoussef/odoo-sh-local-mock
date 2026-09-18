const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:8000";
const OUT = path.join(
  __dirname,
  "../docs/reports/evidence/cloud-pricing-page-ux/20260906T2030Z",
);

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const results = { checks: [], cta_matrix: [], screenshots: [] };

  function check(name, passed, detail = "") {
    results.checks.push({ name, passed, detail });
    console.log(`${passed ? "PASS" : "FAIL"}: ${name}${detail ? ` ${detail}` : ""}`);
    if (!passed) throw new Error(`${name}: ${detail}`);
  }

  function wireErrors(page, label) {
    page.on("pageerror", (err) => errors.push(`${label} pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`${label} console: ${msg.text()}`);
    });
  }

  async function overflow(page) {
    return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  }

  async function savePage(page, fileName) {
    await page.screenshot({ path: path.join(OUT, fileName), fullPage: true });
    results.screenshots.push(fileName);
  }

  async function saveLocator(locator, fileName) {
    await locator.screenshot({ path: path.join(OUT, fileName) });
    results.screenshots.push(fileName);
  }

  const contextEn = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  const pageEn = await contextEn.newPage();
  wireErrors(pageEn, "pricing-en");
  await pageEn.goto(`${BASE}/cloud/pricing?lang=en`, { waitUntil: "networkidle" });

  check("EN metadata", await pageEn.locator("html").getAttribute("lang") === "en" && await pageEn.locator("html").getAttribute("dir") === "ltr");
  check("EN breadcrumb", (await pageEn.locator(".cloud-page__breadcrumb").innerText()).includes("Home") && (await pageEn.locator(".cloud-page__breadcrumb").innerText()).includes("Helpers ERP Cloud"));
  check("EN progress active", (await pageEn.locator(".cloud-pricing-progress__label").innerText()).includes("Step 1 of 4") && await pageEn.locator(".cloud-pricing-progress__steps .is-active").count() === 1);
  check("EN heading", (await pageEn.locator("#cloud-pricing-title").innerText()).includes("Choose the right plan for your company"));
  check("EN demo notice", (await pageEn.locator(".cloud-demo-notice").innerText()).includes("No real payment will be collected."));
  check("EN header CTA anchor", await pageEn.locator(".mkt-nav__try").innerText() === "Choose a Plan" && await pageEn.locator(".mkt-nav__try").getAttribute("href") === "#plans");

  const enCards = pageEn.locator(".cloud-pricing-card");
  check("EN four cards", await enCards.count() === 4);
  check("EN order", (await enCards.nth(0).innerText()).includes("Free Trial") && (await enCards.nth(1).innerText()).includes("Starter") && (await enCards.nth(2).innerText()).includes("Business") && (await enCards.nth(3).innerText()).includes("Enterprise Cloud"));
  check("EN business recommended", (await pageEn.locator(".cloud-pricing-card--business").innerText()).includes("Most Popular"));
  check("EN trial no monthly price", !(await pageEn.locator(".cloud-pricing-card--trial").innerText()).includes("per month"));
  check("EN enterprise quote", (await pageEn.locator(".cloud-pricing-card--enterprise").innerText()).includes("Custom quote") && (await pageEn.locator(".cloud-pricing-card--enterprise .cloud-pricing-card__cta").innerText()).includes("Request a Quote"));
  check("EN included and max split", (await pageEn.locator(".cloud-pricing-card--starter").innerText()).includes("5 users included") && (await pageEn.locator(".cloud-pricing-card--starter").innerText()).includes("Can be increased up to 10 users"));
  check("EN monthly values", (await pageEn.locator(".cloud-pricing-card--starter").innerText()).includes("$49.00 USD") && (await pageEn.locator(".cloud-pricing-card--business").innerText()).includes("$149.00 USD"));

  const enCtas = await pageEn.locator(".cloud-pricing-card__cta").evaluateAll((nodes) => nodes.map((node) => ({ text: node.textContent.trim().replace(/\s+/g, " "), href: node.getAttribute("href") })));
  results.cta_matrix.push(...enCtas.map((row) => ({ lang: "en", ...row })));
  check("EN CTA routes", enCtas.some((row) => row.text === "Start Free Trial" && row.href.includes("/cloud/register?plan=trial&cycle=monthly")) && enCtas.some((row) => row.text === "Request a Quote" && row.href.includes("/cloud/register?plan=enterprise&cycle=monthly")));

  await pageEn.locator(".mkt-nav__try").click();
  check("EN header CTA scroll URL", pageEn.url().includes("#plans"));
  await savePage(pageEn, "01-pricing-full-en-100.png");
  await saveLocator(pageEn.locator(".cloud-pricing-grid"), "03-pricing-cards-en-100.png");
  await saveLocator(pageEn.locator(".cloud-billing-form"), "05-pricing-monthly-en.png");
  await pageEn.locator(".cloud-pricing-card--starter").focus();
  await savePage(pageEn, "13-pricing-keyboard-focus.png");
  await contextEn.close();

  const contextAr = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  const pageAr = await contextAr.newPage();
  wireErrors(pageAr, "pricing-ar");
  await pageAr.goto(`${BASE}/cloud/pricing?lang=ar`, { waitUntil: "networkidle" });

  check("AR metadata", await pageAr.locator("html").getAttribute("lang") === "ar" && await pageAr.locator("html").getAttribute("dir") === "rtl");
  check("AR bdi product", await pageAr.locator(".cloud-page__breadcrumb bdi").getAttribute("dir") === "ltr" && (await pageAr.locator(".cloud-page__breadcrumb").innerText()).includes("Helpers ERP Cloud"));
  check("AR heading", (await pageAr.locator("#cloud-pricing-title").innerText()).includes("اختر الخطة المناسبة لشركتك"));
  check("AR demo notice", (await pageAr.locator(".cloud-demo-notice").innerText()).includes("لن يتم تحصيل أي مبلغ حقيقي") && !(await pageAr.locator("body").innerText()).includes("خصم حقيقي"));
  check("AR header CTA anchor", await pageAr.locator(".mkt-nav__try").innerText() === "اختر خطة" && await pageAr.locator(".mkt-nav__try").getAttribute("href") === "#plans");
  check("AR localized cards", (await pageAr.locator(".cloud-pricing-card--trial").innerText()).includes("تجربة مجانية") && (await pageAr.locator(".cloud-pricing-card--business").innerText()).includes("الأكثر اختيارًا") && (await pageAr.locator(".cloud-pricing-card--enterprise").innerText()).includes("عرض سعر مخصص"));
  check("AR included and max split", (await pageAr.locator(".cloud-pricing-card--starter").innerText()).includes("يشمل 5 مستخدمين") && (await pageAr.locator(".cloud-pricing-card--starter").innerText()).includes("يمكن الزيادة حتى 10 مستخدمين"));
  check("AR plan name bidi", await pageAr.locator(".cloud-pricing-card--starter h3 bdi").getAttribute("dir") === "ltr" && await pageAr.locator(".cloud-pricing-card--business h3 bdi").getAttribute("dir") === "ltr");

  const arCtas = await pageAr.locator(".cloud-pricing-card__cta").evaluateAll((nodes) => nodes.map((node) => ({ text: node.textContent.trim().replace(/\s+/g, " "), href: node.getAttribute("href") })));
  results.cta_matrix.push(...arCtas.map((row) => ({ lang: "ar", ...row })));
  check("AR CTA routes preserve locale", arCtas.some((row) => row.text.includes("ابدأ التجربة المجانية") && row.href.includes("lang=ar")) && arCtas.some((row) => row.text.includes("اطلب عرض سعر") && row.href.includes("plan=enterprise") && row.href.includes("lang=ar")));

  await savePage(pageAr, "02-pricing-full-ar-100.png");
  await saveLocator(pageAr.locator(".cloud-pricing-grid"), "04-pricing-cards-ar-100.png");
  await saveLocator(pageAr.locator(".cloud-billing-form"), "06-pricing-monthly-ar.png");
  await contextAr.close();

  const contextAnnual = await browser.newContext({ viewport: { width: 1280, height: 820 } });
  const pageAnnualEn = await contextAnnual.newPage();
  wireErrors(pageAnnualEn, "pricing-annual-en");
  await pageAnnualEn.goto(`${BASE}/cloud/pricing?cycle=annual&lang=en`, { waitUntil: "networkidle" });
  check("EN annual selected", await pageAnnualEn.locator('input[name="cycle"][value="annual"]').isChecked());
  check("EN annual totals and savings", (await pageAnnualEn.locator(".cloud-pricing-card--starter").innerText()).includes("$490.00 USD") && (await pageAnnualEn.locator(".cloud-pricing-card--starter").innerText()).includes("Save $98.00 USD"));
  await saveLocator(pageAnnualEn.locator(".cloud-billing-form"), "07-pricing-annual-en.png");

  await pageAnnualEn.goto(`${BASE}/cloud/pricing?cycle=annual&lang=ar`, { waitUntil: "networkidle" });
  check("AR annual selected", await pageAnnualEn.locator('input[name="cycle"][value="annual"]').isChecked());
  check("AR annual totals and savings", (await pageAnnualEn.locator(".cloud-pricing-card--starter").innerText()).includes("$490.00 USD") && (await pageAnnualEn.locator(".cloud-pricing-card--starter").innerText()).includes("توفير $98.00 USD"));
  await saveLocator(pageAnnualEn.locator(".cloud-billing-form"), "08-pricing-annual-ar.png");
  await contextAnnual.close();

  for (const zoom of [1, 1.25, 1.4, 1.5, 1.6]) {
    for (const lang of ["en", "ar"]) {
      const context = await browser.newContext({ viewport: { width: Math.round(1280 / zoom), height: 820 } });
      const page = await context.newPage();
      wireErrors(page, `pricing-${lang}-${zoom}`);
      await page.goto(`${BASE}/cloud/pricing?lang=${lang}`, { waitUntil: "networkidle" });
      check(`${Math.round(zoom * 100)}% ${lang.toUpperCase()} no overflow`, await overflow(page) <= 1, `overflow=${await overflow(page)}px`);
      if (lang === "ar" && zoom === 1.4) await savePage(page, "09-pricing-ar-140.png");
      if (lang === "en" && zoom === 1.6) await savePage(page, "10-pricing-en-160.png");
      if (lang === "ar" && zoom === 1.6) await savePage(page, "11-pricing-ar-160.png");
      await context.close();
    }
  }

  const mobileEn = await browser.newContext({ ...devices["Pixel 5"] });
  const pageMobileEn = await mobileEn.newPage();
  wireErrors(pageMobileEn, "pricing-mobile-en");
  await pageMobileEn.goto(`${BASE}/cloud/pricing?lang=en`, { waitUntil: "networkidle" });
  check("Mobile EN no horizontal scroll", await overflow(pageMobileEn) <= 1, `overflow=${await overflow(pageMobileEn)}px`);
  const mobileCardWidthsEn = await pageMobileEn.locator(".cloud-pricing-card").evaluateAll((nodes) => nodes.every((node) => node.getBoundingClientRect().width <= document.documentElement.clientWidth));
  check("Mobile EN cards single-column fit", mobileCardWidthsEn);
  await savePage(pageMobileEn, "12-pricing-mobile-en.png");
  await mobileEn.close();

  const mobileAr = await browser.newContext({ ...devices["Pixel 5"] });
  const pageMobileAr = await mobileAr.newPage();
  wireErrors(pageMobileAr, "pricing-mobile-ar");
  await pageMobileAr.goto(`${BASE}/cloud/pricing?lang=ar`, { waitUntil: "networkidle" });
  check("Mobile AR no horizontal scroll", await overflow(pageMobileAr) <= 1, `overflow=${await overflow(pageMobileAr)}px`);
  const mobileCardWidthsAr = await pageMobileAr.locator(".cloud-pricing-card").evaluateAll((nodes) => nodes.every((node) => node.getBoundingClientRect().width <= document.documentElement.clientWidth));
  check("Mobile AR cards single-column fit", mobileCardWidthsAr);
  await savePage(pageMobileAr, "14-pricing-mobile-ar.png");
  await mobileAr.close();

  await browser.close();
  if (errors.length) throw new Error(`Browser errors:\n${errors.join("\n")}`);

  fs.writeFileSync(path.join(OUT, "verification-summary.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ ok: true, checks: results.checks.length, out: OUT, screenshots: results.screenshots }, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
