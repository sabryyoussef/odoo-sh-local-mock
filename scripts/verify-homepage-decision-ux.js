const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:8000";
const OUT = path.join(
  __dirname,
  "../docs/reports/evidence/homepage-decision-ux/20260906T1700Z",
);

async function runVerification() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const results = {
    checks: [],
    cta_matrix: [],
    screenshots: [],
  };

  function logCheck(name, passed, detail = "") {
    results.checks.push({ name, passed, detail });
    if (!passed) {
      console.error(`FAIL: ${name} - ${detail}`);
    } else {
      console.log(`PASS: ${name} ${detail}`);
    }
  }

  // 1. Desktop EN (1280x800)
  const contextEn = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const pageEn = await contextEn.newPage();
  pageEn.on("pageerror", (err) => errors.push(`EN pageerror: ${err.message}`));
  pageEn.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`EN console error: ${msg.text()}`);
  });

  await pageEn.goto(`${BASE}/`, { waitUntil: "networkidle" });

  const enLang = await pageEn.locator("html").getAttribute("lang");
  const enDir = await pageEn.locator("html").getAttribute("dir");
  logCheck("Desktop EN lang/dir", enLang === "en" && enDir === "ltr", `lang=${enLang}, dir=${enDir}`);

  const enHeading = await pageEn.locator("h1.hero__title").innerText();
  logCheck(
    "Desktop EN Heading",
    enHeading.includes("Choose the right ERP path for your needs"),
    `Found: "${enHeading}"`,
  );

  const enSubheading = await pageEn.locator("p.hero__subtitle").innerText();
  logCheck(
    "Desktop EN Subheading",
    enSubheading.includes("Start with an industry-ready solution"),
    `Found: "${enSubheading}"`,
  );

  // Check 3 Cards on English
  const card1Title = await pageEn.locator("#card-ready-solutions .product-card__title").innerText();
  const card1Cta = await pageEn.locator("#card-ready-solutions .btn-hero--card").innerText();
  const card2Title = await pageEn.locator("#card-cloud .product-card__title").innerText();
  const card2Cta = await pageEn.locator("#card-cloud .btn-hero--card").innerText();
  const card3Title = await pageEn.locator("#card-platform .product-card__title").innerText();
  const card3Cta = await pageEn.locator("#card-platform .btn-hero--card").innerText();

  logCheck("Card 1 EN Title/CTA", card1Title.includes("Ready Solutions") && card1Cta.includes("Browse Ready Solutions"), `Title: ${card1Title}, CTA: ${card1Cta}`);
  logCheck("Card 2 EN Title/CTA", card2Title.includes("Helpers ERP Cloud") && card2Cta.includes("Configure My ERP"), `Title: ${card2Title}, CTA: ${card2Cta}`);
  logCheck("Card 3 EN Title/CTA", card3Title.includes("Developer Platform") && card3Cta.includes("Open Developer Platform"), `Title: ${card3Title}, CTA: ${card3Cta}`);

  // Test Header CTA scroll to #decision-paths
  const navCtaText = await pageEn.locator(".mkt-nav__try").innerText();
  logCheck("Nav CTA EN text", navCtaText.includes("Choose Your Solution"), `Nav CTA: ${navCtaText}`);
  await pageEn.locator(".mkt-nav__try").click();
  const currentUrl = pageEn.url();
  logCheck("Nav CTA scroll target", currentUrl.includes("#decision-paths"), `URL: ${currentUrl}`);

  // Screenshot Desktop EN
  const enShot = path.join(OUT, "01-desktop-en-100.png");
  await pageEn.screenshot({ path: enShot, fullPage: true });
  results.screenshots.push("01-desktop-en-100.png");

  // Keyboard focus test
  await pageEn.locator("#card-ready-solutions").focus();
  const focusShot = path.join(OUT, "02-desktop-en-focus-state.png");
  await pageEn.screenshot({ path: focusShot });
  results.screenshots.push("02-desktop-en-focus-state.png");
  await contextEn.close();

  // 2. Desktop AR (1280x800)
  const contextAr = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const pageAr = await contextAr.newPage();
  pageAr.on("pageerror", (err) => errors.push(`AR pageerror: ${err.message}`));
  pageAr.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`AR console error: ${msg.text()}`);
  });

  await pageAr.goto(`${BASE}/?lang=ar`, { waitUntil: "networkidle" });
  const arLang = await pageAr.locator("html").getAttribute("lang");
  const arDir = await pageAr.locator("html").getAttribute("dir");
  logCheck("Desktop AR lang/dir", arLang === "ar" && arDir === "rtl", `lang=${arLang}, dir=${arDir}`);

  const arHeading = await pageAr.locator("h1.hero__title").innerText();
  logCheck(
    "Desktop AR Heading",
    arHeading.includes("اختر مسار ERP المناسب لاحتياجك"),
    `Found: "${arHeading}"`,
  );

  const arSubheading = await pageAr.locator("p.hero__subtitle").innerText();
  logCheck(
    "Desktop AR Subheading",
    arSubheading.includes("ابدأ بحل جاهز لنشاطك"),
    `Found: "${arSubheading}"`,
  );

  // Check 3 Cards on Arabic
  const arCard1Title = await pageAr.locator("#card-ready-solutions .product-card__title").innerText();
  const arCard1Cta = await pageAr.locator("#card-ready-solutions .btn-hero--card").innerText();
  const arCard2Title = await pageAr.locator("#card-cloud .product-card__title").innerText();
  const arCard2Cta = await pageAr.locator("#card-cloud .btn-hero--card").innerText();
  const arCard3Title = await pageAr.locator("#card-platform .product-card__title").innerText();
  const arCard3Cta = await pageAr.locator("#card-platform .btn-hero--card").innerText();

  logCheck("Card 1 AR Title/CTA", arCard1Title.includes("حلول جاهزة") && arCard1Cta.includes("استعرض الحلول الجاهزة"), `Title: ${arCard1Title}, CTA: ${arCard1Cta}`);
  logCheck("Card 2 AR Title/CTA", arCard2Title.includes("سحابة Helpers ERP") && arCard2Cta.includes("جهّز نظام شركتي"), `Title: ${arCard2Title}, CTA: ${arCard2Cta}`);
  logCheck("Card 3 AR Title/CTA", arCard3Title.includes("منصة المطوّرين") || arCard3Title.includes("منصة المطورين") && (arCard3Cta.includes("افتح منصة المطورين") || arCard3Cta.includes("افتح منصة المطوّرين")), `Title: ${arCard3Title}, CTA: ${arCard3Cta}`);

  // Screenshot Desktop AR
  const arShot = path.join(OUT, "03-desktop-ar-100.png");
  await pageAr.screenshot({ path: arShot, fullPage: true });
  results.screenshots.push("03-desktop-ar-100.png");

  // 3. CTA Destination Matrix Verification in AR
  // Card 1
  const card1Href = await pageAr.locator("#card-ready-solutions .btn-hero--card").getAttribute("href");
  results.cta_matrix.push({ card: "Ready Solutions (AR)", href: card1Href, expected: "/solutions?lang=ar" });
  logCheck("Card 1 AR Link", card1Href.includes("/solutions?lang=ar"), `Href: ${card1Href}`);

  // Card 2
  const card2Href = await pageAr.locator("#card-cloud .btn-hero--card").getAttribute("href");
  results.cta_matrix.push({ card: "Helpers ERP Cloud (AR)", href: card2Href, expected: "/cloud?lang=ar" });
  logCheck("Card 2 AR Link", card2Href.includes("/cloud?lang=ar"), `Href: ${card2Href}`);

  // Card 3
  const card3Href = await pageAr.locator("#card-platform .btn-hero--card").getAttribute("href");
  results.cta_matrix.push({ card: "Developer Platform (AR)", href: card3Href, expected: "/platform?lang=ar" });
  logCheck("Card 3 AR Link", card3Href.includes("/platform?lang=ar"), `Href: ${card3Href}`);

  await contextAr.close();

  // 4. Zoom Testing: 125% and 150% Zoom
  // At 150% zoom on 1280px screen (effective width: 1280 / 1.5 = ~853px)
  const contextZoom150 = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1.5,
  });
  const pageZoom150 = await contextZoom150.newPage();
  await pageZoom150.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const zoom150Shot = path.join(OUT, "04-desktop-en-150-zoom.png");
  await pageZoom150.screenshot({ path: zoom150Shot, fullPage: true });
  results.screenshots.push("04-desktop-en-150-zoom.png");

  // Also check AR at 150% zoom
  await pageZoom150.goto(`${BASE}/?lang=ar`, { waitUntil: "networkidle" });
  const zoom150ArShot = path.join(OUT, "05-desktop-ar-150-zoom.png");
  await pageZoom150.screenshot({ path: zoom150ArShot, fullPage: true });
  results.screenshots.push("05-desktop-ar-150-zoom.png");

  const zoom150Overflow = await pageZoom150.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("150% Zoom No Overflow", zoom150Overflow <= 1, `Overflow: ${zoom150Overflow}px`);
  await contextZoom150.close();

  // 5. Mobile EN
  const mobileEnContext = await browser.newContext({ ...devices["Pixel 5"] });
  const pageMobEn = await mobileEnContext.newPage();
  await pageMobEn.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const mobEnOverflow = await pageMobEn.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("Mobile EN No Horizontal Scroll", mobEnOverflow <= 1, `Overflow: ${mobEnOverflow}px`);
  const mobEnShot = path.join(OUT, "06-mobile-en.png");
  await pageMobEn.screenshot({ path: mobEnShot, fullPage: true });
  results.screenshots.push("06-mobile-en.png");
  await mobileEnContext.close();

  // 6. Mobile AR
  const mobileArContext = await browser.newContext({ ...devices["Pixel 5"] });
  const pageMobAr = await mobileArContext.newPage();
  await pageMobAr.goto(`${BASE}/?lang=ar`, { waitUntil: "networkidle" });
  const mobArOverflow = await pageMobAr.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("Mobile AR No Horizontal Scroll", mobArOverflow <= 1, `Overflow: ${mobArOverflow}px`);
  const mobArShot = path.join(OUT, "07-mobile-ar.png");
  await pageMobAr.screenshot({ path: mobArShot, fullPage: true });
  results.screenshots.push("07-mobile-ar.png");
  await mobileArContext.close();

  await browser.close();

  if (errors.length > 0) {
    console.error("Browser console / page errors:", errors);
    throw new Error(`Browser errors encountered: ${errors.join(", ")}`);
  }

  const allPassed = results.checks.every((c) => c.passed);
  console.log("\n--- Verification Summary ---");
  console.log(`Total checks: ${results.checks.length}, All passed: ${allPassed}`);
  console.log(`Screenshots saved to: ${OUT}`);
  return results;
}

runVerification()
  .then((res) => {
    fs.writeFileSync(path.join(OUT, "verification-summary.json"), JSON.stringify(res, null, 2));
    process.exit(0);
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });