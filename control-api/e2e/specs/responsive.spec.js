const { test, expect } = require("../fixtures");
const { WizardPage } = require("../pages");

test.describe("Mobile layout smoke", () => {
  test("wizard has no obvious horizontal overflow", async ({ page }) => {
    await page.goto("/platform/deploy/version");
    await expect(page.getByRole("heading", { name: "Select Odoo version" })).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(24);
    const wizard = new WizardPage(page);
    await wizard.selectOdoo19();
    await expect(page.getByTestId("plan-option-trial")).toBeVisible();
  });
});
