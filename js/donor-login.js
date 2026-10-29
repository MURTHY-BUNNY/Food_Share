/* =====================================================
   FOODSHARE - DONOR LOGIN
   File: js/donor-login.js
   ===================================================== */

"use strict";

(function () {
  const form = document.getElementById("donor-login-form");
  const messageBox = document.getElementById("login-message");
  const submitButton = document.getElementById("login-button");

  if (!form) return;

  /* An existing token is validated by the dashboard; bouncing through it
     keeps a stale token from trapping the user on a blank page. */
  if (App.getDonorToken()) {
    window.location.replace("donor-dashboard.html");
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!form.reportValidity()) return;

    const email = document.getElementById("email").value.trim().toLowerCase();
    const password = document.getElementById("password").value;

    App.clearMessage(messageBox);

    if (!email || !password) {
      App.showMessage(messageBox, "Enter your email address and password.", "error");
      return;
    }

    const credentials = new URLSearchParams();
    credentials.set("username", email);
    credentials.set("password", password);

    const originalLabel = submitButton ? submitButton.innerHTML : "";

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Signing in…";
    }

    try {
      const result = await App.request("/donors/login", {
        method: "POST",
        body: credentials
      });

      if (!result.access_token) {
        throw new App.ApiError("The API did not return an access token.", 500);
      }

      App.setDonorToken(result.access_token);
      App.showMessage(messageBox, "Signed in. Opening your dashboard…", "success");

      window.location.href = "donor-dashboard.html";
    } catch (error) {
      const text = error.isNetworkError
        ? error.message
        : error.status === 422
          ? "Enter a valid email address and your password."
          : error.message;

      App.showMessage(messageBox, text, "error");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = originalLabel;
      }
    }
  });
})();
