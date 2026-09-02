const { test, expect } = require("../fixtures");
const { WizardPage } = require("../pages");

test.describe("Quick Deploy navigation", () => {
  test("version, plan, modules, and review steps load", async ({ page }) => {
    const wizard = new WizardPage(page);
    await page.goto("/platform/deploy/version");
    await expect(page.getByRole("heading", { name: "Select Odoo version" })).toBeVisible();
    await expect(page.getByTestId("version-option-19.0")).toContainText("Odoo 19 Community");
    await wizard.selectOdoo19();

    await expect(page).toHaveURL(/\/platform\/deploy\/plan/);
    await expect(page.getByRole("heading", { name: "Select plan" })).toBeVisible();
    await expect(page.getByTestId("plan-option-trial")).toBeVisible();
    await expect(page.getByTestId("plan-option-trial")).not.toContainText("undefined");
    await wizard.selectTrialPlan();

    await expect(page).toHaveURL(/\/platform\/deploy\/modules/);
    await expect(page.getByRole("heading", { name: "Select apps" })).toBeVisible();
    await expect(page.getByTestId("module-crm")).toContainText("CRM");
    await expect(page.getByTestId("module-sale_management")).toContainText("Sales");
    await expect(page.getByTestId("module-stock")).toContainText("Inventory");
    await wizard.toggleModule("crm");
    await wizard.continueToReview();

    await expect(page).toHaveURL(/\/platform\/deploy\/review/);
    await expect(page.getByRole("heading", { name: "Review deployment" })).toBeVisible();
    await expect(page.getByTestId("review-plan")).toHaveText(/trial/i);
    await expect(page.getByTestId("review-version")).toHaveText(/19/);
    await expect(page.getByTestId("review-app-crm")).toBeVisible();
  });

  test("trial plan renders when description is synthesized from empty price label", async ({ page }) => {
    await page.goto("/platform/deploy/plan");
    await expect(page.getByTestId("plan-option-trial")).toBeVisible();
    const text = await page.getByTestId("plan-option-trial").innerText();
    expect(text.toLowerCase()).not.toContain("undefined");
    expect(text.toLowerCase()).not.toContain("null");
  });

  test("browser back and forward keep module selection", async ({ page }) => {
    const wizard = new WizardPage(page);
    await page.goto("/platform/deploy/modules");
    await wizard.toggleModule("crm");
    await expect(page.getByTestId("module-crm").locator("input")).toBeChecked();
    await wizard.continueToReview();
    await expect(page).toHaveURL(/\/review/);
    await page.goBack();
    await expect(page).toHaveURL(/\/modules/);
    await expect(page.getByTestId("module-crm").locator("input")).toBeChecked();
    await page.goForward();
    await expect(page.getByTestId("review-app-crm")).toBeVisible();
  });
});
