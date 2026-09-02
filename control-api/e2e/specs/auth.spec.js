const { test, expect } = require("../fixtures");
const { LoginPage } = require("../pages");

test.describe("Authentication", () => {
  test("unauthenticated user is redirected to login", async ({ page }) => {
    await page.goto("/platform/deploy/version");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("link", { name: /Sign in with GitHub/i })).toBeVisible();
  });

  test("invalid credentials show a safe error", async ({ page, runtime }) => {
    const login = new LoginPage(page);
    await login.gotoE2e();
    await login.submit(runtime.login, "not-the-password");
    await expect(page.getByRole("alert")).toHaveText("Invalid username or password.");
    await expect(page).toHaveURL(/\/e2e\/login/);
    await expect(page.locator("body")).not.toContainText(runtime.password);
  });

  test("valid isolated test user can sign in", async ({ page, runtime }) => {
    const login = new LoginPage(page);
    await login.gotoE2e();
    await login.submit(runtime.login, runtime.password);
    await expect(page).toHaveURL(/\/platform\/deploy\/version/);
    await expect(page.getByRole("heading", { name: "Select Odoo version" })).toBeVisible();
  });

  test("session reuse through storage state works", async ({ page, runtime }) => {
    const login = new LoginPage(page);
    await login.gotoE2e();
    await login.submit(runtime.login, runtime.password);
    await page.waitForURL("**/platform/deploy/version");
    const state = await page.context().storageState();
    const second = await page.context().browser().newContext({
      storageState: state,
      baseURL: runtime.baseURL,
    });
    const reused = await second.newPage();
    await reused.goto("/platform/deploy/plan");
    await expect(reused).toHaveURL(/\/platform\/deploy\/plan/);
    await expect(reused.getByRole("heading", { name: "Select plan" })).toBeVisible();
    await second.close();
  });
});
