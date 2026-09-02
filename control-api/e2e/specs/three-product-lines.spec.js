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
    await expect(page.getByRole("link", { name: "Cloud sign in" })).toHaveAttribute("href", "/cloud/login");
    await expect(page.getByRole("link", { name: "Developer sign in" })).toHaveAttribute("href", "/login");
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
    await expect(page.locator('.mkt-nav__actions a[href="/login"]')).toBeVisible();
    await expect(page.getByRole("link", { name: /View Developer Plans/ })).toBeVisible();
    await shot(page, testInfo, "developer-platform");
    await page.goto("/platform/pricing");
    await expect(page.getByRole("heading", { name: /Developer Platform plan/ })).toBeVisible();
    await shot(page, testInfo, "developer-platform-pricing");
    await page.goto("/login");
    await expect(page.getByRole("link", { name: /Sign in with GitHub/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /Helpers ERP Cloud sign in/i })).toHaveAttribute("href", "/cloud/login");
    await shot(page, testInfo, "developer-github-login");
  });

  test("Helpers ERP Cloud demo journey without GitHub", async ({ page }, testInfo) => {
    const stamp = Date.now();
    const email = `uat.cloud.${stamp}@company.example`;

    await page.goto("/cloud");
    await expect(page.getByRole("heading", { name: "Helpers ERP Cloud" })).toBeVisible();
    await expect(page.getByText(/No coding or server administration required/)).toBeVisible();
    await expect(page.getByRole("link", { name: "View plans and start" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toHaveAttribute("href", "/cloud/login");
    await expect(page.locator("body")).not.toContainText("commit SHA");
    await shot(page, testInfo, "cloud-overview");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/cloud");
    await shot(page, testInfo, "cloud-overview-mobile");
    await page.setViewportSize({ width: 1280, height: 720 });

    await page.goto("/cloud/pricing");
    await expect(page.getByRole("heading", { name: "Trial" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Starter" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Business" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Enterprise Cloud" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Start free" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Choose Business" })).toBeVisible();
    await expect(page.getByText("Presentation Only").first()).toBeVisible();
    await shot(page, testInfo, "cloud-pricing");

    await page.getByRole("link", { name: "Choose Business" }).click();
    await expect(page).toHaveURL(/\/cloud\/register/);
    await page.getByLabel("Full name").fill("UAT Cloud Buyer");
    await page.getByLabel("Work email").fill(email);
    await page.locator('input[name="password"]').fill("SecurePass1");
    await page.locator('input[name="password_confirm"]').fill("SecurePass1");
    await page.locator('input[name="terms"]').check();
    await shot(page, testInfo, "cloud-register");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await expect(page.getByRole("heading", { name: "Choose how you work" })).toBeVisible();
    await expect(page.getByText("Business", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Customize your plan")).toBeVisible();
    await expect(page.locator('input[name="addon_ids"]')).not.toHaveCount(0);
    await shot(page, testInfo, "cloud-setup-configure");
    await page.setViewportSize({ width: 390, height: 844 });
    await shot(page, testInfo, "cloud-setup-configure-mobile");
    await page.setViewportSize({ width: 1280, height: 720 });

    await page.locator("label.pricing-card").filter({ hasText: "Trading" }).locator('input[name="package_id"]').check();
    await page.getByLabel("Legal company name").fill("UAT Trading SAE");
    await page.getByLabel("Workspace name").fill("UAT Trading");
    await page.getByLabel(/Workspace (URL|address)/).fill(`uat-trading-${stamp}`);
    await page.selectOption('select[name="country"]', "Egypt");
    await page.locator("details.cloud-customize").locator('input[name="required_users"]').fill("8");
    await page.locator("details.cloud-customize").locator('input[name="required_storage_gb"]').fill("15");
    const egypt = page.locator("label.pricing-card").filter({ hasText: "Egyptian localization" });
    if (await egypt.locator('input[name="addon_ids"]').count()) {
      await egypt.locator('input[name="addon_ids"]').check();
    }
    await expect(page.locator('input[name="repository"]')).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await expect(page.getByRole("heading", { name: /Confirm your Helpers ERP Cloud workspace/ })).toBeVisible();
    await expect(page.getByText("Presentation Only").first()).toBeVisible();
    await shot(page, testInfo, "cloud-setup-confirm");
    await page.getByRole("button", { name: "Place demo order" }).click();

    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
    await shot(page, testInfo, "cloud-checkout-success");

    await page.goto("/cloud/instances");
    await expect(page.getByRole("heading", { name: /Your Helpers ERP Cloud workspaces/ })).toBeVisible();
    await expect(page.getByText(/Open Odoo is unavailable/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Open Odoo" })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("Manual Build");
    await shot(page, testInfo, "cloud-instances");
  });
});
