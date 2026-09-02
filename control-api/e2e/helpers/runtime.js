const fs = require("fs");
const path = require("path");

function runtimePath() {
  return path.join(__dirname, "..", ".auth", "runtime.json");
}

function loadRuntime() {
  const file = runtimePath();
  if (!fs.existsSync(file)) {
    throw new Error(
      "Isolated Playwright runtime is missing. The e2e wrapper must start the disposable control-api first.",
    );
  }
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

module.exports = { loadRuntime, runtimePath };
