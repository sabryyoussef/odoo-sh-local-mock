(() => {
  "use strict";

  const TERMINAL_STATES = new Set([
    "active",
    "expired",
    "deleted",
    "failed",
  ]);

const INITIAL_DELAY_MS = 1500;
const MAX_DELAY_MS = 8000;
const BACKOFF_FACTOR = 1.5;
const MAX_POLLS = 40;

  const SECRET_KEYS = new Set([
    "csrf_token",
    "database_name",
    "role_name",
    "filestore_path",
    "internal_url",
    "password",
    "secret",
    "token",
    "runtime_id",
    "visitor_key",
  ]);

  function prefersReducedMotion() {
    return Boolean(
      window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    );
  }

  function initStartForm() {
    const form = document.querySelector("[data-quick-demo-start]");
    if (!form) return;
    const btn = form.querySelector("#quick-demo-start-cta");
    form.addEventListener("submit", () => {
      if (!btn) return;
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
    });
  }

  function setProgress(root, percent) {
    const value = Math.max(0, Math.min(100, Number(percent) || 0));
    const bar = root.querySelector("[data-qd-progressbar]");
    const fill = root.querySelector("[data-qd-progress-fill]");
    const text = root.querySelector("[data-qd-progress-text]");
    if (bar) bar.setAttribute("aria-valuenow", String(value));
    if (fill) {
      if (prefersReducedMotion()) {
        fill.style.transition = "none";
      }
      fill.style.width = `${value}%`;
    }
    if (text) text.textContent = `${value}%`;
    root.dataset.qdProgress = String(value);
  }

  function setStatusLabel(root, message) {
    const label = root.querySelector("[data-qd-status-label]");
    if (label && message) label.textContent = message;
  }

  function updateLaunchSlot(root, payload) {
    const slot = root.querySelector("[data-qd-launch-slot]");
    if (!slot) return;
    const canLaunch = Boolean(payload.can_launch);
    const href = payload.open_href;
    let open = slot.querySelector('[data-qd-cta="open"]');
    if (canLaunch && href) {
      if (!open) {
        open = document.createElement("a");
        open.className = "btn btn--primary quick-demo__touch";
        open.dataset.qdCta = "open";
        open.id = "quick-demo-open-cta";
        open.textContent = root.getAttribute("data-qd-open-label") || "Open HMS";
        slot.appendChild(open);
      }
      open.setAttribute("href", href);
    } else if (open) {
      open.remove();
    }
  }

  function applyPayload(root, payload) {
    if (!payload || typeof payload !== "object") return;
    const state = String(payload.state || "");
    if (state) {
      root.dataset.qdState = state;
      const panel = root.querySelector("[data-qd-state-panel]");
      if (panel) panel.setAttribute("data-qd-state-panel", state);
    }
    if (typeof payload.progress_percent !== "undefined") {
      setProgress(root, payload.progress_percent);
    }
    if (payload.status_message) {
      setStatusLabel(root, payload.status_message);
    }
    root.dataset.qdCanLaunch = payload.can_launch ? "true" : "false";
    updateLaunchSlot(root, payload);

    // Never render secret/internal fields into the DOM from poll payloads.
    Object.keys(payload).forEach((key) => {
      if (SECRET_KEYS.has(key) || /secret|password|token|internal/i.test(key)) {
        return;
      }
    });
  }

  function shouldStop(root, payload) {
    const state = String((payload && payload.state) || root.dataset.qdState || "");
    return TERMINAL_STATES.has(state);
  }

  function initStatusPolling() {
    const root = document.querySelector('[data-quick-demo-page="status"]');
    if (!root) return;
    if (root.dataset.pollEnabled !== "true") return;
    const url = root.dataset.statusUrl;
    if (!url) return;

    let delay = INITIAL_DELAY_MS;
    let polls = 0;
    let timer = null;
    let stopped = false;

    const openLabel = root.querySelector('[data-qd-cta="open"]');
    if (openLabel) {
      root.setAttribute("data-qd-open-label", openLabel.textContent.trim());
    }

    function stop() {
      stopped = true;
      root.dataset.pollEnabled = "false";
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
    }

    async function tick() {
      if (stopped) return;
      polls += 1;
      if (polls > MAX_POLLS) {
        stop();
        return;
      }
      try {
        const res = await fetch(url, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!res.ok) {
          throw new Error(`status ${res.status}`);
        }
        const payload = await res.json();
        applyPayload(root, payload);
        if (shouldStop(root, payload)) {
          stop();
          return;
        }
      } catch (_err) {
        // Keep trying with backoff; do not reload the page.
      }
      delay = Math.min(MAX_DELAY_MS, Math.round(delay * BACKOFF_FACTOR));
      timer = window.setTimeout(tick, delay);
    }

    timer = window.setTimeout(tick, delay);

    document.addEventListener("visibilitychange", () => {
      if (document.hidden || stopped) return;
      if (!timer && root.dataset.pollEnabled === "true") {
        delay = INITIAL_DELAY_MS;
        timer = window.setTimeout(tick, delay);
      }
    });
  }

  function boot() {
    initStartForm();
    initStatusPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Expose for presentational / unit-style contract checks in Playwright.
  window.HelpersQuickDemo = {
    TERMINAL_STATES,
    INITIAL_DELAY_MS,
    MAX_DELAY_MS,
    applyPayload,
    shouldStop,
    setProgress,
  };
})();
