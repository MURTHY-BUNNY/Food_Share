
/* =====================================================
   FOODSHARE - DONOR LOGIN
   Email/Password + Google Sign-In
   File: js/donor-login.js
   ===================================================== */

"use strict";

(function () {

    /* =====================================================
       DOM ELEMENTS
       ===================================================== */

    const form = document.getElementById("donor-login-form");
    const messageBox = document.getElementById("login-message");
    const submitButton = document.getElementById("login-button");
    const googleButton = document.getElementById("google-signin-button");

    if (!form) return;

    if (!window.App) {
        console.error("FoodShare API module (App) is not loaded.");
        return;
    }

    /* =====================================================
       EXISTING SESSION CHECK
       ===================================================== */

    if (App.getDonorToken()) {
        window.location.replace("donor-dashboard.html");
        return;
    }

    let googleLoginInProgress = false;

    /* =====================================================
       COMMON LOGIN SUCCESS HANDLER
       ===================================================== */

    function completeLogin(result) {

        if (!result || !result.access_token) {
            throw new App.ApiError(
                "The API did not return an access token.",
                500
            );
        }

        // Store FoodShare donor JWT.
        App.setDonorToken(result.access_token);

        App.showMessage(
            messageBox,
            "Signed in successfully. Opening your dashboard...",
            "success"
        );

        window.location.href = "donor-dashboard.html";
    }

    /* =====================================================
       EMAIL AND PASSWORD LOGIN
       Existing functionality preserved.
       ===================================================== */

    form.addEventListener("submit", async function (event) {

        event.preventDefault();

        if (!form.reportValidity()) return;

        const email = document
            .getElementById("email")
            .value
            .trim()
            .toLowerCase();

        const password = document
            .getElementById("password")
            .value;

        App.clearMessage(messageBox);

        if (!email || !password) {

            App.showMessage(
                messageBox,
                "Enter your email address and password.",
                "error"
            );

            return;
        }

        const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailPattern.test(email)) {
            App.showMessage(
                messageBox,
                "Enter a valid email address.",
                "error"
            );
            return;
        }

        const credentials = new URLSearchParams();

        credentials.set("username", email);
        credentials.set("password", password);

        const originalLabel = submitButton
            ? submitButton.innerHTML
            : "";

        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = "Signing in...";
        }

        try {

            const result = await App.request("/donors/login", {
                method: "POST",
                body: credentials
            });

            completeLogin(result);

        } catch (error) {

            const errorMessage = error.isNetworkError
                ? error.message
                : error.status === 422
                    ? "Enter a valid email address and your password."
                    : error.message;

            App.showMessage(
                messageBox,
                errorMessage,
                "error"
            );

        } finally {

            if (submitButton) {
                submitButton.disabled = false;
                submitButton.innerHTML = originalLabel;
            }

        }

    });

    /* =====================================================
       GOOGLE SIGN-IN INITIALIZATION
       ===================================================== */

    function initializeGoogleSignIn() {

        if (!googleButton) {
            console.error(
                'Google Sign-In container "#google-signin-button" was not found.'
            );
            return;
        }

        if (!window.google || !window.google.accounts ||
            !window.google.accounts.id) {

            App.showMessage(
                messageBox,
                "Google Sign-In could not load. Please refresh the page.",
                "error"
            );

            return;
        }

        try {

            google.accounts.id.initialize({

                client_id:
                    "957117898526-lqjpaeblpjfflsc4u5lugqe13aero63t.apps.googleusercontent.com",

                callback: handleGoogleCredential

            });

            google.accounts.id.renderButton(
                googleButton,
                {
                    theme: "outline",
                    size: "large",
                    text: "continue_with",
                    shape: "rectangular",
                    width: 320
                }
            );

        } catch (error) {

            console.error("Google initialization error:", error);

            App.showMessage(
                messageBox,
                "Unable to initialize Google Sign-In. Please refresh the page.",
                "error"
            );

        }

    }

    /* =====================================================
       GOOGLE CREDENTIAL HANDLER
       ===================================================== */

    async function handleGoogleCredential(response) {

        if (googleLoginInProgress) return;

        App.clearMessage(messageBox);

        if (!response || !response.credential) {

            App.showMessage(
                messageBox,
                "Google did not return a valid sign-in credential.",
                "error"
            );

            return;
        }

        googleLoginInProgress = true;

        if (submitButton) {
            submitButton.disabled = true;
        }

        App.showMessage(
            messageBox,
            "Verifying your Google account...",
            "success"
        );

        try {

            /*
             * IMPORTANT:
             *
             * Send a JavaScript object here.
             * api.js automatically converts it into JSON.
             *
             * DO NOT use JSON.stringify() here.
             */

            const result = await App.request("/donors/google", {

                method: "POST",

                body: {

                    credential: response.credential,

                    account_type: "Individual"

                }

            });

            completeLogin(result);

        } catch (error) {

            console.error("Google donor login error:", error);

            let errorMessage =
                error.message || "Google sign-in failed.";

            if (error.isNetworkError) {

                errorMessage =
                    "Cannot connect to FoodShare. Check whether the backend is running.";

            }

            App.showMessage(
                messageBox,
                errorMessage,
                "error"
            );

        } finally {

            googleLoginInProgress = false;

            if (submitButton) {
                submitButton.disabled = false;
            }

        }

    }

    /* =====================================================
       WAIT FOR GOOGLE IDENTITY SERVICES
       ===================================================== */

    function waitForGoogle(attempt) {

        const currentAttempt = attempt || 0;

        if (
            window.google &&
            window.google.accounts &&
            window.google.accounts.id
        ) {

            initializeGoogleSignIn();
            return;

        }

        if (currentAttempt >= 50) {

            App.showMessage(
                messageBox,
                "Google Sign-In could not load. Check your internet connection and refresh.",
                "error"
            );

            console.error("Google Identity Services failed to load.");

            return;
        }

        window.setTimeout(
            () => waitForGoogle(currentAttempt + 1),
            200
        );

    }

    /* =====================================================
       START GOOGLE SIGN-IN
       ===================================================== */

    waitForGoogle();

})();
