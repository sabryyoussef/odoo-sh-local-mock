(() => {
  const filterInput = document.getElementById("branch-filter");
  if (filterInput) {
    filterInput.addEventListener("input", () => {
      const q = filterInput.value.trim().toLowerCase();
      document.querySelectorAll(".sidebar__branch").forEach((el) => {
        const name = el.getAttribute("data-branch-name") || "";
        el.classList.toggle("is-hidden", Boolean(q) && !name.includes(q));
      });
    });
  }

  const projectSearch = document.getElementById("project-search");
  if (projectSearch) {
    projectSearch.addEventListener("input", () => {
      const q = projectSearch.value.trim().toLowerCase();
      document.querySelectorAll(".project-card").forEach((el) => {
        const name = el.getAttribute("data-project-name") || "";
        if (name === "__create__") {
          el.classList.toggle("is-hidden", Boolean(q));
          return;
        }
        el.classList.toggle("is-hidden", Boolean(q) && !name.includes(q));
      });
    });
  }

  const grid = document.getElementById("project-grid");
  document.querySelectorAll(".view-toggle__btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".view-toggle__btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-pressed", "true");
      if (!grid) return;
      grid.classList.toggle("is-list", btn.getAttribute("data-view") === "list");
    });
  });

  const copyBtn = document.getElementById("copy-clone");
  const cloneCmd = document.getElementById("clone-command");
  if (copyBtn && cloneCmd) {
    copyBtn.addEventListener("click", async () => {
      const text = cloneCmd.textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        copyBtn.textContent = "Copied";
        setTimeout(() => {
          copyBtn.textContent = "Copy";
        }, 1200);
      } catch (_) {
        copyBtn.textContent = "Failed";
        setTimeout(() => {
          copyBtn.textContent = "Copy";
        }, 1200);
      }
    });
  }

  const tabBody = document.getElementById("tab-body");
  const tabs = document.querySelectorAll(".content-tabs__tab");
  tabs.forEach((tab) => {
    if (tab.tagName === "A" && tab.getAttribute("href")) return;
    tab.addEventListener("click", () => {
      const id = tab.getAttribute("data-tab");
      tabs.forEach((t) => {
        if (t.tagName !== "A") t.classList.remove("is-active");
      });
      tab.classList.add("is-active");
      if (!tabBody) return;
      if (id === "history") {
        if (!window.__historyHtml) window.__historyHtml = tabBody.innerHTML;
        else tabBody.innerHTML = window.__historyHtml;
      } else {
        if (!window.__historyHtml) window.__historyHtml = tabBody.innerHTML;
        tabBody.innerHTML = `
          <div class="placeholder-panel">
            <h2>${(id || "").toUpperCase()}</h2>
            <p>Placeholder — not implemented in this batch.</p>
          </div>
        `;
      }
    });
  });
  if (tabBody && tabBody.querySelector(".history-timeline")) {
    window.__historyHtml = tabBody.innerHTML;
  }

  // Deploy wizard subscription validation
  const validateBtn = document.getElementById("validate-sub");
  const codeInput = document.getElementById("subscription-code");
  const subResult = document.getElementById("sub-result");
  const validCodeEl = document.getElementById("sub-valid-code");
  const deploySubmit = document.getElementById("deploy-submit");
  const sumSub = document.getElementById("sum-sub");
  let subscriptionOk = false;

  function syncSummary() {
    const repo = document.getElementById("deploy-repo");
    const name = document.getElementById("deploy-name");
    const version = document.getElementById("deploy-version");
    const location = document.getElementById("deploy-location");
    if (document.getElementById("sum-repo") && repo) document.getElementById("sum-repo").textContent = repo.value;
    if (document.getElementById("sum-name") && name) document.getElementById("sum-name").textContent = name.value;
    if (document.getElementById("sum-version") && version) document.getElementById("sum-version").textContent = version.value;
    if (document.getElementById("sum-location") && location) document.getElementById("sum-location").textContent = location.value;
  }

  ["deploy-repo", "deploy-name", "deploy-version", "deploy-location"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", syncSummary);
    if (el) el.addEventListener("change", syncSummary);
  });
  syncSummary();

  if (validateBtn && codeInput && subResult && validCodeEl) {
    validateBtn.addEventListener("click", () => {
      const expected = (validCodeEl.value || "").trim();
      const entered = (codeInput.value || "").trim();
      subResult.hidden = false;
      if (entered === expected) {
        subscriptionOk = true;
        subResult.className = "sub-result sub-result--ok";
        subResult.innerHTML = `
          <strong>Subscription Status: Valid</strong>
          <div>Plan: ${subResult.dataset.plan}</div>
          <div>Code: ${subResult.dataset.code}</div>
          <div>Expires: ${subResult.dataset.expires}</div>
          <div>Projects allowed: ${subResult.dataset.allowed}</div>
          <div>Projects used: ${subResult.dataset.used}</div>
          <div>Odoo versions: ${subResult.dataset.versions}</div>
        `;
        if (sumSub) sumSub.textContent = `${subResult.dataset.code} · Valid`;
      } else {
        subscriptionOk = false;
        subResult.className = "sub-result sub-result--bad";
        subResult.innerHTML = `<strong>Subscription not recognized</strong><div>Enter ${expected} for the demo.</div>`;
        if (sumSub) sumSub.textContent = "Not validated";
      }
      if (deploySubmit) deploySubmit.disabled = !subscriptionOk;
    });
    codeInput.addEventListener("input", () => {
      subscriptionOk = false;
      if (deploySubmit) deploySubmit.disabled = true;
      if (sumSub) sumSub.textContent = "Not validated";
    });
  }

  const deployForm = document.getElementById("deploy-form");
  if (deployForm) {
    deployForm.addEventListener("submit", (e) => {
      if (!subscriptionOk) {
        e.preventDefault();
        if (validateBtn) validateBtn.click();
        if (!subscriptionOk) e.preventDefault();
      }
    });
  }

  // Deploy progress animation
  const progressList = document.getElementById("progress-list");
  const progressFill = document.getElementById("progress-fill");
  const progressTitle = document.getElementById("progress-title");
  const progressStatus = document.getElementById("progress-status");
  const completeCard = document.getElementById("complete-card");
  if (progressList && progressFill) {
    const items = Array.from(progressList.querySelectorAll(".progress-item"));
    const messages = [
      "Connecting repository...",
      "Validating configuration...",
      "Creating project...",
      "Detecting branches...",
      "Preparing environments...",
      "Finalizing platform...",
      "Almost done...",
      "Deployment complete",
    ];
    let i = 0;
    const tick = () => {
      if (i >= items.length) {
        if (progressTitle) progressTitle.textContent = "Deployment Complete";
        if (progressStatus) progressStatus.textContent = "Your mock platform is ready.";
        if (completeCard) completeCard.hidden = false;
        progressFill.style.width = "100%";
        return;
      }
      items[i].classList.add("is-done");
      const mark = items[i].querySelector(".progress-item__mark");
      if (mark) mark.textContent = "✓";
      if (progressStatus) progressStatus.textContent = messages[Math.min(i, messages.length - 1)];
      progressFill.style.width = `${((i + 1) / items.length) * 100}%`;
      i += 1;
      setTimeout(tick, 550);
    };
    setTimeout(tick, 400);
  }

  // Log filters
  const logFilters = document.getElementById("log-filters");
  if (logFilters) {
    logFilters.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        logFilters.querySelectorAll("button").forEach((b) => b.classList.remove("is-filter-active"));
        btn.classList.add("is-filter-active");
        const channel = btn.getAttribute("data-channel");
        document.querySelectorAll(".log-line").forEach((line) => {
          const ch = line.getAttribute("data-channel");
          line.hidden = channel !== "all" && ch !== channel;
        });
      });
    });
  }

  // Account copy subscription
  const copySub = document.getElementById("copy-sub-code");
  const accountCode = document.getElementById("account-sub-code");
  if (copySub && accountCode) {
    copySub.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(accountCode.textContent.trim());
        copySub.textContent = "Copied";
        setTimeout(() => {
          copySub.textContent = "Copy Subscription Code";
        }, 1200);
      } catch (_) {
        copySub.textContent = "Failed";
      }
    });
  }

  // Checkout: Pay is a direct link now — no JS processing overlay
  // (kept for backwards compatibility if an old form is cached)
  const checkoutForm = document.getElementById("checkout-form");
  if (checkoutForm && checkoutForm.tagName === "FORM") {
    checkoutForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const url =
        checkoutForm.getAttribute("data-success-url") ||
        "/payment/success?plan=professional";
      window.location.href = url;
    });
  }

  // Payment success copy
  const copySuccess = document.getElementById("copy-success-code");
  const successCode = document.getElementById("success-sub-code");
  if (copySuccess && successCode) {
    copySuccess.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(successCode.textContent.trim());
        copySuccess.textContent = "Copied";
        setTimeout(() => {
          copySuccess.textContent = "Copy Subscription Code";
        }, 1200);
      } catch (_) {
        copySuccess.textContent = "Failed";
      }
    });
  }
})();
