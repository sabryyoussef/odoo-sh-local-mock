const ALLOWED_CONSOLE = [
  /favicon\.ico/i,
  /Download the React DevTools/i,
];

const ALLOWED_FAILED = [
  /favicon\.ico/i,
  /net::ERR_ABORTED/i,
  /NS_BINDING_ABORTED/i,
];

function isSameOrigin(url, baseURL) {
  try {
    const target = new URL(url);
    const base = new URL(baseURL);
    return target.origin === base.origin;
  } catch {
    return false;
  }
}

function isAllowedConsole(text) {
  return ALLOWED_CONSOLE.some((re) => re.test(text));
}

function isAllowedFailedRequest(url, errorText) {
  const hay = `${url} ${errorText || ""}`;
  return ALLOWED_FAILED.some((re) => re.test(hay));
}

function attachErrorCollectors(page, bag, baseURL) {
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const text = msg.text();
      if (!isAllowedConsole(text)) {
        bag.console.push(text);
      }
    }
  });
  page.on("pageerror", (err) => {
    bag.page.push(String(err && err.message ? err.message : err));
  });
  page.on("requestfailed", (req) => {
    const url = req.url();
    const err = req.failure() ? req.failure().errorText : "";
    if (!isSameOrigin(url, baseURL)) {
      return;
    }
    if (isAllowedFailedRequest(url, err)) {
      return;
    }
    bag.failed.push(`${req.method()} ${url} ${err}`.trim());
  });
  page.on("response", (res) => {
    const url = res.url();
    if (!isSameOrigin(url, baseURL)) {
      return;
    }
    if (res.status() >= 500) {
      bag.http5xx.push(`${res.status()} ${res.request().method()} ${url}`);
    }
  });
}

function assertNoUnexpectedErrors(bag) {
  const parts = [];
  if (bag.console.length) {
    parts.push(`console errors: ${bag.console.join(" | ")}`);
  }
  if (bag.page.length) {
    parts.push(`page errors: ${bag.page.join(" | ")}`);
  }
  if (bag.failed.length) {
    parts.push(`failed requests: ${bag.failed.join(" | ")}`);
  }
  if (bag.http5xx.length) {
    parts.push(`5xx responses: ${bag.http5xx.join(" | ")}`);
  }
  if (parts.length) {
    throw new Error(`Unexpected browser/application errors. ${parts.join("; ")}`);
  }
}

module.exports = {
  attachErrorCollectors,
  assertNoUnexpectedErrors,
  isSameOrigin,
};
