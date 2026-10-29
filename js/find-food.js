/* =====================================================
   FOODSHARE - FIND FOOD PAGE
   File: js/find-food.js
   Filters the public donation list and submits food
   requests for people who cannot find a match.
   ===================================================== */

"use strict";

(function () {
  const filterForm = document.getElementById("filter-form");
  const searchInput = document.getElementById("search");
  const categoryInput = document.getElementById("category");
  const locationInput = document.getElementById("location");
  const resetButton = document.getElementById("reset-filters");
  const list = document.getElementById("food-list");
  const countLabel = document.getElementById("listing-count");
  const messageBox = document.getElementById("listing-message");

  const requestForm = document.getElementById("request-form");
  const requestMessage = document.getElementById("request-message");
  const requestSubmit = document.getElementById("request-submit");

  if (!list) return;

  let requestCounter = 0;

  function setCount(total, filtered) {
    if (!countLabel) return;

    if (!filtered) {
      countLabel.textContent = total === 1
        ? "1 donation available"
        : `${total} donations available`;
      return;
    }

    countLabel.textContent = `${total} donation${total === 1 ? "" : "s"} match your filters`;
  }

  function render(donations, filtered) {
    list.replaceChildren();

    setCount(donations.length, filtered);

    if (!donations.length) {
      list.appendChild(App.emptyState(
        filtered ? "No donations match these filters" : "No donations are listed right now",
        filtered
          ? "Try another category or clear the filters."
          : "New donations appear here as soon as a donor publishes them."
      ));
      return;
    }

    donations.forEach((donation) => {
      list.appendChild(createFoodCard(donation));
    });
  }

  async function load() {
    const ticket = ++requestCounter;

    const params = new URLSearchParams();

    const search = (searchInput && searchInput.value || "").trim();
    const category = categoryInput ? categoryInput.value : "";
    const area = (locationInput && locationInput.value || "").trim();

    if (search) params.set("search", search);
    if (category) params.set("category", category);
    if (area) params.set("location", area);

    const query = params.toString();

    list.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "loading";
    loading.textContent = "Loading available food";
    list.appendChild(loading);

    try {
      const data = await App.request(`/donations${query ? `?${query}` : ""}`);

      if (ticket !== requestCounter) return;

      App.clearMessage(messageBox);
      render(App.pickList(data, "donations"), Boolean(query));
    } catch (error) {
      if (ticket !== requestCounter) return;

      list.replaceChildren(App.emptyState("Could not load donations", error.message));
      setCount(0, false);
      App.showMessage(messageBox, error.message, "error");
    }
  }

  const reload = App.debounce(load, 300);

  filterForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });

  searchInput?.addEventListener("input", reload);
  locationInput?.addEventListener("input", reload);
  categoryInput?.addEventListener("change", load);

  resetButton?.addEventListener("click", () => {
    if (searchInput) searchInput.value = "";
    if (locationInput) locationInput.value = "";
    if (categoryInput) categoryInput.value = "";
    load();
  });

  /* ---------------- food requests ---------------------------- */

  requestForm?.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!requestForm.reportValidity()) return;

    App.clearMessage(requestMessage);

    const data = new FormData(requestForm);
    const payload = {
      requester_name: String(data.get("requester_name") || "").trim(),
      contact: String(data.get("contact") || "").trim(),
      location: String(data.get("location") || "").trim(),
      food_category: String(data.get("food_category") || "").trim(),
      quantity_needed: Number(data.get("quantity_needed"))
    };

    if (payload.requester_name.length < 2) {
      App.showMessage(requestMessage, "Enter your name.", "error");
      return;
    }

    if (payload.contact.length < 7) {
      App.showMessage(requestMessage, "Enter a phone number with at least 7 digits.", "error");
      return;
    }

    if (!Number.isFinite(payload.quantity_needed) || payload.quantity_needed <= 0) {
      App.showMessage(requestMessage, "Enter how much food you need.", "error");
      return;
    }

    const originalLabel = requestSubmit ? requestSubmit.innerHTML : "";

    if (requestSubmit) {
      requestSubmit.disabled = true;
      requestSubmit.textContent = "Submitting…";
    }

    try {
      const result = await App.request("/food-requests", {
        method: "POST",
        body: payload
      });

      App.showMessage(
        requestMessage,
        `Request #${result.request_id} submitted. The FoodShare team will review it and contact you.`,
        "success"
      );

      requestForm.reset();
    } catch (error) {
      App.showMessage(requestMessage, error.message, "error");
    } finally {
      if (requestSubmit) {
        requestSubmit.disabled = false;
        requestSubmit.innerHTML = originalLabel;
      }
    }
  });

  load();
})();
