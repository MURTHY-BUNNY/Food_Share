
/**
 * FoodShare donor registration
 *
 * Google flow:
 * 1. Verify Google identity with the backend.
 * 2. Existing donor: sign in and redirect.
 * 3. New donor: complete FoodShare profile.
 * 4. Create account and receive donor JWT.
 *
 * Email flow:
 * 1. Submit registration details.
 * 2. Verify email OTP.
 * 3. Create account and receive donor JWT.
 */

(function () {
  "use strict";

  // --------------------------------------------------
  // EXISTING EMAIL REGISTRATION ELEMENTS
  // --------------------------------------------------

  const form = document.getElementById("donor-register-form");
  const messageBox = document.getElementById("donor-register-message");
  const accountType = document.getElementById("account_type");
  const organizationField = document.getElementById("organization-field");
  const organizationName = document.getElementById("organization_name");
  const submitButton = document.getElementById("donor-register-button");

  const verifyForm = document.getElementById("donor-verify-form");
  const verifyButton = document.getElementById("verify-submit-button");

  const panelTitle = document.getElementById("register-heading");
  const panelSubtitle = document.getElementById("register-subtitle");

  const verifyEmail = document.getElementById("verify-email");
  const verifyExpiry = document.getElementById("verify-expiry");

  const demoCodeBox = document.getElementById("verify-demo-code");
  const demoCodeValue = document.getElementById("verify-demo-value");

  const resendButton = document.getElementById("resend-code-button");
  const resendTimer = document.getElementById("resend-timer");
  const changeEmailButton = document.getElementById("change-email-button");

  const stepMarkers = document.querySelectorAll(
    "[data-step-indicator]"
  );

  if (!form || !verifyForm) {
    console.error("FoodShare registration forms were not found.");
    return;
  }

  const FALLBACK_COOLDOWN = 60;

  let pendingEmail = "";
  let cooldownTimer = null;
  let submitting = false;
  let requestingCode = false;

  // --------------------------------------------------
  // OTP INPUT
  // --------------------------------------------------

  const otp = OtpInput.mount(
    document.getElementById("verify-code-input"),
    {
      length: 6,
      id: "verify-code",
      describedBy: "verify-code-help",

      onComplete: () => {
        verifyButton.disabled = false;

        if (!submitting) {
          submitCode();
        }
      }
    }
  );

  // --------------------------------------------------
  // REGISTRATION STEPS
  // --------------------------------------------------

  function showStep(name) {
    const onVerify = name === "verify";

    form.hidden = onVerify;
    verifyForm.hidden = !onVerify;

    if (panelTitle) {
      panelTitle.textContent = onVerify
        ? "Verify your email"
        : "Account details";
    }

    if (panelSubtitle) {
      panelSubtitle.textContent = onVerify
        ? "Enter the 6-digit code we sent to your inbox."
        : "Fields marked with an asterisk are required.";
    }

    stepMarkers.forEach((marker) => {
      const index = Number(marker.dataset.stepIndicator);

      marker.classList.toggle(
        "is-current",
        onVerify ? index === 2 : index === 1
      );

      marker.classList.toggle(
        "is-done",
        onVerify && index === 1
      );
    });

    if (onVerify) {
      otp.focus();
    }
  }

  function syncOrganizationField() {
    const isOrganization =
      accountType.value === "Organization";

    organizationField.hidden = !isOrganization;
    organizationName.required = isOrganization;

    if (!isOrganization) {
      organizationName.value = "";
    }
  }

  // --------------------------------------------------
  // OTP RESEND COOLDOWN
  // --------------------------------------------------

  function startCooldown(seconds) {
    const total =
      Number(seconds) > 0
        ? Number(seconds)
        : FALLBACK_COOLDOWN;

    let remaining = total;

    window.clearInterval(cooldownTimer);

    resendButton.disabled = true;

    const paint = () => {
      if (remaining <= 0) {
        window.clearInterval(cooldownTimer);

        resendButton.disabled = false;
        resendTimer.textContent = "Still no code?";

        return;
      }

      resendTimer.textContent =
        `You can ask for a new code in ${remaining}s.`;

      remaining -= 1;
    };

    paint();

    cooldownTimer = window.setInterval(paint, 1000);
  }

  // --------------------------------------------------
  // EMAIL REGISTRATION PAYLOAD
  // --------------------------------------------------

  function detailsPayload() {
    const data = new FormData(form);

    const isOrganization =
      data.get("account_type") === "Organization";

    const organization = String(
      data.get("organization_name") || ""
    ).trim();

    return {
      full_name: String(
        data.get("full_name") || ""
      ).trim(),

      account_type: isOrganization
        ? "Organization"
        : "Individual",

      organization_name: isOrganization
        ? organization
        : null,

      email: String(
        data.get("email") || ""
      ).trim().toLowerCase(),

      phone: String(
        data.get("phone") || ""
      ).trim(),

      location: String(
        data.get("location") || ""
      ).trim(),

      password: String(
        data.get("password") || ""
      ),

      confirmation: String(
        data.get("confirm_password") || ""
      ),

      organization: organization,
      isOrganization: isOrganization
    };
  }

  // --------------------------------------------------
  // SHOW EMAIL VERIFICATION SCREEN
  // --------------------------------------------------

  function showCodeStep(result, email) {
    pendingEmail = email;

    verifyEmail.textContent = email;

    verifyExpiry.textContent =
      result.expires_in_minutes || 15;

    if (result.delivery === "demo" && result.dev_code) {
      demoCodeValue.textContent = result.dev_code;
      demoCodeBox.hidden = false;
    } else {
      demoCodeBox.hidden = true;
    }

    otp.clear();
    verifyButton.disabled = true;

    verifyButton.innerHTML =
      '<i class="bi bi-shield-check" aria-hidden="true"></i> Verify and create account';

    googleStartSection.hidden = true;
    showEmailRegistration.hidden = true;
    googleProfileForm.hidden = true;

    showStep("verify");

    startCooldown(
      result.resend_after_seconds || FALLBACK_COOLDOWN
    );

    App.clearMessage(messageBox);
  }

  // --------------------------------------------------
  // EMAIL STEP 1: REQUEST OTP
  // --------------------------------------------------

  async function requestCode(event) {
    event.preventDefault();

    if (requestingCode) {
      return;
    }

    if (!form.reportValidity()) {
      return;
    }

    const payload = detailsPayload();

    // Validate email before requesting OTP.
    const emailPattern =
      /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

    if (!payload.email || !emailPattern.test(payload.email)) {
      App.showMessage(
        messageBox,
        "Wrong email ID entered. Please check your email address.",
        "error"
      );

      document.getElementById("email").focus();
      return;
    }

    App.clearMessage(messageBox);

    if (payload.password !== payload.confirmation) {
      App.showMessage(
        messageBox,
        "The two passwords do not match.",
        "error"
      );

      return;
    }

    if (
      payload.isOrganization &&
      payload.organization.length < 2
    ) {
      App.showMessage(
        messageBox,
        "Enter the organization name.",
        "error"
      );

      return;
    }

    const originalLabel = submitButton.innerHTML;

    requestingCode = true;
    submitButton.disabled = true;
    submitButton.textContent = "Validating email…";

    try {
      const result = await App.request(
        "/donors/register",
        {
          method: "POST",

          body: {
            full_name: payload.full_name,
            account_type: payload.account_type,
            organization_name: payload.organization_name,
            email: payload.email,
            phone: payload.phone,
            location: payload.location,
            password: payload.password
          }
        }
      );

      showCodeStep(result, payload.email);

    } catch (error) {
      App.showMessage(
        messageBox,
        error.message,
        "error"
      );

    } finally {
      requestingCode = false;
      submitButton.disabled = false;
      submitButton.innerHTML = originalLabel;
    }
  }

  // --------------------------------------------------
  // EMAIL STEP 2: VERIFY OTP
  // --------------------------------------------------

  async function submitCode(event) {
    if (event) {
      event.preventDefault();
    }

    if (submitting) {
      return;
    }

    if (!pendingEmail) {
      showStep("account");
      return;
    }

    if (!otp.isComplete()) {
      otp.setInvalid(true);

      App.showMessage(
        messageBox,
        "Enter all six digits of the code.",
        "error"
      );

      otp.focus();
      return;
    }

    const originalLabel = verifyButton.innerHTML;

    submitting = true;
    verifyButton.disabled = true;
    verifyButton.textContent = "Checking code…";

    App.clearMessage(messageBox);

    try {
      const result = await App.request(
        "/donors/register/verify",
        {
          method: "POST",

          body: {
            email: pendingEmail,
            code: otp.value()
          }
        }
      );

      if (!result.access_token) {
        throw new App.ApiError(
          "The API did not return an access token.",
          500
        );
      }

      App.setDonorToken(result.access_token);

      App.showMessage(
        messageBox,

        result.donor &&
        result.donor.verification_status === "Pending"

          ? "Email verified. Organization approval is pending. Opening your dashboard…"

          : "Email verified. Opening your dashboard…",

        "success"
      );

      window.setTimeout(() => {
        window.location.href = "donor-dashboard.html";
      }, 1200);

    } catch (error) {
      otp.setInvalid(true);

      App.showMessage(
        messageBox,
        error.message,
        "error"
      );

      otp.focus();

      submitting = false;
      verifyButton.disabled = false;
      verifyButton.innerHTML = originalLabel;
    }
  }

  // --------------------------------------------------
  // RESEND EMAIL OTP
  // --------------------------------------------------

  async function resendCode() {
    if (!pendingEmail) {
      showStep("account");
      return;
    }

    resendButton.disabled = true;
    resendButton.textContent = "Sending...";

    try {
      const result = await App.request(
        "/donors/register/resend",
        {
          method: "POST",

          body: {
            email: pendingEmail
          }
        }
      );

      otp.clear();
      verifyButton.disabled = true;

      demoCodeValue.textContent =
        result.dev_code || "";

      demoCodeBox.hidden = !(
        result.delivery === "demo" &&
        result.dev_code
      );

      startCooldown(
        result.resend_after_seconds || FALLBACK_COOLDOWN
      );

      App.showMessage(
        messageBox,
        result.message || "A new code is on its way.",
        "success"
      );

      otp.focus();

    } catch (error) {
      App.showMessage(
        messageBox,
        error.message,
        "error"
      );

      resendButton.disabled = false;

    } finally {
      resendButton.textContent = "Resend code";
    }
  }

  // --------------------------------------------------
  // RETURN TO EMAIL DETAILS
  // --------------------------------------------------

  function backToAccountDetails() {
    window.clearInterval(cooldownTimer);

    otp.clear();

    verifyButton.disabled = true;
    pendingEmail = "";

    demoCodeBox.hidden = true;

    App.clearMessage(messageBox);

    verifyForm.hidden = true;
    form.hidden = false;

    googleStartSection.hidden = true;
    showEmailRegistration.hidden = true;

    document.getElementById("email").focus();
  }

  // ==================================================
  // GOOGLE REGISTRATION
  // ==================================================

  const googleRegisterContainer =
    document.getElementById("google-register-button");

  const googleStartSection =
    document.getElementById("google-start-section");

  const googleProfileForm =
    document.getElementById("google-profile-form");

  const googleProfileName =
    document.getElementById("google-profile-name");

  const googleProfileEmail =
    document.getElementById("google-profile-email");

  const googleAccountType =
    document.getElementById("google-account-type");

  const googleOrganizationField =
    document.getElementById("google-organization-field");

  const googleOrganizationName =
    document.getElementById("google-organization-name");

  const googleProfileSubmit =
    document.getElementById("google-profile-submit");

  const googleProfileCancel =
    document.getElementById("google-profile-cancel");

  const showEmailRegistration =
    document.getElementById("show-email-registration");

  const backToGoogle =
    document.getElementById("back-to-google");

  // Your Google OAuth Web Client ID
  const GOOGLE_CLIENT_ID =
    "957117898526-lqjpaeblpjfflsc4u5lugqe13aero63t.apps.googleusercontent.com";

  let googleOnboardingToken = "";
  let googleRegistrationInProgress = false;

  function setGoogleRegistrationBusy(busy) {
    googleRegisterContainer.style.pointerEvents =
      busy ? "none" : "";
    googleRegisterContainer.style.opacity =
      busy ? "0.6" : "";
    googleRegisterContainer.setAttribute(
      "aria-busy",
      String(busy)
    );
  }

  // --------------------------------------------------
  // GOOGLE ORGANIZATION FIELD
  // --------------------------------------------------

  function syncGoogleOrganizationField() {
    const isOrganization =
      googleAccountType.value === "Organization";

    googleOrganizationField.hidden = !isOrganization;

    googleOrganizationName.required = isOrganization;

    if (!isOrganization) {
      googleOrganizationName.value = "";
    }
  }

  // --------------------------------------------------
  // RETURN TO GOOGLE SCREEN
  // --------------------------------------------------

  function showGoogleStart() {
    googleOnboardingToken = "";
    googleRegistrationInProgress = false;
    setGoogleRegistrationBusy(false);

    googleProfileForm.hidden = true;
    googleStartSection.hidden = false;

    showEmailRegistration.hidden = false;

    form.hidden = true;
    verifyForm.hidden = true;

    App.clearMessage(messageBox);
  }

  // --------------------------------------------------
  // SHOW NEW GOOGLE USER PROFILE FORM
  // --------------------------------------------------

  function showGoogleProfile(result) {
    googleOnboardingToken = result.onboarding_token;
    googleRegistrationInProgress = false;
    setGoogleRegistrationBusy(false);

    googleProfileName.textContent =
      result.profile?.full_name || "Google user";

    googleProfileEmail.textContent =
      result.profile?.email || "";

    googleProfileForm.hidden = false;

    googleStartSection.hidden = true;
    showEmailRegistration.hidden = true;

    form.hidden = true;
    verifyForm.hidden = true;

    googleAccountType.value = "Individual";

    syncGoogleOrganizationField();

    document.getElementById("google-phone").focus();

    App.showMessage(
      messageBox,
      "Google verified. Complete your FoodShare profile to create your donor account.",
      "success"
    );
  }

  // --------------------------------------------------
  // GOOGLE STEP 1: SEND CREDENTIAL TO FASTAPI
  // --------------------------------------------------

  async function handleGoogleRegistration(response) {
    if (googleRegistrationInProgress) {
      return;
    }

    if (!response || !response.credential) {
      App.showMessage(
        messageBox,
        "Google did not return a valid sign-in response. Please try again.",
        "error"
      );

      return;
    }

    googleRegistrationInProgress = true;
    setGoogleRegistrationBusy(true);

    App.clearMessage(messageBox);

    try {
      const result = await App.request(
        "/donors/google",
        {
          method: "POST",

          body: {
            credential: response.credential
          }
        }
      );

      // New Google identity: complete profile.
      if (result.profile_required) {
        if (
          !result.onboarding_token ||
          !result.profile
        ) {
          throw new App.ApiError(
            "The API returned an incomplete Google registration session.",
            500
          );
        }

        showGoogleProfile(result);
        return;
      }

      // Existing donor: direct sign-in.
      if (!result.access_token) {
        throw new App.ApiError(
          "The API did not return a donor access token.",
          500
        );
      }

      App.setDonorToken(result.access_token);

      App.showMessage(
        messageBox,
        "Google sign-in successful. Opening your FoodShare dashboard…",
        "success"
      );

      window.setTimeout(() => {
        window.location.href = "donor-dashboard.html";
      }, 900);

    } catch (error) {
      App.showMessage(
        messageBox,
        error.message || "Google sign-in failed. Please try again.",
        "error"
      );

      googleRegistrationInProgress = false;
      setGoogleRegistrationBusy(false);
    }
  }

  // --------------------------------------------------
  // GOOGLE STEP 2: COMPLETE NEW DONOR PROFILE
  // --------------------------------------------------

  async function completeGoogleProfile(event) {
    event.preventDefault();

    if (!googleOnboardingToken) {
      App.showMessage(
        messageBox,
        "Your Google registration session is missing. Please start again.",
        "error"
      );

      showGoogleStart();
      return;
    }

    if (!googleProfileForm.reportValidity()) {
      return;
    }

    const isOrganization =
      googleAccountType.value === "Organization";

    const organization = String(
      googleOrganizationName.value || ""
    ).trim();

    const phone = String(
      document.getElementById("google-phone").value || ""
    ).trim();

    const location = String(
      document.getElementById("google-location").value || ""
    ).trim();

    if (
      isOrganization &&
      organization.length < 2
    ) {
      App.showMessage(
        messageBox,
        "Enter your organization name.",
        "error"
      );

      googleOrganizationName.focus();
      return;
    }

    googleProfileSubmit.disabled = true;

    googleProfileSubmit.textContent =
      "Creating account…";

    App.clearMessage(messageBox);

    try {
      const result = await App.request(
        "/donors/google/complete",
        {
          method: "POST",

          body: {
            onboarding_token: googleOnboardingToken,

            account_type: isOrganization
              ? "Organization"
              : "Individual",

            organization_name: isOrganization
              ? organization
              : null,

            phone: phone,
            location: location
          }
        }
      );

      if (!result.access_token) {
        throw new App.ApiError(
          "The API did not return a donor access token.",
          500
        );
      }

      App.setDonorToken(result.access_token);

      googleOnboardingToken = "";

      App.showMessage(
        messageBox,

        result.donor?.verification_status === "Pending"

          ? "Your FoodShare account is ready. Organization verification is pending. Opening your dashboard…"

          : "Your FoodShare donor account has been created. Opening your dashboard…",

        "success"
      );

      window.setTimeout(() => {
        window.location.href = "donor-dashboard.html";
      }, 1000);

    } catch (error) {
      App.showMessage(
        messageBox,
        error.message || "Could not complete your FoodShare profile.",
        "error"
      );

    } finally {
      googleProfileSubmit.disabled = false;

      googleProfileSubmit.textContent =
        "Complete FoodShare profile";
    }
  }

  // --------------------------------------------------
  // INITIALIZE GOOGLE IDENTITY SERVICES
  // --------------------------------------------------

  function initializeGoogleRegistration() {
    if (!googleRegisterContainer) {
      return;
    }

    if (
      !window.google ||
      !window.google.accounts ||
      !window.google.accounts.id
    ) {
      App.showMessage(
        messageBox,
        "Google sign-up could not load. Check your internet connection or use email registration.",
        "error"
      );

      return;
    }

    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleGoogleRegistration,
      ux_mode: "popup",
      auto_select: false
    });

    window.google.accounts.id.renderButton(
      googleRegisterContainer,
      {
        type: "standard",
        theme: "outline",
        size: "large",
        text: "continue_with",
        shape: "rectangular",
        logo_alignment: "left",
        width: 320
      }
    );
  }

  // ==================================================
  // EVENT LISTENERS
  // ==================================================

  // Email registration
  form.addEventListener("submit", requestCode);

  verifyForm.addEventListener("submit", submitCode);

  resendButton.addEventListener("click", resendCode);

  changeEmailButton.addEventListener(
    "click",
    backToAccountDetails
  );

  accountType.addEventListener(
    "change",
    syncOrganizationField
  );

  // Google registration
  googleAccountType.addEventListener(
    "change",
    syncGoogleOrganizationField
  );

  googleProfileForm.addEventListener(
    "submit",
    completeGoogleProfile
  );

  googleProfileCancel.addEventListener(
    "click",
    showGoogleStart
  );

  // Switch from Google to email registration
  showEmailRegistration.addEventListener(
    "click",
    () => {
      googleStartSection.hidden = true;
      showEmailRegistration.hidden = true;

      form.hidden = false;
      verifyForm.hidden = true;

      App.clearMessage(messageBox);

      document.getElementById("full_name").focus();
    }
  );

  // Return from email registration to Google
  backToGoogle.addEventListener(
    "click",
    showGoogleStart
  );

  // --------------------------------------------------
  // INITIAL PAGE STATE
  // --------------------------------------------------

  syncOrganizationField();
  syncGoogleOrganizationField();

  form.hidden = true;
  verifyForm.hidden = true;
  googleProfileForm.hidden = true;

  // Google Identity Services loads asynchronously.
  window.addEventListener(
    "load",
    initializeGoogleRegistration
  );

})();
