(() => {
  const form = document.querySelector("[data-cloud-register-form]");

  document.querySelectorAll("[data-tooltip-trigger]").forEach((trigger) => {
    const tooltip = document.getElementById(trigger.getAttribute("aria-controls") || "");
    if (!tooltip) return;
    const open = () => {
      tooltip.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
    };
    const close = () => {
      tooltip.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    };
    trigger.addEventListener("mouseenter", open);
    trigger.addEventListener("focus", open);
    trigger.addEventListener("mouseleave", close);
    trigger.addEventListener("blur", close);
    trigger.addEventListener("click", () => {
      open();
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
  });

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const input = document.getElementById(button.getAttribute("data-password-toggle") || "");
    if (!input) return;
    const labelShow = button.getAttribute("data-label-show") || "Show password";
    const labelHide = button.getAttribute("data-label-hide") || "Hide password";
    button.addEventListener("click", () => {
      const visible = input.type === "text";
      input.type = visible ? "password" : "text";
      button.setAttribute("aria-pressed", visible ? "false" : "true");
      button.setAttribute("aria-label", visible ? labelShow : labelHide);
    });
  });

  if (!form) return;
  const submit = form.querySelector("button[type='submit']");
  const submittingLabel = form.getAttribute("data-submitting-label") || "Creating account...";

  function setError(input, message) {
    const error = document.getElementById(`err-${input.name}`);
    if (message) {
      if (error) {
        error.textContent = message;
        error.hidden = false;
      }
      input.setAttribute("aria-invalid", "true");
    } else {
      if (error) {
        error.textContent = "";
        error.hidden = true;
      }
      input.removeAttribute("aria-invalid");
    }
  }

  function validateInput(input) {
    const type = input.getAttribute("data-validate");
    if (!type) return true;
    const message = input.getAttribute("data-error-message") || "Please check this field.";
    let ok = true;
    if (type === "full_name") ok = input.value.trim().length >= 2;
    if (type === "email") ok = input.value.trim().length > 0 && (input.type !== "email" || input.validity.valid);
    if (type === "password") ok = input.value.length >= 8;
    if (type === "login_password") ok = input.value.length > 0;
    if (type === "password_confirm") {
      const password = form.querySelector("#password");
      ok = input.value.length >= 8 && (!password || input.value === password.value);
    }
    if (type === "terms") ok = input.checked;
    setError(input, ok ? "" : message);
    return ok;
  }

  form.querySelectorAll("[data-validate]").forEach((input) => {
    input.addEventListener("input", () => validateInput(input));
    input.addEventListener("change", () => validateInput(input));
    input.addEventListener("blur", () => validateInput(input));
  });

  form.addEventListener("submit", (event) => {
    const inputs = Array.from(form.querySelectorAll("[data-validate]"));
    const valid = inputs.map(validateInput).every(Boolean);
    if (!valid) {
      event.preventDefault();
      const firstInvalid = form.querySelector("[aria-invalid='true']");
      if (firstInvalid) firstInvalid.focus();
      return;
    }
    if (submit && !submit.disabled) {
      submit.dataset.originalLabel = submit.textContent || "";
      submit.textContent = submittingLabel;
      submit.disabled = true;
    }
  });
})();