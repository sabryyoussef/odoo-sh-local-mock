const { spawnSync } = require("child_process");
const { test, expect } = require("../fixtures");
const { WizardPage } = require("../pages");

test.describe("Safe confirmation boundary", () => {
  test("confirm queues an isolated job without worker claim or live resources", async ({
    page,
    e2eState,
    runtime,
  }) => {
    const wizard = new WizardPage(page);
    await page.goto("/platform/deploy/version");
    await wizard.selectOdoo19();
    await wizard.selectTrialPlan();
    await wizard.toggleModule("crm");
    await wizard.continueToReview();
    await expect(page.getByTestId("review-summary")).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm" })).toBeVisible();

    const csrf = await page.locator("input[name=csrf_token]").inputValue();
    const trialId = await page.locator("input[name=trial_id]").inputValue();
    const checksum = await page.locator("input[name=snapshot_checksum]").inputValue();
    const idempotency = await page.locator("input[name=idempotency_key]").inputValue();

    await wizard.confirm();
    await expect(page).toHaveURL(/\/portal\/platform\/trials\//);
    await expect(page.getByTestId("deployment-status")).toBeVisible();

    const afterFirst = await e2eState.getState();
    expect(afterFirst.isolated).toBe(true);
    expect(afterFirst.live_control_db).toBe(false);
    expect(afterFirst.job_count).toBe(1);
    expect(afterFirst.claimed_job_count).toBe(0);
    expect(afterFirst.tenant_count).toBe(0);
    expect(afterFirst.tenant_container_names).toEqual([]);
    expect(afterFirst.tenant_db_names).toEqual([]);

    const second = await page.request.post(`${runtime.baseURL}/platform/deploy/confirm`, {
      form: {
        csrf_token: csrf,
        trial_id: trialId,
        snapshot_checksum: checksum,
        idempotency_key: idempotency,
      },
    });
    expect(second.status()).toBeLessThan(500);

    const afterSecond = await e2eState.getState();
    expect(afterSecond.job_count).toBe(1);
    expect(afterSecond.claimed_job_count).toBe(0);
    expect(afterSecond.tenant_count).toBe(0);

    const docker = spawnSync("docker", ["ps", "-a", "--format", "{{.Names}}"], { encoding: "utf8" });
    const names = (docker.stdout || "").split("\n").filter(Boolean);
    const before = new Set((runtime.liveBefore && runtime.liveBefore.dockerNames) || []);
    const extra = names.filter(
      (n) => !before.has(n) && !n.includes("mosh-e2e-g3a") && !n.includes("control-api-e2e"),
    );
    expect(extra, `unexpected docker names: ${extra.join(",")}`).toEqual([]);
  });
});
