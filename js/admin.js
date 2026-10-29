/* =====================================================
   FOODSHARE - ADMIN CONSOLE
   File: js/admin.js
   Loads donations, donor accounts and food requests, then
   drives every admin action the API exposes.
   ===================================================== */

"use strict";

(function () {
  if (!App.getAdminToken()) {
    window.location.replace("admin-login.html");
    return;
  }

  const alertBox = document.getElementById("alert");
  const refreshButton = document.getElementById("refresh");
  const logoutButton = document.getElementById("logout");
  const searchInput = document.getElementById("q");
  const statusFilter = document.getElementById("f-status");
  const typeFilter = document.getElementById("f-type");

  const donationsBody = document.getElementById("donations-body");
  const donorsBody = document.getElementById("donors-body");
  const requestsBody = document.getElementById("requests-body");

  const donationsCount = document.getElementById("donations-count");
  const donorsCount = document.getElementById("donors-count");
  const requestsCount = document.getElementById("requests-count");

  const mailMode = document.getElementById("mail-mode");
  const mailHost = document.getElementById("mail-host");
  const mailSender = document.getElementById("mail-sender");
  const mailTransport = document.getElementById("mail-transport");
  const mailHint = document.getElementById("mail-hint");
  const mailTestForm = document.getElementById("mail-test-form");
  const mailTestAddress = document.getElementById("mail-test-address");
  const mailTestButton = document.getElementById("mail-test-button");

  const DONATION_STATUSES = ["Available", "Reserved", "Completed", "Cancelled", "Expired"];
  const REQUEST_STATUSES = ["Pending", "Approved", "Fulfilled", "Rejected"];

  let donations = [];
  let donors = [];
  let requests = [];
  let lastLoadAt = 0;

  /* ---------------- helpers ------------------------------------ */

  function signOut() {
    App.clearAdminToken();
    window.location.replace("admin-login.html");
  }

  async function adminRequest(path, options) {
    const settings = Object.assign({ auth: "admin" }, options || {});

    try {
      return await App.request(path, settings);
    } catch (error) {
      if (error.isAuthError) {
        signOut();
      }

      throw error;
    }
  }

  function setAlert(text, type) {
    App.showMessage(alertBox, text, type || "info");
  }

  function textCell(text, className) {
    const td = document.createElement("td");
    td.textContent = text === null || text === undefined || text === "" ? "—" : String(text);

    if (className) td.className = className;

    return td;
  }

  function twoLineCell(title, subtitle) {
    const td = document.createElement("td");

    const strong = document.createElement("span");
    strong.className = "cell-title";
    strong.textContent = title === null || title === undefined || title === ""
      ? "—"
      : String(title);

    td.appendChild(strong);

    if (subtitle) {
      const small = document.createElement("span");
      small.className = "cell-sub";
      small.textContent = subtitle;
      td.appendChild(small);
    }

    return td;
  }

  function emptyRow(colspan, title, hint) {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = colspan;
    td.appendChild(App.emptyState(title, hint));
    row.appendChild(td);

    return row;
  }

  function makeSelect(values, current, onChange) {
    const select = document.createElement("select");
    select.className = "table-select";
    select.setAttribute("aria-label", "Change status");

    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      option.selected = value === current;
      select.appendChild(option);
    });

    select.addEventListener("change", () => onChange(select.value, select));

    return select;
  }

  function setStat(id, value) {
    const element = document.getElementById(id);

    if (element) {
      element.textContent = String(value);
    }
  }

  function countBy(list, predicate) {
    return list.reduce((total, item) => total + (predicate(item) ? 1 : 0), 0);
  }

  /* ---------------- summary ------------------------------------ */

  function renderStats() {
    setStat("m-total", donations.length);
    setStat("m-available", countBy(donations, (item) => item.status === "Available"));
    setStat("m-completed", countBy(donations, (item) => item.status === "Completed"));
    setStat(
      "m-closed",
      countBy(donations, (item) => item.status === "Cancelled" || item.status === "Expired")
    );
    setStat("m-donors", donors.length);
    setStat(
      "m-pending",
      countBy(
        donors,
        (item) => item.account_type === "Organization" && item.verification_status === "Pending"
      )
    );
  }

  /* ---------------- donations ---------------------------------- */

  function visibleDonations() {
    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
    const status = statusFilter ? statusFilter.value : "";
    const type = typeFilter ? typeFilter.value : "";

    return donations.filter((item) => {
      const haystack = [
        item.food_name,
        item.donor_name,
        item.donor_email,
        item.location,
        item.category
      ]
        .join(" ")
        .toLowerCase();

      return (!query || haystack.includes(query))
        && (!status || item.status === status)
        && (!type || (item.item_type || "Food") === type);
    });
  }

  async function changeDonationStatus(donation, status, select) {
    if (status === donation.status) return;

    select.disabled = true;

    try {
      await adminRequest(`/admin/donations/${encodeURIComponent(donation.id)}/status`, {
        method: "PATCH",
        body: { status }
      });

      donation.status = status;
      renderDonations();
      renderStats();
      setAlert(`Donation #${donation.id} set to ${status}.`, "success");
    } catch (error) {
      select.value = donation.status;
      setAlert(error.message, "error");
    } finally {
      select.disabled = false;
    }
  }

  async function deleteDonation(donation) {
    const confirmed = window.confirm(
      `Delete donation #${donation.id} "${donation.food_name}"? This cannot be undone.`
    );

    if (!confirmed) return;

    try {
      await adminRequest(`/admin/donations/${encodeURIComponent(donation.id)}`, {
        method: "DELETE"
      });

      donations = donations.filter((item) => item.id !== donation.id);
      renderDonations();
      renderStats();
      setAlert(`Donation #${donation.id} deleted.`, "success");
    } catch (error) {
      setAlert(error.message, "error");
    }
  }

  function renderDonations() {
    const rows = visibleDonations();

    donationsBody.replaceChildren();

    if (donationsCount) donationsCount.textContent = String(donations.length);

    if (!rows.length) {
      donationsBody.appendChild(
        emptyRow(
          9,
          donations.length ? "No donations match these filters" : "No donations yet",
          donations.length
            ? "Clear the search or filters."
            : "Donations appear here once a donor publishes one."
        )
      );
      return;
    }

    rows.forEach((donation) => {
      const row = document.createElement("tr");

      const statusCell = document.createElement("td");
      statusCell.appendChild(App.statusBadge(donation.status));

      const actionCell = document.createElement("td");
      actionCell.className = "cell-actions";

      const actions = document.createElement("div");
      actions.className = "table-actions";

      actions.appendChild(
        makeSelect(DONATION_STATUSES, donation.status, (value, select) => {
          changeDonationStatus(donation, value, select);
        })
      );

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-small btn-danger";
      remove.textContent = "Delete";
      remove.addEventListener("click", () => {
        deleteDonation(donation);
      });

      actions.appendChild(remove);
      actionCell.appendChild(actions);

      row.append(
        textCell(donation.id),
        twoLineCell(donation.food_name || "Food donation", donation.item_type || "Food"),
        textCell(App.formatQuantity(donation.quantity, donation.quantity_unit), "cell-nowrap"),
        textCell(donation.category),
        textCell(donation.location),
        textCell(App.formatDate(donation.pickup_deadline), "cell-nowrap"),
        twoLineCell(donation.donor_name || "Unknown donor", donation.donor_email || "No account linked"),
        statusCell,
        actionCell
      );

      donationsBody.appendChild(row);
    });
  }

  /* ---------------- donor accounts ----------------------------- */

  async function setVerification(donor, status, button) {
    button.disabled = true;

    try {
      await adminRequest(`/admin/organizations/${encodeURIComponent(donor.id)}/verification`, {
        method: "PATCH",
        body: { verification_status: status }
      });

      donor.verification_status = status;
      renderDonors();
      renderStats();
      setAlert(`${donor.organization_name || donor.full_name} marked ${status}.`, "success");
    } catch (error) {
      button.disabled = false;
      setAlert(error.message, "error");
    }
  }

  function renderDonors() {
    donorsBody.replaceChildren();

    if (donorsCount) donorsCount.textContent = String(donors.length);

    if (!donors.length) {
      donorsBody.appendChild(
        emptyRow(8, "No donor accounts", "Accounts appear here after registration.")
      );
      return;
    }

    donors.forEach((donor) => {
      const row = document.createElement("tr");

      const verificationCell = document.createElement("td");
      verificationCell.appendChild(App.statusBadge(donor.verification_status || "Pending"));

      const actionCell = document.createElement("td");
      actionCell.className = "cell-actions";

      const actions = document.createElement("div");
      actions.className = "table-actions";

      if (donor.account_type === "Organization") {
        if (donor.verification_status !== "Verified") {
          const approve = document.createElement("button");
          approve.type = "button";
          approve.className = "btn btn-small";
          approve.textContent = "Approve";
          approve.addEventListener("click", () => {
            setVerification(donor, "Verified", approve);
          });
          actions.appendChild(approve);
        }

        if (donor.verification_status !== "Rejected") {
          const reject = document.createElement("button");
          reject.type = "button";
          reject.className = "btn btn-small btn-secondary";
          reject.textContent = "Reject";
          reject.addEventListener("click", () => {
            setVerification(donor, "Rejected", reject);
          });
          actions.appendChild(reject);
        }
      } else {
        const note = document.createElement("span");
        note.className = "cell-sub";
        note.textContent = "Not required";
        actions.appendChild(note);
      }

      actionCell.appendChild(actions);

      row.append(
        textCell(donor.id),
        twoLineCell(donor.full_name || "Unnamed"),
        twoLineCell(donor.account_type, donor.organization_name || ""),
        textCell(donor.location),
        textCell(donor.email),
        textCell(donor.phone, "cell-nowrap"),
        verificationCell,
        actionCell
      );

      donorsBody.appendChild(row);
    });
  }

  /* ---------------- food requests ------------------------------ */

  async function changeRequestStatus(request, status, select) {
    if (status === request.request_status) return;

    select.disabled = true;

    try {
      await adminRequest(`/food-requests/${encodeURIComponent(request.id)}/status`, {
        method: "PATCH",
        body: { request_status: status }
      });

      request.request_status = status;
      renderRequests();
      setAlert(`Request #${request.id} marked ${status}.`, "success");
    } catch (error) {
      select.value = request.request_status;
      setAlert(error.message, "error");
    } finally {
      select.disabled = false;
    }
  }

  function renderRequests() {
    requestsBody.replaceChildren();

    if (requestsCount) requestsCount.textContent = String(requests.length);

    if (!requests.length) {
      requestsBody.appendChild(
        emptyRow(8, "No food requests", "Requests from the Find Food page appear here.")
      );
      return;
    }

    requests.forEach((request) => {
      const row = document.createElement("tr");

      const statusCell = document.createElement("td");
      statusCell.appendChild(App.statusBadge(request.request_status || "Pending"));

      const actionCell = document.createElement("td");
      actionCell.className = "cell-actions";

      const actions = document.createElement("div");
      actions.className = "table-actions";

      actions.appendChild(
        makeSelect(REQUEST_STATUSES, request.request_status || "Pending", (value, select) => {
          changeRequestStatus(request, value, select);
        })
      );

      actionCell.appendChild(actions);

      row.append(
        textCell(request.id),
        textCell(request.requester_name),
        textCell(request.contact, "cell-nowrap"),
        textCell(request.location),
        textCell(request.food_category),
        textCell(App.formatQuantity(request.quantity_needed, "units"), "cell-nowrap"),
        statusCell,
        actionCell
      );

      requestsBody.appendChild(row);
    });
  }

  /* ---------------- email delivery ----------------------------- */

  let mailLive = false;

  function renderMail(info) {
    mailLive = info.mode === "smtp";

    if (mailMode) {
      mailMode.textContent = mailLive ? "Sending by email" : "Demo mode";
      mailMode.className = mailLive ? "badge" : "badge badge-warning";
    }

    if (mailHost) {
      mailHost.textContent = info.host ? `${info.host}:${info.port}` : "Not configured";
    }

    if (mailSender) {
      mailSender.textContent = info.sender || "—";
    }

    if (mailTransport) {
      mailTransport.textContent = mailLive
        ? info.transport + (info.requires_login ? " with mailbox login" : "")
        : "No message leaves the machine";
    }

    if (mailHint) {
      mailHint.hidden = mailLive;
      mailHint.textContent = mailLive ? "" : info.reason || "";
    }

    if (mailTestButton) {
      mailTestButton.disabled = !mailLive;
    }

    if (mailTestAddress) {
      mailTestAddress.disabled = !mailLive;
    }
  }

  async function sendTestMail(event) {
    event.preventDefault();

    if (!mailTestForm.reportValidity()) {
      return;
    }

    const address = mailTestAddress.value.trim();
    const label = mailTestButton.innerHTML;

    mailTestButton.disabled = true;
    mailTestButton.textContent = "Sending…";

    try {
      const result = await adminRequest("/admin/mail/test", {
        method: "POST",
        body: { email: address }
      });

      setAlert(result.message || `A test message is on its way to ${address}.`, "success");
    } catch (error) {
      setAlert(error.message, "error");
    } finally {
      mailTestButton.innerHTML = label;
      mailTestButton.disabled = !mailLive;
    }
  }

  /* ---------------- loading ------------------------------------ */

  async function loadAll() {
    if (refreshButton) refreshButton.disabled = true;
    lastLoadAt = Date.now();
    setAlert("Refreshing dashboard…", "info");

    const results = await Promise.allSettled([
      adminRequest("/admin/donations"),
      adminRequest("/admin/donors"),
      adminRequest("/food-requests"),
      adminRequest("/admin/mail/status")
    ]);

    const problems = [];

    if (results[0].status === "fulfilled") {
      donations = App.pickList(results[0].value, "donations");
    } else {
      donations = [];
      problems.push(`Donations: ${results[0].reason.message}`);
    }

    if (results[1].status === "fulfilled") {
      donors = App.pickList(results[1].value, "donors");
    } else {
      donors = [];
      problems.push(`Accounts: ${results[1].reason.message}`);
    }

    if (results[2].status === "fulfilled") {
      requests = App.pickList(results[2].value, "requests");
    } else {
      requests = [];
      problems.push(`Food requests: ${results[2].reason.message}`);
    }

    if (results[3].status === "fulfilled") {
      renderMail(results[3].value.mail || {});
    } else {
      renderMail({ mode: "demo", reason: results[3].reason.message });
    }

    renderStats();
    renderDonations();
    renderDonors();
    renderRequests();

    if (problems.length) {
      setAlert(problems.join(" "), "error");
    } else {
      setAlert(
        `Loaded ${donations.length} donation(s), ${donors.length} account(s) and ${requests.length} request(s).`,
        "success"
      );
    }

    if (refreshButton) refreshButton.disabled = false;
  }

  /* Coming back to the tab reloads the tables, so a donation published by a
     donor elsewhere shows up without pressing Refresh. */
  function refreshIfStale() {
    if (document.visibilityState === "hidden") return;
    if (Date.now() - lastLoadAt < 5000) return;
    if (refreshButton && refreshButton.disabled) return;

    loadAll();
  }

  refreshButton?.addEventListener("click", loadAll);
  logoutButton?.addEventListener("click", signOut);
  searchInput?.addEventListener("input", renderDonations);
  statusFilter?.addEventListener("change", renderDonations);
  typeFilter?.addEventListener("change", renderDonations);
  mailTestForm?.addEventListener("submit", sendTestMail);

  document.addEventListener("visibilitychange", refreshIfStale);
  window.addEventListener("focus", refreshIfStale);

  loadAll();
})();
