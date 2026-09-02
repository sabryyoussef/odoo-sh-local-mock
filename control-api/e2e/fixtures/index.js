const { test: base, expect } = require("playwright/test");
const { loadRuntime } = require("../helpers/runtime");
const { attachErrorCollectors, assertNoUnexpectedErrors } = require("../helpers/errors");

const test = base.extend({
  runtime: async ({}, use) => {
    await use(loadRuntime());
  },
  baseURL: async ({ runtime }, use) => {
    await use(runtime.baseURL);
  },
  page: async ({ page, runtime }, use) => {
    const bag = { console: [], page: [], failed: [], http5xx: [] };
    attachErrorCollectors(page, bag, runtime.baseURL);
    await use(page);
    assertNoUnexpectedErrors(bag);
  },
  e2eState: async ({ request, runtime }, use) => {
    async function getState() {
      const res = await request.get(`${runtime.baseURL}/e2e/state`, {
        headers: { "x-e2e-secret": runtime.authSecret },
      });
      if (!res.ok()) {
        throw new Error(`e2e state HTTP ${res.status()}`);
      }
      return res.json();
    }
    await use({ getState });
  },
  resetUser: [
    async ({ page, runtime }, use, testInfo) => {
      const project = testInfo.project.name;
      const isLifecycle = project.startsWith("lifecycle") || /lifecycle/.test(testInfo.file || "");
      if ((project === "desktop-chromium" || project === "mobile-chromium") && !isLifecycle) {
        const res = await page.request.post(`${runtime.baseURL}/e2e/reset-user`, {
          headers: { "x-e2e-secret": runtime.authSecret },
        });
        if (!res.ok()) {
          throw new Error(`e2e reset-user HTTP ${res.status()}`);
        }
      }
      await use();
    },
    { auto: true },
  ],
});

module.exports = { test, expect };
