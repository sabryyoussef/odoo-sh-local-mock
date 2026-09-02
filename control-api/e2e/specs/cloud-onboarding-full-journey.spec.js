const { test, expect } = require("../fixtures");
const { attachErrorCollectors, assertNoUnexpectedErrors } = require("../helpers/errors");
const {
  STAMP,
  EVIDENCE_ROOT,
  shot,
  assertNoOverflow,
  uniqueEmail,
  csrf,
  registerCloud,
  loginCloud,
  logoutCloud,
  fillConfigure,
  continueConfigure,
  orderCount,
} = require("../helpers/cloud-onboarding");

test.describe.configure({ timeout: 180_000 });

function isMobileProject(testInfo) {
  return String(testInfo.project.name || "").includes("mobile");
}

function isFirefoxProject(testInfo) {
  return String(testInfo.project.name || "").includes("firefox");
}

function prefix(testInfo, name) {
  const tags = [];
  if (isFirefoxProject(testInfo)) tags.push("firefox");
  if (isMobileProject(testInfo)) tags.push("mobile");
  if (!tags.length) return name;
  return name.replace(/\.png$/, `-${tags.join("-")}.png`);
}

async function expectCloudIsolation(page) {
  await expect(page.locator('input[name="repository"]')).toHaveCount(0);
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("commit SHA");
  await expect(page.getByRole("button", { name: /Manual Build|Rebuild|CONNECT/ })).toHaveCount(0);
}

