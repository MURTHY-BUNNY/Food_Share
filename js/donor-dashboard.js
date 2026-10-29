/* =====================================================
   FOODSHARE - DONOR DASHBOARD
   File: js/donor-dashboard.js
   Loads the signed-in donor, their totals and their
   donations, and drives publish / edit / complete / cancel.
   ===================================================== */

"use strict";

(function () {
  const form = document.getElementById("donation-form");
  const formHeading = document.getElementById("donation-form-heading");
  const formHint = document.getElementById("form-mode-hint");
  const submitButton = document.getElementById("submit-donation-button");
  const resetButton = document.getElementById("reset-donation-form");
  const cancelEditButton = document.getElementById("cancel-edit-button");
  const donationMessage = document.getElementById("donation-message");
  const donationsMessage = document.getElementById("donations-message");
  const dashboardMessage = document.getElementById("dashboard-message");
  const tableBody = document.getElementById("my-donations");
  const countPill = document.getElementById("donation-count");
  const logoutButton = document.getElementById("logout-button");
  const pickupInput = document.getElementById("pickup-deadline");
  const expiryInput = document.getElementById("expiry-date");

  if (!form || !tableBody) return;

  let donations = [];
  let editingId = null;
  let donor = null;

  const FIELDS = {
    foodName: document.getElementById("food-name"),
    itemType: document.getElementById("item-type"),
    quantity: document.getElementById("quantity"),
    quantityUnit: document.getElementById("quantity-unit"),
    category: document.getElementById("category"),
    location: document.getElementById("location"),
    packaging: document.getElementById("packaging-condition")
  };

  /* ---------------- helpers ------------------------------------ */

  function logout() {
    App.clearDonorToken();
    window.location.href = "donor-login.html";
  }

  function applyInputBounds() {
    const now = new Date();

    if (pickupInput) {
      pickupInput.min = App.toInputDateTime(new Date(now.getTime() + 15 * 60000));
    }

    if (expiryInput) {
      expiryInput.min = App.toInputDate(now);
    }
  }

  function setStat(id, value) {
    const element = document.getElementById(id);

    if (element) {
      element.textContent = String(value);
    }
  }

  function makeButton(label, modifier, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn btn-small ${modifier}`.trim();
    button.textContent = label;
    button.addEventListener("click", onClick);

    return button;
  }

  /* ---------------- profile and totals ------------------------- */

  function renderProfile(data) {
    donor = data;

    const name = document.getElementById("donor-name");
    const type = document.getElementById("donor-type");
    const verification = document.getElementById("verification-status");

    if (name) {
      name.textContent = donor.organization_name || donor.full_name || "Donor";
    }

    if (type) {
      type.replaceChildren(App.statusBadge(donor.account_type || "Individual"));
    }

    if (verification) {
      verification.replaceChildren(
        App.statusBadge(donor.verification_status || "Pending")
      );
    }
  }

  async function loadProfile() {
    const data = await App.request("/donors/me", { auth: "donor" });
    renderProfile(data.donor || data);
  }

  async function loadStats() {
    const data = await App.request("/donors/me/dashboard", { auth: "donor" });
    const summary = data.summary || data.statistics || {};

    setStat("total-donations", summary.total_donations || 0);
    setStat("available-donations", summary.available_donations || 0);
    setStat("completed-donations", summary.completed_donations || 0);
    setStat(
      "closed-donations",
      (summary.cancelled_donations || 0) + (summary.expired_donations || 0)
    );
  }

  /* ---------------- donation table ----------------------------- */

  function renderDonations() {
    tableBody.replaceChildren();

    if (countPill) {
      countPill.textContent = String(donations.length);
    }

    if (!donations.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.appendChild(
        App.emptyState(
          "No donations yet",
          "Publish your first donation with the form on the left."
        )
      );
      row.appendChild(cell);
      tableBody.appendChild(row);
      return;
    }

    donations.forEach((donation) => {
      const row = document.createElement("tr");

      const foodCell = document.createElement("td");
      const title = document.createElement("span");
      title.className = "cell-title";
      title.textContent = donation.food_name || "Food donation";

      const locationLine = document.createElement("span");
      locationLine.className = "cell-sub";
      locationLine.textContent = donation.location || "No location";

      foodCell.append(title, locationLine);

      const quantityCell = document.createElement("td");
      quantityCell.className = "cell-nowrap";
      quantityCell.textContent = App.formatQuantity(
        donation.quantity,
        donation.quantity_unit
      );

      const deadlineCell = document.createElement("td");
      deadlineCell.className = "cell-nowrap";
      deadlineCell.textContent = App.formatDate(donation.pickup_deadline);

      const statusCell = document.createElement("td");
      statusCell.appendChild(App.statusBadge(donation.status));

      const actionsCell = document.createElement("td");
      actionsCell.className = "cell-actions";

      const actions = document.createElement("div");
      actions.className = "table-actions";

      const status = String(donation.status || "").toLowerCase();

      if (status === "available") {
        actions.appendChild(
          makeButton("Edit", "btn-outline", () => startEdit(donation))
        );

        actions.appendChild(
          makeButton("Complete", "", () => handleAction("complete", donation))
        );

        actions.appendChild(
          makeButton("Cancel", "btn-secondary", () => handleAction("cancel", donation))
        );
      } else if (status === "reserved") {
        actions.appendChild(
          makeButton("Complete", "", () => handleAction("complete", donation))
        );
      } else {
        const none = document.createElement("span");
        none.className = "cell-sub";
        none.textContent = "No actions";
        actions.appendChild(none);
      }

      actionsCell.appendChild(actions);
      row.append(foodCell, quantityCell, deadlineCell, statusCell, actionsCell);
      tableBody.appendChild(row);
    });
  }

  async function loadDonations() {
    try {
      const data = await App.request("/donors/me/donations", { auth: "donor" });
      donations = App.pickList(data, "donations");
      renderDonations();
    } catch (error) {
      if (error.isAuthError) {
        logout();
        return;
      }

      tableBody.replaceChildren();

      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.appendChild(App.emptyState("Could not load your donations", error.message));
      row.appendChild(cell);
      tableBody.appendChild(row);

      if (countPill) countPill.textContent = "0";
    }
  }

  /* ---------------- form --------------------------------------- */

  function setFormMode(isEditing) {
    if (formHeading) {
      formHeading.textContent = isEditing ? "Edit donation" : "Publish a donation";
    }

    if (formHint) {
      formHint.textContent = isEditing
        ? "Saving replaces the listing details. The status stays available."
        : "Your account name and phone number are added to the listing automatically.";
    }

    if (submitButton) {
      submitButton.textContent = isEditing ? "Save changes" : "Publish donation";
    }

    if (cancelEditButton) {
      cancelEditButton.hidden = !isEditing;
    }
  }

  function resetForm() {
    editingId = null;
    form.reset();
    applyInputBounds();
    setFormMode(false);
    App.clearMessage(donationMessage);
  }

  function startEdit(donation) {
    editingId = donation.id;
    setFormMode(true);

    if (FIELDS.foodName) FIELDS.foodName.value = donation.food_name || "";
    if (FIELDS.itemType) FIELDS.itemType.value = donation.item_type || "Food";
    if (FIELDS.quantity) FIELDS.quantity.value = donation.quantity ?? "";
    if (FIELDS.quantityUnit) FIELDS.quantityUnit.value = donation.quantity_unit || "kg";
    if (FIELDS.category) FIELDS.category.value = donation.category || "Other";
    if (FIELDS.location) FIELDS.location.value = donation.location || "";
    if (FIELDS.packaging) FIELDS.packaging.value = donation.packaging_condition || "";

    if (pickupInput) pickupInput.value = App.toInputDateTime(donation.pickup_deadline);
    if (expiryInput) {
      expiryInput.value = donation.expiry_date
        ? App.toInputDate(donation.expiry_date)
        : "";
    }

    App.clearMessage(donationMessage);
    App.showMessage(
      donationMessage,
      `Editing "${donation.food_name}". Change the details and save.`,
      "info"
    );

    form.scrollIntoView({ behavior: "smooth", block: "start" });
    FIELDS.foodName?.focus({ preventScroll: true });
  }

  function readPayload() {
    const data = new FormData(form);

    return {
      food_name: String(data.get("food_name") || "").trim(),
      item_type: String(data.get("item_type") || "Food"),
      quantity: Number(data.get("quantity")),
      quantity_unit: String(data.get("quantity_unit") || "kg"),
      category: String(data.get("category") || "").trim(),
      location: String(data.get("location") || "").trim(),
      pickup_deadline: data.get("pickup_deadline")
        ? new Date(String(data.get("pickup_deadline"))).toISOString()
        : "",
      expiry_date: String(data.get("expiry_date") || "").trim() || null,
      packaging_condition: String(data.get("packaging_condition") || "").trim() || null
    };
  }

  function validate(payload) {
    if (payload.food_name.length < 2) return "Enter the food name.";
    if (!payload.category) return "Choose a category.";
    if (payload.location.length < 2) return "Enter the pickup location.";
    if (!payload.pickup_deadline) return "Choose when the food can be collected until.";
    if (new Date(payload.pickup_deadline).getTime() <= Date.now()) {
      return "The collection deadline has to be in the future.";
    }
    if (!Number.isFinite(payload.quantity) || payload.quantity <= 0) {
      return "Enter a quantity greater than zero.";
    }

    return null;
  }

  async function submit(event) {
    event.preventDefault();

    if (!form.reportValidity()) return;

    const payload = readPayload();
    const problem = validate(payload);

    App.clearMessage(donationMessage);

    if (problem) {
      App.showMessage(donationMessage, problem, "error");
      return;
    }

    const isEditing = editingId !== null;
    const originalLabel = submitButton ? submitButton.textContent : "";

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = isEditing ? "Saving…" : "Publishing…";
    }

    try {
      const result = isEditing
        ? await App.request(`/donors/me/donations/${encodeURIComponent(editingId)}`, {
            method: "PUT",
            auth: "donor",
            body: payload
          })
        : await App.request("/donations", {
            method: "POST",
            auth: "donor",
            body: payload
          });

      resetForm();
      App.showMessage(
        donationMessage,
        result.message || (isEditing ? "Donation updated." : "Donation published."),
        "success"
      );

      await Promise.all([loadDonations(), loadStats()]);
    } catch (error) {
      if (error.isAuthError) {
        logout();
        return;
      }

      App.showMessage(donationMessage, error.message, "error");
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = isEditing ? "Save changes" : originalLabel || "Publish donation";
      }
    }
  }

  /* ---------------- complete / cancel -------------------------- */

  async function handleAction(action, donation) {
    const config = {
      complete: {
        path: `/donors/me/donations/${encodeURIComponent(donation.id)}/complete`,
        question: `Mark "${donation.food_name}" as completed? It will stop appearing in listings.`,
        fallback: "Donation marked as completed."
      },
      cancel: {
        path: `/donors/me/donations/${encodeURIComponent(donation.id)}/cancel`,
        question: `Cancel "${donation.food_name}"? This cannot be undone.`,
        fallback: "Donation cancelled."
      }
    }[action];

    if (!config) return;

    if (!window.confirm(config.question)) return;

    App.clearMessage(donationsMessage);

    try {
      const result = await App.request(config.path, {
        method: "PATCH",
        auth: "donor"
      });

      App.showMessage(donationsMessage, result.message || config.fallback, "success");

      if (editingId !== null && String(editingId) === String(donation.id)) {
        resetForm();
      }

      await Promise.all([loadDonations(), loadStats()]);
    } catch (error) {
      if (error.isAuthError) {
        logout();
        return;
      }

      App.showMessage(donationsMessage, error.message, "error");
    }
  }

  /* ---------------- refresh on return -------------------------- */

  let lastRefreshAt = 0;

  /* A donation published in another tab (or from the Donate page) should be
     visible as soon as the donor comes back to this tab, without a reload. */
  async function refreshIfStale() {
    if (document.visibilityState === "hidden") return;

    const now = Date.now();

    if (now - lastRefreshAt < 4000) return;

    lastRefreshAt = now;

    if (editingId !== null) return;

    await Promise.allSettled([loadStats(), loadDonations()]);
  }

  /* ---------------- boot --------------------------------------- */

  async function init() {
    if (!App.getDonorToken()) {
      window.location.replace("donor-login.html");
      return;
    }

    logoutButton?.addEventListener("click", logout);
    form.addEventListener("submit", submit);
    resetButton?.addEventListener("click", resetForm);
    cancelEditButton?.addEventListener("click", resetForm);

    document.addEventListener("visibilitychange", refreshIfStale);
    window.addEventListener("focus", refreshIfStale);

    applyInputBounds();
    setFormMode(false);

    try {
      await loadProfile();
    } catch (error) {
      if (error.isAuthError) {
        logout();
        return;
      }

      App.showMessage(dashboardMessage, error.message, "error");
    }

    await Promise.all([loadStats(), loadDonations()]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
