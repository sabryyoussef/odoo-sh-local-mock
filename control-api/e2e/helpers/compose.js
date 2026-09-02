const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..", "..", "..");
const E2E_DIR = path.join(ROOT, "control-api", "e2e");
const AUTH_DIR = path.join(E2E_DIR, ".auth");
const COMPOSE_FILE = path.join(ROOT, "docker-compose.e2e.yml");
const COMPOSE_PROJECT = "mosh-e2e-g3a";
const LIVE_CONTROL_DB = path.join(ROOT, "data", "control.db");

function run(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, ...opts.env },
    stdio: opts.stdio || ["ignore", "pipe", "pipe"],
  });
  if (result.status !== 0 && opts.allowFail !== true) {
    const err = (result.stderr || result.stdout || "").trim();
    throw new Error(`${cmd} ${args.join(" ")} failed: ${err}`);
  }
  return result;
}

function uniqueId() {
  return `g3a_${Date.now().toString(36)}_${crypto.randomBytes(4).toString("hex")}`;
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function sqliteScalar(dbPath, sql) {
  if (!fs.existsSync(dbPath)) {
    return null;
  }
  const result = spawnSync("sqlite3", [dbPath, sql], { encoding: "utf8" });
  if (result.status !== 0) {
    return null;
  }
  return (result.stdout || "").trim();
}

function snapshotLive() {
  const docker = run("docker", ["ps", "-a", "--format", "{{.Names}}"], { allowFail: true });
  const names = (docker.stdout || "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const pg = run(
    "docker",
    [
      "exec",
      "odoo-sh-local-mock-build-postgres-1",
      "psql",
      "-U",
      "mosh_admin",
      "-d",
      "postgres",
      "-Atc",
      "SELECT datname FROM pg_database ORDER BY 1",
    ],
    { allowFail: true },
  );
  return {
    dockerNames: names,
    pgDatabases: (pg.stdout || "").split("\n").map((s) => s.trim()).filter(Boolean),
    maxDeploymentJob: sqliteScalar(LIVE_CONTROL_DB, "SELECT COALESCE(MAX(id),0) FROM deployment_jobs;"),
    maxTemplateJob: sqliteScalar(LIVE_CONTROL_DB, "SELECT COALESCE(MAX(id),0) FROM platform_template_build_jobs;"),
    tenantPt: sqliteScalar(
      LIVE_CONTROL_DB,
      "SELECT status || '|' || COALESCE(container_name,'') || '|' || COALESCE(http_port,'') FROM tenants WHERE tenant_code='pt_trial_1_a89ea9';",
    ),
    template: sqliteScalar(
      LIVE_CONTROL_DB,
      "SELECT state || '|' || validation_status || '|' || COALESCE(postgres_database_name,'') || '|' || COALESCE(installed_module_set_json,'') FROM template_databases WHERE template_code='odoo19-community-base-v1';",
    ),
  };
}

function waitForHealth(baseURL, timeoutMs = 90000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const result = spawnSync("curl", ["-sS", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}", `${baseURL}/health`], {
      encoding: "utf8",
    });
    if ((result.stdout || "").trim() === "200") {
      return;
    }
    spawnSync("sleep", ["0.5"]);
  }
  throw new Error(`Isolated control-api did not become healthy at ${baseURL}/health`);
}

