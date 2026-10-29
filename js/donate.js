/* =====================================================
   FOODSHARE - DONATE FOOD PAGE
   File: js/donate.js
   Publishes a donation for the signed-in donor. The donor
   name and phone number are taken from the account by the
   API, so they are deliberately not sent from the form.
   ===================================================== */

"use strict";

(function () {
  const form = document.getElementById("donation-form");
  const messageBox = document.getElementById("donation-message");
  const submitButton = document.getElementById("donation-submit");
  const summary = document.getElementById("donor-summary");
  const deadlineInput = document.getElementById("pickup_deadline");
  const expiryInput = document.getElementById("expiry_date");

  if (!form) return;

  function applyInputBounds() {
    const now = new Date();
    const earliestPickup = new Date(now.getTime() + 15 * 60000);

    if (deadlineInput) {
      deadlineInput.min = App.toInputDateTime(earliestPickup);
    }

    if (expiryInput) {
      expiryInput.min = App.toInputDate(now);
    }
  }

  function showSignInPrompt() {
    form.hidden = true;

    const prompt = document.createElement("div");
    prompt.className = "empty-state";

    const icon = document.createElement("i");
    icon.className = "bi bi-person-lock";
    icon.setAttribute("aria-hidden", "true");

    const title = document.createElement("strong");
    title.textContent = "Sign in to list a donation";

    const hint = document.createElement("span");
    hint.textContent = "Every listing is tied to a donor account so the collection can be arranged.";

    const actions = document.createElement("div");
    actions.className = "form-actions";
    actions.style.justifyContent = "center";

    const signIn = document.createElement("a");
    signIn.className = "btn btn-small";
    signIn.href = "donor-login.html";
    signIn.textContent = "Sign in";

    const register = document.createElement("a");
    register.className = "btn btn-small btn-outline";
    register.href = "donor-register.html";
    register.textContent = "Create an account";

    actions.append(signIn, register);
    prompt.append(icon, title, hint, actions);

    form.parentElement.insertBefore(prompt, form);

    if (summary) {
      summary.replaceChildren(
        App.emptyState("No donor account signed in", "Sign in to see your account details here.")
      );
    }
  }

  function renderSummary(donor) {
    if (!summary) return;

    summary.replaceChildren();

    const name = document.createElement("p");
    name.style.fontWeight = "800";
    name.style.fontSize = "1.05rem";
    name.textContent = donor.organization_name || donor.full_name || "Donor";

    const type = document.createElement("p");
    type.className = "cell-sub";
    type.textContent = donor.account_type === "Organization"
      ? "Organization account"
      : "Individual account";

    const facts = document.createElement("dl");
    facts.className = "detail-list";
    facts.style.marginTop = "14px";

    [
      ["Contact", donor.phone || "Not provided"],
      ["Based in", donor.location || "Not provided"],
      ["Email", donor.email || "Not provided"]
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");

      term.textContent = label;
      detail.textContent = value;
      row.append(term, detail);
      facts.appendChild(row);
    });

    const statusRow = document.createElement("p");
    statusRow.style.marginTop = "14px";
    statusRow.append(App.statusBadge(donor.verification_status || "Pending"));

    const dashboard = document.createElement("a");
    dashboard.className = "btn btn-secondary btn-small";
    dashboard.style.marginTop = "16px";
    dashboard.href = "donor-dashboard.html";
    dashboard.textContent = "Open my dashboard";

    summary.append(name, type, facts, statusRow, dashboard);
  }

  async function loadDonor() {
    try {
      const data = await App.request("/donors/me", { auth: "donor" });
      renderSummary(data.donor || data);
    } catch (error) {
      if (error.isAuthError) {
        App.clearDonorToken();
        showSignInPrompt();
        return;
      }

      if (summary) {
        summary.replaceChildren(App.emptyState("Account unavailable", error.message));
      }
    }
  }

  function readPayload() {
    const data = new FormData(form);
    const deadline = data.get("pickup_deadline");

    return {
      food_name: String(data.get("food_name") || "").trim(),
      item_type: String(data.get("item_type") || "Food"),
      quantity: Number(data.get("quantity")),
      quantity_unit: String(data.get("quantity_unit") || "kg"),
      category: String(data.get("category") || "").trim(),
      location: String(data.get("location") || "").trim(),
      pickup_deadline: deadline ? new Date(deadline).toISOString() : "",
      expiry_date: String(data.get("expiry_date") || "").trim() || null,
      packaging_condition: String(data.get("packaging_condition") || "").trim() || null
    };
  }

  function validate(payload) {
    if (!payload.food_name || payload.food_name.length < 2) {
      return "Enter the food name (at least 2 characters).";
    }

    if (!payload.category) {
      return "Choose a category for this donation.";
    }

    if (!payload.location || payload.location.length < 2) {
      return "Enter the pickup location.";
    }

    if (!payload.pickup_deadline || Number.isNaN(new Date(payload.pickup_deadline).getTime())) {
      return "Choose the date and time the food can be collected until.";
    }

    if (new Date(payload.pickup_deadline).getTime() <= Date.now()) {
      return "The collection deadline has to be in the future.";
    }

    if (!Number.isFinite(payload.quantity) || payload.quantity <= 0) {
      return "Enter a quantity greater than zero.";
    }

    if (payload.expiry_date) {
      const expiry = new Date(`${payload.expiry_date}T23:59:59`);

      if (expiry.getTime() < new Date().setHours(0, 0, 0, 0)) {
        return "The expiry date cannot be in the past.";
      }
    }

    return null;
  }

  async function submit(event) {
    event.preventDefault();

    if (!form.reportValidity()) return;

    const payload = readPayload();
    const problem = validate(payload);

    App.clearMessage(messageBox);

    if (problem) {
      App.showMessage(messageBox, problem, "error");
      return;
    }

    const originalLabel = submitButton ? submitButton.innerHTML : "";

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Publishing…";
    }

    try {
      const result = await App.request("/donations", {
        method: "POST",
        auth: "donor",
        body: payload
      });

      App.showMessage(
        messageBox,
        `Donation #${result.donation_id} is live and now appears in Find Food.`,
        "success"
      );

      form.reset();
      applyInputBounds();
    } catch (error) {
      if (error.isAuthError) {
        App.clearDonorToken();
        App.showMessage(messageBox, "Your session ended. Sign in again to publish this donation.", "error");
        showSignInPrompt();
      } else {
        App.showMessage(messageBox, error.message, "error");
      }
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = originalLabel;
      }
    }
  }

  applyInputBounds();

  if (!App.getDonorToken()) {
    showSignInPrompt();
    return;
  }

  form.addEventListener("submit", submit);
  loadDonor();
})();
