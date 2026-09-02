const fs = require("fs");
const path = require("path");
const { stopIsolatedApp, snapshotLive, liveDrift, AUTH_DIR } = require("./compose");
const { loadRuntime } = require("./runtime");

module.exports = async function globalTeardown() {
  let runtime;
  try {
    runtime = loadRuntime();
  } catch {
    stopIsolatedApp();
    return;
  }
  const after = snapshotLive();
  const issues = liveDrift(runtime.liveBefore || {}, after);
  stopIsolatedApp();
  const runtimeFile = path.join(AUTH_DIR, "runtime.json");
  const storage = path.join(AUTH_DIR, "user.json");
  if (fs.existsSync(runtimeFile)) {
    fs.unlinkSync(runtimeFile);
  }
  if (fs.existsSync(storage)) {
    fs.unlinkSync(storage);
  }
  if (issues.length) {
    throw new Error(`Live invariants drifted during isolated Playwright: ${issues.join("; ")}`);
  }
};