test.describe("Helpers ERP Cloud full onboarding journeys", () => {
  test("Journey A — new Business customer monthly @cloud-primary", async ({ page, runtime }, testInfo) => {
    const stamp = Date.now();
    const email = uniqueEmail("uat.biz");
    const subdomain = `uat-biz-${stamp}`;
    const shotName = (name) => prefix(testInfo, name);

    await page.goto("/");
    await expect(page.getByRole("link", { name: "Cloud sign in" })).toHaveAttribute("href", "/cloud/login");
    await expect(page.getByRole("link", { name: "Developer sign in" })).toHaveAttribute("href", "/login");
    await shot(page, shotName("01-homepage-product-signin.png"));
    await assertNoOverflow(expect, page);

    await page.getByRole("link", { name: "Helpers ERP Cloud" }).first().click();
    await expect(page).toHaveURL(/\/cloud$/);
    await expect(page.getByRole("link", { name: "View plans and start" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toHaveAttribute("href", "/cloud/login");
    await expectCloudIsolation(page);
    await shot(page, shotName("02-cloud-overview.png"));

    await page.getByRole("link", { name: "View plans and start" }).click();
    await expect(page).toHaveURL(/\/cloud\/pricing/);
    await expect(page.getByRole("link", { name: "Choose Business" })).toBeVisible();
    await shot(page, shotName("03-cloud-pricing-monthly.png"));

    const businessHref = await page.getByRole("link", { name: "Choose Business" }).getAttribute("href");
    expect(businessHref).toMatch(/plan=business/);
    expect(businessHref).toMatch(/cycle=monthly/);
    await page.getByRole("link", { name: "Choose Business" }).click();
    await expect(page).toHaveURL(/\/cloud\/register/);
    expect(page.url()).toMatch(/plan=business/);
    expect(page.url()).toMatch(/cycle=monthly/);

    await shot(page, shotName("05-cloud-register.png"));
    await registerCloud(page, { name: "UAT Business Buyer", email });
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await expect(page.getByRole("heading", { name: "Choose how you work" })).toBeVisible();
    await expect(page.getByText("Business", { exact: true }).first()).toBeVisible();
    await expect(page.locator("body")).not.toContainText("Select Odoo version");
    await expect(page.locator('input[name="version_id"]')).toHaveCount(0);
    await shot(page, shotName("07-cloud-configure-default.png"));

    await fillConfigure(page, {
      package: "Trading",
      company: "UAT Trading SAE",
      workspace: "UAT Trading",
      subdomain,
      country: "Egypt",
      language: "ar_001",
      users: 8,
      storage: 15,
      addon: "Egyptian localization",
    });
    await shot(page, shotName("08-cloud-configure-customized.png"));
    await page.getByRole("button", { name: "Update total" }).click();
    await expect(page.locator("#quote-total")).toContainText("$189.00");
    await expect(page.locator("#cloud-quote")).toContainText("Approved add-ons");
    await shot(page, shotName("09-cloud-server-quote.png"));
    await continueConfigure(page);

    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await expect(page.getByText("UAT Trading SAE")).toBeVisible();
    await expect(page.getByText(`${subdomain}.helpers-erp.example`)).toBeVisible();
    await expect(page.getByText("Egypt · EGP · ar_001")).toBeVisible();
    await expect(page.getByText("Business · monthly")).toBeVisible();
    const listedTotal = await page.locator("body").innerText();
    expect(listedTotal).toMatch(/\$\d+/);
    await shot(page, shotName("10-cloud-confirm.png"));

    await page.evaluate(() => {
      const form = document.getElementById("cloud-confirm-form");
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "total_cents";
      input.value = "1";
      form.appendChild(input);
    });
    const token = await csrf(page);
    const key = await page.locator('input[name="idempotency_key"]').inputValue();
    const postOnce = () =>
      page.request.post("/cloud/setup/confirm", {
        form: { csrf_token: token, idempotency_key: key, total_cents: "1" },
        maxRedirects: 0,
      });
    const first = await postOnce();
    const second = await postOnce();
    expect([302, 303]).toContain(first.status());
    expect([302, 303]).toContain(second.status());
    const orders = await orderCount(page, runtime, email);
    expect(orders.count).toBe(1);

    await page.goto(first.headers()["location"] || "/cloud/instances");
    if (page.url().includes("checkout/success")) {
      await expect(page.getByRole("heading", { name: /Demo order received/ })).toBeVisible();
      await expect(page.getByText("$0.01")).toHaveCount(0);
      await expect(page.getByText("Finish configuring your workspace before confirming.")).toHaveCount(0);
      await shot(page, shotName("11-cloud-success.png"));
      await page.goto("/cloud/instances");
    }
    await expect(page.getByRole("heading", { name: /Your Helpers ERP Cloud workspaces/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "UAT Trading" })).toBeVisible();
    await expectCloudIsolation(page);
    await shot(page, shotName("12-cloud-instances.png"));
    await assertNoOverflow(expect, page);
  });

  test("Journey B — annual Starter yearly catalog total", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const stamp = Date.now();
    const email = uniqueEmail("uat.annual");
    await page.goto("/cloud/pricing");
    await page.locator('input[name="cycle"][value="annual"]').check();
    await page.waitForURL(/cycle=annual/);
    await expect(page.getByText(/yearly totals from the catalog/i)).toBeVisible();
    await expect(page.getByText(/no extra discount is applied/i)).toBeVisible();
    await expect(page.getByText("$490.00")).toBeVisible();
    await expect(page.getByText("$588.00")).toHaveCount(0);
    await shot(page, "04-cloud-pricing-annual.png");
    const href = await page.getByRole("link", { name: "Choose Starter" }).getAttribute("href");
    expect(href).toMatch(/plan=starter/);
    expect(href).toMatch(/cycle=annual/);
    await page.getByRole("link", { name: "Choose Starter" }).click();
    await expect(page.url()).toMatch(/cycle=annual/);
    await registerCloud(page, { name: "Annual Starter Buyer", email });
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await expect(page.getByText("annual")).toBeVisible();
    await expect(page.getByText("Starter").first()).toBeVisible();
    await fillConfigure(page, {
      package: "Sales",
      company: "Annual Starter Co",
      workspace: "Annual Starter",
      subdomain: `annual-st-${stamp}`,
      country: "Saudi Arabia",
    });
    await continueConfigure(page);
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await expect(page.getByText(/year/i).first()).toBeVisible();
    await expect(page.getByText("Yearly total")).toBeVisible();
    await page.getByRole("button", { name: "Place demo order" }).click();
    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
    await shot(page, "B-annual-success.png");
  });

  test("Journey C — Trial monthly normalization and demo path", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const stamp = Date.now();
    const email = uniqueEmail("uat.trial");
    await page.goto("/cloud/pricing?cycle=annual");
    await expect(page.getByRole("link", { name: "Start free" })).toHaveAttribute("href", /plan=trial/);
    await expect(page.getByRole("link", { name: "Start free" })).toHaveAttribute("href", /cycle=monthly/);
    await page.getByRole("link", { name: "Start free" }).click();
    await registerCloud(page, { name: "Trial Buyer", email });
    await expect(page.getByText("Trial").first()).toBeVisible();
    await expect(page.locator('input[name="required_users"]')).toHaveValue(/^[123]$/);
    await expect(page.locator("label.pricing-card").filter({ hasText: "Egyptian localization" })).toContainText(
      /Not available|Include this add-on/,
    );
    const egyptBox = page.locator("label.pricing-card").filter({ hasText: "Egyptian localization" }).locator('input[name="addon_ids"]');
    expect(await egyptBox.count()).toBe(0);
    await fillConfigure(page, {
      package: "Sales",
      company: "Trial Co",
      workspace: "Trial Co",
      subdomain: `trial-co-${stamp}`,
      country: "Egypt",
    });
    await continueConfigure(page);
    await page.getByRole("button", { name: "Place demo order" }).click();
    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
  });

  test("Journey D — Enterprise custom quote", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const stamp = Date.now();
    const email = uniqueEmail("uat.ent");
    await page.goto("/cloud/pricing");
    await expect(page.getByText("Custom quote").first()).toBeVisible();
    await page.getByRole("link", { name: "Continue with Enterprise" }).click();
    await registerCloud(page, { name: "Enterprise Buyer", email });
    await fillConfigure(page, {
      package: "Full ERP",
      company: "Enterprise Co",
      workspace: "Enterprise Co",
      subdomain: `ent-co-${stamp}`,
      country: "UAE",
    });
    await expect(page.locator("#cloud-quote")).toContainText("Custom quote");
    await shot(page, "13-cloud-enterprise-custom-quote.png");
    await continueConfigure(page);
    await expect(page.getByText("Custom quote")).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit Enterprise request" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Place demo order" })).toHaveCount(0);
    await shot(page, "14-cloud-enterprise-confirm.png");
    await page.getByRole("button", { name: "Submit Enterprise request" }).click();
    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
  });

  test("Journey E — login resume precedence", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const stamp = Date.now();
    const password = "SecurePass1";

    const noDraft = uniqueEmail("uat.nodraft");
    await page.goto("/cloud/register");
    await registerCloud(page, { name: "No Draft", email: noDraft, password });
    await logoutCloud(page);
    await loginCloud(page, { email: noDraft, password });
    await expect(page).toHaveURL(/\/cloud\/pricing/);

    await logoutCloud(page);
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toBeVisible();
    const incomplete = uniqueEmail("uat.inc");
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await expect(page.getByLabel("Full name")).toBeVisible();
    await registerCloud(page, { name: "Incomplete", email: incomplete, password });
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await logoutCloud(page);
    await loginCloud(page, { email: incomplete, password });
    await expect(page).toHaveURL(/\/cloud\/setup/);

    await page.goto("/cloud/pricing");
    await page.getByRole("link", { name: "Choose Business" }).click();
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await expect(page.getByText("Business", { exact: true }).first()).toBeVisible();

    const ready = uniqueEmail("uat.ready");
    await logoutCloud(page);
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await registerCloud(page, { name: "Ready User", email: ready, password });
    await fillConfigure(page, {
      package: "Sales",
      company: "Ready Co",
      workspace: "Ready Co",
      subdomain: `ready-co-${stamp}`,
      country: "Egypt",
    });
    await continueConfigure(page);
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await logoutCloud(page);
    await loginCloud(page, { email: ready, password });
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);

    await page.getByRole("button", { name: "Place demo order" }).click();
    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
    await logoutCloud(page);
    await loginCloud(page, { email: ready, password });
    await expect(page).toHaveURL(/\/cloud\/instances/);

    await page.goto("/cloud/pricing");
    await page.getByRole("link", { name: "Choose Starter" }).click();
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await expect(page.getByText("Starter").first()).toBeVisible();
  });

  test("Journey F — plan downgrade normalization", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const stamp = Date.now();
    const email = uniqueEmail("uat.down");
    await page.goto("/cloud/register?plan=business&cycle=monthly");
    await registerCloud(page, { name: "Downgrade Buyer", email });
    await fillConfigure(page, {
      package: "Trading",
      company: "Downgrade Co",
      workspace: "Downgrade Co",
      subdomain: `down-co-${stamp}`,
      country: "Egypt",
      users: 40,
      storage: 80,
      addon: "Egyptian localization",
    });
    await page.getByRole("button", { name: "Change plan" }).click();
    await page.getByRole("link", { name: "Choose Starter" }).click();
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await expect(page.getByText("Starter").first()).toBeVisible();
    const users = Number(await page.locator('input[name="required_users"]').inputValue());
    const storage = Number(await page.locator('input[name="required_storage_gb"]').inputValue());
    expect(users).toBeLessThanOrEqual(10);
    expect(storage).toBeLessThanOrEqual(50);
    await expect(page.getByLabel("Legal company name")).toHaveValue("Downgrade Co");
    await page.getByRole("button", { name: "Change plan" }).click();
    await page.getByRole("link", { name: "Start free" }).click();
    await expect(page.getByText("Trial").first()).toBeVisible();
    const egyptAfterTrial = page
      .locator("label.pricing-card")
      .filter({ hasText: "Egyptian localization" })
      .locator('input[name="addon_ids"]');
    expect(await egyptAfterTrial.count()).toBe(0);
    await shot(page, "15-cloud-downgrade-normalized.png");
  });

  test("Journey G — validation and security UX", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    await page.goto("/cloud/register");
    await page.$eval("form", (form) => form.setAttribute("novalidate", "novalidate"));
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.locator("#form-error-summary")).toBeVisible();
    await shot(page, "06-cloud-register-validation.png");

    await page.getByLabel("Full name").fill("A");
    await page.getByLabel("Work email").fill("not-an-email");
    await page.locator('input[name="password"]').fill("short");
    await page.locator('input[name="password_confirm"]').fill("mismatch");
    await page.$eval("form", (form) => form.setAttribute("novalidate", "novalidate"));
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.locator("#form-error-summary")).toBeFocused();
    await expect(page.locator("[aria-invalid='true']").first()).toBeVisible();
    await expect(page.getByText("Enter your full name.")).toBeVisible();

    const email = uniqueEmail("uat.val");
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await registerCloud(page, { name: "Validation Buyer", email });

    await page.goto("/cloud/setup?plan=not-a-plan&cycle=weekly");
    await expect(page).toHaveURL(/\/cloud\/(pricing|setup)/);

    await logoutCloud(page);
    await page.goto("/cloud/login?next=https://evil.example/steal");
    await loginCloud(page, { email });
    expect(page.url()).not.toMatch(/evil\.example/);
    expect(page.url()).toMatch(/\/cloud\//);

    await page.goto("/cloud/setup");
    await fillConfigure(page, {
      package: "Sales",
      company: "Val Co",
      workspace: "Val Co",
      subdomain: "admin",
      country: "Egypt",
    });
    await continueConfigure(page);
    await expect(page.locator(".field-error").first()).toContainText(/reserved|invalid|workspace/i);
    await shot(page, "16-cloud-workspace-validation.png");

    for (const bad of ["api", "www", "cloud", "platform", "ab", "has space", "UPPER_CHARS", "x".repeat(60)]) {
      await fillConfigure(page, { subdomain: bad });
      await continueConfigure(page);
      await expect(page).toHaveURL(/\/cloud\/setup$/);
      await expect(page.locator(".field-error").first()).toBeVisible();
    }

    await fillConfigure(page, { company: "شركة النيل", workspace: "شركة النيل", subdomain: "" });
    await page.locator('input[name="requested_subdomain"]').fill("");
    await continueConfigure(page);
    if (page.url().includes("/confirm")) {
      await expect(page.locator("body")).toContainText(/workspace-\d+\.helpers-erp\.example/);
    } else {
      await expect(page.locator("body")).toContainText(/workspace-\d+|reserved|required/i);
    }
  });

  test("Journey G2 — duplicate workspace and CSRF", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const stamp = Date.now();
    const slug = `dup-ws-${stamp}`;
    const email1 = uniqueEmail("uat.dup1");
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await registerCloud(page, { name: "Dup One", email: email1 });
    await fillConfigure(page, {
      package: "Sales",
      company: "Dup One Co",
      workspace: "Dup One",
      subdomain: slug,
      country: "Egypt",
    });
    await continueConfigure(page);
    await page.getByRole("button", { name: "Place demo order" }).click();
    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
    await logoutCloud(page);

    const email2 = uniqueEmail("uat.dup2");
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await registerCloud(page, { name: "Dup Two", email: email2 });
    await fillConfigure(page, {
      package: "Sales",
      company: "Dup Two Co",
      workspace: "Dup Two",
      subdomain: slug,
      country: "Egypt",
    });
    await continueConfigure(page);
    await expect(page.locator(".field-error").first()).toContainText(/taken|already|unique|exists|workspace/i);

    await page.request.post("/cloud/setup", { form: { action: "continue" }, maxRedirects: 0 });
    await page.goto("/cloud/setup");
    await expect(page.getByRole("heading", { name: "Choose how you work" })).toBeVisible();
  });

  test("Journey H — product-line isolation @cloud-primary", async ({ page }, testInfo) => {
    await page.goto("/cloud");
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toHaveAttribute("href", "/cloud/login");
    await expectCloudIsolation(page);
    await page.goto("/platform");
    await expect(page.locator('.mkt-nav__actions a[href="/login"]')).toBeVisible();
    await page.goto("/login");
    await expect(page.getByRole("link", { name: /Sign in with GitHub/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /Helpers ERP Cloud sign in/i })).toHaveAttribute("href", "/cloud/login");
    await shot(page, prefix(testInfo, "17-developer-github-login.png"));
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Cloud sign in" })).toHaveAttribute("href", "/cloud/login");
    await expect(page.getByRole("link", { name: "Developer sign in" })).toHaveAttribute("href", "/login");
  });

  test("Journey I — compatibility URLs and legacy plan POST", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "desktop chromium coverage");
    const email = uniqueEmail("uat.compat");
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await registerCloud(page, { name: "Compat Buyer", email });
    const redirects = [
      ["/cloud/setup/version", /\/cloud\/setup$/],
      ["/cloud/setup/package", /\/cloud\/setup$/],
      ["/cloud/setup/company", /\/cloud\/setup$/],
      ["/cloud/setup/addons", /\/cloud\/setup$/],
    ];
    for (const [from, dest] of redirects) {
      await page.goto(from);
      await expect(page).toHaveURL(dest);
    }
    await page.goto("/cloud/setup/plan");
    await expect(page).toHaveURL(/\/cloud\/setup$/);

    await fillConfigure(page, {
      package: "Sales",
      company: "Compat Co",
      workspace: "Compat Co",
      subdomain: `compat-${Date.now()}`,
      country: "Egypt",
    });
    await continueConfigure(page);
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await page.goto("/cloud/setup/review");
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await page.goto("/cloud/checkout");
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    const token = await csrf(page);
    const key = await page.locator('input[name="idempotency_key"]').inputValue();
    const legacy = await page.request.post("/cloud/checkout", {
      form: { csrf_token: token, idempotency_key: key },
      maxRedirects: 0,
    });
    expect([302, 303]).toContain(legacy.status());
    expect(legacy.headers()["location"]).toMatch(/\/cloud\/checkout\/success/);
  });

  test("keyboard-only smoke for register, configure, confirm @cloud-primary", async ({ page }, testInfo) => {
    test.skip(isMobileProject(testInfo), "keyboard on desktop/firefox");
    const email = uniqueEmail("uat.key");
    await page.goto("/cloud/register?plan=starter&cycle=monthly");
    await page.locator("#full_name").focus();
    await expect(page.locator("#full_name")).toBeFocused();
    await page.keyboard.type("Keyboard Buyer");
    await page.keyboard.press("Tab");
    await page.keyboard.type(email);
    await page.keyboard.press("Tab");
    await page.keyboard.type("SecurePass1");
    await page.keyboard.press("Tab");
    await page.keyboard.type("SecurePass1");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Space");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await page.getByLabel("Legal company name").focus();
    await page.keyboard.type("Keyboard Co");
    await page.keyboard.press("Tab");
    await page.keyboard.type("Keyboard Co");
    await continueConfigure(page);
    if (page.url().includes("/confirm")) {
      await page.getByRole("button", { name: /Place demo order|Submit Enterprise request/ }).focus();
      await expect(page.locator("#cloud-confirm-cta")).toBeFocused();
    }
  });
});

