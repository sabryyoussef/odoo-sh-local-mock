const { test, expect } = require("../fixtures");
const { WizardPage } = require("../pages");

test.describe("Accessibility smoke", () => {
  test("key controls have accessible names and keyboard path works", async ({ page }) => {
    const wizard = new WizardPage(page);
    await page.goto("/platform/deploy/version");
    await expect(page.getByRole("button", { name: "Continue" })).toBeVisible();
    await page.locator("input[name=version_id]:not([disabled])").first().focus();
    await page.keyboard.press("Space");
    await page.getByRole("button", { name: "Continue" }).press("Enter");
    await expect(page).toHaveURL(/\/platform\/deploy\/plan/);
    await expect(page.getByRole("button", { name: "Continue" })).toBeVisible();
    await wizard.selectTrialPlan();
    await expect(page.getByLabel("Search apps")).toBeVisible();
    await expect(page.getByRole("button", { name: "Search" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review" })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(8);
  });
});
