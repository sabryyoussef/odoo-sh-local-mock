const ALLOWED_CONSOLE = [
  /favicon\.ico/i,
  /Download the React DevTools/i,
];

const ALLOWED_FAILED = [
  /favicon\.ico/i,
  /net::ERR_ABORTED/i,
  /NS_BINDING_ABORTED/i,
];

// Explicitly expected client errors. Anything else 4xx/5xx still fails the test.
const EXPECTED_CLIENT_ERRORS = [
  { method: "POST", path: /^\/cloud\/register\/?$/, statuses: [400] },
  { method: "POST", path: /^\/cloud\/login\/?$/, statuses: [400] },
  { method: "POST", path: /^\/cloud\/setup\/?$/, statuses: [400] },
  { method: "GET", path: /^\/cloud\/setup\/quote\/?$/, statuses: [400, 401] },
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

function resourcePath(url) {
  try {
    return new URL(url).pathname;
  } catch {
    return url || "";
  }
}

function isExpectedClientError(method, url, status) {
  const path = resourcePath(url);
  return EXPECTED_CLIENT_ERRORS.some(
    (rule) => rule.method === method && rule.path.test(path) && rule.statuses.includes(status),
  );
}

function isAllowedConsole(text) {
  return ALLOWED_CONSOLE.some((re) => re.test(text));
}

function isAllowedFailedRequest(url, errorText) {
  const hay = `${url} ${errorText || ""}`;
  return ALLOWED_FAILED.some((re) => re.test(hay));
}

function isChromeStatusConsole(text) {
  return /Failed to load resource: the server responded with a status of (\d+)/i.test(text || "");
}

function chromeStatusFromConsole(text) {
  const match = /status of (\d+)/i.exec(text || "");
  return match ? Number(match[1]) : null;
}

function attachErrorCollectors(page, bag, baseURL) {
  bag.expected4xx = bag.expected4xx || [];
  bag.http4xx = bag.http4xx || [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") {
      return;
    }
    const text = msg.text();
    if (isAllowedConsole(text)) {
      return;
    }
    if (isChromeStatusConsole(text)) {
      const status = chromeStatusFromConsole(text);
      const expected = (bag.expected4xx || []).some((row) => row.status === status);
      if (expected && status >= 400 && status < 500) {
        return;
      }
    }
    bag.console.push(text);
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
    const status = res.status();
    const method = res.request().method();
    if (status >= 500) {
      bag.http5xx.push(`${status} ${method} ${url}`);
      return;
    }
    if (status >= 400) {
      if (isExpectedClientError(method, url, status)) {
        bag.expected4xx.push({ method, url, status });
        return;
      }
      bag.http4xx.push(`${status} ${method} ${url}`);
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
  if ((bag.http4xx || []).length) {
    parts.push(`unexpected 4xx responses: ${bag.http4xx.join(" | ")}`);
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
  isExpectedClientError,
  EXPECTED_CLIENT_ERRORS,
};
