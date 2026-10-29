/* =====================================================
   FOODSHARE - LANDING PAGE
   File: js/home.js
   Loads the newest available donations and the live count
   shown on the hero photo.
   ===================================================== */

"use strict";

(function () {
  const listings = document.getElementById("home-listings");
  const countLabel = document.getElementById("hero-available-count");
  const hintLabel = document.getElementById("hero-available-hint");

  if (!listings) return;

  function setHeroCount(total) {
    if (!countLabel) return;

    if (total === 0) {
      countLabel.textContent = "No donations listed right now";
      if (hintLabel) hintLabel.textContent = "Check back, or list your own surplus food";
      return;
    }

    countLabel.textContent = total === 1
      ? "1 donation available"
      : `${total} donations available`;

    if (hintLabel) hintLabel.textContent = "Open for collection right now";
  }

  function render(donations) {
    listings.replaceChildren();

    if (!donations.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.style.gridColumn = "1 / -1";

      const icon = document.createElement("i");
      icon.className = "bi bi-basket";
      icon.setAttribute("aria-hidden", "true");

      const title = document.createElement("strong");
      title.textContent = "No donations are listed right now";

      const hint = document.createElement("span");
      hint.textContent = "Have surplus food? List it and it will appear here.";

      const action = document.createElement("a");
      action.className = "btn btn-small";
      action.href = "donate.html";
      action.textContent = "List a donation";
      action.style.marginTop = "10px";

      empty.append(icon, title, hint, action);
      listings.appendChild(empty);
      return;
    }

    donations.slice(0, 3).forEach((donation) => {
      listings.appendChild(createFoodCard(donation));
    });
  }

  async function load() {
    try {
      const data = await App.request("/donations");
      const donations = App.pickList(data, "donations");

      setHeroCount(donations.length);
      render(donations);
    } catch (error) {
      listings.replaceChildren();

      const failure = App.emptyState(
        "Could not load donations",
        error.message
      );

      failure.style.gridColumn = "1 / -1";
      listings.appendChild(failure);

      if (countLabel) {
        countLabel.textContent = "Listing count unavailable";
      }
      if (hintLabel) {
        hintLabel.textContent = "The FoodShare API is not responding";
      }
    }
  }

  load();
})();
