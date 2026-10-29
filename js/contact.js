/* =====================================================
   FOODSHARE - CONTACT PAGE
   File: js/contact.js
   Builds a pre-filled email from the contact form.

   Replace CONTACT_EMAIL with the real FoodShare inbox.
   ===================================================== */

"use strict";

(function () {
  const CONTACT_EMAIL = "contact@foodshare.example";

  const form = document.getElementById("contact-form");
  const messageBox = document.getElementById("contact-message");
  const submitButton = document.getElementById("contact-submit");
  const emailLink = document.getElementById("support-email");

  if (!form) return;

  if (emailLink) {
    emailLink.href = `mailto:${CONTACT_EMAIL}`;
    emailLink.textContent = CONTACT_EMAIL;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    if (!form.reportValidity()) return;

    const data = new FormData(form);
    const name = String(data.get("name") || "").trim();
    const email = String(data.get("email") || "").trim();
    const subject = String(data.get("subject") || "").trim();
    const bodyText = String(data.get("message") || "").trim();

    if (name.length < 2 || !email || !subject || bodyText.length < 10) {
      App.showMessage(
        messageBox,
        "Fill in your name, email, subject and a message of at least 10 characters.",
        "error"
      );
      return;
    }

    const subjectLine = encodeURIComponent(`FoodShare: ${subject}`);
    const body = encodeURIComponent(`${bodyText}\n\n—\n${name}\n${email}`);

    App.showMessage(
      messageBox,
      `Opening your email application with the message addressed to ${CONTACT_EMAIL}.`,
      "info"
    );

    if (submitButton) {
      submitButton.disabled = true;
      window.setTimeout(() => {
        submitButton.disabled = false;
      }, 1500);
    }

    window.location.href = `mailto:${CONTACT_EMAIL}?subject=${subjectLine}&body=${body}`;
  });
})();
