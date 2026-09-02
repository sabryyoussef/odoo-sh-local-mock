class LoginPage {
  constructor(page) {
    this.page = page;
  }

  async gotoE2e() {
    await this.page.goto("/e2e/login");
  }

  async submit(username, password) {
    await this.page.getByLabel("Username").fill(username);
    await this.page.getByLabel("Password").fill(password);
    await this.page.getByRole("button", { name: "Sign in" }).click();
  }
}

class WizardPage {
  constructor(page) {
    this.page = page;
  }

  async selectOdoo19() {
    await this.page.getByTestId("version-option-19.0").click();
    await this.page.getByRole("button", { name: "Continue" }).click();
  }

  async selectTrialPlan() {
    await this.page.getByTestId("plan-option-trial").click();
    await this.page.getByRole("button", { name: "Continue" }).click();
  }

  module(name) {
    return this.page.getByTestId(`module-${name}`);
  }

  async toggleModule(name) {
    await this.module(name).locator("input[type=checkbox]").click();
  }

  async search(term) {
    await this.page.getByLabel("Search apps").fill(term);
    await this.page.getByRole("button", { name: "Search" }).click();
  }

  async continueToReview() {
    await this.page.getByRole("button", { name: "Review" }).click();
  }

  async confirm() {
    await this.page.getByRole("button", { name: "Confirm" }).click();
  }
}

class LifecyclePage {
  constructor(page) {
    this.page = page;
  }

  panel() {
    return this.page.getByTestId("lifecycle-panel");
  }

  state() {
    return this.page.getByTestId("lifecycle-state");
  }

  countdown() {
    return this.page.getByTestId("lifecycle-countdown");
  }

  async openTrial(trialId) {
    await this.page.goto(`/portal/platform/trials/${trialId}`);
  }
}

module.exports = { LoginPage, WizardPage, LifecyclePage };
