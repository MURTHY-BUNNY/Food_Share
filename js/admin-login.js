/* =====================================================
   FOODSHARE - ADMIN LOGIN
   File: js/admin-login.js
   ===================================================== */

"use strict";

(function () {
  const form = document.getElementById("admin-login-form");
  const messageBox = document.getElementById("admin-login-message");
  const submitButton = document.getElementById("admin-login-button");

  if (!form) return;

  if (App.getAdminToken()) {
    window.location.replace("admin.html");
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!form.reportValidity()) return;

    const username = document.getElementById("admin-username").value.trim();
    const password = document.getElementById("admin-password").value;

    App.clearMessage(messageBox);

    if (!username || !password) {
      App.showMessage(messageBox, "Enter the administrator username and password.", "error");
      return;
    }

    const originalLabel = submitButton ? submitButton.innerHTML : "";

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Signing in…";
    }

    try {
      const result = await App.request("/admin/login", {
        method: "POST",
        body: { username, password }
      });

      if (!result.access_token) {
        throw new App.ApiError("The API did not return an access token.", 500);
      }

      App.setAdminToken(result.access_token);
      App.showMessage(messageBox, "Signed in. Opening the admin console…", "success");

      window.location.href = "admin.html";
    } catch (error) {
      App.showMessage(
        messageBox,
        error.status === 401
          ? "That username and password combination was not accepted."
          : error.message,
        "error"
      );
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = originalLabel;
      }
    }
  });
})();
