/**
 * Donor password reset, in three steps.
 *
 * 1. address  - asks the API to email a 6-digit code
 * 2. code     - exchanges the code for a short-lived reset token
 * 3. password - sets the new password with that token
 *
 * Keeping the code and the password apart means the code is never resent with
 * a new password, and the reset window can expire on its own.
 */
(function () {
  "use strict";

  const messageBox = document.getElementById("reset-message");
  const stepMarkers = document.querySelectorAll("[data-step-index]");

  const emailForm = document.getElementById("reset-email-form");
  const emailInput = document.getElementById("reset-email-input");
  const sendButton = document.getElementById("send-reset-code");

  const codeForm = document.getElementById("reset-code-form");
  const codeEmail = document.getElementById("reset-code-email");
  const codeExpiry = document.getElementById("reset-code-expiry");
  const demoCodeBox = document.getElementById("reset-demo-code");
  const demoCodeValue = document.getElementById("reset-demo-value");
  const resendButton = document.getElementById("reset-resend-code");
  const resendTimer = document.getElementById("reset-resend-timer");
  const changeEmailButton = document.getElementById("reset-change-email");
  const verifyButton = document.getElementById("reset-verify-button");

  const passwordForm = document.getElementById("reset-password-form");
  const newPasswordInput = document.getElementById("reset-new-password");
  const confirmPasswordInput = document.getElementById("reset-confirm-password");
  const saveButton = document.getElementById("reset-save-button");

  if (!emailForm || !codeForm || !passwordForm) {
    return;
  }

  const FALLBACK_COOLDOWN = 60;

  let email = "";
  let resetToken = "";
  let cooldownTimer = null;
  let busy = false;

  const otp = OtpInput.mount(document.getElementById("reset-code-input"), {
    length: 6,
    id: "reset-code",
    describedBy: "reset-code-help",
    onComplete: () => {
      verifyButton.disabled = false;

      if (!busy) {
        submitCode();
      }
    }
  });

  /* ---------------- steps --------------------------------------- */

  function showStep(position) {
    emailForm.hidden = position !== 1;
    codeForm.hidden = position !== 2;
    passwordForm.hidden = position !== 3;

    stepMarkers.forEach((marker) => {
      const index = Number(marker.dataset.stepIndex);

      marker.classList.toggle("is-current", index === position);
      marker.classList.toggle("is-done", index < position);
    });

    if (position === 2) {
      otp.focus();
    }

    if (position === 3) {
      newPasswordInput.focus();
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
        resendTimer.textContent = "Did not get the code?";
        return;
      }

      resendTimer.textContent = `You can ask for a new code in ${remaining}s.`;
      remaining -= 1;
    };

    paint();
    cooldownTimer = window.setInterval(paint, 1000);
  }

  /* ---------------- step 1: request a code ---------------------- */

  function showCodeStep(result, address) {
    email = address;

    codeEmail.textContent = address;
    codeExpiry.textContent = result.expires_in_minutes || 15;

    if (result.delivery === "demo" && result.dev_code) {
      demoCodeValue.textContent = result.dev_code;
      demoCodeBox.hidden = false;
    } else {
      demoCodeBox.hidden = true;
    }

    otp.clear();
    verifyButton.disabled = true;

    showStep(2);
    startCooldown(result.resend_after_seconds || FALLBACK_COOLDOWN);

    App.showMessage(
      messageBox,
      result.message ||
        `If an account exists for ${address}, a 6-digit code is on its way.`,
      "success"
    );
  }

  async function requestCode(event) {
    if (event) {
      event.preventDefault();
    }

    if (!emailForm.reportValidity()) {
      return;
    }

    const address = emailInput.value.trim().toLowerCase();
    const originalLabel = sendButton.innerHTML;

    App.clearMessage(messageBox);
    sendButton.disabled = true;
    sendButton.textContent = "Sending code…";

    try {
      const result = await App.request("/donors/password/forgot", {
        method: "POST",
        body: { email: address }
      });

      showCodeStep(result, address);
    } catch (error) {
      App.showMessage(messageBox, error.message, "error");
    } finally {
      sendButton.disabled = false;
      sendButton.innerHTML = originalLabel;
    }
  }

  /* ---------------- step 2: verify the code --------------------- */

  async function submitCode(event) {
    if (event) {
      event.preventDefault();
    }

    if (busy) {
      return;
    }

    if (!email) {
      showStep(1);
      return;
    }

    if (!otp.isComplete()) {
      otp.setInvalid(true);
      App.showMessage(messageBox, "Enter all six digits of the code.", "error");
      otp.focus();
      return;
    }

    const originalLabel = verifyButton.innerHTML;

    busy = true;
    verifyButton.disabled = true;
    verifyButton.textContent = "Checking code…";
    App.clearMessage(messageBox);

    try {
      const result = await App.request("/donors/password/verify-code", {
        method: "POST",
        body: { email: email, code: otp.value() }
      });

      resetToken = result.reset_token || "";

      if (!resetToken) {
        throw new App.ApiError("The API did not return a reset token.", 500);
      }

      showStep(3);
      App.showMessage(messageBox, "Code accepted. Choose a new password.", "success");
    } catch (error) {
      otp.setInvalid(true);
      otp.focus();
      App.showMessage(messageBox, error.message, "error");
    } finally {
      busy = false;
      verifyButton.disabled = false;
      verifyButton.innerHTML = originalLabel;
    }
  }

  /* ---------------- step 3: save the password ------------------- */

  async function savePassword(event) {
    event.preventDefault();

    if (!passwordForm.reportValidity()) {
      return;
    }

    App.clearMessage(messageBox);

    const password = newPasswordInput.value;

    if (password !== confirmPasswordInput.value) {
      App.showMessage(messageBox, "The two passwords do not match.", "error");
      confirmPasswordInput.focus();
      return;
    }

    if (!resetToken) {
      otp.clear();
      showStep(1);
      App.showMessage(
        messageBox,
        "The reset window closed. Start again to get a new code.",
        "error"
      );
      return;
    }

    const originalLabel = saveButton.innerHTML;

    saveButton.disabled = true;
    saveButton.textContent = "Saving…";

    try {
      const result = await App.request("/donors/password/reset", {
        method: "POST",
        body: { reset_token: resetToken, new_password: password }
      });

      passwordForm.hidden = true;

      stepMarkers.forEach((marker) => {
        marker.classList.remove("is-current");
        marker.classList.add("is-done");
      });

      App.showMessage(
        messageBox,
        result.message || "Password updated. Taking you to sign in…",
        "success"
      );

      window.setTimeout(() => {
        window.location.href = "donor-login.html";
      }, 2200);
    } catch (error) {
      App.showMessage(messageBox, error.message, "error");

      if (error.status === 401) {
        /* The reset window expired while the form was open. */
        resetToken = "";
        otp.clear();
        showStep(1);
      }

      saveButton.disabled = false;
      saveButton.innerHTML = originalLabel;
    }
  }

  /* ---------------- resend and back ---------------------------- */

  async function resendCode() {
    if (!email) {
      showStep(1);
      return;
    }

    resendButton.disabled = true;
    resendButton.textContent = "Sending...";

    try {
      const result = await App.request("/donors/password/forgot", {
        method: "POST",
        body: { email: email }
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

  function backToEmail() {
    window.clearInterval(cooldownTimer);
    otp.clear();
    verifyButton.disabled = true;
    demoCodeBox.hidden = true;
    email = "";
    resetToken = "";
    App.clearMessage(messageBox);
    showStep(1);
    emailInput.focus();
  }

  /* ---------------- wiring ------------------------------------- */

  emailForm.addEventListener("submit", requestCode);
  codeForm.addEventListener("submit", submitCode);
  passwordForm.addEventListener("submit", savePassword);
  resendButton.addEventListener("click", resendCode);
  changeEmailButton.addEventListener("click", backToEmail);

  showStep(1);
})();
