const fs = require("fs");
const path = require("path");
const { expect } = require("playwright/test");

const STAMP =
  process.env.CLOUD_EVIDENCE_STAMP ||
  new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const EVIDENCE_ROOT = path.join(
  __dirname,
  "..",
  "artifacts",
  "cloud-onboarding-evidence",
  STAMP,
);

function evidenceDir() {
  fs.mkdirSync(EVIDENCE_ROOT, { recursive: true });
  fs.writeFileSync(path.join(EVIDENCE_ROOT, "STAMP.txt"), STAMP);
  return EVIDENCE_ROOT;
}

function evidencePath(name) {
  evidenceDir();
  return path.join(EVIDENCE_ROOT, name);
}

async function shot(page, name) {
  await page.screenshot({ path: evidencePath(name), fullPage: true });
}

async function overflow(page, max = 32) {
  const extra = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  return extra;
}

async function assertNoOverflow(expect, page, max = 32) {
  expect(await overflow(page)).toBeLessThanOrEqual(max);
}

function uniqueStamp() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function uniqueEmail(prefix) {
  return `${prefix}.${uniqueStamp()}@company.example`;
}

async function csrf(page) {
  return page.locator('input[name="csrf_token"]').first().inputValue();
}

async function registerCloud(page, { name, email, password = "SecurePass1" }) {
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Work email").fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('input[name="password_confirm"]').fill(password);
  await page.locator('input[name="terms"]').check();
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL(/\/cloud\/(setup|pricing|instances)/);
}

async function loginCloud(page, { email, password = "SecurePass1" }) {
  await page.goto("/cloud/login");
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/cloud\//);
}

async function logoutCloud(page) {
  const tokenLoc = page.locator('form[action="/cloud/logout"] input[name="csrf_token"], input[name="csrf_token"]');
  if (await tokenLoc.count()) {
    const token = await tokenLoc.first().inputValue();
    await page.request.post("/cloud/logout", { form: { csrf_token: token } });
  }
  await page.goto("/cloud");
}

async function fillConfigure(page, fields) {
  await expect(page.getByRole("heading", { name: "Choose how you work" })).toBeVisible();
  if (fields.package) {
    await page.locator("label.pricing-card").filter({ hasText: fields.package }).locator('input[name="package_id"]').check();
  }
  if (fields.company) {
    await page.getByLabel("Legal company name").fill(fields.company);
  }
  if (fields.workspace) {
    await page.getByLabel("Workspace name").fill(fields.workspace);
  }
  if (fields.subdomain) {
    await page.getByLabel(/Workspace (URL|address)/).fill(fields.subdomain);
  }
  if (fields.country) {
    await page.selectOption('select[name="country"]', fields.country);
  }
  if (fields.language) {
    await page.selectOption('select[name="language"]', fields.language);
  }
  if (fields.users != null || fields.storage != null || fields.addon) {
    const details = page.locator("details.cloud-customize");
    if (!(await details.evaluate((el) => el.open))) {
      await details.locator("summary").click();
    }
  }
  if (fields.users != null) {
    await page.locator('input[name="required_users"]').fill(String(fields.users));
  }
  if (fields.storage != null) {
    await page.locator('input[name="required_storage_gb"]').fill(String(fields.storage));
  }
  if (fields.addon) {
    const card = page.locator("label.pricing-card").filter({ hasText: fields.addon });
    const box = card.locator('input[name="addon_ids"]');
    if (await box.count()) {
      await box.check();
    }
  }
}

async function continueConfigure(page) {
  await page.getByRole("button", { name: "Continue" }).click();
}

async function orderCount(page, runtime, email) {
  const res = await page.request.get(`${runtime.baseURL}/e2e/cloud/orders`, {
    params: { email },
    headers: { "x-e2e-secret": runtime.authSecret },
  });
  if (!res.ok()) {
    throw new Error(`e2e cloud orders HTTP ${res.status()}`);
  }
  return res.json();
}

module.exports = {
  STAMP,
  EVIDENCE_ROOT,
  evidenceDir,
  evidencePath,
  shot,
  overflow,
  assertNoOverflow,
  uniqueEmail,
  uniqueStamp,
  csrf,
  registerCloud,
  loginCloud,
  logoutCloud,
  fillConfigure,
  continueConfigure,
  orderCount,
};
