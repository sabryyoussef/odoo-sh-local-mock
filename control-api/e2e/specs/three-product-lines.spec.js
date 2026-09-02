const { test, expect } = require("../fixtures");

async function shot(page, testInfo, name) {
  await page.screenshot({
    path: testInfo.outputPath(`${name}.png`),
    fullPage: true,
  });
}

test.describe("Three product lines — isolated UAT", () => {
  test.describe.configure({ timeout: 90_000 });

  test("homepage shows three independent journeys", async ({ page }, testInfo) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Ready Business Solutions" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Build Your ERP" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Ship Custom Odoo from Git" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Browse Solutions" }).first()).toHaveAttribute("href", "/solutions");
    await expect(page.getByRole("link", { name: "Configure Your ERP" }).first()).toHaveAttribute("href", "/cloud");
    await expect(page.getByRole("link", { name: "Developer Platform" }).first()).toHaveAttribute("href", "/platform");
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Ready Solutions" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Helpers ERP Cloud" })).toBeVisible();
    await shot(page, testInfo, "homepage-desktop");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Build Your ERP" })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(48);
    await shot(page, testInfo, "homepage-mobile");
  });

  test("Ready Solutions catalog stays vertical and non-developer", async ({ page }, testInfo) => {
    await page.goto("/solutions");
    await expect(page).toHaveURL(/\/catalog/);
    await expect(page.getByRole("heading", { name: /Veterinary Hospital/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Hospital Management/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /School Information System/ })).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
    await expect(page.locator('input[name="repository"]')).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Manual Build|Rebuild|CONNECT/ })).toHaveCount(0);
    await shot(page, testInfo, "ready-solutions-catalog");
    await page.getByRole("link", { name: /Veterinary Hospital/ }).first().click();
    await expect(page.getByRole("heading", { name: /Veterinary Hospital/ })).toBeVisible();
    await shot(page, testInfo, "ready-solutions-vet");
  });

  test("Developer Platform public journey still uses Git language", async ({ page }, testInfo) => {
    await page.goto("/platform");
    await expect(page.getByRole("heading", { name: "Developer Platform" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "GitHub integration" })).toBeVisible();
    await expect(page.getByRole("link", { name: /View Developer Plans/ })).toBeVisible();
    await shot(page, testInfo, "developer-platform");
    await page.goto("/platform/pricing");
    await expect(page.getByRole("heading", { name: /Developer Platform plan/ })).toBeVisible();
    await shot(page, testInfo, "developer-platform-pricing");
  });

  test("Helpers ERP Cloud demo journey without GitHub", async ({ page }, testInfo) => {
    const stamp = Date.now();
    const email = `uat.cloud.${stamp}@company.example`;

    await page.goto("/cloud");
    await expect(page.getByRole("heading", { name: "Helpers ERP Cloud" })).toBeVisible();
    await expect(page.getByText(/No coding or server administration required/)).toBeVisible();
    await expect(page.locator("body")).not.toContainText("commit SHA");
    await shot(page, testInfo, "cloud-overview");

    await page.goto("/cloud/pricing");
    await expect(page.getByRole("heading", { name: "Trial" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Starter" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Business" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Enterprise Cloud" })).toBeVisible();
    await expect(page.getByText("Presentation Only").first()).toBeVisible();
    await shot(page, testInfo, "cloud-pricing");

    await page.goto("/cloud/register");
    await page.getByLabel("Full name").fill("UAT Cloud Buyer");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Phone").fill("+20100000999");
    await page.getByLabel("Company name").fill("UAT Trading Co");
    await page.getByLabel("Country").fill("Egypt");
    await page.locator('input[name="password"]').fill("SecurePass1");
    await page.locator('input[name="password_confirm"]').fill("SecurePass1");
    await page.locator('input[name="terms"]').check();
    await shot(page, testInfo, "cloud-register");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/cloud\/setup\/plan/);
    await expect(page.getByRole("heading", { name: "Choose a Cloud plan" })).toBeVisible();
    await shot(page, testInfo, "cloud-setup-plan");

    await page.locator('input[name="billing_cycle"][value="annual"]').check();
    await page.locator("label.pricing-card").filter({ hasText: "Business" }).locator('input[name="plan_id"]').check();
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/cloud\/setup\/version/);
    await expect(page.getByRole("heading", { name: "Odoo 19 Community" })).toBeVisible();
    await shot(page, testInfo, "cloud-setup-version");
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/cloud\/setup\/package/);
    await page.locator("label.pricing-card").filter({ hasText: "Trading" }).locator('input[name="package_id"]').check();
    await shot(page, testInfo, "cloud-setup-package");
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/cloud\/setup\/company/);
    await page.getByLabel("Legal company name").fill("UAT Trading SAE");
    await page.getByLabel("Workspace name").fill("UAT Trading");
    await page.getByLabel("Requested subdomain").fill(`uat-trading-${stamp}`);
    await page.getByLabel("Country").fill("Egypt");
    await page.getByLabel("Currency").fill("EGP");
    await page.getByLabel("Language").fill("en_US");
    await page.getByLabel("Time zone").fill("Africa/Cairo");
    await page.getByLabel("Required users").fill("8");
    await page.getByLabel("Required storage (GB)").fill("15");
    await expect(page.locator('input[name="repository"]')).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
    await shot(page, testInfo, "cloud-setup-company");
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/cloud\/setup\/addons/);
    const egypt = page.locator("label.pricing-card").filter({ hasText: "Egyptian localization" });
    if (await egypt.locator('input[name="addon_ids"]').count()) {
      await egypt.locator('input[name="addon_ids"]').check();
    }
    await shot(page, testInfo, "cloud-setup-addons");
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/cloud\/setup\/review/);
    await expect(page.getByRole("heading", { name: /Review your Helpers ERP Cloud workspace/ })).toBeVisible();
    await expect(page.getByText("Presentation Only").first()).toBeVisible();
    await expect(page.getByText(/Total /).first()).toBeVisible();
    await shot(page, testInfo, "cloud-setup-review");
    await page.getByRole("link", { name: "Continue to demo checkout" }).click();

    await expect(page).toHaveURL(/\/cloud\/checkout/);
    await expect(page.getByRole("heading", { name: /Demo Checkout — No Real Charge/ })).toBeVisible();
    await expect(page.locator('input[name="card_number"]')).toHaveCount(0);
    await expect(page.locator('input[name="cvv"]')).toHaveCount(0);
    await shot(page, testInfo, "cloud-checkout");
    const idempotency = await page.locator('input[name="idempotency_key"]').inputValue();
    const csrf = await page.locator('form[action="/cloud/checkout"] input[name="csrf_token"]').inputValue();
    const first = await page.request.post("/cloud/checkout", {
      form: { csrf_token: csrf, idempotency_key: idempotency },
      maxRedirects: 0,
    });
    expect([302, 303]).toContain(first.status());
    const second = await page.request.post("/cloud/checkout", {
      form: { csrf_token: csrf, idempotency_key: idempotency },
      maxRedirects: 0,
    });
    expect([302, 303]).toContain(second.status());
    await page.goto("/cloud/checkout/success");
    await shot(page, testInfo, "cloud-checkout-success");

    await page.goto("/cloud/instances");
    await expect(page.getByRole("heading", { name: /Your Helpers ERP Cloud workspaces/ })).toBeVisible();
    await expect(page.getByText(/Open Odoo is unavailable/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Odoo" })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("Manual Build");
    await shot(page, testInfo, "cloud-instances");
  });
});
