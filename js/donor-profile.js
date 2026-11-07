/* =====================================================
   FOODSHARE - DONOR PROFILE PAGE
   File: js/donor-profile.js

   Handles the donor profile page:
   - account summary (photo, status, contact details)
   - editable personal details
   - profile photo upload, preview and removal
   - password change
   - account deletion

   Uses the shared helpers in js/api.js (window.App) so the
   API base, token handling and error formatting stay in one
   place.
   ===================================================== */

"use strict";

(function () {

  const FALLBACK_PHOTO = "../assets/default-avatar.svg";

  const MAX_PHOTO_BYTES = 2 * 1024 * 1024;

  const ALLOWED_PHOTO_TYPES = [
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp"
  ];

  const STATUS_BADGE = {
    verified: { className: "badge", icon: "bi-patch-check" },
    pending: { className: "badge badge-warning", icon: "bi-hourglass-split" },
    rejected: { className: "badge badge-danger", icon: "bi-x-circle" }
  };

  const field = (id) => document.getElementById(id);

  /* =====================================================
     AUTHENTICATION
     ===================================================== */

  if (!App.getDonorToken()) {
    window.location.replace("donor-login.html");
    return;
  }

  function signOut() {
    App.clearDonorToken();
    window.location.href = "donor-login.html";
  }

  function handleAuthFailure(error) {
    if (error && error.status === 401) {
      App.clearDonorToken();
      window.location.replace("donor-login.html");
      return true;
    }

    return false;
  }

  /* =====================================================
     SMALL HELPERS
     ===================================================== */

  function text(value, fallback) {
    const clean = String(value === null || value === undefined ? "" : value).trim();
    return clean || (fallback || "—");
  }

  /** Absolute URL for a stored photo path, with an optional cache buster. */
  function photoUrl(relativePath, bustCache) {
    if (!relativePath) {
      return FALLBACK_PHOTO;
    }

    if (/^https?:\/\//i.test(relativePath)) {
      return relativePath;
    }

    const path = relativePath.startsWith("/") ? relativePath : `/${relativePath}`;
    const url = `${App.API_BASE}${path}`;

    return bustCache ? `${url}?v=${Date.now()}` : url;
  }

  function setBusy(button, busy, busyLabel) {
    if (!button) {
      return;
    }

    if (busy) {
      button.dataset.label = button.innerHTML;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");

      if (busyLabel) {
        button.textContent = busyLabel;
      }

      return;
    }

    button.disabled = false;
    button.removeAttribute("aria-busy");

    if (button.dataset.label) {
      button.innerHTML = button.dataset.label;
      delete button.dataset.label;
    }
  }

  /* =====================================================
     STATE
     ===================================================== */

  let currentDonor = null;

  let selectedPhoto = null;

  let previewUrl = null;

  /* =====================================================
     SUMMARY + FORM RENDERING
     ===================================================== */

  function statusBadge(status) {
    const key = String(status || "").toLowerCase();

    const config = STATUS_BADGE[key] || {
      className: "badge",
      icon: "bi-info-circle"
    };

    const badge = document.createElement("span");
    badge.className = config.className;

    const icon = document.createElement("i");
    icon.className = `bi ${config.icon}`;
    icon.setAttribute("aria-hidden", "true");

    badge.append(icon, document.createTextNode(text(status, "Unknown")));

    return badge;
  }

  function renderSummary(donor) {
    currentDonor = { ...donor };

    field("summary-name").textContent = text(donor.full_name, "Your profile");

    field("summary-status").replaceChildren(statusBadge(donor.verification_status));

    field("summary-type").textContent = text(donor.account_type, "Individual");

    field("summary-email").textContent = text(donor.email);

    field("summary-phone").textContent = text(donor.phone);

    field("summary-account-type").textContent = text(donor.account_type, "Individual");

    const organizationRow = field("summary-organization-row");

    if (donor.account_type === "Organization" && donor.organization_name) {
      organizationRow.hidden = false;
      field("summary-organization").textContent = donor.organization_name;
    } else {
      organizationRow.hidden = true;
      field("summary-organization").textContent = "—";
    }

    field("summary-location").textContent = text(donor.location);

    field("summary-created").textContent = donor.created_at
      ? App.formatDate(donor.created_at, { dateOnly: true })
      : "Not recorded";

    setPhoto(donor.profile_photo_url || donor.profile_photo_path, false);
  }

  function fillForm(donor) {
    field("profile-full-name").value = donor.full_name || "";

    field("profile-email").value = donor.email || "";

    field("profile-phone").value = donor.phone || "";

    field("profile-location").value = donor.location || "";

    field("profile-account-type").value = donor.account_type || "";

    field("profile-organization").value = donor.organization_name || "";

    field("organization-field").hidden = donor.account_type !== "Organization";
  }

  /** Point both avatars at the given photo path. */
  function setPhoto(relativePath, bustCache) {
    const url = photoUrl(relativePath, bustCache);

    field("summary-photo").src = url;
    field("profile-photo-preview").src = url;

    const hasPhoto = Boolean(relativePath);

    field("remove-photo-button").hidden = !hasPhoto;
    field("upload-photo-button").disabled = !selectedPhoto;
  }

  function releasePreview() {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      previewUrl = null;
    }
  }

  /* =====================================================
     LOAD PROFILE
     ===================================================== */

  async function loadProfile() {
    try {
      const result = await App.request("/donors/me", { auth: "donor" });

      const donor = result.donor || result;

      renderSummary(donor);
      fillForm(donor);

    } catch (error) {
      if (handleAuthFailure(error)) {
        return;
      }

      App.showMessage("profile-message", error.message, "error");
    }
  }

  /* =====================================================
     EDIT PERSONAL DETAILS
     ===================================================== */

  const detailsForm = field("profile-form");

  detailsForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!detailsForm.reportValidity()) {
      return;
    }

    const payload = {
      full_name: field("profile-full-name").value.trim(),
      phone: field("profile-phone").value.trim(),
      location: field("profile-location").value.trim(),
      organization_name: field("profile-organization").value.trim() || null
    };

    if (payload.full_name.length < 2) {
      App.showMessage("details-message", "Enter your full name.", "error");
      return;
    }

    if (payload.phone.replace(/\D/g, "").length < 7) {
      App.showMessage("details-message", "Enter a phone number with at least 7 digits.", "error");
      return;
    }

    if (payload.location.length < 2) {
      App.showMessage("details-message", "Enter the area you donate from.", "error");
      return;
    }

    const button = field("save-profile-button");

    App.clearMessage("details-message");
    setBusy(button, true, "Saving…");

    try {
      const result = await App.request("/donors/me", {
        method: "PUT",
        auth: "donor",
        body: payload
      });

      const donor = result.donor || { ...currentDonor, ...payload };

      renderSummary(donor);
      fillForm(donor);

      App.showMessage(
        "details-message",
        result.message || "Profile updated successfully.",
        "success"
      );

      App.clearMessage("profile-message");

    } catch (error) {
      if (handleAuthFailure(error)) {
        return;
      }

      App.showMessage("details-message", error.message, "error");

    } finally {
      setBusy(button, false);
    }
  });

  field("cancel-profile-button").addEventListener("click", () => {
    if (currentDonor) {
      fillForm(currentDonor);
    }

    App.clearMessage("details-message");
  });

  /* =====================================================
     PROFILE PHOTO
     ===================================================== */

  const photoInput = field("profile-photo-input");
  const uploadButton = field("upload-photo-button");

  photoInput.addEventListener("change", () => {
    App.clearMessage("photo-message");

    const file = photoInput.files && photoInput.files[0];

    if (!file) {
      return;
    }

    if (!ALLOWED_PHOTO_TYPES.includes(file.type)) {
      App.showMessage(
        "photo-message",
        "Choose a JPG, JPEG, PNG or WEBP image.",
        "error"
      );

      photoInput.value = "";
      return;
    }

    if (file.size > MAX_PHOTO_BYTES) {
      App.showMessage(
        "photo-message",
        "That image is larger than 2 MB. Choose a smaller file.",
        "error"
      );

      photoInput.value = "";
      return;
    }

    releasePreview();

    selectedPhoto = file;
    previewUrl = URL.createObjectURL(file);

    /* Preview before uploading, as required. */
    field("profile-photo-preview").src = previewUrl;

    uploadButton.disabled = false;

    App.showMessage(
      "photo-message",
      "Preview ready. Select Upload photo to save it.",
      "info"
    );
  });

  uploadButton.addEventListener("click", async () => {
    if (!selectedPhoto) {
      App.showMessage("photo-message", "Choose a photo first.", "error");
      return;
    }

    const formData = new FormData();
    formData.append("photo", selectedPhoto);

    App.clearMessage("photo-message");
    setBusy(uploadButton, true, "Uploading…");

    try {
      const result = await App.request("/donors/me/photo", {
        method: "POST",
        auth: "donor",
        body: formData
      });

      releasePreview();

      selectedPhoto = null;
      photoInput.value = "";

      setPhoto(result.profile_photo_url, true);

      if (currentDonor) {
        currentDonor.profile_photo_url = result.profile_photo_url;
        currentDonor.profile_photo_path = result.profile_photo_url;
      }

      App.showMessage(
        "photo-message",
        result.message || "Profile photo uploaded successfully.",
        "success"
      );

    } catch (error) {
      if (handleAuthFailure(error)) {
        return;
      }

      /* Keep the preview so the donor can retry without re-picking. */
      App.showMessage("photo-message", error.message, "error");

      uploadButton.disabled = false;

    } finally {
      setBusy(uploadButton, false);

      /* Nothing selected any more after a successful upload. */
      if (!selectedPhoto) {
        uploadButton.disabled = true;
      }
    }
  });

  field("remove-photo-button").addEventListener("click", async (event) => {
    const confirmed = window.confirm("Remove your profile photo?");

    if (!confirmed) {
      return;
    }

    const button = event.currentTarget;

    App.clearMessage("photo-message");
    setBusy(button, true, "Removing…");

    try {
      const result = await App.request("/donors/me/photo", {
        method: "DELETE",
        auth: "donor"
      });

      releasePreview();

      selectedPhoto = null;
      photoInput.value = "";

      setPhoto(null, false);

      if (currentDonor) {
        currentDonor.profile_photo_url = null;
        currentDonor.profile_photo_path = null;
      }

      App.showMessage(
        "photo-message",
        result.message || "Profile photo removed successfully.",
        "success"
      );

    } catch (error) {
      if (handleAuthFailure(error)) {
        return;
      }

      App.showMessage("photo-message", error.message, "error");

    } finally {
      setBusy(button, false);
    }
  });

  /* =====================================================
     PASSWORD VISIBILITY TOGGLES
     ===================================================== */

  document.querySelectorAll("[data-toggle-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = field(button.dataset.togglePassword);

      if (!input) {
        return;
      }

      const showing = input.type === "text";

      input.type = showing ? "password" : "text";

      button.setAttribute("aria-pressed", String(!showing));
      button.setAttribute(
        "aria-label",
        `${showing ? "Show" : "Hide"} ${input.id.replace(/-/g, " ")}`
      );

      const icon = button.querySelector("i");

      if (icon) {
        icon.className = showing ? "bi bi-eye" : "bi bi-eye-slash";
      }
    });
  });

  /* =====================================================
     CHANGE PASSWORD
     ===================================================== */

  const passwordForm = field("change-password-form");

  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!passwordForm.reportValidity()) {
      return;
    }

    const currentPassword = field("current-password").value;
    const newPassword = field("new-password").value;
    const confirmPassword = field("confirm-new-password").value;

    App.clearMessage("change-password-message");

    if (newPassword !== confirmPassword) {
      App.showMessage(
        "change-password-message",
        "The new password and confirmation do not match.",
        "error"
      );
      return;
    }

    if (newPassword.length < 8) {
      App.showMessage(
        "change-password-message",
        "Use at least 8 characters for the new password.",
        "error"
      );
      return;
    }

    if (newPassword === currentPassword) {
      App.showMessage(
        "change-password-message",
        "Choose a new password different from your current one.",
        "error"
      );
      return;
    }

    const button = field("change-password-submit");

    setBusy(button, true, "Updating…");

    try {
      const result = await App.request("/donors/me/password", {
        method: "PUT",
        auth: "donor",
        body: {
          current_password: currentPassword,
          new_password: newPassword
        }
      });

      /* The API rotates the session token after a password change. */
      if (result.access_token) {
        App.setDonorToken(result.access_token);
      }

      passwordForm.reset();

      App.showMessage(
        "change-password-message",
        result.message || "Password changed successfully.",
        "success"
      );

    } catch (error) {
      if (handleAuthFailure(error)) {
        return;
      }

      App.showMessage("change-password-message", error.message, "error");

    } finally {
      setBusy(button, false);
    }
  });

  /* =====================================================
     DELETE ACCOUNT
     ===================================================== */

  const deleteDialog = field("delete-account-dialog");
  const deleteForm = field("delete-account-form");
  const deleteOpenButton = field("delete-account-open");

  deleteOpenButton.addEventListener("click", () => {
    deleteForm.reset();

    App.clearMessage("account-delete-message");

    if (typeof deleteDialog.showModal === "function") {
      deleteDialog.showModal();
      field("delete-account-password").focus();
      return;
    }

    App.showMessage(
      "account-delete-message",
      "This browser cannot open the confirmation dialog. Update the browser to delete an account.",
      "error"
    );
  });

  field("delete-account-cancel").addEventListener("click", () => {
    deleteDialog.close();
  });

  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const password = field("delete-account-password").value;

    const confirmation = field("delete-account-confirmation").value.trim();

    App.clearMessage("account-delete-message");

    if (confirmation !== "DELETE") {
      App.showMessage(
        "account-delete-message",
        'Type DELETE in capitals to confirm.',
        "error"
      );
      field("delete-account-confirmation").focus();
      return;
    }

    if (!field("delete-account-password").value) {
      App.showMessage(
        "account-delete-message",
        "Enter your current password.",
        "error"
      );
      field("delete-account-password").focus();
      return;
    }

    const finalCheck = window.confirm(
      "Permanently delete your FoodShare account? This cannot be undone."
    );

    if (!finalCheck) {
      return;
    }

    const button = field("delete-account-submit");

    setBusy(button, true, "Deleting…");

    try {
      await App.request("/donors/me", {
        method: "DELETE",
        auth: "donor",
        body: {
          password: password,
          confirmation: confirmation
        }
      });

      /* Never keep a token that points at a deleted account. */
      App.clearDonorToken();

      window.location.replace("index.html?account_deleted=1");

    } catch (error) {
      if (handleAuthFailure(error)) {
        return;
      }

      App.showMessage("account-delete-message", error.message, "error");

      setBusy(button, false);
    }
  });

  /* =====================================================
     SIGN OUT
     ===================================================== */

  field("profile-signout").addEventListener("click", (event) => {
    event.preventDefault();
    signOut();
  });

  /* =====================================================
     INITIALIZE
     ===================================================== */

  loadProfile();

})();
