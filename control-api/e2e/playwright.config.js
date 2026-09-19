const fs = require("fs");
const os = require("os");
const path = require("path");
const { defineConfig, devices } = require("playwright/test");

const E2E_DIR = __dirname;
const AUTH_DIR = path.join(E2E_DIR, ".auth");
const firefoxInstalled = fs.existsSync(path.join(os.homedir(), ".cache", "ms-playwright", "firefox-1538"));

const cloudOnboardingUse = {
  storageState: { cookies: [], origins: [] },
  trace: "on",
  video: "on",
  screenshot: "only-on-failure",
};

const projects = [
  {
    name: "setup",
    testMatch: /auth\.setup\.spec\.js/,
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      storageState: { cookies: [], origins: [] },
    },
  },
  {
    name: "auth-chromium",
    testMatch: /auth\.spec\.js/,
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      storageState: { cookies: [], origins: [] },
    },
  },
  {
    name: "desktop-chromium",
    testIgnore: /auth\.spec\.js|auth\.setup\.spec\.js|responsive\.spec\.js|three-product-lines\.spec\.js|lifecycle|cloud-onboarding-full-journey|quick-demo-community/,
    dependencies: ["setup"],
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      storageState: path.join(AUTH_DIR, "user.json"),
    },
  },
  {
    name: "quick-demo-chromium",
    testMatch: /quick-demo-community\.spec\.js/,
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      storageState: { cookies: [], origins: [] },
    },
  },
  {
    name: "mobile-chromium",
    testMatch: /(^|\/)responsive\.spec\.js$/,
    dependencies: ["setup"],
    use: {
      browserName: "chromium",
      ...devices["Pixel 5"],
      storageState: path.join(AUTH_DIR, "user.json"),
    },
  },
  {
    name: "cloud-onboarding-chromium",
    testMatch: /cloud-onboarding-full-journey\.spec\.js/,
    retries: 0,
    timeout: 180_000,
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      ...cloudOnboardingUse,
    },
  },
  {
    name: "cloud-onboarding-mobile",
    testMatch: /cloud-onboarding-full-journey\.spec\.js/,
    grep: /@cloud-primary/,
    retries: 0,
    timeout: 180_000,
    use: {
      browserName: "chromium",
      ...devices["Pixel 5"],
      viewport: { width: 390, height: 844 },
      ...cloudOnboardingUse,
    },
  },
  {
    name: "product-lines-chromium",
    testMatch: /three-product-lines\.spec\.js/,
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      storageState: { cookies: [], origins: [] },
    },
  },
  {
    name: "lifecycle-chromium",
    testMatch: /lifecycle\.spec\.js/,
    use: {
      browserName: "chromium",
      viewport: { width: 1280, height: 720 },
      storageState: { cookies: [], origins: [] },
    },
  },
  {
    name: "lifecycle-mobile",
    testMatch: /lifecycle\.responsive\.spec\.js/,
    use: {
      browserName: "chromium",
      ...devices["Pixel 5"],
      storageState: { cookies: [], origins: [] },
    },
  },
];

if (firefoxInstalled) {
  projects.push({
    name: "desktop-firefox",
    testMatch: /auth\.spec\.js/,
    use: {
      browserName: "firefox",
      viewport: { width: 1280, height: 720 },
      storageState: { cookies: [], origins: [] },
    },
  });
  projects.push({
    name: "cloud-onboarding-firefox",
    testMatch: /cloud-onboarding-full-journey\.spec\.js/,
    grep: /@cloud-primary/,
    retries: 0,
    timeout: 180_000,
    use: {
      browserName: "firefox",
      viewport: { width: 1280, height: 720 },
      ...cloudOnboardingUse,
    },
  });
}

module.exports = defineConfig({
  testDir: path.join(E2E_DIR, "specs"),
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: path.join(E2E_DIR, "playwright-report"), open: "never" }],
    ["json", { outputFile: path.join(E2E_DIR, "playwright-report", "results.json") }],
    ["junit", { outputFile: path.join(E2E_DIR, "playwright-report", "results.xml") }],
  ],
  globalSetup: path.join(E2E_DIR, "helpers", "global-setup.js"),
  globalTeardown: path.join(E2E_DIR, "helpers", "global-teardown.js"),
  outputDir: path.join(E2E_DIR, "test-results"),
  use: {
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "on-first-retry",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects,
});
