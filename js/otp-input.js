
/**
 * FoodShare - OTP Input Component
 *
 * Features:
 * 1. Six-digit OTP input.
 * 2. Automatically moves to the next input.
 * 3. Moves backwards using Backspace.
 * 4. Supports left and right arrow keys.
 * 5. Supports pasting an entire OTP.
 * 6. Validates all OTP digits.
 * 7. Supports clearing OTP inputs.
 * 8. Supports enabling/disabling inputs.
 * 9. Supports invalid input styling.
 * 10. Calls onComplete when all digits are entered.
 */

(function () {
  "use strict";

  const OtpInput = {

    // ================================================
    // MOUNT OTP INPUT COMPONENT
    // ================================================

    mount(container, options = {}) {

      if (!container) {
        throw new Error(
          "OTP input container not found."
        );
      }

      const length = Number(options.length) || 6;

      if (!Number.isInteger(length) || length < 1) {
        throw new Error(
          "OTP length must be a positive integer."
        );
      }

      const boxes = [];

      let disabled = false;
      let invalid = false;
      let lastCompletedValue = "";

      container.classList.add("otp-input");

      container.replaceChildren();

      // ==============================================
      // CHECK OTP COMPLETION
      // ==============================================

      function complete() {

        const isFilled = boxes.every(box =>
          /^\d$/.test(box.value)
        );

        if (!isFilled) {
          lastCompletedValue = "";
          return;
        }

        const code = boxes
          .map(box => box.value)
          .join("");

        // Avoid duplicate completion callbacks.
        if (code === lastCompletedValue) {
          return;
        }

        lastCompletedValue = code;

        if (typeof options.onComplete === "function") {
          options.onComplete(code);
        }
      }

      // ==============================================
      // FILL OTP INPUTS FROM A GIVEN POSITION
      // ==============================================

      function fillFrom(startIndex, text) {

        if (disabled) {
          return;
        }

        const digits = String(text)
          .replace(/\D/g, "")
          .slice(0, length - startIndex);

        if (!digits) {
          return;
        }

        digits.split("").forEach((digit, offset) => {

          const target = boxes[startIndex + offset];

          if (target) {
            target.value = digit;
          }

        });

        invalid = false;

        container.classList.remove("is-invalid");

        const nextIndex = Math.min(
          startIndex + digits.length,
          length - 1
        );

        boxes[nextIndex]?.focus();

        complete();
      }

      // ==============================================
      // CREATE OTP INPUT BOXES
      // ==============================================

      for (let i = 0; i < length; i++) {

        const input = document.createElement("input");

        input.type = "text";

        input.inputMode = "numeric";

        input.maxLength = 1;

        input.className = "otp-digit";

        input.autocomplete =
          i === 0 ? "one-time-code" : "off";

        input.pattern = "[0-9]";

        input.required = true;

        input.setAttribute(
          "aria-label",
          `Digit ${i + 1}`
        );

        if (i === 0 && options.id) {
          input.id = options.id;
        }

        if (options.describedBy) {
          input.setAttribute(
            "aria-describedby",
            options.describedBy
          );
        }

        // ==========================================
        // INPUT EVENT
        // ==========================================

        input.addEventListener("input", () => {

          if (disabled) {
            return;
          }

          const digits = input.value.replace(/\D/g, "");

          // Handle multiple digits entered into one box.
          if (digits.length > 1) {
            fillFrom(i, digits);
            return;
          }

          input.value = digits;

          invalid = false;

          container.classList.remove("is-invalid");

          if (input.value && boxes[i + 1]) {
            boxes[i + 1].focus();
          }

          complete();

        });

        // ==========================================
        // KEYBOARD NAVIGATION
        // ==========================================

        input.addEventListener("keydown", event => {

          if (disabled) {
            return;
          }

          // Backspace: move to previous box.
          if (
            event.key === "Backspace" &&
            !input.value &&
            boxes[i - 1]
          ) {
            boxes[i - 1].focus();
          }

          // Left arrow.
          if (
            event.key === "ArrowLeft" &&
            boxes[i - 1]
          ) {
            event.preventDefault();
            boxes[i - 1].focus();
          }

          // Right arrow.
          if (
            event.key === "ArrowRight" &&
            boxes[i + 1]
          ) {
            event.preventDefault();
            boxes[i + 1].focus();
          }

          // Allow only numeric characters.
          if (
            event.key.length === 1 &&
            !/[0-9]/.test(event.key)
          ) {
            event.preventDefault();
          }

        });

        // ==========================================
        // BACKSPACE INPUT HANDLING
        // ==========================================

        input.addEventListener("keydown", event => {

          if (
            event.key === "Backspace" &&
            input.value
          ) {
            input.value = "";

            lastCompletedValue = "";

            invalid = false;

            container.classList.remove("is-invalid");
          }

        });

        // ==========================================
        // PASTE OTP
        // ==========================================

        input.addEventListener("paste", event => {

          if (disabled) {
            return;
          }

          event.preventDefault();

          const pasted = event.clipboardData
            ? event.clipboardData.getData("text")
            : "";

          const digits = pasted.replace(/\D/g, "");

          if (!digits) {
            return;
          }

          // If the full OTP is pasted, start at box zero.
          const startIndex =
            digits.length >= length ? 0 : i;

          fillFrom(startIndex, digits);

        });

        // ==========================================
        // APPEND INPUT TO CONTAINER
        // ==========================================

        boxes.push(input);

        container.appendChild(input);

      }

      // ==============================================
      // PUBLIC OTP METHODS
      // ==============================================

      return {

        // Get complete OTP string.
        value: () => {

          return boxes
            .map(box => box.value)
            .join("");

        },

        // Check whether every box has one digit.
        isComplete: () => {

          return boxes.every(box =>
            /^\d$/.test(box.value)
          );

        },

        // Clear all OTP boxes.
        clear: () => {

          boxes.forEach(box => {
            box.value = "";
          });

          lastCompletedValue = "";

          invalid = false;

          container.classList.remove("is-invalid");

          if (!disabled) {
            boxes[0]?.focus();
          }

        },

        // Focus the first OTP box.
        focus: () => {

          if (!disabled) {
            boxes[0]?.focus();
          }

        },

        // Mark OTP as invalid or valid.
        setInvalid: value => {

          invalid = Boolean(value);

          container.classList.toggle(
            "is-invalid",
            invalid
          );

        },

        // Enable or disable all OTP boxes.
        setDisabled: value => {

          disabled = Boolean(value);

          boxes.forEach(box => {
            box.disabled = disabled;
          });

        },

        // Return all OTP input elements.
        getInputs: () => {

          return [...boxes];

        },

        // Return the current OTP length.
        getLength: () => {

          return length;

        }

      };

    }

  };

  // ================================================
  // MAKE COMPONENT AVAILABLE GLOBALLY
  // ================================================

  window.OtpInput = OtpInput;

})();