test.describe("Helpers ERP Cloud no-JavaScript fallback", () => {
  test("Journey A works with JavaScript disabled", async ({ browser, runtime }, testInfo) => {
    test.skip(isMobileProject(testInfo) || isFirefoxProject(testInfo), "chromium no-js");
    const context = await browser.newContext({
      baseURL: runtime.baseURL,
      javaScriptEnabled: false,
      viewport: { width: 1280, height: 720 },
    });
    const page = await context.newPage();
    const bag = { console: [], page: [], failed: [], http5xx: [], http4xx: [], expected4xx: [] };
    attachErrorCollectors(page, bag, runtime.baseURL);
    const stamp = Date.now();
    const email = uniqueEmail("uat.nojs");
    await page.goto("/cloud/pricing");
    await page.locator('input[name="cycle"][value="annual"]').check();
    await page.getByRole("button", { name: "Show prices" }).click();
    await expect(page).toHaveURL(/cycle=annual/);
    await page.goto("/cloud/pricing?cycle=monthly");
    await page.getByRole("link", { name: "Choose Starter" }).click();
    await registerCloud(page, { name: "NoJS Buyer", email });
    await expect(page).toHaveURL(/\/cloud\/setup/);
    await fillConfigure(page, {
      package: "Sales",
      company: "NoJS Co",
      workspace: "NoJS Co",
      subdomain: `nojs-${stamp}`,
      country: "Egypt",
    });
    await page.getByRole("button", { name: "Update total" }).click();
    await expect(page.locator("#cloud-quote")).toContainText(/\$|Your total|Custom quote/);
    await continueConfigure(page);
    await expect(page).toHaveURL(/\/cloud\/setup\/confirm/);
    await page.getByRole("button", { name: "Place demo order" }).click();
    await expect(page).toHaveURL(/\/cloud\/checkout\/success/);
    assertNoUnexpectedErrors(bag);
    await context.close();
  });
});
