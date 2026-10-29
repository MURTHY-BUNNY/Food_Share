/* =====================================================
   FOODSHARE - DONATION CARD BUILDER
   File: js/food-card.js
   Renders one donation as a card. Shared by the landing
   page and the Find Food listing so both stay identical.
   ===================================================== */

"use strict";

function createFoodCard(donation) {
  const card = document.createElement("article");
  card.className = "food-card";

  const head = document.createElement("div");
  head.className = "food-card-head";

  const heading = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = donation.food_name || "Food donation";

  const category = document.createElement("p");
  category.className = "cell-sub";
  category.textContent = donation.category || "Uncategorised";

  heading.append(title, category);
  head.append(heading, App.statusBadge(donation.status));

  const facts = document.createElement("dl");
  facts.className = "fact-list";

  function addFact(label, value) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");

    term.textContent = label;
    detail.textContent = value;

    row.append(term, detail);
    facts.appendChild(row);
  }

  addFact(
    "Quantity",
    App.formatQuantity(donation.quantity, donation.quantity_unit)
  );
  addFact("Location", donation.location || "Not provided");
  addFact("Pickup by", App.formatDate(donation.pickup_deadline));

  const foot = document.createElement("div");
  foot.className = "food-card-foot";

  const donor = document.createElement("span");
  donor.className = "donor";
  donor.textContent = donation.donor_name || "FoodShare donor";

  const link = document.createElement("a");
  link.className = "btn btn-small btn-outline";
  link.href = `track.html?id=${encodeURIComponent(donation.id)}`;
  link.textContent = "View details";

  foot.append(donor, link);
  card.append(head, facts, foot);

  return card;
}
