/**
 * QD1 Community Quick Demo — Playwright presentational + integration-pending contract.
 *
 * Presentational checks use setContent fixtures and do not require an Odoo runtime.
 * Authenticated route behavior is covered by the FastAPI HTTP suite. A browser
 * launch against real Odoo remains intentionally deferred.
 */

const { test, expect } = require("playwright/test");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../..");
const JS_PATH = path.join(ROOT, "app/static/js/quick-demo.js");
const CSS_SNIPPET = `
  .quick-demo__touch { min-height: 44px; min-width: 44px; display: inline-flex; align-items: center; justify-content: center; padding: 10px 16px; }
  .quick-demo__touch:focus, .quick-demo__touch:focus-visible { outline: 2px solid #714b67; outline-offset: 2px; }
  .quick-demo__progress-track { width: 100%; height: 10px; background: #e6edf3; }
  .quick-demo__progress-fill { height: 100%; background: #714b67; }
  body { margin: 0; font-family: sans-serif; }
  html[dir="rtl"] .quick-demo { font-size: 16px; }
`;

function catalogFixture({ enabled, lang = "en" }) {
  const dir = lang === "ar" ? "rtl" : "ltr";
  const tryNow = lang === "ar" ? "جرّب الآن" : "Try now";
  const trial = lang === "ar" ? "ابدأ تجربة مجانية لمدة 7 أيام" : "Start 7-day free trial";
  const paid = lang === "ar" ? "اختر الباقة" : "Choose package";
  const community = lang === "ar" ? "مجتمعي" : "Community";
  const compare = lang === "ar" ? "قارن الباقات" : "Compare packages";
  const hmsCtas = enabled
    ? `<div class="solution-card__cta">
        <span class="badge badge--community">${community}</span>
        <a class="btn btn--primary quick-demo__touch" href="/quick-demo/hms?edition=community" data-cta-quick-demo data-edition="community">${tryNow}</a>
        <a class="btn btn--ghost quick-demo__touch" href="/portal/trial/confirm" data-cta-trial>${trial}</a>
        <a class="btn btn--ghost quick-demo__touch" href="/solutions/hms#packages" data-cta-paid>${paid}</a>
      </div>`
    : `<div class="solution-card__cta"><a class="btn btn--primary" href="/solutions/hms">${compare}</a></div>`;
  return `<!DOCTYPE html><html lang="${lang}" dir="${dir}"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${CSS_SNIPPET}</style></head><body class="quick-demo">
    <main class="quick-demo">
      <article class="solution-card" data-solution-code="hms">
        <h2>Hospital Management</h2>
        ${hmsCtas}
      </article>
      <article class="solution-card" data-solution-code="sis">
        <h2>SIS</h2>
        <div class="solution-card__cta"><a class="btn btn--primary" href="/solutions/sis">${compare}</a></div>
      </article>
    </main></body></html>`;
}

function statusFixture({ state, canLaunch, progress, poll = false, lang = "en" }) {
  const dir = lang === "ar" ? "rtl" : "ltr";
  const openLabel = lang === "ar" ? "افتح نظام المستشفى" : "Open HMS";
  const launch =
    canLaunch
      ? `<a class="btn btn--primary quick-demo__touch" href="/quick-demo/open/demo" data-qd-cta="open" id="quick-demo-open-cta">${openLabel}</a>`
      : "";
  const recovery =
    state === "expired"
      ? `<p data-qd-recovery="expired">This Quick Demo session has expired.</p>`
      : state === "failed"
        ? `<p data-qd-recovery="failed">We could not finish preparing this Quick Demo.</p>`
        : "";
  return `<!DOCTYPE html><html lang="${lang}" dir="${dir}"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${CSS_SNIPPET}</style>
    </head><body>
    <div class="app-flow quick-demo" data-quick-demo-page="status"
      data-qd-state="${state}" data-qd-can-launch="${canLaunch ? "true" : "false"}"
      data-qd-progress="${progress}" data-status-url="${poll ? "/mock-status" : ""}"
      data-poll-enabled="${poll ? "true" : "false"}" data-qd-open-label="${openLabel}">
      <main>
        <h1>Preparing your Quick Demo</h1>
        <div class="quick-demo__live" role="status" aria-live="polite" aria-atomic="true" data-qd-live>
          <p class="quick-demo__status-label" data-qd-status-label>Status: ${state}</p>
          <div class="quick-demo__progress">
            <div class="quick-demo__progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}" data-qd-progressbar>
              <div class="quick-demo__progress-fill" style="width:${progress}%" data-qd-progress-fill></div>
            </div>
          </div>
        </div>
        ${recovery}
        <div class="quick-demo__actions" data-qd-actions>
          <div class="quick-demo__launch-slot" data-qd-launch-slot>${launch}</div>
          <a class="btn btn--ghost quick-demo__touch" href="/catalog?solution=hms">Back to HMS</a>
        </div>
      </main>
    </div>
    <script>${fs.readFileSync(JS_PATH, "utf8")}</script>
    </body></html>`;
}

