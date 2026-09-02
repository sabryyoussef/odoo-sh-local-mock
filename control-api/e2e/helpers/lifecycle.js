const { LoginPage } = require("../pages");
const { attachErrorCollectors, assertNoUnexpectedErrors } = require("./errors");

function secretHeaders(runtime) {
  return { "x-e2e-secret": runtime.authSecret };
}

async function e2eJson(request, runtime, method, path, body) {
  const options = {
    headers: {
      ...secretHeaders(runtime),
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
  };
  if (body !== undefined) {
    options.data = body;
  }
  const res = await request[method](`${runtime.baseURL}${path}`, options);
  const text = await res.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  if (!res.ok()) {
    throw new Error(`${method.toUpperCase()} ${path} HTTP ${res.status()} ${text.slice(0, 300)}`);
  }
  return json;
}

async function restoreLifecycle(request, runtime) {
  await e2eJson(request, runtime, "post", "/e2e/lifecycle/restore", {});
}

async function fixtures(request, runtime) {
  return e2eJson(request, runtime, "get", "/e2e/lifecycle/fixtures");
}

async function setClock(request, runtime, body) {
  return e2eJson(request, runtime, "post", "/e2e/clock", body);
}

async function setRuntime(request, runtime, body) {
  return e2eJson(request, runtime, "post", "/e2e/runtime", body);
}

async function tick(request, runtime, trialId) {
  return e2eJson(request, runtime, "post", "/e2e/lifecycle/tick", { trial_id: trialId });
}

async function events(request, runtime, trialId) {
  return e2eJson(request, runtime, "get", `/e2e/lifecycle/events?trial_id=${trialId}`);
}

async function detail(request, runtime, trialId) {
  return e2eJson(request, runtime, "get", `/e2e/lifecycle/detail?trial_id=${trialId}`);
}

async function probeProtection(request, runtime, target) {
  return e2eJson(request, runtime, "post", "/e2e/lifecycle/probe-protection", { target });
}

async function loginAs(page, runtime, login, password) {
  const form = new LoginPage(page);
  await form.gotoE2e();
  await form.submit(login, password);
  await page.waitForURL("**/platform/deploy/version");
}

async function openOperatorContext(browser, runtime) {
  const ctx = await browser.newContext({
    baseURL: runtime.baseURL,
    viewport: { width: 1280, height: 720 },
  });
  const page = await ctx.newPage();
  const bag = { console: [], page: [], failed: [], http5xx: [] };
  attachErrorCollectors(page, bag, runtime.baseURL);
  await loginAs(page, runtime, runtime.operatorLogin, runtime.operatorPassword);
  return {
    ctx,
    page,
    async close() {
      assertNoUnexpectedErrors(bag);
      await ctx.close();
    },
  };
}

function remainingHours(text) {
  const days = Number((text.match(/(\d+)\s+days/) || [])[1] || 0);
  const hours = Number((text.match(/([\d.]+)\s+hours remaining/) || [])[1] || 0);
  return days * 24 + hours;
}

function assertNoSecrets(haystack, runtime) {
  const text = String(haystack);
  const lowered = text.toLowerCase();
  if (text.includes(runtime.authSecret)) {
    throw new Error("e2e auth secret leaked");
  }
  if (text.includes(runtime.customerPassword) || text.includes(runtime.operatorPassword) || text.includes(runtime.otherPassword)) {
    throw new Error("test password leaked");
  }
  if (lowered.includes("e2e-placeholder-token")) {
    throw new Error("placeholder token leaked");
  }
  if (lowered.includes("admin_password_protected") && lowered.includes("change-me")) {
    throw new Error("admin password leaked");
  }
}

module.exports = {
  restoreLifecycle,
  fixtures,
  setClock,
  setRuntime,
  tick,
  events,
  detail,
  probeProtection,
  loginAs,
  openOperatorContext,
  remainingHours,
  assertNoSecrets,
  e2eJson,
};
