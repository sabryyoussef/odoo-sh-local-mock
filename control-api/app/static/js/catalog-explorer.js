(() => {
  const root = document.querySelector("[data-catalog-explorer]");
  if (!root) return;

  function currentLang() {
    const params = new URLSearchParams(window.location.search);
    return params.get("lang") || document.documentElement.lang || "en";
  }

  function buildUrl(code, { replaceHash = true } = {}) {
    const url = new URL(window.location.href);
    if (code) url.searchParams.set("solution", code);
    else url.searchParams.delete("solution");
    const lang = currentLang();
    if (lang && lang !== "en") url.searchParams.set("lang", lang);
    else if (lang === "en") url.searchParams.delete("lang");
    if (replaceHash) url.hash = "";
    return url;
  }

  const selected = root.getAttribute("data-selected");
  if (selected) {
    const params = new URLSearchParams(window.location.search);
    if (!params.get("solution")) {
      history.replaceState({ solution: selected }, "", buildUrl(selected));
    }
  }

  window.addEventListener("popstate", () => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("solution");
    if (code && code !== selected) {
      window.location.href = buildUrl(code);
    }
  });

  root.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (ev) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (target) {
        ev.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  const packageCards = root.querySelectorAll("[data-package-card]");
  const selectedPanel = document.getElementById("selected-package");
  const selectedName = document.getElementById("selected-package-name");
  const selectedPrice = document.getElementById("selected-package-price");
  const selectedEdition = document.getElementById("selected-package-edition");
  const selectedCta = document.getElementById("selected-package-cta");

  function trialHrefForPackage(card) {
    const id = card.getAttribute("data-package-id");
    const raw = card.getAttribute("data-trial-href");
    if (!raw) return "#";
    try {
      const url = new URL(raw, window.location.origin);
      if (id) url.searchParams.set("package_id", id);
      return `${url.pathname}${url.search}`;
    } catch (_err) {
      return raw;
    }
  }

  function selectPackage(card) {
    const id = card.getAttribute("data-package-id");
    const name = card.querySelector("h4") ? card.querySelector("h4").textContent.trim() : "";
    const price = card.querySelector(".package-card__amount")
      ? card.querySelector(".package-card__amount").textContent.trim()
      : "";
    const edition = card.querySelector(".package-card__edition")
      ? card.querySelector(".package-card__edition").textContent.trim()
      : "";
    const trialHref = trialHrefForPackage(card);

    packageCards.forEach((c) => c.classList.remove("package-card--selected"));
    card.classList.add("package-card--selected");

    if (selectedPanel && selectedName && selectedPrice) {
      selectedName.textContent = name;
      selectedPrice.textContent = price;
      if (selectedEdition) selectedEdition.textContent = edition;
      if (selectedCta && trialHref) {
        selectedCta.href = trialHref;
      }
      selectedPanel.hidden = false;
      selectedPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    const url = new URL(window.location.href);
    url.searchParams.set("package_id", id);
    history.replaceState({ packageId: id }, "", url.toString());
  }

  packageCards.forEach((card) => {
    const btn = card.querySelector("[data-select-package]");
    if (btn) {
      btn.addEventListener("click", () => selectPackage(card));
    }
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("a, button")) return;
      selectPackage(card);
    });
  });

  const initialParams = new URLSearchParams(window.location.search);
  const initialPkg = initialParams.get("package_id");
  if (initialPkg) {
    const card = root.querySelector(`[data-package-id="${initialPkg}"]`);
    if (card) selectPackage(card);
  }

  if ("IntersectionObserver" in window) {
    const lazyImages = root.querySelectorAll('img[loading="lazy"]');
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) observer.unobserve(entry.target);
      });
    });
    lazyImages.forEach((img) => observer.observe(img));
  }
})();
