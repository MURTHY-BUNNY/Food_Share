/**
 * Donor signup, in two steps.
 *
 * Step 1 collects the details and asks the API to email a 6-digit code; no
 * account exists yet. Step 2 takes that code, which is what creates the
 * account, and signs the new donor in.
 */
(function () {
  "use strict";

  const form = document.getElementById("donor-register-form");
  const messageBox = document.getElementById("donor-register-message");
  const accountType = document.getElementById("account_type");
  const organizationField = document.getElementById("organization-field");
  const organizationName = document.getElementById("organization_name");
  const submitButton = document.getElementById("donor-register-button");

  const verifyForm = document.getElementById("donor-verify-form");
  const verifyButton = document.getElementById("verify-submit-button");
  const panelTitle = document.getElementById("register-heading");
  const panelSubtitle = document.getElementById("register-subtitle");
  const verifyEmail = document.getElementById("verify-email");
  const verifyExpiry = document.getElementById("verify-expiry");
  const demoCodeBox = document.getElementById("verify-demo-code");
  const demoCodeValue = document.getElementById("verify-demo-value");
  const resendButton = document.getElementById("resend-code-button");
  const resendTimer = document.getElementById("resend-timer");
  const changeEmailButton = document.getElementById("change-email-button");
  const stepMarkers = document.querySelectorAll("[data-step-indicator]");

  if (!form || !verifyForm) {
    return;
  }

  const FALLBACK_COOLDOWN = 60;

  let pendingEmail = "";
  let cooldownTimer = null;
  let submitting = false;

  const otp = OtpInput.mount(document.getElementById("verify-code-input"), {
    length: 6,
    id: "verify-code",
    describedBy: "verify-code-help",
    onComplete: () => {
      verifyButton.disabled = false;

      if (!submitting) {
        submitCode();
      }
    }
  });

  /* ---------------- steps --------------------------------------- */

  function showStep(name) {
    const onVerify = name === "verify";

    form.hidden = onVerify;
    verifyForm.hidden = !onVerify;

    if (panelTitle) {
      panelTitle.textContent = onVerify ? "Verify your email" : "Account details";
    }

    if (panelSubtitle) {
      panelSubtitle.textContent = onVerify
        ? "Enter the 6-digit code we sent to your inbox."
        : "Fields marked with an asterisk are required.";
    }

    stepMarkers.forEach((marker) => {
      const index = Number(marker.dataset.stepIndicator);

      marker.classList.toggle("is-current", onVerify ? index === 2 : index === 1);
      marker.classList.toggle("is-done", onVerify && index === 1);
    });

    if (onVerify) {
      otp.focus();
    }
  }

  function syncOrganizationField() {
    const isOrganization = accountType.value === "Organization";

    organizationField.hidden = !isOrganization;
    organizationName.required = isOrganization;

    if (!isOrganization) {
      organizationName.value = "";
    }
  }

  /* ---------------- cooldown ------------------------------------ */

  function startCooldown(seconds) {
    const total = Number(seconds) > 0 ? Number(seconds) : FALLBACK_COOLDOWN;
    let remaining = total;

    window.clearInterval(cooldownTimer);
    resendButton.disabled = true;

    const paint = () => {
      if (remaining <= 0) {
        window.clearInterval(cooldownTimer);
        resendButton.disabled = false;
        resendTimer.textContent = "Still no code?";
        return;
      }

      resendTimer.textContent = `You can ask for a new code in ${remaining}s.`;
      remaining -= 1;
    };

    paint();
    cooldownTimer = window.setInterval(paint, 1000);
  }

  /* ---------------- step 1: request a code ---------------------- */

  function detailsPayload() {
    const data = new FormData(form);
    const isOrganization = data.get("account_type") === "Organization";
    const organization = String(data.get("organization_name") || "").trim();

    return {
      full_name: String(data.get("full_name") || "").trim(),
      account_type: isOrganization ? "Organization" : "Individual",
      organization_name: isOrganization ? organization : null,
      email: String(data.get("email") || "").trim().toLowerCase(),
      phone: String(data.get("phone") || "").trim(),
      location: String(data.get("location") || "").trim(),
      password: String(data.get("password") || ""),
      confirmation: String(data.get("confirm_password") || ""),
      organization: organization,
      isOrganization: isOrganization
    };
  }

  function showCodeStep(result, email) {
    pendingEmail = email;

    verifyEmail.textContent = email;
    verifyExpiry.textContent = result.expires_in_minutes || 15;

    if (result.delivery === "demo" && result.dev_code) {
      demoCodeValue.textContent = result.dev_code;
      demoCodeBox.hidden = false;
    } else {
      demoCodeBox.hidden = true;
    }

    otp.clear();
    verifyButton.disabled = true;
    verifyButton.innerHTML =
      '<i class="bi bi-shield-check" aria-hidden="true"></i> Verify and create account';

    showStep("verify");
    startCooldown(result.resend_after_seconds || FALLBACK_COOLDOWN);

    /* The recipient block above already names the address and the expiry, so
       the message box stays free for errors. */
    App.clearMessage(messageBox);
  }

  async function requestCode(event) {
    event.preventDefault();

    if (!form.reportValidity()) {
      return;
    }

    const payload = detailsPayload();

    App.clearMessage(messageBox);

    if (payload.password !== payload.confirmation) {
      App.showMessage(messageBox, "The two passwords do not match.", "error");
      return;
    }

    if (payload.isOrganization && payload.organization.length < 2) {
      App.showMessage(messageBox, "Enter the organization name.", "error");
      return;
    }

    const originalLabel = submitButton.innerHTML;

    submitButton.disabled = true;
    submitButton.textContent = "Sending code…";

    try {
      const result = await App.request("/donors/register", {
        method: "POST",
        body: {
          full_name: payload.full_name,
          account_type: payload.account_type,
          organization_name: payload.organization_name,
          email: payload.email,
          phone: payload.phone,
          location: payload.location,
          password: payload.password
        }
      });

      showCodeStep(result, payload.email);
    } catch (error) {
      App.showMessage(messageBox, error.message, "error");
    } finally {
      submitButton.disabled = false;
      submitButton.innerHTML = originalLabel;
    }
  }

  /* ---------------- step 2: enter the code --------------------- */

  async function submitCode(event) {
    if (event) {
      event.preventDefault();
    }

    if (submitting) {
      return;
    }

    if (!pendingEmail) {
      showStep("account");
      return;
    }

    if (!otp.isComplete()) {
      otp.setInvalid(true);
      App.showMessage(messageBox, "Enter all six digits of the code.", "error");
      otp.focus();
      return;
    }

    const originalLabel = verifyButton.innerHTML;

    submitting = true;
    verifyButton.disabled = true;
    verifyButton.textContent = "Checking code…";
    App.clearMessage(messageBox);

    try {
      const result = await App.request("/donors/register/verify", {
        method: "POST",
        body: { email: pendingEmail, code: otp.value() }
      });

      if (!result.access_token) {
        throw new App.ApiError("The API did not return an access token.", 500);
      }

      App.setDonorToken(result.access_token);

      App.showMessage(
        messageBox,
        result.donor && result.donor.verification_status === "Pending"
          ? "Email verified. An administrator still has to approve this organization, so its listings stay private for now. Opening your dashboard…"
          : "Email verified. Opening your dashboard…",
        "success"
      );

      window.setTimeout(() => {
        window.location.href = "donor-dashboard.html";
      }, 1200);
    } catch (error) {
      otp.setInvalid(true);
      App.showMessage(messageBox, error.message, "error");
      otp.focus();

      submitting = false;
      verifyButton.disabled = false;
      verifyButton.innerHTML = originalLabel;
    }
  }

  /* ---------------- resend and back ---------------------------- */

  async function resendCode() {
    if (!pendingEmail) {
      showStep("account");
      return;
    }

    resendButton.disabled = true;
    resendButton.textContent = "Sending...";

    try {
      const result = await App.request("/donors/register/resend", {
        method: "POST",
        body: { email: pendingEmail }
      });

      otp.clear();
      verifyButton.disabled = true;
      demoCodeValue.textContent = result.dev_code || "";
      demoCodeBox.hidden = !(result.delivery === "demo" && result.dev_code);

      startCooldown(result.resend_after_seconds || FALLBACK_COOLDOWN);
      App.showMessage(messageBox, result.message || "A new code is on its way.", "success");
      otp.focus();
    } catch (error) {
      App.showMessage(messageBox, error.message, "error");
      resendButton.disabled = false;
    } finally {
      resendButton.textContent = "Resend code";
    }
  }

  function backToAccountDetails() {
    window.clearInterval(cooldownTimer);
    otp.clear();
    verifyButton.disabled = true;
    pendingEmail = "";
    demoCodeBox.hidden = true;
    App.clearMessage(messageBox);
    showStep("account");
    document.getElementById("email").focus();
  }

  /* ---------------- wiring ------------------------------------- */

  form.addEventListener("submit", requestCode);
  verifyForm.addEventListener("submit", submitCode);
  resendButton.addEventListener("click", resendCode);
  changeEmailButton.addEventListener("click", backToAccountDetails);
  accountType.addEventListener("change", syncOrganizationField);

  syncOrganizationField();
  showStep("account");
})();
