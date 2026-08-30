/**
 * Capture Mock Odoo.sh demo screenshots with Playwright.
 * Usage: BASE_URL=http://localhost:8000 node scripts/capture-screenshots.cjs
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE_URL = process.env.BASE_URL || "http://localhost:8000";
const OUT_DIR = path.join(__dirname, "..", "docs", "screenshots");

const PAGES = [
  { name: "01-landing", path: "/" },
  { name: "02-login", path: "/login" },
  { name: "03-authorize", path: "/auth/github/authorize" },
  { name: "04-pricing", path: "/pricing" },
  { name: "05-checkout", path: "/checkout?plan=professional" },
  { name: "06-payment-success", path: "/payment/success?plan=professional" },
  { name: "07-deploy", path: "/deploy?subscription=MOSH-2026-ABCD-1234" },
  { name: "08-deploy-progress", path: "/deploy/progress" },
  { name: "09-projects", path: "/projects" },
  { name: "10-branches", path: "/project/alzaeem/branches" },
  { name: "11-build-details", path: "/project/alzaeem/build/21" },
  { name: "12-build-logs", path: "/project/alzaeem/build/21/logs" },
  { name: "13-build-connect", path: "/project/alzaeem/build/21/connect" },
  { name: "14-backups", path: "/project/alzaeem/backups" },
  { name: "15-settings", path: "/project/alzaeem/settings" },
  { name: "16-account", path: "/account" },
];

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });

  for (const item of PAGES) {
    const url = `${BASE_URL}${item.path}`;
    console.log(`Capturing ${item.name} <- ${url}`);
    await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(400);
    if (item.name === "08-deploy-progress") {
      await page.waitForTimeout(5000);
    }
    const out = path.join(OUT_DIR, `${item.name}.png`);
    await page.screenshot({ path: out, fullPage: true });
    console.log(`  -> ${out}`);
  }

  await browser.close();
  console.log("Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
