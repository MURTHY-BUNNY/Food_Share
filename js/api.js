/* =====================================================
   FOODSHARE - SHARED FRONTEND MODULE
   File: js/api.js
   Loaded by every page. Owns the API base URL, auth token
   storage, request/error handling, formatting helpers and
   the shared navigation behaviour.
   ===================================================== */

"use strict";

const App = (function () {
  const API_BASE = (window.FOODSHARE_API_BASE || "http://127.0.0.1:8000").replace(/\/+$/, "");

  const DONOR_TOKEN_KEY = "foodshare_donor_token";
  const ADMIN_TOKEN_KEY = "foodshare_admin_token";

  const STATUS_CLASS = {
    available: "available",
    reserved: "reserved",
    completed: "completed",
    expired: "expired",
    cancelled: "cancelled",
    pending: "pending",
    fulfilled: "fulfilled",
    approved: "approved"
  };

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status || 0;
      this.payload = payload || null;
    }

    get isAuthError() {
      return this.status === 401 || this.status === 403;
    }

    get isNetworkError() {
      return this.status === 0;
    }
  }

  /* ---------------- storage ------------------------------------ */

  function readStorage(store, key) {
    try {
      return store.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStore(store, key, value) {
    try {
      if (value === null || value === undefined) {
        store.removeItem(key);
      } else {
        store.setItem(key, value);
      }
    } catch (error) {
      /* Private mode or storage disabled: the request simply stays anonymous. */
    }
  }

  /* Donor sessions survive refreshes; admin sessions end with the tab. */
  const getDonorToken = () => readStorage(window.localStorage, DONOR_TOKEN_KEY);
  const setDonorToken = (token) => writeStore(window.localStorage, DONOR_TOKEN_KEY, token);
  const clearDonorToken = () => writeStore(window.localStorage, DONOR_TOKEN_KEY, null);

  const getAdminToken = () => readStorage(window.sessionStorage, ADMIN_TOKEN_KEY);
  const setAdminToken = (token) => writeStore(window.sessionStorage, ADMIN_TOKEN_KEY, token);
  const clearAdminToken = () => writeStore(window.sessionStorage, ADMIN_TOKEN_KEY, null);

  /* ---------------- requests ----------------------------------- */

  function describeError(payload, status) {
    const detail = payload ? payload.detail : null;

    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
          const label = field ? String(field).replace(/_/g, " ") : "";
          return label ? `${label}: ${item.msg}` : item.msg;
        })
        .filter(Boolean);

      if (messages.length) {
        return messages.join(" ");
      }
    }

    if (payload && typeof payload.message === "string" && payload.message.trim()) {
      return payload.message;
    }

    if (status === 401) return "Please sign in again.";
    if (status === 403) return "You do not have access to this action.";
    if (status === 404) return "That record could not be found.";
    if (status >= 500) return "The FoodShare API ran into an error. Please try again.";

    return `Request failed (${status}).`;
  }

  async function request(path, options) {
    const settings = options || {};
    const method = settings.method || "GET";
    const headers = Object.assign({ Accept: "application/json" }, settings.headers || {});

    let body;

    if (settings.body !== undefined && settings.body !== null) {
      if (settings.body instanceof URLSearchParams || settings.body instanceof FormData) {
        body = settings.body;
      } else {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(settings.body);
      }
    }

    if (settings.auth === "donor") {
      const token = getDonorToken();

      if (!token) {
        throw new ApiError("Please sign in as a donor to continue.", 401);
      }

      headers.Authorization = `Bearer ${token}`;
    }

    if (settings.auth === "admin") {
      const token = getAdminToken();

      if (!token) {
        throw new ApiError("Your admin session has ended. Please sign in again.", 401);
      }

      headers.Authorization = `Bearer ${token}`;
    }

    let response;

    try {
      response = await fetch(`${API_BASE}${path}`, { method, headers, body });
    } catch (error) {
      throw new ApiError(
        `Cannot reach the FoodShare API at ${API_BASE}. Confirm the backend is running.`,
        0
      );
    }

    const text = await response.text();
    let payload = {};

    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        throw new ApiError("The API returned an unreadable response.", response.status);
      }
    }

    if (!response.ok) {
      if (response.status === 401 && settings.auth === "donor") {
        clearDonorToken();
      }

      throw new ApiError(describeError(payload, response.status), response.status, payload);
    }

    return payload;
  }

  function pickList(payload, key) {
    if (Array.isArray(payload)) return payload;

    if (payload && Array.isArray(payload[key])) return payload[key];

    return [];
  }

  /* ---------------- formatting --------------------------------- */

  const dateFormatter = new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric"
  });

  const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });

  const numberFormatter = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 2
  });

  function formatDate(value, options) {
    if (value === null || value === undefined || value === "") return "Not provided";

    const date = normalizeDate(value);

    if (!date) return String(value);

    return (options && options.dateOnly ? dateFormatter : dateTimeFormatter).format(date);
  }

  function normalizeDate(value) {
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) ? null : value;
    }

    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const parts = value.split("-").map(Number);
      return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    const date = new Date(value);

    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatQuantity(quantity, unit) {
    const label = unit || "items";

    if (quantity === null || quantity === undefined || quantity === "") {
      return `— ${label}`;
    }

    const amount = Number(quantity);

    return `${Number.isFinite(amount) ? numberFormatter.format(amount) : quantity} ${label}`;
  }

  function toInputDateTime(date) {
    const value = normalizeDate(date) || new Date();
    const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);

    return local.toISOString().slice(0, 16);
  }

  function toInputDate(date) {
    const value = normalizeDate(date);

    return value ? toInputDateTime(value).slice(0, 10) : "";
  }

  function debounce(fn, wait) {
    let timer;

    return function debounced(...args) {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => fn.apply(this, args), wait || 250);
    };
  }

  /* ---------------- messages ----------------------------------- */

  function resolveElement(target) {
    if (!target) return null;

    return typeof target === "string" ? document.getElementById(target) : target;
  }

  function showMessage(target, text, type) {
    const element = resolveElement(target);

    if (!element) {
      if (type === "error") console.error(text);
      return;
    }

    element.textContent = typeof text === "string" ? text : (text && text.message) || "Something went wrong.";
    element.className = `message message-${type || "info"}`;
    element.hidden = false;
  }

  function clearMessage(target) {
    const element = resolveElement(target);

    if (!element) return;

    element.textContent = "";
    element.className = "message";
  }

  function statusBadge(status) {
    const label = String(status || "Unknown");
    const badge = document.createElement("span");
    const modifier = STATUS_CLASS[label.toLowerCase()] || "expired";

    badge.className = `status status-${modifier}`;
    badge.textContent = label;

    return badge;
  }

  function emptyState(title, hint) {
    const wrapper = document.createElement("div");
    wrapper.className = "empty-state";

    const icon = document.createElement("i");
    icon.className = "bi bi-inbox";
    icon.setAttribute("aria-hidden", "true");

    const heading = document.createElement("strong");
    heading.textContent = title;

    wrapper.append(icon, heading);

    if (hint) {
      const paragraph = document.createElement("span");
      paragraph.textContent = hint;
      wrapper.appendChild(paragraph);
    }

    return wrapper;
  }

  /* ---------------- navigation --------------------------------- */

  function currentPage() {
    const file = window.location.pathname.split("/").pop();

    return file || "index.html";
  }

  function initNavigation() {
    const toggle = document.querySelector(".nav-toggle");
    const nav = document.getElementById("site-nav");

    if (toggle && nav) {
      toggle.addEventListener("click", () => {
        const open = nav.classList.toggle("is-open");

        toggle.setAttribute("aria-expanded", String(open));
        toggle.innerHTML = open
          ? '<i class="bi bi-x-lg" aria-hidden="true"></i>'
          : '<i class="bi bi-list" aria-hidden="true"></i>';
      });

      nav.addEventListener("click", (event) => {
        if (event.target.closest("a") && nav.classList.contains("is-open")) {
          nav.classList.remove("is-open");
          toggle.setAttribute("aria-expanded", "false");
          toggle.innerHTML = '<i class="bi bi-list" aria-hidden="true"></i>';
        }
      });
    }

    const page = currentPage();

    document.querySelectorAll(".nav-links a[href]").forEach((link) => {
      if (link.getAttribute("href") === page) {
        link.classList.add("active");
        link.setAttribute("aria-current", "page");
      }
    });

    const authLink = document.querySelector("[data-auth-link]");

    if (authLink && getDonorToken()) {
      /* A signed-in donor does not need the "Join Us" call to action. */
      const joinLink = document.querySelector(".nav-links a.btn");

      if (joinLink) {
        joinLink.hidden = true;
      }

      authLink.textContent = "Sign out";
      authLink.classList.add("nav-signout");
      authLink.addEventListener("click", (event) => {
        event.preventDefault();
        clearDonorToken();
        window.location.href = "index.html";
      });
    }
  }

  function initYear() {
    const year = document.getElementById("current-year");

    if (year) {
      year.textContent = new Date().getFullYear();
    }
  }

  function init() {
    initNavigation();
    initYear();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return {
    API_BASE,
    ApiError,
    request,
    pickList,
    getDonorToken,
    setDonorToken,
    clearDonorToken,
    getAdminToken,
    setAdminToken,
    clearAdminToken,
    formatDate,
    formatQuantity,
    toInputDateTime,
    toInputDate,
    debounce,
    showMessage,
    clearMessage,
    statusBadge,
    emptyState
  };
})();
