/* =====================================================
   FOODSHARE - DONATION DETAIL PAGE
   File: js/track.js
   Shows one donation, including the donor contact number
   a collector needs in order to arrange pickup.
   ===================================================== */

"use strict";

(function () {
  const body = document.getElementById("tracking-body");
  const messageBox = document.getElementById("tracking-message");
  const details = document.getElementById("donation-details");

  if (!body || !details) return;

  function setText(id, value) {
    const element = document.getElementById(id);

    if (!element) return;

    element.textContent = value === null || value === undefined || value === ""
      ? "Not provided"
      : String(value);
  }

  function showMissing(title, hint) {
    details.hidden = true;

    const state = App.emptyState(title, hint);
    const action = document.createElement("a");

    action.className = "btn btn-small";
    action.href = "find-food.html";
    action.textContent = "Browse available food";
    action.style.marginTop = "12px";

    state.appendChild(action);
    body.appendChild(state);
  }

  function render(donation) {
    setText("detail-name", donation.food_name);
    setText("detail-id", `#${donation.id}`);
    setText("detail-category", donation.category);
    setText("detail-type", donation.item_type);
    setText("detail-quantity", App.formatQuantity(donation.quantity, donation.quantity_unit));
    setText("detail-location", donation.location);
    setText("detail-deadline", App.formatDate(donation.pickup_deadline));
    setText(
      "detail-expiry",
      donation.expiry_date ? App.formatDate(donation.expiry_date, { dateOnly: true }) : null
    );
    setText("detail-packaging", donation.packaging_condition || "Not specified");
    setText("detail-donor", donation.donor_name);

    const statusHolder = document.getElementById("detail-status");

    if (statusHolder) {
      statusHolder.replaceChildren(App.statusBadge(donation.status));
    }

    const notice = document.getElementById("detail-notice");

    if (notice && String(donation.status).toLowerCase() !== "available") {
      notice.textContent = `This donation is currently marked ${donation.status}, so it may no longer be available for collection.`;
      notice.hidden = false;
    } else if (notice) {
      notice.hidden = true;
    }

    const contact = document.getElementById("detail-contact");
    const call = document.getElementById("call-donor");
    const phone = (donation.contact || "").replace(/[^\d+]/g, "");

    if (contact) {
      contact.textContent = donation.contact || "Not provided";
    }

    if (call) {
      if (phone) {
        call.href = `tel:${phone}`;
      } else {
        call.removeAttribute("href");
        call.setAttribute("aria-disabled", "true");
        call.classList.add("btn-secondary");
        call.classList.remove("btn");
        call.textContent = "No number listed";
      }
    }

    details.hidden = false;
  }

  async function load() {
    const id = new URLSearchParams(window.location.search).get("id");

    if (!id || !/^\d+$/.test(id)) {
      showMissing(
        "No donation selected",
        "Open this page from a listing to see its details."
      );
      return;
    }

    try {
      const data = await App.request(`/donations/${encodeURIComponent(id)}`);
      render(data.donation || data);
    } catch (error) {
      if (error.status === 404) {
        showMissing("That donation no longer exists", "It may have been removed by the donor.");
        return;
      }

      showMissing("Could not load this donation", error.message);
      App.showMessage(messageBox, error.message, "error");
    }
  }

  load();
})();
