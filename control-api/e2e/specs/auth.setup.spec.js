const { test } = require("../fixtures");
const { LoginPage } = require("../pages");

test("save isolated authentication storage state", async ({ page, runtime }) => {
  const login = new LoginPage(page);
  await login.gotoE2e();
  await login.submit(runtime.login, runtime.password);
  await page.waitForURL("**/platform/deploy/version");
  await page.context().storageState({ path: runtime.storageStatePath });
});
