const { startIsolatedApp } = require("./compose");

module.exports = async function globalSetup() {
  await startIsolatedApp();
};