async function startIsolatedApp() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  const port = await findFreePort();
  const env = {
    E2E_PORT: String(port),
    E2E_SESSION_SECRET: crypto.randomBytes(32).toString("hex"),
    E2E_AUTH_SECRET: crypto.randomBytes(24).toString("hex"),
    E2E_USER_LOGIN: "e2e_g3a_user",
    E2E_USER_PASSWORD: `E2e!${crypto.randomBytes(12).toString("base64url")}`,
    E2E_CUSTOMER_LOGIN: "e2e_g3c_customer",
    E2E_CUSTOMER_PASSWORD: `E2eC!${crypto.randomBytes(12).toString("base64url")}`,
    E2E_OPERATOR_LOGIN: "e2e_g3c_operator",
    E2E_OPERATOR_PASSWORD: `E2eO!${crypto.randomBytes(12).toString("base64url")}`,
    E2E_OTHER_LOGIN: "e2e_g3c_other",
    E2E_OTHER_PASSWORD: `E2eX!${crypto.randomBytes(12).toString("base64url")}`,
    OPERATOR_GITHUB_LOGINS: "e2e_g3c_operator",
    E2E_RUN_ID: uniqueId(),
  };
  const baseURL = `http://127.0.0.1:${port}`;
  const liveBefore = snapshotLive();
  const runtime = {
    baseURL,
    port,
    login: env.E2E_USER_LOGIN,
    password: env.E2E_USER_PASSWORD,
    customerLogin: env.E2E_CUSTOMER_LOGIN,
    customerPassword: env.E2E_CUSTOMER_PASSWORD,
    operatorLogin: env.E2E_OPERATOR_LOGIN,
    operatorPassword: env.E2E_OPERATOR_PASSWORD,
    otherLogin: env.E2E_OTHER_LOGIN,
    otherPassword: env.E2E_OTHER_PASSWORD,
    authSecret: env.E2E_AUTH_SECRET,
    storageStatePath: path.join(AUTH_DIR, "user.json"),
    liveBefore,
    composeProject: COMPOSE_PROJECT,
    runId: env.E2E_RUN_ID,
    hostname: os.hostname(),
  };
  fs.writeFileSync(path.join(AUTH_DIR, "runtime.json"), JSON.stringify(runtime, null, 2));
  fs.chmodSync(path.join(AUTH_DIR, "runtime.json"), 0o600);

  run(
    "docker",
    [
      "compose",
      "-p",
      COMPOSE_PROJECT,
      "-f",
      COMPOSE_FILE,
      "up",
      "-d",
      "--wait",
      "--wait-timeout",
      "90",
    ],
    { env },
  );
  waitForHealth(baseURL);
  const loginPage = spawnSync("curl", ["-sS", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", `${baseURL}/e2e/login`], {
    encoding: "utf8",
  });
  if ((loginPage.stdout || "").trim() !== "200") {
    throw new Error("Isolated /e2e/login is not ready; refusing to skip Playwright setup");
  }
  return runtime;
}

function stopIsolatedApp() {
  run(
    "docker",
    ["compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE, "down", "--remove-orphans", "-v"],
    { allowFail: true },
  );
}

function liveDrift(before, after) {
  const issues = [];
  if (before.maxDeploymentJob !== after.maxDeploymentJob) {
    issues.push(`live deployment_jobs max ${before.maxDeploymentJob} -> ${after.maxDeploymentJob}`);
  }
  if (before.maxTemplateJob !== after.maxTemplateJob) {
    issues.push(`live template_build_jobs max ${before.maxTemplateJob} -> ${after.maxTemplateJob}`);
  }
  if (before.tenantPt !== after.tenantPt) {
    issues.push("retained G2 tenant row changed");
  }
  if (before.template !== after.template) {
    issues.push("golden template row changed");
  }
  const newDocker = after.dockerNames.filter(
    (n) => !before.dockerNames.includes(n) && !n.includes("mosh-e2e-g3a") && !n.includes("control-api-e2e"),
  );
  if (newDocker.length) {
    issues.push(`new docker names: ${newDocker.join(",")}`);
  }
  const newPg = after.pgDatabases.filter((n) => !before.pgDatabases.includes(n));
  if (newPg.length) {
    issues.push(`new postgres databases: ${newPg.join(",")}`);
  }
  return issues;
}

module.exports = {
  ROOT,
  AUTH_DIR,
  COMPOSE_PROJECT,
  LIVE_CONTROL_DB,
  startIsolatedApp,
  stopIsolatedApp,
  snapshotLive,
  liveDrift,
};
