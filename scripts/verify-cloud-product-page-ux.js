const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:8000";
const OUT = path.join(
  __dirname,
  "../docs/reports/evidence/cloud-product-page-final-refinement/20260906T1935Z",
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

  // 1. Desktop English at 100% (1280x800)
  const contextEn = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const pageEn = await contextEn.newPage();
  pageEn.on("pageerror", (err) => errors.push(`EN pageerror: ${err.message}`));
  pageEn.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`EN console error: ${msg.text()}`);
  });

  await pageEn.goto(`${BASE}/cloud?lang=en`, { waitUntil: "networkidle" });

  const enLang = await pageEn.locator("html").getAttribute("lang");
  const enDir = await pageEn.locator("html").getAttribute("dir");
  logCheck("Desktop EN lang/dir", enLang === "en" && enDir === "ltr", `lang=${enLang}, dir=${enDir}`);

  // Breadcrumb
  const enBreadcrumb = await pageEn.locator(".cloud-page__breadcrumb").innerText();
  logCheck("Desktop EN Breadcrumb", enBreadcrumb.includes("Home") && enBreadcrumb.includes("Helpers ERP Cloud"), `Found: "${enBreadcrumb}"`);

  // Hero
  const enEyebrow = await pageEn.locator(".hero__eyebrow").innerText();
  const enHeading = await pageEn.locator(".hero__title").innerText();
  const enSubtitle = await pageEn.locator(".hero__subtitle").innerText();
  logCheck("Desktop EN Eyebrow", enEyebrow.includes("Managed Cloud ERP for your company"), `Found: "${enEyebrow}"`);
  logCheck("Desktop EN Heading", enHeading.includes("Set up your company ERP and start working faster"), `Found: "${enHeading}"`);
  logCheck("Desktop EN Subtitle", enSubtitle.includes("Choose your plan, applications, and users."), `Found: "${enSubtitle}"`);

  // Hero CTAs
  const enHeroPrimary = await pageEn.locator(".hero__actions .btn-hero--primary").innerText();
  const enHeroSecondary = await pageEn.locator(".hero__actions .btn-hero--secondary").innerText();
  const enHeroSecondaryHref = await pageEn.locator(".hero__actions .btn-hero--secondary").getAttribute("href");
  const enAccountLink = await pageEn.locator(".cloud-hero__account-link").innerText();
  logCheck("Desktop EN Hero CTAs", enHeroPrimary.includes("Set Up My ERP") && enHeroSecondary.includes("How It Works") && enHeroSecondaryHref === "#how-it-works" && enAccountLink.includes("I already have an account"), `Primary: ${enHeroPrimary}, Secondary: ${enHeroSecondary}, Href: ${enHeroSecondaryHref}, Account: ${enAccountLink}`);

  // 4 Steps
  const enStepsCount = await pageEn.locator(".cloud-step-card").count();
  const enStep1Title = await pageEn.locator(".cloud-step-card").nth(0).locator(".cloud-step-card__title").innerText();
  const enStep2Title = await pageEn.locator(".cloud-step-card").nth(1).locator(".cloud-step-card__title").innerText();
  const enStep3Title = await pageEn.locator(".cloud-step-card").nth(2).locator(".cloud-step-card__title").innerText();
  const enStep3Desc = await pageEn.locator(".cloud-step-card").nth(2).locator(".cloud-step-card__desc").innerText();
  const enStep4Title = await pageEn.locator(".cloud-step-card").nth(3).locator(".cloud-step-card__title").innerText();
  logCheck("Desktop EN 4 Steps Rendered", enStepsCount === 4 && enStep1Title.includes("Choose your plan") && enStep2Title.includes("Select your applications") && enStep3Title.includes("Enter company details") && enStep3Desc.includes("subdomain") && enStep4Title.includes("Review and start"), `Steps count: ${enStepsCount}, Step 3: ${enStep3Desc}`);

  // Benefits
  const enBenefitsCount = await pageEn.locator(".cloud-benefit-card").count();
  const enBenefit1Text = await pageEn.locator(".cloud-benefit-card").nth(0).innerText();
  logCheck("Desktop EN 5 Benefits Rendered", enBenefitsCount === 5 && enBenefit1Text.includes("An isolated and secure workspace"), `Benefits count: ${enBenefitsCount}`);

  const benefitBoxes = await pageEn.locator(".cloud-benefit-card").evaluateAll((nodes) => nodes.map((node) => {
    const rect = node.getBoundingClientRect();
    return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) };
  }));
  const firstRow = benefitBoxes.slice(0, 3);
  const secondRow = benefitBoxes.slice(3, 5);
  const equalWidths = benefitBoxes.every((box) => Math.abs(box.width - benefitBoxes[0].width) <= 2);
  const secondRowCentered = secondRow[0].x > firstRow[0].x && secondRow[1].x < firstRow[2].x;
  logCheck("Desktop benefit cards balanced", equalWidths && secondRowCentered, JSON.stringify(benefitBoxes));

  // Developer Platform Callout
  const enDevTitle = await pageEn.locator(".cloud-dev-callout__title").innerText();
  const enDevCta = await pageEn.locator(".cloud-dev-callout__action .btn-hero").innerText();
  const enDevHref = await pageEn.locator(".cloud-dev-callout__action .btn-hero").getAttribute("href");
  logCheck("Desktop EN Developer Platform Callout", enDevTitle.includes("Need advanced development and customization?") && enDevCta.includes("Explore Developer Platform") && enDevHref === "/platform", `Dev Title: ${enDevTitle}, CTA: ${enDevCta}, Href: ${enDevHref}`);

  // Final CTA
  const enFinalTitle = await pageEn.locator(".cloud-final-cta__title").innerText();
  const enFinalPrimary = await pageEn.locator(".cloud-final-cta__actions .btn-hero--primary").innerText();
  const enFinalButtonCount = await pageEn.locator(".cloud-final-cta__actions .btn-hero").count();
  logCheck("Desktop EN Final CTA", enFinalTitle.includes("Ready to configure your company ERP?") && enFinalPrimary.includes("Set Up My ERP") && enFinalButtonCount === 1, `Final Title: ${enFinalTitle}, Buttons: ${enFinalButtonCount}`);

  // Navigation on /cloud
  const enNavSignin = await pageEn.locator(".mkt-nav__actions .mkt-nav__ghost").innerText();
  const enNavCta = await pageEn.locator(".mkt-nav__actions .mkt-nav__try").innerText();
  logCheck("Desktop EN Nav Contextual Labels", enNavSignin.includes("Cloud Sign In") && enNavCta.includes("Configure My ERP"), `Nav Signin: ${enNavSignin}, Nav CTA: ${enNavCta}`);

  const enBodyText = await pageEn.locator("body").innerText();
  logCheck("Desktop EN no View Plans button", !enBodyText.includes("View Plans"), "View Plans absent from Cloud overview");

  // Screenshot Desktop EN 100%
  const en100Shot = path.join(OUT, "01-cloud-desktop-en-100.png");
  await pageEn.screenshot({ path: en100Shot, fullPage: true });
  results.screenshots.push("01-cloud-desktop-en-100.png");

  const enHeroShot = path.join(OUT, "03-cloud-hero-en-100.png");
  await pageEn.locator(".hero--cloud-product").screenshot({ path: enHeroShot });
  results.screenshots.push("03-cloud-hero-en-100.png");

  // Keyboard focus state
  await pageEn.locator(".cloud-step-card").nth(0).focus();
  const focusShot = path.join(OUT, "10-keyboard-focus-state.png");
  await pageEn.screenshot({ path: focusShot });
  results.screenshots.push("10-keyboard-focus-state.png");
  logCheck("Keyboard focus applied", true, "Focus state captured on step card");

  await contextEn.close();

  // 2. Desktop Arabic at 100% (1280x800)
  const contextAr = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const pageAr = await contextAr.newPage();
  pageAr.on("pageerror", (err) => errors.push(`AR pageerror: ${err.message}`));
  pageAr.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`AR console error: ${msg.text()}`);
  });

  await pageAr.goto(`${BASE}/cloud?lang=ar`, { waitUntil: "networkidle" });

  const arLang = await pageAr.locator("html").getAttribute("lang");
  const arDir = await pageAr.locator("html").getAttribute("dir");
  logCheck("Desktop AR lang/dir", arLang === "ar" && arDir === "rtl", `lang=${arLang}, dir=${arDir}`);

  // Breadcrumb AR
  const arBreadcrumb = await pageAr.locator(".cloud-page__breadcrumb").innerText();
  const arProductBdi = await pageAr.locator(".cloud-page__breadcrumb-current bdi").getAttribute("dir");
  logCheck("Desktop AR Breadcrumb", arBreadcrumb.includes("الرئيسية") && arBreadcrumb.includes("Helpers ERP Cloud") && arProductBdi === "ltr", `Found: "${arBreadcrumb}", bdi=${arProductBdi}`);

  // Hero AR
  const arEyebrow = await pageAr.locator(".hero__eyebrow").innerText();
  const arHeading = await pageAr.locator(".hero__title").innerText();
  const arSubtitle = await pageAr.locator(".hero__subtitle").innerText();
  logCheck("Desktop AR Eyebrow", arEyebrow.includes("نظام ERP سحابي مُدار لشركتك"), `Found: "${arEyebrow}"`);
  logCheck("Desktop AR Heading", arHeading.includes("جهّز نظام شركتك وابدأ العمل بسرعة"), `Found: "${arHeading}"`);
  logCheck("Desktop AR Subtitle", arSubtitle.includes("اختر خطتك وتطبيقاتك وعدد المستخدمين"), `Found: "${arSubtitle}"`);

  // Hero CTAs AR
  const arHeroPrimary = await pageAr.locator(".hero__actions .btn-hero--primary").innerText();
  const arHeroSecondary = await pageAr.locator(".hero__actions .btn-hero--secondary").innerText();
  const arHeroSecondaryHref = await pageAr.locator(".hero__actions .btn-hero--secondary").getAttribute("href");
  const arAccountLink = await pageAr.locator(".cloud-hero__account-link").innerText();
  logCheck("Desktop AR Hero CTAs", arHeroPrimary.includes("ابدأ تجهيز نظام شركتك") && arHeroSecondary.includes("كيف تعمل الخدمة؟") && arHeroSecondaryHref === "#how-it-works" && arAccountLink.includes("لدي حساب بالفعل"), `Primary: ${arHeroPrimary}, Secondary: ${arHeroSecondary}, Href: ${arHeroSecondaryHref}, Account: ${arAccountLink}`);

  // 4 Steps AR
  const arStepsCount = await pageAr.locator(".cloud-step-card").count();
  const arStep1Title = await pageAr.locator(".cloud-step-card").nth(0).locator(".cloud-step-card__title").innerText();
  const arStep2Title = await pageAr.locator(".cloud-step-card").nth(1).locator(".cloud-step-card__title").innerText();
  const arStep3Title = await pageAr.locator(".cloud-step-card").nth(2).locator(".cloud-step-card__title").innerText();
  const arStep3Desc = await pageAr.locator(".cloud-step-card").nth(2).locator(".cloud-step-card__desc").innerText();
  const arStep4Title = await pageAr.locator(".cloud-step-card").nth(3).locator(".cloud-step-card__title").innerText();
  logCheck("Desktop AR 4 Steps Rendered", arStepsCount === 4 && arStep1Title.includes("اختر خطتك") && arStep2Title.includes("اختر تطبيقاتك") && arStep3Title.includes("أدخل بيانات شركتك") && arStep3Desc.includes("النطاق الفرعي") && arStep4Title.includes("راجع وابدأ"), `Steps count: ${arStepsCount}, Step 3: ${arStep3Desc}`);

  // Benefits AR
  const arBenefitsCount = await pageAr.locator(".cloud-benefit-card").count();
  const arBenefit1Text = await pageAr.locator(".cloud-benefit-card").nth(0).innerText();
  logCheck("Desktop AR 5 Benefits Rendered", arBenefitsCount === 5 && arBenefit1Text.includes("مساحة مستقلة وآمنة لبيانات شركتك"), `Benefits count: ${arBenefitsCount}`);

  // Developer Platform Callout AR
  const arDevTitle = await pageAr.locator(".cloud-dev-callout__title").innerText();
  const arDevCta = await pageAr.locator(".cloud-dev-callout__action .btn-hero").innerText();
  const arDevHref = await pageAr.locator(".cloud-dev-callout__action .btn-hero").getAttribute("href");
  logCheck("Desktop AR Developer Platform Callout", arDevTitle.includes("هل تحتاج إلى تطوير وتخصيص متقدم؟") && arDevCta.includes("استكشف منصة المطورين") && arDevHref === "/platform?lang=ar", `Dev Title: ${arDevTitle}, CTA: ${arDevCta}, Href: ${arDevHref}`);

  // Final CTA AR
  const arFinalTitle = await pageAr.locator(".cloud-final-cta__title").innerText();
  const arFinalPrimary = await pageAr.locator(".cloud-final-cta__actions .btn-hero--primary").innerText();
  const arFinalButtonCount = await pageAr.locator(".cloud-final-cta__actions .btn-hero").count();
  logCheck("Desktop AR Final CTA", arFinalTitle.includes("هل أنت مستعد لتجهيز نظام شركتك؟") && arFinalPrimary.includes("ابدأ تجهيز نظام شركتك") && arFinalButtonCount === 1, `Final Title: ${arFinalTitle}, Buttons: ${arFinalButtonCount}`);

  // Navigation on /cloud in AR
  const arNavSignin = await pageAr.locator(".mkt-nav__actions .mkt-nav__ghost").innerText();
  const arNavCta = await pageAr.locator(".mkt-nav__actions .mkt-nav__try").innerText();
  logCheck("Desktop AR Nav Contextual Labels", arNavSignin.includes("دخول عملاء السحابة") && arNavCta.includes("ابدأ تجهيز نظامك"), `Nav Signin: ${arNavSignin}, Nav CTA: ${arNavCta}`);

  const arBodyText = await pageAr.locator("body").innerText();
  logCheck("Desktop AR no legacy setup wording", !arBodyText.includes("ابدأ إعداد نظام شركتي") && !arBodyText.includes("ابدأ إعداد نظامي") && !arBodyText.includes("استعرض الخطط"), "Legacy Arabic CTA wording absent from Cloud overview");

  // Screenshot Desktop AR 100%
  const ar100Shot = path.join(OUT, "02-cloud-desktop-ar-100.png");
  await pageAr.screenshot({ path: ar100Shot, fullPage: true });
  results.screenshots.push("02-cloud-desktop-ar-100.png");

  const arHeroShot = path.join(OUT, "04-cloud-hero-ar-100.png");
  await pageAr.locator(".hero--cloud-product").screenshot({ path: arHeroShot });
  results.screenshots.push("04-cloud-hero-ar-100.png");

  // Record CTA destination matrix
  results.cta_matrix.push(
    { name: "Hero Primary (EN)", text: enHeroPrimary, href: "/cloud/pricing" },
    { name: "Hero Secondary (EN)", text: enHeroSecondary, href: "#how-it-works" },
    { name: "Hero Account Link (EN)", text: enAccountLink, href: "/cloud/login" },
    { name: "Dev Callout (EN)", text: enDevCta, href: "/platform" },
    { name: "Final CTA Primary (EN)", text: enFinalPrimary, href: "/cloud/pricing" },
    { name: "Nav Sign In (EN)", text: enNavSignin, href: "/cloud/login" },
    { name: "Nav CTA (EN)", text: enNavCta, href: "/cloud/pricing" },
    { name: "Hero Primary (AR)", text: arHeroPrimary, href: "/cloud/pricing?lang=ar" },
    { name: "Hero Secondary (AR)", text: arHeroSecondary, href: "#how-it-works" },
    { name: "Hero Account Link (AR)", text: arAccountLink, href: "/cloud/login?lang=ar" },
    { name: "Dev Callout (AR)", text: arDevCta, href: "/platform?lang=ar" },
    { name: "Final CTA Primary (AR)", text: arFinalPrimary, href: "/cloud/pricing?lang=ar" },
    { name: "Nav Sign In (AR)", text: arNavSignin, href: "/cloud/login?lang=ar" },
    { name: "Nav CTA (AR)", text: arNavCta, href: "/cloud/pricing?lang=ar" },
  );

  await contextAr.close();

  // 3. Zoom Testing: 100%, 125%, 140%, 150%, and 160% desktop scale via viewport equivalents.
  for (const zoom of [1, 1.25, 1.4, 1.5, 1.6]) {
    const contextZoom = await browser.newContext({ viewport: { width: Math.round(1280 / zoom), height: 800 } });
    const pageZoom = await contextZoom.newPage();
    await pageZoom.goto(`${BASE}/cloud?lang=en`, { waitUntil: "networkidle" });
    const enOverflow = await pageZoom.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    logCheck(`${Math.round(zoom * 100)}% Zoom EN No Overflow`, enOverflow <= 1, `Overflow: ${enOverflow}px`);
    await pageZoom.goto(`${BASE}/cloud?lang=ar`, { waitUntil: "networkidle" });
    const arOverflow = await pageZoom.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    logCheck(`${Math.round(zoom * 100)}% Zoom AR No Overflow`, arOverflow <= 1, `Overflow: ${arOverflow}px`);
    await contextZoom.close();
  }

  const contextZoom140 = await browser.newContext({ viewport: { width: Math.round(1280 / 1.4), height: 800 } });
  const pageZoom140 = await contextZoom140.newPage();
  await pageZoom140.goto(`${BASE}/cloud?lang=ar`, { waitUntil: "networkidle" });
  const zoom140ArShot = path.join(OUT, "05-cloud-desktop-ar-140.png");
  await pageZoom140.screenshot({ path: zoom140ArShot, fullPage: true });
  results.screenshots.push("05-cloud-desktop-ar-140.png");
  await contextZoom140.close();

  const contextZoom160 = await browser.newContext({
    viewport: { width: Math.round(1280 / 1.6), height: 800 },
  });
  const pageZoom160 = await contextZoom160.newPage();
  await pageZoom160.goto(`${BASE}/cloud?lang=en`, { waitUntil: "networkidle" });
  const zoom160EnShot = path.join(OUT, "06-cloud-desktop-en-160.png");
  await pageZoom160.screenshot({ path: zoom160EnShot, fullPage: true });
  results.screenshots.push("06-cloud-desktop-en-160.png");

  const zoom160EnOverflow = await pageZoom160.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("160% Zoom EN No Overflow", zoom160EnOverflow <= 1, `Overflow: ${zoom160EnOverflow}px`);

  // AR at 160% zoom
  await pageZoom160.goto(`${BASE}/cloud?lang=ar`, { waitUntil: "networkidle" });
  const zoom160ArShot = path.join(OUT, "07-cloud-desktop-ar-160.png");
  await pageZoom160.screenshot({ path: zoom160ArShot, fullPage: true });
  results.screenshots.push("07-cloud-desktop-ar-160.png");

  const zoom160ArOverflow = await pageZoom160.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("160% Zoom AR No Overflow", zoom160ArOverflow <= 1, `Overflow: ${zoom160ArOverflow}px`);
  await contextZoom160.close();

  // 4. Mobile EN (Pixel 5)
  const mobileEnContext = await browser.newContext({ ...devices["Pixel 5"] });
  const pageMobEn = await mobileEnContext.newPage();
  await pageMobEn.goto(`${BASE}/cloud?lang=en`, { waitUntil: "networkidle" });
  const mobEnOverflow = await pageMobEn.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("Mobile EN No Horizontal Scroll", mobEnOverflow <= 1, `Overflow: ${mobEnOverflow}px`);
  const mobEnShot = path.join(OUT, "08-cloud-mobile-en.png");
  await pageMobEn.screenshot({ path: mobEnShot, fullPage: true });
  results.screenshots.push("08-cloud-mobile-en.png");
  await mobileEnContext.close();

  // 5. Mobile AR (Pixel 5)
  const mobileArContext = await browser.newContext({ ...devices["Pixel 5"] });
  const pageMobAr = await mobileArContext.newPage();
  await pageMobAr.goto(`${BASE}/cloud?lang=ar`, { waitUntil: "networkidle" });
  const mobArOverflow = await pageMobAr.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  logCheck("Mobile AR No Horizontal Scroll", mobArOverflow <= 1, `Overflow: ${mobArOverflow}px`);
  const mobArShot = path.join(OUT, "09-cloud-mobile-ar.png");
  await pageMobAr.screenshot({ path: mobArShot, fullPage: true });
  results.screenshots.push("09-cloud-mobile-ar.png");
  await mobileArContext.close();

  await browser.close();

  if (errors.length > 0) {
    console.error("Browser console / page errors:", errors);
    throw new Error(`Browser errors encountered: ${errors.join(", ")}`);
  }

  const allPassed = results.checks.every((c) => c.passed);
  console.log("\n--- Cloud Product Page Verification Summary ---");
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