test.describe("QD1 Quick Demo presentational contract", () => {
  test.describe.configure({ timeout: 60_000 });

  test("feature-disabled catalog has no Quick Demo CTA", async ({ page }) => {
    await page.setContent(catalogFixture({ enabled: false }), { waitUntil: "domcontentloaded" });
    await expect(page.locator("[data-cta-quick-demo]")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Compare packages" }).first()).toBeVisible();
  });

  test("enabled catalog shows Community Try now + Free Trial + Choose package", async ({ page }) => {
    await page.setContent(catalogFixture({ enabled: true }), { waitUntil: "domcontentloaded" });
    await expect(page.locator("[data-cta-quick-demo]")).toBeVisible();
    await expect(page.getByRole("link", { name: "Try now" })).toHaveAttribute(
      "href",
      /\/quick-demo\/hms\?edition=community/,
    );
    await expect(page.getByRole("link", { name: "Start 7-day free trial" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Choose package" })).toBeVisible();
    await expect(page.locator('a[href*="edition=enterprise"]')).toHaveCount(0);
  });

  test("Arabic catalog copy and RTL", async ({ page }) => {
    await page.setContent(catalogFixture({ enabled: true, lang: "ar" }), { waitUntil: "domcontentloaded" });
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("link", { name: "جرّب الآن" })).toBeVisible();
    await expect(page.getByRole("link", { name: "ابدأ تجربة مجانية لمدة 7 أيام" })).toBeVisible();
    await expect(page.getByRole("link", { name: "اختر الباقة" })).toBeVisible();
  });

  test("launch hidden before active; shown only when can_launch", async ({ page }) => {
    await page.setContent(
      statusFixture({ state: "creating_user", canLaunch: false, progress: 50 }),
      { waitUntil: "domcontentloaded" },
    );
    await expect(page.locator('[data-qd-cta="open"]')).toHaveCount(0);

    await page.setContent(
      statusFixture({ state: "active", canLaunch: true, progress: 100 }),
      { waitUntil: "domcontentloaded" },
    );
    await expect(page.locator('[data-qd-cta="open"]')).toBeVisible();
    await expect(page.getByRole("link", { name: "Open HMS" })).toHaveAttribute(
      "href",
      "/quick-demo/open/demo",
    );
  });

  test("terminal polling stops without reload loop", async ({ page }) => {
    let hits = 0;
    await page.route("**/mock-status", async (route) => {
      hits += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          state: hits >= 2 ? "active" : "health_check",
          progress_percent: hits >= 2 ? 100 : 80,
          status_message: hits >= 2 ? "ready" : "checking",
          can_launch: hits >= 2,
          open_href: hits >= 2 ? "/quick-demo/open/demo" : null,
        }),
      });
    });
    // Give the document an http origin so relative /mock-status fetches resolve.
    await page.route("http://127.0.0.1/", async (route) => {
      await route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>qd1</title>" });
    });
    await page.goto("http://127.0.0.1/");

    await page.setContent(
      statusFixture({ state: "health_check", canLaunch: false, progress: 80, poll: true }),
      { waitUntil: "domcontentloaded" },
    );

    await expect.poll(() => hits, { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
    await expect(page.locator('[data-quick-demo-page="status"]')).toHaveAttribute(
      "data-poll-enabled",
      "false",
    );
    const hitsAfterStop = hits;
    await page.waitForTimeout(2500);
    expect(hits).toBe(hitsAfterStop);
    await expect(page.locator('[data-qd-cta="open"]')).toBeVisible();
  });

  test("expired and failed recovery copy", async ({ page }) => {
    await page.setContent(statusFixture({ state: "expired", canLaunch: false, progress: 100 }), { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-qd-recovery="expired"]')).toBeVisible();
    await page.setContent(statusFixture({ state: "failed", canLaunch: false, progress: 40 }), { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-qd-recovery="failed"]')).toBeVisible();
  });

  test("no secret/internal fields in status DOM", async ({ page }) => {
    await page.setContent(statusFixture({ state: "creating_database", canLaunch: false, progress: 20 }), { waitUntil: "domcontentloaded" });
    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/database_name|filestore_path|127\.0\.0\.1|csrf_token|runtime_id/i);
  });

  test("360px viewport without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 640 });
    await page.setContent(catalogFixture({ enabled: true }), { waitUntil: "domcontentloaded" });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("keyboard focus and 44px touch targets", async ({ page }) => {
    await page.setContent(catalogFixture({ enabled: true }), { waitUntil: "domcontentloaded" });
    const cta = page.locator("[data-cta-quick-demo]");
    await cta.focus();
    await expect(cta).toBeFocused();
    const box = await cta.boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
  });

  test("aria-live status region present", async ({ page }) => {
    await page.setContent(statusFixture({ state: "requested", canLaunch: false, progress: 5 }), { waitUntil: "domcontentloaded" });
    await expect(page.locator('[aria-live="polite"]')).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});

test.describe("QD1 Quick Demo real-runtime E2E — deferred", () => {
  test.fixme("real Odoo launch requires the separately authorized QD1-F adapter", async () => {
    test.info().annotations.push({
      type: "runtime-deferred",
      description:
        "HTTP routes are verified with the fake adapter; no live Odoo launch is fabricated in QD1-E",
    });
  });
});
