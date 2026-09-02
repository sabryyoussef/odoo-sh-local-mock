const { test, expect } = require("../fixtures");
const { LifecyclePage } = require("../pages");
const life = require("../helpers/lifecycle");

test.describe("G3-C isolated trial lifecycle", () => {
  test.beforeEach(async ({ request, runtime }) => {
    await life.restoreLifecycle(request, runtime);
  });

  test("A. active countdown decreases and stays non-negative", async ({ page, request, runtime }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.countdown.trial_id;
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const ui = new LifecyclePage(page);
    await ui.openTrial(trialId);
    await expect(ui.panel()).toBeVisible();
    await expect(ui.state()).toHaveText("trial_active");
    await expect(ui.countdown()).toContainText("Expires");
    await expect(ui.countdown()).toContainText("days");
    await expect(ui.countdown()).toContainText("hours remaining");
    const first = life.remainingHours(await ui.countdown().innerText());
    expect(first).toBeGreaterThan(0);
    await life.setClock(request, runtime, { advance_days: 2 });
    await ui.openTrial(trialId);
    const second = life.remainingHours(await ui.countdown().innerText());
    expect(second).toBeLessThan(first);
    expect(second).toBeGreaterThan(0);
    await life.setClock(request, runtime, { advance_days: 10 });
    await ui.openTrial(trialId);
    expect(life.remainingHours(await ui.countdown().innerText())).toBe(0);
    await expect(ui.countdown()).not.toContainText("negative");
    await expect(ui.state()).not.toHaveText("trial_active");
    const otherRes = await page.request.get(`/api/platform/trials/${fx.trials.other.trial_id}/lifecycle`);
    expect(otherRes.status()).toBe(404);
    const mine = await page.request.get(`/api/platform/trials/${trialId}/lifecycle`);
    expect(mine.ok()).toBeTruthy();
    const body = await mine.json();
    life.assertNoSecrets(JSON.stringify(body), runtime);
    expect(JSON.stringify(body)).not.toContain("database_role");
    life.assertNoSecrets(await page.content(), runtime);
  });

  test("B. expiry warnings appear once and do not claim email", async ({ page, request, runtime }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.countdown.trial_id;
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const ui = new LifecyclePage(page);
    await life.setClock(request, runtime, { advance_days: 4 });
    await ui.openTrial(trialId);
    await expect(page.getByTestId("lifecycle-expiry-warning")).toBeVisible();
    await life.tick(request, runtime, trialId);
    await life.tick(request, runtime, trialId);
    let ev = await life.events(request, runtime, trialId);
    const three = ev.events.filter((e) => e.type === "warning_expiry_3d");
    expect(three).toHaveLength(1);
    expect(String(three[0].payload_json || "")).not.toMatch(/email.?sent/i);
    await life.setClock(request, runtime, { iso: fx.clock_epoch });
    await life.setClock(request, runtime, { advance_days: 6, advance_hours: 12 });
    await ui.openTrial(trialId);
    await expect(page.getByTestId("lifecycle-expiry-warning")).toBeVisible();
    await life.tick(request, runtime, trialId);
    await life.tick(request, runtime, trialId);
    ev = await life.events(request, runtime, trialId);
    expect(ev.events.filter((e) => e.type === "warning_expiry_1d")).toHaveLength(1);
    await life.setClock(request, runtime, { iso: fx.clock_epoch });
    await life.setClock(request, runtime, { advance_days: 7, advance_seconds: 1 });
    await life.tick(request, runtime, trialId);
    await life.tick(request, runtime, trialId);
    await ui.openTrial(trialId);
    await expect(page.getByTestId("lifecycle-expiry-warning")).toBeVisible();
    await expect(page.getByTestId("lifecycle-grace-warning")).toBeVisible();
    ev = await life.events(request, runtime, trialId);
    expect(ev.events.filter((e) => e.type === "warning_at_expiry")).toHaveLength(1);
    const html = await page.content();
    expect(html.toLowerCase()).not.toContain("email was sent");
    expect(html.toLowerCase()).not.toContain("email sent");
  });

  test("C. grace transition is stable and does not expand entitlements", async ({ page, request, runtime }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.grace.trial_id;
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const ui = new LifecyclePage(page);
    await life.setClock(request, runtime, { advance_days: 7, advance_seconds: 2 });
    const first = await life.tick(request, runtime, trialId);
    expect(first.status).toBe("grace");
    const d1 = await life.detail(request, runtime, trialId);
    const second = await life.tick(request, runtime, trialId);
    expect(second.status).toBe("grace");
    const d2 = await life.detail(request, runtime, trialId);
    expect(d2.grace_ends_at).toBe(d1.grace_ends_at);
    await ui.openTrial(trialId);
    await expect(ui.state()).toHaveText("grace");
    await expect(page.getByTestId("lifecycle-grace-warning")).toBeVisible();
    await expect(page.getByTestId("tenant-launch")).toBeVisible();
    await expect(page.getByTestId("module-crm")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Review" })).toHaveCount(0);
    const ev = await life.events(request, runtime, trialId);
    expect(ev.events.filter((e) => e.type === "warning_at_expiry")).toHaveLength(1);
  });

  test("D. suspension waits for fake stop and retains disposable resources", async ({ page, request, runtime }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.suspend.trial_id;
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const ui = new LifecyclePage(page);
    await life.setClock(request, runtime, { advance_days: 7, advance_seconds: 2 });
    await life.tick(request, runtime, trialId);
    await life.setClock(request, runtime, { advance_days: 3, advance_seconds: 2 });
    const pending = await life.tick(request, runtime, trialId);
    expect(["suspension_pending", "suspended"]).toContain(pending.status);
    const rt = await life.setRuntime(request, runtime, {});
    expect(rt.stop_calls).toBe(1);
    await life.tick(request, runtime, trialId);
    const after = await request.get(`${runtime.baseURL}/e2e/runtime`, { headers: { "x-e2e-secret": runtime.authSecret } });
    const rt2 = await after.json();
    expect(rt2.stop_calls).toBe(1);
    const snap = await life.detail(request, runtime, trialId);
    expect(snap.status).toBe("suspended");
    expect(snap.tenant.database_name).toBeTruthy();
    expect(snap.tenant.database_role).toBeTruthy();
    expect(snap.tenant.filestore_path).toBeTruthy();
    expect(snap.tenant.http_port).toBeTruthy();
    expect(snap.backup_count).toBeGreaterThan(0);
    expect(snap.tenant.id).toBeTruthy();
    await ui.openTrial(trialId);
    await expect(page.getByTestId("lifecycle-suspended-banner")).toBeVisible();
    await expect(ui.state()).toHaveText("suspended");
    await expect(page.getByTestId("lifecycle-access-blocked")).toBeVisible();
    await expect(page.getByTestId("tenant-launch")).toHaveCount(0);
  });

  test("D2. failed fake stop stays retryable and is not suspended", async ({ request, runtime }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.suspend_fail.trial_id;
    await life.setRuntime(request, runtime, { stop_fail: true });
    await life.setClock(request, runtime, { advance_days: 7, advance_seconds: 2 });
    await life.tick(request, runtime, trialId);
    await life.setClock(request, runtime, { advance_days: 3, advance_seconds: 2 });
    const failed = await life.tick(request, runtime, trialId);
    expect(failed.status).toBe("suspension_pending");
    expect(failed.ok).toBeFalsy();
    const snap = await life.detail(request, runtime, trialId);
    expect(snap.last_lifecycle_error).toBeTruthy();
    expect(snap.status).not.toBe("suspended");
    const immediate = await life.tick(request, runtime, trialId);
    expect(immediate.runtime.stop_calls).toBe(1);
    await life.setClock(request, runtime, { advance_seconds: 61 });
    await life.setRuntime(request, runtime, { stop_fail: false });
    const recovered = await life.tick(request, runtime, trialId);
    expect(recovered.status).toBe("suspended");
    expect(recovered.runtime.stop_calls).toBe(2);
  });

  test("E. operator reactivation requires health and is idempotent", async ({ page, request, runtime, browser }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.reactivate.trial_id;
    await life.setRuntime(request, runtime, { stopped: true, health: true });
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const denied = await page.request.post(`/api/operator/platform/trials/${trialId}/reactivate`);
    expect(denied.status()).toBe(403);
    const operator = await life.openOperatorContext(browser, runtime);
    try {
      const first = await operator.page.request.post(`/api/operator/platform/trials/${trialId}/reactivate`);
      expect(first.ok()).toBeTruthy();
      const body = await first.json();
      expect(body.status).toBe("trial_active");
      const rt = await life.setRuntime(request, runtime, {});
      expect(rt.start_calls).toBe(1);
      const second = await operator.page.request.post(`/api/operator/platform/trials/${trialId}/reactivate`);
      expect(second.ok()).toBeTruthy();
      const rt2 = await request.get(`${runtime.baseURL}/e2e/runtime`, { headers: { "x-e2e-secret": runtime.authSecret } });
      expect((await rt2.json()).start_calls).toBe(1);
      const snap = await life.detail(request, runtime, trialId);
      expect(snap.tenant.tenant_code).toBe("e2e_g3c_reactivate");
      expect(snap.status).toBe("trial_active");
    } finally {
      await operator.close();
    }
    const ui = new LifecyclePage(page);
    await ui.openTrial(trialId);
    await expect(page.getByTestId("lifecycle-reactivation-status")).toBeVisible();
  });

  test("E2. health failure leaves a retryable suspended state", async ({ request, runtime, browser }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.reactivate_health.trial_id;
    await life.setRuntime(request, runtime, { stopped: true, health: false });
    const operator = await life.openOperatorContext(browser, runtime);
    try {
      const res = await operator.page.request.post(`/api/operator/platform/trials/${trialId}/reactivate`);
      expect(res.status()).toBe(400);
      const snap = await life.detail(request, runtime, trialId);
      expect(snap.status).toBe("suspended");
      expect(snap.last_lifecycle_error).toMatch(/health/i);
    } finally {
      await operator.close();
    }
  });

  test("F. conversion stores converted_at without billing side effects", async ({ request, runtime, browser }) => {
    const fx = await life.fixtures(request, runtime);
    const trialId = fx.trials.convert.trial_id;
    const before = await life.detail(request, runtime, trialId);
    const operator = await life.openOperatorContext(browser, runtime);
    try {
      const res = await operator.page.request.post(
        `/api/operator/platform/trials/${trialId}/convert?subscription_id=${fx.convert_subscription_id}`,
      );
      expect(res.ok()).toBeTruthy();
      const body = await res.json();
      expect(body.billing).toBeFalsy();
      expect(body.converted_at).toBeTruthy();
      expect(JSON.stringify(body).toLowerCase()).not.toContain("invoice");
      expect(JSON.stringify(body).toLowerCase()).not.toContain("payment");
    } finally {
      await operator.close();
    }
    const after = await life.detail(request, runtime, trialId);
    expect(after.converted_at).toBeTruthy();
    expect(after.conversion_subscription_ref).toBe(String(fx.convert_subscription_id));
    expect(after.customer_subscription_count).toBe(before.customer_subscription_count);
    expect(after.tenant.customer_subscription_id).toBeNull();
    expect(after.tenant.platform_trial_id).toBe(trialId);
    await life.setClock(request, runtime, { advance_days: 10, advance_seconds: 5 });
    const ticked = await life.tick(request, runtime, trialId);
    expect(ticked.action).toBe("skipped_converted");
    expect(ticked.status).toBe("converted");
    const operator2 = await life.openOperatorContext(browser, runtime);
    try {
      const terminate = await operator2.page.request.post(`/api/operator/platform/trials/${trialId}/terminate`);
      expect(terminate.status()).toBe(400);
    } finally {
      await operator2.close();
    }
  });

  test("G. termination safeguards and fake cleanup", async ({ page, request, runtime, browser }) => {
    const fx = await life.fixtures(request, runtime);
    const activeId = fx.trials.countdown.trial_id;
    const termId = fx.trials.terminate.trial_id;
    const otherId = fx.trials.other.trial_id;
    const templateId = fx.trials.template.trial_id;
    expect(fx.auto_terminate_enabled).toBeFalsy();
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    expect((await page.request.post(`/api/operator/platform/trials/${termId}/terminate`)).status()).toBe(403);
    expect((await page.request.post(`/api/operator/platform/trials/${termId}/terminate/execute`)).status()).toBe(403);
    const anon = await request.post(`${runtime.baseURL}/api/operator/platform/trials/${termId}/terminate`);
    expect(anon.status()).toBe(401);
    const operator = await life.openOperatorContext(browser, runtime);
    try {
      expect((await operator.page.request.post(`/api/operator/platform/trials/${activeId}/terminate`)).status()).toBe(400);
      await life.setClock(request, runtime, { advance_days: 7, advance_seconds: 2 });
      await life.tick(request, runtime, termId);
      await life.setClock(request, runtime, { advance_days: 3, advance_seconds: 2 });
      await life.tick(request, runtime, termId);
      const requested = await operator.page.request.post(`/api/operator/platform/trials/${termId}/terminate`);
      expect(requested.ok()).toBeTruthy();
      expect((await requested.json()).status).toBe("termination_pending");
      const early = await operator.page.request.post(`/api/operator/platform/trials/${termId}/terminate/execute`);
      expect(early.status()).toBe(400);
      await life.setClock(request, runtime, { advance_days: 30, advance_seconds: 5 });
      const lateTick = await life.tick(request, runtime, termId);
      expect(lateTick.status).toBe("termination_pending");
      const executed = await operator.page.request.post(`/api/operator/platform/trials/${termId}/terminate/execute`);
      expect(executed.ok()).toBeTruthy();
      expect((await executed.json()).status).toBe("terminated");
      const rt = await request.get(`${runtime.baseURL}/e2e/runtime`, { headers: { "x-e2e-secret": runtime.authSecret } });
      expect((await rt.json()).destroy_calls).toBe(1);
      const again = await operator.page.request.post(`/api/operator/platform/trials/${termId}/terminate/execute`);
      expect(again.status()).toBe(400);
      const rt2 = await request.get(`${runtime.baseURL}/e2e/runtime`, { headers: { "x-e2e-secret": runtime.authSecret } });
      expect((await rt2.json()).destroy_calls).toBe(1);
      const templateExec = await operator.page.request.post(`/api/operator/platform/trials/${templateId}/terminate/execute`);
      expect(templateExec.status()).toBe(400);
    } finally {
      await operator.close();
    }
    const ui = new LifecyclePage(page);
    await ui.openTrial(termId);
    await expect(page.getByTestId("lifecycle-termination-status")).toContainText("terminated");
    const other = await life.detail(request, runtime, otherId);
    expect(other.status).toBe("trial_active");
    for (const target of ["solution", "customer_subscription", "template"]) {
      const probe = await life.probeProtection(request, runtime, target);
      expect(probe.protected).toBeTruthy();
    }
  });

  test("H. ownership, CSRF, and operator boundaries", async ({ page, request, runtime, browser }) => {
    const fx = await life.fixtures(request, runtime);
    await life.loginAs(page, runtime, runtime.customerLogin, runtime.customerPassword);
    const otherPage = await page.request.get(`/portal/platform/trials/${fx.trials.other.trial_id}`);
    expect(otherPage.status()).toBe(404);
    expect((await page.request.get(`/api/platform/trials/${fx.trials.other.trial_id}/lifecycle`)).status()).toBe(404);
    expect((await request.get(`${runtime.baseURL}/api/platform/trials/${fx.trials.countdown.trial_id}/lifecycle`)).status()).toBe(401);
    const operator = await life.openOperatorContext(browser, runtime);
    try {
      const csrf = await operator.page.request.post(`/operator/platform/trials/${fx.trials.reactivate.trial_id}/reactivate`, {
        form: { csrf_token: "" },
      });
      expect(csrf.status()).toBe(403);
    } finally {
      await operator.close();
    }
    const ui = new LifecyclePage(page);
    await ui.openTrial(fx.trials.countdown.trial_id);
    const html = await page.content();
    life.assertNoSecrets(html, runtime);
    expect(html).not.toContain("mosh_r_e2e_countdown");
  });
});
