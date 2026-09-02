const { test, expect } = require("../fixtures");
const { LifecyclePage } = require("../pages");
const life = require("../helpers/lifecycle");

test.describe("G3-C lifecycle responsive and accessibility", () => {
  test.beforeEach(async ({ request, runtime }) => {
    await life.restoreLifecycle(request, runtime);
  });

  test("lifecycle panel is usable on Pixel 5 without overflow", async ({ page, request, runtime }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.countdown.trial_id;
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const ui = new LifecyclePage(page);
    await ui.openTrial(trialId);
    await expect(ui.panel()).toBeVisible();
    await expect(page.getByLabel("Lifecycle state")).toBeVisible();
    await expect(page.getByLabel("Trial countdown")).toBeVisible();
    await expect(page.getByRole("link", { name: "Launch tenant" })).toBeVisible();
    await page.getByRole("link", { name: "Launch tenant" }).focus();
    await expect(page.getByRole("link", { name: "Launch tenant" })).toBeFocused();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(24);
    await life.setClock(request, runtime, { advance_days: 7, advance_seconds: 2 });
    await life.tick(request, runtime, fx.trials.grace.trial_id);
    await ui.openTrial(fx.trials.grace.trial_id);
    await expect(page.getByLabel("Grace period warning")).toBeVisible();
    await expect(page.getByTestId("lifecycle-grace-warning")).toContainText("grace");
    await life.setClock(request, runtime, { advance_days: 3, advance_seconds: 2 });
    await life.tick(request, runtime, fx.trials.suspend.trial_id);
    await life.tick(request, runtime, fx.trials.suspend.trial_id);
    await ui.openTrial(fx.trials.suspend.trial_id);
    await expect(page.getByLabel("Suspended trial banner")).toBeVisible();
    await expect(page.getByTestId("lifecycle-suspended-banner")).toContainText("suspended");
  });
});
