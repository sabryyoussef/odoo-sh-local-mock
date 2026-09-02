const { test, expect } = require("../fixtures");
const { WizardPage } = require("../pages");

test.describe("Entitlements and modules", () => {
  test("selecting more than the plan maximum is rejected", async ({ page }) => {
    const wizard = new WizardPage(page);
    await page.goto("/platform/deploy/modules");
    await wizard.toggleModule("crm");
    await wizard.toggleModule("sale_management");
    await wizard.toggleModule("stock");
    await wizard.continueToReview();
    await expect(page).toHaveURL(/error=1/);
    await expect(page.getByTestId("entitlement-error")).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(/does not allow that many apps/i);
  });

  test("a valid allowed selection succeeds and shows dependencies", async ({ page }) => {
    const wizard = new WizardPage(page);
    await page.goto("/platform/deploy/modules");
    await wizard.search("CRM");
    await expect(page.getByTestId("module-crm")).toBeVisible();
    await page.goto("/platform/deploy/modules");
    await expect(page.getByTestId("module-deps-sale_management")).toContainText("sale");
    await wizard.toggleModule("sale_management");
    await wizard.continueToReview();
    await expect(page).toHaveURL(/\/platform\/deploy\/review/);
    await expect(page.getByTestId("review-app-sale_management")).toBeVisible();
    await expect(page.getByTestId("review-dependencies")).toBeVisible();
  });
});
